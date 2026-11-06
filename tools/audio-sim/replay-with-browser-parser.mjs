#!/usr/bin/env node
/**
 * Replay a recording through the *browser* parser, end to end.
 *
 * `probe-simulator.mjs` encodes a wav into LKA1 packets and
 * `host-receiver.mjs` decodes it with the Node reference. This script does the
 * same round trip through `pwa/lka1.js` — the code the phone will run once the
 * probe exists — and reports whether the PCM came back byte-identical.
 *
 * It is the quickest way to answer "does the browser-side unpacker still work?"
 * without a phone, a probe or a BLE adapter:
 *
 *   node tools/audio-sim/replay-with-browser-parser.mjs
 *   node tools/audio-sim/replay-with-browser-parser.mjs path/to/other.wav
 */

import { readPcm16Wav, toMono } from "./lib/wav.mjs";
import { encodeAudioToLinkPackets } from "./lib/pipeline.mjs";
import {
  FrameReassembler,
  splitPacketStream,
  pcmFromAudioFrame,
} from "../../pwa/lka1.js";

const DEFAULT_CLIP = new URL(
  "../../data/raw/yaseen/extracted/AS/New_AS_001.wav",
  import.meta.url,
);

const input = process.argv[2] ? new URL(`file://${process.argv[2]}`) : DEFAULT_CLIP;

const wav = readPcm16Wav(input);
if (wav.sampleRate !== 8000) {
  console.error(`expected an 8 kHz recording, got ${wav.sampleRate} Hz`);
  process.exit(2);
}
const mono = toMono(wav.samples, wav.channels);

// Same fragmentation the probe uses: ATT MTU 247 -> 222 bytes of LKA1 payload.
const { packets } = encodeAudioToLinkPackets(mono, { sessionId: 1, maxFragmentPayload: 222 });
const stream = new Uint8Array(Buffer.concat(packets));

const reassembler = new FrameReassembler();
const pcmBytes = [];
let frames = 0;
for (const packet of splitPacketStream(stream)) {
  const result = reassembler.push(packet);
  if (result.status === "complete") {
    for (const byte of pcmFromAudioFrame(result.frame)) pcmBytes.push(byte);
    frames += 1;
  }
}

const samples = new Int16Array(pcmBytes.length / 2);
for (let i = 0; i < samples.length; i += 1) {
  samples[i] = pcmBytes[2 * i] | (pcmBytes[2 * i + 1] << 8);
}

const overlap = Math.min(samples.length, mono.length);
let maxAbsDiff = 0;
for (let i = 0; i < overlap; i += 1) {
  maxAbsDiff = Math.max(maxAbsDiff, Math.abs(samples[i] - mono[i]));
}
let padNonZero = 0;
for (let i = overlap; i < samples.length; i += 1) {
  if (samples[i] !== 0) padNonZero += 1;
}

console.log(JSON.stringify({
  input: input.pathname,
  packets: packets.length,
  frames,
  samplesOut: samples.length,
  samplesIn: mono.length,
  overlapMaxAbsDiff: maxAbsDiff,
  paddedSamples: samples.length - overlap,
  paddedNonZero: padNonZero,
  ok: maxAbsDiff === 0 && padNonZero === 0,
}, null, 2));

if (maxAbsDiff !== 0) process.exit(1);
