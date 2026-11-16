#include "prediction.h"
#include <string.h>

#define MAX_JSON_INTEGER 9007199254740991ULL

struct cursor { const char *at, *end; };

static void skip_space(struct cursor *c)
{
    while (c->at < c->end && (*c->at == ' ' || *c->at == '\t' || *c->at == '\r' || *c->at == '\n')) ++c->at;
}

static int take(struct cursor *c, char expected)
{
    skip_space(c);
    if (c->at >= c->end || *c->at != expected) return 0;
    ++c->at;
    return 1;
}

/* Identifiers only: printable ASCII, no escapes, so nothing needs decoding. */
static int read_string(struct cursor *c, char *out, size_t capacity)
{
    size_t used = 0;
    if (!take(c, '"')) return 0;
    while (c->at < c->end) {
        char value = *c->at++;
        if (value == '"') { out[used] = '\0'; return 1; }
        if (value == '\\' || (unsigned char)value < 0x20u || (unsigned char)value > 0x7Eu ||
            used + 1 >= capacity) return 0;
        out[used++] = value;
    }
    return 0;
}

static int read_integer(struct cursor *c, uint64_t *out)
{
    uint64_t value = 0;
    const char *start;
    skip_space(c);
    start = c->at;
    while (c->at < c->end && *c->at >= '0' && *c->at <= '9') {
        value = value * 10u + (uint64_t)(*c->at - '0');
        if (value > MAX_JSON_INTEGER) return 0;
        ++c->at;
    }
    if (c->at == start || (start[0] == '0' && c->at - start > 1)) return 0;
    if (c->at < c->end && (*c->at == '.' || *c->at == 'e' || *c->at == 'E')) return 0;
    *out = value;
    return 1;
}

static int read_literal(struct cursor *c, const char *text)
{
    size_t length = strlen(text);
    if ((size_t)(c->end - c->at) < length || memcmp(c->at, text, length) != 0) return 0;
    c->at += length;
    return 1;
}

static int read_boolean(struct cursor *c, int *out)
{
    skip_space(c);
    if (read_literal(c, "true")) { *out = 1; return 1; }
    if (read_literal(c, "false")) { *out = 0; return 1; }
    return 0;
}

enum { FIELD_SCHEMA, FIELD_DEVICE, FIELD_BOOT, FIELD_SEQ, FIELD_SEQUENCE_END, FIELD_TIMESTAMP,
       FIELD_ACTIVITY, FIELD_MODEL, FIELD_VALIDATED, FIELD_COUNT };

static const char *const field_names[FIELD_COUNT] = {
    "schema_version", "device_id", "boot_id", "prediction_seq", "sequence_end",
    "device_timestamp_ms", "activity", "model_id", "hardware_validated"};

enum pred_result pred_parse(const char *json, size_t length, struct pred_message *out)
{
    struct cursor c;
    uint32_t seen = 0;
    char key[24], activity[16] = {0};
    int field;

    if (json == NULL || length == 0 || length > BCA_PRED_JSON_MAX) return PRED_REJECT_FORMAT;
    memset(out, 0, sizeof(*out));
    c.at = json;
    c.end = json + length;
    if (!take(&c, '{')) return PRED_REJECT_FORMAT;
    for (;;) {
        int ok;
        if (!read_string(&c, key, sizeof(key)) || !take(&c, ':')) return PRED_REJECT_FORMAT;
        for (field = 0; field < FIELD_COUNT; ++field)
            if (strcmp(key, field_names[field]) == 0) break;
        if (field == FIELD_COUNT || (seen & (1u << field)) != 0) return PRED_REJECT_FORMAT;
        seen |= 1u << field;
        switch (field) {
        case FIELD_SCHEMA:       ok = read_integer(&c, &out->schema_version); break;
        case FIELD_DEVICE:       ok = read_string(&c, out->device_id, sizeof(out->device_id)); break;
        case FIELD_BOOT:         ok = read_string(&c, out->boot_id, sizeof(out->boot_id)); break;
        case FIELD_SEQ:          ok = read_integer(&c, &out->prediction_seq); break;
        case FIELD_SEQUENCE_END: ok = read_integer(&c, &out->sequence_end); break;
        case FIELD_TIMESTAMP:    ok = read_integer(&c, &out->device_timestamp_ms); break;
        case FIELD_ACTIVITY:     ok = read_string(&c, activity, sizeof(activity)); break;
        case FIELD_MODEL:        ok = read_string(&c, out->model_id, sizeof(out->model_id)); break;
        default:                 ok = read_boolean(&c, &out->hardware_validated); break;
        }
        if (!ok) return PRED_REJECT_FORMAT;
        if (take(&c, ',')) continue;
        if (take(&c, '}')) break;
        return PRED_REJECT_FORMAT;
    }
    skip_space(&c);
    if (c.at != c.end || seen != (1u << FIELD_COUNT) - 1u) return PRED_REJECT_FORMAT;
    if ((out->schema_version != 1u && out->schema_version != 2u) || out->model_id[0] == '\0') return PRED_REJECT_SCHEMA;
    if ((out->schema_version == 2u) != (strcmp(out->model_id, BCA_THREE_CLASS_MODEL_ID) == 0)) return PRED_REJECT_SCHEMA;
    if (strcmp(activity, "walking") == 0) out->activity = PRED_ACTIVITY_WALKING;
    else if (strcmp(activity, "running") == 0) out->activity = PRED_ACTIVITY_RUNNING;
    else if (out->schema_version == 2u && strcmp(activity, "stationary") == 0) out->activity = PRED_ACTIVITY_STATIONARY;
    else return PRED_REJECT_ACTIVITY;
    return PRED_ACCEPTED;
}

