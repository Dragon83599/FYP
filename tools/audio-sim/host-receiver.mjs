import { mkdirSync, readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { decodeFrame } from './lib/audio-protocol.mjs';
import { FrameReassembler, splitPacketStream } from './lib/link-a-protocol.mjs';
import { writePcm16Wav } from './lib/wav.mjs';
import { decimate8kTo4k } from './lib/dsp.mjs';

const args = parseArgs(process.argv.slice(2));
if (!args.input || !args.output) {
  console.error(
    'Usage: node tools/audio-sim/host-receiver.mjs input.lka output-8k.wav ' +
    '[--ai output-4k.wav]'
  );
  process.exit(2);
}

const packets = splitPacketStream(readFileSync(resolve(args.input)));
const reassembler = new FrameReassembler();
const frames = [];
const errors = [];
packets.forEach((packet, index) => {
  try {
    const result = reassembler.push(packet);
    if (result.status === 'complete') {
      frames.push(decodeFrame(result.frame).frame);
    }
  } catch (error) {
    errors.push({ packetIndex: index, message: error.message });
  }
});
frames.sort((a, b) => a.sequence - b.sequence);

const samples = new Int16Array(frames.length * 160);
frames.forEach((frame, index) => samples.set(frame.samples, index * 160));
const outputPath = resolve(args.output);
mkdirSync(dirname(outputPath), { recursive: true });
writePcm16Wav(outputPath, samples, 8000, 1);

let aiPath = null;
let aiSamples = 0;
if (args.ai) {
  const ai = decimate8kTo4k(samples);
  aiPath = resolve(args.ai);
  aiSamples = ai.length;
  writePcm16Wav(aiPath, ai, 4000, 1);
}

console.log(JSON.stringify({
  input: resolve(args.input),
  output: outputPath,
  aiOutput: aiPath,
  inputPackets: packets.length,
  decodedFrames: frames.length,
  incompleteFrames: reassembler.pending.size,
  errors: errors.length,
  outputSamples8k: samples.length,
  outputSamples4k: aiSamples
}, null, 2));

function parseArgs(values) {
  const parsed = {};
  for (let i = 0; i < values.length; i += 1) {
    const value = values[i];
    if (value.startsWith('--')) {
      parsed[value.slice(2)] = values[i + 1];
      i += 1;
    } else if (!parsed.input) {
      parsed.input = value;
    } else if (!parsed.output) {
      parsed.output = value;
    }
  }
  return parsed;
}
