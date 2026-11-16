/* Minimal bounded MQTT 3.1.1 client over the official MQTTPacket serializer.
 * Portable: the transport is injected, so the same code runs against a host broker.
 *
 * The official paho_mqtt_pipe.c is not used. It moves each publish through a
 * 512-byte pipe, which splits a five-sample message, never matches a PUBACK to its
 * packet, and has no QoS 1 retry. One message is in flight here at a time.
 */
#ifndef MQTT_CORE_H
#define MQTT_CORE_H
#include <stddef.h>
#include <stdint.h>

struct mqtt_io {
    void *context;
    int (*write)(void *context, const uint8_t *data, size_t length);    /* 0, or <0 */
    int (*read)(void *context, uint8_t *data, size_t length, uint32_t wait_ms); /* >0, 0 timeout, <0 */
    uint32_t (*now_ms)(void *context);
};

enum mqtt_status {
    MQTT_OK = 0,
    MQTT_ERR_IO = -1,
    MQTT_ERR_TIMEOUT = -2,
    MQTT_ERR_SERIALIZE = -3,
    MQTT_ERR_PROTOCOL = -4,
    MQTT_ERR_OVERSIZE = -5,
    MQTT_ERR_REFUSED = -6,
    MQTT_ERR_SUBSCRIBE = -7,
    MQTT_ERR_BUSY = -8
};

enum mqtt_poll_event { MQTT_EVENT_NONE, MQTT_EVENT_PUBACK, MQTT_EVENT_MESSAGE };

struct mqtt_incoming {
    const char *topic, *payload;
    size_t topic_length, payload_length;
    int retained, qos;
};

struct mqtt_core {
    struct mqtt_io io;
    uint8_t *tx, *rx;
    size_t tx_capacity, rx_capacity, rx_have;
    uint32_t keepalive_ms, packet_timeout_ms, last_tx_ms, rx_started_ms, ping_sent_ms, inflight_sent_ms;
    uint32_t oversize_rx, unexpected_ack;
    uint16_t next_id, inflight_id, incoming_id;
    uint8_t inflight, ping_pending, ack_pending;
    int connack_code;
};

void mqtt_core_init(struct mqtt_core *m, const struct mqtt_io *io, uint8_t *tx, size_t tx_capacity,
                    uint8_t *rx, size_t rx_capacity, uint32_t packet_timeout_ms);
/* Resets the session state, then sends CONNECT (clean session) and waits for CONNACK. */
int mqtt_core_connect(struct mqtt_core *m, const char *client_id, const char *username,
                      const char *password, uint16_t keepalive_s);
int mqtt_core_subscribe(struct mqtt_core *m, const char *topic, int qos);
/* QoS 1, never retained. duplicate=1 resends the in-flight packet identifier. */
int mqtt_core_publish(struct mqtt_core *m, const char *topic, const void *payload, size_t length, int duplicate);
/* Handles at most one packet and the keepalive. <0 means the session is unusable. */
int mqtt_core_poll(struct mqtt_core *m, uint32_t wait_ms, struct mqtt_incoming *incoming);
/* Complete an incoming QoS 1 message after the application has consumed it. */
int mqtt_core_acknowledge(struct mqtt_core *m);
int mqtt_core_inflight_expired(const struct mqtt_core *m, uint32_t timeout_ms);
void mqtt_core_disconnect(struct mqtt_core *m);
#endif
