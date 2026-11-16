/* Replaces the package's ports/tls_client.c, tls_hardware.c and tls_net.c, whose
 * rand() entropy, disabled date checks and bundled expired CA are not acceptable.
 *
 * TLS memory comes from a private CCM arena: a handshake cannot exhaust the system
 * heap that lwIP and the RW007 driver depend on, and it fails alone if it runs out.
 */
#include "tls_transport.h"
#include <sys/socket.h>
#include <sys/select.h>
#include <sys/time.h>
#include <sys/ioctl.h>
#include <sys/errno.h>
#include <fcntl.h>
#include <netdb.h>
#include <unistd.h>
#include <stdio.h>
#include "app_config.h"
#include "app_status.h"
#include "platform.h"
#include "provision.h"
#include "time_sync.h"
#include "bounded_dns.h"
#include "tls_policy.h"
#include "mbedtls/ctr_drbg.h"
#include "mbedtls/entropy.h"
#include "mbedtls/platform.h"
#include "mbedtls/sha256.h"
#include "mbedtls/ssl.h"
#include "mbedtls/x509_crt.h"
#include "mbedtls/version.h"

ALIGN(8) static BCA_CCM rt_uint8_t arena[BCA_TLS_ARENA_BYTES];
static struct rt_memheap heap;
static BCA_CCM mbedtls_entropy_context entropy;
static BCA_CCM mbedtls_ctr_drbg_context drbg;
static BCA_CCM mbedtls_ssl_context ssl;
static BCA_CCM mbedtls_ssl_config config;
static BCA_CCM mbedtls_x509_crt ca_chain;
static int sock = -1;
static rt_bool_t ready, active, seeded;
static struct tls_transport_stats stats;

/* 3.6 requires a monotonic millisecond clock; use the existing extended device
 * clock, never an unauthenticated SNTP value for elapsed-time limits. */
mbedtls_ms_time_t mbedtls_ms_time(void) { return (mbedtls_ms_time_t)bca_now_ms(); }

static void *arena_calloc(size_t count, size_t size)
{
    size_t total = count * size;
    void *memory;
    if (size != 0 && total / size != count) return RT_NULL;
    memory = rt_memheap_alloc(&heap, total ? total : 1u);
    if (memory != RT_NULL) rt_memset(memory, 0, total);
    return memory;
}

static void arena_free(void *memory)
{
    if (memory != RT_NULL) rt_memheap_free(memory);
}

static mbedtls_time_t utc_now(mbedtls_time_t *out)
{
    mbedtls_time_t now = (mbedtls_time_t)time_sync_utc();
    if (out != RT_NULL) *out = now;
    return now;
}

/* MBEDTLS_ENTROPY_HARDWARE_ALT source. A failed RNG fails the handshake. */
int mbedtls_hardware_poll(void *data, unsigned char *output, size_t length, size_t *written)
{
    (void)data;
    *written = 0;
    if (!platform_random(output, length)) return MBEDTLS_ERR_ENTROPY_SOURCE_FAILED;
    *written = length;
    return 0;
}

static int bio_send(void *context, const unsigned char *data, size_t length)
{
    int count;
    (void)context;
    count = send(sock, data, length, 0);
    if (count >= 0) return count;
    return errno == EWOULDBLOCK || errno == EAGAIN ? MBEDTLS_ERR_SSL_WANT_WRITE : MBEDTLS_ERR_SSL_INTERNAL_ERROR;
}

static int wait_socket(rt_uint32_t wait_ms, rt_bool_t for_write)
{
    fd_set set, failed;
    struct timeval timeout;
    FD_ZERO(&set);
    FD_ZERO(&failed);
    FD_SET(sock, &set);
    FD_SET(sock, &failed);
    timeout.tv_sec = wait_ms / 1000u;
    timeout.tv_usec = (wait_ms % 1000u) * 1000u;
    return select(sock + 1, for_write ? RT_NULL : &set, for_write ? &set : RT_NULL, &failed, &timeout);
}

