#include "platform.h"
#include <rthw.h>
#include <board.h>
#include <stdlib.h>
#include "app_config.h"

char g_device_id[40], g_boot_id[48];
static struct rt_mutex rng_lock;

/* Startup never touches CCM, so its contents are undefined until this runs. */
static int ccm_clear(void)
{
    extern char _sccmram[], _eccmram[];
    __HAL_RCC_CCMDATARAMEN_CLK_ENABLE();
    rt_memset(_sccmram, 0, (rt_size_t)(_eccmram - _sccmram));
    return 0;
}
INIT_BOARD_EXPORT(ccm_clear);

/* Same RNG sequence as the verified sensor firmware, with the FIPS continuous test
 * RM0090 requires of software: consecutive words must differ.
 */
static int random_words(uint32_t *words, size_t count)
{
    uint32_t previous = 0, i;
    int ok = 1;

    __HAL_RCC_RNG_CLK_ENABLE();
    RNG->CR = RNG_CR_RNGEN;
    for (i = 0; ok && i <= count; ++i) {
        uint32_t value;
        rt_tick_t start = rt_tick_get();
        while ((RNG->SR & RNG_SR_DRDY) == 0) {
            if ((RNG->SR & (RNG_SR_CECS | RNG_SR_SECS)) != 0 ||
                (rt_tick_t)(rt_tick_get() - start) >= 100u) { ok = 0; break; }
            rt_thread_delay(1);
        }
        if (!ok || (RNG->SR & (RNG_SR_CECS | RNG_SR_SECS)) != 0) { ok = 0; break; }
        value = RNG->DR;
        if (i > 0) { /* the first generated word is discarded */
            if (value == previous) { ok = 0; break; }
            words[i - 1] = value;
        }
        previous = value;
    }
    RNG->CR = 0;
    return ok;
}

int platform_random(void *output, size_t length)
{
    uint32_t words[16];
    rt_uint8_t *at = output;
    int ok = 1;

    if (rt_mutex_take(&rng_lock, RT_WAITING_FOREVER) != RT_EOK) return 0;
    while (ok && length != 0) {
        size_t chunk = length > sizeof(words) ? sizeof(words) : length;
        ok = random_words(words, (chunk + 3u) / 4u);
        if (ok) rt_memcpy(at, words, chunk);
        at += chunk;
        length -= chunk;
    }
    rt_memset(words, 0, sizeof(words));
    rt_mutex_release(&rng_lock);
    return ok;
}

int platform_identity_init(void)
{
    uint32_t words[5];
    const volatile uint32_t *uid = (const volatile uint32_t *)0x1FFF7A10u;

    if (rt_mutex_init(&rng_lock, "rng", RT_IPC_FLAG_PRIO) != RT_EOK) return 0;
    if (!platform_random(words, sizeof(words))) return 0;
    rt_snprintf(g_device_id, sizeof(g_device_id), "rtspark-%08lx%08lx%08lx",
                (unsigned long)uid[0], (unsigned long)uid[1], (unsigned long)uid[2]);
    rt_snprintf(g_boot_id, sizeof(g_boot_id), BCA_BOOT_ID_PREFIX "%08lx%08lx%08lx%08lx",
                (unsigned long)words[0], (unsigned long)words[1], (unsigned long)words[2], (unsigned long)words[3]);
    srand(words[4]); /* lwIP LWIP_RAND: DNS ids and TCP ports, not cryptographic use */
    return 1;
}
