/**
 * Link A (`LKA1`) packet parsing and frame reassembly, browser-safe.
 *
 * Why a second implementation exists
 * ---------------------------------
 * `tools/audio-sim/lib/link-a-protocol.mjs` is the reference, but it is written
 * against Node's `Buffer`, which a browser does not have. When the probe is real,
 * the *phone* has to do this work between the BLE notification and the model:
 * verify the CRC, check the fragment metadata, reassemble the 352-byte AUD1
 * frame, and hand 320 bytes of PCM to the pre-processing chain.
 *
 * Two implementations of one wire format is exactly how silent divergence
 * starts, so `tests/node/lka1-browser.test.mjs` feeds the same bytes to both and
 * requires identical output, including identical rejections. If you change one
 * side, that test fails until the other side follows.
 *
 * Wire formats (frozen in docs/link-a-interface-freeze.md):
 *   AUD1 frame : 30 B header + 320 B PCM + 2 B CRC16   = 352 B
 *   LKA1 packet: 20 B header + <=222 B payload + 2 B CRC16
 *
 * Pure ES module: no Node APIs, no Buffer, no DOM.
 */

export const LINK_A_PROTOCOL_VERSION = 1;
export const LINK_A_PROTOCOL_MAGIC = 0x31414b4c;      // "LKA1" little-endian
export const LINK_A_WIRE_HEADER_BYTES = 20;
export const LINK_A_WIRE_CRC_BYTES = 2;
export const LINK_A_WIRE_OVERHEAD_BYTES = LINK_A_WIRE_HEADER_BYTES + LINK_A_WIRE_CRC_BYTES;
export const LINK_A_BLE_ATT_MTU = 247;
export const LINK_A_DEFAULT_LINK_PAYLOAD_BYTES = LINK_A_BLE_ATT_MTU - 3;
export const LINK_A_DEFAULT_MAX_FRAGMENT_PAYLOAD =
  LINK_A_DEFAULT_LINK_PAYLOAD_BYTES - LINK_A_WIRE_OVERHEAD_BYTES;
export const LINK_A_MAX_FRAGMENTS = 255;

export const LINK_A_PACKET_DATA = 1;
export const LINK_A_PACKET_ACK = 2;
export const LINK_A_PACKET_NACK = 3;

export const LINK_A_FLAG_START = 1 << 0;
export const LINK_A_FLAG_END = 1 << 1;
export const LINK_A_FLAG_RETRANSMISSION = 1 << 2;

export const LINK_A_OFFSET = Object.freeze({
  magic: 0,
  version: 4,
  type: 5,
  flags: 6,
  reserved: 7,
  sessionId: 8,
  frameSequence: 10,
  fragmentIndex: 14,
  fragmentCount: 15,
  fragmentOffset: 16,
  fragmentBytes: 18,
  payload: 20,
});

/** AUD1 application frame: 352 bytes (30 header + 320 PCM + 2 CRC). */
export const AUDIO_WIRE_FRAME_BYTES = 352;

export class LinkAProtocolError extends Error {
  constructor(code, message) {
    super(message);
    this.name = "LinkAProtocolError";
    this.code = code;
  }
}

/** CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF) over bytes[start, end). */
export function crc16CcittFalse(bytes, start = 0, end = bytes.length) {
  let crc = 0xffff;
  for (let i = start; i < end; i += 1) {
    crc ^= bytes[i] << 8;
    for (let bit = 0; bit < 8; bit += 1) {
      crc = (crc & 0x8000) !== 0 ? ((crc << 1) ^ 0x1021) : (crc << 1);
      crc &= 0xffff;
    }
  }
  return crc;
}

function requireBytes(input, what) {
  if (!(input instanceof Uint8Array)) {
    throw new TypeError(`${what} must be a Uint8Array`);
  }
  return input;
}

/**
 * Parse one LKA1 packet from the start of `input`.
 *
 * Returns the decoded fields plus `consumed`, the number of bytes the packet
 * occupies -- a notification may carry exactly one packet, a file may carry
 * thousands, and both use this.
 */
