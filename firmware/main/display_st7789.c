/*
 * display_st7789.c - minimal ST7789 display path for the PCG stethoscope host.
 *
 * What this file is:
 *   Glue between app_main's 2 s waveform window and the ST7789 panel driver
 *   that is built into ESP-IDF (esp_lcd_new_panel_st7789()). There is no panel
 *   driver here and no framebuffer: one 320 x 16 RGB565 strip buffer is filled
 *   and pushed band by band with esp_lcd_panel_draw_bitmap().
 *
 * What this file is not:
 *   A UI. display_stub.c remains the UI data path (min/max envelope, status
 *   lines); this module only rasterises pixels when a panel is present. The
 *   two are deliberately independent so a board without a panel still runs
 *   the BLE/pipeline/heart-rate firmware unchanged.
 *
 * Memory:
 *   s_strip          320 * 16 * 2 = 10,240 B  (.bss)
 *   s_wave_y         320 * 1       =    320 B  (.bss)
 *   s_font5x7        95 * 5        =    475 B  (.rodata, flash)
 *   handles/flags/                   ~ 24 B  (.bss)
 *   Total static RAM ~10.6 KB, under the 16 KB display budget. The only heap
 *   use is inside display_init() (SPI bus, panel IO, ST7789 object and one
 *   FreeRTOS binary semaphore); no draw call allocates.
 *
 * Hardware status:
 *   UNVERIFIED. There is no panel on the bench yet. Every pin, the SPI host,
 *   the pixel clock, the colour order, the mirror/swap combination and the
 *   panel offsets below are PLACEHOLDERS that must be checked against the real
 *   board. See the "PLACEHOLDERS" block.
 *
 * Hardware bring-up checklist (none of this is testable in the current
 * hardware-less milestone):
 *   1. flash + monitor: "ST7789 ... ready" appears and boot continues even if
 *      the panel is missing (display_init must never abort the boot);
 *   2. the panel is black after boot: clear + backlight on work;
 *   3. draw one test string: orientation (swap/mirror), colour order (RGB vs
 *      BGR, invert) and the visible-area offsets are correct;
 *   4. measure one full-screen clear (15 bands) and one waveform frame: the
 *      budget assumes ~31 ms for 153,600 B at 40 MHz, i.e. >= 10 fps;
 *   5. watch the waveform redraw for tearing: one strip is shared by every
 *      band, so a future double-buffered variant may be needed if it shows;
 *   6. confirm the backlight polarity/level and the actual current draw.
 */
#include "display_st7789.h"

#include <string.h>

#include "driver/gpio.h"
#include "driver/spi_master.h"
#include "esp_attr.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_ops.h"
#include "esp_lcd_panel_st7789.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

static const char *TAG = "display_st7789";

/*
 * ============================================================================
 * PLACEHOLDERS - confirm every value in this block against the actual board.
 * ============================================================================
 *
 * The pin numbers are a safe-looking default for an ESP32-S3 devkit: they are
 * not strapping pins (0/3/45/46), not the USB pins (19/20), not the UART
 * console pins (43/44) and not the flash/PSRAM pins (26..37 on WROOM-1
 * N16R8). They are NOT taken from a schematic. If the board wires the panel
 * differently, change them here only.
 *
 * SPI2_HOST is the general-purpose SPI peripheral (SPI0/SPI1 are used by the
 * flash/PSRAM cache). 40 MHz is the ST7789 datasheet write-cycle limit for
 * most modules at 3.3 V; drop to 20 MHz if the first bring-up shows corruption
 * or the module uses long jumper wires.
 */
#define DISPLAY_SPI_HOST SPI2_HOST
#define DISPLAY_PIN_SCLK 12
#define DISPLAY_PIN_MOSI 11
#define DISPLAY_PIN_CS 10
#define DISPLAY_PIN_DC 9
#define DISPLAY_PIN_RST 14
#define DISPLAY_PIN_BL 13
#define DISPLAY_BL_ACTIVE_HIGH 1

#define DISPLAY_PIXEL_CLOCK_HZ (40 * 1000 * 1000)
#define DISPLAY_CMD_BITS 8
#define DISPLAY_PARAM_BITS 8
#define DISPLAY_SPI_MODE 0
#define DISPLAY_TRANS_QUEUE_DEPTH 1

