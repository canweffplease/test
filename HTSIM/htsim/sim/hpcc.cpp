// -*- c-basic-offset: 4; indent-tabs-mode: nil -*- 
#include <math.h>
#include <iostream>
#include <algorithm>
#include "hpcc.h"
#include "queue.h"
#include <stdio.h>
#include "switch.h"
#include "trigger.h"
using namespace std;

////////////////////////////////////////////////////////////////
//  HPCC SOURCE
////////////////////////////////////////////////////////////////

/* When you're debugging, sometimes it's useful to enable debugging on
   a single HPCC receiver, rather than on all of them.  Set this to the
   node ID and recompile if you need this; otherwise leave it
   alone. */
//#define LOGSINK 2332
#define LOGSINK   0 


/* keep track of RTOs.  Generally, we shouldn't see RTOs if
   return-to-sender is enabled.  Otherwise we'll see them with very
   large incasts. */
uint32_t HPCCSrc::_global_node_count = 0;

// Known baseline RTT. Was 12 us, the propagation-only RTT of the default
// 3-tier tree (6 links x 1 us x 2). HPCC sizes W_init = B*T and caps W there,
// which fills the link only if T is the full base RTT; with 9 KB packets at
// 400 Gbps the store-and-forward serialization adds 6 x 0.18 us, and the
// measured base RTT host 0 -> host 8 is 13.1 us (docs/VALIDATION.md). With
// 12 us a lone flow is window-limited to 600 KB / 13.1 us = 92 % of line
// rate. Other topologies need -hpcc_T (e.g. 16.4 us on the 100 Gbps
// fat_tree_128_* files). -hpcc_legacy restores 12 us.
simtime_picosec HPCCSrc::_T = timeFromUs(13.1);
double HPCCSrc::_eta = 0.95;//Target link utilization
uint32_t HPCCSrc::_max_stages = 5;//Maximum stages for additive increases
uint32_t HPCCSrc::_N = 10;//maximum number of flows.
uint32_t HPCCSrc::_Wai = 0;//Additive increase amount.
bool HPCCSrc::_legacy = false;   // -hpcc_legacy: original HTSIM window update
bool HPCCSrc::_Wai_set = false;  // -hpcc_wai given
bool HPCCSrc::_trace = false;    // -hpcc_trace

HPCCSrc::HPCCSrc(HPCCLogger* logger, TrafficLogger* pktlogger, EventList &eventlist, linkspeed_bps rate)
    : BaseQueue(rate,eventlist,NULL), _logger(logger), _flow(pktlogger)
{
    _mss = Packet::data_packet_size();
    _end_trigger = NULL;

    _stop_time = 0;
    _flow_started = false;
    _pacing_rate = rate;

    _acked_packets = 0;
    _packets_sent = 0;
    _new_packets_sent = 0;
    _rtx_packets_sent = 0;
    _acks_received = 0;
    _nacks_received = 0;

    _highest_sent = 0;
    _last_acked = 0;
    _dstaddr = UINT32_MAX;

    _sink = 0;
    _done = false;

    _drops = 0;
    _flow_size = ((uint64_t)1)<<63;
  
    _node_num = _global_node_count++;
    _nodename = "HPCCsrc " + to_string(_node_num);

    // Was srand(time(NULL)): reseeding the global RNG from the wall clock
    // made every run nondeterministic and ignored -seed. roce.cpp already
    // has this line commented out upstream. The ECMP entropy now comes from
    // the -seed stream like every other random choice.
    //srand(time(NULL));
    _pathid = random()%256;

    //cout << _nodename << " path id is " << _pathid << endl;

    // debugging hack
    _log_me = false;

    _state_send = READY;
    _time_last_sent = 0;
    
    _cwnd = _T * rate / pow(10,12) / 8;

    _pacing_rate = _cwnd * 8 * pow(10,12) / _T;
    update_spacing();

    _link_count = 0;
    _last_update_seq = 0;

    // Was uninitialised (read by computeWind on the first ACK).
    _incStage = 0;
    _Wc = _cwnd;

    // HPCC paper, Sec. 3.3: W_init = B_NIC * T; the sending rate never
    // exceeds the NIC line rate, i.e. W <= W_init (the reference ns-3 code
    // clamps the rate to [minRate, lineRate] before storing it as the
    // reference). W_AI = W_init * (1 - eta) / N.
    _W_init = (double)_cwnd;
    _W_max = _W_init;
    _W_min = _mss;
    _Wc_d = _W_init;
    _have_ref = false;
    _last_goback_ts = 0;
    if (_legacy)
        _Wai = _mss;   // original HTSIM value
    else if (!_Wai_set)
        _Wai = (uint32_t)(_W_init * (1.0 - _eta) / _N);

    _stat_maxW = _stat_minW = _W_init;
    _stat_wc_updates = 0;
    _stat_stale_nacks = 0;
    _tr_tau = _tr_uq = _tr_utx = _tr_hop = 0;

    cout << "Initial CWND is " << _cwnd << " target RTT " << timeAsUs(_T) << " rate " << rate
         << (_legacy ? " legacy" : " paper") << " eta " << _eta << " maxStage " << _max_stages
         << " W_AI " << _Wai << endl;
    _flightsize = 0;
    _U = _eta;
}

