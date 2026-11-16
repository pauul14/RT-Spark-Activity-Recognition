/* One static DNS request, owned by the MQTT/time thread. The lwIP callback runs
 * on tcpip. A timed-out request keeps its storage until its late callback returns;
 * no dangling stack pointer, slot reuse or unbounded getaddrinfo wait is allowed.
 */
#include "bounded_dns.h"
#include <rthw.h>
#include <lwip/err.h>
#include <lwip/dns.h>
#include <lwip/tcpip.h>
#include <lwip/sockets.h>

enum dns_phase { DNS_IDLE, DNS_QUEUED, DNS_PENDING, DNS_DONE, DNS_ABANDONED };
static struct {
    struct rt_mutex owner;
    struct rt_semaphore completed;
    volatile enum dns_phase phase;
    char host[129];
    ip_addr_t address;
    rt_bool_t success;
} query;

rt_err_t bca_dns_init(void)
{
    rt_err_t result=rt_mutex_init(&query.owner,"dnsown",RT_IPC_FLAG_PRIO);
    if(result==RT_EOK) result=rt_sem_init(&query.completed,"dnsdone",0,RT_IPC_FLAG_FIFO);
    return result;
}
static void resolved(const char *name,const ip_addr_t *address,void *argument)
{
    rt_base_t level=rt_hw_interrupt_disable();
    (void)name; (void)argument;
    if(query.phase==DNS_ABANDONED) query.phase=DNS_IDLE;
    else if(query.phase==DNS_PENDING)
    {
        query.success=address!=RT_NULL;
        if(address) query.address=*address;
        query.phase=DNS_DONE;
        rt_sem_release(&query.completed);
    }
    rt_hw_interrupt_enable(level);
}
static void start_query(void *argument)
{
    err_t error;
    rt_base_t level=rt_hw_interrupt_disable();
    (void)argument;
    if(query.phase==DNS_ABANDONED) { query.phase=DNS_IDLE; rt_hw_interrupt_enable(level); return; }
    query.phase=DNS_PENDING;
    rt_hw_interrupt_enable(level);
    error=dns_gethostbyname(query.host,&query.address,resolved,RT_NULL);
    if(error==ERR_OK) resolved(query.host,&query.address,RT_NULL);
    else if(error!=ERR_INPROGRESS) resolved(query.host,RT_NULL,RT_NULL);
}
rt_err_t bca_resolve_ipv4(const char *host,rt_uint16_t port,struct sockaddr_in *out,rt_uint32_t timeout_ms)
{
    rt_err_t result=-RT_ERROR;
    rt_base_t level;
    if(!host || !host[0] || !out || !port || !timeout_ms || timeout_ms>10000 || rt_strlen(host)>128) return -RT_EINVAL;
    if(rt_mutex_take(&query.owner,RT_WAITING_NO)!=RT_EOK) return -RT_EBUSY;
    level=rt_hw_interrupt_disable();
    if(query.phase!=DNS_IDLE) { rt_hw_interrupt_enable(level); rt_mutex_release(&query.owner); return -RT_EBUSY; }
    while(rt_sem_take(&query.completed,RT_WAITING_NO)==RT_EOK) { }
    rt_strncpy(query.host,host,sizeof(query.host)-1); query.host[sizeof(query.host)-1]='\0';
    query.success=RT_FALSE; query.phase=DNS_QUEUED;
    rt_hw_interrupt_enable(level);
    if(tcpip_callback_with_block(start_query,RT_NULL,0)!=ERR_OK)
    { query.phase=DNS_IDLE; rt_mutex_release(&query.owner); return -RT_ENOMEM; }
    result=rt_sem_take(&query.completed,rt_tick_from_millisecond((rt_int32_t)timeout_ms));
    level=rt_hw_interrupt_disable();
    if(query.phase==DNS_DONE)
    {
        if(result==RT_EOK && query.success)
        {
            rt_memset(out,0,sizeof(*out)); out->sin_len=sizeof(*out); out->sin_family=AF_INET;
            out->sin_port=htons(port); out->sin_addr.s_addr=ip4_addr_get_u32(ip_2_ip4(&query.address));
            result=RT_EOK;
        }
        else result=-RT_ERROR;
        query.phase=DNS_IDLE;
    }
    else { query.phase=DNS_ABANDONED; result=-RT_ETIMEOUT; }
    rt_hw_interrupt_enable(level);
    rt_mutex_release(&query.owner);
    return result;
}
