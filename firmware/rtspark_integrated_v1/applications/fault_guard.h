#ifndef FAULT_GUARD_H
#define FAULT_GUARD_H
#include <rtthread.h>

/* Call first in main(): records and clears RCC_CSR, adopts any valid fault record
 * left by the previous run, and installs the assert hook.
 */
void bca_fault_boot(void);
/* Prints #RESET, #FAULT_RECORD (once) after the console is ready. */
void bca_fault_report(void);
/* One-line summary for bca_status. */
void bca_fault_status(void);

/* Main (interrupt) stack watermark: paint once from thread context, read later. */
void bca_msp_paint(void);
rt_uint32_t bca_msp_free_bytes(void);
rt_uint32_t bca_msp_size_bytes(void);

/* Vector-table entry and its C continuation; not called directly. */
void bca_hardfault_entry(void);
void bca_fault_handle(rt_uint32_t exc_return, rt_uint32_t msp, rt_uint32_t psp) __attribute__((noreturn));

#endif
