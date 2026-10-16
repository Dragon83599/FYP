import { crc16CcittFalse, AUDIO_WIRE_FRAME_BYTES } from './audio-protocol.mjs';

export const LINK_A_PROTOCOL_VERSION = 1;
export const LINK_A_PROTOCOL_MAGIC = 0x31414b4c;
export const LINK_A_WIRE_HEADER_BYTES = 20;
export const LINK_A_WIRE_CRC_BYTES = 2;
export const LINK_A_WIRE_OVERHEAD_BYTES =
  LINK_A_WIRE_HEADER_BYTES + LINK_A_WIRE_CRC_BYTES;
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
  payload: 20
});

export class LinkAProtocolError extends Error {
  constructor(code, message) {
    super(message);
    this.name = 'LinkAProtocolError';
    this.code = code;
  }
}

export function fragmentFrame(frame, options) {
  const {
    sessionId,
    frameSequence,
    maxFragmentPayload = LINK_A_DEFAULT_MAX_FRAGMENT_PAYLOAD
  } = options ?? {};
  validateFragmentOptions(frame, maxFragmentPayload);

  const count = Math.ceil(frame.length / maxFragmentPayload);
  if (count > LINK_A_MAX_FRAGMENTS) {
    throw new LinkAProtocolError('too_many_fragments', 'Frame needs too many fragments');
  }

  const packets = [];
  for (let index = 0; index < count; index += 1) {
    const offset = index * maxFragmentPayload;
    const end = Math.min(offset + maxFragmentPayload, frame.length);
    const payload = frame.subarray(offset, end);
    let flags = 0;
    if (index === 0) flags |= LINK_A_FLAG_START;
    if (index === count - 1) flags |= LINK_A_FLAG_END;
    packets.push(encodePacket({
      type: LINK_A_PACKET_DATA,
      flags,
      sessionId,
      frameSequence,
      fragmentIndex: index,
      fragmentCount: count,
      fragmentOffset: offset,
      payload
    }));
  }
  return packets;
}

export function encodePacket(packet) {
  validatePacket(packet);
  const fragmentBytes = packet.payload?.length ?? 0;
  const output = Buffer.alloc(
    LINK_A_WIRE_HEADER_BYTES + fragmentBytes + LINK_A_WIRE_CRC_BYTES
  );
  output.writeUInt32LE(LINK_A_PROTOCOL_MAGIC, LINK_A_OFFSET.magic);
  output.writeUInt8(LINK_A_PROTOCOL_VERSION, LINK_A_OFFSET.version);
  output.writeUInt8(packet.type, LINK_A_OFFSET.type);
  output.writeUInt8(packet.flags ?? 0, LINK_A_OFFSET.flags);
  output.writeUInt8(0, LINK_A_OFFSET.reserved);
  output.writeUInt16LE(packet.sessionId, LINK_A_OFFSET.sessionId);
  output.writeUInt32LE(packet.frameSequence >>> 0, LINK_A_OFFSET.frameSequence);
  output.writeUInt8(packet.fragmentIndex ?? 0, LINK_A_OFFSET.fragmentIndex);
  output.writeUInt8(packet.fragmentCount ?? 0, LINK_A_OFFSET.fragmentCount);
  output.writeUInt16LE(packet.fragmentOffset ?? 0, LINK_A_OFFSET.fragmentOffset);
  output.writeUInt16LE(fragmentBytes, LINK_A_OFFSET.fragmentBytes);
  if (fragmentBytes > 0) {
    packet.payload.copy(output, LINK_A_OFFSET.payload);
  }
  const crc = crc16CcittFalse(output.subarray(0, output.length - 2));
  output.writeUInt16LE(crc, output.length - 2);
  return output;
}

