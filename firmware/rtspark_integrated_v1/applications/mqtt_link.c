/* Everything here may wait on the network, and that is safe: batches reach this thread
 * only through the non-blocking telemetry ring, so an outage costs old batches, which
 * are counted and appear to the cloud as sequence gaps, and never costs sensor timing.
 */
#include "mqtt_link.h"
#include <rthw.h>
#include <finsh.h>
#include "app_config.h"
#include "app_status.h"
#include "motion_acq.h"
#include "mqtt_session.h"
#include "net_backoff.h"
#include "platform.h"
#include "provision.h"
#include "time_sync.h"
#include "tls_transport.h"
#include "wifi_link.h"

static BCA_CCM rt_uint8_t tx_buffer[BCA_MQTT_TX_MAX], rx_buffer[BCA_MQTT_RX_MAX];
static BCA_CCM char payload[BCA_MOTION_JSON_MAX];
static BCA_CCM struct prov_mqtt settings;
static BCA_CCM struct pred_state prediction;
static BCA_CCM struct mqtt_session session;
static struct mqtt_core core;
static char motion_topic[96], prediction_topic[96];
static rt_bool_t started;
static struct rt_thread worker_thread;
ALIGN(8) static BCA_CCM rt_uint8_t worker_stack[BCA_STACK_MQTT];

static int io_write(void *context, const uint8_t *data, size_t length)
{
    (void)context;
    return tls_transport_write(data, length);
}

static int io_read(void *context, uint8_t *data, size_t length, uint32_t wait_ms)
{
    (void)context;
    return tls_transport_read(data, length, wait_ms);
}

static uint32_t io_now(void *context)
{
    (void)context;
    return (uint32_t)rt_tick_get();
}

static int ops_link_ready(void *context)
{
    (void)context;
    return wifi_link_ready();
}

static int ops_next_batch(void *context, struct motion_batch *out)
{
    (void)context;
    return telemetry_pop(&g_telemetry, out);
}

static void ops_batch_offered(void *context, uint64_t seq)
{
    rt_base_t level = rt_hw_interrupt_disable();
    (void)context;
    g_bca_status.latest_seq = seq;
    g_bca_status.any_published = RT_TRUE;
    rt_hw_interrupt_enable(level);
}

static void ops_online(void *context)
{
    (void)context;
    prov_wipe(settings.password, sizeof(settings.password));
    ++g_bca_status.mqtt_connects;
    bca_status_set_mqtt(MQTT_ONLINE, 0);
}

static void ops_published(void *context)
{
    (void)context;
    ++g_bca_status.mqtt_published;
}

static void ops_retried(void *context)
{
    (void)context;
    ++g_bca_status.mqtt_retries;
}

static void ops_message(void *context, const struct mqtt_incoming *message)
{
    struct pred_identity identity;
    rt_base_t level;
    (void)context;

    identity.device_id = g_device_id;
    identity.boot_id = g_boot_id;
    identity.now_ms = bca_now_ms();
    level = rt_hw_interrupt_disable();
    identity.latest_seq = g_bca_status.latest_seq;
    identity.any_published = g_bca_status.any_published;
    rt_hw_interrupt_enable(level);
    pred_process_message(&prediction, &identity, prediction_topic, message->topic, message->topic_length,
                         message->retained, message->payload, message->payload_length);
    level = rt_hw_interrupt_disable();
    rt_memcpy((void *)&g_bca_status.prediction, &prediction, sizeof(prediction));
    rt_hw_interrupt_enable(level);
}

static void worker(void *parameter)
{
    static const struct mqtt_io io = {RT_NULL, io_write, io_read, io_now};
    static const struct mqtt_session_ops ops = {RT_NULL, ops_link_ready, ops_next_batch, ops_batch_offered,
                                                ops_online, ops_published, ops_retried, ops_message};
    rt_uint32_t backoff_ms = 0;
    (void)parameter;

    mqtt_core_init(&core, &io, tx_buffer, sizeof(tx_buffer), rx_buffer, sizeof(rx_buffer), BCA_MQTT_PACKET_MS);
    session.core = &core;
    session.ops = ops;
    session.payload = payload;
    session.payload_capacity = sizeof(payload);
    session.device_id = g_device_id;
    session.boot_id = g_boot_id;
    session.motion_topic = motion_topic;
    session.prediction_topic = prediction_topic;
    session.ack_timeout_ms = BCA_MQTT_ACK_MS;
    session.max_tries = BCA_MQTT_PUBLISH_TRIES;
    session.poll_ms = BCA_MQTT_POLL_MS;
    session.keepalive_s = BCA_MQTT_KEEPALIVE_S;
    for (;;)
    {
        rt_err_t result;
        rt_tick_t session_start;
        int outcome = 0;
        rt_bool_t tls_attempted = RT_FALSE;

        if (!prov_mqtt_present() || !prov_ca_present())
        {
            bca_status_set_mqtt(MQTT_WAIT_PROV, 0);
            rt_thread_mdelay(1000);
            continue;
        }
        if (!wifi_link_ready())
        {
            bca_status_set_mqtt(MQTT_WAIT_WIFI, 0);
            rt_thread_mdelay(500);
            continue;
        }
        if (!prov_mqtt_copy(&settings)) continue;
        result = RT_EOK;
        if (time_sync_due())
        {
            bca_status_set_mqtt(MQTT_WAIT_TIME, 0);
            result = time_sync_run(settings.time_host);
            if (time_sync_valid()) result = RT_EOK; /* an aging clock remains usable for now */
        }
        session_start = rt_tick_get();
        if (result == RT_EOK)
        {
            bca_status_set_mqtt(MQTT_CONNECTING, 0);
            tls_attempted = RT_TRUE;
            result = tls_transport_open(settings.host, settings.port);
        }
        if (result == RT_EOK)
        {
            outcome = mqtt_session_run(&session, settings.username, settings.password);
            if (outcome != MQTT_ERR_IO) mqtt_core_disconnect(&core);
            tls_transport_close();
        }
        prov_wipe(&settings, sizeof(settings));
        if ((rt_tick_t)(rt_tick_get() - session_start) >= rt_tick_from_millisecond(BCA_MQTT_BACKOFF_MAX_MS))
            backoff_ms = 0; /* a long-lived session earned a fresh start */
        backoff_ms = bca_backoff_next(backoff_ms, BCA_MQTT_BACKOFF_MIN_MS, BCA_MQTT_BACKOFF_MAX_MS);
        {
            struct tls_transport_stats tls;
            tls_transport_stats_read(&tls);
            bca_status_set_mqtt(tls_attempted && result != RT_EOK && tls.verify_flags != 0 ? MQTT_BLOCKED : MQTT_BACKOFF,
                                outcome != 0 ? outcome : !tls_attempted || tls.last_error == 0 ? result : tls.last_error);
        }
        rt_kprintf("#MQTT,disconnected,session=%d,result=%d,retry_ms=%lu\n", outcome, (int)result, (unsigned long)backoff_ms);
        rt_thread_mdelay(backoff_ms);
    }
}

