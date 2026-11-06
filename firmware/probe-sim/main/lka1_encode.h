/*
 * AUD1 / LKA1 encoding in plain C, with no ESP-IDF dependency.
 *
 * The probe has to produce exactly the bytes the phone expects: a 352-byte AUD1
 * frame (30 B header + 320 B PCM + 2 B CRC16), split into 222-byte LKA1 packets
 * (20 B header + payload + 2 B CRC16). Getting this wrong produces no error at
 * either end -- just a phone that reassembles something that is not the audio.
 *
 * Keeping it IDF-free means it can be compiled and checked on the host, against
 * the two golden vectors that already exist in the repository, before the board
 * is even unpacked: see tests/c/lka1_vectors.c.
 */

#ifndef LKA1_ENCODE_H
#define LKA1_ENCODE_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define AUD1_FRAME_BYTES        352
#define AUD1_HEADER_BYTES       30
#define AUD1_PAYLOAD_BYTES      320   /* 160 samples x 2 bytes */
#define AUD1_FRAME_SAMPLES      160
#define AUD1_SAMPLE_RATE_HZ     8000
#define AUD1_CODEC_PCM_S16_LE   1

#define LKA1_HEADER_BYTES       20
#define LKA1_CRC_BYTES          2
#define LKA1_MAX_FRAGMENT       222
#define LKA1_MAX_PACKET_BYTES   (LKA1_HEADER_BYTES + LKA1_MAX_FRAGMENT + LKA1_CRC_BYTES)

#define LKA1_TYPE_DATA          1
#define LKA1_TYPE_COMMAND       4

#define LKA1_FLAG_START         0x01
#define LKA1_FLAG_END           0x02

#define LKA1_COMMAND_PAYLOAD_BYTES 8

/* Control command ids, matching pwa/gatt.js. */
#define LKA1_CMD_START          1
#define LKA1_CMD_STOP           2
#define LKA1_CMD_SET_GAIN       3
#define LKA1_CMD_GET_STATUS     4

/** CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF), the same as the host. */
uint16_t lka1_crc16(const uint8_t *data, size_t length);

/**
 * Encode one 352-byte AUD1 frame from 160 int16 samples.
 *
 * Returns the number of bytes written (always AUD1_FRAME_BYTES), or 0 if the
 * output buffer is too small.
 */
size_t aud1_encode(uint8_t *out, size_t out_size,
                   const int16_t *samples, uint32_t sequence, uint32_t timestamp_ms);

/**
 * Encode one LKA1 packet.
 *
 * Returns the packet length (header + payload + CRC), or 0 if the arguments do
 * not fit: payload must be 1..LKA1_MAX_FRAGMENT and the buffer large enough.
 */
size_t lka1_encode_packet(uint8_t *out, size_t out_size,
                          uint8_t type, uint8_t flags, uint16_t session_id,
                          uint32_t frame_sequence, uint8_t fragment_index,
                          uint8_t fragment_count, uint16_t fragment_offset,
                          const uint8_t *payload, uint16_t payload_size);

/**
 * Encode the optional type=4 COMMAND packet (START/STOP/...).
 *
 * Returns LKA1_HEADER_BYTES + 8 + 2 = 30 on success, 0 otherwise.
 */
size_t lka1_encode_command(uint8_t *out, size_t out_size, uint16_t session_id,
                           uint8_t command, uint16_t arg, uint32_t token);

#endif /* LKA1_ENCODE_H */
