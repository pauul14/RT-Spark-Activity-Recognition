#include "provision.h"
#include <rtdevice.h>
#include <finsh.h>
#include <shell.h>
#include <wlan_mgnt.h>
#include <stdlib.h>
#include "platform.h"
#include "tls_transport.h"
#include "wifi_link.h"
#include <rthw.h>

#define KEY_ABORT 0x03 /* Ctrl-C */
#define INPUT_ABORTED  (-1)
#define INPUT_TIMEOUT  (-2)
#define INPUT_TOO_LONG (-3)

/* FinSH owns the console, and it is idle while one of its commands runs. */
extern struct finsh_shell *shell;

/* The only MCU copy of Wi-Fi credentials before a join. Entered in place, so no
 * staging copy exists, and never valid while wifi_entry_active is set.
 */
static BCA_CCM struct prov_wifi wifi;
static volatile rt_bool_t wifi_entry_active;
static rt_tick_t wifi_stored_at;
static BCA_CCM struct prov_mqtt mqtt;
static BCA_CCM char ca_pem[BCA_CA_PEM_MAX + 1];
static rt_size_t ca_length;
static volatile rt_bool_t wifi_set;
static rt_bool_t mqtt_set, ca_set;
static struct rt_mutex lock;

void prov_wipe(void *memory, rt_size_t length)
{
    volatile rt_uint8_t *bytes = memory;
    while (length-- != 0) *bytes++ = 0;
}

rt_err_t prov_init(void) { return rt_mutex_init(&lock, "prov", RT_IPC_FLAG_PRIO); }

rt_bool_t prov_wifi_present(void) { return wifi_set; }
rt_bool_t prov_mqtt_present(void) { return mqtt_set; }
rt_bool_t prov_ca_present(void) { return ca_set; }

/* Only one join is admitted per boot, so the stored copy has no further use once taken. */
rt_bool_t prov_wifi_take(struct prov_wifi *out)
{
    rt_bool_t present;
    rt_mutex_take(&lock, RT_WAITING_FOREVER);
    present = wifi_set && !wifi_entry_active;
    if (present)
    {
        rt_memcpy(out, &wifi, sizeof(*out));
        wifi_set = RT_FALSE;
        prov_wipe(&wifi, sizeof(wifi));
    }
    rt_mutex_release(&lock);
    return present;
}

/* Called by the Wi-Fi supervisor while no lease is armed. Unused credentials do not
 * wait indefinitely in RAM for an arm that may never come.
 */
void prov_wifi_expire(void)
{
    rt_bool_t expired = RT_FALSE;
    if (!wifi_set || wifi_entry_active || rt_mutex_take(&lock, RT_WAITING_NO) != RT_EOK) return;
    if (wifi_set && (rt_tick_t)(rt_tick_get() - wifi_stored_at) >= rt_tick_from_millisecond(BCA_PROV_WIFI_TTL_MS))
    {
        wifi_set = RT_FALSE;
        prov_wipe(&wifi, sizeof(wifi));
        expired = RT_TRUE;
    }
    rt_mutex_release(&lock);
    if (expired) rt_kprintf("#PROV,wifi,expired=1,wiped=1\n");
}

rt_bool_t prov_mqtt_copy(struct prov_mqtt *out)
{
    rt_bool_t present;
    rt_mutex_take(&lock, RT_WAITING_FOREVER);
    present = mqtt_set;
    if (present) rt_memcpy(out, &mqtt, sizeof(*out));
    rt_mutex_release(&lock);
    return present;
}

const char *prov_ca_acquire(rt_size_t *length_with_nul)
{
    rt_mutex_take(&lock, RT_WAITING_FOREVER);
    if (ca_set)
    {
        *length_with_nul = ca_length + 1u;
        return ca_pem;
    }
    rt_mutex_release(&lock);
    return RT_NULL;
}

void prov_ca_release(void) { rt_mutex_release(&lock); }

/* finsh_getchar() waits forever, so an abandoned host left the prompt open and blocked
 * the stop path. This waits only until the deadline.
 */
static int read_key(rt_tick_t deadline)
{
    char key = 0;
    for (;;)
    {
        rt_int32_t left;
        if (rt_device_read(shell->device, -1, &key, 1) == 1) return (unsigned char)key;
        left = (rt_int32_t)(deadline - rt_tick_get());
        if (left <= 0) return INPUT_TIMEOUT;
        rt_sem_take(&shell->rx_sem, left);
    }
}

/* The UART driver leaves received bytes in its ring after they are read. Wipe every
 * slot not still waiting to be read, so typed secrets do not linger there.
 */