/*mem_b HPCCSrc::queuesize(){
  return 0;
  }

  mem_b HPCCSrc::maxsize(){
  return 0;
  }*/

void HPCCSrc::set_traffic_logger(TrafficLogger* pktlogger) {
    _flow.set_logger(pktlogger);
}

void HPCCSrc::log_me() {
    // avoid looping
    if (_log_me == true)
        return;

    cout << "Enabling logging on HPCCSrc " << _nodename << endl;
    _log_me = true;
    if (_sink)
        _sink->log_me();
}

void HPCCSrc::startflow(){
    cout << "startflow " << _flow._name << " at " << timeAsUs(eventlist().now()) << endl;
    _flow_started = true;
    _highest_sent = 0;
    _last_acked = 0;
    
    _acked_packets = 0;
    _packets_sent = 0;
    _done = false;
    
    eventlist().sourceIsPendingRel(*this,0);
}

void HPCCSrc::set_end_trigger(Trigger& end_trigger) {
    _end_trigger = &end_trigger;
}

void HPCCSrc::connect(Route* routeout, Route* routeback, HPCCSink& sink, simtime_picosec starttime) {
    assert(routeout);
    _route = routeout;
    
    _sink = &sink;
    _flow.set_id(get_id()); // identify the packet flow with the HPCC source that generated it
    _flow._name = _name;
    _sink->connect(*this, routeback);

    if (starttime != TRIGGER_START) {
        // Was startflow() here, which ignored starttime and started every
        // flow at t=0 whatever the matrix said. starttime is already in
        // picoseconds (connection_matrix.cpp), as in ndp.cpp; doNextEvent()
        // calls startflow() on this first wakeup.
        eventlist().sourceIsPending(*this,starttime);
    }
    else cout << "TRIGGER START " << _nodename << endl; 
}

/* PHPCCss a NACK.  Generally this involves queuing the NACKed packet
   for retransmission, but then waiting for a PULL to actually resend
   it.  However, sometimes the NACK has the PULL bit set, and then we
   resend immediately */
void HPCCSrc::processNack(const HPCCNack& nack){
    // Reordering (adaptive routing) makes the sink NACK every packet that
    // arrives above a hole, so NACKs arrive in bursts and can be overtaken by
    // ACKs. Ignore a NACK that (a) is behind the cumulative ACK we already
    // have, or (b) was triggered by a packet sent before our last go-back-N
    // (nack.ts() is the triggering data packet's send time, hpcc.cpp
    // send_nack): that go-back already resends everything it could ask for.
    // The original code went back on every NACK, moving _last_acked
    // backwards and zeroing _flightsize under ACKs still in flight, which
    // tripped the assert in processAck.
    if (nack.ackno() < _last_acked || nack.ts() < _last_goback_ts) {
        _stat_stale_nacks++;
        return;
    }
    _last_goback_ts = eventlist().now();
    _last_acked = nack.ackno();
    _rtx_packets_sent += _highest_sent - _last_acked;

    cout << "HPCC " << _name << " go back n from " <<  _highest_sent << " to " << _last_acked << " at " << timeAsUs(eventlist().now()) << " us" << endl;

    // Was _highest_sent > _flow_size; doNextEvent stops at >=, so a flow
    // whose size is a multiple of the MSS never restarted (same as roce.cpp).
    if (_flow_size && _highest_sent>=_flow_size && _last_acked < _flow_size){
        //restart the pacing of packets, this has stopped once we've passed the flow size but now a packet in the last window was lost.
        eventlist().sourceIsPendingRel(*this,0);
    }

    _highest_sent = _last_acked;
    _nacks_received ++;

    if (_legacy) {
        _cwnd = _mss;
    }
    // Paper / reference ns-3: go-back-N only; the window is set by INT,
    // not cut to one packet on a NACK (HTSIM NACKs carry no INT).
    _flightsize = 0;

    //this packet be sent when it is time to send a new packet!
}

