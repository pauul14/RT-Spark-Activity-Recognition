/* Diagnostic start-up and scan logic come from Codex's checkpoint 3G-F main.c. The
 * supervisor owns reconnection, so WLAN auto-reconnect stays off and every attempt
 * is visible and paced. Network names are never printed or kept; a scan reports
 * scalars only. This thread waits here for as long as the network needs: acquisition
 * runs above it at priority 6 and shares neither locks nor buffers with it.
 */
#include "wifi_link.h"
#include <rtdevice.h>
#include <rthw.h>
#include <finsh.h>
#include <wlan_mgnt.h>
#include <spi_wifi_rw007.h>
#include "app_config.h"
#include "app_status.h"
#include "wifi_guard.h"
#include <stdlib.h>
#include <lwip/tcpip.h>
#include <lwip/dhcp.h>
#include <netif/ethernetif.h>
#include "platform.h"
#include "provision.h"

#define EVENT_SCAN_REQUEST 0x01u
#define EVENT_LINK_CHANGE  0x02u

struct ap_summary { rt_int16_t rssi, channel; rt_uint32_t security, rate; };

static struct ap_summary reports[BCA_WIFI_AP_CAPACITY];
static volatile rt_uint32_t report_count, report_overflow;
static volatile rt_bool_t accepting, scan_busy, started;
static struct wifi_guard guard; /* starts inhibited; terminal stop cannot rearm */
static struct rt_timer lease_timer;
static volatile rt_bool_t cleanup_done;
static volatile rt_bool_t join_inflight;
static volatile rt_bool_t ip_quiet;
static rt_bool_t ip_stop_queued;
static BCA_CCM struct prov_wifi join_credentials;

rt_bool_t wifi_link_stopping(void) { return guard.stopped != 0; }
rt_bool_t wifi_link_provision_allowed(void)
{
    return started && !guard.armed && !guard.stopped && !scan_busy &&
        (g_bca_status.wifi==WIFI_NO_CREDS || g_bca_status.wifi==WIFI_WAIT_ARM);
}
int bca_wifi_radio_stopped(void) { return guard.stopped != 0; }
int bca_wifi_join_permit(void)
{
    rt_base_t level=rt_hw_interrupt_disable();
    int permit=wifi_guard_join(&guard,(rt_uint32_t)rt_tick_get());
    rt_hw_interrupt_enable(level);
    return permit;
}
static void cut_radio(void)
{
    wifi_port_rf_off_isr();
    if (!cleanup_done) bca_status_set_wifi(WIFI_STOPPING,0);
    rw007_stop_wake(); /* event send is ISR-safe; transfer owner drains/wipes */
}
static void active_state(enum bca_wifi_state state)
{
    rt_base_t level=rt_hw_interrupt_disable();
    if (!guard.stopped) bca_status_set_wifi(state,0);
    rt_hw_interrupt_enable(level);
}
static void lease_expired(void *unused)
{
    rt_base_t level=rt_hw_interrupt_disable();
    (void)unused;
    wifi_guard_tick(&guard,(rt_uint32_t)rt_tick_get());
    if (guard.stopped) cut_radio();
    rt_hw_interrupt_enable(level);
}
static void stop_reason(unsigned reason)
{
    rt_base_t level=rt_hw_interrupt_disable();
    wifi_guard_stop(&guard,reason);
    cut_radio();
    rt_hw_interrupt_enable(level);
}
void wifi_link_request_stop(void)
{
    wifi_port_prepare_stop(); /* thread context; timer thereafter uses only a GPIO write */
    stop_reason(WG_OPERATOR);
    /* Immediate RF inhibit; complete cleanup is reported separately. */
}

static struct rt_semaphore scan_done;
static struct rt_event events;
static struct rt_thread worker_thread;
ALIGN(8) static rt_uint8_t worker_stack[BCA_STACK_WIFI_LINK];

/* Never print names, identifiers or response strings from a scan callback.
 * Only bounded scalar summaries cross into our persistent application data.
 */
