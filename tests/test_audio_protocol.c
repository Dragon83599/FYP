#include "audio_protocol.h"

#include <stdio.h>
#include <string.h>

static int failures = 0;

#define CHECK(condition)                                                       \
    do {                                                                       \
        if (!(condition)) {                                                    \
            ++failures;                                                        \
            fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #condition); \
        }                                                                      \
    } while (0)

static void test_compile_time_contract(void)
{
    CHECK(AUDIO_PROTOCOL_VERSION == 1u);
    CHECK(AUDIO_SAMPLE_RATE_HZ == 8000u);
    CHECK(AUDIO_AI_SAMPLE_RATE_HZ == 4000u);
    CHECK(AUDIO_FRAME_DURATION_MS == 20u);
    CHECK(AUDIO_FRAME_SAMPLES == 160u);
    CHECK(AUDIO_FRAME_PAYLOAD_BYTES == 320u);
    CHECK(AUDIO_WIRE_HEADER_BYTES == 30u);
    CHECK(AUDIO_WIRE_FRAME_BYTES == 352u);
    CHECK(sizeof(audio_frame_t) == 336u);
}

static audio_frame_t make_zero_frame(void)
{
    audio_frame_t frame;

    memset(&frame, 0, sizeof(frame));
    frame.sample_rate_hz = AUDIO_SAMPLE_RATE_HZ;
    frame.timestamp_ms = 1000u;
    frame.sequence = 1u;
    frame.channel_count = AUDIO_CHANNEL_COUNT;
    return frame;
}

static void test_golden_frame(void)
{
    static const uint8_t expected_header[AUDIO_WIRE_HEADER_BYTES] = {
        0x41, 0x55, 0x44, 0x31, 0x01, 0x01, 0x1E, 0x00,
        0x01, 0x01, 0x10, 0x00, 0x40, 0x1F, 0x00, 0x00,
        0xA0, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00,
        0xE8, 0x03, 0x00, 0x00, 0x40, 0x01
    };
    audio_frame_t input = make_zero_frame();
    audio_frame_t output;
    uint8_t encoded[AUDIO_WIRE_FRAME_BYTES];
    uint8_t flags = 0u;
    size_t encoded_length = 0u;
    size_t consumed = 0u;
    int status;

    status = audio_protocol_encode_frame(
        encoded,
        sizeof(encoded),
        &input,
        AUDIO_FLAG_TIMESTAMP_VALID,
        &encoded_length);
    CHECK(status == AUDIO_STATUS_OK);
    CHECK(encoded_length == AUDIO_WIRE_FRAME_BYTES);
    CHECK(memcmp(encoded, expected_header, sizeof(expected_header)) == 0);
    CHECK(encoded[350] == 0x5Eu);
    CHECK(encoded[351] == 0x7Bu);

    memset(&output, 0, sizeof(output));
    status = audio_protocol_decode_frame(
        encoded,
        sizeof(encoded),
        &output,
        &flags,
        &consumed);
    CHECK(status == AUDIO_STATUS_OK);
    CHECK(consumed == AUDIO_WIRE_FRAME_BYTES);
    CHECK(flags == AUDIO_FLAG_TIMESTAMP_VALID);
    CHECK(output.sequence == input.sequence);
    CHECK(output.timestamp_ms == input.timestamp_ms);
    CHECK(output.sample_rate_hz == input.sample_rate_hz);
    CHECK(output.channel_count == input.channel_count);
    CHECK(memcmp(output.samples, input.samples, sizeof(input.samples)) == 0);
}

static void test_round_trip_arbitrary_samples(void)
{
    audio_frame_t input = make_zero_frame();
    audio_frame_t output;
    uint8_t encoded[AUDIO_WIRE_FRAME_BYTES];
    size_t encoded_length = 0u;
    size_t i;

    input.sequence = 0x12345678u;
    input.timestamp_ms = 0x01020304u;
    for (i = 0; i < AUDIO_FRAME_SAMPLES; ++i) {
        input.samples[i] = (audio_sample_t)((int32_t)(i * 257u) - 16000);
    }

    CHECK(audio_protocol_encode_frame(
              encoded,
              sizeof(encoded),
              &input,
              AUDIO_FLAG_TIMESTAMP_VALID,
              &encoded_length) == AUDIO_STATUS_OK);
    CHECK(encoded_length == AUDIO_WIRE_FRAME_BYTES);
    CHECK(audio_protocol_decode_frame(
              encoded,
              sizeof(encoded),
              &output,
              NULL,
              NULL) == AUDIO_STATUS_OK);
    CHECK(output.sequence == input.sequence);
    CHECK(output.timestamp_ms == input.timestamp_ms);
    CHECK(memcmp(output.samples, input.samples, sizeof(input.samples)) == 0);
}

static void test_invalid_frames(void)
{
    audio_frame_t frame = make_zero_frame();
    audio_frame_t decoded;
    uint8_t encoded[AUDIO_WIRE_FRAME_BYTES];
    size_t encoded_length = 0u;

    CHECK(audio_protocol_encode_frame(
              encoded,
              sizeof(encoded),
              &frame,
              AUDIO_FLAG_TIMESTAMP_VALID,
              &encoded_length) == AUDIO_STATUS_OK);

    encoded[0] = 0u;
    CHECK(audio_protocol_decode_frame(
              encoded,
              sizeof(encoded),
              &decoded,
              NULL,
              NULL) == AUDIO_STATUS_BAD_MAGIC);

    encoded[0] = 0x41u;
    encoded[100] ^= 0x01u;
    CHECK(audio_protocol_decode_frame(
              encoded,
              sizeof(encoded),
              &decoded,
              NULL,
              NULL) == AUDIO_STATUS_BAD_CRC);

    CHECK(audio_protocol_decode_frame(
              encoded,
              AUDIO_WIRE_FRAME_BYTES - 1u,
              &decoded,
              NULL,
              NULL) == AUDIO_STATUS_TRUNCATED);

    CHECK(audio_protocol_encode_frame(
              encoded,
              AUDIO_WIRE_FRAME_BYTES - 1u,
              &frame,
              AUDIO_FLAG_TIMESTAMP_VALID,
              &encoded_length) == AUDIO_STATUS_BUFFER_TOO_SMALL);
}

static void test_crc_vectors(void)
{
    static const uint8_t check[] = {'1', '2', '3', '4', '5', '6', '7', '8', '9'};
    CHECK(audio_protocol_crc16(NULL, 0u) == 0xFFFFu);
    CHECK(audio_protocol_crc16(check, sizeof(check)) == 0x29B1u);
}

int main(void)
{
    test_compile_time_contract();
    test_golden_frame();
    test_round_trip_arbitrary_samples();
    test_invalid_frames();
    test_crc_vectors();

    if (failures != 0) {
        fprintf(stderr, "%d test(s) failed\n", failures);
        return 1;
    }

    printf("audio_protocol tests passed\n");
    return 0;
}
