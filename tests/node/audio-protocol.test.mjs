import test from 'node:test';
import assert from 'node:assert/strict';
import {
  AUDIO_WIRE_HEADER_BYTES,
  AUDIO_WIRE_FRAME_BYTES,
  AUDIO_FLAG_TIMESTAMP_VALID,
  AudioProtocolError,
  crc16CcittFalse,
  decodeFrame,
  encodeFrame,
  findAudioMagic
} from '../../tools/audio-sim/lib/audio-protocol.mjs';

function zeroFrame() {
  return {
    samples: new Int16Array(160),
    sampleRateHz: 8000,
    timestampMs: 1000,
    sequence: 1,
    channelCount: 1
  };
}

test('golden AUD1 frame matches the frozen test vector', () => {
  const encoded = encodeFrame(zeroFrame(), AUDIO_FLAG_TIMESTAMP_VALID);
  const expectedHeader = Buffer.from(
    '4155443101011e0001011000401f0000a000000001000000e80300004001',
    'hex'
  );
  assert.equal(encoded.length, AUDIO_WIRE_FRAME_BYTES);
  assert.deepEqual(encoded.subarray(0, AUDIO_WIRE_HEADER_BYTES), expectedHeader);
  assert.equal(encoded.readUInt16LE(350), 0x7b5e);
});

test('AUD1 round trip preserves samples and metadata', () => {
  const frame = zeroFrame();
  frame.sequence = 0x12345678;
  frame.timestampMs = 0x01020304;
  for (let i = 0; i < frame.samples.length; i += 1) frame.samples[i] = i * 257 - 16000;

  const decoded = decodeFrame(encodeFrame(frame));
  assert.equal(decoded.frame.sequence, frame.sequence);
  assert.equal(decoded.frame.timestampMs, frame.timestampMs);
  assert.deepEqual(decoded.frame.samples, frame.samples);
});

test('AUD1 rejects corrupted CRC and finds the next magic', () => {
  const encoded = encodeFrame(zeroFrame());
  const corrupted = Buffer.from(encoded);
  corrupted[100] ^= 1;
  assert.throws(() => decodeFrame(corrupted), (error) => {
    assert.ok(error instanceof AudioProtocolError);
    assert.equal(error.code, 'bad_crc');
    return true;
  });

  const stream = Buffer.concat([Buffer.from([0, 1, 2]), encoded, Buffer.from([9])]);
  assert.equal(findAudioMagic(stream), 3);
});

test('CRC-16/CCITT-FALSE reference vector', () => {
  assert.equal(crc16CcittFalse(Buffer.from('123456789', 'ascii')), 0x29b1);
});
