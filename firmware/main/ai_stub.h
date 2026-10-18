/*
 * ai_stub.h - the inference call site, deliberately NOT an implementation.
 *
 * ADR-008 fixes the MVP route: ESP32-S3, DSP S1/S2, and the existing small 1D
 * CNN for murmur screening; all inference must run on the device (ADR-007
 * revision 2026-10-17 forbids phone/server/cloud fallback). ADR-007's current
 * prototype baseline is:
 *
 *   model             small 1D CNN, 4 convolutional blocks
 *   parameters        41,105
 *   FP32 weights      162.0 KiB
 *   INT8 (ONNX)       68.8 KiB          <-- the number quoted in this stub
 *   MACs / window     5,722,944         (2 s window, 4 kHz, mono int16 in)
 *   input             8,000 int16 samples @ 4 kHz = 2.0 s
 *   step              1.0 s (window overlap 50 %)
 *   evidence level    E4 - measured on a PC, NOT on an ESP32-S3
 *
 * None of that is reproduced here. ai_stub_infer() returns
 * AI_STUB_STATUS_NOT_IMPLEMENTED and writes an explicitly invalid result
 * (probability = -1.0f). Nothing in this file loads, allocates, quantises or
 * executes a model, and the firmware must not be described as "running AI"
 * until this file is replaced with a real ESP-DL / TFLite Micro backend.
 */
#ifndef AI_STUB_H
#define AI_STUB_H

#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

/*
 * Expected model input, in samples, at AUDIO_AI_SAMPLE_RATE_HZ (4 kHz).
 * 8,000 samples = 2.0 s = HOST_PIPELINE_AI_WINDOW_SAMPLES. Kept as its own
 * constant so a future model change fails loudly here instead of silently
 * feeding a differently sized window.
 */
#define AI_STUB_INPUT_SAMPLES 8000u

/* Model artifact size from ADR-007 (INT8 ONNX, prototype baseline). */
#define AI_STUB_MODEL_INT8_BYTES (68.8f * 1024.0f)

typedef enum {
    AI_STUB_STATUS_OK = 0,
    /* No inference engine is linked in yet. This is the current state. */
    AI_STUB_STATUS_NOT_IMPLEMENTED = 1,
    AI_STUB_STATUS_INVALID_ARGUMENT = -1
} ai_stub_status_t;

typedef struct {
    /*
     * P(murmur present) in [0, 1], or a negative value when the result is
     * invalid (AI_STUB_STATUS_NOT_IMPLEMENTED). The display layer must treat
     * negative as "no result", never as "healthy".
     */
    float murmur_probability;
    /* 0 = no murmur, 1 = murmur, 2 = unknown/not computed. */
    uint8_t murmur_present;
    /* Wall-clock duration of the inference call, in microseconds (0 = none). */
    uint32_t inference_us;
    /*
     * Free-text reason, e.g. "not implemented" or "input window short".
     * Never NULL after ai_stub_infer() returns.
     */
    const char *detail;
} ai_stub_result_t;

/* Records the expected contract in the log. Does not allocate or load a model. */
esp_err_t ai_stub_init(void);

/*
 * Run inference on one window.
 *
 * Preconditions:
 *   window_4k != NULL
 *   sample_count == AI_STUB_INPUT_SAMPLES (8,000 int16 samples at 4 kHz)
 *
 * The current implementation validates the arguments, logs at most once per
 * N calls, fills *result with an invalid result and returns
 * AI_STUB_STATUS_NOT_IMPLEMENTED. Callers must not block on it: the eventual
 * real implementation is allowed to take up to the AI window step (1 s) but
 * the current stub returns immediately.
 */
ai_stub_status_t ai_stub_infer(const int16_t *window_4k,
                               size_t sample_count,
                               ai_stub_result_t *result);

#ifdef __cplusplus
}
#endif

#endif /* AI_STUB_H */