static int bio_recv(void *context, unsigned char *data, size_t length, uint32_t timeout_ms)
{
    int ready_count, count;
    (void)context;
    ready_count = wait_socket(timeout_ms, RT_FALSE);
    if (ready_count == 0) return MBEDTLS_ERR_SSL_TIMEOUT;
    if (ready_count < 0) return MBEDTLS_ERR_SSL_INTERNAL_ERROR;
    count = recv(sock, data, length, 0);
    if (count > 0) return count;
    if (count == 0) return MBEDTLS_ERR_SSL_CONN_EOF;
    return errno == EWOULDBLOCK || errno == EAGAIN ? MBEDTLS_ERR_SSL_WANT_READ : MBEDTLS_ERR_SSL_INTERNAL_ERROR;
}

/* lwIP's blocking connect can outlast a dropped link by minutes, hence non-blocking. */
static rt_err_t tcp_connect(const char *host, rt_uint16_t port)
{
    struct sockaddr_in address;
    struct timeval timeout;
    int blocking = 0, nonblocking = 1, error = 0;
    socklen_t error_length = sizeof(error);
    rt_err_t status = -RT_ERROR;

    if (bca_resolve_ipv4(host,port,&address,10000)!=RT_EOK) return -RT_ETIMEOUT;
    sock = socket(AF_INET, SOCK_STREAM, 0);
    if (sock < 0) return -RT_ERROR;
    if (ioctl(sock, FIONBIO, &nonblocking)!=0)
    { closesocket(sock); sock=-1; return -RT_ERROR; }
    if (connect(sock, (struct sockaddr *)&address, sizeof(address)) == 0) status = RT_EOK;
    else if (errno == EINPROGRESS && wait_socket(BCA_TLS_CONNECT_MS, RT_TRUE) > 0 &&
             getsockopt(sock, SOL_SOCKET, SO_ERROR, &error, &error_length) == 0 && error == 0) status = RT_EOK;
    if (status == RT_EOK)
    {
        if (ioctl(sock, FIONBIO, &blocking)!=0) status=-RT_ERROR;
        timeout.tv_sec = 5;
        timeout.tv_usec = 0;
        if (setsockopt(sock, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout))!=0) status=-RT_ERROR;
    }
    if (status!=RT_EOK)
    {
        closesocket(sock);
        sock = -1;
    }
    return status;
}

rt_err_t tls_transport_init(void)
{
    rt_err_t result = rt_memheap_init(&heap, "tls", arena, sizeof(arena));
    if (result != RT_EOK) return result;
    result=bca_dns_init();
    if(result!=RT_EOK) return result;
    mbedtls_platform_set_calloc_free(arena_calloc, arena_free);
    mbedtls_platform_set_time(utc_now);
    mbedtls_entropy_init(&entropy);
    mbedtls_ctr_drbg_init(&drbg);
    ready = RT_TRUE;
    rt_kprintf("#TLS,library=" MBEDTLS_VERSION_STRING_FULL ",verify=required,transport=tls12_only\n");
    return RT_EOK;
}

static rt_err_t fail(int error)
{
    stats.last_error = error;
    ++stats.failures;
    tls_transport_close();
    return -RT_ERROR;
}

rt_err_t tls_transport_open(const char *host, rt_uint16_t port)
{
    static const char personalization[] = "bca182-mqtt-tls";
    const char *pem;
    rt_size_t pem_length = 0;
    rt_tick_t started;
    uint32_t verify_flags = 0;
    int error;

    stats.last_error = 0;
    stats.verify_flags = 0;
    /* Preconditions are checked here too, so no caller can bypass them. */
    if (!ready || active || host == RT_NULL || host[0] == '\0' || !time_sync_valid() ||
        time_sync_utc() < BCA_TIME_MIN_EPOCH) return -RT_EINVAL;
    if (!seeded)
    {
        error = mbedtls_ctr_drbg_seed(&drbg, mbedtls_entropy_func, &entropy,
                                      (const unsigned char *)personalization, sizeof(personalization) - 1u);
        if (error != 0) return fail(error);
        seeded = RT_TRUE;
    }
    active = RT_TRUE;
    mbedtls_ssl_init(&ssl);
    mbedtls_ssl_config_init(&config);
    mbedtls_x509_crt_init(&ca_chain);
    pem = prov_ca_acquire(&pem_length);
    if (pem == RT_NULL) return fail(-1);
    error = mbedtls_x509_crt_parse(&ca_chain, (const unsigned char *)pem, pem_length);
    prov_ca_release();
    if (error != 0) return fail(error);

    error = tls_policy_configure(&config, &ca_chain, mbedtls_ctr_drbg_random, &drbg, 5000u);
    if (error != 0) return fail(error);
    error = tls_policy_bind(&ssl, &config, host);
    if (error != 0) return fail(error);

    if (tcp_connect(host, port) != RT_EOK) return fail(-2);
    mbedtls_ssl_set_bio(&ssl, RT_NULL, bio_send, RT_NULL, bio_recv);
    started = rt_tick_get();
    while ((error = mbedtls_ssl_handshake(&ssl)) != 0)
    {
        if (error != MBEDTLS_ERR_SSL_WANT_READ && error != MBEDTLS_ERR_SSL_WANT_WRITE &&
            error != MBEDTLS_ERR_SSL_TIMEOUT)
        {
            stats.verify_flags = mbedtls_ssl_get_verify_result(&ssl);
            return fail(error);
        }
        if ((rt_tick_t)(rt_tick_get() - started) >= rt_tick_from_millisecond(BCA_TLS_HANDSHAKE_MS))
            return fail(MBEDTLS_ERR_SSL_TIMEOUT);
    }
    if (!tls_policy_verified(&ssl, &verify_flags))
    {
        stats.verify_flags = verify_flags;
        return fail(MBEDTLS_ERR_X509_CERT_VERIFY_FAILED);
    }
    stats.verify_flags = 0;
    ++stats.connects;
    return RT_EOK;
}

