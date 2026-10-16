#ifndef LINK_A_PROTOCOL_H
#define LINK_A_PROTOCOL_H

#include "audio_protocol.h"

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/*
 * Transport-independent fragmentation for link A (wireless probe -> host).
 * The radio may use BLE, a 2.4 GHz proprietary link, Wi-Fi, or a test pipe.
 */
#define LINK_A_PROTOCOL_VERSION              1u
#define LINK_A_PROTOCOL_MAGIC                0x31414B4Cu /* "LKA1" on wire (LE) */

#define LINK_A_WIRE_HEADER_BYTES             20u
#define LINK_A_WIRE_CRC_BYTES                2u
#define LINK_A_WIRE_OVERHEAD_BYTES \
    (LINK_A_WIRE_HEADER_BYTES + LINK_A_WIRE_CRC_BYTES)

#define LINK_A_BLE_ATT_MTU                    247u
#define LINK_A_DEFAULT_LINK_PAYLOAD_BYTES     (LINK_A_BLE_ATT_MTU - 3u)
#define LINK_A_DEFAULT_MAX_FRAGMENT_PAYLOAD \
    (LINK_A_DEFAULT_LINK_PAYLOAD_BYTES - LINK_A_WIRE_OVERHEAD_BYTES)

#define LINK_A_MAX_FRAGMENTS                 255u
#define LINK_A_MAX_FRAME_BYTES               AUDIO_WIRE_FRAME_BYTES

typedef enum {
    LINK_A_PACKET_DATA = 1,
    LINK_A_PACKET_ACK = 2,
    LINK_A_PACKET_NACK = 3
} link_a_packet_type_t;

typedef enum {
    LINK_A_FLAG_START = 1u << 0,
    LINK_A_FLAG_END = 1u << 1,
    LINK_A_FLAG_RETRANSMISSION = 1u << 2
} link_a_packet_flags_t;

typedef enum {
    LINK_A_STATUS_OK = 0,
    LINK_A_STATUS_IN_PROGRESS = 1,
    LINK_A_STATUS_COMPLETE = 2,
    LINK_A_STATUS_DUPLICATE = 3,
    LINK_A_STATUS_INVALID_ARGUMENT = -1,
    LINK_A_STATUS_BUFFER_TOO_SMALL = -2,
    LINK_A_STATUS_TRUNCATED = -3,
    LINK_A_STATUS_BAD_MAGIC = -4,
    LINK_A_STATUS_BAD_VERSION = -5,
    LINK_A_STATUS_BAD_HEADER = -6,
    LINK_A_STATUS_BAD_TYPE = -7,
    LINK_A_STATUS_BAD_FRAGMENT = -8,
    LINK_A_STATUS_BAD_CRC = -9,
    LINK_A_STATUS_CONFLICT = -10
} link_a_status_t;

typedef enum {
    LINK_A_OFFSET_MAGIC = 0,
    LINK_A_OFFSET_VERSION = 4,
    LINK_A_OFFSET_TYPE = 5,
    LINK_A_OFFSET_FLAGS = 6,
    LINK_A_OFFSET_RESERVED = 7,
    LINK_A_OFFSET_SESSION_ID = 8,
    LINK_A_OFFSET_FRAME_SEQUENCE = 10,
    LINK_A_OFFSET_FRAGMENT_INDEX = 14,
    LINK_A_OFFSET_FRAGMENT_COUNT = 15,
    LINK_A_OFFSET_FRAGMENT_OFFSET = 16,
    LINK_A_OFFSET_FRAGMENT_BYTES = 18,
    LINK_A_OFFSET_PAYLOAD = 20
} link_a_wire_offset_t;

typedef struct {
    uint8_t type;
    uint8_t flags;
    uint16_t session_id;
    uint32_t frame_sequence;
    uint8_t fragment_index;
    uint8_t fragment_count;
    uint16_t fragment_offset;
    uint16_t fragment_bytes;
    const uint8_t *payload;
} link_a_packet_t;

typedef struct {
    uint8_t active;
    uint16_t session_id;
    uint32_t frame_sequence;
    uint8_t fragment_count;
    uint8_t received_fragments;
    size_t received_bytes;
    size_t frame_length;
    uint8_t fragment_seen[32];
    uint8_t frame[LINK_A_MAX_FRAME_BYTES];
    uint8_t covered[LINK_A_MAX_FRAME_BYTES];
} link_a_reassembler_t;

int link_a_encode_packet(
    uint8_t *output,
    size_t output_capacity,
    const link_a_packet_t *packet,
    size_t *output_length);

int link_a_decode_packet(
    const uint8_t *input,
    size_t input_length,
    link_a_packet_t *packet,
    size_t *consumed);

size_t link_a_fragment_count(
    size_t frame_bytes,
    size_t max_fragment_payload);

int link_a_encode_frame_fragment(
    uint8_t *output,
    size_t output_capacity,
    const uint8_t *frame,
    size_t frame_length,
    uint16_t session_id,
    uint32_t frame_sequence,
    uint8_t fragment_index,
    size_t max_fragment_payload,
    size_t *output_length);

void link_a_reassembler_init(link_a_reassembler_t *reassembler);

/*
 * Push one complete link-A packet. On success, completed_frame points into the
 * reassembler buffer only when LINK_A_STATUS_COMPLETE is returned.
 */
int link_a_reassembler_push(
    link_a_reassembler_t *reassembler,
    const uint8_t *packet_data,
    size_t packet_length,
    const uint8_t **completed_frame,
    size_t *completed_length);

#ifdef __cplusplus
}
#endif

#endif
