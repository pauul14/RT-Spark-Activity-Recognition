/* Five-sample batches between the acquisition thread and the MQTT thread.
 * Portable: no I/O or RTOS. The caller supplies the critical-section hooks.
 *
 * Ownership: `partial` belongs to the single producer (acquisition). A committed
 * batch belongs to the ring until telemetry_pop() copies it out; nothing else
 * touches a slot. Neither side ever blocks. When full, the oldest batch is
 * dropped so recent motion survives an outage, and its sequence number is never
 * reused, which lets the cloud see the loss instead of a silent splice.
 */
#ifndef TELEMETRY_H
#define TELEMETRY_H
#include <stdint.h>
#include "app_config.h"

struct motion_sample { uint64_t t_ms; int32_t micro[6]; };
struct motion_batch { uint64_t seq; struct motion_sample samples[BCA_BATCH_SAMPLES]; };

struct telemetry_stats {
    uint64_t built, popped;
    uint32_t dropped_oldest, partial_discarded, peak_depth;
};

typedef uintptr_t (*telemetry_lock_fn)(void);
typedef void (*telemetry_unlock_fn)(uintptr_t state);

struct telemetry {
    struct motion_batch slots[BCA_BATCH_RING];
    struct motion_batch partial;
    struct telemetry_stats stats;
    uint64_t next_seq, last_t_ms;
    uint32_t head, count, partial_count, partial_epoch;
    telemetry_lock_fn lock;
    telemetry_unlock_fn unlock;
};

void telemetry_init(struct telemetry *t, telemetry_lock_fn lock, telemetry_unlock_fn unlock);
/* Producer only; bounded time, never waits. */
void telemetry_push(struct telemetry *t, uint64_t t_ms, const int32_t micro[6], uint32_t filter_epoch);
/* Consumer only. Returns 1 and copies the oldest batch, or returns 0 when empty. */
int telemetry_pop(struct telemetry *t, struct motion_batch *out);
void telemetry_stats_read(struct telemetry *t, struct telemetry_stats *out, uint32_t *depth);
#endif