/* PHPCCss an ACK.  Mostly just housekeeping*/
void HPCCSrc::processAck(const HPCCAck& ack) {
    HPCCAck::seq_t ackno = ack.ackno();

    if (ackno > _last_acked) { // a brand new ack    
        // Was assert(ackno - _last_acked <= _flightsize). After a go-back-N
        // (_flightsize = 0), ACKs for packets of the abandoned window that
        // were only reordered, not lost, still arrive and fired the assert.
        HPCCAck::seq_t newly = ackno - _last_acked;
        _flightsize -= min(newly, _flightsize);
        _last_acked = ackno;
        if (_highest_sent < ackno)
            _highest_sent = ackno;   // already delivered, do not resend
    }

    //HPCC magic here.
    /*
      21: Procedure NewAck(ack)
      22:    if ack.seq > lastUpdateSeq then
      23:        W = ComputeWind(MeasureInflight(ack), True);
      24:        lastUpdateSeq = snd_nxt;
      25:    else
      26:        W = ComputeWind(MeasureInflight(ack), False);
      27:    R = W/T; L = ack.L; 
    */   
    if (_legacy) {
        // Original HTSIM update (-hpcc_legacy). _cwnd is both W and Wc and is
        // overwritten on every ACK, so the once-per-RTT update compounds per
        // ACK; the INT reference is never refreshed (measureInFlight).
        if (ackno > _last_update_seq){
            _cwnd = computeWind(measureInFlight(ack), true);
            if (_trace) cout << "HPCCW " << timeAsUs(eventlist().now()) << " " << _nodename << " W " << _cwnd << " U " << _U << " full" << endl;
            _last_update_seq = _highest_sent;
        }
        else {
            _cwnd = computeWind(measureInFlight(ack), false);
        }
        _stat_maxW = max(_stat_maxW, (double)_cwnd);
        _stat_minW = min(_stat_minW, (double)_cwnd);
        _pacing_rate = _cwnd * 8 * pow(10,12) / _T;
        update_spacing();
    } else if (!_have_ref) {
        // First ACK: store its INT as the reference L; no rate change (the
        // reference ns-3 code does the same in its first RTT).
        storeReference(ack);
        _have_ref = true;
        _last_update_seq = _highest_sent;
    } else {
        // Algorithm 1, NewAck. W is recomputed on every ACK from the fixed
        // reference Wc (fast reaction); Wc, incStage and lastUpdateSeq only
        // move once per RTT, when the ACK covers the data sent at the last
        // update (ack.seq > lastUpdateSeq).
        double U = measureInflightPaper(ack);   // also does L = ack.L
        bool updateWc = ackno > _last_update_seq;
        double W = computeWindPaper(U, updateWc);
        if (updateWc) {
            _last_update_seq = _highest_sent;   // snd_nxt
            _stat_wc_updates++;
            if (_trace) {
                double n = _tr_tau > 0 ? _tr_tau : 1;
                cout << "HPCCW " << timeAsUs(eventlist().now()) << " " << _nodename << " W " << W << " U " << U
                     << " stage " << _incStage << " uq " << _tr_uq / n << " utx " << _tr_utx / n
                     << " hop " << _tr_hop / n << endl;
                _tr_tau = _tr_uq = _tr_utx = _tr_hop = 0;
            }
        }
        setWindow(W);                           // R = W / T
    }

    //cout << "INT Entries " << ack._int_hop << " qs " << ack._int_info[0]._queuesize << " TS " << timeAsUs(ack._int_info[0]._ts) << " TX " << ack._int_info[0]._txbytes << endl; 


    if (_logger) _logger->logHPCC(*this, HPCCLogger::HPCC_RCV);

    if (ackno >= _flow_size){
        cout << "Flow " << _name << " flowId " << flow_id() << " finished at " << timeAsUs(eventlist().now()) << " total bytes " << ackno << endl;
        print_hpcc_stats();
        _done = true;
        if (_end_trigger) {
            _end_trigger->activate();
        }

        return;
    }
}

