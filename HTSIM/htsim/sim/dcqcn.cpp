// -*- c-basic-offset: 4; indent-tabs-mode: nil -*- 
#include <math.h>
#include <stdlib.h>
#include <iostream>
#include <algorithm>
#include "dcqcn.h"
#include "queue.h"
#include <stdio.h>
#include "switch.h"
#include "trigger.h"
using namespace std;

////////////////////////////////////////////////////////////////
//  DCQCN SOURCE
////////////////////////////////////////////////////////////////

/* keep track of RTOs.  Generally, we shouldn't see RTOs if
   return-to-sender is enabled.  Otherwise we'll see them with very
   large incasts. */
simtime_picosec DCQCNSink::_cnp_interval = timeFromUs(50.0);
simtime_picosec DCQCNSrc::_cc_update_period = timeFromUs(55.0);

// Paper parameters (Zhu et al., SIGCOMM 2015): g = 1/256, B = 10 MB, F = 5,
// T = K = 55 us, R_AI = 40 Mbps and R_HAI = 200 Mbps on 40 Gbps NICs. We scale
// R_AI and R_HAI with the line rate (0.1 % and 0.5 %). See OUR_CHANGES.md (section "DCQCN made faithful to the paper").
double DCQCNSrc::_g = .00390625; // g = 1/256
uint64_t DCQCNSrc::_B = 10000000; // bytes to go until we fire the byte counter
uint32_t DCQCNSrc::_F = 5;
bool DCQCNSrc::_legacy = false;
linkspeed_bps DCQCNSrc::_RAI_cfg = 0;
linkspeed_bps DCQCNSrc::_RHAI_cfg = 0;
double DCQCNSrc::_RAI_frac = 0.001;
double DCQCNSrc::_RHAI_frac = 0.005;
double DCQCNSrc::_shared_alpha = 1;   // legacy only
// Debug: DCQCN_TRACE=1 in the environment prints one line per CNP and per
// rate increase ("DCQCN_TRACE <us> <flow> <event> RC_Gbps RT_Gbps alpha T BC").
static bool dcqcn_trace = getenv("DCQCN_TRACE") != NULL;

void DCQCNTimer::doNextEvent() {
    _src->timerFired();
}

DCQCNSrc::DCQCNSrc(RoceLogger* logger, TrafficLogger* pktlogger, EventList &eventlist, linkspeed_bps rate)
    : RoceSrc(logger,pktlogger,eventlist,rate), _timer(eventlist, this)
{
    _cnps_received = 0;

    //linkspeed
    _link = rate;
    //current transmit rate
    _RC = rate;
    //target transmit rate
    _RT = rate;

    _alpha = 1;
    if (_legacy) {
        // original HTSIM values: 5 % and 10 % of line rate
        _RAI = rate / 20;
        _RHAI = rate / 10;
    } else {
        _RAI = _RAI_cfg ? _RAI_cfg : (linkspeed_bps)(_RAI_frac * rate);
        _RHAI = _RHAI_cfg ? _RHAI_cfg : (linkspeed_bps)(_RHAI_frac * rate);
    }

    _last_cc_update = 0;
    _last_alpha_update = 0;

    _T = 0;
    _BC = 0;
    _byte_counter = 0;
    _old_highest_sent = 0;

    _timer_due = 0;
    _timer_started = false;
}

void DCQCNSrc::armTimer() {
    _timer_due = eventlist().now() + _cc_update_period;
    eventlist().sourceIsPending(_timer, _timer_due);
}

