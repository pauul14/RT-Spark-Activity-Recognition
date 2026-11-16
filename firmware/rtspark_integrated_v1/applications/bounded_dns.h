#ifndef BCA_BOUNDED_DNS_H
#define BCA_BOUNDED_DNS_H
#include <rtthread.h>
struct sockaddr_in;
rt_err_t bca_dns_init(void);
rt_err_t bca_resolve_ipv4(const char *host,rt_uint16_t port,struct sockaddr_in *out,rt_uint32_t timeout_ms);
#endif
