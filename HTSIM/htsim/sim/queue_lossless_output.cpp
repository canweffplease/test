// -*- c-basic-offset: 4; indent-tabs-mode: nil -*-        
#include <math.h>
#include <iostream>
#include <sstream>
#include <cstdlib>
#include "switch.h"
#include "hpccpacket.h"
#include "queue_lossless_output.h"
#include "queue_lossless_input.h"

int LosslessOutputQueue::_ecn_enabled = false;
int LosslessOutputQueue::_K = 0;

bool LosslessOutputQueue::_ecn_red = false;
mem_b LosslessOutputQueue::_ecn_kmin = 0;
mem_b LosslessOutputQueue::_ecn_kmax = 0;
double LosslessOutputQueue::_ecn_pmax = 0;
uint64_t LosslessOutputQueue::_ecn_seen = 0;
uint64_t LosslessOutputQueue::_ecn_drawn = 0;
uint64_t LosslessOutputQueue::_ecn_marked_prob = 0;
uint64_t LosslessOutputQueue::_ecn_marked_forced = 0;
std::mt19937_64 LosslessOutputQueue::_ecn_rng;

static void print_ecn_red_stats() {
    cout << "ECN_RED stats: data pkts dequeued " << LosslessOutputQueue::_ecn_seen
         << " drawn(Kmin<q<=Kmax) " << LosslessOutputQueue::_ecn_drawn
         << " marked_prob " << LosslessOutputQueue::_ecn_marked_prob
         << " marked_forced(q>Kmax) " << LosslessOutputQueue::_ecn_marked_forced << endl;
}

void LosslessOutputQueue::enable_red(mem_b kmin, mem_b kmax, double pmax, unsigned seed) {
    assert(kmin >= 0 && kmax >= kmin && pmax >= 0 && pmax <= 1);
    _ecn_red = true;
    _ecn_kmin = kmin;
    _ecn_kmax = kmax;
    _ecn_pmax = pmax;
    _ecn_rng.seed(seed);
    atexit(print_ecn_red_stats);
}

// Debug: ECN_TRACE=1 (RED mode only) prints "ECN_MARK <us> <queue> <flow name> prob|forced <q bytes>" per mark.
static bool ecn_trace = getenv("ECN_TRACE") != NULL;

int LosslessOutputQueue::red_mark(mem_b q) {
    _ecn_seen++;
    if (q > _ecn_kmax) {           // above Kmax: always mark (same strict '>' as the step rule)
        _ecn_marked_forced++;
        return 2;
    }
    if (q <= _ecn_kmin)            // never true when Kmin == Kmax, so no division by zero below
        return 0;
    double p = _ecn_pmax * (double)(q - _ecn_kmin) / (double)(_ecn_kmax - _ecn_kmin);
    _ecn_drawn++;
    double u = (double)(_ecn_rng() >> 11) * (1.0 / 9007199254740992.0);   // uniform [0,1), 53 bits
    if (u < p) {
        _ecn_marked_prob++;
        return 1;
    }
    return 0;
}

LosslessOutputQueue::LosslessOutputQueue(linkspeed_bps bitrate, mem_b maxsize, 
                                         EventList& eventlist, QueueLogger* logger)
    : Queue(bitrate,maxsize,eventlist,logger), 
      _state_send(READY)
{
    //assume worst case: PAUSE frame waits for one MSS packet to be sent to other switch, and there is 
    //an MSS just beginning to be sent when PAUSE frame arrives; this means 2 packets per incoming
    //port, and we must have buffering for all ports except this one (assuming no one hop cycles!)

    _sending = 0;

    _txbytes = 0;

    stringstream ss;
    ss << "queue lossless output(" << bitrate/1000000 << "Mb/s," << maxsize << "bytes)";
    _nodename = ss.str();
}


void
LosslessOutputQueue::receivePacket(Packet& pkt){
    if (pkt.type()==ETH_PAUSE)
        receivePacket(pkt,NULL);
    else {
        LosslessInputQueue* q = pkt.get_ingress_queue();
        pkt.clear_ingress_queue();
        receivePacket(pkt,dynamic_cast<VirtualQueue*>(q));
    }
}

