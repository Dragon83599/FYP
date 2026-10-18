#include "host_pipeline.h"

#include <math.h>
#include <stdio.h>
#include <string.h>

/*
 * Unit tests for the portable host audio pipeline. Plain C, no framework:
 * failures are counted and main() returns non-zero if any check fails.
 */

static int failures = 0;

#define CHECK(condition)                                                       \
    do {                                                                       \
        if (!(condition)) {                                                    \
            ++failures;                                                        \
            fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #condition); \
        }                                                                      \
    } while (0)

#define TEST_PACKET_BYTES (LINK_A_WIRE_OVERHEAD_BYTES + AUDIO_WIRE_FRAME_BYTES)

#define TEST_PI 3.14159265358979323846

static void fill_dc(int16_t *samples, size_t count, int16_t value)
{
    size_t i;

    for (i = 0u; i < count; ++i) {
        samples[i] = value;
    }
}

static void fill_sine(
    int16_t *samples,
    size_t count,
    double frequency_hz,
    double amplitude)
{
    size_t i;

    for (i = 0u; i < count; ++i) {
        double value = sin(
            (2.0 * TEST_PI * frequency_hz * (double)i) /
            (double)AUDIO_SAMPLE_RATE_HZ);
        samples[i] = (int16_t)lround(value * amplitude * 32767.0);
    }
}

static double rms(const int16_t *samples, size_t count)
{
    double sum = 0.0;
    size_t i;

    if (count == 0u) {
        return 0.0;
    }
    for (i = 0u; i < count; ++i) {
        double value = (double)samples[i];
        sum += value * value;
    }
    return sqrt(sum / (double)count);
}

static int encode_wire_frame(
    uint8_t *wire,
    const int16_t *samples,
    uint32_t sequence,
    uint32_t timestamp_ms)
{
    audio_frame_t frame;
    size_t wire_length = 0u;

    memset(&frame, 0, sizeof(frame));
    frame.sample_rate_hz = AUDIO_SAMPLE_RATE_HZ;
    frame.timestamp_ms = timestamp_ms;
    frame.sequence = sequence;
    frame.channel_count = AUDIO_CHANNEL_COUNT;
    memcpy(frame.samples, samples, sizeof(frame.samples));

    return audio_protocol_encode_frame(
               wire,
               AUDIO_WIRE_FRAME_BYTES,
               &frame,
               AUDIO_FLAG_TIMESTAMP_VALID,
               &wire_length) == AUDIO_STATUS_OK &&
           wire_length == AUDIO_WIRE_FRAME_BYTES;
}

static size_t make_fragment(
    uint8_t *packet,
    const uint8_t *wire_frame,
    uint16_t session_id,
    uint32_t frame_sequence,
    uint8_t fragment_index,
    size_t max_fragment_payload,
    uint8_t *fragment_count)
{
    size_t count = link_a_fragment_count(
        AUDIO_WIRE_FRAME_BYTES,
        max_fragment_payload);
    size_t packet_length = 0u;

    if (fragment_count != NULL) {
        *fragment_count = (uint8_t)count;
    }
    if (count == 0u ||
        link_a_encode_frame_fragment(
            packet,
            TEST_PACKET_BYTES,
            wire_frame,
            AUDIO_WIRE_FRAME_BYTES,
            session_id,
            frame_sequence,
            fragment_index,
            max_fragment_payload,
            &packet_length) != LINK_A_STATUS_OK) {
        return 0u;
    }
    return packet_length;
}

/*
 * Push one frame whose 160 samples are all value, using the single-fragment
 * packet shape (max payload 352 bytes). Returns the host status.
 */
static int push_dc_frame(
    host_pipeline_t *pipeline,
    uint16_t session_id,
    uint32_t sequence,
    int16_t value)
{
    int16_t samples[AUDIO_FRAME_SAMPLES];
    uint8_t wire[AUDIO_WIRE_FRAME_BYTES];
    uint8_t packet[TEST_PACKET_BYTES];
    uint8_t fragment_count = 0u;
    size_t packet_length;

    fill_dc(samples, AUDIO_FRAME_SAMPLES, value);
    if (!encode_wire_frame(wire, samples, sequence, sequence * 20u)) {
        return HOST_PIPELINE_STATUS_INVALID_ARGUMENT;
    }
    packet_length = make_fragment(
        packet,
        wire,
        session_id,
        sequence,
        0u,
        AUDIO_WIRE_FRAME_BYTES,
        &fragment_count);
    if (packet_length == 0u || fragment_count != 1u) {
        return HOST_PIPELINE_STATUS_INVALID_ARGUMENT;
    }
    return host_pipeline_push(pipeline, packet, packet_length);
}

