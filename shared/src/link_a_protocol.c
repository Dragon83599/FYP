#include "link_a_protocol.h"

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

static int validate_packet(const link_a_packet_t *packet)
{
    uint8_t expected_flags = 0u;

    if (packet == NULL) {
        return LINK_A_STATUS_INVALID_ARGUMENT;
    }
    if (packet->type != LINK_A_PACKET_DATA &&
        packet->type != LINK_A_PACKET_ACK &&
        packet->type != LINK_A_PACKET_NACK) {
        return LINK_A_STATUS_BAD_TYPE;
    }
    if ((packet->flags &
         (uint8_t)~(LINK_A_FLAG_START | LINK_A_FLAG_END |
                    LINK_A_FLAG_RETRANSMISSION)) != 0u) {
        return LINK_A_STATUS_BAD_HEADER;
    }

    if (packet->type != LINK_A_PACKET_DATA) {
        if (packet->fragment_index != 0u ||
            packet->fragment_count != 0u ||
            packet->fragment_offset != 0u ||
            packet->fragment_bytes != 0u ||
            packet->flags != 0u) {
            return LINK_A_STATUS_BAD_HEADER;
        }
        return LINK_A_STATUS_OK;
    }

    if (packet->fragment_count == 0u ||
        packet->fragment_index >= packet->fragment_count ||
        packet->fragment_bytes == 0u) {
        return LINK_A_STATUS_BAD_FRAGMENT;
    }
    if ((packet->payload == NULL && packet->fragment_bytes != 0u) ||
        ((size_t)packet->fragment_offset + packet->fragment_bytes >
         LINK_A_MAX_FRAME_BYTES)) {
        return LINK_A_STATUS_BAD_FRAGMENT;
    }

    if (packet->fragment_index == 0u) {
        expected_flags |= LINK_A_FLAG_START;
    }
    if ((uint8_t)(packet->fragment_index + 1u) == packet->fragment_count) {
        expected_flags |= LINK_A_FLAG_END;
    }
    if ((packet->flags &
         (LINK_A_FLAG_START | LINK_A_FLAG_END)) != expected_flags) {
        return LINK_A_STATUS_BAD_FRAGMENT;
    }

    return LINK_A_STATUS_OK;
}

int link_a_encode_packet(
    uint8_t *output,
    size_t output_capacity,
    const link_a_packet_t *packet,
    size_t *output_length)
{
    size_t packet_length;
    uint16_t crc;
    int status;

    if (output == NULL || output_length == NULL) {
        return LINK_A_STATUS_INVALID_ARGUMENT;
    }

    status = validate_packet(packet);
    if (status != LINK_A_STATUS_OK) {
        return status;
    }

    packet_length = LINK_A_WIRE_HEADER_BYTES +
                    (size_t)packet->fragment_bytes +
                    LINK_A_WIRE_CRC_BYTES;
    if (output_capacity < packet_length) {
        return LINK_A_STATUS_BUFFER_TOO_SMALL;
    }

    memset(output, 0, LINK_A_WIRE_HEADER_BYTES);
    write_le32(&output[LINK_A_OFFSET_MAGIC], LINK_A_PROTOCOL_MAGIC);
    output[LINK_A_OFFSET_VERSION] = (uint8_t)LINK_A_PROTOCOL_VERSION;
    output[LINK_A_OFFSET_TYPE] = packet->type;
    output[LINK_A_OFFSET_FLAGS] = packet->flags;
    output[LINK_A_OFFSET_RESERVED] = 0u;
    write_le16(&output[LINK_A_OFFSET_SESSION_ID], packet->session_id);
    write_le32(&output[LINK_A_OFFSET_FRAME_SEQUENCE], packet->frame_sequence);
    output[LINK_A_OFFSET_FRAGMENT_INDEX] = packet->fragment_index;
    output[LINK_A_OFFSET_FRAGMENT_COUNT] = packet->fragment_count;
    write_le16(&output[LINK_A_OFFSET_FRAGMENT_OFFSET], packet->fragment_offset);
    write_le16(&output[LINK_A_OFFSET_FRAGMENT_BYTES], packet->fragment_bytes);

    if (packet->fragment_bytes != 0u) {
        memcpy(
            &output[LINK_A_OFFSET_PAYLOAD],
            packet->payload,
            packet->fragment_bytes);
    }

    crc = audio_protocol_crc16(
        output,
        LINK_A_WIRE_HEADER_BYTES + packet->fragment_bytes);
    write_le16(
        &output[LINK_A_WIRE_HEADER_BYTES + packet->fragment_bytes],
        crc);
    *output_length = packet_length;

    return LINK_A_STATUS_OK;
}