/*
 * Colour order: most ST7789 modules are BGR, some are RGB. If red and blue
 * are exchanged, flip this to LCD_RGB_ELEMENT_ORDER_RGB. The strip buffer is
 * written in MSB-first wire order (see display_wire_rgb565()), which matches
 * LCD_RGB_DATA_ENDIAN_BIG; do not change only one of the two.
 */
#define DISPLAY_RGB_ELE_ORDER LCD_RGB_ELEMENT_ORDER_BGR
#define DISPLAY_DATA_ENDIAN LCD_RGB_DATA_ENDIAN_BIG

/*
 * Panel orientation and colour inversion. A 240 x 320 ST7789 is rotated to
 * 320 x 240 landscape with swap_xy; mirror_x/mirror_y depend on how the
 * module is mounted. IPS panels usually need invert_color = true, TN panels
 * usually false. Confirm all four on the real panel.
 */
#define DISPLAY_SWAP_XY 1
#define DISPLAY_MIRROR_X 0
#define DISPLAY_MIRROR_Y 0
#define DISPLAY_INVERT_COLOR 1

/*
 * Visible-area offsets inside the controller RAM, in the rotated coordinate
 * system. 0/0 is correct for a full 240 x 320 (or 320 x 240) ST7789; small
 * panels (135 x 240, 240 x 240) need non-zero gaps.
 */
#define DISPLAY_X_GAP 0
#define DISPLAY_Y_GAP 0

/* Colours used internally: the waveform area is cleared to black per frame. */
#define DISPLAY_WAVE_BG_COLOUR DISPLAY_COLOUR_BLACK

/*
 * Auto-range floor. Peak-to-peak values below this are treated as silence so
 * idle noise is not stretched to full screen height. 256 counts of the int16
 * sample range is ~0.8 % full scale; a real PCG window is far above it.
 */
#define DISPLAY_WAVE_MIN_PEAK 256

/*
 * Upper bound on how long one strip push may take before the code gives up
 * waiting for the DMA-complete interrupt. 320 x 16 x 2 B at 40 MHz is ~2 ms;
 * 100 ms leaves a very wide margin without hanging a boot.
 */
#define DISPLAY_FLUSH_TIMEOUT_MS 100

/* ------------------------------------------------------------------------- */
/* Static storage                                                            */
/* ------------------------------------------------------------------------- */

/*
 * The strip buffer is filled with RGB565 pixels already byte-swapped to the
 * order the panel expects on the wire, then handed to SPI DMA. 4-byte aligned
 * so the SPI DMA engine can read it directly.
 */
static uint16_t s_strip[DISPLAY_STRIP_PIXELS] __attribute__((aligned(4)));

/* One y coordinate per display column for the polyline, filled per frame. */
static uint8_t s_wave_y[DISPLAY_H_RES];

/*
 * A 5x7 font, ASCII 0x20..0x7E, five column bytes per glyph, bit 0 = top row.
 * Classic public-domain 5x7 glyph shapes; kept in .rodata (flash), not RAM.
 */
