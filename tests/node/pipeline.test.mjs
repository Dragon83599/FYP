import test from 'node:test';
import assert from 'node:assert/strict';
import { generateSineWave } from '../../tools/audio-sim/lib/dsp.mjs';
import {
  encodeAudioToLinkPackets,
  receiveAudioPackets,
  runSoftwareLoopback
} from '../../tools/audio-sim/lib/pipeline.mjs';

test('complete software loopback preserves all frames', () => {
  const samples = generateSineWave({ frequencyHz: 440, durationSeconds: 0.2 });
  const result = runSoftwareLoopback(samples, {
    sessionId: 1,
    maxFragmentPayload: 222
  });
  assert.equal(result.frames.length, 10);
  assert.equal(result.received.stats.decodedFrames, 10);
  assert.deepEqual(result.received.stats.missingSequences, []);
  assert.equal(result.received.aiInput4k.length, 800);
});

test('packet loss drops only the affected logical frame', () => {
  const samples = generateSineWave({ frequencyHz: 440, durationSeconds: 0.2 });
  const encoded = encodeAudioToLinkPackets(samples, { sessionId: 1 });
  const dropped = encoded.packets.filter((_, index) => index !== 2);
  const received = receiveAudioPackets(dropped);
  assert.equal(received.stats.decodedFrames, 9);
  assert.equal(received.stats.incompleteFrames, 1);
  assert.deepEqual(received.stats.missingSequences, [1]);
});

test('timestamp advances by exactly one frame duration', () => {
  const samples = generateSineWave({ frequencyHz: 440, durationSeconds: 0.1 });
  const received = receiveAudioPackets(
    encodeAudioToLinkPackets(samples, { sessionId: 1 }).packets
  );
  assert.equal(received.frames[1].timestampMs - received.frames[0].timestampMs, 20);
});
