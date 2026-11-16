/* Verified 50 Hz acquisition and 5 Hz filtered output. */
#ifndef MOTION_ACQ_H
#define MOTION_ACQ_H
#include <rtthread.h>
#include "telemetry.h"

enum motion_trace_mode { MOTION_TRACE_OFF, MOTION_TRACE_FILTERED, MOTION_TRACE_RAW };

struct motion_counters {
    rt_uint32_t raw, filtered, dropped_raw, dropped_filtered;
    rt_uint32_t irregular, estimated_missed, read_overruns, rail, filter_resets;
    rt_uint32_t max_read_ms, fatal;
};

extern struct telemetry g_telemetry;

/* Readies the ring at boot, so MQTT and the console work with the sensor stopped. */
void motion_prepare(void);
/* Identity, reset, configuration, 13 readbacks, then the acquisition thread. */
rt_err_t motion_start(void);
void motion_counters_read(struct motion_counters *out);
#endif
