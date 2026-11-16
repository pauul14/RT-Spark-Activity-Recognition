#include "mqtt_core.h"
#include <string.h>
#include "MQTTPacket.h"

static int send_tx(struct mqtt_core *m, int length)
{
    if (length <= 0) return MQTT_ERR_SERIALIZE;
    if (m->io.write(m->io.context, m->tx, (size_t)length) != 0) return MQTT_ERR_IO;
    m->last_tx_ms = m->io.now_ms(m->io.context);
    return MQTT_OK;
}

void mqtt_core_init(struct mqtt_core *m, const struct mqtt_io *io, uint8_t *tx, size_t tx_capacity,
                    uint8_t *rx, size_t rx_capacity, uint32_t packet_timeout_ms)
{
    memset(m, 0, sizeof(*m));
    m->io = *io;
    m->tx = tx;
    m->rx = rx;
    m->tx_capacity = tx_capacity;
    m->rx_capacity = rx_capacity;
    m->packet_timeout_ms = packet_timeout_ms;
}

/* Returns the total packet length once known, 0 while the header is incomplete, <0 if bad. */
static long packet_length(const struct mqtt_core *m)
{
    uint32_t remaining = 0, multiplier = 1;
    size_t i;
    for (i = 1; i < m->rx_have; ++i) {
        remaining += (uint32_t)(m->rx[i] & 0x7Fu) * multiplier;
        if ((m->rx[i] & 0x80u) == 0) return (long)(i + 1u + remaining);
        if (i == 4) return -1;
        multiplier *= 128u;
    }
    return 0;
}

/* The legacy packet helpers trust packet-specific variable-header lengths.
 * Validate those before calling them, especially the QoS1 packet id that follows
 * a PUBLISH topic: a topic ending at rx_capacity must not cause a two-byte read
 * beyond the receive buffer. Only packet types supported by this client pass.
 */
static int packet_valid(const struct mqtt_core *m, size_t length)
{
    size_t body = 1, remaining, topic;
    unsigned type = m->rx[0] >> 4, flags = m->rx[0] & 15u;
    while (body < length && (m->rx[body++] & 128u) != 0) { }
    remaining = length - body;
    switch (type) {
    case CONNACK:
        return flags == 0 && remaining == 2 && m->rx[body] == 0 && m->rx[body + 1] <= 5;
    case PUBACK:
        return flags == 0 && remaining == 2 && (m->rx[body] || m->rx[body + 1]);
    case SUBACK:
        return flags == 0 && remaining == 3 && (m->rx[body] || m->rx[body + 1]) &&
               (m->rx[body + 2] <= 2 || m->rx[body + 2] == 128);
    case PINGRESP:
        return flags == 0 && remaining == 0;
    case PUBLISH:
        if (((flags >> 1) & 3u) > 1u || remaining < 2) return 0;
        topic = (size_t)m->rx[body] * 256u + m->rx[body + 1];
        if (topic == 0 || topic > remaining - 2) return 0;
        body += 2 + topic;
        if ((flags & 6u) != 0 && (length - body < 2 || !(m->rx[body] || m->rx[body + 1]))) return 0;
        return 1;
    default:
        return 0;
    }
}

/* Accumulates one packet across calls, so a timeout never loses a partial read. */
static int read_packet(struct mqtt_core *m, uint32_t wait_ms)
{
    for (;;) {
        long total = packet_length(m);
        size_t want;
        int count;
        if (total < 0) return MQTT_ERR_PROTOCOL;
        if (total > (long)m->rx_capacity) { ++m->oversize_rx; return MQTT_ERR_OVERSIZE; }
        if (total != 0 && m->rx_have == (size_t)total)
            return packet_valid(m, (size_t)total) ? (int)total : MQTT_ERR_PROTOCOL;
        want = total != 0 ? (size_t)total - m->rx_have : 1u;
        count = m->io.read(m->io.context, m->rx + m->rx_have, want, wait_ms);
        if (count < 0) return MQTT_ERR_IO;
        if (count == 0) {
            if (m->rx_have != 0 && m->io.now_ms(m->io.context) - m->rx_started_ms >= m->packet_timeout_ms)
                return MQTT_ERR_TIMEOUT;
            return 0;
        }
        if (m->rx_have == 0) m->rx_started_ms = m->io.now_ms(m->io.context);
        m->rx_have += (size_t)count;
        wait_ms = 50u; /* the rest of a started packet follows immediately */
    }
}

