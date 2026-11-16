/* HardFault and assert handling that cannot be defeated by a corrupt stack pointer.
 *
 * The 2026-11-07 lockup: a thread-mode fault arrived with MSP and PSP both outside RAM,
 * and RT-Thread's handler pushed onto that MSP before its first rt_kprintf, faulting
 * inside HardFault. Here the vector entry switches to a dedicated stack before any
 * push or call, records a compact CRC-protected record in memory that survives a system
 * reset, and resets deliberately. The board returns to HOLD; the record and the reset
 * cause are printed at the next boot. Nothing on this path prints, locks or allocates.
 */
#include <rthw.h>
#include <board.h>
#include "app_config.h"
#include "fault_guard.h"
#include "fault_record.h"

/* Not zeroed or loaded by startup: survives a system reset, lost on power removal. */
static struct bca_fault_record saved __attribute__((section(".bca_noinit"), used));
rt_uint32_t bca_fault_stack[BCA_FAULT_STACK_BYTES / 4u] __attribute__((section(".bca_noinit"), aligned(8), used));
rt_uint32_t *const bca_fault_stack_top = &bca_fault_stack[BCA_FAULT_STACK_BYTES / 4u];

static struct bca_fault_record previous;
static rt_bool_t previous_valid;
static rt_uint32_t boot_csr;

extern rt_uint32_t _sstack[], _estack[];

__attribute__((naked, used)) void bca_hardfault_entry(void)
{
    /* No stack access until MSP points at the emergency stack. */
    __asm volatile(
        "mov   r0, lr                  \n"  /* EXC_RETURN */
        "mrs   r1, msp                 \n"
        "mrs   r2, psp                 \n"
        "ldr   r3, =bca_fault_stack_top\n"
        "ldr   r3, [r3]                \n"
        "msr   msp, r3                 \n"
        "isb                           \n"
        "b     bca_fault_handle        \n"
        ".ltorg                        \n");
}

static void record_and_reset(rt_uint32_t kind, rt_uint32_t exc_return, rt_uint32_t msp, rt_uint32_t psp,
                             rt_uint32_t function, rt_uint32_t line) __attribute__((noreturn));
static void record_and_reset(rt_uint32_t kind, rt_uint32_t exc_return, rt_uint32_t msp, rt_uint32_t psp,
                             rt_uint32_t function, rt_uint32_t line)
{
    struct bca_fault_regs regs;

    regs.cfsr = SCB->CFSR;
    regs.hfsr = SCB->HFSR;
    regs.mmfar = SCB->MMFAR;
    regs.bfar = SCB->BFAR;
    regs.icsr = SCB->ICSR;
    bca_fault_capture(&saved, kind, exc_return, msp, psp, &regs, rt_tick_get(), (rt_uint32_t)(rt_ubase_t)rt_thread_self());
    saved.assert_function = function;
    saved.assert_line = line;
    saved.crc = bca_fault_crc(&saved);
    __DSB();
#if BCA_FAULT_HALT_FOR_DEBUG
    for (;;) __NOP(); /* bench-only: keep the state for a debugger */
#else
    NVIC_SystemReset();
#endif
}

void bca_fault_handle(rt_uint32_t exc_return, rt_uint32_t msp, rt_uint32_t psp)
{
    record_and_reset(BCA_FAULT_HARD, exc_return, msp, psp, 0, 0);
}

static void assert_hook(const char *expression, const char *function, rt_size_t line)
{
    (void)expression;
    rt_hw_interrupt_disable();
    record_and_reset(BCA_FAULT_ASSERT, 0, __get_MSP(), __get_PSP(), (rt_uint32_t)(rt_ubase_t)function, (rt_uint32_t)line);
}

void bca_fault_boot(void)
{
    boot_csr = RCC->CSR;
    RCC->CSR |= RCC_CSR_RMVF; /* only after recording, so every boot reports one reset */
    if (bca_fault_valid(&saved))
    {
        previous = saved;
        previous_valid = RT_TRUE;
    }
    saved.magic = 0; /* report a record once */
    rt_assert_set_hook(assert_hook);
}

static rt_bool_t in_flash(rt_uint32_t address)
{
    return address >= 0x08000000u && address < 0x08100000u;
}

void bca_fault_report(void)
{
    rt_kprintf("#RESET,csr=0x%08lX,cause=%s,fault_record=%s\n", (unsigned long)boot_csr,
               bca_reset_cause(boot_csr), previous_valid ? "present" : "none");
    if (!previous_valid) return;
    rt_kprintf("#FAULT_RECORD,kind=%s,cfsr=0x%08lX,hfsr=0x%08lX,mmfar=0x%08lX,bfar=0x%08lX,icsr=0x%08lX\n",
               previous.kind == BCA_FAULT_HARD ? "hardfault" : "assert", (unsigned long)previous.regs.cfsr,
               (unsigned long)previous.regs.hfsr, (unsigned long)previous.regs.mmfar,
               (unsigned long)previous.regs.bfar, (unsigned long)previous.regs.icsr);
    rt_kprintf("#FAULT_CONTEXT,exc_return=0x%08lX,msp=0x%08lX,psp=0x%08lX,frame=%s,pc=0x%08lX,lr=0x%08lX,xpsr=0x%08lX,thread=%s,tick=%lu\n",
               (unsigned long)previous.exc_return, (unsigned long)previous.msp, (unsigned long)previous.psp,
               previous.frame_valid ? "valid" : "outside_ram", (unsigned long)previous.frame[6],
               (unsigned long)previous.frame[5], (unsigned long)previous.frame[7],
               previous.thread_name[0] ? previous.thread_name : "-", (unsigned long)previous.tick);
    if (previous.kind == BCA_FAULT_ASSERT)
        rt_kprintf("#FAULT_ASSERT,function=%s,line=%lu\n",
                   in_flash(previous.assert_function) ? (const char *)(rt_ubase_t)previous.assert_function : "?",
                   (unsigned long)previous.assert_line);
}

void bca_fault_status(void)
{
    rt_kprintf("#RESET,csr=0x%08lX,cause=%s,fault_record=%s\n", (unsigned long)boot_csr, bca_reset_cause(boot_csr),
               previous_valid ? (previous.kind == BCA_FAULT_HARD ? "hardfault" : "assert") : "none");
}

#define MSP_PAINT 0xA5A5A5A5u

void bca_msp_paint(void)
{
    /* Thread context with interrupts off: no exception is using the main stack. */
    rt_base_t level = rt_hw_interrupt_disable();
    rt_uint32_t *limit = (rt_uint32_t *)(rt_ubase_t)(__get_MSP() & ~3u);
    rt_uint32_t *word;

    for (word = _sstack; word < limit && word < _estack; ++word) *word = MSP_PAINT;
    rt_hw_interrupt_enable(level);
}

rt_uint32_t bca_msp_free_bytes(void)
{
    const rt_uint32_t *word = _sstack;

    while (word < _estack && *word == MSP_PAINT) ++word;
    return (rt_uint32_t)((rt_ubase_t)word - (rt_ubase_t)_sstack);
}

rt_uint32_t bca_msp_size_bytes(void)
{
    return (rt_uint32_t)((rt_ubase_t)_estack - (rt_ubase_t)_sstack);
}
