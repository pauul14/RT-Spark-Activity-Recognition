/* Physically verified Checkpoint 3F-B acquisition, moved out of main.c.
 * Everything from read_register through near_rail, the register table and the
 * sampling loop are the verified code; validation/check_static.py compares them.
 * Integration changes: telemetry_push() after each filtered output, status updates,
 * and serial evidence only on request, because the acquisition thread still never
 * performs I/O and the shared console must stay usable for provisioning.
 */
#include <rtthread.h>
#include <rtdevice.h>
#include <rthw.h>
#include <board.h>
#include <finsh.h>
#include <stdlib.h>
#include <icm20608.h>
#include "app_config.h"
#include "app_status.h"
#include "motion_acq.h"
#include "motion_core.h"
#include "platform.h"

#define SENSOR_BUS       "i2c2"
#define SENSOR_ADDRESS   0x68
#define WHO_AM_I_REG     0x75
#define EXPECTED_ID      0xAF
#define PWR_MGMT_1_REG  0x6B
#define DEVICE_RESET_BIT 0x80
#define SAMPLE_HZ        50
#define SAMPLE_PERIOD_MS (1000 / SAMPLE_HZ)

#if RT_TICK_PER_SECOND % SAMPLE_HZ != 0
#error "Sensor probe requires an integral number of scheduler ticks per sample"
#endif

/* RT-Thread's BSP registers software I2C before main. Addresses here are 7-bit.
 * This transaction writes only the register pointer, then uses a repeated START.
 */
static rt_err_t read_register(struct rt_i2c_bus_device *bus,
                              rt_uint8_t reg, rt_uint8_t *value)
{
    struct rt_i2c_msg messages[2];
    messages[0].addr = SENSOR_ADDRESS;
    messages[0].flags = RT_I2C_WR;
    messages[0].buf = &reg;
    messages[0].len = 1;
    messages[1].addr = SENSOR_ADDRESS;
    messages[1].flags = RT_I2C_RD;
    messages[1].buf = value;
    messages[1].len = 1;
    return rt_i2c_transfer(bus, messages, 2) == 2 ? RT_EOK : -RT_ERROR;
}

/* RM-000030 Rev 1.0 sections 3.6-3.12, 3.15, 3.22-3.25.
 * Complete bytes are written. Readback masks cover defined active controls;
 * reserved bits and unused low-power oscillator bits are not compared.
 * Both bypass fields are zero: DLPF enabled, 1 kHz filter-path base rate.
 * SMPLRT_DIV=19 => register update rate 1000/(1+19)=50 Hz.
 * Host polls DATA_RDY_INT and only reads when a new frame is available.
 * Software FIR provides the separate 50-to-5 Hz anti-alias stage.
 * RM-000030 sections 3.15-3.20: status is read-to-clear, burst 0x3B..0x48.
 * Hardware 10/10.2 Hz LPFs do not guarantee rejection of all inputs above 25 Hz.
 */
struct sensor_setting
{
    const char *name;
    rt_uint8_t reg;
    rt_uint8_t value;
    rt_uint8_t mask;
};
static const struct sensor_setting sensor_settings[] =
{
    {"PWR_MGMT_1",   0x6B, 0x01, 0xFF}, /* CLKSEL=001; awake, continuous */
    {"PWR_MGMT_2",   0x6C, 0x00, 0xBF}, /* all axes on; FIFO_LP_EN=0 */
    {"USER_CTRL",    0x6A, 0x00, 0x55}, /* I2C on; FIFO and reset bits off */
    {"FIFO_EN",      0x23, 0x00, 0xF8}, /* no FIFO streams */
    {"INT_ENABLE",   0x38, 0x01, 0xF5}, /* enable data-ready status; no MCU IRQ */
    {"INT_PIN_CFG", 0x37, 0x00, 0xFC}, /* only INT_STATUS reads clear status */
    {"LP_MODE_CFG",  0x1E, 0x00, 0xF0}, /* gyro duty cycling off; LP averaging default */
    {"ACCEL_INTEL_CTRL", 0x69, 0x00, 0xC0}, /* wake-on-motion disabled */
    {"GYRO_CONFIG",  0x1B, 0x10, 0xFB}, /* FS_SEL=2; FCHOICE_B=00; no self-test */
    {"ACCEL_CONFIG", 0x1C, 0x10, 0xF8}, /* ACCEL_FS_SEL=2; no self-test */
    {"CONFIG",       0x1A, 0x05, 0x7F}, /* DLPF_CFG=5: gyro 3-dB BW 10 Hz */
    {"ACCEL_CONFIG2",0x1D, 0x05, 0x3F}, /* FCHOICE_B=0; DLPF=5: 10.2 Hz */
    {"SMPLRT_DIV",   0x19, 0x13, 0xFF}, /* 50 Hz register updates, not 5 Hz */
};