int link_a_decode_packet(
    const uint8_t *input,
    size_t input_length,
    link_a_packet_t *packet,
    size_t *consumed)
{
    size_t expected_length;
    uint16_t expected_crc;
    uint16_t actual_crc;
    link_a_packet_t decoded;
    int status;

    if (input == NULL || packet == NULL) {
        return LINK_A_STATUS_INVALID_ARGUMENT;
    }
    if (input_length < LINK_A_WIRE_HEADER_BYTES + LINK_A_WIRE_CRC_BYTES) {
        return LINK_A_STATUS_TRUNCATED;
    }
    if (read_le32(&input[LINK_A_OFFSET_MAGIC]) != LINK_A_PROTOCOL_MAGIC) {
        return LINK_A_STATUS_BAD_MAGIC;
    }
    if (input[LINK_A_OFFSET_VERSION] != (uint8_t)LINK_A_PROTOCOL_VERSION) {
        return LINK_A_STATUS_BAD_VERSION;
    }
    if (input[LINK_A_OFFSET_RESERVED] != 0u) {
        return LINK_A_STATUS_BAD_HEADER;
    }

    memset(&decoded, 0, sizeof(decoded));
    decoded.type = input[LINK_A_OFFSET_TYPE];
    decoded.flags = input[LINK_A_OFFSET_FLAGS];
    decoded.session_id = read_le16(&input[LINK_A_OFFSET_SESSION_ID]);
    decoded.frame_sequence = read_le32(&input[LINK_A_OFFSET_FRAME_SEQUENCE]);
    decoded.fragment_index = input[LINK_A_OFFSET_FRAGMENT_INDEX];
    decoded.fragment_count = input[LINK_A_OFFSET_FRAGMENT_COUNT];
    decoded.fragment_offset = read_le16(&input[LINK_A_OFFSET_FRAGMENT_OFFSET]);
    decoded.fragment_bytes = read_le16(&input[LINK_A_OFFSET_FRAGMENT_BYTES]);

    expected_length = LINK_A_WIRE_HEADER_BYTES +
                      (size_t)decoded.fragment_bytes +
                      LINK_A_WIRE_CRC_BYTES;
    if (input_length < expected_length) {
        return LINK_A_STATUS_TRUNCATED;
    }

    expected_crc = read_le16(
        &input[LINK_A_WIRE_HEADER_BYTES + decoded.fragment_bytes]);
    actual_crc = audio_protocol_crc16(
        input,
        LINK_A_WIRE_HEADER_BYTES + decoded.fragment_bytes);
    if (expected_crc != actual_crc) {
        return LINK_A_STATUS_BAD_CRC;
    }

    if (decoded.fragment_bytes != 0u) {
        decoded.payload = &input[LINK_A_OFFSET_PAYLOAD];
    }
    status = validate_packet(&decoded);
    if (status != LINK_A_STATUS_OK) {
        return status;
    }

    *packet = decoded;
    if (consumed != NULL) {
        *consumed = expected_length;
    }

    return LINK_A_STATUS_OK;
}

size_t link_a_fragment_count(
    size_t frame_bytes,
    size_t max_fragment_payload)
{
    size_t count;

    if (frame_bytes == 0u || max_fragment_payload == 0u) {
        return 0u;
    }
    count = (frame_bytes + max_fragment_payload - 1u) / max_fragment_payload;
    if (count > LINK_A_MAX_FRAGMENTS) {
        return 0u;
    }
    return count;
}

int link_a_encode_frame_fragment(
    uint8_t *output,
    size_t output_capacity,
    const uint8_t *frame,
    size_t frame_length,
    uint16_t session_id,
    uint32_t frame_sequence,
    uint8_t fragment_index,
    size_t max_fragment_payload,
    size_t *output_length)
{
    link_a_packet_t packet;
    size_t fragment_count;
    size_t fragment_offset;
    size_t fragment_bytes;

    if (output == NULL || frame == NULL || output_length == NULL) {
        return LINK_A_STATUS_INVALID_ARGUMENT;
    }
    if (frame_length == 0u || frame_length > LINK_A_MAX_FRAME_BYTES ||
        max_fragment_payload == 0u || max_fragment_payload > UINT16_MAX) {
        return LINK_A_STATUS_INVALID_ARGUMENT;
    }

    fragment_count = link_a_fragment_count(frame_length, max_fragment_payload);
    if (fragment_count == 0u || fragment_index >= fragment_count) {
        return LINK_A_STATUS_BAD_FRAGMENT;
    }

    fragment_offset = (size_t)fragment_index * max_fragment_payload;
    fragment_bytes = frame_length - fragment_offset;
    if (fragment_bytes > max_fragment_payload) {
        fragment_bytes = max_fragment_payload;
    }

    memset(&packet, 0, sizeof(packet));
    packet.type = LINK_A_PACKET_DATA;
    packet.session_id = session_id;
    packet.frame_sequence = frame_sequence;
    packet.fragment_index = fragment_index;
    packet.fragment_count = (uint8_t)fragment_count;
    packet.fragment_offset = (uint16_t)fragment_offset;
    packet.fragment_bytes = (uint16_t)fragment_bytes;
    packet.payload = &frame[fragment_offset];
    if (fragment_index == 0u) {
        packet.flags |= LINK_A_FLAG_START;
    }
    if ((uint8_t)(fragment_index + 1u) == fragment_count) {
        packet.flags |= LINK_A_FLAG_END;
    }

    return link_a_encode_packet(
        output,
        output_capacity,
        &packet,
        output_length);
}

