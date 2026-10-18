/*
 * app_main.c - task structure of the PCG stethoscope host firmware.
 *
 * Data flow (matches ADR-014 and the ADR-008 edge-inference constraint):
 *
 *   probe --BLE notify--> ble_central.c (NimBLE host task, core 0)
 *                              |
 *                              | one complete LKA1 packet per notification
 *                              v
 *                        app_pipeline.c  (mutex + ring, 160 frames = 3.2 s)
 *                              |
 *                              | newest 8000 samples (2 s @ 4 kHz)
 *                              v
 *                    +---------+---------+
 *                    v                   v
 *              display_stub.c       ai_stub.c  (NOT implemented)
 *              (no-op / LVGL-ready) (returns NOT_IMPLEMENTED)
 *
 * Threads:
 *   - NimBLE host task (created by nimble_port_freertos_init, core 0, priority
 *     from Kconfig): runs the GAP/GATTC callbacks, pushes packets.
 *   - ai_display_task (core 1, priority 5): every AI_WINDOW_STEP_MS reads the
 *     newest window, renders it, and calls the inference stub.
 *   - esp_timer task: reconnect / MTU-timeout timers owned by ble_central.c.
 *
 * Why the consumer runs on core 1 at a modest priority:
 *   NimBLE's host task must never wait for the render or the model. Pinning the
 *   consumer to the APP core keeps the BLE event loop on the PRO core even if a
 *   future LVGL flush or inference overruns its budget. Priority 5 is above the
 *   idle task and below NimBLE's host task (21 by default).
 *
 * What is real today: BLE central, LKA1 reassembly, AUD1 CRC check, 8k->4k
 * Q15 decimation, ring buffering, window extraction, and periodic statistics.
 * What is stubbed: the model (ai_stub) and the panel (display_stub). There is no
 * hardware in this milestone; the acceptance criterion is that this builds.
 */
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

#include "esp_chip_info.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_system.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "ai_stub.h"
#include "app_pipeline.h"
#include "ble_central.h"
#include "display_stub.h"

static const char *TAG = "app_main";

/* Consumer task placement and cadence (see the thread notes above). */
#define APP_AI_TASK_CORE 1
#define APP_AI_TASK_PRIORITY 5
#define APP_AI_TASK_STACK_BYTES 4096

/*
 * ADR-007 window protocol: 2 s window, 1 s step (50 % overlap). At 4 kHz a
 * 1 s step is 50 new decimated frames, so the 160-frame ring always holds more
 * than one full window plus one step.
 */
#define APP_AI_WINDOW_STEP_MS 1000u

/* Log the pipeline counters every N windows (10 s at the current step). */
#define APP_STATS_EVERY_WINDOWS 10u

/*
 * The AI window buffer is static: 8000 int16 = 16,000 B of .bss. It is shared
 * by the display and AI call sites (both run in the same task, so no lock).
 */
static int16_t s_ai_window[HOST_PIPELINE_AI_WINDOW_SAMPLES];

static void log_boot_banner(void)
{
    esp_chip_info_t chip;
    esp_chip_info(&chip);

    ESP_LOGI(TAG, "PCG stethoscope host firmware, ESP32-S3 (silicon rev %d, %d core(s))",
             chip.revision, chip.cores);
    ESP_LOGI(TAG,
             "static budget: ring %u B + AI window %u B = %u B of .bss before stacks (ADR-014/ADR-007)",
             (unsigned)APP_PIPELINE_RING_BYTES,
             (unsigned)sizeof(s_ai_window),
             (unsigned)(APP_PIPELINE_RING_BYTES + sizeof(s_ai_window)));
    ESP_LOGI(TAG, "heap after init: internal %u B free of %u B",
             (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL),
             (unsigned)heap_caps_get_total_size(MALLOC_CAP_INTERNAL));
}

static void log_pipeline_stats(void)
{
    host_pipeline_stats_t stats;
    app_pipeline_get_stats(&stats);

    /*
     * overruns climbs once the ring is full: this consumer always wants the
     * *newest* 2 s, so overwriting old frames is the intended behaviour, not a
     * fault. Real frame loss must be detected from LKA1 sequence gaps and
     * crc_errors / packet_errors instead.
     */
    ESP_LOGI(TAG,
             "pipeline: in=%u ok=%u crc_err=%u pkt_err=%u fmt_err=%u stale=%u "
             "restarts=%u overruns=%u (rolling ring, expected) lock_misses=%u",
             (unsigned)stats.packets_in, (unsigned)stats.frames_ok,
             (unsigned)stats.crc_errors, (unsigned)stats.packet_errors,
             (unsigned)stats.format_errors, (unsigned)stats.stale_packets,
             (unsigned)stats.session_restarts, (unsigned)stats.overruns,
             (unsigned)app_pipeline_lock_misses());
}