/* 1. Out-of-order fragments still produce exactly one valid frame. */
static void test_out_of_order_fragments(void)
{
    int16_t samples[AUDIO_FRAME_SAMPLES];
    int16_t expected[HOST_PIPELINE_DECIMATED_FRAME_SAMPLES];
    int16_t actual[HOST_PIPELINE_DECIMATED_FRAME_SAMPLES];
    uint8_t wire[AUDIO_WIRE_FRAME_BYTES];
    uint8_t first[TEST_PACKET_BYTES];
    uint8_t second[TEST_PACKET_BYTES];
    uint8_t fragment_count = 0u;
    size_t first_length;
    size_t second_length;
    int16_t ring[8u * HOST_PIPELINE_DECIMATED_FRAME_SAMPLES];
    host_pipeline_t pipeline;
    host_pipeline_decimator_t reference;
    const host_pipeline_stats_t *stats;

    fill_sine(samples, AUDIO_FRAME_SAMPLES, 440.0, 0.5);
    CHECK(encode_wire_frame(wire, samples, 100u, 2000u));

    first_length = make_fragment(
        first, wire, 0x1234u, 100u, 0u, 222u, &fragment_count);
    CHECK(fragment_count == 2u);
    CHECK(first_length > 0u);
    second_length = make_fragment(
        second, wire, 0x1234u, 100u, 1u, 222u, &fragment_count);
    CHECK(second_length > 0u);

    CHECK(host_pipeline_init(&pipeline, ring, 8u) == HOST_PIPELINE_STATUS_OK);

    /* Deliver the second fragment first. */
    CHECK(host_pipeline_push(&pipeline, second, second_length) ==
          HOST_PIPELINE_STATUS_INCOMPLETE);
    CHECK(host_pipeline_frames_available(&pipeline) == 0u);
    CHECK(host_pipeline_push(&pipeline, first, first_length) ==
          HOST_PIPELINE_STATUS_COMPLETE);

    stats = host_pipeline_get_stats(&pipeline);
    CHECK(stats->packets_in == 2u);
    CHECK(stats->frames_ok == 1u);
    CHECK(stats->crc_errors == 0u);
    CHECK(stats->frames_dropped == 0u);
    CHECK(stats->last_sequence == 100u);
    CHECK(stats->current_session_id == 0x1234u);
    CHECK(host_pipeline_frames_available(&pipeline) == 1u);

    /* The stored frame must equal a fresh decimator run over the same PCM. */
    host_pipeline_decimator_init(&reference);
    CHECK(host_pipeline_decimator_process(
              &reference,
              samples,
              AUDIO_FRAME_SAMPLES,
              expected) == HOST_PIPELINE_DECIMATED_FRAME_SAMPLES);
    CHECK(host_pipeline_read_newest(&pipeline, actual, 1u) == 1u);
    CHECK(memcmp(expected, actual, sizeof(expected)) == 0);
}

/* 2. Duplicate fragments are suppressed and the frame is still produced once. */
static void test_duplicate_fragment(void)
{
    int16_t samples[AUDIO_FRAME_SAMPLES];
    uint8_t wire[AUDIO_WIRE_FRAME_BYTES];
    uint8_t first[TEST_PACKET_BYTES];
    uint8_t second[TEST_PACKET_BYTES];
    uint8_t fragment_count = 0u;
    size_t first_length;
    size_t second_length;
    int16_t ring[4u * HOST_PIPELINE_DECIMATED_FRAME_SAMPLES];
    host_pipeline_t pipeline;
    const host_pipeline_stats_t *stats;

    fill_sine(samples, AUDIO_FRAME_SAMPLES, 440.0, 0.4);
    CHECK(encode_wire_frame(wire, samples, 5u, 100u));
    first_length = make_fragment(
        first, wire, 0x0102u, 5u, 0u, 222u, &fragment_count);
    second_length = make_fragment(
        second, wire, 0x0102u, 5u, 1u, 222u, &fragment_count);
    CHECK(first_length > 0u);
    CHECK(second_length > 0u);

    CHECK(host_pipeline_init(&pipeline, ring, 4u) == HOST_PIPELINE_STATUS_OK);

    CHECK(host_pipeline_push(&pipeline, first, first_length) ==
          HOST_PIPELINE_STATUS_INCOMPLETE);
    CHECK(host_pipeline_push(&pipeline, first, first_length) ==
          HOST_PIPELINE_STATUS_DUPLICATE);
    CHECK(host_pipeline_push(&pipeline, first, first_length) ==
          HOST_PIPELINE_STATUS_DUPLICATE);
    CHECK(host_pipeline_frames_available(&pipeline) == 0u);

    CHECK(host_pipeline_push(&pipeline, second, second_length) ==
          HOST_PIPELINE_STATUS_COMPLETE);
    CHECK(host_pipeline_push(&pipeline, second, second_length) ==
          HOST_PIPELINE_STATUS_DUPLICATE);

    stats = host_pipeline_get_stats(&pipeline);
    CHECK(stats->packets_in == 5u);
    CHECK(stats->frames_ok == 1u);
    CHECK(stats->frames_dropped == 0u);
    CHECK(host_pipeline_frames_available(&pipeline) == 1u);
}

