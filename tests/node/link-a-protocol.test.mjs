import test from 'node:test';
import assert from 'node:assert/strict';
import {
  FrameReassembler,
  LINK_A_FLAG_START,
  LINK_A_WIRE_HEADER_BYTES,
  LinkAProtocolError,
  decodePacket,
  encodePacket,
  fragmentFrame,
  splitPacketStream
} from '../../tools/audio-sim/lib/link-a-protocol.mjs';
import { AUDIO_WIRE_FRAME_BYTES } from '../../tools/audio-sim/lib/audio-protocol.mjs';

function testFrame() {
  const frame = Buffer.alloc(AUDIO_WIRE_FRAME_BYTES);
  for (let i = 0; i < frame.length; i += 1) frame[i] = i ^ 0x5a;
  return frame;
}

test('link A fragments and reassembles out of order', () => {
  const frame = testFrame();
  const packets = fragmentFrame(frame, {
    sessionId: 0x1234,
    frameSequence: 7,
    maxFragmentPayload: 128
  });
  assert.equal(packets.length, 3);

  const reassembler = new FrameReassembler();
  let completed = null;
  for (const packet of [...packets].reverse()) {
    const result = reassembler.push(packet);
    if (result.status === 'complete') completed = result;
  }
  assert.ok(completed);
  assert.deepEqual(completed.frame, frame);
});

test('link A detects duplicates, invalid CRC, and missing fragments', () => {
  const frame = testFrame();
  const packets = fragmentFrame(frame, {
    sessionId: 7,
    frameSequence: 9,
    maxFragmentPayload: 128
  });
  const reassembler = new FrameReassembler();
  assert.equal(reassembler.push(packets[0]).status, 'in_progress');
  assert.equal(reassembler.push(packets[0]).status, 'duplicate');
  assert.equal(reassembler.push(packets[1]).status, 'in_progress');
  assert.equal(reassembler.pending.size, 1);

  const corrupted = Buffer.from(packets[0]);
  corrupted[corrupted.length - 3] ^= 1;
  assert.throws(() => decodePacket(corrupted), (error) => {
    assert.ok(error instanceof LinkAProtocolError);
    assert.equal(error.code, 'bad_crc');
    return true;
  });
});

test('link A ACK packet uses the same envelope', () => {
  const encoded = encodePacket({
    type: 2,
    flags: 0,
    sessionId: 0xcafe,
    frameSequence: 42,
    fragmentIndex: 0,
    fragmentCount: 0,
    fragmentOffset: 0,
    payload: Buffer.alloc(0)
  });
  assert.equal(encoded.length, LINK_A_WIRE_HEADER_BYTES + 2);
  const decoded = decodePacket(encoded);
  assert.equal(decoded.type, 2);
  assert.equal(decoded.sessionId, 0xcafe);
  assert.equal(decoded.frameSequence, 42);
});

test('link A start flag encodes the first fragment', () => {
  const packets = fragmentFrame(testFrame(), {
    sessionId: 1,
    frameSequence: 1,
    maxFragmentPayload: 222
  });
  assert.equal(decodePacket(packets[0]).flags & LINK_A_FLAG_START, LINK_A_FLAG_START);
});

test('link A matches the frozen golden packet', () => {
  const packet = encodePacket({
    type: 1,
    flags: LINK_A_FLAG_START,
    sessionId: 0x1234,
    frameSequence: 7,
    fragmentIndex: 0,
    fragmentCount: 2,
    fragmentOffset: 0,
    payload: Buffer.from([0, 1, 2, 3, 4, 5, 6, 7, 8, 9])
  });
  assert.equal(
    packet.toString('hex'),
    '4c4b413101010100341207000000000200000a00000102030405060708095e36'
  );
});

test('link A stream parser preserves packet boundaries', () => {
  const packets = fragmentFrame(testFrame(), {
    sessionId: 1,
    frameSequence: 2,
    maxFragmentPayload: 222
  });
  const parsed = splitPacketStream(Buffer.concat(packets));
  assert.equal(parsed.length, packets.length);
  parsed.forEach((packet, index) => assert.deepEqual(packet, packets[index]));
});
