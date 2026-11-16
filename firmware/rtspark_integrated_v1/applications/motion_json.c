#include "motion_json.h"
#include <string.h>

struct writer { char *out; size_t capacity, used; int failed; };

static void put_bytes(struct writer *w, const char *text, size_t length)
{
    if (w->failed || length > w->capacity - w->used) { w->failed = 1; return; }
    memcpy(w->out + w->used, text, length);
    w->used += length;
}

static void put_text(struct writer *w, const char *text) { put_bytes(w, text, strlen(text)); }

static void put_u64(struct writer *w, uint64_t value)
{
    char reverse[20];
    unsigned int count = 0;
    do {
        reverse[count++] = (char)('0' + value % 10u);
        value /= 10u;
    } while (value != 0);
    while (count != 0) put_bytes(w, &reverse[--count], 1);
}

/* Nominal physical scale, six decimals, matching the verified serial output. */
static void put_micro(struct writer *w, int32_t micro)
{
    uint32_t magnitude = micro < 0 ? (uint32_t)(-(int64_t)micro) : (uint32_t)micro;
    uint32_t divisor = 100000u, fraction = magnitude % 1000000u;
    if (micro < 0) put_bytes(w, "-", 1);
    put_u64(w, magnitude / 1000000u);
    put_bytes(w, ".", 1);
    for (; divisor != 0; divisor /= 10u) {
        char digit = (char)('0' + (fraction / divisor) % 10u);
        put_bytes(w, &digit, 1);
    }
}

static int identifier_valid(const char *text, size_t maximum)
{
    size_t i, length = strlen(text);
    if (length == 0 || length > maximum) return 0;
    for (i = 0; i < length; ++i) {
        char c = text[i];
        if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') ||
              c == '_' || c == '-')) return 0;
    }
    return 1;
}

size_t motion_json_encode(char *out, size_t capacity, const struct motion_batch *batch,
                          const char *device_id, const char *boot_id)
{
    static const char *const axes[6] = {
        "acceleration_x", "acceleration_y", "acceleration_z", "gyro_x", "gyro_y", "gyro_z"};
    struct writer w = {out, capacity, 0, 0};
    unsigned int sample, axis;

    if (!identifier_valid(device_id, 64) || !identifier_valid(boot_id, 128)) return 0;
    put_text(&w, "{\"schema_version\":1,\"device_id\":\"");
    put_text(&w, device_id);
    put_text(&w, "\",\"boot_id\":\"");
    put_text(&w, boot_id);
    put_text(&w, "\",\"seq\":");
    put_u64(&w, batch->seq);
    put_text(&w, ",\"sample_hz\":5.0,\"timestamp_unit\":\"ms\",\"timestamp_source\":\"device_monotonic\","
                 "\"sensor_model\":\"ICM-20608-G\",\"frame_id\":\"sensor_native_unmapped\","
                 "\"calibration_id\":null,\"acceleration_unit\":\"g\",\"gyro_unit\":\"rad/s\","
                 "\"gravity_handling\":\"included\",\"samples\":[");
    for (sample = 0; sample < BCA_BATCH_SAMPLES; ++sample) {
        put_text(&w, sample ? ",{\"t_ms\":" : "{\"t_ms\":");
        put_u64(&w, batch->samples[sample].t_ms);
        for (axis = 0; axis < 6; ++axis) {
            put_text(&w, ",\"");
            put_text(&w, axes[axis]);
            put_text(&w, "\":");
            put_micro(&w, batch->samples[sample].micro[axis]);
        }
        put_text(&w, "}");
    }
    put_text(&w, "]}");
    return w.failed ? 0 : w.used;
}
