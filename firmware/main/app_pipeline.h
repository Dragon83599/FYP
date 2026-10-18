/*
 * app_pipeline.h - the one thread-safe entry point to the portable data path.
 *
 * Owner: firmware/main. This module wraps the shared host_pipeline_t (ADR-014)
 * because that library is explicitly *not* thread-safe: the BLE host task calls
 * host_pipeline_push() while the AI/display consumer task calls
 * host_pipeline_read_newest_samples(). ADR-014 lists this as risk (1) and
 * requires the caller to serialise the two. Doing it in exactly one place keeps
 * the lock discipline auditable and keeps host_pipeline itself unmodified.
 *
 * Memory: the ring buffer is static, sized at compile time. No heap, no PSRAM.
 *
 *   160 frames x 80 samples x 2 B = 25,600 B of .bss
 *
 * Ring capacity choice (why 160 and not the library default of 32):
 *   - 32 frames (library default) = 0.64 s < 2 s AI window: the AI could never
 *     read a full window. ADR-014 D7 already flags this.
 *   - 100 frames (exact 2 s window) works but leaves no slack: the consumer
 *     runs on a 1 s step (ADR-007: 2 s window, 1 s step), so up to 50 new
 *     frames arrive between two reads; a slow read would overwrite frames the
 *     consumer still needs and would bump the overrun counter on every cycle.
 *   - 160 frames = 3.2 s = 2 s window + 1 s step + 0.2 s scheduling jitter.
 *     Cost is 9,600 B more .bss than the minimum; no runtime cost.
 *   - Evidence level: engineering calculation (E4) from the frozen frame sizes
 *     in audio_protocol.h. Revisit if ADR-007 changes the window or step.
 *
 * Locking rules:
 *   - app_pipeline_push_packet() and app_pipeline_read_window() may be called
 *     from any FreeRTOS task, never from an ISR.
 *   - Neither call holds the lock while doing I/O or logging, so the BLE host
 *     task is blocked for microseconds per LKA1 packet (one 352 B frame at
 *     50 Hz), not for the duration of a display update.
 *   - If the lock cannot be taken within APP_PIPELINE_LOCK_TIMEOUT_MS the call
 *     fails and increments a counter instead of blocking the Bluetooth stack
 *     forever. A non-zero counter here means the consumer is holding the ring
 *     for too long and is a bug to investigate, not to paper over.
 */
#ifndef APP_PIPELINE_H
#define APP_PIPELINE_H

#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"
#include "host_pipeline.h"

#ifdef __cplusplus
extern "C" {
#endif

/*
 * 160 decimated frames = 3.2 s at 4 kHz (see the capacity discussion above).
 * Must be >= HOST_PIPELINE_AI_WINDOW_FRAMES (100) or the AI window can never
 * be filled; that is enforced at compile time in app_pipeline.c.
 */
#define APP_PIPELINE_RING_FRAMES 160u

/* Bytes of static storage consumed by the ring (documentation only). */
#define APP_PIPELINE_RING_BYTES \
    (APP_PIPELINE_RING_FRAMES * HOST_PIPELINE_DECIMATED_FRAME_SAMPLES * \
     sizeof(int16_t))

/* 2 s AI window at 4 kHz: 8,000 int16 samples = 100 frames (ADR-007). */
#define APP_PIPELINE_WINDOW_SAMPLES HOST_PIPELINE_AI_WINDOW_SAMPLES

/* How long a caller waits for the ring lock before giving up. */
#define APP_PIPELINE_LOCK_TIMEOUT_MS 50u

/*
 * Allocate the static mutex and initialise the pipeline and its ring.
 * Safe to call once from app_main() before any task uses the pipeline.
 * Returns ESP_ERR_INVALID_STATE if called twice.
 */
esp_err_t app_pipeline_init(void);

/*
 * Feed one complete LKA1 packet (20 B header + payload + 2 B CRC) to the
 * pipeline. Returns one of the host_pipeline_status_t values:
 *   >= 0  a valid packet was consumed (COMPLETE means one AUD1 frame was
 *         validated, decimated and stored)
 *   < 0   the packet was rejected (bad CRC, bad framing, stale session, ...)
 * A negative return is normal on a noisy link and must not stop the caller.
 * Returns HOST_PIPELINE_STATUS_INVALID_ARGUMENT if the module is not
 * initialised or the lock timed out.
 */
int app_pipeline_push_packet(const uint8_t *packet_data, size_t packet_length);

/*
 * Copy the newest `sample_count` decimated samples (oldest first) into output.
 * sample_count must be a non-zero multiple of 80 and <= the ring capacity.
 * Returns the number of samples copied, or 0 when fewer than sample_count
 * samples are buffered yet (all-or-nothing, ADR-014 D6) or on a lock timeout.
 */
size_t app_pipeline_read_window(int16_t *output, size_t sample_count);

/* Number of complete decimated frames currently buffered. */
size_t app_pipeline_frames_available(void);

/* Ring capacity in frames (compile-time constant, exposed for logging). */
size_t app_pipeline_frames_capacity(void);

/* Copy the pipeline counters under the lock (torn-free snapshot). */
void app_pipeline_get_stats(host_pipeline_stats_t *out);

/*
 * Counts push/read calls that failed to take the lock within
 * APP_PIPELINE_LOCK_TIMEOUT_MS. Expected to stay 0.
 */
uint32_t app_pipeline_lock_misses(void);

#ifdef __cplusplus
}
#endif

#endif /* APP_PIPELINE_H */
