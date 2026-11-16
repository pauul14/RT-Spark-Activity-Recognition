/* The certificate and protocol policy, in one place. Portable, so the host tests
 * prove rejection behaviour with exactly the code the firmware runs.
 */
#ifndef TLS_POLICY_H
#define TLS_POLICY_H
#include <stdint.h>
#include "mbedtls/ssl.h"
#include "mbedtls/x509_crt.h"

/* TLS 1.2 only, certificate required, SHA-256+ chains, RSA >= 2048 bits. */
int tls_policy_configure(mbedtls_ssl_config *config, mbedtls_x509_crt *trusted_ca,
                         int (*rng)(void *, unsigned char *, size_t), void *rng_context, uint32_t read_timeout_ms);
/* Refuses an absent host: mbedTLS would otherwise silently skip the name check. */
int tls_policy_bind(mbedtls_ssl_context *ssl, const mbedtls_ssl_config *config, const char *host);
/* 1 only after a completed handshake whose chain, name and dates all verified. */
int tls_policy_verified(const mbedtls_ssl_context *ssl, uint32_t *flags);
#endif
