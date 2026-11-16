#ifndef ACTIVITY_VIEW_H
#define ACTIVITY_VIEW_H

#include "app_status.h"

#define ACTIVITY_LCD_SIZE 240u
#define ACTIVITY_LCD_INSET 8u
#define ACTIVITY_LCD_WIDTH (ACTIVITY_LCD_SIZE - 2u * ACTIVITY_LCD_INSET)

enum activity_screen {
    ACTIVITY_BOOT, ACTIVITY_OFFLINE, ACTIVITY_CONNECTING, ACTIVITY_WAITING,
    ACTIVITY_NO_PREDICTION, ACTIVITY_WALKING, ACTIVITY_RUNNING, ACTIVITY_STATIONARY
};

struct activity_view {
    enum activity_screen shown;
    uint16_t row[ACTIVITY_LCD_SIZE];
};

/* The sink consumes the RGB565 span synchronously. No heap or framebuffer. */
typedef void (*activity_row_sink)(void *, unsigned x, unsigned y, unsigned width, uint16_t *);
enum activity_screen activity_view_select(const struct bca_status *status);
void activity_view_render(struct activity_view *, enum activity_screen, activity_row_sink, void *);

#endif