static void wipe_console_fifo(void)
{
    struct rt_serial_device *serial = (struct rt_serial_device *)shell->device;
    struct rt_serial_rx_fifo *fifo;
    rt_uint32_t size, pending, free, i;
    rt_base_t level;

    if (serial == RT_NULL || (serial->parent.open_flag & RT_DEVICE_FLAG_INT_RX) == 0 || serial->serial_rx == RT_NULL)
        return;
    fifo = serial->serial_rx;
    size = serial->config.bufsz;
    level = rt_hw_interrupt_disable();
    pending = fifo->is_full ? size : (fifo->put_index + size - fifo->get_index) % size;
    free = size - pending;
    for (i = 0; i < free; ++i)
        ((volatile rt_uint8_t *)fifo->buffer)[(fifo->put_index + i) % size] = 0;
    rt_hw_interrupt_enable(level);
}

/* Bytes arriving after a prompt has ended must not reach the shell, which would echo
 * them, keep them in history and run them. Discard until the line is quiet.
 */
static unsigned long drain_console(void)
{
    rt_tick_t end = rt_tick_get() + rt_tick_from_millisecond(BCA_PROV_DRAIN_MAX_MS);
    unsigned long discarded = 0;
    for (;;)
    {
        rt_tick_t quiet = rt_tick_get() + rt_tick_from_millisecond(BCA_PROV_DRAIN_QUIET_MS);
        if ((rt_int32_t)(quiet - end) > 0) quiet = end;
        if (read_key(quiet) < 0) break;
        ++discarded;
    }
    wipe_console_fifo();
    return discarded;
}

/* Returns the length, or a negative INPUT_ code. Nothing typed is ever echoed unless
 * echo is set, and the buffer is wiped on any failure.
 */
static int read_line(const char *prompt, char *out, rt_size_t capacity, rt_bool_t echo, rt_tick_t deadline)
{
    rt_size_t used = 0;
    rt_bool_t overflow = RT_FALSE;
    rt_kprintf("%s", prompt);
    for (;;)
    {
        rt_tick_t limit = rt_tick_get() + rt_tick_from_millisecond(BCA_PROV_KEY_TIMEOUT_MS);
        int key;
        if ((rt_int32_t)(limit - deadline) > 0) limit = deadline;
        key = read_key(limit);
        if (key < 0 || key == KEY_ABORT)
        {
            prov_wipe(out, capacity);
            rt_kprintf("\n");
            return key == INPUT_TIMEOUT ? INPUT_TIMEOUT : INPUT_ABORTED;
        }
        if (key == '\r' || (key == '\n' && used != 0)) break;
        if (key == '\n') continue; /* tail of CRLF, including the one ending the command */
        if (key == 0x7F || key == 0x08)
        {
            if (used != 0) { out[--used] = '\0'; if (echo) rt_kprintf("\b \b"); }
            continue;
        }
        if (key < 0x20 || key > 0x7E) continue;
        if (used + 1u >= capacity) { overflow = RT_TRUE; continue; }
        out[used++] = (char)key;
        if (echo) rt_kprintf("%c", key);
    }
    out[used] = '\0';
    rt_kprintf("\n");
    if (overflow)
    {
        prov_wipe(out, capacity);
        return INPUT_TOO_LONG;
    }
    return (int)used;
}

static const char *input_failure(int code)
{
    return code == INPUT_TIMEOUT ? "timeout" : code == INPUT_TOO_LONG ? "too_long" : "cancelled";
}

/* Every exit drains, so the host can treat the discarded_input line as the end of
 * the exchange. Neither field is echoed: the SSID is not needed in any log.
 */
static void prov_wifi_command(void)
{
    int ssid = INPUT_ABORTED, password = INPUT_ABORTED;
    const char *reason = RT_NULL;
    rt_tick_t deadline;
    rt_base_t level = rt_hw_interrupt_disable();

    if (!wifi_link_provision_allowed())
    {
        rt_hw_interrupt_enable(level);
        rt_kprintf("#PROV,rejected,wifi_not_initialized_or_lease_active_or_stopped\n");
        rt_kprintf("#PROV,wifi,discarded_input=%lu\n", drain_console());
        return;
    }
    wifi_entry_active = RT_TRUE;
    rt_hw_interrupt_enable(level);
    rt_mutex_take(&lock, RT_WAITING_FOREVER);
    wifi_set = RT_FALSE;
    prov_wipe(&wifi, sizeof(wifi));
    rt_mutex_release(&lock);

    deadline = rt_tick_get() + rt_tick_from_millisecond(BCA_PROV_ENTRY_MS);
    rt_kprintf("#PROV,wifi,echo=none,timeout_s=%u,ctrl_c_aborts\n", BCA_PROV_ENTRY_MS / 1000u);
    ssid = read_line("SSID: ", wifi.ssid, sizeof(wifi.ssid), RT_FALSE, deadline);
    if (ssid > 0) password = read_line("Password (hidden): ", wifi.password, sizeof(wifi.password), RT_FALSE, deadline);
    if (ssid == 0) reason = "ssid_required";
    else if (ssid < 0) reason = input_failure(ssid);
    else if (password < 0) reason = input_failure(password);
    else if (password != 0 && password < 8) reason = "password_8_to_31_or_empty";

    rt_mutex_take(&lock, RT_WAITING_FOREVER);
    if (reason == RT_NULL)
    {
        wifi_set = RT_TRUE;
        wifi_stored_at = rt_tick_get();
    }
    else prov_wipe(&wifi, sizeof(wifi));
    wifi_entry_active = RT_FALSE;
    rt_mutex_release(&lock);
    if (reason == RT_NULL)
        rt_kprintf("#PROV,wifi,stored=ram_only,ssid_len=%d,expires_s=%u\n", ssid, BCA_PROV_WIFI_TTL_MS / 1000u);
    else rt_kprintf("#PROV,wifi,not_stored,reason=%s\n", reason);
    rt_kprintf("#PROV,wifi,discarded_input=%lu\n", drain_console());
}
MSH_CMD_EXPORT_ALIAS(prov_wifi_command, prov_wifi, Enter Wi-Fi credentials into RAM);

