#ifndef AUDIO_PROTOCOL_H
#define AUDIO_PROTOCOL_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/*
 * Phase 0 baseline, revision v0.2 (2026-10-07).
 *
 * 20..1000 Hz is the signal bandwidth. The acquisition rate is 8 kHz so
 * that anti-alias filtering and a later 2:1 decimation to 4 kHz are practical.
 *
 * The protocol is transport agnostic. The current product baseline uses a
 * wireless link from the probe to the host, and Bluetooth from the host to a
 * phone or server.
 */
#define AUDIO_PROTOCOL_VERSION       1u
#define AUDIO_PROTOCOL_MAGIC         0x31445541u /* "AUD1" on the wire (LE) */

#define AUDIO_SAMPLE_RATE_HZ         8000u
#define AUDIO_AI_SAMPLE_RATE_HZ      4000u
#define AUDIO_FRAME_DURATION_MS      20u
#define AUDIO_CHANNEL_COUNT          1u
#define AUDIO_BITS_PER_SAMPLE        16u
#define AUDIO_BYTES_PER_SAMPLE       2u

#define AUDIO_FRAME_SAMPLES \
    ((AUDIO_SAMPLE_RATE_HZ * AUDIO_FRAME_DURATION_MS) / 1000u)
#define AUDIO_FRAME_PAYLOAD_BYTES \
    (AUDIO_FRAME_SAMPLES * AUDIO_CHANNEL_COUNT * AUDIO_BYTES_PER_SAMPLE)

#define AUDIO_WIRE_HEADER_BYTES      30u
#define AUDIO_WIRE_CRC_BYTES         2u
#define AUDIO_WIRE_FRAME_BYTES \
    (AUDIO_WIRE_HEADER_BYTES + AUDIO_FRAME_PAYLOAD_BYTES + AUDIO_WIRE_CRC_BYTES)

typedef int16_t audio_sample_t;

typedef enum {
    AUDIO_CODEC_PCM_S16_LE = 1
} audio_codec_t;

typedef enum {
    AUDIO_STATUS_OK = 0,
    AUDIO_STATUS_INVALID_ARGUMENT = -1,
    AUDIO_STATUS_BUFFER_TOO_SMALL = -2,
    AUDIO_STATUS_TRUNCATED = -3,
    AUDIO_STATUS_BAD_MAGIC = -4,
    AUDIO_STATUS_BAD_VERSION = -5,
    AUDIO_STATUS_BAD_HEADER = -6,
    AUDIO_STATUS_BAD_CODEC = -7,
    AUDIO_STATUS_BAD_CHANNELS = -8,
    AUDIO_STATUS_BAD_SAMPLE_RATE = -9,
    AUDIO_STATUS_BAD_FRAME_SAMPLES = -10,
    AUDIO_STATUS_BAD_PAYLOAD_SIZE = -11,
    AUDIO_STATUS_BAD_CRC = -12
} audio_protocol_status_t;

typedef enum {
    AUDIO_FLAG_TIMESTAMP_VALID = 1u << 0
} audio_frame_flags_t;

typedef enum {
    AUDIO_WIRE_OFFSET_MAGIC = 0,
    AUDIO_WIRE_OFFSET_VERSION = 4,
    AUDIO_WIRE_OFFSET_FLAGS = 5,
    AUDIO_WIRE_OFFSET_HEADER_BYTES = 6,
    AUDIO_WIRE_OFFSET_CODEC = 8,
    AUDIO_WIRE_OFFSET_CHANNELS = 9,
    AUDIO_WIRE_OFFSET_BITS_PER_SAMPLE = 10,
    AUDIO_WIRE_OFFSET_RESERVED = 11,
    AUDIO_WIRE_OFFSET_SAMPLE_RATE_HZ = 12,
    AUDIO_WIRE_OFFSET_FRAME_SAMPLES = 16,
    AUDIO_WIRE_OFFSET_SEQUENCE = 20,
    AUDIO_WIRE_OFFSET_TIMESTAMP_MS = 24,
    AUDIO_WIRE_OFFSET_PAYLOAD_BYTES = 28,
    AUDIO_WIRE_OFFSET_PAYLOAD = 30
} audio_wire_offset_t;

/*
 * Local in-memory frame. This structure is intentionally not the wire format:
 * compiler padding must never become part of the transport contract.
 */
typedef struct {
    audio_sample_t samples[AUDIO_FRAME_SAMPLES];
    uint32_t sample_rate_hz;
    uint32_t timestamp_ms;
    uint32_t sequence;
    uint8_t channel_count;
    uint8_t reserved[3];
} audio_frame_t;

typedef struct {
    uint8_t bytes[AUDIO_WIRE_HEADER_BYTES];
} audio_wire_header_t;

/* Serialize fields into the fixed little-endian wire header. */
int audio_protocol_encode_header(
    audio_wire_header_t *header,
    const audio_frame_t *frame,
    uint8_t flags);

/*
 * Parse and validate a fixed wire header without depending on host alignment
 * or endianness.
 */
int audio_protocol_decode_header(
    const audio_wire_header_t *header,
    audio_frame_t *frame,
    uint8_t *flags);

/*
 * Encode or decode a complete AUD1 frame. Decode accepts a buffer that may
 * contain additional bytes; consumed receives the number of bytes belonging
 * to the decoded frame.
 */
int audio_protocol_encode_frame(
    uint8_t *output,
    size_t output_capacity,
    const audio_frame_t *frame,
    uint8_t flags,
    size_t *output_length);

int audio_protocol_decode_frame(
    const uint8_t *input,
    size_t input_length,
    audio_frame_t *frame,
    uint8_t *flags,
    size_t *consumed);

/*
 * CRC-16/CCITT-FALSE:
 * polynomial 0x1021, initial value 0xFFFF, no reflection, xorout 0x0000.
 */
uint16_t audio_protocol_crc16(
    const uint8_t *data,
    size_t length);

/* Compile-time guards for the current frozen baseline. */
#define AUDIO_STATIC_ASSERT(condition, name) \
    typedef char audio_static_assert_##name[(condition) ? 1 : -1]

AUDIO_STATIC_ASSERT(AUDIO_FRAME_SAMPLES == 160u, frame_samples_are_160);
AUDIO_STATIC_ASSERT(AUDIO_FRAME_PAYLOAD_BYTES == 320u, payload_is_320_bytes);
AUDIO_STATIC_ASSERT(AUDIO_WIRE_FRAME_BYTES == 352u, wire_frame_is_352_bytes);
AUDIO_STATIC_ASSERT(AUDIO_WIRE_OFFSET_PAYLOAD == 30, payload_offset_is_30);
AUDIO_STATIC_ASSERT(
    AUDIO_WIRE_OFFSET_PAYLOAD == (int)AUDIO_WIRE_HEADER_BYTES,
    payload_matches_header_size);
AUDIO_STATIC_ASSERT(offsetof(audio_frame_t, sample_rate_hz) == 320u, pcm_ends_at_320);
AUDIO_STATIC_ASSERT(offsetof(audio_frame_t, channel_count) == 332u, channels_at_332);
AUDIO_STATIC_ASSERT(sizeof(audio_frame_t) == 336u, local_frame_is_336_bytes);

#ifdef __cplusplus
}
#endif

#endif
