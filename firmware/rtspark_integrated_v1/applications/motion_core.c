#include "motion_core.h"
#include "filter_coeffs.h"
#include <string.h>

void motion_filter_reset(struct motion_filter *f) { memset(f, 0, sizeof(*f)); }

int motion_interval_valid(uint64_t previous, uint64_t current)
{
    /* A timing break invalidates the uniformly sampled FIR history. */
    return current > previous && current - previous >= 18u && current - previous <= 22u;
}
uint64_t motion_extend_ticks(uint64_t extended, uint32_t previous, uint32_t current)
{
    /* Unsigned subtraction extends one wrap; caller must run within a wrap. */
    return extended + (uint32_t)(current - previous);
}
int32_t motion_accel_micro(int64_t counts_q16)
{
    return (int32_t)((counts_q16 * 1000000LL) / (4096LL * 65536LL));
}
int32_t motion_gyro_micro(int64_t counts_q16)
{
    /* 1e6*pi/(180*32.8), rational approximation; no floating point printf. */
    return (int32_t)((counts_q16 * 17453293LL) / (32800LL * 65536LL));
}
int motion_filter_push(struct motion_filter *f, const int16_t raw[6], uint64_t t_ms,
                       uint32_t seq, struct motion_filtered *out)
{
    uint32_t i, axis, at, center;
    for (axis=0; axis<6; ++axis) f->history[f->next][axis] = raw[axis];
    f->times[f->next] = t_ms;
    f->sequences[f->next] = seq;
    f->next = (f->next + 1u) % MOTION_TAPS;
    if (f->count < MOTION_TAPS) {
        ++f->count;
        if (f->count < MOTION_TAPS) return 0;
        f->phase = 0;
    } else {
        if (++f->phase < MOTION_DECIMATION) return 0;
        f->phase = 0;
    }
    /* f->next is oldest; coefficient symmetry gives a linear phase delay. */
    for (axis=0; axis<6; ++axis) {
        int64_t sum=0;
        at=f->next;
        for (i=0; i<MOTION_TAPS; ++i) {
            sum += (int64_t)motion_coeff_q30[i] * f->history[at][axis];
            if (++at == MOTION_TAPS) at=0;
        }
        /* Signed division (not a right shift) specifies truncation toward zero. */
        out->counts_q16[axis] = sum / 16384LL;
    }
    center=(f->next + MOTION_DELAY_SAMPLES) % MOTION_TAPS;
    out->t_ms=f->times[center];
    out->source_seq=f->sequences[center];
    return 1;
}
