#ifndef HOST_PIPELINE_H
#define HOST_PIPELINE_H

#include "audio_protocol.h"
#include "link_a_protocol.h"

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/*
 * Host-side audio data path: LKA1 packets -> reassembly -> AUD1 validation ->
 * streaming 8 kHz -> 4 kHz decimation -> ring buffer of decimated frames.
 *
 * The pipeline owns no heap memory. The caller owns both the pipeline struct
 * and the decimated-frame ring storage, and passes them to
 * host_pipeline_init(). All state is plain data, so the pipeline also works on
 * an embedded host MCU and is easy to snapshot in a debugger.
 *
 * ---------------------------------------------------------------------------
 * Design notes (engineering decisions, per docs/engineering-decision-guide.md)
 * ---------------------------------------------------------------------------
 * Evidence levels used below: "primary" = standards/datasheets/official repo
 * source or official measurements, "independent" = reproducible paper/public
 * experiment, "estimate" = calculation or engineering judgement.
 *
 * D1. Decimator arithmetic: int16 Q15 FIR (chosen) vs float32 FIR.
 *     - The coefficients are the same 63-tap Blackman-windowed sinc used by
 *       tools/audio-sim/lib/dsp.mjs (cutoff 1700 Hz at 8 kHz), quantised to
 *       Q15 offline and with the centre tap corrected so the coefficient sum
 *       is exactly 32768. Source: repository reference implementation
 *       (primary for this project); response verified by the C unit tests.
 *     - Measured Q15 response (independent, reproduced in tests): 440 Hz gain
 *       1.0003 (+0.002 dB), 1900 Hz gain 0.0436 (-27.2 dB). Node acceptance
 *       thresholds are >0.7 passband and <0.35 stopband, so Q15 has >3x margin.
 *     - Cost (estimate): 63 multiply-accumulates per output sample plus a
 *       126-byte history shift per input sample, i.e. ~0.5 MMAC/s and
 *       ~1 MB/s at 8 kHz. Negligible on a phone/server host and acceptable on
 *       a 48 MHz Cortex-M0+; float32 would need an FPU or soft-float and its
 *       rounding would differ between toolchains.
 *     - Tradeoff: Q15 cannot be bit-identical to the Node float64 output
 *       (quantisation error ~0.03 %). Determinism across runs and toolchains is
 *       more valuable than bit-identity with a simulator, so Q15 wins.
 *     - Fallback: replace host_pipeline_decimator_process() with float32 if a
 *       target has no fast 64-bit multiply-accumulate; keep the same
 *       coefficients and the same test thresholds.
 *
 * D2. Filter shape: reuse the Node 63-tap/1700 Hz design (chosen) vs a short
 *     half-band filter (e.g. 31 taps). Reuse gives one filter reference for
 *     Node and C and preserves the 1900 Hz rejection margin; 31 taps widens
 *     the transition band so 1900 Hz would no longer be safely rejected.
 *     Fallback: retune cutoff/taps in both implementations and re-run both
 *     test suites (the C test measures the response directly).
 *
 * D3. Ring storage: caller-owned flat int16_t buffer (chosen) vs malloc vs a
 *     linked list of frame records. Fixed memory, no fragmentation, contiguous
 *     reads, and trivially testable. Cost: the caller must size the buffer.
 *     Fallback: a static-capacity wrapper in the host application.
 *
 * D4. Session isolation: reject packets from recently superseded session IDs
 *     (chosen) vs reset-on-change only vs accepting only START fragments for
 *     an unknown session.
 *     - Reset-on-change alone lets one late old-session fragment pull the
 *       reassembler back to the old session, which discards the new session's
 *       partial frame and stalls it.
 *     - Requiring fragment 0 first would drop legitimate out-of-order first
 *       fragments (required by test 1).
 *     - Chosen: keep the last HOST_PIPELINE_SESSION_HISTORY superseded IDs and
 *       drop those packets as stale. Risk: a probe that reuses an ID retired
 *       fewer than 4 sessions ago is treated as stale until another session
 *       appears; host_pipeline_reset() recovers. The probe is responsible for
 *       using a new session ID per session (that is the purpose of the
 *       session_id field in link_a_protocol.h).
 *
 * D5. AUD1 validation: reuse audio_protocol_decode_frame() (chosen) vs an
 *     inline CRC + header check. Reuse keeps one decoder and validates magic,
 *     version, codec, channels, rate, sample count and payload size as well as
 *     the CRC. Cost: a second parse of a 352-byte frame and a 336-byte stack
 *     frame; both are acceptable for a 20 ms frame rate.
 *     Fallback: inline the CRC-only check if stack is tight.
 *
 * D6. host_pipeline_read_newest(): all-or-nothing (chosen) vs partial fill.
 *     An AI window must have a fixed length; returning 0 when fewer frames are
 *     available prevents silently feeding a short window to the model.
 *
 * D7. Default ring capacity: 32 frames (as specified). 32 frames x 80 samples
 *     = 2560 samples = 0.64 s at 4 kHz. The task brief says "2.56 s", which is
 *     arithmetically inconsistent (it assumes 1000 samples/s); a 2.56 s ring
 *     would need 128 frames. The 2 s AI window is 8000 samples = 100 frames,
 *     so a host that feeds the model must initialise with capacity >=
 *     HOST_PIPELINE_AI_WINDOW_FRAMES. The default stays 32 as requested.
 *
 * Threading: the pipeline is not thread-safe. The caller must serialise
 * host_pipeline_push() against host_pipeline_read_newest()/clear()/reset(),
 * for example with one lock around the BLE receive callback and the AI worker.
 *
 * Runtime floating point: none. Floating point is used only in the test
 * program to synthesise sine waves and measure RMS; the library itself is
 * integer-only.
 */

