#include "tls_policy.h"

int tls_policy_configure(mbedtls_ssl_config *config, mbedtls_x509_crt *trusted_ca,
                         int (*rng)(void *, unsigned char *, size_t), void *rng_context, uint32_t read_timeout_ms)
{
    int error = mbedtls_ssl_config_defaults(config, MBEDTLS_SSL_IS_CLIENT, MBEDTLS_SSL_TRANSPORT_STREAM,
                                            MBEDTLS_SSL_PRESET_DEFAULT);
    if (error != 0) return error;
    mbedtls_ssl_conf_min_tls_version(config, MBEDTLS_SSL_VERSION_TLS1_2);
    mbedtls_ssl_conf_authmode(config, MBEDTLS_SSL_VERIFY_REQUIRED);
    mbedtls_ssl_conf_cert_profile(config, &mbedtls_x509_crt_profile_next);
    mbedtls_ssl_conf_ca_chain(config, trusted_ca, NULL);
    mbedtls_ssl_conf_rng(config, rng, rng_context);
    mbedtls_ssl_conf_read_timeout(config, read_timeout_ms);
    return 0;
}

int tls_policy_bind(mbedtls_ssl_context *ssl, const mbedtls_ssl_config *config, const char *host)
{
    int error;
    if (host == NULL || host[0] == '\0') return MBEDTLS_ERR_SSL_BAD_INPUT_DATA;
    error = mbedtls_ssl_setup(ssl, config);
    if (error != 0) return error;
    /* Sets SNI and, being non-NULL, makes hostname verification mandatory. */
    return mbedtls_ssl_set_hostname(ssl, host);
}

int tls_policy_verified(const mbedtls_ssl_context *ssl, uint32_t *flags)
{
    *flags = mbedtls_ssl_get_verify_result(ssl);
    return *flags == 0;
}