void HPCCSrc::processPause(const EthPausePacket& p) {
    if (p.sleepTime()>0){
        //remote end is telling us to shut up.
        //cout << "Source " << str() << " PAUSE " << timeAsUs(eventlist().now()) << endl;
        //assert(_state_send != PAUSED);
        _state_send = PAUSED;
    }
    else {
        //we are allowed to send!
        //assert(_state_send != READY);
        _state_send = READY;
        //cout << "Source " << str() << " RESUME " << timeAsUs(eventlist().now()) << endl;
        eventlist().sourceIsPendingRel(*this,0);
    }
}

void HPCCSrc::receivePacket(Packet& pkt) 
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
    case HPCCNACK: 
        _nacks_received++;
        processNack((const HPCCNack&)pkt);
        pkt.free();
        return;
    case HPCCACK:
        _acks_received++;
        processAck((const HPCCAck&)pkt);
        pkt.free();
        return;
    default:
        abort();
    }
}

// Note: the data sequence number is the number of Byte1 of the packet, not the last byte.
void HPCCSrc::send_packet() {
    HPCCPacket* p = NULL;
    bool last_packet = false;

    assert(_flow_started);

    if (_flow_size && (_last_acked >= _flow_size || _highest_sent > _flow_size))
        //flow is finished
        return;

    if (_flow_size && _highest_sent + _mss >= _flow_size) {
        last_packet = true;
        //cout << _name << " sending last packet with SEQNO " << _highest_sent+1 << " at " << timeAsUs(eventlist().now()) << endl;
    }
    
    p = HPCCPacket::newpkt(_flow, *_route, _highest_sent+1, _mss, false, last_packet,_dstaddr);
    
    assert(p);
    p->set_pathid(_pathid);

    p->flow().logTraffic(*p,*this,TrafficLogger::PKT_CREATESEND);
    p->set_ts(eventlist().now());
    
    if (_log_me) {
        cout << "Sent " << _highest_sent+1 << " Flow Size: " << _flow_size << endl;
    }
    _highest_sent += _mss;
    _packets_sent++;

    _flightsize += _mss;

    //cout << "Sent " << _highest_sent+1 << " Flow Size: " << _flow_size << " Flow " << _name << " time " << timeAsUs(eventlist().now()) << endl;

    p->sendOn();
}

void HPCCSrc::doNextEvent() {
    if (!_flow_started){
        // first wakeup, scheduled by connect() at the matrix start time
        startflow();
        return;
    }

    if (_state_send==PAUSED)
        return;

    if (_flow_size && _highest_sent >= _flow_size) {
        //cout << _name << " stopping send coz highest_sent is " << _highest_sent << endl;
        return;
    }

    if (_time_last_sent==0 || eventlist().now() - _time_last_sent >= _packet_spacing){
        if (_cwnd >= _flightsize + _mss) 
            send_packet();

        _time_last_sent = eventlist().now();
    }

    simtime_picosec next_send = _time_last_sent + _packet_spacing;
    assert(next_send > eventlist().now());

    eventlist().sourceIsPending(*this, next_send);
}

double HPCCSrc::measureInFlight(const HPCCAck& ack){
    /*    Taken from https://datatracker.ietf.org/doc/draft-miao-tsv-hpcc/01/
          2:  u = 0;
          3:    for each link i on the path do
          4:                  ack.L[i].txBytes-L[i].txBytes
          txRate =  ----------------------------- ;
          ack.L[i].ts-L[i].ts
          5:               min(ack.L[i].qlen,L[i].qlen)      txRate
          u' = ----------------------------- +  ---------- ;
          ack.L[i].B*T                ack.L[i].B
          6:         if u' > u then
          7:             u = u'; tau = ack.L[i].ts -  L[i].ts;
          8:     tau = min(tau, T);
          9:     U = (1 - tau/T)*U + tau/T*u;
          10:    return U;*/

    double u = 0, uprime;
    uint32_t i;
    double txRate;
    simtime_picosec tau = _T;

    if (ack._int_hop == _link_count){
        for (i = 0;i<_link_count;i++){
            txRate = (ack._int_info[i]._txbytes - _link_info[i]._txbytes) * 8 * pow(10,12) / (ack._int_info[i]._ts - _link_info[i]._ts);

            uprime = min(ack._int_info[i]._queuesize, _link_info[i]._queuesize)*8 * pow (10,12) / ( (double)ack._int_info[i]._linkrate * _T ) + txRate / ack._int_info[i]._linkrate; 
            if (uprime > u) {
                u = uprime;
                tau = ack._int_info[i]._ts - _link_info[i]._ts;
            }
        }

        tau = min (tau, _T);

        _U = (1 - tau/_T)*_U + tau/_T*u;
    }
    else {    //reset path state
        _link_count = ack._int_hop;
        for (i = 0;i<_link_count;i++)
            _link_info[i] = ack._int_info[i];
    }

    return _U;
};