#define HOST_PIPELINE_DECIMATION_FACTOR 2u

/* 80 decimated samples per 20 ms AUD1 frame, i.e. 4 kHz. */
#define HOST_PIPELINE_DECIMATED_FRAME_SAMPLES \
    (AUDIO_FRAME_SAMPLES / HOST_PIPELINE_DECIMATION_FACTOR)

#define HOST_PIPELINE_DECIMATOR_TAPS 63u

/* "capacity 0 at init" selects this default. */
#define HOST_PIPELINE_DEFAULT_RING_FRAMES 32u

/* 2 s AI window: 8000 samples at 4 kHz = 100 decimated frames. */
#define HOST_PIPELINE_AI_WINDOW_SAMPLES (AUDIO_AI_SAMPLE_RATE_HZ * 2u)
#define HOST_PIPELINE_AI_WINDOW_FRAMES \
    (HOST_PIPELINE_AI_WINDOW_SAMPLES / HOST_PIPELINE_DECIMATED_FRAME_SAMPLES)

/* Number of superseded session IDs remembered to reject stragglers. */
#define HOST_PIPELINE_SESSION_HISTORY 4u

/*
 * Status values. Non-negative values mean "no fault"; negative values are
 * errors. The values intentionally follow the link-A convention.
 */
typedef enum {
    /* Also used by init/reset. */
    HOST_PIPELINE_STATUS_OK = 0,

    /* Fragment accepted; the logical frame still needs more fragments. */
    HOST_PIPELINE_STATUS_INCOMPLETE = 1,

    /* A complete AUD1 frame was validated, decimated and stored. */
    HOST_PIPELINE_STATUS_COMPLETE = 2,

    /* Duplicate fragment suppressed; no frame was produced by this call. */
    HOST_PIPELINE_STATUS_DUPLICATE = 3,

    /* Fragment started a new session (state was reset), frame incomplete. */
    HOST_PIPELINE_STATUS_SESSION_RESTARTED = 4,

    /* First packet of a new session also completed its AUD1 frame. */
    HOST_PIPELINE_STATUS_COMPLETE_NEW_SESSION = 5,

    /* Non-data LKA1 packet (ACK/NACK) ignored. */
    HOST_PIPELINE_STATUS_IGNORED = 6,

    /* Packet belongs to a superseded session and was dropped. */
    HOST_PIPELINE_STATUS_STALE_SESSION = 7,

    HOST_PIPELINE_STATUS_INVALID_ARGUMENT = -1,

    /* LKA1 framing/CRC/format error or reassembler conflict; packet_errors. */
    HOST_PIPELINE_STATUS_BAD_PACKET = -2,

    /* AUD1 CRC failed; crc_errors was incremented. */
    HOST_PIPELINE_STATUS_BAD_AUDIO_CRC = -3,

    /* AUD1 header/format field invalid; format_errors was incremented. */
    HOST_PIPELINE_STATUS_BAD_AUDIO_FORMAT = -4
} host_pipeline_status_t;

