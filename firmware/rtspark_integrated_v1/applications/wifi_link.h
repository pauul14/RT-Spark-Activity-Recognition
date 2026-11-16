/* RW007 bring-up, bounded scanning and supervised station connection. */
#ifndef WIFI_LINK_H
#define WIFI_LINK_H
#include <rtthread.h>

rt_err_t wifi_link_start(void);
rt_bool_t wifi_link_ready(void);
rt_bool_t wifi_link_stopping(void);
rt_bool_t wifi_link_provision_allowed(void);
void wifi_link_request_stop(void);
int bca_wifi_radio_stopped(void);
int bca_wifi_join_permit(void);
void wifi_port_rf_off_isr(void);
void wifi_port_prepare_stop(void);
rt_err_t bca_wlan_forget_key(void);
rt_err_t bca_wlan_forget_secret(void);
void rw007_stop_wake(void);
int rw007_tx_quiescent(void);

/* wifi_port.c: reset timing and routing from the pinned example, without its unbounded wait. */
rt_err_t wifi_port_start(void);
void wifi_port_stop(void);
rt_err_t wifi_port_irq_result(void);
#endif