export function decodePacket(input) {
  requireBytes(input, "packet");
  if (input.length < LINK_A_WIRE_OVERHEAD_BYTES) {
    throw new LinkAProtocolError("truncated", "Link A packet is truncated");
  }
  const view = new DataView(input.buffer, input.byteOffset, input.byteLength);
  if (view.getUint32(LINK_A_OFFSET.magic, true) !== LINK_A_PROTOCOL_MAGIC) {
    throw new LinkAProtocolError("bad_magic", "Link A magic is invalid");
  }
  if (view.getUint8(LINK_A_OFFSET.version) !== LINK_A_PROTOCOL_VERSION) {
    throw new LinkAProtocolError("bad_version", "Link A version is invalid");
  }
  if (view.getUint8(LINK_A_OFFSET.reserved) !== 0) {
    throw new LinkAProtocolError("bad_header", "Link A reserved field must be zero");
  }

  const fragmentBytes = view.getUint16(LINK_A_OFFSET.fragmentBytes, true);
  const expectedLength = LINK_A_WIRE_HEADER_BYTES + fragmentBytes + LINK_A_WIRE_CRC_BYTES;
  if (input.length < expectedLength) {
    throw new LinkAProtocolError("truncated", "Link A packet is truncated");
  }
  const expectedCrc = view.getUint16(expectedLength - 2, true);
  const actualCrc = crc16CcittFalse(input, 0, expectedLength - 2);
  if (expectedCrc !== actualCrc) {
    throw new LinkAProtocolError("bad_crc", "Link A packet CRC is invalid");
  }

  const packet = {
    type: view.getUint8(LINK_A_OFFSET.type),
    flags: view.getUint8(LINK_A_OFFSET.flags),
    sessionId: view.getUint16(LINK_A_OFFSET.sessionId, true),
    frameSequence: view.getUint32(LINK_A_OFFSET.frameSequence, true),
    fragmentIndex: view.getUint8(LINK_A_OFFSET.fragmentIndex),
    fragmentCount: view.getUint8(LINK_A_OFFSET.fragmentCount),
    fragmentOffset: view.getUint16(LINK_A_OFFSET.fragmentOffset, true),
    payload: fragmentBytes === 0
      ? new Uint8Array(0)
      : input.subarray(LINK_A_OFFSET.payload, LINK_A_OFFSET.payload + fragmentBytes),
    consumed: expectedLength,
  };
  validatePacket(packet);
  return packet;
}

function validatePacket(packet) {
  if (![LINK_A_PACKET_DATA, LINK_A_PACKET_ACK, LINK_A_PACKET_NACK].includes(packet.type)) {
    throw new LinkAProtocolError("bad_type", "Unsupported Link A packet type");
  }
  const allowedFlags = LINK_A_FLAG_START | LINK_A_FLAG_END | LINK_A_FLAG_RETRANSMISSION;
  if ((packet.flags & ~allowedFlags) !== 0) {
    throw new LinkAProtocolError("bad_header", "Unsupported Link A flags");
  }
  if (packet.type !== LINK_A_PACKET_DATA) {
    if (
      packet.fragmentIndex !== 0 ||
      packet.fragmentCount !== 0 ||
      packet.fragmentOffset !== 0 ||
      packet.payload.length !== 0 ||
      packet.flags !== 0
    ) {
      throw new LinkAProtocolError("bad_header", "ACK/NACK metadata must be zero");
    }
    return;
  }

  const fragmentBytes = packet.payload.length;
  if (
    packet.sessionId < 0 || packet.sessionId > 0xffff ||
    packet.frameSequence < 0 ||
    packet.fragmentCount <= 0 || packet.fragmentCount > LINK_A_MAX_FRAGMENTS ||
    packet.fragmentIndex < 0 || packet.fragmentIndex >= packet.fragmentCount ||
    packet.fragmentOffset < 0 ||
    fragmentBytes <= 0 ||
    packet.fragmentOffset + fragmentBytes > AUDIO_WIRE_FRAME_BYTES
  ) {
    throw new LinkAProtocolError("bad_fragment", "Invalid data fragment metadata");
  }
  const expectedStart = packet.fragmentIndex === 0 ? LINK_A_FLAG_START : 0;
  const expectedEnd = packet.fragmentIndex === packet.fragmentCount - 1 ? LINK_A_FLAG_END : 0;
  if ((packet.flags & (LINK_A_FLAG_START | LINK_A_FLAG_END)) !== (expectedStart | expectedEnd)) {
    throw new LinkAProtocolError("bad_fragment", "Fragment flags do not match index");
  }
}

/** Byte length of the packet starting at `offset` (header only, no CRC check). */
export function readPacketLength(input, offset = 0) {
  requireBytes(input, "stream");
  if (offset < 0 || offset + LINK_A_WIRE_OVERHEAD_BYTES > input.length) {
    throw new LinkAProtocolError("truncated", "Link A stream header is truncated");
  }
  const view = new DataView(input.buffer, input.byteOffset, input.byteLength);
  const fragmentBytes = view.getUint16(offset + LINK_A_OFFSET.fragmentBytes, true);
  const packetLength = LINK_A_WIRE_HEADER_BYTES + fragmentBytes + LINK_A_WIRE_CRC_BYTES;
  if (offset + packetLength > input.length) {
    throw new LinkAProtocolError("truncated", "Link A stream packet is truncated");
  }
  return packetLength;
}

/** Split a byte stream (e.g. a replay file) into packet views. */
export function splitPacketStream(input) {
  requireBytes(input, "stream");
  const packets = [];
  let offset = 0;
  while (offset < input.length) {
    const length = readPacketLength(input, offset);
    packets.push(input.subarray(offset, offset + length));
    offset += length;
  }
  return packets;
}

