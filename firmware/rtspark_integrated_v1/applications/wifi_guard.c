#include "wifi_guard.h"
static int reached(uint32_t now, uint32_t deadline) { return (int32_t)(now-deadline)>=0; }
void wifi_guard_stop(struct wifi_guard *g, unsigned reason)
{
    if (!g->stopped) g->reason=reason;
    g->stopped=1; g->ready=0;
}
int wifi_guard_tick(struct wifi_guard *g, uint32_t now)
{
    if (g->armed && !g->stopped) {
        if (reached(now,g->deadline)) wifi_guard_stop(g,WG_DEADLINE);
        else if (!g->ready && reached(now,g->join_deadline)) wifi_guard_stop(g,WG_JOIN_TIMEOUT);
    }
    return g->stopped;
}
int wifi_guard_arm(struct wifi_guard *g, uint32_t now, uint32_t lease_ms)
{
    if (g->armed || g->stopped || lease_ms<30000u || lease_ms>600000u) return 0;
    g->armed=1; g->deadline=now+lease_ms; g->join_deadline=now+(lease_ms<60000u?lease_ms:60000u);
    return 1;
}
int wifi_guard_join(struct wifi_guard *g, uint32_t now)
{
    if (wifi_guard_tick(g,now) || !g->armed || g->attempts) return 0;
    ++g->attempts; return 1; /* counted at the actual RW007 JOIN command admission */
}
int wifi_guard_ready(struct wifi_guard *g, uint32_t now)
{
    if (wifi_guard_tick(g,now) || !g->armed || g->attempts!=1) return 0;
    g->ready=1; return 1;
}