rt_err_t mqtt_link_start(void)
{
    rt_err_t result;
    if (started) return RT_EOK;
    rt_snprintf(motion_topic, sizeof(motion_topic), "bca182/v1/devices/%s/motion", g_device_id);
    rt_snprintf(prediction_topic, sizeof(prediction_topic), "bca182/v1/devices/%s/prediction", g_device_id);
    bca_status_set_mqtt(MQTT_WAIT_PROV, 0);
    result = rt_thread_init(&worker_thread, "mqttlink", worker, RT_NULL, worker_stack, sizeof(worker_stack),
                            BCA_PRIO_MQTT, 10);
    if (result == RT_EOK) result = rt_thread_startup(&worker_thread);
    if (result == RT_EOK) started = RT_TRUE;
    return result;
}

static void mqtt_status(void)
{
    struct bca_status status;
    struct tls_transport_stats tls;
    struct telemetry_stats ring;
    rt_uint32_t depth;

    bca_status_read(&status);
    tls_transport_stats_read(&tls);
    telemetry_stats_read(&g_telemetry, &ring, &depth);
    rt_kprintf("#MQTT,state=%s,error=%ld,connects=%lu,published=%lu,retries=%lu\n",
               bca_mqtt_text(status.mqtt), (long)status.mqtt_error, (unsigned long)status.mqtt_connects,
               (unsigned long)status.mqtt_published, (unsigned long)status.mqtt_retries);
    rt_kprintf("#QUEUE,depth=%lu,peak=%lu,capacity=%u,dropped_oldest=%lu,partial_discarded=%lu,encode_failures=%lu\n",
               (unsigned long)depth, (unsigned long)ring.peak_depth, BCA_BATCH_RING,
               (unsigned long)ring.dropped_oldest, (unsigned long)ring.partial_discarded, (unsigned long)session.encode_failures);
    rt_kprintf("#TLS,connects=%lu,failures=%lu,verify_flags=0x%lx,last_error=-0x%lx,arena_peak=%lu,arena_total=%lu,time_valid=%d\n",
               (unsigned long)tls.connects, (unsigned long)tls.failures, (unsigned long)tls.verify_flags,
               (unsigned long)(tls.last_error < 0 ? -tls.last_error : tls.last_error),
               (unsigned long)tls.arena_peak, (unsigned long)tls.arena_total, (int)time_sync_valid());
    rt_kprintf("#PREDICTION,accepted=%lu,format=%lu,schema=%lu,activity=%lu,device=%lu,boot=%lu,replay=%lu,future=%lu,stale=%lu,retained=%lu,foreign=%lu\n",
               (unsigned long)status.prediction.results[PRED_ACCEPTED],
               (unsigned long)status.prediction.results[PRED_REJECT_FORMAT],
               (unsigned long)status.prediction.results[PRED_REJECT_SCHEMA],
               (unsigned long)status.prediction.results[PRED_REJECT_ACTIVITY],
               (unsigned long)status.prediction.results[PRED_REJECT_DEVICE],
               (unsigned long)status.prediction.results[PRED_REJECT_BOOT],
               (unsigned long)status.prediction.results[PRED_REJECT_REPLAY],
               (unsigned long)status.prediction.results[PRED_REJECT_FUTURE],
               (unsigned long)status.prediction.results[PRED_REJECT_STALE],
               (unsigned long)status.prediction.results[PRED_REJECT_RETAINED],
               (unsigned long)status.prediction.results[PRED_REJECT_TOPIC]);
}
MSH_CMD_EXPORT(mqtt_status, Show MQTT TLS queue and prediction counters);
