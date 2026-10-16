#include "link_a_protocol.h"

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

static void fill_frame(uint8_t *frame)
{
    size_t i;

    for (i = 0; i < AUDIO_WIRE_FRAME_BYTES; ++i) {
        frame[i] = (uint8_t)(i ^ 0x5Au);
    }
}

static void test_fragment_count(void)
{
    CHECK(link_a_fragment_count(AUDIO_WIRE_FRAME_BYTES, 352u) == 1u);
    CHECK(link_a_fragment_count(AUDIO_WIRE_FRAME_BYTES, 176u) == 2u);
    CHECK(link_a_fragment_count(AUDIO_WIRE_FRAME_BYTES, 128u) == 3u);
    CHECK(link_a_fragment_count(AUDIO_WIRE_FRAME_BYTES, 0u) == 0u);
}

static void test_reassembly_out_of_order(void)
{
    uint8_t frame[AUDIO_WIRE_FRAME_BYTES];
    uint8_t packet[LINK_A_DEFAULT_LINK_PAYLOAD_BYTES];
    link_a_reassembler_t reassembler;
    const uint8_t *completed = NULL;
    size_t completed_length = 0u;
    size_t packet_length;
    size_t count;
    size_t i;
    int status;

    fill_frame(frame);
    count = link_a_fragment_count(AUDIO_WIRE_FRAME_BYTES, 128u);
    CHECK(count == 3u);
    link_a_reassembler_init(&reassembler);

    for (i = count; i > 0u; --i) {
        status = link_a_encode_frame_fragment(
            packet,
            sizeof(packet),
            frame,
            sizeof(frame),
            0x1234u,
            0x01020304u,
            (uint8_t)(i - 1u),
            128u,
            &packet_length);
        CHECK(status == LINK_A_STATUS_OK);
        status = link_a_reassembler_push(
            &reassembler,
            packet,
            packet_length,
            &completed,
            &completed_length);
        if (i == 1u) {
            CHECK(status == LINK_A_STATUS_COMPLETE);
        } else {
            CHECK(status == LINK_A_STATUS_IN_PROGRESS);
        }
    }

    CHECK(completed != NULL);
    CHECK(completed_length == AUDIO_WIRE_FRAME_BYTES);
    CHECK(memcmp(completed, frame, sizeof(frame)) == 0);
}

static void test_duplicate_and_corruption(void)
{
    uint8_t frame[AUDIO_WIRE_FRAME_BYTES];
    uint8_t packet[LINK_A_DEFAULT_LINK_PAYLOAD_BYTES];
    link_a_packet_t packet_view;
    link_a_reassembler_t reassembler;
    size_t packet_length = 0u;

    fill_frame(frame);
    link_a_reassembler_init(&reassembler);

    CHECK(link_a_encode_frame_fragment(
              packet,
              sizeof(packet),
              frame,
              sizeof(frame),
              7u,
              9u,
              0u,
              128u,
              &packet_length) == LINK_A_STATUS_OK);
    CHECK(link_a_reassembler_push(
              &reassembler,
              packet,
              packet_length,
              NULL,
              NULL) == LINK_A_STATUS_IN_PROGRESS);
    CHECK(link_a_reassembler_push(
              &reassembler,
              packet,
              packet_length,
              NULL,
              NULL) == LINK_A_STATUS_DUPLICATE);

    packet[LINK_A_OFFSET_PAYLOAD] ^= 0x01u;
    CHECK(link_a_decode_packet(
              packet,
              packet_length,
              &packet_view,
              NULL) == LINK_A_STATUS_BAD_CRC);
}

static void test_ack(void)
{
    link_a_packet_t input;
    link_a_packet_t output;
    uint8_t encoded[LINK_A_WIRE_HEADER_BYTES + LINK_A_WIRE_CRC_BYTES];
    size_t encoded_length = 0u;

    memset(&input, 0, sizeof(input));
    input.type = LINK_A_PACKET_ACK;
    input.session_id = 0xCAFEu;
    input.frame_sequence = 42u;

    CHECK(link_a_encode_packet(
              encoded,
              sizeof(encoded),
              &input,
              &encoded_length) == LINK_A_STATUS_OK);
    CHECK(encoded_length == sizeof(encoded));
    CHECK(link_a_decode_packet(
              encoded,
              encoded_length,
              &output,
              NULL) == LINK_A_STATUS_OK);
    CHECK(output.type == LINK_A_PACKET_ACK);
    CHECK(output.session_id == input.session_id);
    CHECK(output.frame_sequence == input.frame_sequence);
}

static void test_golden_packet(void)
{
    static const uint8_t payload[] = {
        0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09
    };
    static const uint8_t expected[] = {
        0x4C, 0x4B, 0x41, 0x31, 0x01, 0x01, 0x01, 0x00,
        0x34, 0x12, 0x07, 0x00, 0x00, 0x00, 0x00, 0x02,
        0x00, 0x00, 0x0A, 0x00, 0x00, 0x01, 0x02, 0x03,
        0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x5E, 0x36
    };
    link_a_packet_t packet;
    uint8_t encoded[sizeof(expected)];
    size_t encoded_length = 0u;

    memset(&packet, 0, sizeof(packet));
    packet.type = LINK_A_PACKET_DATA;
    packet.flags = LINK_A_FLAG_START;
    packet.session_id = 0x1234u;
    packet.frame_sequence = 7u;
    packet.fragment_index = 0u;
    packet.fragment_count = 2u;
    packet.fragment_offset = 0u;
    packet.fragment_bytes = (uint16_t)sizeof(payload);
    packet.payload = payload;

    CHECK(link_a_encode_packet(
              encoded,
              sizeof(encoded),
              &packet,
              &encoded_length) == LINK_A_STATUS_OK);
    CHECK(encoded_length == sizeof(expected));
    CHECK(memcmp(encoded, expected, sizeof(expected)) == 0);
}

int main(void)
{
    test_fragment_count();
    test_reassembly_out_of_order();
    test_duplicate_and_corruption();
    test_ack();
    test_golden_packet();

    if (failures != 0) {
        fprintf(stderr, "%d test(s) failed\n", failures);
        return 1;
    }

    printf("link_a_protocol tests passed\n");
    return 0;
}
