#ifndef FAULT_RECORD_H
#define FAULT_RECORD_H
/* Portable fault-record logic. No register access and no RT-Thread calls, so it is
 * safe on the emergency fault stack and can be compiled and tested on the host.
 */
#include <rtthread.h>

#define BCA_FAULT_MAGIC   0x46415531u /* "FAU1" */
#define BCA_FAULT_VERSION 1u

enum bca_fault_kind { BCA_FAULT_NONE = 0, BCA_FAULT_HARD = 1, BCA_FAULT_ASSERT = 2 };

struct bca_fault_regs
{
    rt_uint32_t cfsr, hfsr, mmfar, bfar, icsr;
};

struct bca_fault_record
{
    rt_uint32_t magic, version, kind;
    rt_uint32_t exc_return, msp, psp;
    struct bca_fault_regs regs;
    rt_uint32_t frame_valid;
    rt_uint32_t frame[8];             /* r0 r1 r2 r3 r12 lr pc xpsr, only when frame_valid */
    rt_uint32_t tick, thread;
    char thread_name[RT_NAME_MAX + 1]; /* only when the thread pointer was in RAM; always terminated */
    rt_uint32_t assert_function, assert_line;
    rt_uint32_t crc;
};

rt_bool_t bca_ram_range_ok(rt_uint32_t address, rt_uint32_t length);
rt_uint32_t bca_fault_crc(const struct bca_fault_record *record);
void bca_fault_capture(struct bca_fault_record *record, rt_uint32_t kind, rt_uint32_t exc_return,
                       rt_uint32_t msp, rt_uint32_t psp, const struct bca_fault_regs *regs,
                       rt_uint32_t tick, rt_uint32_t thread);
rt_bool_t bca_fault_valid(const struct bca_fault_record *record);
const char *bca_reset_cause(rt_uint32_t csr);

#endif
