#ifndef CONSOLE_LOG_H
#define CONSOLE_LOG_H
#include <rtthread.h>
rt_err_t console_log_init(void);
rt_uint32_t console_log_dropped(void);
#endif