void tls_transport_close(void)
{
    if (!active) return;
    if (sock >= 0)
    {
        (void)mbedtls_ssl_close_notify(&ssl);
        closesocket(sock);
        sock = -1;
    }
    mbedtls_ssl_free(&ssl);
    mbedtls_ssl_config_free(&config);
    mbedtls_x509_crt_free(&ca_chain);
    active = RT_FALSE;
}

int tls_transport_write(const rt_uint8_t *data, size_t length)
{
    rt_tick_t started=rt_tick_get();
    while (length != 0)
    {
        if ((rt_tick_t)(rt_tick_get()-started)>=rt_tick_from_millisecond(BCA_MQTT_PACKET_MS))
        { stats.last_error=MBEDTLS_ERR_SSL_TIMEOUT; return -1; }
        int count = mbedtls_ssl_write(&ssl, data, length);
        if (count == MBEDTLS_ERR_SSL_WANT_WRITE || count == MBEDTLS_ERR_SSL_WANT_READ) return -1;
        if (count <= 0) { stats.last_error = count; return -1; }
        data += count;
        length -= (size_t)count;
    }
    return 0;
}

int tls_transport_read(rt_uint8_t *data, size_t length, rt_uint32_t wait_ms)
{
    int count;
    if (mbedtls_ssl_get_bytes_avail(&ssl) == 0)
    {
        count = wait_socket(wait_ms, RT_FALSE);
        if (count == 0) return 0;
        if (count < 0) return -1;
    }
    count = mbedtls_ssl_read(&ssl, data, length);
    if (count > 0) return count;
    if (count == MBEDTLS_ERR_SSL_WANT_READ || count == MBEDTLS_ERR_SSL_WANT_WRITE ||
        count == MBEDTLS_ERR_SSL_TIMEOUT) return 0;
    stats.last_error = count;
    return -1;
}

void tls_transport_stats_read(struct tls_transport_stats *out)
{
    *out = stats;
    out->arena_total = heap.pool_size;
    out->arena_peak = heap.max_used_size;
}

rt_err_t tls_ca_check(const char *pem, size_t length_with_nul, char *fingerprint, size_t capacity)
{
    mbedtls_x509_crt chain;
    unsigned char digest[32];
    rt_err_t result = -RT_ERROR;
    int i;

    if (!ready || capacity < 65u) return -RT_EINVAL;
    mbedtls_x509_crt_init(&chain);
    if (mbedtls_x509_crt_parse(&chain, (const unsigned char *)pem, length_with_nul) == 0 &&
        mbedtls_x509_crt_get_ca_istrue(&chain) && chain.raw.p != RT_NULL)
    {
        if (mbedtls_sha256(chain.raw.p, chain.raw.len, digest, 0)!=0)
        { mbedtls_x509_crt_free(&chain); return -RT_ERROR; }
        for (i = 0; i < 32; ++i) rt_snprintf(fingerprint + i * 2, 3, "%02x", digest[i]);
        result = RT_EOK;
    }
    mbedtls_x509_crt_free(&chain);
    return result;
}
