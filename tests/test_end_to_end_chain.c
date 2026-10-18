/*
 * End-to-end receive-chain test: one signal, every stage, no hardware.
 *
 *   int16 PCM at 8 kHz
 *     -> audio_protocol_encode_frame()            (AUD1, 352 B, 160 samples)
 *     -> link_a_fragment_count/encode_frame_fragment()  (LKA1 fragments)
 *     -> host_pipeline_push()                     (reassembly + AUD1 CRC)
 *     -> host_pipeline_read_newest_samples()      (8 kHz -> 4 kHz Q15 +
 *                                                  100-frame ring)
 *     -> hr_detector_push_hop()                   (2 s window, 1 s step)
 *     -> hr_detector_estimate()                   (bpm)
 *
 * Every stage has its own unit suite (audio_protocol, link_a_protocol,
 * host_pipeline, hr_detector). This test exists to prove they *compose*:
 *
 *   1. synthetic known-rate: a PCG signal with a known rate produces that rate
 *      after the whole chain, for 60/90/130 bpm.
 *   2. transport integrity: the 4 kHz samples that come out of the chain are
 *      bit-identical to a direct host_pipeline_decimator_process() run over the
 *      same 8 kHz input, so fragmentation/reassembly/CRC cannot silently alter
 *      audio.
 *   3. loss tolerance: dropping the occasional LKA1 fragment still delivers
 *      frames, raises frames_dropped, and leaves the heart rate inside +-5 bpm.
 *   4. session isolation: a mid-stream session_id change rejects stale packets
 *      and the detector (reset on stats.session_restarts, as the firmware does)
 *      recomputes the rate from post-session audio only.
 *   5. overlap: the ring's 2 s window read every 1 s (the firmware cadence)
 *      does not double the rate, because hr_detector de-duplicates events.
 *   6. real data: when data/processed/circor/recordings.csv is present, 20
 *      recordings are upsampled 4 kHz -> 8 kHz and pushed through the chain;
 *      the chain heart rate must match a detector fed the original 4 kHz
 *      samples directly within +-2 bpm. Absent data prints a skip message so
 *      CI without the corpus still passes.
 *
 * The synthetic signals are generated in C (no binary fixtures, no Python
 * helper): the signal is the same synthetic PCG model as
 * tests/test_hr_detector.c, evaluated at 8 kHz instead of 4 kHz.
 *
 * Plain C99, no framework; failures are counted and main() returns non-zero.
 * Buffers are static; malloc is not used. The test uses floating point only to
 * synthesise signals and to print rates.
 */

#include "audio_protocol.h"
#include "host_pipeline.h"
#include "hr_detector.h"
#include "link_a_protocol.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifndef TEST_E2E_SOURCE_DIR
#define TEST_E2E_SOURCE_DIR "."
#endif

#define E2E_PI 3.14159265358979323846

/* Longest synthetic run is 18 s (6 s session A + 12 s session B). */
#define E2E_MAX_SECONDS 24u
#define E2E_MAX_8K_SAMPLES (E2E_MAX_SECONDS * AUDIO_SAMPLE_RATE_HZ)

/* Real recordings are truncated to at most 20 s before upsampling. */
#define E2E_MAX_RECORDING_SECONDS 20u
#define E2E_MAX_4K_SAMPLES \
    (E2E_MAX_RECORDING_SECONDS * AUDIO_AI_SAMPLE_RATE_HZ)

/* 128 frames = 2.56 s at 4 kHz; the AI window needs the newest 100. */
#define E2E_RING_FRAMES 128u

#if E2E_RING_FRAMES < HOST_PIPELINE_AI_WINDOW_FRAMES
#error "E2E_RING_FRAMES must hold a 2 s AI window"
#endif

/* 20 (LKA1 header) + 222 (fragment payload) + 2 (CRC) = 244 on the wire. */
#define E2E_PACKET_BYTES \
    (LINK_A_WIRE_OVERHEAD_BYTES + LINK_A_DEFAULT_LINK_PAYLOAD_BYTES)

/* Firmware cadence: 2 s window, 1 s step = 50 decimated frames. */
#define E2E_FIRMWARE_STEP_FRAMES \
    (AUDIO_AI_SAMPLE_RATE_HZ / HOST_PIPELINE_DECIMATED_FRAME_SAMPLES)

#define E2E_PATH_BYTES 1024u
/* Full path = source dir + corpus-relative path; leave headroom. */
#define E2E_FULL_PATH_BYTES (E2E_PATH_BYTES + 256u)

static int failures = 0;

#define CHECK(condition)                                                       \
    do {                                                                       \
        if (!(condition)) {                                                    \
            ++failures;                                                        \
            fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #condition); \
        }                                                                      \
    } while (0)

/* ------------------------------------------------------------------------- */
/* Static storage                                                             */
/* ------------------------------------------------------------------------- */

static int16_t e2e_ring[
    E2E_RING_FRAMES * HOST_PIPELINE_DECIMATED_FRAME_SAMPLES];
static int16_t e2e_window[HOST_PIPELINE_AI_WINDOW_SAMPLES];
static int16_t e2e_signal[E2E_MAX_8K_SAMPLES];
static int16_t e2e_wav4k[E2E_MAX_4K_SAMPLES];
static int16_t e2e_reference[
    (E2E_MAX_8K_SAMPLES / AUDIO_FRAME_SAMPLES) *
    HOST_PIPELINE_DECIMATED_FRAME_SAMPLES];
static float e2e_work_chain[HR_DETECTOR_WORK_FLOATS];
static float e2e_work_direct[HR_DETECTOR_WORK_FLOATS];

/* ------------------------------------------------------------------------- */
/* Synthetic PCG (same model as tests/test_hr_detector.c, evaluated at 8 kHz) */
/* ------------------------------------------------------------------------- */

/* Deterministic noise (no rand(), so the suite is reproducible everywhere). */
static double e2e_uniform(uint32_t *state)
{
    *state = *state * 1664525u + 1013904223u;
    return (double)(*state >> 8) / 8388608.0 - 1.0;
}

