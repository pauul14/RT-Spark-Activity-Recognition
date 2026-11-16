#include "app_status.h"
#include <rthw.h>

volatile struct bca_status g_bca_status;

void bca_status_read(struct bca_status *out)
{
    rt_base_t level = rt_hw_interrupt_disable();
    rt_memcpy(out, (const void *)&g_bca_status, sizeof(*out));
    rt_hw_interrupt_enable(level);
    out->now_ms = bca_now_ms();
}

void bca_status_set_sensor(enum bca_sensor_state state) { g_bca_status.sensor = state; }

void bca_status_set_wifi(enum bca_wifi_state state, rt_int32_t error)
{
    rt_base_t level = rt_hw_interrupt_disable();
    g_bca_status.wifi = state;
    g_bca_status.wifi_error = error;
    rt_hw_interrupt_enable(level);
}

void bca_status_set_mqtt(enum bca_mqtt_state state, rt_int32_t error)
{
    rt_base_t level = rt_hw_interrupt_disable();
    g_bca_status.mqtt = state;
    g_bca_status.mqtt_error = error;
    rt_hw_interrupt_enable(level);
}

void bca_status_mark_started(rt_uint32_t subsystem)
{
    rt_base_t level = rt_hw_interrupt_disable();
    g_bca_status.started_mask |= subsystem;
    rt_hw_interrupt_enable(level);
}

void bca_fatal(rt_uint32_t code, const char *what)
{
    rt_base_t level = rt_hw_interrupt_disable();
    if (g_bca_status.fatal == 0) g_bca_status.fatal = code;
    rt_hw_interrupt_enable(level);
    rt_kprintf("#ERROR,fatal,code=%lu,what=%s\n", (unsigned long)code, what);
}

rt_uint64_t bca_now_ms(void)
{
    static rt_uint64_t extended;
    static rt_tick_t previous;
    rt_uint64_t now;
    rt_base_t level = rt_hw_interrupt_disable();
    rt_tick_t tick = rt_tick_get();
    extended += (rt_tick_t)(tick - previous);
    previous = tick;
    now = extended;
    rt_hw_interrupt_enable(level);
    return now;
}

const char *bca_sensor_text(enum bca_sensor_state state)
{
    static const char *const names[] = {"OFF", "INIT", "WARMUP", "OK 5Hz", "FAULT"};
    return names[state];
}

const char *bca_wifi_text(enum bca_wifi_state state)
{
    static const char *const names[] = {"OFF", "INIT", "NO CREDS", "JOINING", "DHCP", "CONNECTED",
                                        "RETRY", "DOWN", "FAILED", "STOPPING", "WAIT ARM"};
    return names[state];
}

const char *bca_mqtt_text(enum bca_mqtt_state state)
{
    static const char *const names[] = {"OFF", "NO CONFIG", "WAIT WIFI", "WAIT TIME", "CONNECTING",
                                        "ONLINE TLS", "RETRY", "BLOCKED"};
    return names[state];
}
