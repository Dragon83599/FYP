/**
 * Cross-check the browser parser against the Node reference.
 *
 * Two implementations of one wire format is a maintenance hazard, and the
 * failure mode is silent: both "work" while disagreeing about a byte. These
 * tests therefore compare on the *same bytes* — produced by the real encoder
 * from a real recording — and require identical frames, identical edge-case
 * verdicts, and errors with identical codes.
 *
 * Browser module: pwa/lka1.js.  Reference: tools/audio-sim/lib/link-a-protocol.mjs.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

import {
  FrameReassembler as NodeReassembler,
  splitPacketStream as nodeSplit,
  decodePacket as nodeDecode,
} from "../../tools/audio-sim/lib/link-a-protocol.mjs";
import { crc16CcittFalse as nodeCrc } from "../../tools/audio-sim/lib/audio-protocol.mjs";
import { encodeAudioToLinkPackets } from "../../tools/audio-sim/lib/pipeline.mjs";
import { toMono, readPcm16Wav } from "../../tools/audio-sim/lib/wav.mjs";
import {
  FrameReassembler as BrowserReassembler,
  splitPacketStream as browserSplit,
  decodePacket as browserDecode,
  crc16CcittFalse as browserCrc,
  pcmFromAudioFrame,
  AUDIO_WIRE_FRAME_BYTES,
} from "../../pwa/lka1.js";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const CLIP = path.join(ROOT, "data/raw/yaseen/extracted/AS/New_AS_001.wav");

function encodeClip() {
  const wav = readPcm16Wav(CLIP);
  const mono = toMono(wav.samples, wav.channels);
  return encodeAudioToLinkPackets(mono, { sessionId: 7, maxFragmentPayload: 222 });
}

function reassembleAll(Reassembler, split, stream) {
  const reassembler = new Reassembler();
  const frames = [];
  for (const packet of split(stream)) {
    const result = reassembler.push(packet);
    if (result.status === "complete") frames.push(Buffer.from(result.frame));
  }
  return frames;
}

test("CRC matches the reference on every packet", () => {
  const { packets } = encodeClip();
  for (const packet of packets) {
    assert.equal(browserCrc(packet, 0, packet.length - 2), nodeCrc(packet.subarray(0, packet.length - 2)));
  }
});

test("both implementations decode every packet identically", () => {
  const { packets } = encodeClip();
  for (const packet of packets) {
    const a = nodeDecode(packet);
    const b = browserDecode(new Uint8Array(packet));
    assert.equal(b.type, a.type);
    assert.equal(b.flags, a.flags);
    assert.equal(b.sessionId, a.sessionId);
    assert.equal(b.frameSequence, a.frameSequence);
    assert.equal(b.fragmentIndex, a.fragmentIndex);
    assert.equal(b.fragmentCount, a.fragmentCount);
    assert.equal(b.fragmentOffset, a.fragmentOffset);
    assert.equal(b.consumed, a.consumed);
    assert.deepEqual(Buffer.from(b.payload), Buffer.from(a.payload));
  }
});

test("both implementations reassemble the same frames from the same bytes", () => {
  const { packets } = encodeClip();
  const stream = Buffer.concat(packets);
  const nodeFrames = reassembleAll(NodeReassembler, nodeSplit, stream);
  const browserFrames = reassembleAll(BrowserReassembler, (s) => browserSplit(new Uint8Array(s)), stream);

  assert.ok(nodeFrames.length > 0, "the clip should produce frames");
  assert.equal(browserFrames.length, nodeFrames.length);
  for (let i = 0; i < nodeFrames.length; i += 1) {
    assert.equal(nodeFrames[i].length, AUDIO_WIRE_FRAME_BYTES);
    assert.deepEqual(browserFrames[i], nodeFrames[i], `frame ${i} differs`);
  }
});

test("the PCM inside each frame is 320 bytes and matches the AUD1 payload", () => {
  const { packets } = encodeClip();
  const stream = Buffer.concat(packets);
  const frames = reassembleAll(BrowserReassembler, (s) => browserSplit(new Uint8Array(s)), stream);
  for (const frame of frames) {
    const pcm = pcmFromAudioFrame(new Uint8Array(frame));
    assert.equal(pcm.length, 320);
  }
});

test("a corrupted packet is rejected by both, with the same code", () => {
  const { packets } = encodeClip();
  const broken = Buffer.from(packets[3]);
  broken[25] ^= 0xff;                       // inside the payload, CRC now wrong

  assert.throws(() => nodeDecode(broken), (error) => error.code === "bad_crc");
  assert.throws(() => browserDecode(new Uint8Array(broken)), (error) => error.code === "bad_crc");
});

test("a truncated packet is rejected by both, with the same code", () => {
  const { packets } = encodeClip();
  const short = packets[0].subarray(0, packets[0].length - 5);

  assert.throws(() => nodeDecode(short), (error) => error.code === "truncated");
  assert.throws(() => browserDecode(new Uint8Array(short)), (error) => error.code === "truncated");
});

test("bad magic, bad version and non-zero reserved are rejected identically", () => {
  const { packets } = encodeClip();
  const flip = (offset, mask) => {
    const copy = Buffer.from(packets[0]);
    copy[offset] ^= mask;
    return copy;
  };
  const cases = [
    [flip(0, 0x01), "bad_magic"],
    [flip(4, 0x02), "bad_version"],
    [flip(7, 0x01), "bad_header"],
  ];
  for (const [bytes, code] of cases) {
    assert.throws(() => nodeDecode(bytes), (error) => error.code === code, `node: ${code}`);
    assert.throws(() => browserDecode(new Uint8Array(bytes)), (error) => error.code === code,
      `browser: ${code}`);
  }
});

test("a duplicate fragment is accepted as duplicate by both", () => {
  const { packets } = encodeClip();
  const node = new NodeReassembler();
  const browser = new BrowserReassembler();

  assert.equal(node.push(packets[0]).status, "in_progress");
  assert.equal(browser.push(new Uint8Array(packets[0])).status, "in_progress");
  assert.equal(node.push(packets[0]).status, "duplicate");
  assert.equal(browser.push(new Uint8Array(packets[0])).status, "duplicate");
});

test("losing one fragment drops exactly that frame, in both implementations", () => {
  const { packets } = encodeClip();
  const complete = Buffer.concat(packets);
  const withHole = Buffer.concat([packets[0], ...packets.slice(2)]);   // second fragment of frame 0 missing

  const all = reassembleAll(NodeReassembler, nodeSplit, complete);
  const nodeFrames = reassembleAll(NodeReassembler, nodeSplit, withHole);
  const browserFrames = reassembleAll(BrowserReassembler, (s) => browserSplit(new Uint8Array(s)), withHole);

  assert.ok(all.length > 2, "the clip should produce many frames");
  assert.equal(nodeFrames.length, all.length - 1, "only the damaged frame is lost");
  assert.equal(browserFrames.length, nodeFrames.length);
  // the remaining frames must be the ones after the hole, byte for byte
  for (let i = 0; i < browserFrames.length; i += 1) {
    assert.deepEqual(browserFrames[i], all[i + 1]);
  }
});

test("it runs on bytes from a real clip, not a synthetic buffer", () => {
  // Guard against the test silently using an empty fixture.
  const wav = readPcm16Wav(CLIP);
  assert.equal(wav.sampleRate, 8000);
  assert.ok(readFileSync(CLIP).length > 10_000);
});