static void scan_report(int event, struct rt_wlan_buff *buffer, void *parameter)
{
    const struct rt_wlan_info *info;
    rt_base_t level;
    (void)parameter;
    if (event != RT_WLAN_EVT_SCAN_REPORT || !buffer || !buffer->data ||
        buffer->len != sizeof(struct rt_wlan_info)) return;
    info = (const struct rt_wlan_info *)buffer->data;
    level = rt_hw_interrupt_disable();
    if (accepting && report_count < BCA_WIFI_AP_CAPACITY)
    {
        struct ap_summary *out = &reports[report_count++];
        out->rssi = info->rssi; out->channel = info->channel;
        out->security = info->security; out->rate = info->datarate;
    }
    else if (accepting) ++report_overflow;
    rt_hw_interrupt_enable(level);
}

static void scan_finished(int event, struct rt_wlan_buff *buffer, void *parameter)
{
    (void)buffer; (void)parameter;
    if (event == RT_WLAN_EVT_SCAN_DONE && accepting) rt_sem_release(&scan_done);
}

static void link_changed(int event, struct rt_wlan_buff *buffer, void *parameter)
{
    (void)event; (void)buffer; (void)parameter;
    rt_event_send(&events, EVENT_LINK_CHANGE);
}

static rt_bool_t printable_version(const char *text, rt_size_t capacity)
{
    rt_size_t i;
    if (!text[0]) return RT_FALSE;
    for (i=0; i<capacity; ++i)
    {
        if (!text[i]) return RT_TRUE;
        if ((unsigned char)text[i] < 32u || (unsigned char)text[i] > 126u) return RT_FALSE;
    }
    return RT_FALSE;
}

static void run_scan(void)
{
    rt_err_t result;
    rt_uint32_t i, count, overflow;
    rt_base_t level;
    for (i=0; i<2u; ++i)
        if (rt_sem_take(&scan_done, RT_WAITING_NO) != RT_EOK) break;
    level = rt_hw_interrupt_disable();
    report_count = 0; report_overflow = 0; accepting = RT_TRUE;
    rt_hw_interrupt_enable(level);
    rt_kprintf("#SCAN,begin,timeout_ms=%u,names_omitted=true\n", BCA_WIFI_SCAN_MS);
    result = rt_wlan_scan();
    if (result == RT_EOK)
        result = rt_sem_take(&scan_done, rt_tick_from_millisecond(BCA_WIFI_SCAN_MS));
    level = rt_hw_interrupt_disable();
    accepting = RT_FALSE; count = report_count; overflow = report_overflow;
    rt_hw_interrupt_enable(level);
    scan_busy = RT_FALSE;
    if (result != RT_EOK)
    {
        rt_kprintf("#SCAN,failed,result=%d\n", (int)result);
        return;
    }
    for (i=0; i<count; ++i)
        rt_kprintf("#AP,index=%lu,rssi=%d,channel=%d,security=%lu,rate_bps=%lu\n",
                   (unsigned long)i, (int)reports[i].rssi, (int)reports[i].channel,
                   (unsigned long)reports[i].security, (unsigned long)reports[i].rate);
    rt_kprintf("#SCAN,done,reported=%lu,omitted_over_capacity=%lu\n",
               (unsigned long)count, (unsigned long)overflow);
}