static const uint8_t s_font5x7[95 * 5] = {
    0x00, 0x00, 0x00, 0x00, 0x00, /* 0x20 ' ' */
    0x00, 0x00, 0x5F, 0x00, 0x00, /* 0x21 '!' */
    0x00, 0x07, 0x00, 0x07, 0x00, /* 0x22 '"' */
    0x14, 0x7F, 0x14, 0x7F, 0x14, /* 0x23 '#' */
    0x24, 0x2A, 0x7F, 0x2A, 0x12, /* 0x24 '$' */
    0x23, 0x13, 0x08, 0x64, 0x62, /* 0x25 '%' */
    0x36, 0x49, 0x55, 0x22, 0x50, /* 0x26 '&' */
    0x00, 0x05, 0x03, 0x00, 0x00, /* 0x27 ''' */
    0x00, 0x1C, 0x22, 0x41, 0x00, /* 0x28 '(' */
    0x00, 0x41, 0x22, 0x1C, 0x00, /* 0x29 ')' */
    0x14, 0x08, 0x3E, 0x08, 0x14, /* 0x2A '*' */
    0x08, 0x08, 0x3E, 0x08, 0x08, /* 0x2B '+' */
    0x00, 0x50, 0x30, 0x00, 0x00, /* 0x2C ',' */
    0x08, 0x08, 0x08, 0x08, 0x08, /* 0x2D '-' */
    0x00, 0x60, 0x60, 0x00, 0x00, /* 0x2E '.' */
    0x20, 0x10, 0x08, 0x04, 0x02, /* 0x2F '/' */
    0x3E, 0x51, 0x49, 0x45, 0x3E, /* 0x30 '0' */
    0x00, 0x42, 0x7F, 0x40, 0x00, /* 0x31 '1' */
    0x42, 0x61, 0x51, 0x49, 0x46, /* 0x32 '2' */
    0x21, 0x41, 0x45, 0x4B, 0x31, /* 0x33 '3' */
    0x18, 0x14, 0x12, 0x7F, 0x10, /* 0x34 '4' */
    0x27, 0x45, 0x45, 0x45, 0x39, /* 0x35 '5' */
    0x3C, 0x4A, 0x49, 0x49, 0x30, /* 0x36 '6' */
    0x01, 0x71, 0x09, 0x05, 0x03, /* 0x37 '7' */
    0x36, 0x49, 0x49, 0x49, 0x36, /* 0x38 '8' */
    0x06, 0x49, 0x49, 0x29, 0x1E, /* 0x39 '9' */
    0x00, 0x36, 0x36, 0x00, 0x00, /* 0x3A ':' */
    0x00, 0x56, 0x36, 0x00, 0x00, /* 0x3B ';' */
    0x08, 0x14, 0x22, 0x41, 0x00, /* 0x3C '<' */
    0x14, 0x14, 0x14, 0x14, 0x14, /* 0x3D '=' */
    0x00, 0x41, 0x22, 0x14, 0x08, /* 0x3E '>' */
    0x02, 0x01, 0x51, 0x09, 0x06, /* 0x3F '?' */
    0x32, 0x49, 0x79, 0x41, 0x3E, /* 0x40 '@' */
    0x7E, 0x11, 0x11, 0x11, 0x7E, /* 0x41 'A' */
    0x7F, 0x49, 0x49, 0x49, 0x36, /* 0x42 'B' */
    0x3E, 0x41, 0x41, 0x41, 0x22, /* 0x43 'C' */
    0x7F, 0x41, 0x41, 0x22, 0x1C, /* 0x44 'D' */
    0x7F, 0x49, 0x49, 0x49, 0x41, /* 0x45 'E' */
    0x7F, 0x09, 0x09, 0x01, 0x01, /* 0x46 'F' */
    0x3E, 0x41, 0x49, 0x49, 0x7A, /* 0x47 'G' */
    0x7F, 0x08, 0x08, 0x08, 0x7F, /* 0x48 'H' */
    0x00, 0x41, 0x7F, 0x41, 0x00, /* 0x49 'I' */
    0x20, 0x40, 0x41, 0x3F, 0x01, /* 0x4A 'J' */
    0x7F, 0x08, 0x14, 0x22, 0x41, /* 0x4B 'K' */
    0x7F, 0x40, 0x40, 0x40, 0x40, /* 0x4C 'L' */
    0x7F, 0x02, 0x0C, 0x02, 0x7F, /* 0x4D 'M' */
    0x7F, 0x04, 0x08, 0x10, 0x7F, /* 0x4E 'N' */
    0x3E, 0x41, 0x41, 0x41, 0x3E, /* 0x4F 'O' */
    0x7F, 0x09, 0x09, 0x09, 0x06, /* 0x50 'P' */
    0x3E, 0x41, 0x51, 0x21, 0x5E, /* 0x51 'Q' */
    0x7F, 0x09, 0x19, 0x29, 0x46, /* 0x52 'R' */
    0x46, 0x49, 0x49, 0x49, 0x31, /* 0x53 'S' */
    0x01, 0x01, 0x7F, 0x01, 0x01, /* 0x54 'T' */
    0x3F, 0x40, 0x40, 0x40, 0x3F, /* 0x55 'U' */
    0x1F, 0x20, 0x40, 0x20, 0x1F, /* 0x56 'V' */
    0x7F, 0x20, 0x18, 0x20, 0x7F, /* 0x57 'W' */
    0x63, 0x14, 0x08, 0x14, 0x63, /* 0x58 'X' */
    0x03, 0x04, 0x78, 0x04, 0x03, /* 0x59 'Y' */
    0x61, 0x51, 0x49, 0x45, 0x43, /* 0x5A 'Z' */
    0x00, 0x00, 0x7F, 0x41, 0x41, /* 0x5B '[' */
    0x02, 0x04, 0x08, 0x10, 0x20, /* 0x5C '\' */
    0x41, 0x41, 0x7F, 0x00, 0x00, /* 0x5D ']' */
    0x04, 0x02, 0x01, 0x02, 0x04, /* 0x5E '^' */
    0x40, 0x40, 0x40, 0x40, 0x40, /* 0x5F '_' */
    0x00, 0x01, 0x02, 0x04, 0x00, /* 0x60 '`' */
    0x20, 0x54, 0x54, 0x54, 0x78, /* 0x61 'a' */
    0x7F, 0x48, 0x44, 0x44, 0x38, /* 0x62 'b' */
    0x38, 0x44, 0x44, 0x44, 0x20, /* 0x63 'c' */
    0x38, 0x44, 0x44, 0x48, 0x7F, /* 0x64 'd' */
    0x38, 0x54, 0x54, 0x54, 0x18, /* 0x65 'e' */
    0x08, 0x7E, 0x09, 0x01, 0x02, /* 0x66 'f' */
    0x0C, 0x52, 0x52, 0x52, 0x3E, /* 0x67 'g' */
    0x7F, 0x08, 0x04, 0x04, 0x78, /* 0x68 'h' */
    0x00, 0x44, 0x7D, 0x40, 0x00, /* 0x69 'i' */
    0x20, 0x40, 0x44, 0x3D, 0x00, /* 0x6A 'j' */
    0x7F, 0x10, 0x28, 0x44, 0x00, /* 0x6B 'k' */
    0x00, 0x41, 0x7F, 0x40, 0x00, /* 0x6C 'l' */
    0x7C, 0x04, 0x18, 0x04, 0x78, /* 0x6D 'm' */
    0x7C, 0x08, 0x04, 0x04, 0x78, /* 0x6E 'n' */
    0x38, 0x44, 0x44, 0x44, 0x38, /* 0x6F 'o' */
    0x7C, 0x14, 0x14, 0x14, 0x08, /* 0x70 'p' */
    0x08, 0x14, 0x14, 0x18, 0x7C, /* 0x71 'q' */
    0x7C, 0x08, 0x04, 0x04, 0x08, /* 0x72 'r' */
    0x48, 0x54, 0x54, 0x54, 0x20, /* 0x73 's' */
    0x04, 0x3F, 0x44, 0x40, 0x20, /* 0x74 't' */
    0x3C, 0x40, 0x40, 0x20, 0x7C, /* 0x75 'u' */
    0x1C, 0x20, 0x40, 0x20, 0x1C, /* 0x76 'v' */
    0x3C, 0x40, 0x30, 0x40, 0x3C, /* 0x77 'w' */
    0x44, 0x28, 0x10, 0x28, 0x44, /* 0x78 'x' */
    0x0C, 0x50, 0x50, 0x50, 0x3C, /* 0x79 'y' */
    0x44, 0x64, 0x54, 0x4C, 0x44, /* 0x7A 'z' */
    0x00, 0x08, 0x36, 0x41, 0x00, /* 0x7B '{' */
    0x00, 0x00, 0x7F, 0x00, 0x00, /* 0x7C '|' */
    0x00, 0x41, 0x36, 0x08, 0x00, /* 0x7D '}' */
    0x08, 0x04, 0x08, 0x10, 0x08, /* 0x7E '~' */
};