static void e2e_add_burst(
    int16_t *signal,
    size_t count,
    double sample_rate,
    double centre_s,
    double duration_s,
    double frequency_hz,
    double amplitude)
{
    size_t half = (size_t)(0.5 * duration_s * sample_rate);
    long centre = (long)(centre_s * sample_rate);
    long start = centre - (long)half;
    long end = centre + (long)half;
    long i;

    for (i = start; i <= end; ++i) {
        double offset;
        double window;
        double value;

        if (i < 0 || i >= (long)count) {
            continue;
        }
        offset = (double)(i - start) / (double)(end - start);
        window = 0.5 - 0.5 * cos(2.0 * E2E_PI * offset);
        value = amplitude * window
                * sin(2.0 * E2E_PI * frequency_hz * (double)i / sample_rate);
        value += (double)signal[i];
        if (value > 32767.0) {
            value = 32767.0;
        }
        if (value < -32768.0) {
            value = -32768.0;
        }
        signal[i] = (int16_t)lround(value);
    }
}

/*
 * One S1 (41 Hz) and one S2 (62 Hz) burst per cardiac cycle, deterministic
 * broadband noise and a DC offset. s1_s2_s is scaled with the cycle so the
 * two sounds stay resolvable at 130 bpm (21.6 / bpm seconds, i.e. 0.36 of a
 * cycle: 0.36 s at 60 bpm, 0.24 s at 90, 0.166 s at 130).
 */
static void e2e_synth_pcg(
    int16_t *signal,
    size_t count,
    double bpm,
    double amplitude,
    double noise)
{
    double sample_rate = (double)AUDIO_SAMPLE_RATE_HZ;
    double cycle = 60.0 / bpm;
    double s1_s2_s = 21.6 / bpm;
    double duration = (double)count / sample_rate;
    double time = 0.30;
    uint32_t state = 20261018u;
    size_t i;

    memset(signal, 0, count * sizeof(int16_t));
    for (i = 0u; i < count; ++i) {
        signal[i] = (int16_t)lround(e2e_uniform(&state) * noise);
    }
    while (time + s1_s2_s < duration) {
        e2e_add_burst(
            signal, count, sample_rate, time, 0.075, 41.0, amplitude);
        e2e_add_burst(
            signal, count, sample_rate, time + s1_s2_s, 0.055, 62.0,
            amplitude * 0.62);
        time += cycle;
    }
    for (i = 0u; i < count; ++i) {
        long value = (long)signal[i] + 400;

        if (value > 32767) {
            value = 32767;
        }
        if (value < -32768) {
            value = -32768;
        }
        signal[i] = (int16_t)value;
    }
}

/* ------------------------------------------------------------------------- */
/* Chain harness: 8 kHz frame -> AUD1 -> LKA1 -> host_pipeline -> detector    */
/* ------------------------------------------------------------------------- */

typedef struct {
    host_pipeline_t pipeline;
    hr_detector_t detector;
    uint16_t session_id;
    uint32_t next_sequence;
    uint32_t step_frames;      /* ring frames between AI windows (50 = 1 s) */
    uint32_t loss_period;      /* 0 = reliable; else drop every Nth packet   */
    uint32_t packets_sent;
    uint32_t packets_dropped;
    uint32_t session_frames;   /* frames stored in the current session       */
    uint32_t last_serviced_frames;
    uint32_t restarts_seen;
    uint32_t detector_pushes;
    uint32_t detector_resets;
    uint32_t rejected_packets;
    uint32_t short_reads;
} e2e_chain_t;

static void e2e_chain_init(
    e2e_chain_t *chain,
    uint16_t session_id,
    float *work,
    size_t work_floats,
    uint32_t step_frames,
    uint32_t loss_period)
{
    memset(chain, 0, sizeof(*chain));
    chain->session_id = session_id;
    chain->next_sequence = 1u;
    chain->step_frames = step_frames;
    chain->loss_period = loss_period;
    CHECK(host_pipeline_init(&chain->pipeline, e2e_ring, E2E_RING_FRAMES) ==
          HOST_PIPELINE_STATUS_OK);
    CHECK(hr_detector_init(&chain->detector, work, work_floats, NULL) ==
          HR_DETECTOR_STATUS_OK);
}

/*
 * Firmware restart handling: host_pipeline counts every session change in
 * stats.session_restarts; the consumer polls that counter and resets the
 * detector so no event time from the old session survives. Polling after each
 * packet is only more prompt than the firmware's once-per-window poll.
 */
static void e2e_chain_poll_restart(e2e_chain_t *chain)
{
    const host_pipeline_stats_t *stats =
        host_pipeline_get_stats(&chain->pipeline);

    if (stats->session_restarts != chain->restarts_seen) {
        chain->restarts_seen = stats->session_restarts;
        chain->session_frames = 0u;
        chain->last_serviced_frames = 0u;
        hr_detector_reset(&chain->detector);
        ++chain->detector_resets;
    }
}