static rt_err_t write_register(struct rt_i2c_bus_device *bus,
                               rt_uint8_t reg, rt_uint8_t value)
{
    rt_uint8_t bytes[2] = {reg, value};
    struct rt_i2c_msg message;
    message.addr = SENSOR_ADDRESS;
    message.flags = RT_I2C_WR;
    message.buf = bytes;
    message.len = sizeof(bytes);
    return rt_i2c_transfer(bus, &message, 1) == 1 ? RT_EOK : -RT_ERROR;
}

static rt_err_t check_identity(struct rt_i2c_bus_device *bus, const char *phase)
{
    rt_uint8_t identity = 0;
    if (read_register(bus, WHO_AM_I_REG, &identity) != RT_EOK)
    {
        rt_kprintf("#ERROR,who_am_i_read_failed,phase=%s,actual=unavailable,missing_or_unresponsive_sensor\n",
                   phase);
        return -RT_ERROR;
    }
    rt_kprintf("#IDENTITY,phase=%s,address=0x68,register=0x75,actual=0x%02x,expected=0xAF\n",
               phase, (unsigned int)identity);
    if (identity != EXPECTED_ID)
    {
        rt_kprintf("#ERROR,unsupported_sensor,acquisition_not_started\n");
        return -RT_ERROR;
    }
    return RT_EOK;
}

/* Sensor-only reset, prepared firmware behavior; no STM32 reset is requested.
 * This is called only AFTER a successful initial WHO_AM_I read.
 * 100 ms is a conservative engineering wait, not a claimed reset-time minimum.
 */
static rt_err_t probe_reset(struct rt_i2c_bus_device *bus)
{
    rt_uint8_t power = 0;
    if (write_register(bus, PWR_MGMT_1_REG, DEVICE_RESET_BIT) != RT_EOK)
    {
        rt_kprintf("#ERROR,sensor_reset_write_failed,acquisition_not_started\n");
        return -RT_ERROR;
    }
    rt_thread_mdelay(100);
    if (read_register(bus, PWR_MGMT_1_REG, &power) != RT_EOK)
    {
        rt_kprintf("#ERROR,sensor_reset_read_failed,acquisition_not_started\n");
        return -RT_ERROR;
    }
    rt_kprintf("#REG,phase=after_reset,name=PWR_MGMT_1,addr=0x6B,actual=0x%02x\n",
               (unsigned int)power);
    if ((power & DEVICE_RESET_BIT) != 0)
    {
        rt_kprintf("#ERROR,sensor_reset_not_complete,acquisition_not_started\n");
        return -RT_ERROR;
    }
    return check_identity(bus, "after_reset");
}

/* Retain the existing helper name; now validate the WHOLE acquisition config.
 * Print actual register bytes on startup or on mismatch, never fabricated bytes.
 * Masked readback excludes reserved bits. Every relevant active field is checked.
 */
static rt_err_t verify_ranges(struct rt_i2c_bus_device *bus, rt_bool_t diagnostic)
{
    rt_size_t i;
    for (i = 0; i < sizeof(sensor_settings) / sizeof(sensor_settings[0]); ++i)
    {
        const struct sensor_setting *setting = &sensor_settings[i];
        rt_uint8_t actual = 0;
        if (read_register(bus, setting->reg, &actual) != RT_EOK)
        {
            rt_kprintf("#ERROR,config_read_failed,name=%s,addr=0x%02x,acquisition_stopped\n",
                       setting->name, (unsigned int)setting->reg);
            return -RT_ERROR;
        }
        if (diagnostic || (actual & setting->mask) != setting->value)
            rt_kprintf("#REG,name=%s,addr=0x%02x,actual=0x%02x,expected=0x%02x,mask=0x%02x\n",
                       setting->name, (unsigned int)setting->reg, (unsigned int)actual,
                       (unsigned int)setting->value, (unsigned int)setting->mask);
        if ((actual & setting->mask) != setting->value)
        {
            rt_kprintf("#ERROR,unexpected_configuration,name=%s,acquisition_stopped\n",
                       setting->name);
            return -RT_ERROR;
        }
    }
    return RT_EOK;
}

