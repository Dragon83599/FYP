import { mkdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { generateSineWave } from './lib/dsp.mjs';
import { writePcm16Wav } from './lib/wav.mjs';
import { runSoftwareLoopback } from './lib/pipeline.mjs';

const outputDirectory = resolve('build/audio-sim-demo');
mkdirSync(outputDirectory, { recursive: true });

const durationSeconds = 2;
const samples = generateSineWave({
  frequencyHz: 440,
  sampleRate: 8000,
  durationSeconds,
  amplitude: 0.4
});
const result = runSoftwareLoopback(samples, {
  sessionId: 0x1234,
  maxFragmentPayload: 222
});

const inputPath = resolve(outputDirectory, 'input-8k.wav');
const receivedPath = resolve(outputDirectory, 'received-8k.wav');
const aiPath = resolve(outputDirectory, 'ai-input-4k.wav');
writePcm16Wav(inputPath, samples, 8000, 1);
writePcm16Wav(receivedPath, result.received.samples, 8000, 1);
writePcm16Wav(aiPath, result.received.aiInput4k, 4000, 1);

console.log(JSON.stringify({
  outputDirectory,
  packets: result.packets.length,
  decodedFrames: result.received.stats.decodedFrames,
  aiSamples: result.received.aiInput4k.length,
  files: { inputPath, receivedPath, aiPath }
}, null, 2));
