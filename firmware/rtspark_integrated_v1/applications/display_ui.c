/* Only the display thread owns the LCD. Acquisition and networking keep their R1 timing. */
#include "display_ui.h"
#include <rtdevice.h>
#include <drv_lcd.h>
#include "activity_view.h"

static struct activity_view view;
static struct rt_thread ui_thread;
static rt_bool_t started;
ALIGN(8) static rt_uint8_t ui_stack[BCA_STACK_DISPLAY];

static void draw_row(void *unused, unsigned x, unsigned y, unsigned width, uint16_t *pixels)
{
    (void)unused;
    lcd_fill_array(x, y, x + width - 1, y, pixels);
}

static void ui_entry(void *parameter)
{
    struct bca_status status;
    (void)parameter;
    for (;;) {
        bca_status_read(&status);
        activity_view_render(&view, activity_view_select(&status), draw_row, RT_NULL);
        rt_thread_mdelay(BCA_DISPLAY_PERIOD_MS);
    }
}

rt_err_t display_ui_start(void)
{
    rt_err_t result;
    if (started) return RT_EOK;
    result = rt_thread_init(&ui_thread, "display", ui_entry, RT_NULL, ui_stack,
                            sizeof ui_stack, BCA_PRIO_DISPLAY, 5);
    if (result == RT_EOK) result = rt_thread_startup(&ui_thread);
    if (result == RT_EOK) started = RT_TRUE;
    return result;
}