/* 3. A corrupted AUD1 CRC rejects the frame and increments crc_errors. */
static void test_bad_audio_crc(void)
{
    int16_t samples[AUDIO_FRAME_SAMPLES];
    uint8_t wire[AUDIO_WIRE_FRAME_BYTES];
    uint8_t first[TEST_PACKET_BYTES];
    uint8_t second[TEST_PACKET_BYTES];
    uint8_t fragment_count = 0u;
    size_t first_length;
    size_t second_length;
    int16_t ring[4u * HOST_PIPELINE_DECIMATED_FRAME_SAMPLES];
    host_pipeline_t pipeline;
    const host_pipeline_stats_t *stats;

    fill_sine(samples, AUDIO_FRAME_SAMPLES, 440.0, 0.5);
    CHECK(encode_wire_frame(wire, samples, 9u, 180u));

    /* Corrupt one PCM byte: the LKA1 CRC is recomputed, the AUD1 CRC is not. */
    wire[AUDIO_WIRE_OFFSET_PAYLOAD + 70u] ^= 0x01u;

    first_length = make_fragment(
        first, wire, 0x3333u, 9u, 0u, 222u, &fragment_count);
    second_length = make_fragment(
        second, wire, 0x3333u, 9u, 1u, 222u, &fragment_count);
    CHECK(first_length > 0u);
    CHECK(second_length > 0u);

    CHECK(host_pipeline_init(&pipeline, ring, 4u) == HOST_PIPELINE_STATUS_OK);
    CHECK(host_pipeline_push(&pipeline, first, first_length) ==
          HOST_PIPELINE_STATUS_INCOMPLETE);
    CHECK(host_pipeline_push(&pipeline, second, second_length) ==
          HOST_PIPELINE_STATUS_BAD_AUDIO_CRC);

    stats = host_pipeline_get_stats(&pipeline);
    CHECK(stats->packets_in == 2u);
    CHECK(stats->frames_ok == 0u);
    CHECK(stats->crc_errors == 1u);
    CHECK(stats->format_errors == 0u);
    CHECK(stats->frames_dropped == 0u);
    CHECK(host_pipeline_frames_available(&pipeline) == 0u);

    /* A corrupted LKA1 envelope is rejected before reassembly. */
    second[LINK_A_OFFSET_PAYLOAD] ^= 0x01u;
    CHECK(host_pipeline_push(&pipeline, second, second_length) ==
          HOST_PIPELINE_STATUS_BAD_PACKET);
    stats = host_pipeline_get_stats(&pipeline);
    CHECK(stats->packet_errors == 1u);
    CHECK(stats->packets_in == 3u);
    CHECK(stats->frames_ok == 0u);
    CHECK(host_pipeline_frames_available(&pipeline) == 0u);
}

/* 4. A missing fragment never produces a frame. */
static void test_missing_fragment(void)
{
    int16_t samples_seven[AUDIO_FRAME_SAMPLES];
    int16_t samples_eight[AUDIO_FRAME_SAMPLES];
    int16_t expected[HOST_PIPELINE_DECIMATED_FRAME_SAMPLES];
    int16_t actual[HOST_PIPELINE_DECIMATED_FRAME_SAMPLES];
    uint8_t wire_seven[AUDIO_WIRE_FRAME_BYTES];
    uint8_t wire_eight[AUDIO_WIRE_FRAME_BYTES];
    uint8_t seven_first[TEST_PACKET_BYTES];
    uint8_t eight_first[TEST_PACKET_BYTES];
    uint8_t eight_second[TEST_PACKET_BYTES];
    uint8_t fragment_count = 0u;
    size_t seven_first_length;
    size_t eight_first_length;
    size_t eight_second_length;
    int16_t ring[4u * HOST_PIPELINE_DECIMATED_FRAME_SAMPLES];
    host_pipeline_t pipeline;
    host_pipeline_decimator_t reference;
    const host_pipeline_stats_t *stats;

    fill_dc(samples_seven, AUDIO_FRAME_SAMPLES, 1000);
    fill_dc(samples_eight, AUDIO_FRAME_SAMPLES, 2000);
    CHECK(encode_wire_frame(wire_seven, samples_seven, 7u, 140u));
    CHECK(encode_wire_frame(wire_eight, samples_eight, 8u, 160u));

    seven_first_length = make_fragment(
        seven_first, wire_seven, 0x4444u, 7u, 0u, 222u, &fragment_count);
    CHECK(fragment_count == 2u);
    eight_first_length = make_fragment(
        eight_first, wire_eight, 0x4444u, 8u, 0u, 222u, &fragment_count);
    eight_second_length = make_fragment(
        eight_second, wire_eight, 0x4444u, 8u, 1u, 222u, &fragment_count);
    CHECK(seven_first_length > 0u);
    CHECK(eight_first_length > 0u);
    CHECK(eight_second_length > 0u);

    CHECK(host_pipeline_init(&pipeline, ring, 4u) == HOST_PIPELINE_STATUS_OK);

    /* Frame 7 loses its second fragment; frame 8 then starts. */
    CHECK(host_pipeline_push(&pipeline, seven_first, seven_first_length) ==
          HOST_PIPELINE_STATUS_INCOMPLETE);
    CHECK(host_pipeline_push(&pipeline, eight_first, eight_first_length) ==
          HOST_PIPELINE_STATUS_INCOMPLETE);
    CHECK(host_pipeline_frames_available(&pipeline) == 0u);

    stats = host_pipeline_get_stats(&pipeline);
    CHECK(stats->frames_ok == 0u);
    CHECK(stats->frames_dropped == 1u);

    CHECK(host_pipeline_push(&pipeline, eight_second, eight_second_length) ==
          HOST_PIPELINE_STATUS_COMPLETE);
    stats = host_pipeline_get_stats(&pipeline);
    CHECK(stats->frames_ok == 1u);
    CHECK(stats->last_sequence == 8u);
    CHECK(host_pipeline_frames_available(&pipeline) == 1u);

    /* Only frame 8 entered the decimator. */
    host_pipeline_decimator_init(&reference);
    CHECK(host_pipeline_decimator_process(
              &reference,
              samples_eight,
              AUDIO_FRAME_SAMPLES,
              expected) == HOST_PIPELINE_DECIMATED_FRAME_SAMPLES);
    CHECK(host_pipeline_read_newest(&pipeline, actual, 1u) == 1u);
    CHECK(memcmp(expected, actual, sizeof(expected)) == 0);
}

