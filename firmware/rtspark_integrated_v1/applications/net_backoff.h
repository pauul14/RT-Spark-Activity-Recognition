/* Doubling reconnect delay, shared by Wi-Fi and MQTT. Portable. */
#ifndef NET_BACKOFF_H
#define NET_BACKOFF_H
#include <stdint.h>

static inline uint32_t bca_backoff_next(uint32_t current_ms, uint32_t minimum_ms, uint32_t maximum_ms)
{
    if (current_ms < minimum_ms) return minimum_ms;
    return current_ms >= maximum_ms / 2u ? maximum_ms : current_ms * 2u;
}
#endif