/* The backend requires a DNS name for SNI and certificate verification. */
static rt_bool_t host_is_dns_name(const char *host)
{
    rt_bool_t dot = RT_FALSE, alpha = RT_FALSE;
    const char *c;
    if (host[0] == '\0' || host[0] == '.' || host[0] == '-') return RT_FALSE;
    for (c = host; *c; ++c)
    {
        if (*c == '.') { if (c[1] == '\0' || c[1] == '.') return RT_FALSE; dot = RT_TRUE; }
        else if ((*c >= 'a' && *c <= 'z') || (*c >= 'A' && *c <= 'Z')) alpha = RT_TRUE;
        else if (!((*c >= '0' && *c <= '9') || *c == '-')) return RT_FALSE;
    }
    return dot && alpha;
}

static void prov_mqtt_command(void)
{
    static BCA_CCM struct prov_mqtt entry;
    char port[8];
    long value = 8883;
    const char *reason = "cancelled";
    rt_tick_t deadline = rt_tick_get() + rt_tick_from_millisecond(BCA_PROV_ENTRY_MS);
    int length;

    rt_kprintf("#PROV,mqtt,password_is_not_echoed,tls_required,timeout_s=%u,ctrl_c_aborts\n", BCA_PROV_ENTRY_MS / 1000u);
    length = read_line("Broker DNS name: ", entry.host, sizeof(entry.host), RT_TRUE, deadline);
    if (length < 0) { reason = input_failure(length); goto cancelled; }
    if (!host_is_dns_name(entry.host)) { reason = "dns_hostname_required"; goto cancelled; }
    length = read_line("TLS port [8883]: ", port, sizeof(port), RT_TRUE, deadline);
    if (length < 0) { reason = input_failure(length); goto cancelled; }
    if (port[0] != '\0') value = atol(port);
    if (value < 1 || value > 65535) { reason = "port"; goto cancelled; }
    entry.port = (rt_uint16_t)value;
    length = read_line("Username: ", entry.username, sizeof(entry.username), RT_TRUE, deadline);
    if (length <= 0) { reason = length == 0 ? "username_required" : input_failure(length); goto cancelled; }
    length = read_line("Password (hidden): ", entry.password, sizeof(entry.password), RT_FALSE, deadline);
    if (length <= 0) { reason = length == 0 ? "password_required" : input_failure(length); goto cancelled; }
    length = read_line("NTP host [" BCA_SNTP_DEFAULT_HOST "]: ", entry.time_host, sizeof(entry.time_host), RT_TRUE, deadline);
    if (length < 0) { reason = input_failure(length); goto cancelled; }
    if (entry.time_host[0] == '\0') rt_strncpy(entry.time_host, BCA_SNTP_DEFAULT_HOST, sizeof(entry.time_host) - 1u);
    rt_mutex_take(&lock, RT_WAITING_FOREVER);
    rt_memcpy(&mqtt, &entry, sizeof(mqtt));
    mqtt_set = RT_TRUE;
    rt_mutex_release(&lock);
    prov_wipe(&entry, sizeof(entry));
    rt_kprintf("#PROV,mqtt,stored=ram_only\n");
    rt_kprintf("#PROV,mqtt,discarded_input=%lu\n", drain_console());
    return;
cancelled:
    prov_wipe(&entry, sizeof(entry));
    prov_wipe(port, sizeof(port));
    rt_kprintf("#PROV,mqtt,not_stored,reason=%s\n", reason);
    rt_kprintf("#PROV,mqtt,discarded_input=%lu\n", drain_console());
}
MSH_CMD_EXPORT_ALIAS(prov_mqtt_command, prov_mqtt, Enter broker settings into RAM);