static int wait_packet(struct mqtt_core *m, uint32_t timeout_ms)
{
    uint32_t started = m->io.now_ms(m->io.context);
    for (;;) {
        int length = read_packet(m, 100u);
        if (length != 0) return length;
        if (m->io.now_ms(m->io.context) - started >= timeout_ms) return MQTT_ERR_TIMEOUT;
    }
}

int mqtt_core_connect(struct mqtt_core *m, const char *client_id, const char *username,
                      const char *password, uint16_t keepalive_s)
{
    MQTTPacket_connectData options = MQTTPacket_connectData_initializer;
    unsigned char session_present = 0, code = 0xFFu;
    int length, status;

    m->rx_have = 0;
    m->inflight = m->ping_pending = m->ack_pending = 0;
    m->connack_code = -1;
    m->keepalive_ms = (uint32_t)keepalive_s * 1000u;
    options.MQTTVersion = 4;
    options.clientID.cstring = (char *)client_id;
    options.username.cstring = (char *)username;
    options.password.cstring = (char *)password;
    options.keepAliveInterval = keepalive_s;
    options.cleansession = 1;
    status = send_tx(m, MQTTSerialize_connect(m->tx, (int)m->tx_capacity, &options));
    /* write() consumes bytes synchronously. Do not retain the serialized password
     * while waiting for CONNACK, including a failed/partial write. */
    {
        volatile unsigned char *secret = m->tx;
        size_t remaining = m->tx_capacity;
        while (remaining--) *secret++ = 0;
    }
    if (status != MQTT_OK) return status;
    length = wait_packet(m, m->packet_timeout_ms);
    if (length < 0) return length;
    if ((m->rx[0] >> 4) != CONNACK ||
        MQTTDeserialize_connack(&session_present, &code, m->rx, length) != 1) return MQTT_ERR_PROTOCOL;
    m->rx_have = 0;
    m->connack_code = code;
    return code == 0 ? MQTT_OK : MQTT_ERR_REFUSED;
}

static uint16_t next_packet_id(struct mqtt_core *m)
{
    if (++m->next_id == 0) m->next_id = 1;
    return m->next_id;
}

int mqtt_core_subscribe(struct mqtt_core *m, const char *topic, int qos)
{
    MQTTString filter = MQTTString_initializer;
    int requested = qos, granted = 0x80, count = 0, length, status;
    uint16_t id = next_packet_id(m);
    unsigned short acknowledged = 0;

    filter.cstring = (char *)topic;
    status = send_tx(m, MQTTSerialize_subscribe(m->tx, (int)m->tx_capacity, 0, id, 1, &filter, &requested));
    if (status != MQTT_OK) return status;
    length = wait_packet(m, m->packet_timeout_ms);
    if (length < 0) return length;
    if ((m->rx[0] >> 4) != SUBACK ||
        MQTTDeserialize_suback(&acknowledged, 1, &count, &granted, m->rx, length) != 1 ||
        acknowledged != id || count != 1) return MQTT_ERR_PROTOCOL;
    m->rx_have = 0;
    /* The serializer reads this through plain char, whose sign differs by target. */
    return (granted & 0xFF) == 0x80 ? MQTT_ERR_SUBSCRIBE : MQTT_OK;
}

