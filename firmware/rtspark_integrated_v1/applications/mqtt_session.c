#include "mqtt_session.h"
#include "motion_json.h"

#define QOS_AT_LEAST_ONCE 1

static void take_next_batch(struct mqtt_session *s)
{
    if (s->pending || !s->ops.next_batch(s->ops.context, &s->current)) return;
    s->payload_length = motion_json_encode(s->payload, s->payload_capacity, &s->current, s->device_id, s->boot_id);
    if (s->payload_length == 0)
    {
        ++s->encode_failures; /* cannot occur with valid ids; counted rather than truncated */
        return;
    }
    s->pending = 1;
    s->tries = 0;
    s->ops.batch_offered(s->ops.context, s->current.seq);
}

int mqtt_session_run(struct mqtt_session *s, const char *username, const char *password)
{
    struct mqtt_incoming incoming;
    int status = mqtt_core_connect(s->core, s->device_id, username, password, s->keepalive_s);

    if (status != MQTT_OK) return status;
    status = mqtt_core_subscribe(s->core, s->prediction_topic, QOS_AT_LEAST_ONCE);
    if (status != MQTT_OK) return status;
    s->tries = 0; /* a batch left unacknowledged restarts its budget on a new session */
    s->ops.online(s->ops.context);
    for (;;)
    {
        if (!s->ops.link_ready(s->ops.context)) return MQTT_ERR_IO;
        take_next_batch(s);
        if (s->pending && !s->core->inflight)
        {
            status = mqtt_core_publish(s->core, s->motion_topic, s->payload, s->payload_length, 0);
            if (status != MQTT_OK) return status;
            ++s->tries;
        }
        else if (mqtt_core_inflight_expired(s->core, s->ack_timeout_ms))
        {
            if (s->tries >= s->max_tries) return MQTT_ERR_TIMEOUT;
            /* Identical bytes under the same packet id; the backend drops duplicates. */
            status = mqtt_core_publish(s->core, s->motion_topic, s->payload, s->payload_length, 1);
            if (status != MQTT_OK) return status;
            ++s->tries;
            s->ops.retried(s->ops.context);
        }
        status = mqtt_core_poll(s->core, s->poll_ms, &incoming);
        if (status < 0) return status;
        if (status == MQTT_EVENT_PUBACK)
        {
            s->pending = 0;
            s->ops.published(s->ops.context);
        }
        else if (status == MQTT_EVENT_MESSAGE)
        {
            s->ops.message(s->ops.context, &incoming);
            status = mqtt_core_acknowledge(s->core);
            if (status != MQTT_OK) return status;
        }
    }
}