/*
 * Read-only view of pipeline counters. The struct is returned by
 * host_pipeline_get_stats(); copy it if a consistent snapshot is needed.
 * Counters wrap modulo 2^32 (about 994 days at 50 packets/s).
 */
typedef struct {
    uint32_t packets_in;        /* push() calls with valid arguments        */
    uint32_t packet_errors;     /* packets rejected by LKA1 framing/CRC     */
    uint32_t frames_ok;         /* valid AUD1 frames stored in the ring     */
    uint32_t crc_errors;        /* AUD1 frames rejected on CRC              */
    uint32_t format_errors;     /* AUD1 header/format rejections            */
    uint32_t frames_dropped;    /* logical frames abandoned incomplete      */
    uint32_t overruns;          /* stored frames overwritten (ring full)    */
    uint32_t stale_packets;     /* packets dropped as old-session stragglers*/
    uint32_t session_restarts;  /* observed session ID changes              */
    uint32_t last_sequence;     /* sequence of the last valid AUD1 frame    */
    uint16_t current_session_id;/* active session, 0 before the first packet*/
} host_pipeline_stats_t;

/*
 * Streaming 2:1 decimator state. history[0] is the newest input sample,
 * history[i] is x[n-i], matching the Node StreamingDecimator8kTo4k.
 */
typedef struct {
    int16_t history[HOST_PIPELINE_DECIMATOR_TAPS];
    uint8_t phase;
} host_pipeline_decimator_t;

/*
 * Caller-owned pipeline. Treat the fields as private; use the accessors below.
 * The struct is public only because the caller allocates it.
 */
typedef struct {
    link_a_reassembler_t reassembler;
    host_pipeline_decimator_t decimator;

    int16_t *ring;                 /* caller storage, frames*80 samples      */
    size_t ring_capacity_frames;
    size_t ring_count;             /* frames currently stored                */
    size_t ring_write_frame;       /* slot for the next frame                */

    uint16_t current_session_id;
    uint16_t recent_sessions[HOST_PIPELINE_SESSION_HISTORY];
    uint8_t recent_session_count;
    uint8_t session_active;

    uint8_t frame_open;            /* a logical frame is partially received  */
    uint32_t open_frame_sequence;

    host_pipeline_stats_t stats;
} host_pipeline_t;

/*
 * Initialise a caller-owned pipeline.
 *
 * ring_storage must point to at least ring_capacity_frames *
 * HOST_PIPELINE_DECIMATED_FRAME_SAMPLES int16_t values. Passing 0 for
 * ring_capacity_frames selects HOST_PIPELINE_DEFAULT_RING_FRAMES. The ring is
 * zeroed so the initial state is deterministic.
 *
 * Returns HOST_PIPELINE_STATUS_OK or HOST_PIPELINE_STATUS_INVALID_ARGUMENT.
 */
int host_pipeline_init(
    host_pipeline_t *pipeline,
    int16_t *ring_storage,
    size_t ring_capacity_frames);

/*
 * Feed exactly one complete LKA1 packet (header + payload + CRC).
 *
 * Returns one of:
 *   COMPLETE / COMPLETE_NEW_SESSION  one valid AUD1 frame stored
 *   INCOMPLETE                       more fragments needed
 *   DUPLICATE                        fragment already seen, no frame
 *   SESSION_RESTARTED                new session detected, frame incomplete
 *   STALE_SESSION                    packet from a superseded session
 *   IGNORED                          ACK/NACK packet
 *   BAD_PACKET                       LKA1/format/reassembly error
 *   BAD_AUDIO_CRC / BAD_AUDIO_FORMAT completed frame rejected by AUD1 checks
 *   INVALID_ARGUMENT                 pipeline/packet NULL or length 0
 *
 * A session ID change resets the reassembler, decimator and ring before the
 * packet is processed, so no old-session sample can enter the new stream.
 */
