/*
 * display_stub.h - LVGL-ready display interface with a no-op backend.
 *
 * What is real: the interface, the task/ownership contract, and the waveform
 * downsampling maths (min/max column envelope, the standard way to draw a
 * 8,000-sample trace into a few hundred pixels without aliasing).
 *
 * What is stubbed: there is no LCD, no LVGL and no framebuffer driver. The
 * default backend computes the envelope and logs it at DEBUG level, which is
 * enough to prove the data path delivers a full 2 s window on time.
 *
 * Why a port struct instead of calling LVGL directly:
 *   - The board and panel are not chosen yet (ADR-006/ADR-008: display is a
 *     host-side requirement, panel selection is still open). Linking LVGL now
 *     would freeze an API and a build dependency for hardware that does not
 *     exist.
 *   - A function-pointer port keeps this file compilable on any target and lets
 *     the LVGL backend be dropped in without touching app_main.c.
 *   - Cost: one indirect call per frame (~ns) and one extra struct. Negligible
 *     against a 1 Hz render rate.
 * Fallback: if a static backend is preferred later, replace the port with
 * #ifdef-selected direct calls; the public functions below stay the same.
 */
#ifndef DISPLAY_STUB_H
#define DISPLAY_STUB_H

#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Waveform is drawn as this many min/max columns; a typical 320 px panel. */
#define DISPLAY_STUB_WAVE_COLUMNS 320u

/*
 * Backend vtable. All callbacks are optional (NULL is allowed).
 * ctx is passed back verbatim, so an LVGL backend can carry its canvas object.
 */
typedef struct {
    esp_err_t (*init)(void *ctx, uint16_t width, uint16_t height);
    /* Draw one waveform window; samples are 4 kHz int16, oldest first. */
    esp_err_t (*render_waveform)(void *ctx,
                                 const int16_t *samples,
                                 size_t sample_count);
    /* Draw a short status/murmur string at a pixel position. */
    esp_err_t (*render_text)(void *ctx, int16_t x, int16_t y, const char *text);
    void *ctx;
} display_port_t;

/* Installs the stub backend (default). */
esp_err_t display_stub_init(void);

/*
 * Replace the backend. Pass NULL to go back to the stub. The port struct is
 * copied, so it may live on the caller's stack if the context outlives it.
 */
esp_err_t display_stub_set_port(const display_port_t *port);

/*
 * Render one waveform window. Never blocks on hardware in the stub backend.
 * Returns ESP_ERR_INVALID_ARG if samples is NULL or sample_count is 0, or the
 * backend's error code.
 */
esp_err_t display_stub_render_waveform(const int16_t *samples,
                                       size_t sample_count);

/* Render a status line ("HR 72", "murmur: n/a", ...). */
esp_err_t display_stub_render_text(int16_t x, int16_t y, const char *text);

/* Number of windows handed to the display since boot (diagnostics). */
uint32_t display_stub_windows_rendered(void);

#ifdef __cplusplus
}
#endif

#endif /* DISPLAY_STUB_H */
