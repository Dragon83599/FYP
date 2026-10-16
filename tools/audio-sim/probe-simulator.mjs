import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { encodeAudioToLinkPackets } from './lib/pipeline.mjs';
import { toMono, readPcm16Wav } from './lib/wav.mjs';

const args = parseArgs(process.argv.slice(2));
if (!args.input || !args.output) {
  console.error(
    'Usage: node tools/audio-sim/probe-simulator.mjs input.wav output.lka ' +
    '[--session 1] [--mtu 247] (ATT MTU; 247 yields 222-byte LKA1 payload)'
  );
  process.exit(2);
}

const wav = readPcm16Wav(resolve(args.input));
if (wav.sampleRate !== 8000) {
  throw new Error(`Input WAV must be 8 kHz, got ${wav.sampleRate} Hz`);
}
const mono = toMono(wav.samples, wav.channels);
const result = encodeAudioToLinkPackets(mono, {
  sessionId: Number.parseInt(args.session ?? '1', 10),
  maxFragmentPayload: Number.parseInt(args.mtu ?? '247', 10) - 3 - 22,
  startTimestampMs: Number.parseInt(args.timestamp ?? '0', 10)
});
const packetStream = Buffer.concat(result.packets);
mkdirSync(dirname(resolve(args.output)), { recursive: true });
writeFileSync(resolve(args.output), packetStream);

console.log(JSON.stringify({
  input: resolve(args.input),
  output: resolve(args.output),
  audioFrames: result.frames.length,
  linkPackets: result.packets.length,
  bytes: packetStream.length
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
