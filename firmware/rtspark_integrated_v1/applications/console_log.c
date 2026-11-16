/* Local strong override of RT-Thread's weak, shared-buffer rt_kprintf.
 * Normal thread writers serialize complete calls. The sensor never calls this.
 * Exception context (any IPSR, including faults that RT-Thread's interrupt nest count
 * does not see), a locked scheduler, and early boot never take the mutex; threads wait
 * a bounded time and drop the line rather than block forever. Each call uses its own
 * stack buffer, so an unlocked writer can interleave a line but not overwrite another.
 */
#include "console_log.h"
#include "app_config.h"
#include <rthw.h>
#include <stdarg.h>
#ifndef BCA_HOST_TEST
#include <board.h>
#define BCA_IPSR() __get_IPSR()
#endif

static struct rt_mutex console_lock;
static rt_bool_t ready;
static volatile rt_uint32_t dropped;

rt_err_t console_log_init(void)
{
    rt_err_t result = rt_mutex_init(&console_lock, "log", RT_IPC_FLAG_PRIO);
    if (result == RT_EOK) ready = RT_TRUE;
    return result;
}

rt_uint32_t console_log_dropped(void)
{
    return dropped;
}

int rt_kprintf(const char *format, ...)
{
    char buffer[RT_CONSOLEBUF_SIZE];
    va_list arguments;
    rt_device_t device;
    int length;
    rt_bool_t locked = ready && (BCA_IPSR() & 0x1FFu) == 0 && rt_thread_self() != RT_NULL &&
                       rt_critical_level() == 0;

    if (locked && rt_mutex_take(&console_lock, rt_tick_from_millisecond(BCA_LOG_LOCK_TIMEOUT_MS)) != RT_EOK)
    {
        ++dropped;
        return 0;
    }
    va_start(arguments, format);
    length = rt_vsnprintf(buffer, sizeof(buffer), format, arguments);
    va_end(arguments);
    if (length < 0) length = 0;
    if ((rt_size_t)length >= sizeof(buffer)) length = sizeof(buffer) - 1u;
    device = rt_console_get_device();
    if (device != RT_NULL) rt_device_write(device, 0, buffer, (rt_size_t)length);
    else rt_hw_console_output(buffer);
    if (locked) rt_mutex_release(&console_lock);
    return length;
}