/* Runtime handles, valid only after a successful display_init(). */
static esp_lcd_panel_io_handle_t s_io;
static esp_lcd_panel_handle_t s_panel;
static SemaphoreHandle_t s_flush_done;
static bool s_flush_in_flight;
static bool s_initialised;

/*
 * The static budget is part of the interface contract, not a comment: the
 * display module must stay under 16 KB of .bss including the strip buffer.
 */
_Static_assert(DISPLAY_STRIP_BYTES <= 16 * 1024, "strip buffer exceeds the 16 KB display budget");
_Static_assert(DISPLAY_V_RES % DISPLAY_STRIP_ROWS == 0, "panel height must be a whole number of bands");
_Static_assert(DISPLAY_WAVE_Y % DISPLAY_STRIP_ROWS == 0, "waveform must start on a band boundary");
_Static_assert(DISPLAY_WAVE_HEIGHT % DISPLAY_STRIP_ROWS == 0,
               "waveform height must be a whole number of bands");
_Static_assert(sizeof(s_strip) + sizeof(s_wave_y) <= 16 * 1024,
               "display module static RAM exceeds 16 KB");

/* ------------------------------------------------------------------------- */
/* Small helpers                                                             */
/* ------------------------------------------------------------------------- */

/*
 * RGB565 values in memory are little-endian (Xtensa), but ST7789 expects the
 * high byte first. Swapping once while composing the strip is cheaper than a
 * second pass over the buffer and does not depend on RAMCTL endian support.
 * Keep DISPLAY_DATA_ENDIAN = LCD_RGB_DATA_ENDIAN_BIG in sync with this.
 */
