/* MQTT session supervision: TLS only, one QoS 1 message in flight, bounded retries. */
#ifndef MQTT_LINK_H
#define MQTT_LINK_H
#include <rtthread.h>

rt_err_t mqtt_link_start(void);
#endif