static rt_err_t configure_sensor(struct rt_i2c_bus_device *bus)
{
    rt_size_t i;
    /* The official initializer must run FIRST: it writes PWR_MGMT_1=0,
     * +/-2g, +/-250dps, and ACCEL_CONFIG2=1. Override all of those here.
     * No later driver initialization or calibration is permitted.
     */
    for (i = 0; i < sizeof(sensor_settings) / sizeof(sensor_settings[0]); ++i)
    {
        const struct sensor_setting *setting = &sensor_settings[i];
        if (write_register(bus, setting->reg, setting->value) != RT_EOK)
        {
            rt_kprintf("#ERROR,config_write_failed,name=%s,addr=0x%02x,acquisition_not_started\n",
                       setting->name, (unsigned int)setting->reg);
            return -RT_ERROR;
        }
        if (setting->reg == PWR_MGMT_1_REG)
            rt_thread_mdelay(100); /* Gyro wake/clock-selection settling margin. */
    }
    rt_thread_mdelay(200); /* Initial filter settling margin; validate on hardware. */
    return verify_ranges(bus, RT_TRUE);
}

/* Integer formatting avoids a dependency on floating-point printf support.
 * Output is nominal physical scale, truncated toward zero to six decimals.
 */
static void format_micro(char output[16], rt_int32_t micro)
{
    const char *sign = micro < 0 ? "-" : "";
    rt_uint32_t magnitude = micro < 0 ? (rt_uint32_t)(-(rt_int64_t)micro)
                                      : (rt_uint32_t)micro;
    rt_snprintf(output, 16, "%s%lu.%06lu", sign,
                (unsigned long)(magnitude / 1000000u),
                (unsigned long)(magnitude % 1000000u));
}

static void format_u64(char output[21], rt_uint64_t value)
{
    char reverse[20];
    unsigned int count = 0, i;
    do
    {
        reverse[count++] = (char)('0' + value % 10u);
        value /= 10u;
    } while (value != 0);
    for (i = 0; i < count; ++i)
        output[i] = reverse[count - i - 1];
    output[count] = '\0';
}

static rt_bool_t near_rail(rt_int16_t value)
{
    return value >= 32760 || value <= -32760;
}


#if RT_TICK_PER_SECOND != 1000
#error "Version 1 timestamps require the verified 1 ms RT-Thread tick"
#endif
typedef char require_32_bit_tick[(sizeof(rt_tick_t)==sizeof(uint32_t)) ? 1 : -1];
#define RECORD_CAPACITY BCA_TRACE_RECORDS
#define ACQ_PRIORITY BCA_PRIO_MOTION
#define FRESH_TIMEOUT_MS 100u
struct record {
    uint64_t t_ms;
    uint32_t sequence, source_sequence, filter_epoch, kind;
    int16_t counts[6];
    int32_t micro[6];
};
static struct rt_messagequeue records;
ALIGN(8) static uint8_t record_pool[RECORD_CAPACITY * (RT_ALIGN(sizeof(struct record), RT_ALIGN_SIZE) + sizeof(void *))];
static struct motion_filter filter;
static volatile struct motion_counters stats;
static struct rt_thread acq_thread, trace_thread;
ALIGN(8) static uint8_t acq_stack[BCA_STACK_MOTION];
ALIGN(8) static uint8_t trace_stack[BCA_STACK_TRACE];
static icm20608_device_t active_sensor;
static volatile enum motion_trace_mode trace_mode = MOTION_TRACE_OFF;
struct telemetry g_telemetry;