/* Encode one AUD1 frame and push all of its LKA1 fragments. */
static int e2e_chain_push_frame(e2e_chain_t *chain, const int16_t *samples8k)
{
    audio_frame_t frame;
    uint8_t wire[AUDIO_WIRE_FRAME_BYTES];
    uint8_t packet[E2E_PACKET_BYTES];
    size_t wire_length = 0u;
    size_t fragments;
    size_t fragment;
    int last_status = HOST_PIPELINE_STATUS_INCOMPLETE;
    int stored = 0;

    memset(&frame, 0, sizeof(frame));
    frame.sample_rate_hz = AUDIO_SAMPLE_RATE_HZ;
    frame.timestamp_ms = chain->next_sequence * AUDIO_FRAME_DURATION_MS;
    frame.sequence = chain->next_sequence;
    frame.channel_count = AUDIO_CHANNEL_COUNT;
    memcpy(frame.samples, samples8k, sizeof(frame.samples));

    if (audio_protocol_encode_frame(
            wire,
            sizeof(wire),
            &frame,
            AUDIO_FLAG_TIMESTAMP_VALID,
            &wire_length) != AUDIO_STATUS_OK ||
        wire_length != AUDIO_WIRE_FRAME_BYTES) {
        CHECK(0);
        ++chain->next_sequence;
        return HOST_PIPELINE_STATUS_INVALID_ARGUMENT;
    }

    fragments = link_a_fragment_count(
        AUDIO_WIRE_FRAME_BYTES, LINK_A_DEFAULT_MAX_FRAGMENT_PAYLOAD);
    for (fragment = 0u; fragment < fragments; ++fragment) {
        size_t packet_length = 0u;
        int status;

        if (link_a_encode_frame_fragment(
                packet,
                sizeof(packet),
                wire,
                wire_length,
                chain->session_id,
                chain->next_sequence,
                (uint8_t)fragment,
                LINK_A_DEFAULT_MAX_FRAGMENT_PAYLOAD,
                &packet_length) != LINK_A_STATUS_OK ||
            packet_length == 0u) {
            CHECK(0);
            continue;
        }

        ++chain->packets_sent;
        if (chain->loss_period != 0u &&
            (chain->packets_sent % chain->loss_period) == 0u) {
            ++chain->packets_dropped;
            continue;
        }

        status = host_pipeline_push(
            &chain->pipeline, packet, packet_length);
        if (status < 0) {
            ++chain->rejected_packets;
        }
        if (status == HOST_PIPELINE_STATUS_COMPLETE ||
            status == HOST_PIPELINE_STATUS_COMPLETE_NEW_SESSION) {
            stored = 1;
        }
        last_status = status;

        e2e_chain_poll_restart(chain);
        if (stored != 0) {
            ++chain->session_frames;
            stored = 0;
        }
    }

    ++chain->next_sequence;
    return last_status;
}

/*
 * Consumer side of the firmware loop: once a full 2 s window is in the ring,
 * read the newest 8000 samples every step_frames (50 = 1 s) and push it with
 * the matching hop. host_pipeline_read_newest_samples is all-or-nothing.
 */
static void e2e_chain_service(e2e_chain_t *chain)
{
    size_t samples;

    if (chain->session_frames < HOST_PIPELINE_AI_WINDOW_FRAMES) {
        return;
    }
    if (chain->session_frames - chain->last_serviced_frames <
        chain->step_frames) {
        return;
    }
    if (host_pipeline_frames_available(&chain->pipeline) <
        HOST_PIPELINE_AI_WINDOW_FRAMES) {
        ++chain->short_reads;
        chain->last_serviced_frames = chain->session_frames;
        return;
    }

    samples = host_pipeline_read_newest_samples(
        &chain->pipeline, e2e_window, HOST_PIPELINE_AI_WINDOW_SAMPLES);
    if (samples != HOST_PIPELINE_AI_WINDOW_SAMPLES) {
        ++chain->short_reads;
        return;
    }
    if (hr_detector_push_hop(
            &chain->detector,
            e2e_window,
            samples,
            (size_t)chain->step_frames *
                HOST_PIPELINE_DECIMATED_FRAME_SAMPLES) < 0) {
        ++chain->rejected_packets;
    }
    chain->last_serviced_frames = chain->session_frames;
    ++chain->detector_pushes;
}

typedef struct {
    hr_detector_estimate_t estimate;
    host_pipeline_stats_t stats;
    uint32_t packets_sent;
    uint32_t packets_dropped;
    uint32_t detector_pushes;
    uint32_t detector_resets;
    uint32_t rejected_packets;
    uint32_t short_reads;
    uint32_t session_frames;
} e2e_result_t;

static void e2e_chain_result(const e2e_chain_t *chain, e2e_result_t *result)
{
    memset(result, 0, sizeof(*result));
    (void)hr_detector_estimate(&chain->detector, &result->estimate);
    result->stats = *host_pipeline_get_stats(&chain->pipeline);
    result->packets_sent = chain->packets_sent;
    result->packets_dropped = chain->packets_dropped;
    result->detector_pushes = chain->detector_pushes;
    result->detector_resets = chain->detector_resets;
    result->rejected_packets = chain->rejected_packets;
    result->short_reads = chain->short_reads;
    result->session_frames = chain->session_frames;
}

/* Push frame_count 160-sample frames through the chain, servicing windows. */
static void e2e_run_chain(
    e2e_chain_t *chain,
    const int16_t *signal8k,
    uint32_t frame_count,
    e2e_result_t *result)
{
    uint32_t frame;

    for (frame = 0u; frame < frame_count; ++frame) {
        (void)e2e_chain_push_frame(
            chain,
            &signal8k[(size_t)frame * AUDIO_FRAME_SAMPLES]);
        e2e_chain_service(chain);
    }
    e2e_chain_result(chain, result);
}

/* Detector fed the original 4 kHz samples directly, same 2 s / 1 s cadence. */
static void e2e_run_direct_detector(
    const int16_t *samples4k,
    size_t count4k,
    float *work,
    hr_detector_estimate_t *estimate,
    uint32_t *pushes)
{
    hr_detector_t detector;
    size_t start;

    *pushes = 0u;
    memset(estimate, 0, sizeof(*estimate));
    if (hr_detector_init(&detector, work, HR_DETECTOR_WORK_FLOATS, NULL) !=
        HR_DETECTOR_STATUS_OK) {
        CHECK(0);
        return;
    }
    for (start = 0u;
         start + HR_DETECTOR_WINDOW_SAMPLES <= count4k;
         start += HR_DETECTOR_SAMPLE_RATE_HZ) {
        if (hr_detector_push_hop(
                &detector,
                &samples4k[start],
                HR_DETECTOR_WINDOW_SAMPLES,
                HR_DETECTOR_SAMPLE_RATE_HZ) < 0) {
            CHECK(0);
        }
        ++(*pushes);
    }
    (void)hr_detector_estimate(&detector, estimate);
}

/* ------------------------------------------------------------------------- */
/* 1. Synthetic known-rate                                                    */
/* ------------------------------------------------------------------------- */

