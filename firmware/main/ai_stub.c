#include "ai_stub.h"

#include "esp_log.h"
#include "host_pipeline.h"

static const char *TAG = "ai_stub";

/* Log the "not implemented" line once every N windows instead of 1 Hz spam. */
#define AI_STUB_LOG_INTERVAL 10u

static uint32_t s_call_count;

esp_err_t ai_stub_init(void)
{
    /*
     * TODO(ai): replace this stub with a real on-device backend. The intended
     * sequence, per ADR-007 "下一步" and ADR-008:
     *
     *   1. Train/export the 1D CNN (tools/ai/), freeze the ONNX INT8 artifact
     *      (68.8 KiB, 41,105 parameters) and record its SHA256 in
     *      docs/ai-baseline-plan.md.
     *   2. Convert to an ESP32-S3-runnable format: ESP-DL (preferred, uses the
     *      S3 vector instructions) or TensorFlow Lite Micro. Conversion must
     *      not change the 8,000-sample @ 4 kHz input contract.
     *   3. Embed the model with EMBED_FILES (flash, not .bss) in this
     *      component and run it from a dedicated task pinned to core 1, at
     *      priority above the display task but below the NimBLE host task.
     *   4. Re-measure on the board: peak internal RAM, P95 latency, current,
     *      and BLE frame loss during 5 minutes of concurrent inference.
     *      ADR-007 failure thresholds: P95 > 50 % of the display period,
     *      peak RAM > 70 % of available, or sustained BLE frame loss.
     *   5. Only then change AI_STUB_STATUS_NOT_IMPLEMENTED to a real result.
     *
     * Until step 5 the firmware does not perform inference and must be
     * described that way. No phone/server fallback: ADR-007 revision
     * 2026-10-17 removed it from the design space.
     */
    ESP_LOGI(TAG,
             "AI stub: no model linked. Expected input %u int16 samples @ %u Hz (%.1f s); "
             "planned INT8 model %.1f KiB / 41105 params / 5722944 MAC per window (ADR-007, E4 PC-only)",
             (unsigned)AI_STUB_INPUT_SAMPLES,
             (unsigned)AUDIO_AI_SAMPLE_RATE_HZ,
             (double)AI_STUB_INPUT_SAMPLES / (double)AUDIO_AI_SAMPLE_RATE_HZ,
             (double)AI_STUB_MODEL_INT8_BYTES / 1024.0);
    return ESP_OK;
}

ai_stub_status_t ai_stub_infer(const int16_t *window_4k,
                               size_t sample_count,
                               ai_stub_result_t *result)
{
    if (window_4k == NULL || result == NULL) {
        return AI_STUB_STATUS_INVALID_ARGUMENT;
    }

    s_call_count++;

    result->murmur_probability = -1.0f; /* invalid, never "healthy" */
    result->murmur_present = 2;         /* unknown */
    result->inference_us = 0;
    result->detail = "not implemented: no model linked into this firmware";

    if (sample_count != AI_STUB_INPUT_SAMPLES) {
        /*
         * A wrong window size is a real integration bug (the consumer task and
         * this stub disagree about ADR-007), so it is always logged.
         */
        ESP_LOGE(TAG, "window size %u != required %u samples; refusing",
                 (unsigned)sample_count, (unsigned)AI_STUB_INPUT_SAMPLES);
        result->detail = "invalid window length";
        return AI_STUB_STATUS_INVALID_ARGUMENT;
    }

    if ((s_call_count % AI_STUB_LOG_INTERVAL) == 1u) {
        ESP_LOGW(TAG,
                 "inference not implemented (call %u): %u samples ready, result forced to invalid",
                 (unsigned)s_call_count, (unsigned)sample_count);
    }

    /*
     * Keep the compiler from treating the window pointer as unused while still
     * doing no DSP here: touching the first sample is enough to document that
     * the buffer is real, and costs one load.
     */
    (void)window_4k[0];

    return AI_STUB_STATUS_NOT_IMPLEMENTED;
}
