#include "audio_protocol.h"

#include <string.h>

static uint16_t read_le16(const uint8_t *input)
{
    return (uint16_t)((uint16_t)input[0] | ((uint16_t)input[1] << 8));
}

static uint32_t read_le32(const uint8_t *input)
{
    return (uint32_t)input[0] |
           ((uint32_t)input[1] << 8) |
           ((uint32_t)input[2] << 16) |
           ((uint32_t)input[3] << 24);
}

static void write_le16(uint8_t *output, uint16_t value)
{
    output[0] = (uint8_t)value;
    output[1] = (uint8_t)(value >> 8);
}

static void write_le32(uint8_t *output, uint32_t value)
{
    output[0] = (uint8_t)value;
    output[1] = (uint8_t)(value >> 8);
    output[2] = (uint8_t)(value >> 16);
    output[3] = (uint8_t)(value >> 24);
}

static int validate_frame_fields(const audio_frame_t *frame)
{
    if (frame == NULL) {
        return AUDIO_STATUS_INVALID_ARGUMENT;
    }
    if (frame->sample_rate_hz != AUDIO_SAMPLE_RATE_HZ) {
        return AUDIO_STATUS_BAD_SAMPLE_RATE;
    }
    if (frame->channel_count != AUDIO_CHANNEL_COUNT) {
        return AUDIO_STATUS_BAD_CHANNELS;
    }
    return AUDIO_STATUS_OK;
}

uint16_t audio_protocol_crc16(const uint8_t *data, size_t length)
{
    uint16_t crc = 0xFFFFu;
    size_t i;

    if (data == NULL && length != 0u) {
        return crc;
    }

    for (i = 0; i < length; ++i) {
        unsigned int bit;
        crc ^= (uint16_t)((uint16_t)data[i] << 8);
        for (bit = 0; bit < 8u; ++bit) {
            if ((crc & 0x8000u) != 0u) {
                crc = (uint16_t)((crc << 1) ^ 0x1021u);
            } else {
                crc = (uint16_t)(crc << 1);
            }
        }
    }

    return crc;
}

int audio_protocol_encode_header(
    audio_wire_header_t *header,
    const audio_frame_t *frame,
    uint8_t flags)
{
    int status;

    if (header == NULL) {
        return AUDIO_STATUS_INVALID_ARGUMENT;
    }

    status = validate_frame_fields(frame);
    if (status != AUDIO_STATUS_OK) {
        return status;
    }

    memset(header->bytes, 0, sizeof(header->bytes));
    write_le32(&header->bytes[AUDIO_WIRE_OFFSET_MAGIC], AUDIO_PROTOCOL_MAGIC);
    header->bytes[AUDIO_WIRE_OFFSET_VERSION] = (uint8_t)AUDIO_PROTOCOL_VERSION;
    header->bytes[AUDIO_WIRE_OFFSET_FLAGS] = flags;
    write_le16(
        &header->bytes[AUDIO_WIRE_OFFSET_HEADER_BYTES],
        (uint16_t)AUDIO_WIRE_HEADER_BYTES);
    header->bytes[AUDIO_WIRE_OFFSET_CODEC] = (uint8_t)AUDIO_CODEC_PCM_S16_LE;
    header->bytes[AUDIO_WIRE_OFFSET_CHANNELS] = (uint8_t)AUDIO_CHANNEL_COUNT;
    header->bytes[AUDIO_WIRE_OFFSET_BITS_PER_SAMPLE] =
        (uint8_t)AUDIO_BITS_PER_SAMPLE;
    header->bytes[AUDIO_WIRE_OFFSET_RESERVED] = 0u;
    write_le32(
        &header->bytes[AUDIO_WIRE_OFFSET_SAMPLE_RATE_HZ],
        frame->sample_rate_hz);
    write_le32(
        &header->bytes[AUDIO_WIRE_OFFSET_FRAME_SAMPLES],
        (uint32_t)AUDIO_FRAME_SAMPLES);
    write_le32(&header->bytes[AUDIO_WIRE_OFFSET_SEQUENCE], frame->sequence);
    write_le32(&header->bytes[AUDIO_WIRE_OFFSET_TIMESTAMP_MS], frame->timestamp_ms);
    write_le16(
        &header->bytes[AUDIO_WIRE_OFFSET_PAYLOAD_BYTES],
        (uint16_t)AUDIO_FRAME_PAYLOAD_BYTES);

    return AUDIO_STATUS_OK;
}

