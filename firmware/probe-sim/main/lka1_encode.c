#include "lka1_encode.h"

#include <string.h>

/* Little-endian writers: the wire format is LE on every field, and the ESP32 is
 * little-endian, but writing byte by byte keeps this code portable to a host
 * test build on any architecture. */
static void put_u8(uint8_t *out, size_t offset, uint8_t value)
{
    out[offset] = value;
}

static void put_u16(uint8_t *out, size_t offset, uint16_t value)
{
    out[offset] = (uint8_t)(value & 0xff);
    out[offset + 1] = (uint8_t)((value >> 8) & 0xff);
}

static void put_u32(uint8_t *out, size_t offset, uint32_t value)
{
    out[offset] = (uint8_t)(value & 0xff);
    out[offset + 1] = (uint8_t)((value >> 8) & 0xff);
    out[offset + 2] = (uint8_t)((value >> 16) & 0xff);
    out[offset + 3] = (uint8_t)((value >> 24) & 0xff);
}

uint16_t lka1_crc16(const uint8_t *data, size_t length)
{
    uint16_t crc = 0xffff;
    for (size_t i = 0; i < length; i++) {
        crc ^= (uint16_t)((uint16_t)data[i] << 8);
        for (int bit = 0; bit < 8; bit++) {
            if (crc & 0x8000) {
                crc = (uint16_t)((crc << 1) ^ 0x1021);
            } else {
                crc = (uint16_t)(crc << 1);
            }
        }
    }
    return crc;
}

size_t aud1_encode(uint8_t *out, size_t out_size,
                   const int16_t *samples, uint32_t sequence, uint32_t timestamp_ms)
{
    if (out == NULL || samples == NULL || out_size < AUD1_FRAME_BYTES) {
        return 0;
    }
    memset(out, 0, AUD1_FRAME_BYTES);

    /* "AUD1" magic, little-endian 0x31445541 */
    put_u32(out, 0, 0x31445541u);
    put_u8(out, 4, 1);                              /* version */
    put_u8(out, 5, 0x01);                           /* flags: timestamp valid */
    put_u16(out, 6, AUD1_HEADER_BYTES);             /* header_bytes */
    put_u8(out, 8, AUD1_CODEC_PCM_S16_LE);          /* codec */
    put_u8(out, 9, 1);                              /* channels */
    put_u8(out, 10, 16);                            /* bits per sample */
    put_u8(out, 11, 0);                             /* reserved */
    put_u32(out, 12, AUD1_SAMPLE_RATE_HZ);          /* sample_rate_hz */
    put_u32(out, 16, AUD1_FRAME_SAMPLES);           /* frame_samples */
    put_u32(out, 20, sequence);                     /* sequence */
    put_u32(out, 24, timestamp_ms);                 /* timestamp_ms */
    put_u16(out, 28, AUD1_PAYLOAD_BYTES);           /* payload_bytes */

    for (int i = 0; i < AUD1_FRAME_SAMPLES; i++) {
        put_u16(out, AUD1_HEADER_BYTES + (size_t)i * 2, (uint16_t)samples[i]);
    }

    put_u16(out, AUD1_HEADER_BYTES + AUD1_PAYLOAD_BYTES,
            lka1_crc16(out, AUD1_HEADER_BYTES + AUD1_PAYLOAD_BYTES));
    return AUD1_FRAME_BYTES;
}

size_t lka1_encode_packet(uint8_t *out, size_t out_size,
                          uint8_t type, uint8_t flags, uint16_t session_id,
                          uint32_t frame_sequence, uint8_t fragment_index,
                          uint8_t fragment_count, uint16_t fragment_offset,
                          const uint8_t *payload, uint16_t payload_size)
{
    if (out == NULL || payload == NULL || payload_size == 0) {
        return 0;
    }
    if (payload_size > LKA1_MAX_FRAGMENT) {
        return 0;
    }
    size_t total = (size_t)LKA1_HEADER_BYTES + payload_size + LKA1_CRC_BYTES;
    if (out_size < total) {
        return 0;
    }

    put_u32(out, 0, 0x31414b4cu);                   /* "LKA1" */
    put_u8(out, 4, 1);                              /* version */
    put_u8(out, 5, type);
    put_u8(out, 6, flags);
    put_u8(out, 7, 0);                              /* reserved */
    put_u16(out, 8, session_id);
    put_u32(out, 10, frame_sequence);
    put_u8(out, 14, fragment_index);
    put_u8(out, 15, fragment_count);
    put_u16(out, 16, fragment_offset);
    put_u16(out, 18, payload_size);
    memcpy(out + LKA1_HEADER_BYTES, payload, payload_size);
    put_u16(out, LKA1_HEADER_BYTES + payload_size,
            lka1_crc16(out, LKA1_HEADER_BYTES + payload_size));
    return total;
}

size_t lka1_encode_command(uint8_t *out, size_t out_size, uint16_t session_id,
                           uint8_t command, uint16_t arg, uint32_t token)
{
    uint8_t payload[LKA1_COMMAND_PAYLOAD_BYTES];
    payload[0] = command;
    payload[1] = 0;                                  /* status: request */
    payload[2] = (uint8_t)(arg & 0xff);
    payload[3] = (uint8_t)((arg >> 8) & 0xff);
    put_u32(payload, 4, token);

    return lka1_encode_packet(out, out_size, LKA1_TYPE_COMMAND, 0, session_id,
                              0, 0, 1, 0, payload, LKA1_COMMAND_PAYLOAD_BYTES);
}
