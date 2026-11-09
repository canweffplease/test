// -*- c-basic-offset: 4; indent-tabs-mode: nil -*-        


#ifndef DCQCN_H
#define DCQCN_H

/*
 * A DCQCN source and sink
 */

#include <list>
#include <map>
//#include "util.h"
#include "math.h"
#include "config.h"
#include "network.h"
#include "rocepacket.h"
#include "cnppacket.h"
#include "queue.h"
#include "roce.h"
#include "eventlist.h"
#include "eth_pause_packet.h"
#include "trigger.h"
#include "ecn.h"
#define timeInf 0

class DCQCNSink;
class Switch;
class DCQCNSrc;

// Rate-increase / alpha timer of one DCQCN reaction point (faithful mode only).
// A separate EventSource so that timer wakeups do not go through
// RoceSrc::doNextEvent, which would start an extra packet-pacing chain on
// every wakeup (see HTSIM/OUR_CHANGES.md (section "DCQCN made faithful to the paper")).
class DCQCNTimer : public EventSource {
public:
    DCQCNTimer(EventList& eventlist, DCQCNSrc* src) : EventSource(eventlist, "DCQCN timer"), _src(src) {}
    virtual void doNextEvent();
private:
    DCQCNSrc* _src;
};

class DCQCNSrc : public RoceSrc {
    friend class DCQCNSink;
    friend class DCQCNTimer;
public:
    DCQCNSrc(RoceLogger* logger, TrafficLogger* pktlogger, EventList &eventlist, linkspeed_bps rate);

    virtual void receivePacket(Packet& pkt);
    virtual void processCNP(const CNPPacket& cnp);    
    virtual void increaseRate();
    virtual void doNextEvent();

    // should really be private, but loggers want to see:
    uint32_t _cnps_received;    

    // ---- parameters, shared by all flows (set from the command line) ----
    static simtime_picosec _cc_update_period;   // rate-increase timer T and alpha timer K (55 us)
    static double _g;                           // alpha EWMA gain (1/256)
    static uint32_t _F;                         // fast-recovery iterations (5)
    static uint64_t _B;                         // byte counter threshold, bytes (10 MB)
    static bool _legacy;                        // -dcqcn_legacy: original HTSIM behaviour
    static linkspeed_bps _RAI_cfg, _RHAI_cfg;   // -dcqcn_rai/-dcqcn_rhai (bps); 0 = scale to link
    static double _RAI_frac, _RHAI_frac;        // default R_AI, R_HAI as a fraction of line rate

    // legacy mode only: the original single alpha shared by every DCQCNSrc
    static double _shared_alpha;

    // ---- per-flow (per reaction point) state ----
    double _alpha;
    linkspeed_bps _RAI, _RHAI;

    double& alpha() { return _legacy ? _shared_alpha : _alpha; }
    linkspeed_bps rate() const { return _RC; }
    linkspeed_bps target_rate() const { return _RT; }

private:
    void timerFired();
    void armTimer();

    simtime_picosec _last_cc_update, _last_alpha_update;
    linkspeed_bps _RC, _RT, _link;
    
    enum increase_state {invalid = 0, fast_recovery=1,active_increase=2};
    //increase_state _ai_state;
    uint16_t _T,_BC;
    uint64_t _byte_counter, _old_highest_sent;

    DCQCNTimer _timer;              // faithful mode only
    simtime_picosec _timer_due;     // time of the one live timer event; stale ones are ignored
    bool _timer_started;

};

class DCQCNSink : public RoceSink, public EventSource {
    friend class DCQCNSrc;
public:
    DCQCNSink(EventList &eventlist);
    virtual void doNextEvent();

    virtual void receivePacket(Packet& pkt);  
    static simtime_picosec _cnp_interval;

    inline id_t get_id() const {return RoceSink::get_id();}

private:
    simtime_picosec _last_cnp_sent_time;
 
    uint32_t _marked_packets_since_last_cnp;
    uint32_t _packets_since_last_cnp;

    // Mechanism
    void send_cnp();
};

#endif