void
LosslessOutputQueue::receivePacket(Packet& pkt,VirtualQueue* prev) 
{
    //is this a PAUSE frame? 
    if (pkt.type()==ETH_PAUSE){
        EthPausePacket* p = (EthPausePacket*)&pkt;

        if (p->sleepTime()>0){
            //remote end is telling us to shut up.
            //assert(_state_send == READY);
            if (_sending)
                //we have a packet in flight
                _state_send = PAUSE_RECEIVED;
            else
                _state_send = PAUSED;

            //cout << timeAsMs(eventlist().now()) << " " << _name << " PAUSED "<<endl;            
        }
        else {
            //we are allowed to send!
            _state_send = READY;
            //cout << timeAsMs(eventlist().now()) << " " << _name << " GO "<<endl;

            //start transmission if we have packets to send!
            if(_enqueued.size()>0&&!_sending)
                beginService();
        }
        
        pkt.free();
        return;
    }

    /* normal packet, enqueue it */

    //remember the virtual queue that has sent us this packet; will notify the vq once the packet has left our buffer.
    assert(prev!=NULL);

    pkt.flow().logTraffic(pkt, *this, TrafficLogger::PKT_ARRIVE);

    bool queueWasEmpty = _enqueued.empty();

    _vq.push_front(prev);
    Packet* pkt_p = &pkt;
    _enqueued.push(pkt_p);

    _queuesize += pkt.size();

    if (_queuesize > _maxsize){
        cout << " Queue " << _name << " LOSSLESS not working! I should have dropped this packet" << _queuesize / Packet::data_packet_size() << endl;
    }

    if (_logger) 
        _logger->logQueue(*this, QueueLogger::PKT_ENQUEUE, pkt);

    if (queueWasEmpty && _state_send == READY) {
        /* schedule the dequeue event */
        assert(_enqueued.size()==1);
        beginService();
    }
}

void LosslessOutputQueue::beginService(){
    assert(_state_send==READY&&!_sending);

    Queue::beginService();
    _sending = 1;
}

void LosslessOutputQueue::completeService(){
    /* dequeue the packet */
    assert(!_enqueued.empty());

    Packet* pkt = _enqueued.pop();
    VirtualQueue* q = _vq.back();

    //_enqueued.pop_back();
    _vq.pop_back();

    //mark on deque
    if (_ecn_enabled) {
        if (_ecn_red) {
            // probabilistic marking (OUR_CHANGES.md (section "DCQCN switch marking: probabilistic (RED) ECN")); data packets only
            int m = pkt->type() == ROCE ? red_mark(_queuesize) : 0;
            if (m) {
                pkt->set_flags(pkt->flags() | ECN_CE);
                if (ecn_trace)
                    cout << "ECN_MARK " << timeAsUs(eventlist().now()) << " " << _name << " "
                         << pkt->flow().str() << " " << (m == 1 ? "prob" : "forced") << " " << _queuesize << endl;
            }
        } else if (_queuesize > _K)
            pkt->set_flags(pkt->flags() | ECN_CE); 
    }

    if (pkt->type()==HPCC){
        //HPPC INT information adding to packet
        HPCCPacket* h = dynamic_cast<HPCCPacket*>(pkt);
        assert(h->_int_hop<5);

        h->_int_info[h->_int_hop]._queuesize = _queuesize;
        h->_int_info[h->_int_hop]._ts = eventlist().now();

        if (_switch){
            h->_int_info[h->_int_hop]._switchID = _switch->getID();
            h->_int_info[h->_int_hop]._type = _switch->getType();
        }

        h->_int_info[h->_int_hop]._txbytes = _txbytes;
        h->_int_info[h->_int_hop]._linkrate = _bitrate;

        h->_int_hop++;
    }   

    _queuesize -= pkt->size();
    _txbytes += pkt->size();

    pkt->flow().logTraffic(*pkt, *this, TrafficLogger::PKT_DEPART);

    if (_logger) _logger->logQueue(*this, QueueLogger::PKT_SERVICE, *pkt);

    //tell the virtual input queue this packet is done!
    q->completedService(*pkt);

    //this is used for bandwidth utilization tracking. 
    log_packet_send(drainTime(pkt));

    //if (((uint64_t)timeAsUs(eventlist().now()))%5==0)
    //    cout << "Queue bandwidth utilization " << average_utilization() << "%" << endl;

    /* tell the packet to move on to the next pipe */
    pkt->sendOn();

    _sending = 0;

    //if (_state_send!=READY){
    //cout << timeAsMs(eventlist().now()) << " queue " << _name << " not ready but sending pkt " << pkt->type() << endl;
    //}

    if (_state_send == PAUSE_RECEIVED)
        _state_send = PAUSED;

    if (!_enqueued.empty()) {
        if (_state_send == READY)
            /* start packet transmission, schedule the next dequeue event */
            beginService();
    }
}