static void test_synthetic_known_rate(void)
{
    static const double rates[] = {60.0, 90.0, 130.0};
    enum { SECONDS = 14 };
    size_t index;

    for (index = 0u; index < sizeof(rates) / sizeof(rates[0]); ++index) {
        e2e_chain_t chain;
        e2e_result_t result;
        uint32_t frames = (uint32_t)SECONDS * 50u; /* 50 frames/s */
        double bpm = rates[index];

        e2e_synth_pcg(
            e2e_signal,
            (size_t)SECONDS * AUDIO_SAMPLE_RATE_HZ,
            bpm,
            6000.0,
            120.0);
        e2e_chain_init(
            &chain, 0xE001u, e2e_work_chain, HR_DETECTOR_WORK_FLOATS,
            E2E_FIRMWARE_STEP_FRAMES, 0u);
        e2e_run_chain(&chain, e2e_signal, frames, &result);

        printf("known-rate: %5.1f bpm -> chain %6.2f bpm "
               "(valid=%u, events=%u, pairs=%u, pushes=%u, frames=%u)\n",
               bpm,
               (double)result.estimate.bpm,
               (unsigned)result.estimate.valid,
               (unsigned)result.estimate.events_total,
               (unsigned)(result.estimate.events_total / 2u),
               (unsigned)result.detector_pushes,
               (unsigned)result.stats.frames_ok);

        CHECK(result.stats.frames_ok == frames);
        CHECK(result.stats.frames_dropped == 0u);
        CHECK(result.stats.crc_errors == 0u);
        CHECK(result.stats.packet_errors == 0u);
        CHECK(result.rejected_packets == 0u);
        CHECK(result.detector_pushes > 0u);
        CHECK(result.estimate.valid == 1u);
        CHECK(fabs((double)result.estimate.bpm - bpm) <= 5.0);
    }
}

/* ------------------------------------------------------------------------- */
/* 2. Transport integrity: chain output == direct decimator output            */
/* ------------------------------------------------------------------------- */

static void test_transport_integrity(void)
{
    enum { FRAMES = 200 }; /* 4 s at 8 kHz, 2 s at 4 kHz */
    host_pipeline_decimator_t decimator;
    e2e_chain_t chain;
    e2e_result_t result;
    uint32_t frame;
    size_t reference_samples = 0u;
    int identical = 1;

    CHECK(link_a_fragment_count(
              AUDIO_WIRE_FRAME_BYTES, LINK_A_DEFAULT_MAX_FRAGMENT_PAYLOAD) ==
          2u);

    e2e_synth_pcg(
        e2e_signal, (size_t)FRAMES * AUDIO_FRAME_SAMPLES, 90.0, 6000.0, 120.0);
    e2e_chain_init(
        &chain, 0xE002u, e2e_work_chain, HR_DETECTOR_WORK_FLOATS,
        E2E_FIRMWARE_STEP_FRAMES, 0u);
    host_pipeline_decimator_init(&decimator);

    for (frame = 0u; frame < FRAMES; ++frame) {
        int16_t stored[HOST_PIPELINE_DECIMATED_FRAME_SAMPLES];
        size_t produced;
        int status = e2e_chain_push_frame(
            &chain, &e2e_signal[(size_t)frame * AUDIO_FRAME_SAMPLES]);

        CHECK(status == HOST_PIPELINE_STATUS_COMPLETE);
        produced = host_pipeline_decimator_process(
            &decimator,
            &e2e_signal[(size_t)frame * AUDIO_FRAME_SAMPLES],
            AUDIO_FRAME_SAMPLES,
            &e2e_reference[reference_samples]);
        CHECK(produced == HOST_PIPELINE_DECIMATED_FRAME_SAMPLES);
        reference_samples += produced;

        CHECK(host_pipeline_read_newest(&chain.pipeline, stored, 1u) == 1u);
        if (memcmp(
                stored,
                &e2e_reference[(size_t)frame *
                               HOST_PIPELINE_DECIMATED_FRAME_SAMPLES],
                sizeof(stored)) != 0) {
            identical = 0;
        }
    }
    e2e_chain_result(&chain, &result);

    printf("transport integrity: %u frames, %u samples, "
           "chain-vs-direct decimator %s\n",
           (unsigned)FRAMES,
           (unsigned)reference_samples,
           identical != 0 ? "bit-identical" : "MISMATCH");

    CHECK(identical != 0);
    CHECK(result.stats.frames_ok == FRAMES);
    CHECK(result.stats.frames_dropped == 0u);
    CHECK(result.stats.crc_errors == 0u);
    CHECK(result.stats.packet_errors == 0u);
    CHECK(result.stats.packets_in == FRAMES * 2u);
}

/* ------------------------------------------------------------------------- */
/* 3. LKA1 packet loss                                                        */
/* ------------------------------------------------------------------------- */

static void test_packet_loss(void)
{
    /*
     * One dropped packet kills one 20 ms frame. The detector's time base is
     * the surviving sample stream, so a lost frame shortens the median pair
     * sum by 20 ms and biases the rate high by the loss fraction. At the
     * default 222-byte fragment payload a frame is 2 packets, so "every 13th
     * packet" would lose 1 frame in 6.5 (15 %) and could never stay inside
     * +-5 bpm; every 101st packet loses ~2 % of frames, which is still a
     * handful of lost 20 ms frames over 10 s but a physically meaningful
     * tolerance test. The bias it is allowed to cause is < 2 bpm at 90 bpm.
     */
    enum {
        SECONDS = 10,
        FRAMES = SECONDS * 50,
        LOSS_PERIOD = 101
    };
    e2e_chain_t chain;
    e2e_result_t result;

    e2e_synth_pcg(
        e2e_signal,
        (size_t)SECONDS * AUDIO_SAMPLE_RATE_HZ,
        90.0,
        6000.0,
        120.0);
    e2e_chain_init(
        &chain, 0xE003u, e2e_work_chain, HR_DETECTOR_WORK_FLOATS,
        E2E_FIRMWARE_STEP_FRAMES, LOSS_PERIOD);
    e2e_run_chain(&chain, e2e_signal, FRAMES, &result);

    printf("loss: %u frames, %u/%u packets dropped (%u frames abandoned), "
           "frames_ok=%u, frames_dropped=%u, chain %6.2f bpm (valid=%u)\n",
           (unsigned)FRAMES,
           (unsigned)result.packets_dropped,
           (unsigned)result.packets_sent,
           (unsigned)result.stats.frames_dropped,
           (unsigned)result.stats.frames_ok,
           (unsigned)result.stats.frames_dropped,
           (double)result.estimate.bpm,
           (unsigned)result.estimate.valid);

    CHECK(result.packets_dropped > 0u);
    CHECK(result.stats.frames_ok > 0u);
    CHECK(result.stats.frames_ok < FRAMES);
    CHECK(result.stats.frames_dropped > 0u);
    CHECK(result.stats.crc_errors == 0u);
    CHECK(result.stats.packet_errors == 0u);
    CHECK(result.rejected_packets == 0u);
    CHECK(result.estimate.valid == 1u);
    CHECK(fabs((double)result.estimate.bpm - 90.0) <= 5.0);
}