/* 5. A session change isolates streams; old fragments cannot complete. */
static void test_session_isolation(void)
{
    int16_t samples_a[AUDIO_FRAME_SAMPLES];
    int16_t samples_b[AUDIO_FRAME_SAMPLES];
    int16_t samples_c[AUDIO_FRAME_SAMPLES];
    int16_t expected_b[HOST_PIPELINE_DECIMATED_FRAME_SAMPLES];
    int16_t window[HOST_PIPELINE_DECIMATED_FRAME_SAMPLES];
    uint8_t wire_a[AUDIO_WIRE_FRAME_BYTES];
    uint8_t wire_b[AUDIO_WIRE_FRAME_BYTES];
    uint8_t wire_c[AUDIO_WIRE_FRAME_BYTES];
    uint8_t a_first[TEST_PACKET_BYTES];
    uint8_t a_second[TEST_PACKET_BYTES];
    uint8_t b_first[TEST_PACKET_BYTES];
    uint8_t b_second[TEST_PACKET_BYTES];
    uint8_t c_packet[TEST_PACKET_BYTES];
    uint8_t fragment_count = 0u;
    size_t a_first_length;
    size_t a_second_length;
    size_t b_first_length;
    size_t b_second_length;
    size_t c_length;
    int16_t ring[8u * HOST_PIPELINE_DECIMATED_FRAME_SAMPLES];
    host_pipeline_t pipeline;
    host_pipeline_decimator_t reference;
    const host_pipeline_stats_t *stats;

    fill_dc(samples_a, AUDIO_FRAME_SAMPLES, 1111);
    fill_dc(samples_b, AUDIO_FRAME_SAMPLES, 2222);
    fill_dc(samples_c, AUDIO_FRAME_SAMPLES, 3333);
    CHECK(encode_wire_frame(wire_a, samples_a, 21u, 420u));
    CHECK(encode_wire_frame(wire_b, samples_b, 21u, 420u));
    CHECK(encode_wire_frame(wire_c, samples_c, 21u, 420u));

    a_first_length = make_fragment(
        a_first, wire_a, 0xAAAAu, 21u, 0u, 222u, &fragment_count);
    a_second_length = make_fragment(
        a_second, wire_a, 0xAAAAu, 21u, 1u, 222u, &fragment_count);
    b_first_length = make_fragment(
        b_first, wire_b, 0xBBBBu, 21u, 0u, 222u, &fragment_count);
    b_second_length = make_fragment(
        b_second, wire_b, 0xBBBBu, 21u, 1u, 222u, &fragment_count);
    c_length = make_fragment(
        c_packet,
        wire_c,
        0xCCCCu,
        21u,
        0u,
        AUDIO_WIRE_FRAME_BYTES,
        &fragment_count);
    CHECK(fragment_count == 1u);
    CHECK(a_first_length > 0u);
    CHECK(a_second_length > 0u);
    CHECK(b_first_length > 0u);
    CHECK(b_second_length > 0u);
    CHECK(c_length > 0u);

    CHECK(host_pipeline_init(&pipeline, ring, 8u) == HOST_PIPELINE_STATUS_OK);

    /* Old session A: first fragment received, session then replaced by B. */
    CHECK(host_pipeline_push(&pipeline, a_first, a_first_length) ==
          HOST_PIPELINE_STATUS_INCOMPLETE);
    CHECK(host_pipeline_push(&pipeline, b_first, b_first_length) ==
          HOST_PIPELINE_STATUS_SESSION_RESTARTED);
    stats = host_pipeline_get_stats(&pipeline);
    CHECK(stats->session_restarts == 1u);
    CHECK(stats->current_session_id == 0xBBBBu);
    CHECK(stats->frames_ok == 0u);
    CHECK(stats->frames_dropped == 1u);
    CHECK(host_pipeline_frames_available(&pipeline) == 0u);

    /* A late old-session fragment must not disturb B. */
    CHECK(host_pipeline_push(&pipeline, a_second, a_second_length) ==
          HOST_PIPELINE_STATUS_STALE_SESSION);
    stats = host_pipeline_get_stats(&pipeline);
    CHECK(stats->stale_packets == 1u);
    CHECK(stats->frames_ok == 0u);

    CHECK(host_pipeline_push(&pipeline, b_second, b_second_length) ==
          HOST_PIPELINE_STATUS_COMPLETE);
    stats = host_pipeline_get_stats(&pipeline);
    CHECK(stats->frames_ok == 1u);
    CHECK(stats->current_session_id == 0xBBBBu);
    CHECK(stats->last_sequence == 21u);
    CHECK(host_pipeline_frames_available(&pipeline) == 1u);

    /* The stored frame is B's PCM, not A's. */
    host_pipeline_decimator_init(&reference);
    CHECK(host_pipeline_decimator_process(
              &reference,
              samples_b,
              AUDIO_FRAME_SAMPLES,
              expected_b) == HOST_PIPELINE_DECIMATED_FRAME_SAMPLES);
    CHECK(host_pipeline_read_newest(&pipeline, window, 1u) == 1u);
    CHECK(memcmp(expected_b, window, sizeof(expected_b)) == 0);

    /* Even a full retransmission of A after the switch stays stale. */
    CHECK(host_pipeline_push(&pipeline, a_first, a_first_length) ==
          HOST_PIPELINE_STATUS_STALE_SESSION);
    CHECK(host_pipeline_push(&pipeline, a_second, a_second_length) ==
          HOST_PIPELINE_STATUS_STALE_SESSION);
    CHECK(host_pipeline_frames_available(&pipeline) == 1u);

    /* A genuinely new session C that completes in one packet. */
    CHECK(host_pipeline_push(&pipeline, c_packet, c_length) ==
          HOST_PIPELINE_STATUS_COMPLETE_NEW_SESSION);
    stats = host_pipeline_get_stats(&pipeline);
    CHECK(stats->frames_ok == 2u);
    CHECK(stats->session_restarts == 2u);
    CHECK(stats->current_session_id == 0xCCCCu);
    /* The new session reset the ring: only C's frame remains. */
    CHECK(host_pipeline_frames_available(&pipeline) == 1u);

    host_pipeline_decimator_init(&reference);
    CHECK(host_pipeline_decimator_process(
              &reference,
              samples_c,
              AUDIO_FRAME_SAMPLES,
              window) == HOST_PIPELINE_DECIMATED_FRAME_SAMPLES);
    CHECK(host_pipeline_read_newest(&pipeline, expected_b, 1u) == 1u);
    CHECK(memcmp(window, expected_b, sizeof(expected_b)) == 0);

    /* B is now a superseded session too. */
    CHECK(host_pipeline_push(&pipeline, b_second, b_second_length) ==
          HOST_PIPELINE_STATUS_STALE_SESSION);
}