void link_a_reassembler_init(link_a_reassembler_t *reassembler)
{
    if (reassembler != NULL) {
        memset(reassembler, 0, sizeof(*reassembler));
    }
}

int link_a_reassembler_push(
    link_a_reassembler_t *reassembler,
    const uint8_t *packet_data,
    size_t packet_length,
    const uint8_t **completed_frame,
    size_t *completed_length)
{
    link_a_packet_t packet;
    size_t i;
    size_t new_bytes = 0u;
    size_t end_offset;
    int status;

    if (completed_frame != NULL) {
        *completed_frame = NULL;
    }
    if (completed_length != NULL) {
        *completed_length = 0u;
    }
    if (reassembler == NULL) {
        return LINK_A_STATUS_INVALID_ARGUMENT;
    }

    status = link_a_decode_packet(packet_data, packet_length, &packet, NULL);
    if (status != LINK_A_STATUS_OK) {
        return status;
    }
    if (packet.type != LINK_A_PACKET_DATA) {
        return LINK_A_STATUS_OK;
    }

    if (reassembler->active == 0u ||
        reassembler->session_id != packet.session_id ||
        reassembler->frame_sequence != packet.frame_sequence) {
        memset(reassembler, 0, sizeof(*reassembler));
        reassembler->active = 1u;
        reassembler->session_id = packet.session_id;
        reassembler->frame_sequence = packet.frame_sequence;
        reassembler->fragment_count = packet.fragment_count;
    }

    if (reassembler->fragment_count != packet.fragment_count ||
        packet.fragment_index >= packet.fragment_count) {
        return LINK_A_STATUS_BAD_FRAGMENT;
    }

    if ((reassembler->fragment_seen[packet.fragment_index / 8u] &
         (uint8_t)(1u << (packet.fragment_index % 8u))) != 0u) {
        for (i = 0; i < packet.fragment_bytes; ++i) {
            size_t index = (size_t)packet.fragment_offset + i;
            if (reassembler->frame[index] != packet.payload[i]) {
                return LINK_A_STATUS_CONFLICT;
            }
        }
        return LINK_A_STATUS_DUPLICATE;
    }

    end_offset = (size_t)packet.fragment_offset + packet.fragment_bytes;
    if (end_offset > LINK_A_MAX_FRAME_BYTES) {
        return LINK_A_STATUS_BAD_FRAGMENT;
    }
    if ((packet.flags & LINK_A_FLAG_END) != 0u) {
        if (end_offset != AUDIO_WIRE_FRAME_BYTES) {
            return LINK_A_STATUS_BAD_FRAGMENT;
        }
        if (reassembler->frame_length != 0u &&
            reassembler->frame_length != end_offset) {
            return LINK_A_STATUS_BAD_FRAGMENT;
        }
        reassembler->frame_length = end_offset;
    } else if (reassembler->frame_length != 0u &&
               end_offset > reassembler->frame_length) {
        return LINK_A_STATUS_BAD_FRAGMENT;
    }

    for (i = 0; i < packet.fragment_bytes; ++i) {
        size_t index = (size_t)packet.fragment_offset + i;
        if (reassembler->covered[index] != 0u) {
            if (reassembler->frame[index] != packet.payload[i]) {
                return LINK_A_STATUS_CONFLICT;
            }
            continue;
        }
        reassembler->frame[index] = packet.payload[i];
        reassembler->covered[index] = 1u;
        ++new_bytes;
    }

    reassembler->fragment_seen[packet.fragment_index / 8u] |=
        (uint8_t)(1u << (packet.fragment_index % 8u));
    ++reassembler->received_fragments;
    reassembler->received_bytes += new_bytes;

    if (reassembler->frame_length != 0u &&
        reassembler->received_fragments == reassembler->fragment_count &&
        reassembler->received_bytes == reassembler->frame_length) {
        if (completed_frame != NULL) {
            *completed_frame = reassembler->frame;
        }
        if (completed_length != NULL) {
            *completed_length = reassembler->frame_length;
        }
        return LINK_A_STATUS_COMPLETE;
    }

    return LINK_A_STATUS_IN_PROGRESS;
}
