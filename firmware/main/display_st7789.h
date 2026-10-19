/*
 * display_st7789.h - minimal ST7789 panel path for the PCG stethoscope host.
 *
 * This is the *hardware* display path. display_stub.h stays the UI data path
 * (waveform envelope, status lines); this module only knows how to put pixels
 * on a 320x240 RGB565 panel through the ST7789 driver that ships with ESP-IDF.
 *
 * Design constraints (ADR-019 and docs/iram-and-display-budget.md):
 *   - no full framebuffer: one 320 x 16 RGB565 strip buffer (10,240 B) is
 *     composed and pushed band by band, so the static .bss cost of the whole
 *     module stays under 16 KB;
 *   - no heap allocation after display_init() returns (ESP-IDF allocates the
 *     SPI bus, panel IO and panel objects once, inside display_init());
 *   - every draw call returns immediately while the panel is not initialised,
 *     so a board without a panel boots and runs exactly as before.
 *
 * Threading: all draw functions must be called from one task. A draw call
 * blocks until the strip buffer has been copied out by SPI DMA, so it is safe
 * to reuse the single static strip buffer. app_main's ai_display task is the
 * intended owner.
 */
#ifndef DISPLAY_ST7789_H
#define DISPLAY_ST7789_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

/*
 * Compile-time switch. app_main.c guards the display_init() call with this
 * macro; set it to 0 (project-wide, e.g. -DFYP_ENABLE_DISPLAY=0) to build the
 * firmware exactly as it was before the panel path existed. There is no
 * Kconfig entry on purpose: a single #define keeps the build reproducible from
 * the source tree alone.
 *
 * With the switch at 0 nothing references this module, so the linker drops it
 * (and the ESP-IDF SPI/panel driver it pulls in): the image and RAM return to
 * the pre-display values. Verified by building the same tree with the switch
 * at 0: image 512,399 B, DIRAM 169,979/341,760 B, i.e. identical to the
 * baseline in docs/iram-and-display-budget.md.
 */
#ifndef FYP_ENABLE_DISPLAY
#define FYP_ENABLE_DISPLAY 1
#endif

/* Panel geometry, in the rotated landscape orientation. */
#define DISPLAY_H_RES 320
#define DISPLAY_V_RES 240

/* Band size of the static strip buffer: 320 x 16 x 2 B = 10,240 B. */
#define DISPLAY_STRIP_ROWS 16
#define DISPLAY_STRIP_PIXELS (DISPLAY_H_RES * DISPLAY_STRIP_ROWS)
#define DISPLAY_STRIP_BYTES (DISPLAY_STRIP_PIXELS * 2)

/*
 * Waveform area: rows 48..239. The first three 16-pixel bands (0..47) are
 * reserved for the text lines at y = 8, 24 and 40, which is where app_main's
 * status lines already live.
 */
#define DISPLAY_WAVE_Y 48
#define DISPLAY_WAVE_HEIGHT (DISPLAY_V_RES - DISPLAY_WAVE_Y)

/* 5x7 font metrics: 5 columns + 1 blank column between characters. */
#define DISPLAY_FONT_WIDTH 5
#define DISPLAY_FONT_HEIGHT 7
#define DISPLAY_FONT_CELL_WIDTH 6

/* Common RGB565 colours (the panel colour order is a placeholder, see .c). */
#define DISPLAY_COLOUR_BLACK 0x0000
#define DISPLAY_COLOUR_WHITE 0xFFFF
#define DISPLAY_COLOUR_RED 0xF800
#define DISPLAY_COLOUR_GREEN 0x07E0
#define DISPLAY_COLOUR_BLUE 0x001F
#define DISPLAY_COLOUR_CYAN 0x07FF
#define DISPLAY_COLOUR_YELLOW 0xFFE0

/*
 * Initialise the SPI bus, panel IO and ST7789 panel:
 * reset -> init -> invert/mirror/swap -> gap -> display on -> backlight on.
 *
 * Returns ESP_OK on success. On failure the error is logged and returned; the
 * caller (app_main) logs a warning and keeps booting. Calling it again after a
 * successful init is a no-op; after a failed init the sequence is retried from
 * scratch (the resources of the failed attempt are released first).
 */
esp_err_t display_init(void);

/*
 * True once display_init() completed and the host-side panel path is ready.
 * This cannot prove that a panel is physically connected: SPI writes complete
 * even with nothing on the bus, so only a visible test proves presence.
 */
bool display_is_initialised(void);

/* Alias kept for call sites that read better as "is the display ready?". */
static inline bool display_ready(void)
{
    return display_is_initialised();
}

/*
 * Fill the whole panel with one RGB565 colour. No-op while uninitialised.
 * Cost: 15 strip pushes, 153,600 B over SPI (about 31 ms at 40 MHz).
 */
void display_clear(uint16_t rgb565);

/*
 * Draw one waveform window as a polyline in the waveform area.
 *
 * samples: 4 kHz int16 samples, oldest first (app_main's 2 s AI window).
 * n:       sample count; 0 or a NULL pointer is a no-op.
 * colour:  RGB565 trace colour.
 *
 * The window is decimated to 320 columns. Each column keeps the sample with
 * the largest absolute value (not every Nth sample: that would alias narrow
 * murmurs away), and consecutive columns are joined by a vertical line, which
 * is the polyline rasterised at a one-pixel column pitch. The vertical scale
 * is auto-ranged to the window peak; a floor avoids magnifying idle noise.
 * The waveform area is cleared to black first; the text area is untouched.
 */
void display_draw_waveform(const int16_t *samples, size_t n, uint16_t colour);

/*
 * Draw a NUL-terminated string with the built-in 5x7 font.
 *
 * x, y:   top-left pixel of the first glyph. Out-of-range positions are a
 *         no-op; a string that runs past the right edge is clipped.
 * fg, bg: RGB565 text and background colours.
 *
 * The text rectangle (from x to the right edge, the full glyph height, one
 * 16-row band at a time) is filled with bg first, so redrawing a shorter
 * status line leaves no remnants. That also means two strings placed on the
 * same 16-row band and overlapping in x will erase each other; app_main puts
 * each line on its own band.
 */
void display_draw_text(int x, int y, const char *text, uint16_t fg, uint16_t bg);

#ifdef __cplusplus
}
#endif

#endif /* DISPLAY_ST7789_H */