void DCQCNSrc::processCNP(const CNPPacket& cnp){
    double& a = alpha();   // per flow; the shared static only with -dcqcn_legacy
    _RT = _RC;
    _RC = _RC * (1-a/2);
    // Minimum rate clamp. Real DCQCN NICs have one; without it a flow that is
    // cut every 50 us for a few ms reaches an integer rate of 0, and the pacer
    // in roce.h then divides by it (E8 hit this on one seed). One R_AI step,
    // 0.1% of line rate, matches where additive increase would restart from.
    if (_RC < _RAI) _RC = _RAI;
    a = (1-_g)*a + _g;

    //_ai_state = increase_state::fast_recovery;

    _T = 0;
    _BC = 0;
    _byte_counter = 0;
    _old_highest_sent = _highest_sent;

    if (dcqcn_trace)
        cout << "DCQCN_TRACE " << timeAsUs(eventlist().now()) << " " << _flow._name << " cnp " << _RC/1e9 << " " << _RT/1e9 << " " << a << " " << _T << " " << _BC << endl;

    _pacing_rate = _RC;
    update_spacing();
    //cout << " packet spacing is " << timeAsUs(_packet_spacing) << endl;

    _last_cc_update = eventlist().now();
    _last_alpha_update = eventlist().now();

    if (_legacy) {
        // original: wakes the source itself, which also starts another pacing chain
        eventlist().sourceIsPendingRel(*this, _cc_update_period);
    } else {
        // restart both the rate-increase timer and the alpha timer
        _timer_started = true;
        armTimer();
    }
}

void DCQCNSrc::increaseRate(){
    if (_RC>= _link) {
        //no need to increase, already at line rate!
        return;
    }

    if (max(_T,_BC) <= _F){
        //fast recovery
        _RC = (_RT + _RC) / 2;

    } else if (min(_T,_BC) > _F){
        //hyper increase
        _RT = _RT + (min(_T,_BC)-_F)* _RHAI;
        if (!_legacy && _RT > _link) _RT = _link;
        _RC = (_RT + _RC) / 2;

    } else {
        //active increase 
        _RT += _RAI;
        if (!_legacy && _RT > _link) _RT = _link;
        _RC = (_RT + _RC) / 2;

    }

    if (_RC > _link){
        _RC = _link;
    }

    _pacing_rate = _RC;
    update_spacing();

    if (dcqcn_trace)
        cout << "DCQCN_TRACE " << timeAsUs(eventlist().now()) << " " << _flow._name << " inc " << _RC/1e9 << " " << _RT/1e9 << " " << alpha() << " " << _T << " " << _BC << endl;
    //cout << "At " << timeAsUs(eventlist().now()) << " " << _RC << " increase target rate is " << _RT << " node " << nodename() << " packet spacing is " << timeAsUs(_packet_spacing) << " _T is " << _T << " _BC is " << _BC << " byte counter is " << _byte_counter << " alpha is " << alpha() << endl;
}

// Faithful mode: the rate-increase timer T and the alpha timer K, both 55 us,
// both restarted by a CNP (processCNP). Runs on its own EventSource.
void DCQCNSrc::timerFired(){
    if (eventlist().now() != _timer_due)   // superseded by a later armTimer()
        return;
    if (_done)
        return;

    if (eventlist().now()-_last_alpha_update >= _cc_update_period){
        _alpha = (1-_g)*_alpha;
        _last_alpha_update = eventlist().now();
    }

    if (eventlist().now()-_last_cc_update >= _cc_update_period){
        _last_cc_update = eventlist().now();
        _T ++;
        increaseRate();
    }

    armTimer();
}

