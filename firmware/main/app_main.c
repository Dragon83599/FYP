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
 *                    +---------+----------+
 *                    v                    v
 *              hr_detector.c        ai_stub.c  (NOT implemented)
 *              (real: HR ~<bpm>)    (returns NOT_IMPLEMENTED)
 *                    |                    |
 *                    +--> display_stub.c <+
 *                         (no-op / LVGL-ready)
 *
 * Threads:
 *   - NimBLE host task (created by nimble_port_freertos_init, core 0, priority
 *     from Kconfig): runs the GAP/GATTC callbacks, pushes packets.
 *   - ai_display_task (core 1, priority 5): every AI_WINDOW_STEP_MS reads the
 *     newest window, pushes it through the heart-rate detector, renders the
 *     waveform + status lines, and calls the inference stub.
 *   - esp_timer task: reconnect / MTU-timeout timers owned by ble_central.c.
 *
 * Why the consumer runs on core 1 at a modest priority:
 *   NimBLE's host task must never wait for the render or the model. Pinning the
 *   consumer to the APP core keeps the BLE event loop on the PRO core even if a
 *   future LVGL flush or inference overruns its budget. Priority 5 is above the
 *   idle task and below NimBLE's host task (21 by default).
 *
 * What is real today: BLE central, LKA1 reassembly, AUD1 CRC check, 8k->4k
 * Q15 decimation, ring buffering, window extraction, the streaming heart-rate
 * detector (HR is an *estimate*, see docs/mvp-output-contract.md) and periodic
 * statistics. What is stubbed: the murmur model (ai_stub) and the panel
 * (display_stub). There is no hardware in this milestone; the acceptance
 * criterion is that this builds.
 */
#include <stdbool.h>
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
#include "hr_detector.h"

static const char *TAG = "app_main";

/* Consumer task placement and cadence (see the thread notes above). */
#define APP_AI_TASK_CORE 1
#define APP_AI_TASK_PRIORITY 5

/*
 * 8 KiB, up from 4 KiB. hr_detector_push() is now the deepest call in this
 * task. Measured with -fstack-usage at -O2 on the target compiler
 * (xtensa-esp32s3-elf-gcc): hr_detector_push_hop 1,216 B,
 * hr_detector_estimate 448 B, hr_envelope_quantile 320 B. The x86-64 host
 * frames are larger (1,760/432/288 B, hr_detector.h D12), so 8 KiB keeps >5x
 * margin over the measured worst case instead of hoping it fits.
 */
#define APP_AI_TASK_STACK_BYTES 8192

/*
 * ADR-007 window protocol: 2 s window, 1 s step (50 % overlap). At 4 kHz a
 * 1 s step is 50 new decimated frames, so the 160-frame ring always holds more
 * than one full window plus one step.
 */
#define APP_AI_WINDOW_STEP_MS 1000u

/* Log the pipeline counters every N windows (10 s at the current step). */
#define APP_STATS_EVERY_WINDOWS 10u

/* Status-line positions. The stub only logs them; an LVGL backend draws them. */
#define APP_TEXT_MURMUR_X 8
#define APP_TEXT_MURMUR_Y 8
#define APP_TEXT_HR_X 8
#define APP_TEXT_HR_Y 24
#define APP_TEXT_HR_CAPTION_Y 40

/*
 * docs/mvp-output-contract.md section 2 requires the heart rate to be labelled
 * as an estimate ("显示为 HR ~72 并带'估算'字样"). The "~" in "HR ~72" carries
 * that meaning; this caption states it and repeats that the device does not
 * diagnose.
 */
#define APP_HR_CAPTION "estimated, not a diagnosis"

/*
 * The AI window buffer is static: 8000 int16 = 16,000 B of .bss. It is shared
 * by the display and AI call sites (both run in the same task, so no lock).
 */
static int16_t s_ai_window[HOST_PIPELINE_AI_WINDOW_SAMPLES];