/* ------------------------------------------------------------------------- */
/* 4. Session isolation and detector reset on stats.session_restarts          */
/* ------------------------------------------------------------------------- */

static size_t e2e_make_packet(
    uint8_t *packet,
    const int16_t *samples8k,
    uint16_t session_id,
    uint32_t sequence,
    uint8_t fragment_index)
{
    audio_frame_t frame;
    uint8_t wire[AUDIO_WIRE_FRAME_BYTES];
    size_t wire_length = 0u;
    size_t packet_length = 0u;

    memset(&frame, 0, sizeof(frame));
    frame.sample_rate_hz = AUDIO_SAMPLE_RATE_HZ;
    frame.timestamp_ms = sequence * AUDIO_FRAME_DURATION_MS;
    frame.sequence = sequence;
    frame.channel_count = AUDIO_CHANNEL_COUNT;
    memcpy(frame.samples, samples8k, sizeof(frame.samples));
    if (audio_protocol_encode_frame(
            wire,
            sizeof(wire),
            &frame,
            AUDIO_FLAG_TIMESTAMP_VALID,
            &wire_length) != AUDIO_STATUS_OK) {
        return 0u;
    }
    if (link_a_encode_frame_fragment(
            packet,
            E2E_PACKET_BYTES,
            wire,
            wire_length,
            session_id,
            sequence,
            fragment_index,
            LINK_A_DEFAULT_MAX_FRAGMENT_PAYLOAD,
            &packet_length) != LINK_A_STATUS_OK) {
        return 0u;
    }
    return packet_length;
}

static void test_session_isolation(void)
{
    enum {
        A_SECONDS = 6,
        B_SECONDS = 12,
        A_FRAMES = A_SECONDS * 50,
        B_FRAMES = B_SECONDS * 50
    };
    const uint16_t session_a = 0x1111u;
    const uint16_t session_b = 0x2222u;
    e2e_chain_t chain;
    e2e_result_t after_a;
    e2e_result_t after_b;
    uint8_t stale_packet[E2E_PACKET_BYTES];
    size_t stale_length;
    uint32_t frame;

    /* Session A: 60 bpm. */
    e2e_synth_pcg(
        e2e_signal,
        (size_t)A_SECONDS * AUDIO_SAMPLE_RATE_HZ,
        60.0,
        6000.0,
        120.0);
    e2e_chain_init(
        &chain, session_a, e2e_work_chain, HR_DETECTOR_WORK_FLOATS,
        E2E_FIRMWARE_STEP_FRAMES, 0u);
    e2e_run_chain(&chain, e2e_signal, A_FRAMES, &after_a);
    printf("session A (%04x, 60 bpm): %6.2f bpm after %u frames\n",
           (unsigned)session_a,
           (double)after_a.estimate.bpm,
           (unsigned)after_a.stats.frames_ok);
    CHECK(after_a.estimate.valid == 1u);
    CHECK(fabs((double)after_a.estimate.bpm - 60.0) <= 5.0);

    /* Capture a valid session-A packet for replay after the switch. */
    stale_length = e2e_make_packet(
        stale_packet,
        &e2e_signal[(size_t)(A_FRAMES - 1u) * AUDIO_FRAME_SAMPLES],
        session_a,
        A_FRAMES,
        0u);
    CHECK(stale_length > 0u);

    /* Session B: 110 bpm, written after A in the same sample buffer. */
    e2e_synth_pcg(
        &e2e_signal[(size_t)A_SECONDS * AUDIO_SAMPLE_RATE_HZ],
        (size_t)B_SECONDS * AUDIO_SAMPLE_RATE_HZ,
        110.0,
        6000.0,
        120.0);

    chain.session_id = session_b;
    for (frame = 0u; frame < B_FRAMES; ++frame) {
        int status = e2e_chain_push_frame(
            &chain,
            &e2e_signal[((size_t)A_SECONDS * AUDIO_SAMPLE_RATE_HZ) +
                        (size_t)frame * AUDIO_FRAME_SAMPLES]);

        if (frame == 0u) {
            /*
             * A late packet from the superseded session arrives after the new
             * session has been accepted: it must not enter the ring.
             */
            CHECK(status == HOST_PIPELINE_STATUS_COMPLETE_NEW_SESSION ||
                  status == HOST_PIPELINE_STATUS_COMPLETE ||
                  status == HOST_PIPELINE_STATUS_SESSION_RESTARTED ||
                  status == HOST_PIPELINE_STATUS_INCOMPLETE);
            CHECK(host_pipeline_push(
                      &chain.pipeline, stale_packet, stale_length) ==
                  HOST_PIPELINE_STATUS_STALE_SESSION);
        }
        e2e_chain_service(&chain);
    }
    e2e_chain_result(&chain, &after_b);

    printf("session B (%04x, 110 bpm): %6.2f bpm, restarts=%u, "
           "stale_packets=%u, detector_resets=%u, detector windows=%u\n",
           (unsigned)session_b,
           (double)after_b.estimate.bpm,
           (unsigned)after_b.stats.session_restarts,
           (unsigned)after_b.stats.stale_packets,
           (unsigned)after_b.detector_resets,
           (unsigned)after_b.estimate.windows);

    CHECK(after_b.stats.session_restarts == 1u);
    CHECK(after_b.stats.stale_packets == 1u);
    CHECK(after_b.stats.current_session_id == session_b);
    CHECK(after_b.detector_resets == 1u);
    /*
     * The detector was reset on the restart, so its window counter restarted:
     * 12 s of session B at the 2 s / 1 s cadence is B_SECONDS - 1 pushes.
     * Without the reset it would still hold 5 pushes from A plus B's 11.
     */
    CHECK(after_b.estimate.windows == (uint32_t)(B_SECONDS - 1));
    /* The pipeline reset the ring, so windows are B-only. */
    CHECK(after_b.stats.frames_ok > A_FRAMES);
    CHECK(after_b.estimate.valid == 1u);
    CHECK(fabs((double)after_b.estimate.bpm - 110.0) <= 5.0);
}