export function decodePacket(input) {
  if (!Buffer.isBuffer(input) || input.length < LINK_A_WIRE_OVERHEAD_BYTES) {
    throw new LinkAProtocolError('truncated', 'Link A packet is truncated');
  }
  if (input.readUInt32LE(LINK_A_OFFSET.magic) !== LINK_A_PROTOCOL_MAGIC) {
    throw new LinkAProtocolError('bad_magic', 'Link A magic is invalid');
  }
  if (input.readUInt8(LINK_A_OFFSET.version) !== LINK_A_PROTOCOL_VERSION) {
    throw new LinkAProtocolError('bad_version', 'Link A version is invalid');
  }
  if (input.readUInt8(LINK_A_OFFSET.reserved) !== 0) {
    throw new LinkAProtocolError('bad_header', 'Link A reserved field must be zero');
  }

  const fragmentBytes = input.readUInt16LE(LINK_A_OFFSET.fragmentBytes);
  const expectedLength = LINK_A_WIRE_HEADER_BYTES + fragmentBytes + LINK_A_WIRE_CRC_BYTES;
  if (input.length < expectedLength) {
    throw new LinkAProtocolError('truncated', 'Link A packet is truncated');
  }
  const expectedCrc = input.readUInt16LE(expectedLength - 2);
  const actualCrc = crc16CcittFalse(input.subarray(0, expectedLength - 2));
  if (expectedCrc !== actualCrc) {
    throw new LinkAProtocolError('bad_crc', 'Link A packet CRC is invalid');
  }

  const packet = {
    type: input.readUInt8(LINK_A_OFFSET.type),
    flags: input.readUInt8(LINK_A_OFFSET.flags),
    sessionId: input.readUInt16LE(LINK_A_OFFSET.sessionId),
    frameSequence: input.readUInt32LE(LINK_A_OFFSET.frameSequence),
    fragmentIndex: input.readUInt8(LINK_A_OFFSET.fragmentIndex),
    fragmentCount: input.readUInt8(LINK_A_OFFSET.fragmentCount),
    fragmentOffset: input.readUInt16LE(LINK_A_OFFSET.fragmentOffset),
    payload: fragmentBytes === 0
      ? Buffer.alloc(0)
      : input.subarray(LINK_A_OFFSET.payload, LINK_A_OFFSET.payload + fragmentBytes),
    consumed: expectedLength
  };
  validatePacket(packet);
  return packet;
}

export function readPacketLength(input, offset = 0) {
  if (!Buffer.isBuffer(input)) {
    throw new TypeError('input must be a Buffer');
  }
  if (offset < 0 || offset + LINK_A_WIRE_OVERHEAD_BYTES > input.length) {
    throw new LinkAProtocolError('truncated', 'Link A stream header is truncated');
  }
  const fragmentBytes = input.readUInt16LE(offset + LINK_A_OFFSET.fragmentBytes);
  const packetLength = LINK_A_WIRE_HEADER_BYTES + fragmentBytes + LINK_A_WIRE_CRC_BYTES;
  if (offset + packetLength > input.length) {
    throw new LinkAProtocolError('truncated', 'Link A stream packet is truncated');
  }
  return packetLength;
}

export function splitPacketStream(input) {
  const packets = [];
  let offset = 0;
  while (offset < input.length) {
    const packetLength = readPacketLength(input, offset);
    packets.push(input.subarray(offset, offset + packetLength));
    offset += packetLength;
  }
  return packets;
}

export class FrameReassembler {
  constructor({ maxPendingFrames = 8 } = {}) {
    this.maxPendingFrames = maxPendingFrames;
    this.pending = new Map();
  }

  push(packetBytes) {
    const packet = decodePacket(packetBytes);
    if (packet.type !== LINK_A_PACKET_DATA) {
      return { status: 'ignored', packet };
    }

    const key = `${packet.sessionId}:${packet.frameSequence}`;
    let state = this.pending.get(key);
    if (!state) {
      state = {
        packet,
        frame: Buffer.alloc(AUDIO_WIRE_FRAME_BYTES),
        covered: new Uint8Array(AUDIO_WIRE_FRAME_BYTES),
        seen: new Set(),
        receivedBytes: 0,
        frameLength: 0
      };
      this.pending.set(key, state);
      this.#prune();
    }
    if (
      state.packet.fragmentCount !== packet.fragmentCount ||
      packet.fragmentIndex >= packet.fragmentCount
    ) {
      throw new LinkAProtocolError('bad_fragment', 'Fragment metadata conflicts');
    }
    if (state.seen.has(packet.fragmentIndex)) {
      const existing = state.frame.subarray(
        packet.fragmentOffset,
        packet.fragmentOffset + packet.payload.length
      );
      if (!existing.equals(packet.payload)) {
        throw new LinkAProtocolError('conflict', 'Duplicate fragment has different data');
      }
      return { status: 'duplicate', packet };
    }

    const endOffset = packet.fragmentOffset + packet.payload.length;
    if (endOffset > AUDIO_WIRE_FRAME_BYTES) {
      throw new LinkAProtocolError('bad_fragment', 'Fragment exceeds frame bounds');
    }
    if ((packet.flags & LINK_A_FLAG_END) !== 0) {
      if (endOffset !== AUDIO_WIRE_FRAME_BYTES) {
        throw new LinkAProtocolError('bad_fragment', 'Last fragment does not end at 352 bytes');
      }
      if (state.frameLength !== 0 && state.frameLength !== endOffset) {
        throw new LinkAProtocolError('bad_fragment', 'Frame length conflicts');
      }
      state.frameLength = endOffset;
    }

    for (let i = 0; i < packet.payload.length; i += 1) {
      const index = packet.fragmentOffset + i;
      if (state.covered[index]) {
        if (state.frame[index] !== packet.payload[i]) {
          throw new LinkAProtocolError('conflict', 'Overlapping fragments conflict');
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
        status: 'complete',
        packet,
        frame: Buffer.from(state.frame),
        frameLength: state.frameLength
      };
    }
    return { status: 'in_progress', packet };
  }

  dropSequenceBefore(frameSequence) {
    for (const [key, state] of this.pending) {
      if (state.packet.frameSequence < frameSequence) {
        this.pending.delete(key);
      }
    }
  }

  #prune() {
    while (this.pending.size > this.maxPendingFrames) {
      const oldestKey = this.pending.keys().next().value;
      this.pending.delete(oldestKey);
    }
  }
}

