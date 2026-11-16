/* Verified TLS transport for the MQTT thread. There is no plaintext path in this
 * image: a missing CA, clock or hostname, or any TLS failure, ends the attempt.
 */
#ifndef TLS_TRANSPORT_H
#define TLS_TRANSPORT_H
#include <rtthread.h>
#include <stddef.h>

struct tls_transport_stats {
    rt_uint32_t connects, failures, verify_flags;
    rt_int32_t last_error;
    rt_size_t arena_total, arena_peak;
};

rt_err_t tls_transport_init(void);
/* One connection at a time, from the MQTT thread only. */
rt_err_t tls_transport_open(const char *host, rt_uint16_t port);
void tls_transport_close(void);
/* All bytes or <0. */
int tls_transport_write(const rt_uint8_t *data, size_t length);
/* >0 bytes, 0 timed out, <0 failed. */
int tls_transport_read(rt_uint8_t *data, size_t length, rt_uint32_t wait_ms);
void tls_transport_stats_read(struct tls_transport_stats *out);
/* Parses PEM without keeping it. The first certificate must be a CA. */
rt_err_t tls_ca_check(const char *pem, size_t length_with_nul, char *fingerprint, size_t capacity);
#endif
