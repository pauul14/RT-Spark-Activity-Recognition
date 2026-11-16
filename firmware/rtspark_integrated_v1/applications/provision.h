/* RAM-only credentials and trust anchors, entered at the local UART console.
 *
 * Nothing here is compiled in or written to flash, so a power cycle forgets all of it.
 * Secrets are read directly from the console: never echoed, and never in FinSH's
 * command history, which is why they cannot be arguments to a shell command.
 */
#ifndef PROVISION_H
#define PROVISION_H
#include <rtthread.h>
#include "app_config.h"

struct prov_wifi {
    char ssid[BCA_WIFI_SSID_MAX + 1], password[BCA_WIFI_PASS_MAX + 1];
};

struct prov_mqtt {
    char host[BCA_MQTT_HOST_MAX + 1], username[BCA_MQTT_USER_MAX + 1];
    char password[BCA_MQTT_PASS_MAX + 1], time_host[BCA_MQTT_HOST_MAX + 1];
    rt_uint16_t port;
};

rt_err_t prov_init(void);
rt_err_t prov_wifi_clear(void);
rt_bool_t prov_wifi_present(void);
rt_bool_t prov_mqtt_present(void);
rt_bool_t prov_ca_present(void);
/* Copies are the caller's to prov_wipe() as soon as they have been used. The Wi-Fi
 * take also wipes the stored copy: one join per boot, so it has no second use.
 */
rt_bool_t prov_wifi_take(struct prov_wifi *out);
void prov_wifi_expire(void);
rt_bool_t prov_mqtt_copy(struct prov_mqtt *out);
/* NUL-terminated PEM. Valid until prov_ca_release(); prov_ca blocks while it is held. */
const char *prov_ca_acquire(rt_size_t *length_with_nul);
void prov_ca_release(void);
void prov_wipe(void *memory, rt_size_t length);
#endif