int mqtt_core_publish(struct mqtt_core *m, const char *topic, const void *payload, size_t length, int duplicate)
{
    MQTTString name = MQTTString_initializer;
    int status;

    if (duplicate ? !m->inflight : m->inflight) return MQTT_ERR_BUSY;
    if (!duplicate) m->inflight_id = next_packet_id(m);
    name.cstring = (char *)topic;
    status = send_tx(m, MQTTSerialize_publish(m->tx, (int)m->tx_capacity, (unsigned char)(duplicate != 0), 1, 0,
                                              m->inflight_id, name, (unsigned char *)payload, (int)length));
    if (status != MQTT_OK) return status;
    m->inflight = 1;
    m->inflight_sent_ms = m->last_tx_ms;
    return MQTT_OK;
}

static int dispatch(struct mqtt_core *m, int length, struct mqtt_incoming *incoming)
{
    unsigned char type = 0, duplicate = 0, retained = 0;
    unsigned short id = 0;

    switch (m->rx[0] >> 4) {
    case PUBACK:
        if (MQTTDeserialize_ack(&type, &duplicate, &id, m->rx, length) != 1) return MQTT_ERR_PROTOCOL;
        if (m->inflight && id == m->inflight_id) {
            m->inflight = 0;
            return MQTT_EVENT_PUBACK;
        }
        ++m->unexpected_ack; /* a late ack for an already retried packet */
        return MQTT_EVENT_NONE;
    case PINGRESP:
        m->ping_pending = 0;
        return MQTT_EVENT_NONE;
    case PUBLISH: {
        MQTTString topic = MQTTString_initializer;
        unsigned char *payload = NULL;
        int qos = 0, payload_length = 0;
        if (MQTTDeserialize_publish(&duplicate, &qos, &retained, &id, &topic, &payload, &payload_length,
                                    m->rx, length) != 1 || qos > 1) return MQTT_ERR_PROTOCOL;
        incoming->topic = topic.lenstring.data;
        incoming->topic_length = (size_t)topic.lenstring.len;
        incoming->payload = (const char *)payload;
        incoming->payload_length = (size_t)payload_length;
        incoming->retained = retained;
        incoming->qos = qos;
        m->ack_pending = qos == 1;
        m->incoming_id = id;
        return MQTT_EVENT_MESSAGE;
    }
    default:
        return MQTT_ERR_PROTOCOL;
    }
}

int mqtt_core_poll(struct mqtt_core *m, uint32_t wait_ms, struct mqtt_incoming *incoming)
{
    uint32_t now;
    int length = read_packet(m, wait_ms), event = MQTT_EVENT_NONE;

    if (length < 0) return length;
    if (length > 0) {
        event = dispatch(m, length, incoming);
        m->rx_have = 0; /* incoming points into rx, valid until the next poll */
        if (event < 0) return event;
    }
    now = m->io.now_ms(m->io.context);
    if (m->ping_pending && now - m->ping_sent_ms >= m->packet_timeout_ms) return MQTT_ERR_TIMEOUT;
    if (!m->ping_pending && now - m->last_tx_ms >= m->keepalive_ms / 2u) {
        int status = send_tx(m, MQTTSerialize_pingreq(m->tx, (int)m->tx_capacity));
        if (status != MQTT_OK) return status;
        m->ping_pending = 1;
        m->ping_sent_ms = now;
    }
    return event;
}

int mqtt_core_acknowledge(struct mqtt_core *m)
{
    if (!m->ack_pending) return MQTT_OK;
    m->ack_pending = 0;
    return send_tx(m, MQTTSerialize_puback(m->tx, (int)m->tx_capacity, m->incoming_id));
}

int mqtt_core_inflight_expired(const struct mqtt_core *m, uint32_t timeout_ms)
{
    return m->inflight && m->io.now_ms(m->io.context) - m->inflight_sent_ms >= timeout_ms;
}

void mqtt_core_disconnect(struct mqtt_core *m)
{
    (void)send_tx(m, MQTTSerialize_disconnect(m->tx, (int)m->tx_capacity));
}