/* ------------------------------------------------------------------------- */
/* 5. Overlapping 2 s windows at the 1 s firmware cadence                     */
/* ------------------------------------------------------------------------- */

static void test_overlapping_windows(void)
{
    enum { SECONDS = 12, FRAMES = SECONDS * 50 };
    e2e_chain_t overlap_chain;
    e2e_chain_t plain_chain;
    e2e_result_t overlap;
    e2e_result_t plain;
    double events[HR_DETECTOR_EVENT_HISTORY];
    size_t event_count;

    e2e_synth_pcg(
        e2e_signal,
        (size_t)SECONDS * AUDIO_SAMPLE_RATE_HZ,
        60.0,
        6000.0,
        120.0);

    /* Firmware cadence: 2 s window, 1 s hop. */
    e2e_chain_init(
        &overlap_chain, 0xE005u, e2e_work_chain, HR_DETECTOR_WORK_FLOATS,
        E2E_FIRMWARE_STEP_FRAMES, 0u);
    e2e_run_chain(&overlap_chain, e2e_signal, FRAMES, &overlap);

    /* Control: no overlap, 2 s window every 2 s. */
    e2e_chain_init(
        &plain_chain, 0xE005u, e2e_work_chain, HR_DETECTOR_WORK_FLOATS,
        2u * E2E_FIRMWARE_STEP_FRAMES, 0u);
    e2e_run_chain(&plain_chain, e2e_signal, FRAMES, &plain);

    event_count = hr_detector_get_events(
        &overlap_chain.detector, events, HR_DETECTOR_EVENT_HISTORY);

    printf("overlap: 1 s step -> %6.2f bpm (%u events, %u peaks, "
           "%u suppressed as duplicates); 2 s step -> %6.2f bpm\n",
           (double)overlap.estimate.bpm,
           (unsigned)overlap.estimate.events_total,
           (unsigned)overlap.estimate.peaks_total,
           (unsigned)overlap.estimate.duplicates,
           (double)plain.estimate.bpm);

    CHECK(overlap.estimate.valid == 1u);
    CHECK(plain.estimate.valid == 1u);
    CHECK(fabs((double)overlap.estimate.bpm - 60.0) <= 5.0);
    CHECK(fabs((double)plain.estimate.bpm - 60.0) <= 5.0);
    /* Not doubled: the 1 s cadence must agree with the non-overlapping one. */
    CHECK(fabs((double)overlap.estimate.bpm -
               (double)plain.estimate.bpm) <= 2.0);
    CHECK(overlap.estimate.duplicates > 0u);
    CHECK(overlap.estimate.peaks_total ==
          overlap.estimate.events_total + overlap.estimate.duplicates);

    /* Every accepted event is still at least the 120 ms merge floor apart. */
    if (event_count > 1u) {
        size_t i;

        for (i = 1u; i < event_count; ++i) {
            CHECK(events[i] > events[i - 1u]);
            CHECK(events[i] - events[i - 1u] >= 0.12 - 1e-6);
        }
    }
    CHECK(event_count == overlap.estimate.n_events);
}

/* ------------------------------------------------------------------------- */
/* 6. Real data: CirCor 4 kHz -> 8 kHz -> chain vs direct detector            */
/* ------------------------------------------------------------------------- */

static uint16_t e2e_le16(const unsigned char *bytes)
{
    return (uint16_t)((uint16_t)bytes[0] | ((uint16_t)bytes[1] << 8));
}

static uint32_t e2e_le32(const unsigned char *bytes)
{
    return (uint32_t)bytes[0] | ((uint32_t)bytes[1] << 8) |
           ((uint32_t)bytes[2] << 16) | ((uint32_t)bytes[3] << 24);
}

/*
 * Minimal RIFF/WAVE reader for the 16-bit mono PCM files in the CirCor
 * corpus. Fails (returns 0) for anything else instead of guessing.
 */