static rt_err_t read_burst(struct rt_i2c_bus_device *bus, uint8_t reg, uint8_t *out, uint16_t len)
{
    struct rt_i2c_msg messages[2];
    messages[0].addr=SENSOR_ADDRESS; messages[0].flags=RT_I2C_WR;
    messages[0].buf=&reg; messages[0].len=1;
    messages[1].addr=SENSOR_ADDRESS; messages[1].flags=RT_I2C_RD;
    messages[1].buf=out; messages[1].len=len;
    return rt_i2c_transfer(bus,messages,2)==2 ? RT_EOK : -RT_ERROR;
}
static int16_t signed_be(const uint8_t *p)
{
    uint16_t bits=(uint16_t)((uint16_t)p[0]*256u+p[1]);
    return bits>=32768u ? (int16_t)((int32_t)bits-65536) : (int16_t)bits;
}
static void queue_record(const struct record *r)
{
    /* Trace evidence is optional and off by default. The queue is never waited on. */
    if (trace_mode==MOTION_TRACE_OFF || (trace_mode==MOTION_TRACE_FILTERED && r->kind=='R')) return;
    if (rt_mq_send(&records,r,sizeof(*r))!=RT_EOK) {
        if (r->kind=='R') ++stats.dropped_raw; else ++stats.dropped_filtered;
    }
}
static uintptr_t telemetry_lock(void) { return (uintptr_t)rt_hw_interrupt_disable(); }
static void telemetry_unlock(uintptr_t state) { rt_hw_interrupt_enable((rt_base_t)state); }
static void acquisition(void *parameter)
{
    uint32_t sequence=0, export_sequence=0, epoch=0, previous_tick=rt_tick_get();
    uint64_t extended=previous_tick, last_fresh=0, started=extended;
    (void)parameter;
    motion_filter_reset(&filter);
    bca_status_set_sensor(SENSOR_WARMUP);
    for (;;) {
        uint8_t status=0, bytes[14];
        uint32_t tick=rt_tick_get(), read_start;
        uint64_t t_ms;
        int16_t raw[6];
        uint32_t axis;
        struct record r;
        struct motion_filtered out;
        extended=motion_extend_ticks(extended,previous_tick,tick); previous_tick=tick;
        if (rt_mutex_take(active_sensor->lock,RT_WAITING_NO)!=RT_EOK) { stats.fatal=1; break; }
        /* RT-Thread mutexes are recursive. Own the bus without waiting before
         * rt_i2c_transfer takes its recursive bus lock, avoiding FOREVER waits
         * behind another client. Both status and burst share this ownership. */
        if (rt_mutex_take(&active_sensor->i2c->lock,RT_WAITING_NO)!=RT_EOK) {
            rt_mutex_release(active_sensor->lock); stats.fatal=7; break;
        }
        if (read_register(active_sensor->i2c,0x3A,&status)!=RT_EOK) {
            rt_mutex_release(&active_sensor->i2c->lock); rt_mutex_release(active_sensor->lock); stats.fatal=2; break;
        }
        if ((status & 0x01u)==0) {
            rt_mutex_release(&active_sensor->i2c->lock); rt_mutex_release(active_sensor->lock);
            if (extended-(last_fresh ? last_fresh : started)>FRESH_TIMEOUT_MS) { stats.fatal=3; break; }
            rt_thread_delay(1); continue;
        }
        /* Timestamp is read-start, not an invented hardware conversion time. */
        tick=rt_tick_get(); extended=motion_extend_ticks(extended,previous_tick,tick); previous_tick=tick;
        t_ms=extended; read_start=tick;
        if (read_burst(active_sensor->i2c,0x3B,bytes,sizeof(bytes))!=RT_EOK) {
            rt_mutex_release(&active_sensor->i2c->lock); rt_mutex_release(active_sensor->lock); stats.fatal=4; break;
        }
        rt_mutex_release(&active_sensor->i2c->lock); rt_mutex_release(active_sensor->lock);
        tick=rt_tick_get();
        if ((uint32_t)(tick-read_start)>stats.max_read_ms) stats.max_read_ms=(uint32_t)(tick-read_start);
        if ((uint32_t)(tick-read_start)>=10u) ++stats.read_overruns;
        raw[0]=signed_be(&bytes[0]); raw[1]=signed_be(&bytes[2]); raw[2]=signed_be(&bytes[4]);
        raw[3]=signed_be(&bytes[8]); raw[4]=signed_be(&bytes[10]); raw[5]=signed_be(&bytes[12]);
        if (last_fresh && !motion_interval_valid(last_fresh,t_ms)) {
            uint64_t delta=t_ms-last_fresh;
            ++stats.irregular;
            if (delta>30u) stats.estimated_missed+=(uint32_t)((delta+10u)/20u-1u);
            motion_filter_reset(&filter); ++epoch; ++stats.filter_resets; bca_status_set_sensor(SENSOR_WARMUP);
        }
        last_fresh=t_ms;
        rt_memset(&r,0,sizeof(r)); r.kind='R'; r.t_ms=t_ms;
        r.sequence=sequence; r.source_sequence=sequence; r.filter_epoch=epoch;
        for (axis=0;axis<6;++axis) {
            r.counts[axis]=raw[axis];
            r.micro[axis]=axis<3 ? motion_accel_micro((int64_t)raw[axis]*65536LL)
                                       : motion_gyro_micro((int64_t)raw[axis]*65536LL);
            if (near_rail(raw[axis])) ++stats.rail;
        }
        ++stats.raw; queue_record(&r);
        if (motion_filter_push(&filter,raw,t_ms,sequence,&out)) {
            r.kind='F'; r.t_ms=out.t_ms; r.sequence=export_sequence;
            r.source_sequence=out.source_seq; r.filter_epoch=epoch;
            for (axis=0;axis<6;++axis)
                r.micro[axis]=axis<3 ? motion_accel_micro(out.counts_q16[axis]) : motion_gyro_micro(out.counts_q16[axis]);
            ++stats.filtered; queue_record(&r);
            telemetry_push(&g_telemetry,r.t_ms,r.micro,epoch);
            bca_status_set_sensor(SENSOR_RUNNING);
            if (export_sequence==0xFFFFFFFFu) { stats.fatal=5; break; }
            ++export_sequence;
        }
        if (sequence==0xFFFFFFFFu) { stats.fatal=5; break; }
        ++sequence;
        /* Leave CPU time for UART; resume short polling before the next
         * nominal 20 ms update. Freshness, timeout and timing checks still apply.
         */
        rt_thread_delay(15);
    }
    /* No serial output here. Consumer reports fatal and drains queued evidence. */
    bca_status_set_sensor(SENSOR_FAULT);
    g_bca_status.fatal=100u+stats.fatal;
}