HPCCPacket::seq_t HPCCSrc::computeWind(double U, bool updateWc){
    /*
      11: Function ComputeWind(U, updateWc)
      12:    if U >= eta or incStage >= maxStagee then
      13:             Wc
      W = ----- + W_ai;
      U/eta
      14:        if updateWc then
      15:            incStagee = 0; Wc = W ;
      16:    else
      17:        W = Wc + W_ai ;
      18:        if updateWc then
      19:            incStage++; Wc = W ;
      20:    return W
    */
    HPCCPacket::seq_t W;
    
    if (_U >= _eta || _incStage >= _max_stages){
        W = _cwnd / (_U / _eta) + _Wai;

        if (updateWc){
            _incStage = 0;
            _cwnd = W;
        }
    }   
    else {
        W = _cwnd + _Wai;
        if (updateWc){
            _incStage++;
            _cwnd = W;
        }
    } 
    return W; 
};

void HPCCSrc::print_hpcc_stats() {
    cout << "HPCC_STATS " << _name << " maxW " << _stat_maxW << " minW " << _stat_minW
         << " Wc_updates " << _stat_wc_updates << " nacks " << _nacks_received
         << " stale_nacks " << _stat_stale_nacks << " rtx_pkts_est " << _rtx_packets_sent / _mss << endl;
}

void HPCCSrc::storeReference(const HPCCAck& ack) {
    _link_count = ack._int_hop;
    for (uint32_t i = 0; i < _link_count; i++)
        _link_info[i] = ack._int_info[i];
}

// Algorithm 1, MeasureInflight, with three differences from the legacy
// measureInFlight() above: L = ack.L after every ACK (line 27; legacy kept
// the first ACK's INT forever), tau/T in floating point (legacy divided two
// simtime_picosec integers, so tau/T was 0 unless tau >= T), and a hop whose
// switch changed (adaptive routing) or whose timestamp did not advance is
// skipped instead of producing a bogus or negative txRate.
double HPCCSrc::measureInflightPaper(const HPCCAck& ack) {
    if (ack._int_hop != _link_count) {   // path length changed: new reference
        storeReference(ack);
        return _U;
    }
    const double T = (double)_T;          // ps
    double u = 0, tau = T, uq = 0, utx = 0;
    uint32_t hop = 0;
    bool any = false;
    for (uint32_t i = 0; i < _link_count; i++) {
        const IntEntry& now = ack._int_info[i];
        const IntEntry& ref = _link_info[i];
        if (now._switchID != ref._switchID || now._ts <= ref._ts || now._txbytes < ref._txbytes)
            continue;
        double dt = (double)(now._ts - ref._ts);                       // ps
        double B = (double)now._linkrate;                              // bit/s
        double txRate = (double)(now._txbytes - ref._txbytes) * 8.0 * 1e12 / dt;  // bit/s
        // INT qlen is stamped in LosslessOutputQueue::completeService before
        // the departing packet is subtracted (queue_lossless_output.cpp:130
        // vs :144), so it always includes that packet itself. HPCC's qlen
        // (and the reference ns-3 switch, which stamps after dequeue) is the
        // queue left behind. Remove our own packet (all HPCC data packets are
        // _mss + ACKSIZE on the wire); otherwise an idle link reports a
        // phantom 1 MTU queue, U is biased up by 1 MTU/(B*T) (1.5 % at
        // 400 Gbps, 9 KB, 12 us) and the link settles at ~93 % instead of eta.
        double own = (double)(_mss + HPCCPacket::ACKSIZE);
        double qlen = max(0.0, (double)min(now._queuesize, ref._queuesize) - own);  // bytes
        double uprime = qlen * 8.0 / (B * T * 1e-12) + txRate / B;
        if (!any || uprime > u) {
            u = uprime;
            tau = dt;
            any = true;
            uq = qlen * 8.0 / (B * T * 1e-12);
            utx = txRate / B;
            hop = i;
        }
    }
    storeReference(ack);                  // L = ack.L
    if (any) {
        tau = min(tau, T);
        _U = (1.0 - tau / T) * _U + (tau / T) * u;
        if (_trace) {   // time-weighted split of u since the last Wc update
            _tr_tau += tau; _tr_uq += tau * uq; _tr_utx += tau * utx; _tr_hop += tau * hop;
        }
    }
    return _U;
}