static int e2e_read_wav_pcm16(
    const char *path,
    int16_t *out,
    size_t max_samples,
    size_t *out_count,
    int *out_rate)
{
    FILE *handle;
    unsigned char header[12];
    unsigned char chunk[8];
    int have_fmt = 0;
    int have_data = 0;
    uint16_t format = 0u;
    uint16_t channels = 0u;
    uint16_t bits = 0u;
    uint32_t rate = 0u;
    size_t written = 0u;

    *out_count = 0u;
    *out_rate = 0;
    handle = fopen(path, "rb");
    if (handle == NULL) {
        return 0;
    }
    if (fread(header, 1u, sizeof(header), handle) != sizeof(header) ||
        memcmp(header, "RIFF", 4u) != 0 ||
        memcmp(&header[8], "WAVE", 4u) != 0) {
        fclose(handle);
        return 0;
    }

    while (have_data == 0) {
        uint32_t size;

        if (fread(chunk, 1u, sizeof(chunk), handle) != sizeof(chunk)) {
            break;
        }
        size = e2e_le32(&chunk[4]);
        if (memcmp(chunk, "fmt ", 4u) == 0) {
            unsigned char fmt[16];
            size_t to_read = size < sizeof(fmt) ? size : sizeof(fmt);

            if (fread(fmt, 1u, to_read, handle) != to_read) {
                break;
            }
            if (size > to_read) {
                (void)fseek(handle, (long)(size - to_read), SEEK_CUR);
            }
            format = e2e_le16(&fmt[0]);
            channels = e2e_le16(&fmt[2]);
            rate = e2e_le32(&fmt[4]);
            bits = e2e_le16(&fmt[14]);
            have_fmt = 1;
        } else if (memcmp(chunk, "data", 4u) == 0) {
            size_t want;
            size_t done = 0u;
            unsigned char block[4096];

            have_data = 1;
            if (have_fmt == 0 ||
                format != 1u ||
                channels != AUDIO_CHANNEL_COUNT ||
                bits != 16u ||
                rate != AUDIO_AI_SAMPLE_RATE_HZ) {
                break;
            }
            want = (size_t)(size / 2u);
            if (want > max_samples) {
                want = max_samples;
            }
            while (done < want) {
                size_t samples = want - done;
                size_t bytes = samples * 2u;
                size_t i;

                if (bytes > sizeof(block)) {
                    bytes = sizeof(block);
                    samples = bytes / 2u;
                }
                if (fread(block, 1u, bytes, handle) != bytes) {
                    break;
                }
                for (i = 0u; i < samples; ++i) {
                    out[done + i] = (int16_t)e2e_le16(&block[2u * i]);
                }
                done += samples;
            }
            written = done;
        } else {
            /* Skip unknown chunks, honouring RIFF word padding. */
            (void)fseek(handle, (long)(size + (size & 1u)), SEEK_CUR);
        }
    }
    fclose(handle);

    if (have_data == 0 || written == 0u) {
        return 0;
    }
    *out_count = written;
    *out_rate = (int)rate;
    return 1;
}

/* Linear 2x interpolation: out[2n] = x[n], out[2n+1] = (x[n]+x[n+1])/2. */
static size_t e2e_upsample_4k_to_8k(
    const int16_t *input,
    size_t count,
    int16_t *output)
{
    size_t i;

    for (i = 0u; i < count; ++i) {
        int32_t value = input[i];

        output[2u * i] = (int16_t)value;
        if (i + 1u < count) {
            output[2u * i + 1u] =
                (int16_t)(((int32_t)input[i] + (int32_t)input[i + 1u]) / 2);
        } else {
            output[2u * i + 1u] = (int16_t)value;
        }
    }
    return count * 2u;
}

/* Index of a named CSV header field, or -1. Empty fields are preserved. */
static int e2e_csv_column(const char *line, const char *name)
{
    size_t name_length = strlen(name);
    int index = 0;
    const char *cursor = line;

    while (*cursor != '\0' && *cursor != '\r' && *cursor != '\n') {
        const char *start = cursor;
        size_t length;

        while (*cursor != '\0' && *cursor != ',' &&
               *cursor != '\r' && *cursor != '\n') {
            ++cursor;
        }
        length = (size_t)(cursor - start);
        if (length == name_length &&
            strncmp(start, name, name_length) == 0) {
            return index;
        }
        if (*cursor != ',') {
            break;
        }
        ++cursor;
        ++index;
    }
    return -1;
}

/*
 * Copy one CSV field. Unlike strtok() this counts empty fields (the CirCor CSV
 * has empty label/murmur cells), so the wav_path column index stays valid.
 */
static int e2e_csv_field(
    const char *line,
    int wanted,
    char *out,
    size_t out_size)
{
    int index = 0;
    const char *cursor = line;

    if (out_size == 0u) {
        return 0;
    }
    while (*cursor != '\0' && *cursor != '\r' && *cursor != '\n') {
        const char *start = cursor;
        size_t length;

        while (*cursor != '\0' && *cursor != ',' &&
               *cursor != '\r' && *cursor != '\n') {
            ++cursor;
        }
        if (index == wanted) {
            length = (size_t)(cursor - start);
            if (length >= out_size) {
                length = out_size - 1u;
            }
            memcpy(out, start, length);
            out[length] = '\0';
            return 1;
        }
        if (*cursor != ',') {
            break;
        }
        ++cursor;
        ++index;
    }
    /* A trailing empty field ("a,b,") ends the loop with index == wanted. */
    if (index == wanted) {
        out[0] = '\0';
        return 1;
    }
    return 0;
}

/*
 * Read the wav_path column of the first capacity data rows. Returns 0 when the
 * CSV is missing or has no wav_path column, which the caller treats as "no
 * corpus" and skips.
 */
static size_t e2e_read_csv_wav_paths(
    const char *csv_path,
    char paths[][E2E_PATH_BYTES],
    size_t capacity)
{
    char line[2048];
    char header[2048];
    FILE *handle = fopen(csv_path, "r");
    int column = -1;
    size_t count = 0u;

    if (handle == NULL) {
        return 0u;
    }
    if (fgets(header, sizeof(header), handle) != NULL) {
        column = e2e_csv_column(header, "wav_path");
    }
    if (column < 0) {
        fclose(handle);
        return 0u;
    }

    while (count < capacity && fgets(line, sizeof(line), handle) != NULL) {
        if (e2e_csv_field(line, column, paths[count], E2E_PATH_BYTES) == 0 ||
            paths[count][0] == '\0') {
            continue;
        }
        ++count;
    }
    fclose(handle);
    return count;
}