static inline uint16_t display_wire_rgb565(uint16_t colour)
{
    return (uint16_t)((colour >> 8) | (colour << 8));
}

static inline int display_min_int(int a, int b)
{
    return (a < b) ? a : b;
}

static inline int display_max_int(int a, int b)
{
    return (a > b) ? a : b;
}

/*
 * Fill pixels [0, count) of the strip with one RGB565 colour.
 * count is at most DISPLAY_STRIP_PIXELS, so the loop cannot leave the buffer.
 */
static void display_strip_fill(size_t count, uint16_t rgb565)
{
    uint16_t wire = display_wire_rgb565(rgb565);

    for (size_t i = 0; i < count; i++) {
        s_strip[i] = wire;
    }
}

/*
 * Wait until the previous strip transfer has been copied out by SPI DMA.
 * The panel IO callback gives the semaphore from the SPI ISR. A timeout is
 * logged but not fatal: a missing panel must never stop the firmware.
 */
static bool display_wait_flush_done(void)
{
    if (s_flush_done == NULL) {
        return false;
    }

    if (xSemaphoreTake(s_flush_done, pdMS_TO_TICKS(DISPLAY_FLUSH_TIMEOUT_MS)) != pdTRUE) {
        ESP_LOGW(TAG, "SPI flush timeout after %d ms", DISPLAY_FLUSH_TIMEOUT_MS);
        return false;
    }
    return true;
}

/* Called from the SPI ISR when the last chunk of a colour transfer completed. */
static bool IRAM_ATTR display_flush_done_cb(esp_lcd_panel_io_handle_t panel_io,
                                            esp_lcd_panel_io_event_data_t *edata,
                                            void *user_ctx)
{
    (void)panel_io;
    (void)edata;
    (void)user_ctx;

    BaseType_t higher_priority_task_woken = pdFALSE;
    if (s_flush_done != NULL) {
        xSemaphoreGiveFromISR(s_flush_done, &higher_priority_task_woken);
    }
    return higher_priority_task_woken == pdTRUE;
}

/*
 * Push rows [y_start, y_end) of the strip as a rectangle of width `width`.
 * The strip is packed row-major with `width` pixels per row.
 */
static void display_blit_strip(int x_start, int y_start, int width, int height)
{
    if (s_panel == NULL) {
        return;
    }

    /*
     * The strip buffer is shared by every band, so the previous transfer must
     * be complete before the next band overwrites it. In the normal path the
     * semaphore was already consumed by the previous call; only a previous
     * timeout can leave a transfer in flight.
     */
    if (s_flush_in_flight) {
        if (!display_wait_flush_done()) {
            /* Never queue a second transfer over a stuck one. */
            ESP_LOGW(TAG, "previous strip transfer still pending; dropping this band");
            return;
        }
    }

    esp_err_t err = esp_lcd_panel_draw_bitmap(s_panel, x_start, y_start,
                                              x_start + width, y_start + height, s_strip);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "draw_bitmap(%d,%d %dx%d) failed 0x%x", x_start, y_start,
                 width, height, err);
        s_flush_in_flight = false;
        return;
    }

    s_flush_in_flight = true;
    if (display_wait_flush_done()) {
        s_flush_in_flight = false;
    }
}