/*
 * Heart-rate detector state, also static: no malloc anywhere in this file.
 * The work buffer is HR_DETECTOR_WORK_FLOATS floats = 35,096 B; the struct is
 * 352 B in this 32-bit Xtensa build (360 B per the x86-64 header measurement).
 * Together that is +35,448 B of .bss/DIRAM, exactly the measured delta. Neither
 * object is in IRAM: the code and the 129-tap coefficient table are
 * flash/rodata (hr_detector.h D9/D12). The caller owns the work buffer because
 * the detector keeps no mutable static state, so a second instance would need
 * its own 35,096 B.
 */
static hr_detector_t s_hr_detector;
static float s_hr_work[HR_DETECTOR_WORK_FLOATS];

/*
 * hr_detector_push() advances the analysis window by n / 2, which is this
 * loop's cadence only while the AI step is exactly half of the 2 s window.
 * If APP_AI_WINDOW_STEP_MS ever changes, this assertion fails the build and
 * the call below must become
 *   hr_detector_push_hop(&s_hr_detector, s_ai_window, samples, step_samples);
 * (hr_detector.h D11/D12: a wrong hop double-counts or suppresses real events.)
 */
_Static_assert(
    (uint64_t)APP_AI_WINDOW_STEP_MS * HR_DETECTOR_SAMPLE_RATE_HZ / 1000u ==
        HR_DETECTOR_WINDOW_SAMPLES / 2u,
    "hr_detector_push() assumes hop = n/2; use hr_detector_push_hop() if the AI step changes");
_Static_assert(HOST_PIPELINE_AI_WINDOW_SAMPLES == HR_DETECTOR_WINDOW_SAMPLES,
               "the AI window and the heart-rate window must stay the same 2 s window");

