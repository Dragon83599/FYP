#include "display_stub.h"

#include <string.h>

#include "esp_log.h"

static const char *TAG = "display_stub";

/* Column envelope of the most recent window: what an LVGL backend would blit. */
static int16_t s_column_min[DISPLAY_STUB_WAVE_COLUMNS];
static int16_t s_column_max[DISPLAY_STUB_WAVE_COLUMNS];
static size_t s_columns_used;
static uint32_t s_windows_rendered;

static esp_err_t stub_init(void *ctx, uint16_t width, uint16_t height)
{
    (void)ctx;
    ESP_LOGI(TAG,
             "display backend = stub (no panel, no LVGL). Target area %ux%u, %u waveform columns",
             (unsigned)width, (unsigned)height,
             (unsigned)DISPLAY_STUB_WAVE_COLUMNS);
    return ESP_OK;
}

/*
 * Min/max decimation: one (min, max) pair per screen column. This is the
 * correct way to draw a high-rate trace on a narrow display - picking every
 * Nth sample would alias spikes away, and averaging would hide the amplitude
 * of the murmur that the display exists to show.
 *
 * Complexity: one pass over the window, O(sample_count). 8,000 samples at 1 Hz
 * is trivial even on one core.
 */
static esp_err_t stub_render_waveform(void *ctx,
                                      const int16_t *samples,
                                      size_t sample_count)
{
    (void)ctx;

    size_t columns = DISPLAY_STUB_WAVE_COLUMNS;
    if (sample_count < columns) {
        columns = sample_count;
    }
    if (columns == 0u) {
        return ESP_ERR_INVALID_ARG;
    }

    int16_t overall_min = 32767;
    int16_t overall_max = -32768;

    for (size_t column = 0; column < columns; column++) {
        size_t first = (column * sample_count) / columns;
        size_t last = ((column + 1u) * sample_count) / columns;
        if (last <= first) {
            last = first + 1u;
        }
        if (last > sample_count) {
            last = sample_count;
        }

        int16_t column_min = 32767;
        int16_t column_max = -32768;
        for (size_t i = first; i < last; i++) {
            int16_t value = samples[i];
            if (value < column_min) {
                column_min = value;
            }
            if (value > column_max) {
                column_max = value;
            }
        }
        s_column_min[column] = column_min;
        s_column_max[column] = column_max;
        if (column_min < overall_min) {
            overall_min = column_min;
        }
        if (column_max > overall_max) {
            overall_max = column_max;
        }
    }

    s_columns_used = columns;
    s_windows_rendered++;

    /*
     * The stub has no panel, so the only observable effect is this log line and
     * the column arrays above (kept so a debugger or a future LVGL backend can
     * inspect the last frame). DEBUG level: one line per second is too chatty
     * for the default INFO console.
     */
    ESP_LOGD(TAG,
             "waveform: %u samples -> %u columns, peak-to-peak %d, total windows %u",
             (unsigned)sample_count, (unsigned)columns,
             (int)overall_max - (int)overall_min,
             (unsigned)s_windows_rendered);
    return ESP_OK;
}

static esp_err_t stub_render_text(void *ctx, int16_t x, int16_t y, const char *text)
{
    (void)ctx;
    ESP_LOGD(TAG, "text @(%d,%d): %s", (int)x, (int)y,
             (text != NULL) ? text : "(null)");
    return ESP_OK;
}

static display_port_t s_port = {
    .init = stub_init,
    .render_waveform = stub_render_waveform,
    .render_text = stub_render_text,
    .ctx = NULL,
};

esp_err_t display_stub_init(void)
{
    if (s_port.init != NULL) {
        esp_err_t err = s_port.init(s_port.ctx, DISPLAY_STUB_WAVE_COLUMNS, 240);
        if (err != ESP_OK) {
            return err;
        }
    }
    return ESP_OK;
}

esp_err_t display_stub_set_port(const display_port_t *port)
{
    if (port == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    s_port = *port;
    /* A newly installed backend must be initialised by its owner before use. */
    if (s_port.init != NULL) {
        return s_port.init(s_port.ctx, DISPLAY_STUB_WAVE_COLUMNS, 240);
    }
    return ESP_OK;
}

esp_err_t display_stub_render_waveform(const int16_t *samples, size_t sample_count)
{
    if (samples == NULL || sample_count == 0u) {
        return ESP_ERR_INVALID_ARG;
    }
    if (s_port.render_waveform == NULL) {
        return ESP_ERR_NOT_SUPPORTED;
    }
    return s_port.render_waveform(s_port.ctx, samples, sample_count);
}

esp_err_t display_stub_render_text(int16_t x, int16_t y, const char *text)
{
    if (s_port.render_text == NULL) {
        return ESP_ERR_NOT_SUPPORTED;
    }
    return s_port.render_text(s_port.ctx, x, y, text);
}

uint32_t display_stub_windows_rendered(void)
{
    return s_windows_rendered;
}
