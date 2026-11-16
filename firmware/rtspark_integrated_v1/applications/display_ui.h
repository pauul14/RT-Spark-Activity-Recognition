/* Status screen on the official ST7789 driver; lowest-priority application thread. */
#ifndef DISPLAY_UI_H
#define DISPLAY_UI_H
#include <rtthread.h>

rt_err_t display_ui_start(void);
#endif