void motion_counters_read(struct motion_counters *out)
{
    rt_base_t level=rt_hw_interrupt_disable();
    rt_memcpy(out,(const void *)&stats,sizeof(*out));
    rt_hw_interrupt_enable(level);
}

/* Same R/F line and #STAT formats as the verified recorder, so the offline FIR
 * comparison tools still apply. Runs only while a trace was requested.
 */
static void trace_output(void *parameter)
{
    struct record r;
    uint32_t last_report=rt_tick_get(), fatal_reported=0;
    (void)parameter;
    for (;;) {
        if (rt_mq_recv(&records,&r,sizeof(r),100)==RT_EOK) {
            char stamp[21], v[6][16]; uint32_t axis;
            format_u64(stamp,r.t_ms);
            for (axis=0;axis<6;++axis) format_micro(v[axis],r.micro[axis]);
            if (r.kind=='R')
                rt_kprintf("R,%s,%lu,%d,%d,%d,%d,%d,%d,%s,%s,%s,%s,%s,%s\n",stamp,(unsigned long)r.sequence,
                           (int)r.counts[0],(int)r.counts[1],(int)r.counts[2],(int)r.counts[3],(int)r.counts[4],(int)r.counts[5],v[0],v[1],v[2],v[3],v[4],v[5]);
            else
                rt_kprintf("F,%s,%lu,%lu,%lu,%s,%s,%s,%s,%s,%s\n",stamp,(unsigned long)r.sequence,
                           (unsigned long)r.source_sequence,(unsigned long)r.filter_epoch,v[0],v[1],v[2],v[3],v[4],v[5]);
        }
        if (trace_mode!=MOTION_TRACE_OFF && (uint32_t)(rt_tick_get()-last_report)>=1000u) {
            struct motion_counters s; motion_counters_read(&s);
            rt_kprintf("#SESSION,device_id=%s,boot_id=%s,firmware=%s\n",g_device_id,g_boot_id,BCA_FW_VERSION);
            rt_kprintf("#STAT,raw=%lu,filtered=%lu,drop_raw=%lu,drop_filtered=%lu,irregular=%lu,miss_est=%lu,read_overruns=%lu,max_read_ms=%lu,rail_axes=%lu,filter_resets=%lu\n",
                       (unsigned long)s.raw,(unsigned long)s.filtered,(unsigned long)s.dropped_raw,(unsigned long)s.dropped_filtered,
                       (unsigned long)s.irregular,(unsigned long)s.estimated_missed,(unsigned long)s.read_overruns,(unsigned long)s.max_read_ms,
                       (unsigned long)s.rail,(unsigned long)s.filter_resets);
            if (s.fatal && !fatal_reported) { rt_kprintf("#ERROR,acquisition_stopped,code=%lu\n",(unsigned long)s.fatal); fatal_reported=1; }
            last_report=rt_tick_get();
        }
    }
}

