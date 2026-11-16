/* The online half of an MQTT session: batches out, predictions in, bounded retries.
 * Portable, with the platform injected, so tests drive this exact loop.
 */
#ifndef MQTT_SESSION_H
#define MQTT_SESSION_H
#include "mqtt_core.h"
#include "telemetry.h"

struct mqtt_session_ops {
    void *context;
    int (*link_ready)(void *context);
    int (*next_batch)(void *context, struct motion_batch *out);
    /* Before each batch's first send: predictions may now refer to this seq. */
    void (*batch_offered)(void *context, uint64_t seq);
    void (*online)(void *context);
    void (*published)(void *context);
    void (*retried)(void *context);
    void (*message)(void *context, const struct mqtt_incoming *message);
};

struct mqtt_session {
    struct mqtt_core *core;
    struct mqtt_session_ops ops;
    char *payload;
    size_t payload_capacity, payload_length;
    const char *device_id, *boot_id, *motion_topic, *prediction_topic;
    struct motion_batch current;
    uint32_t ack_timeout_ms, max_tries, poll_ms, tries, encode_failures;
    uint16_t keepalive_s;
    int pending; /* an unacknowledged batch survives reconnection */
};

/* Connects, subscribes, then serves until the session fails; returns the mqtt_status. */
int mqtt_session_run(struct mqtt_session *s, const char *username, const char *password);
#endif
