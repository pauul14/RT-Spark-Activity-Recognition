/* Sensor contract v1 encoder. Portable; integer formatting only. */
#ifndef MOTION_JSON_H
#define MOTION_JSON_H
#include <stddef.h>
#include "telemetry.h"

/* Writes one complete message and returns its length, or 0 when it would not fit
 * or an identifier fails the backend pattern. Output is not NUL-terminated and is
 * a pure function of its inputs, so a QoS 1 retry resends identical bytes.
 */
size_t motion_json_encode(char *out, size_t capacity, const struct motion_batch *batch,
                          const char *device_id, const char *boot_id);
#endif
