/* From Codex's checkpoint 3G-F wifi_port.c; only names and the ready limit's source
 * changed. Routing and reset timing follow the pinned example's actual STM32 SPI
 * port. No new boot strap levels or module firmware download sequence are invented.
 */
#include "wifi_link.h"
#include "app_config.h"
#include <rtdevice.h>
#include <board.h>
#include <drv_spi.h>
#include <rthw.h>
#include <spi_wifi_rw007.h>

static rt_err_t irq_result = -RT_ERROR;
extern void spi_wifi_isr(int vector);

static void module_irq(void *parameter)
{
    (void)parameter;
    spi_wifi_isr(0);
}

/* Called by the pinned driver's rt_hw_wifi_init after event/thread setup. */
void spi_wifi_hw_init(void)
{
    irq_result = rt_pin_attach_irq(RW007_INT_BUSY_PIN, PIN_IRQ_MODE_FALLING,
                                   module_irq, RT_NULL);
    if (irq_result == RT_EOK)
        irq_result = rt_pin_irq_enable(RW007_INT_BUSY_PIN, PIN_IRQ_ENABLE);
}

rt_err_t wifi_port_irq_result(void) { return irq_result; }
void wifi_port_prepare_stop(void)
{
    rt_pin_mode(RW007_RST_PIN,PIN_MODE_OUTPUT);
    rt_pin_write(RW007_RST_PIN,PIN_LOW);
}

/* Hard-timer callback: one GPIO write, no locks, logging, allocation or SPI.
 * This inhibits RF even while the supervisor is blocked. Physical timing needs test.
 */
void wifi_port_rf_off_isr(void) { HAL_GPIO_WritePin(GPIOG, GPIO_PIN_15, GPIO_PIN_RESET); }

void wifi_port_stop(void)
{
    rt_pin_irq_enable(RW007_INT_BUSY_PIN, PIN_IRQ_DISABLE);
    rt_pin_write(RW007_RST_PIN, PIN_LOW);
}

rt_err_t wifi_port_start(void)
{
    rt_tick_t start;
    rt_err_t result;
    if (rt_device_find(RW007_SPI_BUS_NAME) == RT_NULL) return -RT_ENOSYS;
    rt_pin_mode(RW007_RST_PIN, PIN_MODE_OUTPUT);
    rt_pin_mode(RW007_INT_BUSY_PIN, PIN_MODE_INPUT_PULLDOWN);
    rt_pin_write(RW007_RST_PIN, PIN_LOW);
    rt_thread_mdelay(100);
    { rt_base_t level=rt_hw_interrupt_disable();
      if (wifi_link_stopping()) { rt_hw_interrupt_enable(level); return -RT_ERROR; }
      rt_pin_write(RW007_RST_PIN, PIN_HIGH);
      rt_hw_interrupt_enable(level); }
    start = rt_tick_get();
    while (!rt_pin_read(RW007_INT_BUSY_PIN))
    {
        if ((rt_tick_t)(rt_tick_get()-start) >= rt_tick_from_millisecond(BCA_WIFI_READY_MS))
        {
            rt_pin_write(RW007_RST_PIN, PIN_LOW);
            return -RT_ETIMEOUT;
        }
        rt_thread_mdelay(5);
    }
    rt_thread_mdelay(200);
    rt_pin_mode(RW007_INT_BUSY_PIN, PIN_MODE_INPUT_PULLUP);
    result = rt_hw_spi_device_attach(RW007_SPI_BUS_NAME, "wspi", GPIOF, GPIO_PIN_10);
    if (result != RT_EOK) return result;
    result = rt_hw_wifi_init("wspi");
    if (result != RT_EOK) return result;
    return wifi_link_stopping() ? -RT_ERROR : irq_result;
}
