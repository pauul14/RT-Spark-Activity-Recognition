#include "telemetry.h"
#include <string.h>

void telemetry_init(struct telemetry *t, telemetry_lock_fn lock, telemetry_unlock_fn unlock)
{
    memset(t, 0, sizeof(*t));
    t->lock = lock;
    t->unlock = unlock;
}

static int interval_valid(uint64_t previous, uint64_t current)
{
    return current > previous && current - previous >= BCA_SAMPLE_INTERVAL_MIN_MS &&
           current - previous <= BCA_SAMPLE_INTERVAL_MAX_MS;
}

void telemetry_push(struct telemetry *t, uint64_t t_ms, const int32_t micro[6], uint32_t filter_epoch)
{
    struct motion_sample *sample;
    uintptr_t state;

    /* A batch is never padded or joined across a timing break or filter reset. */
    if (t->partial_count != 0 &&
        (filter_epoch != t->partial_epoch || !interval_valid(t->last_t_ms, t_ms))) {
        t->partial_count = 0;
        state = t->lock();
        ++t->stats.partial_discarded;
        t->unlock(state);
    }
    if (t->partial_count == 0) t->partial_epoch = filter_epoch;
    sample = &t->partial.samples[t->partial_count];
    sample->t_ms = t_ms;
    memcpy(sample->micro, micro, sizeof(sample->micro));
    t->last_t_ms = t_ms;
    if (++t->partial_count < BCA_BATCH_SAMPLES) return;

    t->partial_count = 0;
    t->partial.seq = t->next_seq++;
    state = t->lock();
    if (t->count == BCA_BATCH_RING) {
        t->head = (t->head + 1u) % BCA_BATCH_RING;
        --t->count;
        ++t->stats.dropped_oldest;
    }
    t->slots[(t->head + t->count) % BCA_BATCH_RING] = t->partial;
    ++t->count;
    ++t->stats.built;
    if (t->count > t->stats.peak_depth) t->stats.peak_depth = t->count;
    t->unlock(state);
}

int telemetry_pop(struct telemetry *t, struct motion_batch *out)
{
    uintptr_t state = t->lock();
    if (t->count == 0) {
        t->unlock(state);
        return 0;
    }
    *out = t->slots[t->head];
    t->head = (t->head + 1u) % BCA_BATCH_RING;
    --t->count;
    ++t->stats.popped;
    t->unlock(state);
    return 1;
}

void telemetry_stats_read(struct telemetry *t, struct telemetry_stats *out, uint32_t *depth)
{
    uintptr_t state = t->lock();
    *out = t->stats;
    *depth = t->count;
    t->unlock(state);
}