void motion_prepare(void)
{
    telemetry_init(&g_telemetry,telemetry_lock,telemetry_unlock);
}

rt_err_t motion_start(void)
{
    struct rt_i2c_bus_device *bus;
    bca_status_set_sensor(SENSOR_INIT);
    bus=rt_i2c_bus_device_find(SENSOR_BUS);
    if (!bus) { rt_kprintf("#ERROR,i2c_bus_missing\n"); goto failed; }
    rt_thread_mdelay(100);
    if (check_identity(bus,"before_reset")!=RT_EOK || probe_reset(bus)!=RT_EOK) goto failed;
    active_sensor=icm20608_init(SENSOR_BUS);
    if (!active_sensor) { rt_kprintf("#ERROR,driver_init_failed\n"); goto failed; }
    if (configure_sensor(bus)!=RT_EOK) { icm20608_deinit(active_sensor); rt_kprintf("#ERROR,configuration_failed\n"); goto failed; }
    rt_kprintf("#META,sensor_model=ICM-20608-G,address=0x68,who_am_i=0xAF,acceleration_unit=g,gyro_unit=rad/s,accel_range_g=8,gyro_range_dps=1000\n");
    rt_kprintf("#META,frame_id=sensor_native_unmapped,calibration_id=none,gravity_handling=included,hardware_validated=false\n");
    rt_kprintf("#META,raw_hz=50,export_hz=5,odr_hz=50,gyro_lpf_hz=10,accel_lpf_hz=10.2,filter=%s,taps=201,delay_samples=100,warmup_samples=201\n",MOTION_FILTER_ID);
    rt_kprintf("#META,timestamp_unit=ms,timestamp_source=device_monotonic,raw_timestamp=read_start,filtered_timestamp=center_raw_read_start,freshness=data_ready_status\n");
    rt_kprintf("#FORMAT,R,t_ms,raw_seq,ax_count,ay_count,az_count,gx_count,gy_count,gz_count,acceleration_x,acceleration_y,acceleration_z,gyro_x,gyro_y,gyro_z\n");
    rt_kprintf("#FORMAT,F,t_ms,export_seq,center_raw_seq,filter_epoch,acceleration_x,acceleration_y,acceleration_z,gyro_x,gyro_y,gyro_z\n");
    if (rt_mq_init(&records,"samples",record_pool,sizeof(struct record),sizeof(record_pool),RT_IPC_FLAG_FIFO)!=RT_EOK) { rt_kprintf("#ERROR,queue_init_failed\n"); goto failed; }
    if (rt_thread_init(&trace_thread,"recorder",trace_output,RT_NULL,trace_stack,sizeof(trace_stack),BCA_PRIO_TRACE,10)!=RT_EOK ||
        rt_thread_init(&acq_thread,"motion50",acquisition,RT_NULL,acq_stack,sizeof(acq_stack),ACQ_PRIORITY,1)!=RT_EOK) {
        rt_kprintf("#ERROR,thread_init_failed\n"); goto failed;
    }
    if (rt_thread_startup(&trace_thread)!=RT_EOK) { rt_kprintf("#ERROR,serial_thread_start_failed\n"); goto failed; }
    if (rt_thread_startup(&acq_thread)!=RT_EOK) { stats.fatal=6; goto failed; }
    return RT_EOK;
failed:
    bca_status_set_sensor(SENSOR_FAULT);
    return -RT_ERROR;
}

static void motion_trace(int argc, char **argv)
{
    if (argc==2 && rt_strcmp(argv[1],"off")==0) trace_mode=MOTION_TRACE_OFF;
    else if (argc==2 && rt_strcmp(argv[1],"filtered")==0) trace_mode=MOTION_TRACE_FILTERED;
    else if (argc==2 && rt_strcmp(argv[1],"raw")==0) trace_mode=MOTION_TRACE_RAW;
    else rt_kprintf("usage: motion_trace off|filtered|raw\n");
}
MSH_CMD_EXPORT(motion_trace, Print verified R/F sensor lines on the console);