static void ai_display_task(void *arg)
{
    (void)arg;

    ai_stub_result_t result = {0};
    char status_line[64];
    uint32_t windows = 0;
    uint32_t empty_reads = 0;
    TickType_t last_wake = xTaskGetTickCount();

    ESP_LOGI(TAG, "consumer task on core %d: %u ms step, %u-sample window",
             APP_AI_TASK_CORE, (unsigned)APP_AI_WINDOW_STEP_MS,
             (unsigned)HOST_PIPELINE_AI_WINDOW_SAMPLES);

    for (;;) {
        vTaskDelayUntil(&last_wake, pdMS_TO_TICKS(APP_AI_WINDOW_STEP_MS));

        size_t samples =
            app_pipeline_read_window(s_ai_window, HOST_PIPELINE_AI_WINDOW_SAMPLES);
        if (samples == 0) {
            /*
             * All-or-nothing read (ADR-014 D6): fewer than 100 frames buffered.
             * Expected for the first ~2 s after connect and after a session
             * restart, which clears the ring.
             */
            empty_reads++;
            if ((empty_reads % 5u) == 1u) {
                ESP_LOGD(TAG, "no full window yet (%u frames buffered, %u empty reads)",
                         (unsigned)app_pipeline_frames_available(),
                         (unsigned)empty_reads);
            }
            continue;
        }

        windows++;

        /* Display consumes the same window; its stub backend down-samples to
         * 320 min/max columns and logs at DEBUG. */
        esp_err_t display_err = display_stub_render_waveform(s_ai_window, samples);
        if (display_err != ESP_OK) {
            ESP_LOGW(TAG, "display_stub_render_waveform failed 0x%x", display_err);
        }

        ai_stub_status_t ai_status = ai_stub_infer(s_ai_window, samples, &result);
        if (ai_status == AI_STUB_STATUS_NOT_IMPLEMENTED) {
            if ((windows % APP_STATS_EVERY_WINDOWS) == 1u) {
                (void)snprintf(status_line, sizeof(status_line), "murmur: n/a (no model)");
                (void)display_stub_render_text(8, 8, status_line);
            }
        } else if (ai_status == AI_STUB_STATUS_OK) {
            (void)snprintf(status_line, sizeof(status_line), "murmur: %.2f",
                           (double)result.murmur_probability);
            (void)display_stub_render_text(8, 8, status_line);
        } else {
            ESP_LOGE(TAG, "ai_stub_infer rejected the window (status=%d)",
                     (int)ai_status);
        }

        if ((windows % APP_STATS_EVERY_WINDOWS) == 0u) {
            ble_central_link_info_t link;
            ble_central_get_link_info(&link);
            ESP_LOGI(TAG,
                     "windows=%u display=%u link: connected=%d ready=%d mtu=%u phy=%s/%s "
                     "notif=%u push_ok=%u push_rej=%u reconnects=%u",
                     (unsigned)windows, (unsigned)display_stub_windows_rendered(),
                     (int)link.connected, (int)link.ready, (unsigned)link.att_mtu,
                     ble_central_phy_name(link.tx_phy), ble_central_phy_name(link.rx_phy),
                     (unsigned)link.notifications, (unsigned)link.push_ok,
                     (unsigned)link.push_rejected, (unsigned)link.reconnect_attempts);
            log_pipeline_stats();
        }
    }
}

void app_main(void)
{
    BaseType_t created;

    log_boot_banner();

    /*
     * Order matters:
     *   1. pipeline  - the BLE callback may fire as soon as the host task runs
     *   2. display   - cheap, no dependency
     *   3. AI        - logs the contract, allocates nothing
     *   4. BLE       - starts the host task, which starts scanning
     *   5. consumer  - reads the ring the BLE task fills
     */
    ESP_ERROR_CHECK(app_pipeline_init());
    ESP_ERROR_CHECK(display_stub_init());
    ESP_ERROR_CHECK(ai_stub_init());
    ESP_ERROR_CHECK(ble_central_init());
    ESP_ERROR_CHECK(ble_central_start_scan());

    created = xTaskCreatePinnedToCore(ai_display_task, "ai_display",
                                      APP_AI_TASK_STACK_BYTES, NULL,
                                      APP_AI_TASK_PRIORITY, NULL, APP_AI_TASK_CORE);
    if (created != pdPASS) {
        ESP_LOGE(TAG, "failed to create the AI/display consumer task");
        abort();
    }

    ESP_LOGI(TAG, "init complete; waiting for an LKA1 probe");
    /* app_main returning is fine: the main task is deleted and the work is
     * owned by the NimBLE host task, the consumer task and the esp_timer task. */
}