static void test_real_data_agreement(void)
{
    enum {
        RECORDINGS = 20,
        CSV_ROWS = 40,
        MIN_COMPARED = 10,
        MIN_SECONDS = 5
    };
    static char paths[CSV_ROWS][E2E_PATH_BYTES];
    char csv_path[E2E_FULL_PATH_BYTES];
    size_t available;
    size_t index;
    int processed = 0;
    int compared = 0;
    int mismatched_validity = 0;
    double worst = 0.0;

    (void)snprintf(
        csv_path,
        sizeof(csv_path),
        "%s/data/processed/circor/recordings.csv",
        TEST_E2E_SOURCE_DIR);
    available = e2e_read_csv_wav_paths(csv_path, paths, CSV_ROWS);
    if (available == 0u) {
        printf("real-data: %s not available; skipping (CI without the "
               "CirCor corpus)\n", csv_path);
        return;
    }

    for (index = 0u; index < available && processed < RECORDINGS; ++index) {
        char wav_path[E2E_FULL_PATH_BYTES];
        const char *label;
        size_t samples4k = 0u;
        size_t usable;
        size_t upsampled;
        uint32_t frames;
        int rate = 0;
        e2e_chain_t chain;
        e2e_result_t chain_result;
        hr_detector_estimate_t direct;
        uint32_t direct_pushes;

        (void)snprintf(
            wav_path, sizeof(wav_path), "%s/%s",
            TEST_E2E_SOURCE_DIR, paths[index]);
        if (e2e_read_wav_pcm16(
                wav_path,
                e2e_wav4k,
                E2E_MAX_4K_SAMPLES,
                &samples4k,
                &rate) == 0) {
            printf("real-data: skipping unreadable/unsupported %s\n",
                   paths[index]);
            continue;
        }

        /* Whole 1 s steps so chain and direct detector see identical windows. */
        usable = (samples4k / AUDIO_AI_SAMPLE_RATE_HZ) *
                 AUDIO_AI_SAMPLE_RATE_HZ;
        if (usable < MIN_SECONDS * AUDIO_AI_SAMPLE_RATE_HZ) {
            printf("real-data: skipping %s (%u samples, need %u s)\n",
                   paths[index],
                   (unsigned)samples4k,
                   (unsigned)MIN_SECONDS);
            continue;
        }

        upsampled = e2e_upsample_4k_to_8k(e2e_wav4k, usable, e2e_signal);
        frames = (uint32_t)(upsampled / AUDIO_FRAME_SAMPLES);
        CHECK(upsampled == usable * 2u);
        CHECK((size_t)frames * AUDIO_FRAME_SAMPLES == upsampled);

        e2e_chain_init(
            &chain,
            (uint16_t)(0x4000u + (unsigned)processed),
            e2e_work_chain,
            HR_DETECTOR_WORK_FLOATS,
            E2E_FIRMWARE_STEP_FRAMES,
            0u);
        e2e_run_chain(&chain, e2e_signal, frames, &chain_result);
        e2e_run_direct_detector(
            e2e_wav4k,
            usable,
            e2e_work_direct,
            &direct,
            &direct_pushes);

        label = strrchr(paths[index], '/');
        label = label != NULL ? label + 1 : paths[index];
        if (chain_result.estimate.valid != 0u && direct.valid != 0u) {
            double difference = fabs(
                (double)chain_result.estimate.bpm - (double)direct.bpm);

            ++compared;
            if (difference > worst) {
                worst = difference;
            }
            printf("real-data %2d: %-24s %5.1fs chain=%6.2f direct=%6.2f "
                   "diff=%5.2f (frames=%u, windows=%u/%u)\n",
                   processed + 1,
                   label,
                   (double)usable / (double)AUDIO_AI_SAMPLE_RATE_HZ,
                   (double)chain_result.estimate.bpm,
                   (double)direct.bpm,
                   difference,
                   (unsigned)chain_result.stats.frames_ok,
                   (unsigned)chain_result.detector_pushes,
                   (unsigned)direct_pushes);
            CHECK(difference <= 2.0);
        } else if (chain_result.estimate.valid == direct.valid) {
            /* Both agree there is no rate (quiet or too few events). */
            printf("real-data %2d: %-24s %5.1fs no valid rate on either path "
                   "(chain valid=%u, direct valid=%u)\n",
                   processed + 1,
                   label,
                   (double)usable / (double)AUDIO_AI_SAMPLE_RATE_HZ,
                   (unsigned)chain_result.estimate.valid,
                   (unsigned)direct.valid);
        } else {
            ++mismatched_validity;
            printf("real-data %2d: %-24s %5.1fs VALIDITY MISMATCH "
                   "(chain valid=%u direct valid=%u)\n",
                   processed + 1,
                   label,
                   (double)usable / (double)AUDIO_AI_SAMPLE_RATE_HZ,
                   (unsigned)chain_result.estimate.valid,
                   (unsigned)direct.valid);
        }
        CHECK(chain_result.stats.crc_errors == 0u);
        CHECK(chain_result.stats.frames_dropped == 0u);
        ++processed;
    }

    printf("real-data: %d recordings processed, %d compared, worst diff %.2f "
           "bpm, %d validity mismatches\n",
           processed, compared, worst, mismatched_validity);

    if (processed == 0) {
        printf("real-data: no usable recordings; skipping\n");
        return;
    }
    CHECK(compared >= MIN_COMPARED);
    CHECK(mismatched_validity == 0);
}

/* ------------------------------------------------------------------------- */

int main(void)
{
    printf("end-to-end chain: 8 kHz PCM -> AUD1 -> LKA1 -> host_pipeline "
           "-> hr_detector\n");
    printf("ring=%u frames (%u ms at 4 kHz), window=%u samples, step=%u "
           "frames\n",
           (unsigned)E2E_RING_FRAMES,
           (unsigned)(E2E_RING_FRAMES * AUDIO_FRAME_DURATION_MS / 2u),
           (unsigned)HOST_PIPELINE_AI_WINDOW_SAMPLES,
           (unsigned)E2E_FIRMWARE_STEP_FRAMES);

    test_synthetic_known_rate();
    test_transport_integrity();
    test_packet_loss();
    test_session_isolation();
    test_overlapping_windows();
    test_real_data_agreement();

    if (failures != 0) {
        fprintf(stderr, "%d test(s) failed\n", failures);
        return 1;
    }

    printf("end_to_end_chain tests passed\n");
    return 0;
}