/* 6. Ring wraps, counts overruns and returns the newest window. */
static void test_ring_wrap_and_window(void)
{
    static int16_t ring_small[4u * HOST_PIPELINE_DECIMATED_FRAME_SAMPLES];
    static int16_t expected_small[6u * HOST_PIPELINE_DECIMATED_FRAME_SAMPLES];
    static int16_t window[HOST_PIPELINE_AI_WINDOW_SAMPLES];
    static int16_t ring_ai[
        HOST_PIPELINE_AI_WINDOW_FRAMES * HOST_PIPELINE_DECIMATED_FRAME_SAMPLES];
    static int16_t expected_ai[
        105u * HOST_PIPELINE_DECIMATED_FRAME_SAMPLES];
    host_pipeline_t small;
    host_pipeline_t ai;
    host_pipeline_decimator_t reference;
    const host_pipeline_stats_t *stats;
    int16_t samples[AUDIO_FRAME_SAMPLES];
    size_t expected_count;
    uint32_t sequence;

    CHECK(host_pipeline_init(&small, ring_small, 4u) ==
          HOST_PIPELINE_STATUS_OK);
    CHECK(host_pipeline_frames_capacity(&small) == 4u);

    /*
     * Feed six distinguishable DC frames while a reference decimator sees the
     * same PCM. The reference output is the ground truth for stored frames,
     * including the filter's response across frame boundaries.
     */
    host_pipeline_decimator_init(&reference);
    expected_count = 0u;
    for (sequence = 1u; sequence <= 6u; ++sequence) {
        int16_t value = (int16_t)(sequence * 1000);

        fill_dc(samples, AUDIO_FRAME_SAMPLES, value);
        CHECK(host_pipeline_decimator_process(
                  &reference,
                  samples,
                  AUDIO_FRAME_SAMPLES,
                  &expected_small[expected_count]) ==
              HOST_PIPELINE_DECIMATED_FRAME_SAMPLES);
        expected_count += HOST_PIPELINE_DECIMATED_FRAME_SAMPLES;
        CHECK(push_dc_frame(&small, 0x5555u, sequence, value) ==
              HOST_PIPELINE_STATUS_COMPLETE);
    }
    CHECK(expected_count == sizeof(expected_small) / sizeof(expected_small[0]));

    stats = host_pipeline_get_stats(&small);
    CHECK(stats->frames_ok == 6u);
    CHECK(stats->overruns == 2u);
    CHECK(host_pipeline_frames_available(&small) == 4u);

    /* Newest two frames are reference frames 5 and 6 (indices 4, 5). */
    memset(window, 0, 2u * HOST_PIPELINE_DECIMATED_FRAME_SAMPLES * sizeof(int16_t));
    CHECK(host_pipeline_read_newest(&small, window, 2u) == 2u);
    CHECK(memcmp(
              window,
              &expected_small[4u * HOST_PIPELINE_DECIMATED_FRAME_SAMPLES],
              2u * HOST_PIPELINE_DECIMATED_FRAME_SAMPLES * sizeof(int16_t)) == 0);

    /* Too many frames requested: all-or-nothing, nothing is copied. */
    CHECK(host_pipeline_read_newest(&small, window, 5u) == 0u);

    /* Oldest available frame first: reference frames 3, 4, 5, 6. */
    CHECK(host_pipeline_read_newest(&small, window, 4u) == 4u);
    CHECK(memcmp(
              window,
              &expected_small[2u * HOST_PIPELINE_DECIMATED_FRAME_SAMPLES],
              4u * HOST_PIPELINE_DECIMATED_FRAME_SAMPLES * sizeof(int16_t)) == 0);

    host_pipeline_clear(&small);
    CHECK(host_pipeline_frames_available(&small) == 0u);
    CHECK(host_pipeline_read_newest(&small, window, 1u) == 0u);
    CHECK(host_pipeline_get_stats(&small)->overruns == 2u);

    /* Default capacity is 32 frames = 640 ms at 20 ms per frame. */
    {
        static int16_t ring_default[
            HOST_PIPELINE_DEFAULT_RING_FRAMES *
            HOST_PIPELINE_DECIMATED_FRAME_SAMPLES];
        host_pipeline_t defaulted;

        CHECK(host_pipeline_init(&defaulted, ring_default, 0u) ==
              HOST_PIPELINE_STATUS_OK);
        CHECK(host_pipeline_frames_capacity(&defaulted) ==
              HOST_PIPELINE_DEFAULT_RING_FRAMES);
        CHECK(HOST_PIPELINE_DEFAULT_RING_FRAMES *
                  AUDIO_FRAME_DURATION_MS == 640u);
    }

    /* A 2 s AI window needs 100 frames = 8000 samples at 4 kHz. */
    CHECK(host_pipeline_init(&ai, ring_ai, HOST_PIPELINE_AI_WINDOW_FRAMES) ==
          HOST_PIPELINE_STATUS_OK);
    host_pipeline_decimator_init(&reference);
    expected_count = 0u;
    for (sequence = 1u; sequence <= 105u; ++sequence) {
        fill_dc(samples, AUDIO_FRAME_SAMPLES, (int16_t)sequence);
        CHECK(host_pipeline_decimator_process(
                  &reference,
                  samples,
                  AUDIO_FRAME_SAMPLES,
                  &expected_ai[expected_count]) ==
              HOST_PIPELINE_DECIMATED_FRAME_SAMPLES);
        expected_count += HOST_PIPELINE_DECIMATED_FRAME_SAMPLES;
        CHECK(push_dc_frame(&ai, 0x6666u, sequence, (int16_t)sequence) ==
              HOST_PIPELINE_STATUS_COMPLETE);
    }
    CHECK(expected_count == sizeof(expected_ai) / sizeof(expected_ai[0]));

    stats = host_pipeline_get_stats(&ai);
    CHECK(stats->frames_ok == 105u);
    CHECK(stats->overruns == 5u);
    CHECK(host_pipeline_frames_available(&ai) ==
          HOST_PIPELINE_AI_WINDOW_FRAMES);

    /* The newest 100 frames are reference frames 6..105. */
    memset(window, 0, sizeof(window));
    CHECK(host_pipeline_read_newest(&ai, window, HOST_PIPELINE_AI_WINDOW_FRAMES) ==
          HOST_PIPELINE_AI_WINDOW_FRAMES);
    CHECK(memcmp(
              window,
              &expected_ai[5u * HOST_PIPELINE_DECIMATED_FRAME_SAMPLES],
              sizeof(window)) == 0);
    CHECK(window[0] ==
          expected_ai[5u * HOST_PIPELINE_DECIMATED_FRAME_SAMPLES]);
    CHECK(window[HOST_PIPELINE_AI_WINDOW_SAMPLES - 1u] ==
          expected_ai[expected_count - 1u]);

    CHECK(host_pipeline_read_newest_samples(
              &ai,
              window,
              HOST_PIPELINE_AI_WINDOW_SAMPLES) ==
          HOST_PIPELINE_AI_WINDOW_SAMPLES);
    CHECK(memcmp(
              window,
              &expected_ai[5u * HOST_PIPELINE_DECIMATED_FRAME_SAMPLES],
              sizeof(window)) == 0);
}