/* ------------------------------------------------------------------------- */
/* Public API                                                                */
/* ------------------------------------------------------------------------- */

static void display_release_partial(void)
{
    if (s_panel != NULL) {
        (void)esp_lcd_panel_del(s_panel);
        s_panel = NULL;
    }
    if (s_io != NULL) {
        (void)esp_lcd_panel_io_del(s_io);
        s_io = NULL;
    }
    (void)spi_bus_free(DISPLAY_SPI_HOST);
}

esp_err_t display_init(void)
{
    esp_err_t err;

    if (s_initialised) {
        return ESP_OK;
    }
    if (s_panel != NULL || s_io != NULL) {
        ESP_LOGW(TAG, "display_init() found a partially initialised state; refusing a second bus");
        return ESP_ERR_INVALID_STATE;
    }

    /* Backlight starts off so the panel shows no garbage during init. */
#if DISPLAY_PIN_BL >= 0
    gpio_config_t bl_config = {
        .pin_bit_mask = 1ULL << DISPLAY_PIN_BL,
        .mode = GPIO_MODE_OUTPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    err = gpio_config(&bl_config);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "backlight GPIO%d config failed 0x%x", DISPLAY_PIN_BL, err);
        return err;
    }
    (void)gpio_set_level(DISPLAY_PIN_BL, DISPLAY_BL_ACTIVE_HIGH ? 0 : 1);
#endif

    spi_bus_config_t bus_config = {
        .sclk_io_num = DISPLAY_PIN_SCLK,
        .mosi_io_num = DISPLAY_PIN_MOSI,
        .miso_io_num = -1,
        .quadwp_io_num = -1,
        .quadhd_io_num = -1,
        .max_transfer_sz = DISPLAY_STRIP_BYTES,
    };
    err = spi_bus_initialize(DISPLAY_SPI_HOST, &bus_config, SPI_DMA_CH_AUTO);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "SPI2_HOST bus init failed 0x%x", err);
        return err;
    }

    /* The completion callback must exist before the first draw_bitmap(). */
    s_flush_done = xSemaphoreCreateBinary();
    if (s_flush_done == NULL) {
        ESP_LOGE(TAG, "flush semaphore allocation failed");
        (void)spi_bus_free(DISPLAY_SPI_HOST);
        return ESP_ERR_NO_MEM;
    }

    esp_lcd_panel_io_spi_config_t io_config = {
        .dc_gpio_num = DISPLAY_PIN_DC,
        .cs_gpio_num = DISPLAY_PIN_CS,
        .pclk_hz = DISPLAY_PIXEL_CLOCK_HZ,
        .lcd_cmd_bits = DISPLAY_CMD_BITS,
        .lcd_param_bits = DISPLAY_PARAM_BITS,
        .spi_mode = DISPLAY_SPI_MODE,
        .trans_queue_depth = DISPLAY_TRANS_QUEUE_DEPTH,
        .on_color_trans_done = display_flush_done_cb,
        .user_ctx = NULL,
    };
    err = esp_lcd_new_panel_io_spi((esp_lcd_spi_bus_handle_t)DISPLAY_SPI_HOST, &io_config, &s_io);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "panel IO init failed 0x%x", err);
        s_io = NULL;
        vSemaphoreDelete(s_flush_done);
        s_flush_done = NULL;
        (void)spi_bus_free(DISPLAY_SPI_HOST);
        return err;
    }

    esp_lcd_panel_dev_config_t panel_config = {
        .reset_gpio_num = DISPLAY_PIN_RST,
        .rgb_ele_order = DISPLAY_RGB_ELE_ORDER,
        .data_endian = DISPLAY_DATA_ENDIAN,
        .bits_per_pixel = 16,
    };
    err = esp_lcd_new_panel_st7789(s_io, &panel_config, &s_panel);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "ST7789 panel init failed 0x%x", err);
        display_release_partial();
        vSemaphoreDelete(s_flush_done);
        s_flush_done = NULL;
        return err;
    }

    err = esp_lcd_panel_reset(s_panel);
    if (err == ESP_OK) {
        err = esp_lcd_panel_init(s_panel);
    }
    if (err == ESP_OK) {
        err = esp_lcd_panel_invert_color(s_panel, DISPLAY_INVERT_COLOR != 0);
    }
    if (err == ESP_OK) {
        err = esp_lcd_panel_swap_xy(s_panel, DISPLAY_SWAP_XY != 0);
    }
    if (err == ESP_OK) {
        err = esp_lcd_panel_mirror(s_panel, DISPLAY_MIRROR_X != 0, DISPLAY_MIRROR_Y != 0);
    }
    if (err == ESP_OK) {
        err = esp_lcd_panel_set_gap(s_panel, DISPLAY_X_GAP, DISPLAY_Y_GAP);
    }
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "ST7789 reset/init sequence failed 0x%x", err);
        display_release_partial();
        vSemaphoreDelete(s_flush_done);
        s_flush_done = NULL;
        return err;
    }

    /* Turn the panel on, then fill it black before the backlight comes up. */
    err = esp_lcd_panel_disp_on_off(s_panel, true);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "disp_on failed 0x%x (continuing with a blank panel)", err);
    }

    s_initialised = true;
    display_clear(DISPLAY_COLOUR_BLACK);

