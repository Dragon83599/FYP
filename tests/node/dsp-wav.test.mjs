import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import {
  StreamingDecimator8kTo4k,
  decimate8kTo4k,
  generateSineWave,
  rms
} from '../../tools/audio-sim/lib/dsp.mjs';
import { readPcm16Wav, writePcm16Wav } from '../../tools/audio-sim/lib/wav.mjs';

test('PCM16 WAV round trip preserves mono samples', () => {
  const directory = mkdtempSync(join(tmpdir(), 'fyp-wav-'));
  try {
    const samples = generateSineWave({ frequencyHz: 440, durationSeconds: 0.02 });
    const filePath = join(directory, 'test.wav');
    writePcm16Wav(filePath, samples, 8000, 1);
    const decoded = readPcm16Wav(filePath);
    assert.equal(decoded.sampleRate, 8000);
    assert.equal(decoded.channels, 1);
    assert.deepEqual(decoded.samples, samples);
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});

test('8 kHz to 4 kHz decimation preserves 440 Hz and rejects 1900 Hz', () => {
  const low = generateSineWave({ frequencyHz: 440, durationSeconds: 0.5 });
  const high = generateSineWave({ frequencyHz: 1900, durationSeconds: 0.5 });
  const lowOutput = decimate8kTo4k(low);
  const highOutput = decimate8kTo4k(high);
  assert.equal(lowOutput.length, 2000);
  assert.ok(rms(lowOutput.subarray(100)) > 0.7 * rms(low.subarray(100)));
  assert.ok(rms(highOutput.subarray(100)) < 0.35 * rms(high.subarray(100)));
});

test('streaming decimator returns one output per two input samples', () => {
  const input = generateSineWave({ frequencyHz: 440, durationSeconds: 0.02 });
  const decimator = new StreamingDecimator8kTo4k();
  const first = decimator.process(input.subarray(0, 80));
  const second = decimator.process(input.subarray(80));
  assert.equal(first.length + second.length, 80);
});