// Algorithm 1, ComputeWind. Wc and incStage change only when updateWc.
double HPCCSrc::computeWindPaper(double U, bool updateWc) {
    double W;
    if (U >= _eta || _incStage >= _max_stages) {
        W = (U > 1e-9) ? _Wc_d / (U / _eta) + _Wai : _W_max;
        W = min(max(W, _W_min), _W_max);
        if (updateWc) {
            _incStage = 0;
            _Wc_d = W;
        }
    } else {
        W = _Wc_d + _Wai;
        W = min(max(W, _W_min), _W_max);
        if (updateWc) {
            _incStage++;
            _Wc_d = W;
        }
    }
    return W;
}

void HPCCSrc::setWindow(double W) {
    _cwnd = (HPCCPacket::seq_t)W;
    _Wc = (HPCCPacket::seq_t)_Wc_d;
    _stat_maxW = max(_stat_maxW, W);
    _stat_minW = min(_stat_minW, W);
    _pacing_rate = W * 8 * pow(10,12) / _T;   // R = W / T
    update_spacing();
}

////////////////////////////////////////////////////////////////
//  HPCC SINK
////////////////////////////////////////////////////////////////

/* Only use this constructor when there is only one for to this receiver */
HPCCSink::HPCCSink()
    : DataReceiver("HPCC_sink"),_cumulative_ack(0) , _total_received(0) 
{
    _src = 0;
    
    _nodename = "HPCCsink";
    _highest_seqno = 0;
    _log_me = false;
    _total_received = 0;
}

void HPCCSink::log_me() {
    // avoid looping
    if (_log_me == true)
        return;

    _log_me = true;

    if (_src)
        _src->log_me();  
}

/* Connect a src to this sink. */ 
void HPCCSink::connect(HPCCSrc& src, Route* route)
{
    _src = &src;
    _route = route;
    
    _cumulative_ack = 0;
    _drops = 0;
}


// Receive a packet.
// Note: _cumulative_ack is the last byte we've ACKed.
// seqno is the first byte of the new packet.
void HPCCSink::receivePacket(Packet& pkt) {
    /*
      if (random()%10==0){
      pkt.free();
      return;
      }*/

    switch (pkt.type()) {
    case HPCC:
        break;
    default:
        abort();
    }

    HPCCPacket *p = (HPCCPacket*)(&pkt);
    HPCCPacket::seq_t seqno = p->seqno();

    simtime_picosec ts = p->ts();
    //bool last_packet = ((HPCCPacket*)&pkt)->last_packet();

    if (seqno > _cumulative_ack+1){
        send_nack(ts,_cumulative_ack);      
    
        pkt.flow().logTraffic(pkt,*this,TrafficLogger::PKT_RCVDESTROY);

        p->free();

        //cout << "Wrong seqno received at HPCC SINK " << seqno << " expecting " << _cumulative_ack << endl;
        return;
    }

    int size = p->size()-HPCCPacket::ACKSIZE; 

    if (seqno == _cumulative_ack+1) { // it's the next expected seq no
        _cumulative_ack = seqno + size - 1;
    } else if (seqno < _cumulative_ack+1) {
        //must have been a bad retransmit
    }
    send_ack(ts,p->_int_info, p->_int_hop);
    // have we seen everything yet?
    pkt.flow().logTraffic(pkt,*this,TrafficLogger::PKT_RCVDESTROY);
    pkt.free();
}

void HPCCSink::send_ack(simtime_picosec ts, IntEntry* intinfo, uint32_t hops) {
    HPCCAck *ack = 0;
    ack = HPCCAck::newpkt(_src->_flow, *_route, _cumulative_ack,_srcaddr);
    ack->set_pathid(0);

    if (hops>0)
        ack->copy_int_info(intinfo,hops);

    ack->sendOn();
}

void HPCCSink::send_nack(simtime_picosec ts, HPCCPacket::seq_t ackno) {
    HPCCNack *nack = NULL;
    nack = HPCCNack::newpkt(_src->_flow, *_route, ackno,_srcaddr);

    nack->set_pathid(0);
    assert(nack);
    nack->flow().logTraffic(*nack,*this,TrafficLogger::PKT_CREATE);
    nack->set_ts(ts);
    nack->sendOn();
}