#if DISPLAY_PIN_BL >= 0
    (void)gpio_set_level(DISPLAY_PIN_BL, DISPLAY_BL_ACTIVE_HIGH ? 1 : 0);
#endif

    ESP_LOGI(TAG,
             "ST7789 %dx%d ready on SPI2_HOST: SCLK=%d MOSI=%d CS=%d DC=%d RST=%d BL=%d "
             "%d MHz, strip %u B, static %u B",
             DISPLAY_H_RES, DISPLAY_V_RES, DISPLAY_PIN_SCLK,
             DISPLAY_PIN_MOSI, DISPLAY_PIN_CS, DISPLAY_PIN_DC, DISPLAY_PIN_RST,
             DISPLAY_PIN_BL, DISPLAY_PIXEL_CLOCK_HZ / (1000 * 1000),
             (unsigned)DISPLAY_STRIP_BYTES,
             (unsigned)(sizeof(s_strip) + sizeof(s_wave_y)));
    return ESP_OK;
}

bool display_is_initialised(void)
{
    return s_initialised;
}

void display_clear(uint16_t rgb565)
{
    if (!s_initialised) {
        return;
    }

    for (int y = 0; y < DISPLAY_V_RES; y += DISPLAY_STRIP_ROWS) {
        display_strip_fill(DISPLAY_STRIP_PIXELS, rgb565);
        display_blit_strip(0, y, DISPLAY_H_RES, DISPLAY_STRIP_ROWS);
    }
}

void display_draw_waveform(const int16_t *samples, size_t n, uint16_t colour)
{
    if (!s_initialised || samples == NULL || n == 0u) {
        return;
    }

    /*
     * First pass: the peak drives the vertical auto-range. Scanning the whole
     * window once is cheaper than keeping a second 320-entry sample array.
     */
    int32_t peak = DISPLAY_WAVE_MIN_PEAK;
    for (size_t i = 0; i < n; i++) {
        int32_t value = samples[i];
        int32_t magnitude = (value < 0) ? -value : value;
        if (magnitude > peak) {
            peak = magnitude;
        }
    }

    /*
     * Second pass: decimate to one representative per column. Keeping the
     * largest absolute sample (rather than every Nth sample) preserves narrow
     * peaks; the polyline then joins consecutive columns so the trace stays
     * continuous. Each column range is disjoint, so this is still O(n).
     */
    const int centre = DISPLAY_WAVE_Y + DISPLAY_WAVE_HEIGHT / 2;
    const int half_height = (DISPLAY_WAVE_HEIGHT - 2) / 2;
    for (int x = 0; x < DISPLAY_H_RES; x++) {
        size_t first = ((size_t)x * n) / DISPLAY_H_RES;
        size_t last = ((size_t)(x + 1) * n) / DISPLAY_H_RES;
        if (last <= first) {
            last = first + 1u;
        }
        if (last > n) {
            last = n;
        }

        int16_t representative = samples[first];
        int32_t best = (representative < 0) ? -(int32_t)representative : representative;
        for (size_t i = first + 1u; i < last; i++) {
            int32_t value = samples[i];
            int32_t magnitude = (value < 0) ? -value : value;
            if (magnitude > best) {
                best = magnitude;
                representative = samples[i];
            }
        }

        int pixel = centre - (int)(((int32_t)representative * half_height) / peak);
        if (pixel < DISPLAY_WAVE_Y) {
            pixel = DISPLAY_WAVE_Y;
        }
        if (pixel >= DISPLAY_WAVE_Y + DISPLAY_WAVE_HEIGHT) {
            pixel = DISPLAY_WAVE_Y + DISPLAY_WAVE_HEIGHT - 1;
        }
        s_wave_y[x] = (uint8_t)pixel;
    }

    /* Render the waveform area one 16-row band at a time. */
    for (int band = DISPLAY_WAVE_Y; band < DISPLAY_WAVE_Y + DISPLAY_WAVE_HEIGHT;
         band += DISPLAY_STRIP_ROWS) {
        display_strip_fill(DISPLAY_STRIP_PIXELS, DISPLAY_WAVE_BG_COLOUR);

        for (int x = 0; x < DISPLAY_H_RES; x++) {
            int y_previous = (x == 0) ? s_wave_y[0] : s_wave_y[x - 1];
            int y_current = s_wave_y[x];
            int y_low = display_min_int(y_previous, y_current);
            int y_high = display_max_int(y_previous, y_current);

            int row_first = display_max_int(y_low, band);
            int row_last = display_min_int(y_high, band + DISPLAY_STRIP_ROWS - 1);
            for (int y = row_first; y <= row_last; y++) {
                s_strip[(size_t)(y - band) * DISPLAY_H_RES + (size_t)x] =
                    display_wire_rgb565(colour);
            }
        }

        display_blit_strip(0, band, DISPLAY_H_RES, DISPLAY_STRIP_ROWS);
    }
}