void DCQCNSrc::doNextEvent(){
    if (!_legacy) {
        if (_done)          // all data ACKed: let the pacing chain die
            return;

        RoceSrc::doNextEvent();

        if (_done || !_flow_started)
            return;

        if (!_timer_started) {
            // timers run from the start of the flow
            _timer_started = true;
            _last_cc_update = eventlist().now();
            _last_alpha_update = eventlist().now();
            armTimer();
        }

        // Byte counter. RoceSrc::_highest_sent counts PACKETS (roce.cpp
        // send_packet), so convert to bytes before comparing with _B.
        if (_highest_sent >= _old_highest_sent)
            _byte_counter += (_highest_sent - _old_highest_sent) * (uint64_t)_mss;
        _old_highest_sent = _highest_sent;   // go-back-N rewinds it; do not underflow

        if (_byte_counter >= _B){
            _byte_counter -= _B;
            _BC++;
            increaseRate();
        }
        return;
    }

    // ---------------- legacy (-dcqcn_legacy): original HTSIM code ----------------
    bool reschedule = false;

    RoceSrc::doNextEvent();

    if (_done)                                    // FIX: stop timer when done
        return;

    //cout << "Adding " << (_highest_sent - _old_highest_sent) << " to sent bytes " << _byte_counter << " " << _highest_sent << endl;

    // NOTE: _highest_sent is in packets, so this counts packets, not bytes.
    _byte_counter += (_highest_sent - _old_highest_sent);
    _old_highest_sent = _highest_sent;

    if (_byte_counter >= _B){
        _byte_counter = 0;
        _BC++;

        increaseRate();
        reschedule = true;
    }

    if (eventlist().now()-_last_alpha_update >= _cc_update_period){
        _shared_alpha = (1-_g)*_shared_alpha;     // one alpha for every flow
        _last_alpha_update = eventlist().now();   // FIX: was never assigned
        reschedule = true;
    }    


    if (eventlist().now()-_last_cc_update >= _cc_update_period){
        _last_cc_update = eventlist().now();
        _T ++;

        increaseRate();
        reschedule = true;
    }

    if (reschedule)
        eventlist().sourceIsPendingRel(*this, _cc_update_period);
}

void DCQCNSrc::receivePacket(Packet& pkt) 
{
    if (!_flow_started){
        assert(pkt.type()==ETH_PAUSE);
        return; 
    }

    if (_stop_time && eventlist().now() >= _stop_time) {
        // stop sending new data, but allow us to finish any retransmissions
        _flow_size = _highest_sent+_mss;
        _stop_time = 0;
    }

    if (_done)
        return;

    switch (pkt.type()) {
    case ETH_PAUSE:    
        processPause((const EthPausePacket&)pkt);
        pkt.free();
        return;
    case ROCENACK: 
        _nacks_received++;
        processNack((const RoceNack&)pkt);
        pkt.free();
        return;
    case ROCEACK:
        _acks_received++;
        processAck((const RoceAck&)pkt);
        pkt.free();
        return;
    case CNP:
        _cnps_received++;
        processCNP((const CNPPacket&)pkt);
        pkt.free();
        return;

    default:
        abort();
    }
}

////////////////////////////////////////////////////////////////
//  DCQCN SINK
////////////////////////////////////////////////////////////////

/* Only use this constructor when there is only one for to this receiver */
DCQCNSink::DCQCNSink(EventList &eventlist)
    : RoceSink(), EventSource(eventlist, "DCQCN Sink")
{
    _last_cnp_sent_time = UINT64_MAX;
    _marked_packets_since_last_cnp = 0;
    _packets_since_last_cnp = 0;
}

// Receive a packet.
// Note: _cumulative_ack is the last byte we've ACKed.
// seqno is the first byte of the new packet.
void DCQCNSink::receivePacket(Packet& pkt) {
    bool ecn_marked = ((pkt.flags() & ECN_CE) != 0);
    RoceSink::receivePacket(pkt);

    if (ecn_marked){
        //generate CNPs here.
        if (_last_cnp_sent_time == UINT64_MAX || eventlist().now() - _last_cnp_sent_time >= _cnp_interval){
            send_cnp();
            eventlist().sourceIsPendingRel(*this,_cnp_interval);
        }               
        else {
            _marked_packets_since_last_cnp++;
        }
    }
    _packets_since_last_cnp++;
}

void DCQCNSink::doNextEvent(){
    if (eventlist().now() - _last_cnp_sent_time >= _cnp_interval && _marked_packets_since_last_cnp >0){
        send_cnp();
        eventlist().sourceIsPendingRel(*this,_cnp_interval);
    }
}

void DCQCNSink::send_cnp() {
    CNPPacket *cnp = 0;
    cnp = CNPPacket::newpkt(_src->_flow, *_route, _cumulative_ack,_srcaddr);
    cnp->set_pathid(0);

    cnp->sendOn();

    _last_cnp_sent_time = eventlist().now();
    _packets_since_last_cnp = 0;
    _marked_packets_since_last_cnp = 0;
}