int audio_protocol_decode_header(
    const audio_wire_header_t *header,
    audio_frame_t *frame,
    uint8_t *flags)
{
    uint16_t header_bytes;
    uint16_t payload_bytes;

    if (header == NULL || frame == NULL) {
        return AUDIO_STATUS_INVALID_ARGUMENT;
    }

    if (read_le32(&header->bytes[AUDIO_WIRE_OFFSET_MAGIC]) !=
        AUDIO_PROTOCOL_MAGIC) {
        return AUDIO_STATUS_BAD_MAGIC;
    }
    if (header->bytes[AUDIO_WIRE_OFFSET_VERSION] !=
        (uint8_t)AUDIO_PROTOCOL_VERSION) {
        return AUDIO_STATUS_BAD_VERSION;
    }

    header_bytes = read_le16(&header->bytes[AUDIO_WIRE_OFFSET_HEADER_BYTES]);
    if (header_bytes != (uint16_t)AUDIO_WIRE_HEADER_BYTES) {
        return AUDIO_STATUS_BAD_HEADER;
    }
    if (header->bytes[AUDIO_WIRE_OFFSET_CODEC] !=
        (uint8_t)AUDIO_CODEC_PCM_S16_LE) {
        return AUDIO_STATUS_BAD_CODEC;
    }
    if (header->bytes[AUDIO_WIRE_OFFSET_CHANNELS] !=
        (uint8_t)AUDIO_CHANNEL_COUNT) {
        return AUDIO_STATUS_BAD_CHANNELS;
    }
    if (header->bytes[AUDIO_WIRE_OFFSET_BITS_PER_SAMPLE] !=
        (uint8_t)AUDIO_BITS_PER_SAMPLE ||
        header->bytes[AUDIO_WIRE_OFFSET_RESERVED] != 0u) {
        return AUDIO_STATUS_BAD_HEADER;
    }

    frame->sample_rate_hz =
        read_le32(&header->bytes[AUDIO_WIRE_OFFSET_SAMPLE_RATE_HZ]);
    if (frame->sample_rate_hz != AUDIO_SAMPLE_RATE_HZ) {
        return AUDIO_STATUS_BAD_SAMPLE_RATE;
    }
    if (read_le32(&header->bytes[AUDIO_WIRE_OFFSET_FRAME_SAMPLES]) !=
        (uint32_t)AUDIO_FRAME_SAMPLES) {
        return AUDIO_STATUS_BAD_FRAME_SAMPLES;
    }

    payload_bytes = read_le16(&header->bytes[AUDIO_WIRE_OFFSET_PAYLOAD_BYTES]);
    if (payload_bytes != (uint16_t)AUDIO_FRAME_PAYLOAD_BYTES) {
        return AUDIO_STATUS_BAD_PAYLOAD_SIZE;
    }

    frame->sequence = read_le32(&header->bytes[AUDIO_WIRE_OFFSET_SEQUENCE]);
    frame->timestamp_ms =
        read_le32(&header->bytes[AUDIO_WIRE_OFFSET_TIMESTAMP_MS]);
    frame->channel_count = (uint8_t)AUDIO_CHANNEL_COUNT;
    memset(frame->reserved, 0, sizeof(frame->reserved));

    if (flags != NULL) {
        *flags = header->bytes[AUDIO_WIRE_OFFSET_FLAGS];
    }

    return AUDIO_STATUS_OK;
}

int audio_protocol_encode_frame(
    uint8_t *output,
    size_t output_capacity,
    const audio_frame_t *frame,
    uint8_t flags,
    size_t *output_length)
{
    audio_wire_header_t header;
    uint16_t crc;
    size_t i;
    int status;

    if (output == NULL || output_length == NULL) {
        return AUDIO_STATUS_INVALID_ARGUMENT;
    }
    if (output_capacity < AUDIO_WIRE_FRAME_BYTES) {
        return AUDIO_STATUS_BUFFER_TOO_SMALL;
    }

    status = audio_protocol_encode_header(&header, frame, flags);
    if (status != AUDIO_STATUS_OK) {
        return status;
    }

    memcpy(output, header.bytes, AUDIO_WIRE_HEADER_BYTES);
    for (i = 0; i < AUDIO_FRAME_SAMPLES; ++i) {
        write_le16(
            &output[AUDIO_WIRE_OFFSET_PAYLOAD + (i * AUDIO_BYTES_PER_SAMPLE)],
            (uint16_t)frame->samples[i]);
    }

    crc = audio_protocol_crc16(
        output,
        AUDIO_WIRE_HEADER_BYTES + AUDIO_FRAME_PAYLOAD_BYTES);
    write_le16(
        &output[AUDIO_WIRE_HEADER_BYTES + AUDIO_FRAME_PAYLOAD_BYTES],
        crc);
    *output_length = AUDIO_WIRE_FRAME_BYTES;

    return AUDIO_STATUS_OK;
}

int audio_protocol_decode_frame(
    const uint8_t *input,
    size_t input_length,
    audio_frame_t *frame,
    uint8_t *flags,
    size_t *consumed)
{
    audio_wire_header_t header;
    uint16_t expected_crc;
    uint16_t actual_crc;
    uint8_t decoded_flags = 0u;
    size_t i;
    int status;

    if (input == NULL || frame == NULL) {
        return AUDIO_STATUS_INVALID_ARGUMENT;
    }
    if (input_length < AUDIO_WIRE_FRAME_BYTES) {
        return AUDIO_STATUS_TRUNCATED;
    }

    memcpy(header.bytes, input, AUDIO_WIRE_HEADER_BYTES);
    status = audio_protocol_decode_header(&header, frame, &decoded_flags);
    if (status != AUDIO_STATUS_OK) {
        return status;
    }

    expected_crc = read_le16(
        &input[AUDIO_WIRE_HEADER_BYTES + AUDIO_FRAME_PAYLOAD_BYTES]);
    actual_crc = audio_protocol_crc16(
        input,
        AUDIO_WIRE_HEADER_BYTES + AUDIO_FRAME_PAYLOAD_BYTES);
    if (expected_crc != actual_crc) {
        return AUDIO_STATUS_BAD_CRC;
    }

    for (i = 0; i < AUDIO_FRAME_SAMPLES; ++i) {
        frame->samples[i] = (audio_sample_t)read_le16(
            &input[AUDIO_WIRE_OFFSET_PAYLOAD + (i * AUDIO_BYTES_PER_SAMPLE)]);
    }
    if (flags != NULL) {
        *flags = decoded_flags;
    }
    if (consumed != NULL) {
        *consumed = AUDIO_WIRE_FRAME_BYTES;
    }

    return AUDIO_STATUS_OK;
}