/* 7. Decimator passes 440 Hz, rejects 1900 Hz, stateful across chunks. */
static void test_decimator_response(void)
{
    enum {
        INPUT_SAMPLES = 4000,
        OUTPUT_SAMPLES = 2000
    };
    static int16_t input[INPUT_SAMPLES];
    static int16_t pass[OUTPUT_SAMPLES];
    static int16_t pass_chunked[OUTPUT_SAMPLES];
    static int16_t stop[OUTPUT_SAMPLES];
    static int16_t stop_chunked[OUTPUT_SAMPLES];
    host_pipeline_decimator_t decimator;
    size_t offset;
    size_t produced;
    double pass_ratio;
    double stop_ratio;

    fill_sine(input, INPUT_SAMPLES, 440.0, 0.5);
    host_pipeline_decimator_init(&decimator);
    CHECK(host_pipeline_decimator_process(
              &decimator,
              input,
              INPUT_SAMPLES,
              pass) == OUTPUT_SAMPLES);

    /* Chunked processing must be byte-identical: state is continuous. */
    host_pipeline_decimator_init(&decimator);
    produced = 0u;
    for (offset = 0u; offset < INPUT_SAMPLES; offset += AUDIO_FRAME_SAMPLES) {
        produced += host_pipeline_decimator_process(
            &decimator,
            &input[offset],
            AUDIO_FRAME_SAMPLES,
            &pass_chunked[produced]);
    }
    CHECK(produced == OUTPUT_SAMPLES);
    CHECK(memcmp(pass, pass_chunked, sizeof(pass)) == 0);

    pass_ratio = rms(&pass[100], OUTPUT_SAMPLES - 100u) /
                 rms(&input[100], INPUT_SAMPLES - 100u);
    CHECK(pass_ratio > 0.7); /* Node acceptance threshold */
    CHECK(pass_ratio > 0.9); /* Q15 measured margin (~1.0003) */

    fill_sine(input, INPUT_SAMPLES, 1900.0, 0.5);
    host_pipeline_decimator_init(&decimator);
    CHECK(host_pipeline_decimator_process(
              &decimator,
              input,
              INPUT_SAMPLES,
              stop) == OUTPUT_SAMPLES);
    host_pipeline_decimator_init(&decimator);
    produced = 0u;
    for (offset = 0u; offset < INPUT_SAMPLES; offset += AUDIO_FRAME_SAMPLES) {
        produced += host_pipeline_decimator_process(
            &decimator,
            &input[offset],
            AUDIO_FRAME_SAMPLES,
            &stop_chunked[produced]);
    }
    CHECK(produced == OUTPUT_SAMPLES);
    CHECK(memcmp(stop, stop_chunked, sizeof(stop)) == 0);

    stop_ratio = rms(&stop[100], OUTPUT_SAMPLES - 100u) /
                 rms(&input[100], INPUT_SAMPLES - 100u);
    CHECK(stop_ratio < 0.35); /* Node acceptance threshold */
    CHECK(stop_ratio < 0.1);  /* Q15 measured margin (~0.0436) */

    /* Odd chunk boundaries keep the output count correct (phase persistence). */
    host_pipeline_decimator_init(&decimator);
    CHECK(host_pipeline_decimator_process(&decimator, input, 1u, pass) == 1u);
    CHECK(host_pipeline_decimator_process(
              &decimator,
              &input[1],
              INPUT_SAMPLES - 1u,
              &pass[1]) == OUTPUT_SAMPLES - 1u);
}

