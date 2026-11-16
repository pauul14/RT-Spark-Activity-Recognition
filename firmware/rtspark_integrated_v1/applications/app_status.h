/* Status shared with the display and console. Each field has one writer thread;
 * readers take a consistent copy, so no reader can ever hold up a writer.
 */
#ifndef APP_STATUS_H
#define APP_STATUS_H
#include <rtthread.h>
#include "prediction.h"

enum bca_sensor_state { SENSOR_OFF, SENSOR_INIT, SENSOR_WARMUP, SENSOR_RUNNING, SENSOR_FAULT };
enum bca_wifi_state { WIFI_OFF, WIFI_INIT, WIFI_NO_CREDS, WIFI_CONNECTING, WIFI_DHCP, WIFI_READY,
                      WIFI_BACKOFF, WIFI_DOWN, WIFI_FAILED, WIFI_STOPPING, WIFI_WAIT_ARM };
enum bca_mqtt_state { MQTT_OFF, MQTT_WAIT_PROV, MQTT_WAIT_WIFI, MQTT_WAIT_TIME, MQTT_CONNECTING,
                      MQTT_ONLINE, MQTT_BACKOFF, MQTT_BLOCKED };

struct bca_status {
    enum bca_sensor_state sensor;
    enum bca_wifi_state wifi;
    enum bca_mqtt_state mqtt;
    rt_int32_t wifi_error, mqtt_error, rssi;
    rt_uint32_t started_mask, fatal;
    rt_uint32_t mqtt_published, mqtt_retries, mqtt_connects;
    rt_uint64_t latest_seq;
    rt_uint64_t now_ms;
    rt_bool_t any_published;
    struct pred_state prediction;
};

extern volatile struct bca_status g_bca_status;

void bca_status_read(struct bca_status *out);
void bca_status_set_sensor(enum bca_sensor_state state);
void bca_status_set_wifi(enum bca_wifi_state state, rt_int32_t error);
void bca_status_set_mqtt(enum bca_mqtt_state state, rt_int32_t error);
void bca_status_mark_started(rt_uint32_t subsystem);
/* Records and prints an unrecoverable fault; the display shows it until reset. */
void bca_fatal(rt_uint32_t code, const char *what);
/* Monotonic milliseconds that survive tick wrap; any thread may call it. */
rt_uint64_t bca_now_ms(void);

const char *bca_sensor_text(enum bca_sensor_state state);
const char *bca_wifi_text(enum bca_wifi_state state);
const char *bca_mqtt_text(enum bca_mqtt_state state);
#endif