void display_draw_text(int x, int y, const char *text, uint16_t fg, uint16_t bg)
{
    if (!s_initialised || text == NULL) {
        return;
    }
    if (x < 0 || x >= DISPLAY_H_RES || y < 0 || y > DISPLAY_V_RES - DISPLAY_FONT_HEIGHT) {
        return;
    }

    size_t length = strlen(text);
    if (length == 0u) {
        return;
    }

    /* Number of whole characters that still fit to the right of x. */
    size_t max_chars = (size_t)(DISPLAY_H_RES - x) / DISPLAY_FONT_CELL_WIDTH;
    if (length > max_chars) {
        length = max_chars;
    }
    if (length == 0u) {
        return;
    }

    /*
     * Clear the full remaining line, not just the new string: app_main
     * redraws "HR 72" as "HR --" on the same row, and fixed-width clearing is
     * what prevents remnants. Each 16-row band is a separate rectangle.
     */
    const int width = DISPLAY_H_RES - x;
    for (int band = (y / DISPLAY_STRIP_ROWS) * DISPLAY_STRIP_ROWS;
         band < y + DISPLAY_FONT_HEIGHT; band += DISPLAY_STRIP_ROWS) {
        int rows = DISPLAY_STRIP_ROWS;
        if (band + rows > DISPLAY_V_RES) {
            rows = DISPLAY_V_RES - band;
        }

        display_strip_fill((size_t)width * (size_t)rows, bg);

        for (size_t index = 0; index < length; index++) {
            unsigned char character = (unsigned char)text[index];
            if (character < 0x20u || character > 0x7Eu) {
                character = '?';
            }
            const uint8_t *glyph = &s_font5x7[(size_t)(character - 0x20u) * DISPLAY_FONT_WIDTH];

            int glyph_x = x + (int)index * DISPLAY_FONT_CELL_WIDTH;
            for (int column = 0; column < DISPLAY_FONT_WIDTH; column++) {
                uint8_t bits = glyph[column];
                for (int row = 0; row < DISPLAY_FONT_HEIGHT; row++) {
                    if ((bits & (uint8_t)(1u << row)) == 0u) {
                        continue;
                    }
                    int pixel_y = y + row - band;
                    if (pixel_y < 0 || pixel_y >= rows) {
                        continue;
                    }
                    int pixel_x = glyph_x + column - x;
                    if (pixel_x < 0 || pixel_x >= width) {
                        continue;
                    }
                    s_strip[(size_t)pixel_y * (size_t)width + (size_t)pixel_x] =
                        display_wire_rgb565(fg);
                }
            }
        }

        display_blit_strip(x, band, width, rows);
    }
}