function validateFragmentOptions(frame, maxFragmentPayload) {
  if (!Buffer.isBuffer(frame) || frame.length === 0 || frame.length > AUDIO_WIRE_FRAME_BYTES) {
    throw new LinkAProtocolError('bad_frame', 'Frame must be between 1 and 352 bytes');
  }
  if (!Number.isInteger(maxFragmentPayload) || maxFragmentPayload <= 0 || maxFragmentPayload > 65535) {
    throw new LinkAProtocolError('bad_fragment', 'Invalid fragment payload size');
  }
}

function validatePacket(packet) {
  if (![LINK_A_PACKET_DATA, LINK_A_PACKET_ACK, LINK_A_PACKET_NACK].includes(packet.type)) {
    throw new LinkAProtocolError('bad_type', 'Unsupported Link A packet type');
  }
  const allowedFlags =
    LINK_A_FLAG_START | LINK_A_FLAG_END | LINK_A_FLAG_RETRANSMISSION;
  if (((packet.flags ?? 0) & ~allowedFlags) !== 0) {
    throw new LinkAProtocolError('bad_header', 'Unsupported Link A flags');
  }
  if (packet.type !== LINK_A_PACKET_DATA) {
    if (
      (packet.fragmentIndex ?? 0) !== 0 ||
      (packet.fragmentCount ?? 0) !== 0 ||
      (packet.fragmentOffset ?? 0) !== 0 ||
      (packet.payload?.length ?? 0) !== 0 ||
      (packet.flags ?? 0) !== 0
    ) {
      throw new LinkAProtocolError('bad_header', 'ACK/NACK metadata must be zero');
    }
    return;
  }

  const fragmentBytes = packet.payload?.length ?? 0;
  if (
    !Number.isInteger(packet.sessionId) ||
    packet.sessionId < 0 ||
    packet.sessionId > 0xffff ||
    !Number.isInteger(packet.frameSequence) ||
    packet.frameSequence < 0 ||
    packet.frameSequence > 0xffffffff ||
    packet.fragmentCount <= 0 ||
    packet.fragmentCount > LINK_A_MAX_FRAGMENTS ||
    packet.fragmentIndex < 0 ||
    packet.fragmentIndex >= packet.fragmentCount ||
    packet.fragmentOffset < 0 ||
    fragmentBytes <= 0 ||
    packet.fragmentOffset + fragmentBytes > AUDIO_WIRE_FRAME_BYTES
  ) {
    throw new LinkAProtocolError('bad_fragment', 'Invalid data fragment metadata');
  }
  const expectedStart = packet.fragmentIndex === 0 ? LINK_A_FLAG_START : 0;
  const expectedEnd =
    packet.fragmentIndex === packet.fragmentCount - 1 ? LINK_A_FLAG_END : 0;
  if ((packet.flags & (LINK_A_FLAG_START | LINK_A_FLAG_END)) !== (expectedStart | expectedEnd)) {
    throw new LinkAProtocolError('bad_fragment', 'Fragment flags do not match index');
  }
}