/* Printed before any #ERROR line, so a capture that stops there still has the evidence. */
static void report_driver_evidence(void)
{
    struct rw007_diagnostics diag;
    rt_uint32_t kept, i;

    rw007_diag_read(&diag);
    rt_kprintf("#RW007_DIAG,ok=%lu,lost=%lu,retries=%lu,stage1_errors=%lu,stage2_errors=%lu,unchanged=%lu,zero=%lu,ones=%lu,echo=%lu,other=%lu\n",
               (unsigned long)diag.transfers_ok, (unsigned long)diag.transfers_lost, (unsigned long)diag.retries,
               (unsigned long)diag.stage1_errors, (unsigned long)diag.stage2_errors, (unsigned long)diag.stage1_unchanged,
               (unsigned long)diag.stage1_zero, (unsigned long)diag.stage1_ones, (unsigned long)diag.stage1_echo,
               (unsigned long)diag.stage1_other);
    rt_kprintf("#RW007_DIAG,last_get_cmd=%ld,result=%ld,len=%lu,capacity=%lu,get_rejected=%lu,packets_rejected=%lu,responses_rejected=%lu,events_rejected=%lu,scan_short=%lu\n",
               (long)diag.last_get_cmd, (long)diag.last_get_result, (unsigned long)diag.last_get_len,
               (unsigned long)diag.last_get_capacity, (unsigned long)diag.get_rejected, (unsigned long)diag.packets_rejected,
               (unsigned long)diag.responses_rejected, (unsigned long)diag.events_rejected, (unsigned long)diag.scan_reports_short);
    kept = diag.failures_kept < RW007_DIAG_KEPT ? diag.failures_kept : RW007_DIAG_KEPT;
    for (i = 0; i < kept; ++i)
    {
        char hex[2u * sizeof(diag.first_failures[0]) + 1u];
        rt_size_t j;
        for (j = 0; j < sizeof(diag.first_failures[0]); ++j)
            rt_snprintf(&hex[2u * j], 3, "%02x", diag.first_failures[i][j]);
        rt_kprintf("#RW007_STAGE1,index=%lu,bytes=%s\n", (unsigned long)i, hex);
    }
}

/* Fail-stop by design: the driver's objects and threads cannot be initialized twice,
 * so a partial RW007 start is reported and left down until the next reset.
 */
