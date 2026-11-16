/* Device identity, hardware entropy and memory placement. */
#ifndef PLATFORM_H
#define PLATFORM_H
#include <rtthread.h>
#include <stddef.h>

/* 64 KiB CCM: CPU-only and outside the BIN. Nothing placed here may be a DMA buffer
 * (this build's SPI and UART transfers do not use DMA) or rely on static initializers.
 */
#define BCA_CCM __attribute__((section(".ccmram")))

extern char g_device_id[40], g_boot_id[48];

/* Creates device_id from the MCU UID and a fresh boot_id from the hardware RNG. */
int platform_identity_init(void);
/* Fails closed on any RNG fault: callers must never substitute weaker randomness. */
int platform_random(void *output, size_t length);
#endif