/* 8. Same input twice (fresh instances and after reset) gives identical bytes. */
static void test_determinism(void)
{
    enum {
        FRAME_COUNT = 10
    };
    static int16_t stream[AUDIO_FRAME_SAMPLES * FRAME_COUNT];
    static int16_t samples[AUDIO_FRAME_SAMPLES];
    static uint8_t packets[FRAME_COUNT][2][TEST_PACKET_BYTES];
    static size_t packet_lengths[FRAME_COUNT][2];
    static int16_t ring_a[16u * HOST_PIPELINE_DECIMATED_FRAME_SAMPLES];
    static int16_t ring_b[16u * HOST_PIPELINE_DECIMATED_FRAME_SAMPLES];
    static int16_t output_a[FRAME_COUNT * HOST_PIPELINE_DECIMATED_FRAME_SAMPLES];
    static int16_t output_b[FRAME_COUNT * HOST_PIPELINE_DECIMATED_FRAME_SAMPLES];
    static int16_t output_c[FRAME_COUNT * HOST_PIPELINE_DECIMATED_FRAME_SAMPLES];
    host_pipeline_t pipeline_a;
    host_pipeline_t pipeline_b;
    uint8_t fragment_count = 0u;
    uint32_t frame;
    uint32_t fragment;

    fill_sine(stream, AUDIO_FRAME_SAMPLES * FRAME_COUNT, 440.0, 0.5);

    for (frame = 0u; frame < FRAME_COUNT; ++frame) {
        static uint8_t wire[AUDIO_WIRE_FRAME_BYTES];

        memcpy(
            samples,
            &stream[frame * AUDIO_FRAME_SAMPLES],
            sizeof(samples));
        CHECK(encode_wire_frame(wire, samples, frame, frame * 20u));
        for (fragment = 0u; fragment < 2u; ++fragment) {
            packet_lengths[frame][fragment] = make_fragment(
                packets[frame][fragment],
                wire,
                0x7777u,
                frame,
                (uint8_t)fragment,
                222u,
                &fragment_count);
            CHECK(fragment_count == 2u);
            CHECK(packet_lengths[frame][fragment] > 0u);
        }
    }

    CHECK(host_pipeline_init(&pipeline_a, ring_a, 16u) ==
          HOST_PIPELINE_STATUS_OK);
    CHECK(host_pipeline_init(&pipeline_b, ring_b, 16u) ==
          HOST_PIPELINE_STATUS_OK);

    for (frame = 0u; frame < FRAME_COUNT; ++frame) {
        for (fragment = 0u; fragment < 2u; ++fragment) {
            CHECK(host_pipeline_push(
                      &pipeline_a,
                      packets[frame][fragment],
                      packet_lengths[frame][fragment]) ==
                  (fragment == 0u ? HOST_PIPELINE_STATUS_INCOMPLETE
                                  : HOST_PIPELINE_STATUS_COMPLETE));
            CHECK(host_pipeline_push(
                      &pipeline_b,
                      packets[frame][fragment],
                      packet_lengths[frame][fragment]) ==
                  (fragment == 0u ? HOST_PIPELINE_STATUS_INCOMPLETE
                                  : HOST_PIPELINE_STATUS_COMPLETE));
        }
    }

    CHECK(host_pipeline_read_newest(&pipeline_a, output_a, FRAME_COUNT) ==
          FRAME_COUNT);
    CHECK(host_pipeline_read_newest(&pipeline_b, output_b, FRAME_COUNT) ==
          FRAME_COUNT);
    CHECK(memcmp(output_a, output_b, sizeof(output_a)) == 0);
    CHECK(memcmp(
              host_pipeline_get_stats(&pipeline_a),
              host_pipeline_get_stats(&pipeline_b),
              sizeof(host_pipeline_stats_t)) == 0);

    /* A full reset must reproduce the same stream from the same packets. */
    host_pipeline_reset(&pipeline_a);
    CHECK(host_pipeline_frames_available(&pipeline_a) == 0u);
    CHECK(host_pipeline_get_stats(&pipeline_a)->packets_in == 0u);
    for (frame = 0u; frame < FRAME_COUNT; ++frame) {
        for (fragment = 0u; fragment < 2u; ++fragment) {
            CHECK(host_pipeline_push(
                      &pipeline_a,
                      packets[frame][fragment],
                      packet_lengths[frame][fragment]) !=
                  HOST_PIPELINE_STATUS_INVALID_ARGUMENT);
        }
    }
    CHECK(host_pipeline_read_newest(&pipeline_a, output_c, FRAME_COUNT) ==
          FRAME_COUNT);
    CHECK(memcmp(output_a, output_c, sizeof(output_a)) == 0);
    CHECK(memcmp(
              host_pipeline_get_stats(&pipeline_a),
              host_pipeline_get_stats(&pipeline_b),
              sizeof(host_pipeline_stats_t)) == 0);
}

int main(void)
{
    test_out_of_order_fragments();
    test_duplicate_fragment();
    test_bad_audio_crc();
    test_missing_fragment();
    test_session_isolation();
    test_ring_wrap_and_window();
    test_decimator_response();
    test_determinism();

    if (failures != 0) {
        fprintf(stderr, "%d test(s) failed\n", failures);
        return 1;
    }

    printf("host_pipeline tests passed\n");
    return 0;
}