static rt_bool_t bring_up(void)
{
    char version[RW007_ID_TEXT_MAX + 1] = {0}; /* always terminated, even at capacity */
    rt_uint8_t mac[6] = {0};
    const char *stage = "port";
    rt_err_t result = wifi_port_start();

    rt_kprintf("#RW007,host_driver_result=%d,irq_result=%d\n", (int)result, (int)wifi_port_irq_result());
    if (result == RT_EOK)
    {
        stage = "station_mode";
        result = rt_wlan_set_mode(RT_WLAN_DEVICE_STA_NAME, RT_WLAN_STATION);
    }
    if (result == RT_EOK)
    {
        stage = "module_version";
        result = rw007_version_get(version);
        if (result == RT_EOK && !printable_version(version, sizeof(version))) result = -RT_ERROR;
    }
    if (result == RT_EOK)
    {
        rt_kprintf("#RW007,module_version=%s,protocol_responded=true\n", version);
        stage = "mac";
        result = rt_wlan_get_mac(mac);
        if (result == RT_EOK && ((mac[0]&1u) ||
            ((mac[0]|mac[1]|mac[2]|mac[3]|mac[4]|mac[5]) == 0u))) result = -RT_ERROR;
    }
    if (result == RT_EOK)
    {
        rt_kprintf("#WLAN,interface=%s,mac=%02x:%02x:%02x:%02x:%02x:%02x,mode=STA\n",
                   RT_WLAN_DEVICE_STA_NAME, mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
        stage = "callbacks";
        rt_wlan_config_autoreconnect(RT_FALSE);
        result = rt_wlan_register_event_handler(RT_WLAN_EVT_SCAN_REPORT, scan_report, RT_NULL);
        if (result == RT_EOK) result = rt_wlan_register_event_handler(RT_WLAN_EVT_SCAN_DONE, scan_finished, RT_NULL);
        if (result == RT_EOK) result = rt_wlan_register_event_handler(RT_WLAN_EVT_READY, link_changed, RT_NULL);
        if (result == RT_EOK) result = rt_wlan_register_event_handler(RT_WLAN_EVT_STA_CONNECTED, link_changed, RT_NULL);
        if (result == RT_EOK) result = rt_wlan_register_event_handler(RT_WLAN_EVT_STA_DISCONNECTED, link_changed, RT_NULL);
        if (result == RT_EOK) result = rt_wlan_register_event_handler(RT_WLAN_EVT_STA_CONNECTED_FAIL, link_changed, RT_NULL);
    }
    if (result == RT_EOK) return RT_TRUE;
    wifi_port_stop();
    bca_status_set_wifi(WIFI_FAILED, result);
    report_driver_evidence();
    rt_kprintf("#RW007,failed,stage=%s,result=%d\n", stage, (int)result);
    rt_kprintf("#ERROR,stage=%s,result=%d,no_automatic_retry\n", stage, (int)result);
    return RT_FALSE;
}


/* Only called by the supervisor after its blocking call returned. No thread is
 * deleted and no buffer still used by that call is wiped concurrently.
 */
static void stop_ip(void *parameter)
{
    struct netif *interface=parameter;
    dhcp_stop(interface);
    netif_set_link_down(interface);
    netif_set_down(interface);
    netif_set_addr(interface,IP4_ADDR_ANY4,IP4_ADDR_ANY4,IP4_ADDR_ANY4);
    ip_quiet=RT_TRUE;
}

static rt_bool_t finish_stop(void)
{
    if (cleanup_done) return RT_TRUE;
    wifi_port_stop();
    if (join_inflight) return RT_FALSE;
    prov_wipe(&join_credentials,sizeof(join_credentials));
    rw007_stop_wake();
    if (join_inflight || !rw007_tx_quiescent()) return RT_FALSE;
    if (bca_wlan_forget_key()!=RT_EOK || prov_wifi_clear()!=RT_EOK) return RT_FALSE;
    if (!ip_stop_queued)
    {
        struct eth_device *eth=(struct eth_device *)rt_device_find("w0");
        if (eth==RT_NULL) ip_quiet=RT_TRUE; /* protocol was never attached */
        else
        {
            if (eth->parent.user_data!=(void *)rt_device_find(RT_WLAN_DEVICE_STA_NAME)) return RT_FALSE;
            if (tcpip_callback_with_block(stop_ip,eth->netif,0)!=ERR_OK) return RT_FALSE;
        }
        ip_stop_queued=RT_TRUE;
    }
    if (!ip_quiet) return RT_FALSE;
    cleanup_done=RT_TRUE;
    rt_timer_stop(&lease_timer);
    bca_status_set_wifi(WIFI_DOWN,0);
    rt_kprintf("#WIFI_STOP,complete=1,rf_reset=1,inflight=0,tx_quiet=1,dhcp_stopped=1,wifi_credentials_cleared=1,rearm=forbidden\n");
    return RT_TRUE;
}

static void worker(void *parameter)
{
    rt_bool_t attempted=RT_FALSE;
    rt_tick_t dhcp_deadline=0;
    rt_tick_t cleanup_deadline=0;
    (void)parameter;
    if (!bring_up()) { stop_reason(WG_FAILURE); }
    else if (!guard.stopped)
    {
        active_state(WIFI_NO_CREDS);
        scan_busy=RT_TRUE;
        run_scan(); /* original single automatic bounded scan */
        report_driver_evidence();
    }
    for (;;)
    {
        rt_uint32_t received=0;
        rt_event_recv(&events,EVENT_SCAN_REQUEST|EVENT_LINK_CHANGE,
            RT_EVENT_FLAG_OR|RT_EVENT_FLAG_CLEAR,rt_tick_from_millisecond(200),&received);
        if (guard.stopped)
        {
            if (finish_stop()) return;
            if (!cleanup_deadline) cleanup_deadline=rt_tick_get()+rt_tick_from_millisecond(30000);
            if ((rt_int32_t)(rt_tick_get()-cleanup_deadline)>=0)
            {
                rt_timer_stop(&lease_timer); /* reset remains asserted, rearm impossible */
                bca_status_set_wifi(WIFI_FAILED,-RT_ETIMEOUT);
                rt_kprintf("#WIFI_STOP,complete=0,cleanup_timeout=1,rf_reset=1,credentials_may_remain=1\n");
                return;
            }
            continue; /* status remains pending; never falsely acknowledge */
        }
        if ((received&EVENT_SCAN_REQUEST)!=0)
        {
            if (guard.armed) { scan_busy=RT_FALSE; rt_kprintf("#SCAN,rejected,diagnostic_active\n"); }
            else run_scan();
        }
        if (!guard.armed)
        {
            prov_wifi_expire();
            active_state(prov_wifi_present()?WIFI_WAIT_ARM:WIFI_NO_CREDS);
            continue;
        }
        if (!attempted)
        {
            rt_err_t result;
            attempted=RT_TRUE;
            if (!prov_wifi_take(&join_credentials)) { stop_reason(WG_FAILURE); continue; }
            /* Arm never reuses a generation: it can succeed only once per boot. */
            if (guard.stopped) { prov_wipe(&join_credentials,sizeof(join_credentials)); continue; }
            join_inflight=RT_TRUE;
            active_state(WIFI_CONNECTING);
            result=rt_wlan_connect(join_credentials.ssid,join_credentials.password);
            prov_wipe(&join_credentials,sizeof(join_credentials));
            /* The manager's key copy only served this join; reconnects are forbidden. */
            if (bca_wlan_forget_secret()!=RT_EOK) rt_kprintf("#WLAN,key_purge_deferred_to_stop\n");
            join_inflight=RT_FALSE;
            if (guard.stopped) continue; /* ignore late success after stop/expiry */
            if (result!=RT_EOK)
            {
                rt_kprintf("#WLAN,connect_failed,result=%d,retry=forbidden\n",(int)result);
                stop_reason(WG_FAILURE); continue;
            }
            dhcp_deadline=rt_tick_get()+rt_tick_from_millisecond(BCA_WIFI_DHCP_MS);
            active_state(WIFI_DHCP);
        }
        if (rt_wlan_is_ready())
        {
            rt_base_t level=rt_hw_interrupt_disable();
            int allowed=wifi_guard_ready(&guard,(rt_uint32_t)rt_tick_get());
            if (allowed) bca_status_set_wifi(WIFI_READY,0);
            else cut_radio();
            rt_hw_interrupt_enable(level);
        }
        else if (guard.ready) stop_reason(WG_LINK_LOST);
        else if ((rt_int32_t)(rt_tick_get()-dhcp_deadline)>=0) stop_reason(WG_JOIN_TIMEOUT);
    }
}

rt_bool_t wifi_link_ready(void)
{
    return started && guard.armed && !guard.stopped && guard.ready && rt_wlan_is_ready();
}

rt_err_t wifi_link_start(void)
{
    rt_err_t result;
    if (started || guard.stopped) return -RT_ERROR;
    wifi_port_prepare_stop(); /* cutoff is valid even if bring-up fails before SPI setup */
    bca_status_set_wifi(WIFI_INIT,0);
    result=rt_sem_init(&scan_done,"wscan",0,RT_IPC_FLAG_FIFO);
    if (result==RT_EOK) result=rt_event_init(&events,"wlink",RT_IPC_FLAG_FIFO);
    if (result==RT_EOK)
    {
        rt_timer_init(&lease_timer,"wlease",lease_expired,RT_NULL,
            rt_tick_from_millisecond(100),RT_TIMER_FLAG_PERIODIC); /* HARD timer */
        result=rt_timer_start(&lease_timer);
    }
    if (result==RT_EOK) result=rt_thread_init(&worker_thread,"wifilink",worker,RT_NULL,
        worker_stack,sizeof(worker_stack),BCA_PRIO_WIFI_LINK,10);
    if (result==RT_EOK) result=rt_thread_startup(&worker_thread);
    if (result==RT_EOK) started=RT_TRUE;
    else { stop_reason(WG_FAILURE); bca_status_set_wifi(WIFI_FAILED,result); }
    return result;
}

static void wifi_diag_arm(int argc,char **argv)
{
    char *end=RT_NULL;
    unsigned long seconds=argc==2 ? strtoul(argv[1],&end,10) : 0;
    rt_base_t level;
    int armed;
    if (!started || !end || *end || seconds<30 || seconds>600 ||
        !prov_wifi_present() || scan_busy || (g_bca_status.wifi!=WIFI_NO_CREDS && g_bca_status.wifi!=WIFI_WAIT_ARM))
    { rt_kprintf("#WIFI_DIAG,rejected,use=wifi_diag_arm_30_to_600_seconds,initialize_and_provision_first\n"); return; }
    level=rt_hw_interrupt_disable();
    armed=wifi_guard_arm(&guard,(rt_uint32_t)rt_tick_get(),(rt_uint32_t)seconds*1000u);
    rt_hw_interrupt_enable(level);
    if (armed) rt_event_send(&events,EVENT_LINK_CHANGE);
    rt_kprintf("#WIFI_DIAG,armed=%d,low_level_join_budget=1,lease_s=%lu,join_limit_s=60,stop=terminal\n",armed,seconds);
}
MSH_CMD_EXPORT(wifi_diag_arm, Arm one bounded diagnostic connection lease);

static void wifi_diag_status(void)
{
    struct wifi_guard snapshot;
    rt_base_t level=rt_hw_interrupt_disable();
    snapshot=guard;
    rt_hw_interrupt_enable(level);
    rt_kprintf("#WIFI_DIAG,armed=%u,attempts=%lu,stopped=%u,reason=%u,inflight=%d,cleanup_complete=%d,rf_reset=%u\n",
        snapshot.armed,(unsigned long)snapshot.attempts,snapshot.stopped,snapshot.reason,
        (int)join_inflight,(int)cleanup_done,snapshot.stopped);
}
MSH_CMD_EXPORT(wifi_diag_status, Show lease cutoff and truthful cleanup status);

static void wifi_link_down(void)
{
    wifi_link_request_stop();
    rt_kprintf("#WIFI_STOP,requested=1,rf_reset=1,complete=0,check=wifi_diag_status\n");
}
MSH_CMD_EXPORT(wifi_link_down, Terminal radio stop and asynchronous credential purge);
static void wifi_link_up(void) { rt_kprintf("#WIFI_DIAG,rejected,unbounded_up_disabled,use=wifi_diag_arm\n"); }
MSH_CMD_EXPORT(wifi_link_up, Unbounded reconnect is disabled);

static void wifi_probe_status(void)
{
    rt_size_t total, used, peak;
    struct bca_status status;
    rt_memory_info(&total, &used, &peak);
    bca_status_read(&status);
    rt_kprintf("#STATUS,wifi=%s,error=%ld,rssi=%ld,ready=%d,heap_total=%lu,heap_used=%lu,heap_peak=%lu\n",
               bca_wifi_text(status.wifi), (long)status.wifi_error, (long)status.rssi,
               (int)wifi_link_ready(), (unsigned long)total, (unsigned long)used, (unsigned long)peak);
    report_driver_evidence();
}
MSH_CMD_EXPORT(wifi_probe_status, Show Wi-Fi state and heap use);


static void wifi_probe_scan(void)
{
    rt_base_t level;
    if (guard.stopped || !started || g_bca_status.wifi == WIFI_FAILED || g_bca_status.wifi == WIFI_INIT)
    {
        rt_kprintf("#SCAN,rejected,not_ready_or_failed\n");
        return;
    }
    level = rt_hw_interrupt_disable();
    if (scan_busy)
    {
        rt_hw_interrupt_enable(level);
        rt_kprintf("#SCAN,rejected,busy\n");
        return;
    }
    scan_busy = RT_TRUE;
    rt_hw_interrupt_enable(level);
    rt_event_send(&events, EVENT_SCAN_REQUEST);
}
MSH_CMD_EXPORT(wifi_probe_scan, Request one bounded scan with names omitted);

