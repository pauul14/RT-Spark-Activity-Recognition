/* Fault record capture shared by the HardFault and assert paths.
 *
 * Runs on the emergency stack with possibly corrupt MSP/PSP values, so it never reads
 * through a pointer before checking that it lies entirely inside SRAM or CCM.
 */
#include "fault_record.h"

/* Host tests point these at a host buffer; the target uses the STM32F407 map. */
#ifndef BCA_SRAM_BASE
#define BCA_SRAM_BASE 0x20000000u
#define BCA_SRAM_SIZE 0x00020000u
#define BCA_CCM_BASE  0x10000000u
#define BCA_CCM_SIZE  0x00010000u
#endif

rt_bool_t bca_ram_range_ok(rt_uint32_t address, rt_uint32_t length)
{
    if ((address & 3u) != 0 || length == 0) return RT_FALSE;
    if (address >= BCA_SRAM_BASE && address - BCA_SRAM_BASE <= BCA_SRAM_SIZE - length) return RT_TRUE;
    if (address >= BCA_CCM_BASE && address - BCA_CCM_BASE <= BCA_CCM_SIZE - length) return RT_TRUE;
    return RT_FALSE;
}

rt_uint32_t bca_fault_crc(const struct bca_fault_record *record)
{
    const rt_uint8_t *bytes = (const rt_uint8_t *)record;
    rt_uint32_t crc = 0xFFFFFFFFu;
    rt_size_t i;
    int bit;

    for (i = 0; i < (rt_size_t)((const rt_uint8_t *)&record->crc - bytes); ++i)
    {
        crc ^= bytes[i];
        for (bit = 0; bit < 8; ++bit) crc = (crc >> 1) ^ (0xEDB88320u & (0u - (crc & 1u)));
    }
    return ~crc;
}

void bca_fault_capture(struct bca_fault_record *record, rt_uint32_t kind, rt_uint32_t exc_return,
                       rt_uint32_t msp, rt_uint32_t psp, const struct bca_fault_regs *regs,
                       rt_uint32_t tick, rt_uint32_t thread)
{
    rt_uint8_t *raw = (rt_uint8_t *)record;
    rt_uint32_t frame_sp = (exc_return & 4u) ? psp : msp;
    rt_size_t i;

    for (i = 0; i < sizeof(*record); ++i) raw[i] = 0;
    record->magic = BCA_FAULT_MAGIC;
    record->version = BCA_FAULT_VERSION;
    record->kind = kind;
    record->exc_return = exc_return;
    record->msp = msp;
    record->psp = psp;
    if (regs != RT_NULL) record->regs = *regs;
    record->tick = tick;
    record->thread = thread;
    if (kind == BCA_FAULT_HARD && bca_ram_range_ok(frame_sp, sizeof(record->frame)))
    {
        const volatile rt_uint32_t *frame = (const volatile rt_uint32_t *)(rt_ubase_t)frame_sp;
        for (i = 0; i < 8; ++i) record->frame[i] = frame[i];
        record->frame_valid = 1;
    }
    /* The object name is the first member of struct rt_thread. */
    if (bca_ram_range_ok(thread, RT_NAME_MAX))
    {
        const volatile char *name = (const volatile char *)(rt_ubase_t)thread;
        for (i = 0; i < RT_NAME_MAX; ++i)
        {
            char c = name[i];
            record->thread_name[i] = (c >= 32 && c < 127) ? c : 0;
            if (c == 0) break;
        }
    }
    record->crc = bca_fault_crc(record);
}

rt_bool_t bca_fault_valid(const struct bca_fault_record *record)
{
    return record->magic == BCA_FAULT_MAGIC && record->version == BCA_FAULT_VERSION &&
           (record->kind == BCA_FAULT_HARD || record->kind == BCA_FAULT_ASSERT) &&
           record->crc == bca_fault_crc(record);
}

const char *bca_reset_cause(rt_uint32_t csr)
{
    /* RCC_CSR flags accumulate until cleared; report the most specific one. A power-on
     * reset also sets PIN and BOR, and every internal reset also drives the NRST pin.
     */
    if (csr & (1u << 30)) return "wwdg";
    if (csr & (1u << 29)) return "iwdg";
    if (csr & (1u << 31)) return "lpwr";
    if (csr & (1u << 28)) return "software";
    if (csr & (1u << 27)) return "power_on";
    if (csr & (1u << 25)) return "brown_out";
    if (csr & (1u << 26)) return "pin";
    return "none";
}