static enum pred_result evaluate(const struct pred_state *state, const struct pred_identity *id,
                                 const struct pred_message *message)
{
    if (message->schema_version == 2u && strcmp(message->device_id,"rtspark-003000423433511539303938") != 0) return PRED_REJECT_DEVICE;
    if (strcmp(message->device_id, id->device_id) != 0) return PRED_REJECT_DEVICE;
    if (strcmp(message->boot_id, id->boot_id) != 0) return PRED_REJECT_BOOT;
    if (state->have_seq && message->prediction_seq <= state->last_seq) return PRED_REJECT_REPLAY;
    if (!id->any_published || message->sequence_end > id->latest_seq ||
        message->device_timestamp_ms > id->now_ms) return PRED_REJECT_FUTURE;
    if (id->now_ms - message->device_timestamp_ms > BCA_PRED_MAX_AGE_MS) return PRED_REJECT_STALE;
    return PRED_ACCEPTED;
}

enum pred_result pred_process(struct pred_state *state, const struct pred_identity *identity,
                              const char *json, size_t length)
{
    struct pred_message message;
    enum pred_result result = pred_parse(json, length, &message);
    if (result == PRED_ACCEPTED) result = evaluate(state, identity, &message);
    ++state->results[result];
    if (result != PRED_ACCEPTED) return result;
    state->have_seq = 1;
    state->last_seq = message.prediction_seq;
    state->have_accepted = 1;
    state->accepted_ms = identity->now_ms;
    state->activity = message.activity;
    state->validated = message.hardware_validated;
    return result;
}

enum pred_result pred_process_message(struct pred_state *state, const struct pred_identity *identity,
                                      const char *expected_topic, const char *topic, size_t topic_length,
                                      int retained, const char *json, size_t length)
{
    enum pred_result result = PRED_ACCEPTED;
    if (topic_length != strlen(expected_topic) || memcmp(topic, expected_topic, topic_length) != 0)
        result = PRED_REJECT_TOPIC;
    else if (retained)
        result = PRED_REJECT_RETAINED;
    if (result == PRED_ACCEPTED) return pred_process(state, identity, json, length);
    ++state->results[result];
    return result;
}

static int fresh(const struct pred_state *state, uint64_t now_ms)
{
    return state->have_accepted && now_ms - state->accepted_ms <= BCA_PRED_HOLD_MS;
}

const char *pred_display_text(const struct pred_state *state, uint64_t now_ms, int link_up)
{
    if (fresh(state, now_ms)) {
        if (!state->validated) return "UNVALIDATED";
        if (!link_up) return "UNKNOWN";
        if (state->activity == PRED_ACTIVITY_STATIONARY) return "STATIONARY";
        return state->activity == PRED_ACTIVITY_RUNNING ? "RUNNING" : "WALKING";
    }
    return link_up ? "WAITING" : "UNKNOWN";
}

int pred_display_is_real(const struct pred_state *state, uint64_t now_ms)
{
    return fresh(state, now_ms) && state->validated;
}
