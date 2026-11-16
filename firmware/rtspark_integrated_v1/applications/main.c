/* BCA182 Lab 3 integrated firmware. Build-only until physically reviewed.
 *
 * Holds at boot with only the display running. `bca_start` brings up each stage
 * explicitly, so one reviewed image serves the whole bring-up sequence and any
 * stage can be tested without the others: `bca_start wifi` alone is the standalone
 * Wi-Fi diagnostic, with no sensor, MQTT or TLS activity.
 */
#include <rtthread.h>
#include <rtdevice.h>
#include <finsh.h>
#include "app_config.h"
#include "app_status.h"
#include "console_log.h"
#include "display_ui.h"
#include "fault_guard.h"
#include "motion_acq.h"
#include "mqtt_link.h"
#include "platform.h"
#include "provision.h"
#include "tls_transport.h"
#include "wifi_link.h"

static rt_err_t start_subsystems(rt_uint32_t mask)
{
    rt_err_t result = RT_EOK;
    rt_uint32_t running = g_bca_status.started_mask;

    if (g_bca_status.fatal != 0) return -RT_ERROR;

    if ((mask & BCA_SUBSYS_DISPLAY) != 0 && (running & BCA_SUBSYS_DISPLAY) == 0)
    {
        result = display_ui_start();
        if (result == RT_EOK) bca_status_mark_started(BCA_SUBSYS_DISPLAY);
    }
    if (result == RT_EOK && (mask & BCA_SUBSYS_SENSOR) != 0 && (running & BCA_SUBSYS_SENSOR) == 0)
    {
        result = motion_start();
        if (result == RT_EOK) bca_status_mark_started(BCA_SUBSYS_SENSOR);
    }
    if (result == RT_EOK && (mask & BCA_SUBSYS_WIFI) != 0 && (running & BCA_SUBSYS_WIFI) == 0)
    {
        result = wifi_link_start();
        if (result == RT_EOK) bca_status_mark_started(BCA_SUBSYS_WIFI);
    }
    if (result == RT_EOK && (mask & BCA_SUBSYS_MQTT) != 0 && (running & BCA_SUBSYS_MQTT) == 0)
    {
        result = mqtt_link_start();
        if (result == RT_EOK) bca_status_mark_started(BCA_SUBSYS_MQTT);
    }
    return result;
}

static void bca_start(int argc, char **argv)
{
    static const struct { const char *name; rt_uint32_t mask; } stages[] = {
        {"display", BCA_SUBSYS_DISPLAY},
        {"sensor", BCA_SUBSYS_SENSOR},
        {"wifi", BCA_SUBSYS_WIFI},
        {"mqtt", BCA_SUBSYS_WIFI | BCA_SUBSYS_MQTT},
        {"all", BCA_SUBSYS_DISPLAY | BCA_SUBSYS_SENSOR | BCA_SUBSYS_WIFI | BCA_SUBSYS_MQTT},
    };
    rt_size_t i;

    for (i = 0; argc == 2 && i < sizeof(stages) / sizeof(stages[0]); ++i)
    {
        if (rt_strcmp(argv[1], stages[i].name) == 0)
        {
            rt_err_t result = start_subsystems(stages[i].mask);
            rt_kprintf("#START,stage=%s,result=%d\n", stages[i].name, (int)result);
            return;
        }
    }
    rt_kprintf("usage: bca_start display|sensor|wifi|mqtt|all\n");
}
MSH_CMD_EXPORT(bca_start, Start one firmware stage);

static void bca_status(void)
{
    struct bca_status status;
    struct motion_counters motion;
    rt_size_t total, used, peak;

    bca_status_read(&status);
    motion_counters_read(&motion);
    rt_memory_info(&total, &used, &peak);
    rt_kprintf("#SESSION,device_id=%s,boot_id=%s,firmware=%s\n", g_device_id, g_boot_id, BCA_FW_VERSION);
    rt_kprintf("#STATE,sensor=%s,wifi=%s,mqtt=%s,fatal=%lu,activity=%s\n", bca_sensor_text(status.sensor),
               bca_wifi_text(status.wifi), bca_mqtt_text(status.mqtt), (unsigned long)status.fatal,
               pred_display_text(&status.prediction, status.now_ms, status.mqtt == MQTT_ONLINE));
    rt_kprintf("#STAT,raw=%lu,filtered=%lu,drop_raw=%lu,drop_filtered=%lu,irregular=%lu,miss_est=%lu,read_overruns=%lu,max_read_ms=%lu,rail_axes=%lu,filter_resets=%lu,acq_fatal=%lu\n",
               (unsigned long)motion.raw, (unsigned long)motion.filtered, (unsigned long)motion.dropped_raw,
               (unsigned long)motion.dropped_filtered, (unsigned long)motion.irregular,
               (unsigned long)motion.estimated_missed, (unsigned long)motion.read_overruns,
               (unsigned long)motion.max_read_ms, (unsigned long)motion.rail, (unsigned long)motion.filter_resets,
               (unsigned long)motion.fatal);
    rt_kprintf("#HEAP,total=%lu,used=%lu,peak=%lu\n", (unsigned long)total, (unsigned long)used, (unsigned long)peak);
    bca_fault_status();
    rt_kprintf("#MSP,size=%lu,free=%lu\n#LOG,dropped=%lu\n", (unsigned long)bca_msp_size_bytes(),
               (unsigned long)bca_msp_free_bytes(), (unsigned long)console_log_dropped());
}
MSH_CMD_EXPORT(bca_status, Show sensor network heap and error state);

int main(void)
{
    rt_err_t result;

    bca_fault_boot(); /* before anything can clear or overwrite the reset evidence */
    bca_msp_paint();
    if (console_log_init() != RT_EOK) { bca_fatal(4, "console_lock"); return -1; }

    rt_kprintf("\n#BOOT,bca182_integrated,firmware=%s,console=uart1,baud=115200,format=8N1\n", BCA_FW_VERSION);
    bca_fault_report();
    rt_kprintf("#POLICY,credentials=ram_only,mqtt_transport=tls_only,plaintext=absent,hardware_validated=false\n");
    motion_prepare();
    if (prov_init() != RT_EOK) { bca_fatal(1, "provisioning_lock"); return -1; }
    if (tls_transport_init() != RT_EOK) { bca_fatal(2, "tls_arena"); return -1; }
    if (!platform_identity_init())
    {
        bca_fatal(3, "hardware_rng_session_id_failed");
        return -1;
    }
    rt_kprintf("#SESSION,device_id=%s,boot_id=%s,firmware=%s\n", g_device_id, g_boot_id, BCA_FW_VERSION);
    result = start_subsystems(BCA_AUTOSTART_MASK);
    if (result != RT_EOK) rt_kprintf("#ERROR,autostart_failed,result=%d\n", (int)result);
    rt_kprintf("#HOLD,use=bca_start,stages=display|sensor|wifi|mqtt|all\n");
    return 0;
}