/**
 * Reassemble 352-byte AUD1 frames from LKA1 packets.
 *
 * Mirrors `FrameReassembler` in the Node reference: the same duplicate,
 * overlap-conflict, bounds and completion rules, so the two produce the same
 * frames *and* reject the same inputs.
 */
export class FrameReassembler {
  constructor({ maxPendingFrames = 8 } = {}) {
    this.maxPendingFrames = maxPendingFrames;
    this.pending = new Map();
  }

  push(packetBytes) {
    const packet = decodePacket(packetBytes);
    if (packet.type !== LINK_A_PACKET_DATA) {
      return { status: "ignored", packet };
    }

    const key = `${packet.sessionId}:${packet.frameSequence}`;
    let state = this.pending.get(key);
    if (!state) {
      state = {
        packet,
        frame: new Uint8Array(AUDIO_WIRE_FRAME_BYTES),
        covered: new Uint8Array(AUDIO_WIRE_FRAME_BYTES),
        seen: new Set(),
        receivedBytes: 0,
        frameLength: 0,
      };
      this.pending.set(key, state);
      this.#prune();
    }
    if (
      state.packet.fragmentCount !== packet.fragmentCount ||
      packet.fragmentIndex >= packet.fragmentCount
    ) {
      throw new LinkAProtocolError("bad_fragment", "Fragment metadata conflicts");
    }
    if (state.seen.has(packet.fragmentIndex)) {
      const start = packet.fragmentOffset;
      const existing = state.frame.subarray(start, start + packet.payload.length);
      if (!bytesEqual(existing, packet.payload)) {
        throw new LinkAProtocolError("conflict", "Duplicate fragment has different data");
      }
      return { status: "duplicate", packet };
    }

    const endOffset = packet.fragmentOffset + packet.payload.length;
    if (endOffset > AUDIO_WIRE_FRAME_BYTES) {
      throw new LinkAProtocolError("bad_fragment", "Fragment exceeds frame bounds");
    }
    if ((packet.flags & LINK_A_FLAG_END) !== 0) {
      if (endOffset !== AUDIO_WIRE_FRAME_BYTES) {
        throw new LinkAProtocolError("bad_fragment", "Last fragment does not end at 352 bytes");
      }
      if (state.frameLength !== 0 && state.frameLength !== endOffset) {
        throw new LinkAProtocolError("bad_fragment", "Frame length conflicts");
      }
      state.frameLength = endOffset;
    }

    for (let i = 0; i < packet.payload.length; i += 1) {
      const index = packet.fragmentOffset + i;
      if (state.covered[index]) {
        if (state.frame[index] !== packet.payload[i]) {
          throw new LinkAProtocolError("conflict", "Overlapping fragments conflict");
        }
      } else {
        state.frame[index] = packet.payload[i];
        state.covered[index] = 1;
        state.receivedBytes += 1;
      }
    }
    state.seen.add(packet.fragmentIndex);

    if (
      state.frameLength !== 0 &&
      state.seen.size === packet.fragmentCount &&
      state.receivedBytes === state.frameLength
    ) {
      this.pending.delete(key);
      return {
        status: "complete",
        packet,
        frame: state.frame.slice(0, state.frameLength),
        frameLength: state.frameLength,
      };
    }
    return { status: "in_progress", packet };
  }

  dropSequenceBefore(frameSequence) {
    for (const [key, state] of this.pending) {
      if (state.packet.frameSequence < frameSequence) this.pending.delete(key);
    }
  }

  #prune() {
    while (this.pending.size > this.maxPendingFrames) {
      const oldestKey = this.pending.keys().next().value;
      this.pending.delete(oldestKey);
    }
  }
}

function bytesEqual(a, b) {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i += 1) {
    if (a[i] !== b[i]) return false;
  }
  return true;
}

/**
 * Pull the 320 PCM bytes out of a 352-byte AUD1 frame (header at 30, CRC at 350).
 *
 * Kept here rather than in the page so the same offsets are used by the replay
 * tool and, later, by the BLE path.
 */
export function pcmFromAudioFrame(frame) {
  requireBytes(frame, "frame");
  if (frame.length !== AUDIO_WIRE_FRAME_BYTES) {
    throw new LinkAProtocolError(
      "bad_frame",
      `frame must be ${AUDIO_WIRE_FRAME_BYTES} bytes, got ${frame.length}`,
    );
  }
  const view = new DataView(frame.buffer, frame.byteOffset, frame.byteLength);
  const expectedCrc = view.getUint16(AUDIO_WIRE_FRAME_BYTES - 2, true);
  const actualCrc = crc16CcittFalse(frame, 0, AUDIO_WIRE_FRAME_BYTES - 2);
  if (expectedCrc !== actualCrc) {
    throw new LinkAProtocolError("bad_crc", "AUD1 frame CRC is invalid");
  }
  return frame.subarray(30, 350);
}
