/**
 * The GATT contract is written twice — once as prose in
 * docs/link-a-gatt-profile.md, once as constants in pwa/gatt.js. These tests
 * read the document and compare, so neither can drift without a failure.
 *
 * It also pins the two golden vectors that already exist in the repository:
 * the frozen DATA packet from link-a-interface-freeze.md and the proposed
 * type=4 COMMAND packet from the GATT profile.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

import {
  LKA1_SERVICE_UUID,
  LKA1_AUDIO_DATA_UUID,
  LKA1_CONTROL_UUID,
  LKA1_STATUS_UUID,
  LKA1_CCCD_UUID,
  LKA1_PREFERRED_MTU,
  LKA1_CONN_INTERVAL_MS,
  CONTROL_CMD,
  STATUS_STATE,
  STATUS_ERROR,
  encodeControl,
  decodeControl,
  decodeStatus,
} from "../../pwa/gatt.js";
import { decodePacket, crc16CcittFalse } from "../../pwa/lka1.js";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const PROFILE = readFileSync(path.join(ROOT, "docs/link-a-gatt-profile.md"), "utf8");
const FREEZE = readFileSync(path.join(ROOT, "docs/link-a-interface-freeze.md"), "utf8");

function hex(text) {
  return new Uint8Array(text.trim().split(/\s+/).map((byte) => Number.parseInt(byte, 16)));
}

test("the UUID table in the profile matches the constants", () => {
  const table = /```text\n基址：([0-9a-f-]+)NN/.exec(PROFILE);
  assert.ok(table, "the profile must still document the UUID base");
  const base = table[1];
  assert.equal(LKA1_SERVICE_UUID, `${base}01`);
  assert.equal(LKA1_AUDIO_DATA_UUID, `${base}02`);
  assert.equal(LKA1_CONTROL_UUID, `${base}03`);
  assert.equal(LKA1_STATUS_UUID, `${base}04`);
  for (const uuid of [LKA1_SERVICE_UUID, LKA1_AUDIO_DATA_UUID, LKA1_CONTROL_UUID, LKA1_STATUS_UUID]) {
    assert.ok(PROFILE.includes(uuid), `profile does not mention ${uuid}`);
  }
  assert.ok(PROFILE.includes("0x2902"), "CCCD must be documented as the standard 0x2902");
  assert.equal(LKA1_CCCD_UUID, "00002902-0000-1000-8000-00805f9b34fb");
});

test("the connection parameters in the profile match the constants", () => {
  assert.match(PROFILE, new RegExp(`ATT MTU \\| ${LKA1_PREFERRED_MTU}`));
  assert.match(PROFILE, new RegExp(`连接间隔 \\| ${LKA1_CONN_INTERVAL_MS} ms`));
  assert.match(PROFILE, /2M，回退 1M/);
});

test("the frozen DATA vector still decodes to the documented fields", () => {
  const vector = hex(
    `4C 4B 41 31 01 01 01 00 34 12 07 00 00 00 00 02
     00 00 0A 00 00 01 02 03 04 05 06 07 08 09 5E 36`,
  );
  const packet = decodePacket(vector);
  assert.equal(packet.type, 1);            // DATA
  assert.equal(packet.flags, 1);           // START
  assert.equal(packet.sessionId, 0x1234);
  assert.equal(packet.frameSequence, 7);
  assert.equal(packet.fragmentIndex, 0);
  assert.equal(packet.fragmentCount, 2);
  assert.equal(packet.fragmentOffset, 0);
  assert.deepEqual(Array.from(packet.payload), [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]);
  assert.equal(crc16CcittFalse(vector, 0, vector.length - 2), 0x365e);
  assert.ok(FREEZE.includes("5E 36"), "the frozen document must still carry this CRC");
});

test("the proposed COMMAND vector from the profile encodes byte for byte", () => {
  // docs/link-a-gatt-profile.md §5.1: START, session_id 0x1234, token 1.
  const documented = hex(
    `4C 4B 41 31 01 04 00 00 34 12 00 00 00 00 00 01
     00 00 08 00 01 00 00 00 01 00 00 00 32 11`,
  );
  const payload = encodeControl(CONTROL_CMD.START, { token: 1 });
  assert.deepEqual(Array.from(payload), [1, 0, 0, 0, 1, 0, 0, 0]);

  const packet = new Uint8Array(30);
  const view = new DataView(packet.buffer);
  packet.set([0x4c, 0x4b, 0x41, 0x31], 0);   // magic "LKA1"
  view.setUint8(4, 1);                        // version
  view.setUint8(5, 4);                        // type = COMMAND
  view.setUint8(6, 0);                        // flags
  view.setUint8(7, 0);                        // reserved
  view.setUint16(8, 0x1234, true);            // session_id
  view.setUint32(10, 0, true);                // frame_sequence
  view.setUint8(14, 0);                       // fragment_index
  view.setUint8(15, 1);                       // fragment_count
  view.setUint16(16, 0, true);                // fragment_offset
  view.setUint16(18, 8, true);                // fragment_bytes
  packet.set(payload, 20);
  view.setUint16(28, crc16CcittFalse(packet, 0, 28), true);

  assert.deepEqual(Array.from(packet), Array.from(documented));
  assert.equal(view.getUint16(28, true), 0x1132);
});

test("control frames round-trip and reject out-of-range values", () => {
  const encoded = encodeControl(CONTROL_CMD.START, { arg: 0xbeef, token: 0xdeadbeef });
  const decoded = decodeControl(encoded);
  assert.equal(decoded.command, CONTROL_CMD.START);
  assert.equal(decoded.arg, 0xbeef);
  assert.equal(decoded.token, 0xdeadbeef);

  assert.throws(() => encodeControl(99), RangeError);
  assert.throws(() => encodeControl(CONTROL_CMD.START, { arg: 0x10000 }), RangeError);
  assert.throws(() => encodeControl(CONTROL_CMD.START, { token: -1 }), RangeError);
});

test("status decodes to named fields and refuses short values", () => {
  const status = decodeStatus(new Uint8Array([STATUS_STATE.STREAMING, STATUS_ERROR.OK, 3, 0]));
  assert.equal(status.stateName, "STREAMING");
  assert.equal(status.errorName, "OK");
  assert.equal(status.dropFramesLow8, 3);
  assert.equal(status.reserved, 0);

  const congested = decodeStatus(new Uint8Array([STATUS_STATE.ERROR, STATUS_ERROR.BLE_CONGEST, 12, 0]));
  assert.equal(congested.errorName, "BLE_CONGEST");

  assert.throws(() => decodeStatus(new Uint8Array([1])), TypeError);
});
