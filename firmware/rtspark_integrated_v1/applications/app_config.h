/* All integrated-firmware limits in one reviewed place. No RTOS includes: the
 * portable modules and their host tests share these values with the firmware.
 */
#ifndef APP_CONFIG_H
#define APP_CONFIG_H

#define BCA_FW_VERSION          "int3-net3-r1"
#define BCA_BOOT_ID_PREFIX      "int1-"

/* Reliability (r1). A HardFault or failed RT_ASSERT records a compact fault record in
 * reset-surviving RAM and resets into HOLD; set the debug option only on a bench.
 */
#define BCA_FAULT_STACK_BYTES   512u
#define BCA_FAULT_HALT_FOR_DEBUG 0
#define BCA_LOG_LOCK_TIMEOUT_MS 200u

/* Subsystems. Bring-up holds everything but the display until `bca_start`, so
 * one image can exercise each stage independently of the others.
 */
#define BCA_SUBSYS_DISPLAY      0x01u
#define BCA_SUBSYS_SENSOR       0x02u
#define BCA_SUBSYS_WIFI         0x04u
#define BCA_SUBSYS_MQTT         0x08u
#define BCA_AUTOSTART_MASK      BCA_SUBSYS_DISPLAY

/* Priorities (lower is more urgent). 8/9 RW007, 10 tcpip/main, 12 eth and 15 WLAN
 * work are the official values; the sensor stays above all of them.
 */
#define BCA_PRIO_MOTION         6
#define BCA_PRIO_WIFI_LINK      17
#define BCA_PRIO_MQTT           18
#define BCA_PRIO_TRACE          21
#define BCA_PRIO_DISPLAY        24

#define BCA_STACK_MOTION        4096u
#define BCA_STACK_TRACE         2048u
#define BCA_STACK_WIFI_LINK     4096u
#define BCA_STACK_MQTT          8192u
#define BCA_STACK_DISPLAY       2048u

/* Bounded buffers. */
#define BCA_TRACE_RECORDS       64u
#define BCA_BATCH_SAMPLES       5u
#define BCA_BATCH_RING          32u     /* newest ~32 s retained while offline */
#define BCA_MOTION_JSON_MAX     1536u
#define BCA_MQTT_TX_MAX         1664u
#define BCA_MQTT_RX_MAX         768u
#define BCA_PRED_JSON_MAX       512u
#define BCA_TLS_ARENA_BYTES     40960u
#define BCA_CA_PEM_MAX          3072u

/* Sensor stream as agreed with the backend. */
#define BCA_SAMPLE_INTERVAL_MIN_MS  100u
#define BCA_SAMPLE_INTERVAL_MAX_MS  300u

/* Wi-Fi supervision. */
#define BCA_WIFI_READY_MS       3000u
#define BCA_WIFI_SCAN_MS        10000u
#define BCA_WIFI_AP_CAPACITY    32u
#define BCA_WIFI_DHCP_MS        20000u
#define BCA_WIFI_BACKOFF_MIN_MS 2000u
#define BCA_WIFI_BACKOFF_MAX_MS 60000u
#define BCA_WIFI_SSID_MAX       32u
#define BCA_WIFI_PASS_MAX       31u     /* RW007 command buffer needs the NUL */

/* MQTT session. */
#define BCA_MQTT_KEEPALIVE_S    30u
#define BCA_MQTT_ACK_MS         10000u
#define BCA_MQTT_PUBLISH_TRIES  3u
#define BCA_MQTT_PING_ACK_MS    10000u
#define BCA_MQTT_PACKET_MS      10000u
#define BCA_MQTT_POLL_MS        100u
#define BCA_MQTT_BACKOFF_MIN_MS 2000u
#define BCA_MQTT_BACKOFF_MAX_MS 60000u
#define BCA_MQTT_HOST_MAX       128u
#define BCA_MQTT_USER_MAX       64u
#define BCA_MQTT_PASS_MAX       64u

/* Console provisioning. Abandoned input times out, late input is drained, and
 * Wi-Fi credentials never wait in RAM longer than the TTL for an arm.
 */
#define BCA_PROV_KEY_TIMEOUT_MS  30000u
#define BCA_PROV_ENTRY_MS        120000u
#define BCA_PROV_DRAIN_QUIET_MS  300u
#define BCA_PROV_DRAIN_MAX_MS    3000u
#define BCA_PROV_WIFI_TTL_MS     300000u

/* TLS and time. Certificates are never checked against an unsynchronized clock. */
#define BCA_TLS_CONNECT_MS      10000u
#define BCA_TLS_HANDSHAKE_MS    30000u
#define BCA_TIME_MIN_EPOCH      1793404800LL    /* 2026-10-31T00:00:00Z, this build */
#define BCA_TIME_MAX_EPOCH      2739830400LL    /* 2056-10-27: reject absurd replies */
#define BCA_TIME_MAX_AGE_MS     86400000u
#define BCA_TIME_RESYNC_MS      21600000u
#define BCA_TIME_AGREE_S        30
#define BCA_SNTP_TIMEOUT_MS     3000u
#define BCA_SNTP_DEFAULT_HOST   "pool.ntp.org"

/* Predictions: judged only against values this device generated. */
#define BCA_PRED_MAX_AGE_MS     15000u  /* FIR delay + window + cloud latency */
#define BCA_PRED_HOLD_MS        20000u  /* afterwards the screen stops claiming it */
#define BCA_DISPLAY_PERIOD_MS   500u

#endif