/* One root certificate, ended by its END line. It is public, so it is echoed line by
 * line as a transcription check. It is parsed before acceptance and its SHA-256 is
 * printed to compare with the value recorded from the trusted source: that comparison
 * is the operator's trust decision.
 */
static void prov_ca_command(void)
{
    char line[80], fingerprint[65];
    char *entry = ca_pem;
    rt_size_t used = 0;
    rt_bool_t inside = RT_FALSE;
    rt_tick_t deadline = rt_tick_get() + rt_tick_from_millisecond(BCA_PROV_ENTRY_MS);

    /* Assembled in place: the trust anchor is absent, not partial, until checked. */
    rt_mutex_take(&lock, RT_WAITING_FOREVER);
    ca_set = RT_FALSE;
    rt_mutex_release(&lock);
    rt_kprintf("#PROV,ca,paste_one_root_pem,timeout_s=%u,ctrl_c_aborts\n", BCA_PROV_ENTRY_MS / 1000u);
    for (;;)
    {
        int length = read_line("", line, sizeof(line), RT_TRUE, deadline);
        if (length < 0)
        {
            prov_wipe(ca_pem, sizeof(ca_pem));
            rt_kprintf("#PROV,ca,not_stored,reason=%s\n", input_failure(length));
            rt_kprintf("#PROV,ca,discarded_input=%lu\n", drain_console());
            return;
        }
        if (length == 0) continue;
        if (rt_strcmp(line, "-----BEGIN CERTIFICATE-----") == 0) inside = RT_TRUE;
        if (!inside) continue;
        if (used + (rt_size_t)length + 2u > BCA_CA_PEM_MAX)
        {
            prov_wipe(ca_pem, sizeof(ca_pem));
            rt_kprintf("#PROV,rejected,ca_too_large,max=%u\n", BCA_CA_PEM_MAX);
            rt_kprintf("#PROV,ca,discarded_input=%lu\n", drain_console());
            return;
        }
        rt_memcpy(entry + used, line, (rt_size_t)length);
        used += (rt_size_t)length;
        entry[used++] = '\n';
        if (rt_strcmp(line, "-----END CERTIFICATE-----") == 0) break;
    }
    entry[used] = '\0';
    if (tls_ca_check(entry, used + 1u, fingerprint, sizeof(fingerprint)) != RT_EOK)
    {
        prov_wipe(ca_pem, sizeof(ca_pem));
        rt_kprintf("#PROV,rejected,ca_invalid\n");
        return;
    }
    rt_mutex_take(&lock, RT_WAITING_FOREVER);
    ca_length = used;
    ca_set = RT_TRUE;
    rt_mutex_release(&lock);
    rt_kprintf("#PROV,ca,stored=ram_only,sha256=%s\n", fingerprint);
}
MSH_CMD_EXPORT_ALIAS(prov_ca_command, prov_ca, Load the broker CA certificate into RAM);

static void prov_status(void)
{
    rt_kprintf("#PROV,wifi=%d,mqtt=%d,ca=%d,storage=ram_only\n", (int)wifi_set, (int)mqtt_set, (int)ca_set);
}
MSH_CMD_EXPORT(prov_status, Show which settings are present without revealing them);

rt_err_t prov_wifi_clear(void)
{
    if (rt_mutex_take(&lock,rt_tick_from_millisecond(100))!=RT_EOK) return -RT_ETIMEOUT;
    if (wifi_entry_active) { rt_mutex_release(&lock); return -RT_EBUSY; }
    wifi_set=RT_FALSE;
    prov_wipe(&wifi,sizeof(wifi));
    rt_mutex_release(&lock);
    return RT_EOK;
}

/* Wipes stored Wi-Fi credentials without the terminal radio stop of prov_clear, so a
 * host that abandons provisioning before arming can always clean up.
 */
static void prov_wifi_forget(void)
{
    rt_kprintf("#PROV,wifi,forget=%s\n", prov_wifi_clear() == RT_EOK ? "wiped" : "busy");
}
MSH_CMD_EXPORT(prov_wifi_forget, Wipe stored Wi-Fi credentials from RAM);

static void prov_clear(void)
{
    wifi_link_request_stop();
    rt_mutex_take(&lock, RT_WAITING_FOREVER);
    wifi_set = mqtt_set = ca_set = RT_FALSE;
    prov_wipe(&wifi, sizeof(wifi));
    prov_wipe(&mqtt, sizeof(mqtt));
    prov_wipe(ca_pem, sizeof(ca_pem));
    ca_length = 0;
    rt_mutex_release(&lock);
    rt_kprintf("#PROV,store_cleared,rf_stop_requested,driver_purge_check=wifi_diag_status\n");
}
MSH_CMD_EXPORT(prov_clear, Erase all provisioned settings from RAM);