int host_pipeline_push(
    host_pipeline_t *pipeline,
    const uint8_t *packet_data,
    size_t packet_length);

/* Read-only statistics; NULL if pipeline is NULL. */
const host_pipeline_stats_t *host_pipeline_get_stats(
    const host_pipeline_t *pipeline);

/* Frames currently available (0..capacity). */
size_t host_pipeline_frames_available(const host_pipeline_t *pipeline);

/* Configured ring capacity in frames. */
size_t host_pipeline_frames_capacity(const host_pipeline_t *pipeline);

/*
 * Copy the newest frame_count frames, oldest first, into output.
 * output must hold frame_count * HOST_PIPELINE_DECIMATED_FRAME_SAMPLES
 * int16_t values. Returns frame_count on success, or 0 if frame_count is 0,
 * exceeds the available frames, or an argument is invalid (all-or-nothing).
 */
size_t host_pipeline_read_newest(
    const host_pipeline_t *pipeline,
    int16_t *output,
    size_t frame_count);

/*
 * Convenience wrapper for AI windows: sample_count must be a non-zero multiple
 * of HOST_PIPELINE_DECIMATED_FRAME_SAMPLES. Returns the number of samples
 * copied (sample_count), or 0 on failure.
 */
size_t host_pipeline_read_newest_samples(
    const host_pipeline_t *pipeline,
    int16_t *output,
    size_t sample_count);

/*
 * Drop all buffered frames (ring becomes empty). Counters are preserved, and
 * the reassembler/decimator state is untouched, so the next frame continues
 * the stream. Use host_pipeline_reset() to clear everything.
 */
void host_pipeline_clear(host_pipeline_t *pipeline);

/*
 * Full reset: reassembler, decimator, ring, session history and statistics.
 * The caller's ring storage pointer/capacity are retained and the storage is
 * zeroed, so the pipeline behaves exactly like a freshly initialised one.
 */
void host_pipeline_reset(host_pipeline_t *pipeline);

/* Reset the decimator to its initial (zero-history, phase 0) state. */
void host_pipeline_decimator_init(host_pipeline_decimator_t *decimator);

/*
 * Process input_samples mono int16 samples at 8 kHz and write 4 kHz samples to
 * output. output must hold at least (input_samples + 1) / 2 int16_t values.
 * State (history and phase) persists across calls, so frame boundaries do not
 * introduce discontinuities. Returns the number of samples written.
 */
size_t host_pipeline_decimator_process(
    host_pipeline_decimator_t *decimator,
    const int16_t *input,
    size_t input_samples,
    int16_t *output);

/* Compile-time guards for the fixed baseline. */
#define HOST_PIPELINE_STATIC_ASSERT(condition, name) \
    typedef char host_pipeline_static_assert_##name[(condition) ? 1 : -1]

HOST_PIPELINE_STATIC_ASSERT(
    AUDIO_FRAME_SAMPLES % HOST_PIPELINE_DECIMATION_FACTOR == 0u,
    frame_samples_are_even);
HOST_PIPELINE_STATIC_ASSERT(
    HOST_PIPELINE_DECIMATED_FRAME_SAMPLES == 80u,
    decimated_frame_is_80_samples);
HOST_PIPELINE_STATIC_ASSERT(
    HOST_PIPELINE_AI_WINDOW_SAMPLES == 8000u,
    ai_window_is_8000_samples);
HOST_PIPELINE_STATIC_ASSERT(
    HOST_PIPELINE_AI_WINDOW_FRAMES == 100u,
    ai_window_is_100_frames);
HOST_PIPELINE_STATIC_ASSERT(
    HOST_PIPELINE_DECIMATOR_TAPS % 2u == 1u,
    decimator_taps_are_odd);

#undef HOST_PIPELINE_STATIC_ASSERT

#ifdef __cplusplus
}
#endif

#endif