static void log_boot_banner(void)
{
    esp_chip_info_t chip;
    esp_chip_info(&chip);

    ESP_LOGI(TAG, "PCG stethoscope host firmware, ESP32-S3 (silicon rev %d, %d core(s))",
             chip.revision, chip.cores);
    ESP_LOGI(TAG,
             "static budget: ring %u B + AI window %u B + HR %u B "
             "(detector %u + work %u) = %u B of .bss before stacks "
             "(ADR-014/ADR-007, hr_detector.h D9)",
             (unsigned)APP_PIPELINE_RING_BYTES,
             (unsigned)sizeof(s_ai_window),
             (unsigned)(sizeof(s_hr_detector) + sizeof(s_hr_work)),
             (unsigned)sizeof(s_hr_detector),
             (unsigned)sizeof(s_hr_work),
             (unsigned)(APP_PIPELINE_RING_BYTES + sizeof(s_ai_window) +
                        sizeof(s_hr_detector) + sizeof(s_hr_work)));
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
    uint32_t session_restarts_seen = 0;
    uint32_t skipped_steps = 0;
    uint32_t frames_seen = 0;
    bool hr_stale = false;
    TickType_t last_wake = xTaskGetTickCount();

    ESP_LOGI(TAG, "consumer task on core %d: %u ms step, %u-sample window",
             APP_AI_TASK_CORE, (unsigned)APP_AI_WINDOW_STEP_MS,
             (unsigned)HOST_PIPELINE_AI_WINDOW_SAMPLES);

    /* The estimate caption never changes; draw it once on the current backend.
     * The per-window status line below draws the value itself. */
    esp_err_t caption_err = display_stub_render_text(
        APP_TEXT_HR_X, APP_TEXT_HR_CAPTION_Y, APP_HR_CAPTION);
    if (caption_err != ESP_OK) {
        ESP_LOGW(TAG, "display_stub_render_text(caption) failed 0x%x", caption_err);
    }

    for (;;) {
        vTaskDelayUntil(&last_wake, pdMS_TO_TICKS(APP_AI_WINDOW_STEP_MS));

        /*
         * Session tracking. A disconnect/reconnect must use a new session_id
         * (docs/link-a-gatt-profile.md section 5), and host_pipeline counts
         * every observed session change in session_restarts. This poll is the
         * restart signal: hr_detector_reset() is called only here, in the one
         * task that touches the detector, never in the NimBLE callback - the
         * detector is not thread-safe. The counter is monotonic, so a lock
         * timeout (which zeroes the snapshot) cannot fake a restart.
         */
        host_pipeline_stats_t stats;
        app_pipeline_get_stats(&stats);
        bool reset_hr = false;

        if (stats.session_restarts > session_restarts_seen) {
            session_restarts_seen = stats.session_restarts;
            reset_hr = true;
            ESP_LOGI(TAG, "new LKA1 session (restart #%u): resetting HR history",
                     (unsigned)session_restarts_seen);
        }

        size_t samples =
            app_pipeline_read_window(s_ai_window, HOST_PIPELINE_AI_WINDOW_SAMPLES);
        if (samples == 0) {
            /*
             * All-or-nothing read (ADR-014 D6): fewer than 100 frames buffered.
             * Expected for the first ~2 s after connect and after a session
             * restart, which clears the ring.
             */
            empty_reads++;
            skipped_steps++;
            if ((empty_reads % 5u) == 1u) {
                ESP_LOGD(TAG, "no full window yet (%u frames buffered, %u empty reads)",
                         (unsigned)app_pipeline_frames_available(),
                         (unsigned)empty_reads);
            }
            continue;
        }

        /*
         * The detector advances an absolute window cursor by hop per push, so
         * a gap in the stream (one or more steps without a full window) must
         * not carry the old cursor and event times into the new data
         * (hr_detector.h D12). Drop the history instead; the display shows
         * "HR --" until 4 events have accumulated again.
         */
        if (skipped_steps > 0) {
            if (windows > 0) {
                reset_hr = true;
                ESP_LOGD(TAG, "HR history reset after %u step(s) without a full window",
                         (unsigned)skipped_steps);
            }
            skipped_steps = 0;
        }
        if (reset_hr) {
            hr_detector_reset(&s_hr_detector);
        }

        windows++;

        /* Display consumes the same window; its stub backend down-samples to
         * 320 min/max columns and logs at DEBUG. */
        esp_err_t display_err = display_stub_render_waveform(s_ai_window, samples);
        if (display_err != ESP_OK) {
            ESP_LOGW(TAG, "display_stub_render_waveform failed 0x%x", display_err);
        }

        /*
         * Heart rate. The loop is a 2 s window every APP_AI_WINDOW_STEP_MS =
         * 1000 ms, i.e. hop = n/2, which is exactly hr_detector_push()'s
         * contract (the _Static_assert above fails the build if that stops
         * being true). Detector and work buffer are static, so this is just a
         * filter + peak pass over the window already in memory.
         *
         * A successful read is not proof of new audio: when the probe goes
         * idle or the link drops, the ring keeps its last 160 frames and the
         * newest 2 s can be byte-identical to the previous window. Pushing that
         * again with hop = n/2 would turn every old peak into a "new" event one
         * second later and fabricate a plausible-looking ~60 bpm. frames_ok
         * counts frames actually decimated and stored, so it only advances on
         * fresh audio; without it, keep "HR --" and drop the stale history.
         */
        /*
         * Signed difference of the monotonic counter: positive means new
         * frames arrived, and it stays correct across a uint32_t wrap (994 days
         * of 50 frame/s streaming). A stats lock timeout zeroes the snapshot,
         * which reads as "not fresh" here - the safe direction.
         */
        bool data_fresh = ((int32_t)(stats.frames_ok - frames_seen) > 0);
        hr_detector_estimate_t hr = {0};
        int display_bpm = -1;

        if (!data_fresh) {
            if (!hr_stale) {
                ESP_LOGW(TAG,
                         "no new decimated frames since the last window "
                         "(frames_ok=%u): heart rate shows -- (probe idle or link lost)",
                         (unsigned)stats.frames_ok);
                hr_stale = true;
                hr_detector_reset(&s_hr_detector);
            }
            (void)snprintf(status_line, sizeof(status_line), "HR --");
            (void)display_stub_render_text(APP_TEXT_HR_X, APP_TEXT_HR_Y, status_line);
            ESP_LOGD(TAG, "heart rate: HR -- (stale window, not pushed)");
        } else {
            if (hr_stale) {
                ESP_LOGI(TAG, "new samples after a stall: HR restarts from --");
                hr_stale = false;
            }
            frames_seen = stats.frames_ok;

            int new_events = hr_detector_push(&s_hr_detector, s_ai_window, samples);
            bool hr_ok = false;

            if (new_events < 0) {
                ESP_LOGW(TAG, "hr_detector_push rejected the window (status=%d)",
                         new_events);
            } else if (hr_detector_estimate(&s_hr_detector, &hr) !=
                       HR_DETECTOR_STATUS_OK) {
                ESP_LOGW(TAG, "hr_detector_estimate failed");
            } else {
                hr_ok = true;
            }

            /*
             * Output contract (docs/mvp-output-contract.md section 2): heart
             * rate is an *estimate*. The "~" is required - the offline median
             * error is 1.19 bpm but 13.5 % of recordings are grossly wrong, so
             * "HR 72" without the tilde would over-claim. "HR --" is shown when
             * the detector cannot support a rate (< 4 events or outside
             * 30..220 bpm, enforced inside hr_detector_estimate()).
             */
            if (hr_ok && hr.valid != 0u) {
                display_bpm = (int)(hr.bpm + 0.5f);
                (void)snprintf(status_line, sizeof(status_line), "HR ~%d", display_bpm);
            } else {
                (void)snprintf(status_line, sizeof(status_line), "HR --");
            }
            (void)display_stub_render_text(APP_TEXT_HR_X, APP_TEXT_HR_Y, status_line);

            /* One line per window so the console log and the displayed value
             * can be compared on the first board boot. bpm = -1 means "--". */
            ESP_LOGI(TAG, "heart rate: %s (bpm=%d, events=%u/%u, windows=%u, dup=%u)",
                     status_line, display_bpm,
                     (unsigned)hr.n_events, (unsigned)HR_DETECTOR_EVENT_HISTORY,
                     (unsigned)hr.windows, (unsigned)hr.duplicates);
        }

        ai_stub_status_t ai_status = ai_stub_infer(s_ai_window, samples, &result);
        if (ai_status == AI_STUB_STATUS_NOT_IMPLEMENTED) {
            if ((windows % APP_STATS_EVERY_WINDOWS) == 1u) {
                (void)snprintf(status_line, sizeof(status_line), "murmur: n/a (no model)");
                (void)display_stub_render_text(APP_TEXT_MURMUR_X,
                                               APP_TEXT_MURMUR_Y, status_line);
            }
        } else if (ai_status == AI_STUB_STATUS_OK) {
            (void)snprintf(status_line, sizeof(status_line), "murmur: %.2f",
                           (double)result.murmur_probability);
            (void)display_stub_render_text(APP_TEXT_MURMUR_X,
                                           APP_TEXT_MURMUR_Y, status_line);
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
     *   4. HR        - binds the static work buffer, no malloc
     *   5. BLE       - starts the host task, which starts scanning
     *   6. consumer  - reads the ring the BLE task fills
     */
    ESP_ERROR_CHECK(app_pipeline_init());
    ESP_ERROR_CHECK(display_stub_init());
    ESP_ERROR_CHECK(ai_stub_init());

    if (hr_detector_init(&s_hr_detector, s_hr_work, HR_DETECTOR_WORK_FLOATS, NULL) !=
        HR_DETECTOR_STATUS_OK) {
        ESP_LOGE(TAG, "hr_detector_init failed");
        abort();
    }
    ESP_LOGI(TAG,
             "heart-rate detector ready: %u-sample window, %u-float work buffer, "
             "estimate only (not a diagnosis)",
             (unsigned)HR_DETECTOR_WINDOW_SAMPLES,
             (unsigned)HR_DETECTOR_WORK_FLOATS);

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
