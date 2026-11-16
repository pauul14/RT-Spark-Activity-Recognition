/* Cloud prediction messages: strict parsing, freshness and what may be displayed.
 * Portable. See docs/PREDICTION_CONTRACT.md; the backend does not publish this yet.
 *
 * Staleness needs no wall clock: boot_id, sequence_end and device_timestamp_ms all
 * echo values this device generated, so old or foreign messages cannot satisfy them.
 */
#ifndef PREDICTION_H
#define PREDICTION_H
#include <stddef.h>
#include <stdint.h>
#include "app_config.h"

#define PRED_ID_MAX 128u
#define BCA_THREE_CLASS_MODEL_ID "board-sha256:134446e8e056d45cb5cce01da0343e0d88e443aa55b2dc7843a15dfc06fa63eb:board_4s_v2_gravity_free"

enum pred_activity { PRED_ACTIVITY_NONE, PRED_ACTIVITY_WALKING, PRED_ACTIVITY_RUNNING, PRED_ACTIVITY_STATIONARY };

enum pred_result {
    PRED_ACCEPTED,
    PRED_REJECT_FORMAT,         /* malformed, unknown, duplicate or missing field */
    PRED_REJECT_SCHEMA,
    PRED_REJECT_ACTIVITY,
    PRED_REJECT_DEVICE,
    PRED_REJECT_BOOT,
    PRED_REJECT_REPLAY,         /* prediction_seq did not increase */
    PRED_REJECT_FUTURE,         /* refers to data this device has not produced */
    PRED_REJECT_STALE,
    PRED_REJECT_RETAINED,       /* outlives the data it describes */
    PRED_REJECT_TOPIC,          /* anything but this device's own prediction topic */
    PRED_RESULT_COUNT
};

struct pred_message {
    char device_id[PRED_ID_MAX + 1], boot_id[PRED_ID_MAX + 1], model_id[PRED_ID_MAX + 1];
    uint64_t schema_version, prediction_seq, sequence_end, device_timestamp_ms;
    enum pred_activity activity;
    int hardware_validated;
};

struct pred_identity {
    const char *device_id, *boot_id;
    uint64_t now_ms;            /* current device monotonic time */
    uint64_t latest_seq;        /* newest motion seq offered to the transport */
    int any_published;
};

struct pred_state {
    uint64_t last_seq, accepted_ms;
    enum pred_activity activity;
    int have_seq, have_accepted, validated;
    uint32_t results[PRED_RESULT_COUNT];
};

enum pred_result pred_parse(const char *json, size_t length, struct pred_message *out);
/* Parse, validate and, only on success, update the state. */
enum pred_result pred_process(struct pred_state *state, const struct pred_identity *identity,
                              const char *json, size_t length);
/* The MQTT entry point: transport checks first, then pred_process. */
enum pred_result pred_process_message(struct pred_state *state, const struct pred_identity *identity,
                                      const char *expected_topic, const char *topic, size_t topic_length,
                                      int retained, const char *json, size_t length);
/* The only text the display may show for the activity line. WALKING or RUNNING
 * requires a fresh message the cloud explicitly marked hardware_validated.
 */
const char *pred_display_text(const struct pred_state *state, uint64_t now_ms, int link_up);
/* True only while pred_display_text is showing a validated activity. */
int pred_display_is_real(const struct pred_state *state, uint64_t now_ms);
#endif
