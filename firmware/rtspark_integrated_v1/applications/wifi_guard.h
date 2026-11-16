/* Portable one-shot diagnostic lease. Caller serializes access (IRQ lock on MCU). */
#ifndef WIFI_GUARD_H
#define WIFI_GUARD_H
#include <stdint.h>
enum wifi_guard_reason { WG_NONE, WG_OPERATOR, WG_DEADLINE, WG_JOIN_TIMEOUT, WG_LINK_LOST, WG_FAILURE };
struct wifi_guard {
    volatile uint32_t deadline, join_deadline, attempts;
    volatile unsigned armed, stopped, ready, reason;
};
int wifi_guard_arm(struct wifi_guard *g, uint32_t now, uint32_t lease_ms);
int wifi_guard_join(struct wifi_guard *g, uint32_t now);
int wifi_guard_tick(struct wifi_guard *g, uint32_t now);
void wifi_guard_stop(struct wifi_guard *g, unsigned reason);
int wifi_guard_ready(struct wifi_guard *g, uint32_t now);
#endif
