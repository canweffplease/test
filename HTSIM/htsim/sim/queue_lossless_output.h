// -*- c-basic-offset: 4; indent-tabs-mode: nil -*-        
#ifndef _LOSSLESS_OUTPUT_QUEUE_H
#define _LOSSLESS_OUTPUT_QUEUE_H
/*
 * A FIFO queue that supports PAUSE frames and lossless operation
 */

#include <list>
#include <random>
#include "queue.h"
#include "config.h"
#include "eventlist.h"
#include "network.h"
#include "loggertypes.h"
#include "eth_pause_packet.h"
#include "ecn.h"

class LosslessOutputQueue : public Queue {
public:
    LosslessOutputQueue(linkspeed_bps bitrate, mem_b maxsize, EventList &eventlist, QueueLogger* logger);

    void receivePacket(Packet& pkt);
    void receivePacket(Packet& pkt,VirtualQueue* q);

    void beginService();
    void completeService();

    bool is_paused() { return _state_send == PAUSED || _state_send == PAUSE_RECEIVED;}

    enum queue_state {PAUSED,READY,PAUSE_RECEIVED};

private:
    list<VirtualQueue*> _vq;

    int _state_send;
    int _sending;
    uint64_t _txbytes;

public:
    static int _ecn_enabled;
    static int _K;

    // Probabilistic (RED-style) ECN marking, DCQCN paper (Zhu et al.,
    // SIGCOMM 2015), congestion point (CP) RED curve, Fig. 5. Off by default: then the step rule
    // "mark if _queuesize > _K" above is used unchanged. Enabled from
    // main_roce.cpp by -ecn_kmin/-ecn_kmax/-ecn_pmax (HTSIM/OUR_CHANGES.md (section "DCQCN switch marking: probabilistic (RED) ECN")).
    // Marking probability on the instantaneous queue q (bytes, at dequeue):
    //   0 if q <= Kmin,  Pmax*(q-Kmin)/(Kmax-Kmin) if Kmin < q <= Kmax,  1 if q > Kmax.
    // Only data packets (type ROCE) are marked or consume random draws.
    static bool _ecn_red;
    static mem_b _ecn_kmin;
    static mem_b _ecn_kmax;
    static double _ecn_pmax;
    static void enable_red(mem_b kmin, mem_b kmax, double pmax, unsigned seed);
    // one decision: 0 = no mark, 1 = probabilistic mark, 2 = forced (q > Kmax).
    // Draws from _ecn_rng only if Kmin < q <= Kmax.
    static int red_mark(mem_b q);
    static uint64_t _ecn_seen, _ecn_drawn, _ecn_marked_prob, _ecn_marked_forced;  // data packets only
private:
    static std::mt19937_64 _ecn_rng;  // own stream, seeded from -seed, so marking draws
                                      // do not shift the global rand() stream used elsewhere
};

#endif
