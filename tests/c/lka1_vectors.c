/*
 * Golden-vector tests for the probe's encoder, built with the host compiler.
 *
 * The two vectors are not invented here: one is frozen in
 * docs/link-a-interface-freeze.md (DATA, session 0x1234, sequence 7, CRC 0x365E)
 * and one is proposed in docs/link-a-gatt-profile.md (type=4 COMMAND, CRC 0x1132).
 * If this file passes, the firmware encoder produces the same bytes the phone's
 * decoder was written against -- before the board is even plugged in.
 *
 *   gcc -std=c11 -Wall -Wextra -O2 -o /tmp/lka1_vectors \
 *       firmware/probe-sim/main/lka1_encode.c tests/c/lka1_vectors.c
 *   /tmp/lka1_vectors
 */

#include <stdio.h>
#include <string.h>

#include "../../firmware/probe-sim/main/lka1_encode.h"

static int failures = 0;

static void expect_bytes(const char *name, const uint8_t *got, const uint8_t *want, size_t length)
{
    if (memcmp(got, want, length) == 0) {
        printf("ok    %s (%zu bytes)\n", name, length);
        return;
    }
    printf("FAIL  %s\n", name);
    for (size_t i = 0; i < length; i++) {
        if (got[i] != want[i]) {
            printf("        byte %zu: got %02X, want %02X\n", i, got[i], want[i]);
            break;
        }
    }
    failures++;
}

static void expect_u16(const char *name, uint16_t got, uint16_t want)
{
    if (got == want) {
        printf("ok    %s (0x%04X)\n", name, got);
        return;
    }
    printf("FAIL  %s: got 0x%04X, want 0x%04X\n", name, got, want);
    failures++;
}

int main(void)
{
    /* --- frozen DATA packet ------------------------------------------------ */
    const uint8_t data_want[] = {
        0x4C, 0x4B, 0x41, 0x31, 0x01, 0x01, 0x01, 0x00,
        0x34, 0x12, 0x07, 0x00, 0x00, 0x00, 0x00, 0x02,
        0x00, 0x00, 0x0A, 0x00, 0x00, 0x01, 0x02, 0x03,
        0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x5E, 0x36,
    };
    const uint8_t payload[] = {0, 1, 2, 3, 4, 5, 6, 7, 8, 9};
    uint8_t packet[LKA1_MAX_PACKET_BYTES] = {0};
    size_t length = lka1_encode_packet(packet, sizeof(packet), LKA1_TYPE_DATA,
                                       LKA1_FLAG_START, 0x1234, 7, 0, 2, 0,
                                       payload, sizeof(payload));
    expect_u16("DATA packet length", (uint16_t)length, (uint16_t)sizeof(data_want));
    expect_bytes("frozen DATA packet", packet, data_want, sizeof(data_want));
    expect_u16("frozen DATA CRC", lka1_crc16(data_want, sizeof(data_want) - 2), 0x365E);

    /* --- proposed COMMAND packet ------------------------------------------ */
    const uint8_t command_want[] = {
        0x4C, 0x4B, 0x41, 0x31, 0x01, 0x04, 0x00, 0x00,
        0x34, 0x12, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01,
        0x00, 0x00, 0x08, 0x00, 0x01, 0x00, 0x00, 0x00,
        0x01, 0x00, 0x00, 0x00, 0x32, 0x11,
    };
    memset(packet, 0, sizeof(packet));
    length = lka1_encode_command(packet, sizeof(packet), 0x1234, LKA1_CMD_START, 0, 1);
    expect_u16("COMMAND packet length", (uint16_t)length, (uint16_t)sizeof(command_want));
    expect_bytes("proposed COMMAND packet", packet, command_want, sizeof(command_want));

    /* --- AUD1 frame shape -------------------------------------------------- */
    int16_t samples[AUD1_FRAME_SAMPLES];
    for (int i = 0; i < AUD1_FRAME_SAMPLES; i++) {
        samples[i] = (int16_t)(i * 100 - 8000);
    }
    uint8_t frame[AUD1_FRAME_BYTES];
    length = aud1_encode(frame, sizeof(frame), samples, 3, 40);
    expect_u16("AUD1 frame length", (uint16_t)length, AUD1_FRAME_BYTES);
    expect_u16("AUD1 magic", (uint16_t)(frame[0] | (frame[1] << 8)), 0x5541);
    expect_u16("AUD1 payload_bytes field", (uint16_t)(frame[28] | (frame[29] << 8)), 320);
    expect_u16("AUD1 CRC covers header+payload",
               lka1_crc16(frame, AUD1_FRAME_BYTES - 2),
               (uint16_t)(frame[350] | (frame[351] << 8)));
    /* first sample survives the round trip */
    expect_u16("AUD1 first sample", (uint16_t)(frame[30] | (frame[31] << 8)), (uint16_t)samples[0]);

    /* --- input rejection --------------------------------------------------- */
    if (lka1_encode_packet(packet, sizeof(packet), LKA1_TYPE_DATA, 0, 1, 0, 0, 2, 0,
                           payload, 0) == 0) {
        printf("ok    rejects empty payload\n");
    } else {
        printf("FAIL  accepted an empty payload\n");
        failures++;
    }

    printf("\n%s (%d failure%s)\n", failures ? "FAILED" : "all vectors passed",
           failures, failures == 1 ? "" : "s");
    return failures ? 1 : 0;
}
