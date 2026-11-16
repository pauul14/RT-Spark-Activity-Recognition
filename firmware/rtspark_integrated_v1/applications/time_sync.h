/* UTC for certificate validity only. Sensor timestamps never use this clock. */
#ifndef TIME_SYNC_H
#define TIME_SYNC_H
#include <rtthread.h>

/* Sets the clock only if two replies agree and fall inside the configured window. */
rt_err_t time_sync_run(const char *host);
rt_bool_t time_sync_valid(void);
rt_bool_t time_sync_due(void);
/* 0 when no valid time exists, so TLS certificate checks fail closed. */
long long time_sync_utc(void);
#endif
