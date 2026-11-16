/* Minimal SNTP client. The netutils client is not used: it places the device MAC
 * and firmware details in each request, and nothing in it checks the reply's origin.
 *
 * Limitation: SNTP is unauthenticated. An on-path attacker could make an expired
 * certificate look current, though never forge a chain, a signature or a hostname.
 */
#include "time_sync.h"
#include <sys/socket.h>
#include <sys/time.h>
#include <netdb.h>
#include <unistd.h>
#include <string.h>
#include <rthw.h>
#include "app_config.h"
#include "app_status.h"
#include "platform.h"
#include "bounded_dns.h"

#define NTP_UNIX_OFFSET 2208988800LL

static volatile long long synced_utc;
static volatile rt_uint64_t synced_at_ms;

/* Cortex-M4 cannot atomically read these 64-bit values. Readers must not mix
 * halves or observe one field from before synchronization and one from after. */
static void read_synced(long long *utc,rt_uint64_t *at)
{
    rt_base_t level=rt_hw_interrupt_disable();
    *utc=synced_utc; *at=synced_at_ms;
    rt_hw_interrupt_enable(level);
}

static rt_uint32_t read_be32(const rt_uint8_t *p)
{
    return ((rt_uint32_t)p[0] << 24) | ((rt_uint32_t)p[1] << 16) | ((rt_uint32_t)p[2] << 8) | p[3];
}

static rt_err_t query(const struct sockaddr *address, socklen_t address_length, long long *utc)
{
    rt_uint8_t request[48], reply[48];
    struct timeval timeout;
    long long value;
    int count, sock;

    rt_memset(request, 0, sizeof(request));
    request[0] = 0x23; /* LI=0, VN=4, mode=3 (client) */
    /* A random transmit field must be echoed, which defeats off-path spoofing. */
    if (!platform_random(&request[40], 8)) return -RT_ERROR;
    sock = socket(address->sa_family, SOCK_DGRAM, 0);
    if (sock < 0) return -RT_ERROR;
    timeout.tv_sec = BCA_SNTP_TIMEOUT_MS / 1000u;
    timeout.tv_usec = 0;
    if (setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout))!=0)
    { closesocket(sock); return -RT_ERROR; }
    count = connect(sock, address, address_length) == 0 ? send(sock, request, sizeof(request), 0) : -1;
    if (count == (int)sizeof(request)) count = recv(sock, reply, sizeof(reply), 0);
    closesocket(sock);
    if (count != (int)sizeof(reply)) return -RT_ETIMEOUT;
    if ((reply[0] & 0x07u) != 4u || (reply[0] >> 6) == 3u || reply[1] == 0u || reply[1] > 15u ||
        rt_memcmp(&reply[24], &request[40], 8) != 0) return -RT_ERROR;
    value = (long long)read_be32(&reply[40]) - NTP_UNIX_OFFSET;
    if (value < BCA_TIME_MIN_EPOCH || value > BCA_TIME_MAX_EPOCH) return -RT_ERROR;
    *utc = value;
    return RT_EOK;
}

rt_err_t time_sync_run(const char *host)
{
    struct sockaddr_in address;
    long long first = 0, second = 0, gap;
    rt_uint64_t first_ms;
    rt_err_t error;

    if (bca_resolve_ipv4(host,123,&address,10000)!=RT_EOK) return -RT_ETIMEOUT;
    error = query((struct sockaddr *)&address,sizeof(address),&first);
    first_ms = bca_now_ms();
    if (error == RT_EOK) error = query((struct sockaddr *)&address,sizeof(address),&second);
    if (error != RT_EOK) return error;
    gap = second - first - (long long)((bca_now_ms() - first_ms) / 1000u);
    if (gap > BCA_TIME_AGREE_S || gap < -BCA_TIME_AGREE_S) return -RT_ERROR;
    rt_enter_critical();
    synced_utc = second;
    synced_at_ms = bca_now_ms();
    rt_exit_critical();
    return RT_EOK;
}

rt_bool_t time_sync_valid(void)
{
    long long utc; rt_uint64_t at;
    read_synced(&utc,&at);
    return utc != 0 && bca_now_ms() - at <= BCA_TIME_MAX_AGE_MS;
}

rt_bool_t time_sync_due(void)
{
    long long utc; rt_uint64_t at;
    read_synced(&utc,&at);
    return utc==0 || bca_now_ms()-at>=BCA_TIME_RESYNC_MS;
}

long long time_sync_utc(void)
{
    long long utc; rt_uint64_t at,elapsed;
    read_synced(&utc,&at); elapsed=bca_now_ms()-at;
    if (!utc || elapsed>BCA_TIME_MAX_AGE_MS) return 0;
    return utc+(long long)(elapsed/1000u);
}
