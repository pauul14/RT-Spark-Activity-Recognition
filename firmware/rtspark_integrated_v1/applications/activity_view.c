#include "activity_view.h"
#include <string.h>

#define RGB(r,g,b) ((uint16_t)((((r) >> 3) << 11) | (((g) >> 2) << 5) | ((b) >> 3)))
#define BACK RGB(8,11,17)
#define INK RGB(246,248,252)
#define MUTED RGB(155,171,191)
#define RING_Y 98
#define RING_LIMIT 61
#define FIRST_CHANGED_ROW (RING_Y - RING_LIMIT)
#define LAST_CHANGED_ROW 218

struct mask { unsigned width, height; const uint8_t *bits; };
#include "activity_art.h"

struct style {
    const struct mask *icon, *title, *hint;
    uint16_t accent, halo, well;
};

static const struct style styles[] = {
    [ACTIVITY_OFFLINE] = {&icon_offline, &word_offline, &hint_offline,
        RGB(155,171,191), RGB(36,45,57), RGB(17,23,32)},
    [ACTIVITY_CONNECTING] = {&icon_connecting, &word_connecting, &hint_connecting,
        RGB(193,167,255), RGB(48,39,68), RGB(27,22,39)},
    [ACTIVITY_WAITING] = {&icon_waiting, &word_waiting, &hint_waiting,
        RGB(255,210,119), RGB(61,49,31), RGB(33,27,20)},
    [ACTIVITY_NO_PREDICTION] = {&icon_invalid, &word_invalid, &hint_invalid,
        RGB(255,210,119), RGB(61,49,31), RGB(33,27,20)},
    [ACTIVITY_WALKING] = {&icon_walking, &word_walking, 0,
        RGB(88,174,255), RGB(23,53,82), RGB(14,29,48)},
    [ACTIVITY_RUNNING] = {&icon_running, &word_running, 0,
        RGB(255,133,86), RGB(76,43,33), RGB(42,25,23)},
    [ACTIVITY_STATIONARY] = {&icon_stationary, &word_stationary, 0,
        RGB(83,225,165), RGB(26,63,51), RGB(15,35,32)},
};

enum activity_screen activity_view_select(const struct bca_status *status)
{
    if (status->fatal || status->sensor == SENSOR_FAULT)
        return ACTIVITY_OFFLINE;
    if (status->wifi == WIFI_READY && status->mqtt == MQTT_ONLINE) {
        const struct pred_state *prediction = &status->prediction;
        if (!pred_display_is_real(prediction, status->now_ms)) {
            /* Reuse the protocol's freshness calculation for unvalidated payloads. */
            return strcmp(pred_display_text(prediction, status->now_ms, 1), "UNVALIDATED") == 0
                ? ACTIVITY_NO_PREDICTION : ACTIVITY_WAITING;
        }
        switch (prediction->activity) {
        case PRED_ACTIVITY_WALKING: return ACTIVITY_WALKING;
        case PRED_ACTIVITY_RUNNING: return ACTIVITY_RUNNING;
        case PRED_ACTIVITY_STATIONARY: return ACTIVITY_STATIONARY;
        default: return ACTIVITY_NO_PREDICTION;
        }
    }
    if (status->wifi == WIFI_INIT || status->wifi == WIFI_CONNECTING || status->wifi == WIFI_DHCP ||
        (status->wifi == WIFI_READY && (status->mqtt == MQTT_WAIT_TIME || status->mqtt == MQTT_CONNECTING)))
        return ACTIVITY_CONNECTING;
    return ACTIVITY_OFFLINE;
}

static void mask_row(uint16_t *row, unsigned origin, unsigned y,
                     const struct mask *mask, unsigned top, uint16_t ink)
{
    if (!mask || y < top || y - top >= mask->height) return;
    const uint8_t *bits = mask->bits + (y - top) * ((mask->width + 7) / 8);
    unsigned start = (ACTIVITY_LCD_SIZE - mask->width) / 2 - origin;
    for (unsigned x = 0; x < mask->width; ++x) {
        if (bits[x / 8] & (0x80u >> (x % 8))) row[start + x] = ink;
    }
}

static void ring_row(uint16_t *row, unsigned origin, unsigned y, const struct style *style)
{
    int dy = (int)y - RING_Y;
    if (dy < -RING_LIMIT || dy > RING_LIMIT) return;
    for (int dx = -RING_LIMIT; dx <= RING_LIMIT; ++dx) {
        int r2 = dx * dx + dy * dy;
        if (r2 > RING_LIMIT * RING_LIMIT) continue;
        uint16_t color = style->halo;
        /* Full, fixed rings. Radius and brightness carry no measurement. */
        if (r2 <= 55 * 55 && r2 >= 50 * 50) color = style->accent;
        else if (r2 < 50 * 50) color = style->well;
        else if (r2 < 60 * 60) color = BACK;
        row[(int)(ACTIVITY_LCD_SIZE / 2 - origin) + dx] = color;
    }
    mask_row(row, origin, y, style->icon, RING_Y - 32, style->accent);
}

void activity_view_render(struct activity_view *view, enum activity_screen screen,
                          activity_row_sink send, void *context)
{
    if (screen == view->shown) return;
    int first = view->shown == ACTIVITY_BOOT;
    unsigned origin = first ? 0 : ACTIVITY_LCD_INSET;
    unsigned width = first ? ACTIVITY_LCD_SIZE : ACTIVITY_LCD_WIDTH;
    unsigned start = first ? 0 : FIRST_CHANGED_ROW;
    unsigned end = first ? ACTIVITY_LCD_SIZE : LAST_CHANGED_ROW + 1;
    const struct style *style = &styles[screen];

    for (unsigned y = start; y < end; ++y) {
        for (unsigned x = 0; x < width; ++x) view->row[x] = BACK;
        if (first) mask_row(view->row, origin, y, &word_brand, 13, MUTED);
        ring_row(view->row, origin, y, style);
        mask_row(view->row, origin, y, style->title, 161, INK);
        mask_row(view->row, origin, y, style->hint, 202, style->accent);
        send(context, origin, y, width, view->row);
    }
    view->shown = screen;
}
