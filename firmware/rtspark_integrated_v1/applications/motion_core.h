/* Versioned portable fixed-point signal path. No I/O, RTOS or allocation. */
#ifndef MOTION_CORE_H
#define MOTION_CORE_H
#include <stdint.h>
#define MOTION_TAPS 201u
#define MOTION_DECIMATION 10u
#define MOTION_DELAY_SAMPLES 100u
#define MOTION_FILTER_ID "fir201_50to5_q30_v1"
struct motion_filter {
    int16_t history[MOTION_TAPS][6];
    uint64_t times[MOTION_TAPS];
    uint32_t sequences[MOTION_TAPS];
    uint32_t next, count, phase;
};
struct motion_filtered { int64_t counts_q16[6]; uint64_t t_ms; uint32_t source_seq; };
void motion_filter_reset(struct motion_filter *f);
int motion_filter_push(struct motion_filter *f, const int16_t raw[6], uint64_t t_ms,
                       uint32_t seq, struct motion_filtered *out);
int motion_interval_valid(uint64_t previous, uint64_t current);
int32_t motion_accel_micro(int64_t counts_q16);
int32_t motion_gyro_micro(int64_t counts_q16);
uint64_t motion_extend_ticks(uint64_t extended, uint32_t previous, uint32_t current);
#endif
