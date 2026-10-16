export const AUDIO_PROTOCOL_VERSION = 1;
export const AUDIO_PROTOCOL_MAGIC = 0x31445541;
export const AUDIO_SAMPLE_RATE_HZ = 8000;
export const AUDIO_AI_SAMPLE_RATE_HZ = 4000;
export const AUDIO_FRAME_DURATION_MS = 20;
export const AUDIO_CHANNEL_COUNT = 1;
export const AUDIO_BITS_PER_SAMPLE = 16;
export const AUDIO_BYTES_PER_SAMPLE = 2;
export const AUDIO_FRAME_SAMPLES =
  (AUDIO_SAMPLE_RATE_HZ * AUDIO_FRAME_DURATION_MS) / 1000;
export const AUDIO_FRAME_PAYLOAD_BYTES =
  AUDIO_FRAME_SAMPLES * AUDIO_CHANNEL_COUNT * AUDIO_BYTES_PER_SAMPLE;
export const AUDIO_WIRE_HEADER_BYTES = 30;
export const AUDIO_WIRE_CRC_BYTES = 2;
export const AUDIO_WIRE_FRAME_BYTES =
  AUDIO_WIRE_HEADER_BYTES + AUDIO_FRAME_PAYLOAD_BYTES + AUDIO_WIRE_CRC_BYTES;

export const AUDIO_CODEC_PCM_S16_LE = 1;
export const AUDIO_FLAG_TIMESTAMP_VALID = 1 << 0;

export const AUDIO_OFFSET = Object.freeze({
  magic: 0,
  version: 4,
  flags: 5,
  headerBytes: 6,
  codec: 8,
  channels: 9,
  bitsPerSample: 10,
  reserved: 11,
  sampleRateHz: 12,
  frameSamples: 16,
  sequence: 20,
  timestampMs: 24,
  payloadBytes: 28,
  payload: 30
});

export class AudioProtocolError extends Error {
  constructor(code, message) {
    super(message);
    this.name = 'AudioProtocolError';
    this.code = code;
  }
}

export function crc16CcittFalse(data) {
  let crc = 0xffff;
  for (const byte of data) {
    crc ^= byte << 8;
    for (let bit = 0; bit < 8; bit += 1) {
      crc = (crc & 0x8000) !== 0
        ? ((crc << 1) ^ 0x1021)
        : (crc << 1);
      crc &= 0xffff;
    }
  }
  return crc;
}

export function encodeHeader(frame, flags = AUDIO_FLAG_TIMESTAMP_VALID) {
  validateFrame(frame);
  const header = Buffer.alloc(AUDIO_WIRE_HEADER_BYTES);
  header.writeUInt32LE(AUDIO_PROTOCOL_MAGIC, AUDIO_OFFSET.magic);
  header.writeUInt8(AUDIO_PROTOCOL_VERSION, AUDIO_OFFSET.version);
  header.writeUInt8(flags & 0xff, AUDIO_OFFSET.flags);
  header.writeUInt16LE(AUDIO_WIRE_HEADER_BYTES, AUDIO_OFFSET.headerBytes);
  header.writeUInt8(AUDIO_CODEC_PCM_S16_LE, AUDIO_OFFSET.codec);
  header.writeUInt8(AUDIO_CHANNEL_COUNT, AUDIO_OFFSET.channels);
  header.writeUInt8(AUDIO_BITS_PER_SAMPLE, AUDIO_OFFSET.bitsPerSample);
  header.writeUInt8(0, AUDIO_OFFSET.reserved);
  header.writeUInt32LE(frame.sampleRateHz, AUDIO_OFFSET.sampleRateHz);
  header.writeUInt32LE(AUDIO_FRAME_SAMPLES, AUDIO_OFFSET.frameSamples);
  header.writeUInt32LE(frame.sequence >>> 0, AUDIO_OFFSET.sequence);
  header.writeUInt32LE(frame.timestampMs >>> 0, AUDIO_OFFSET.timestampMs);
  header.writeUInt16LE(AUDIO_FRAME_PAYLOAD_BYTES, AUDIO_OFFSET.payloadBytes);
  return header;
}

export function encodeFrame(frame, flags = AUDIO_FLAG_TIMESTAMP_VALID) {
  validateFrame(frame);
  const output = Buffer.alloc(AUDIO_WIRE_FRAME_BYTES);
  encodeHeader(frame, flags).copy(output, 0);

  for (let i = 0; i < AUDIO_FRAME_SAMPLES; i += 1) {
    output.writeInt16LE(frame.samples[i], AUDIO_OFFSET.payload + i * 2);
  }

  const crc = crc16CcittFalse(
    output.subarray(0, AUDIO_WIRE_HEADER_BYTES + AUDIO_FRAME_PAYLOAD_BYTES)
  );
  output.writeUInt16LE(crc, AUDIO_WIRE_HEADER_BYTES + AUDIO_FRAME_PAYLOAD_BYTES);
  return output;
}

export function decodeHeader(input) {
  if (!Buffer.isBuffer(input) || input.length < AUDIO_WIRE_HEADER_BYTES) {
    throw new AudioProtocolError('truncated', 'Audio header is truncated');
  }
  if (input.readUInt32LE(AUDIO_OFFSET.magic) !== AUDIO_PROTOCOL_MAGIC) {
    throw new AudioProtocolError('bad_magic', 'Audio magic is invalid');
  }
  if (input.readUInt8(AUDIO_OFFSET.version) !== AUDIO_PROTOCOL_VERSION) {
    throw new AudioProtocolError('bad_version', 'Audio protocol version is invalid');
  }
  if (input.readUInt16LE(AUDIO_OFFSET.headerBytes) !== AUDIO_WIRE_HEADER_BYTES) {
    throw new AudioProtocolError('bad_header', 'Audio header length is invalid');
  }
  if (input.readUInt8(AUDIO_OFFSET.codec) !== AUDIO_CODEC_PCM_S16_LE) {
    throw new AudioProtocolError('bad_codec', 'Audio codec is unsupported');
  }
  if (input.readUInt8(AUDIO_OFFSET.channels) !== AUDIO_CHANNEL_COUNT) {
    throw new AudioProtocolError('bad_channels', 'Audio channel count is unsupported');
  }
  if (
    input.readUInt8(AUDIO_OFFSET.bitsPerSample) !== AUDIO_BITS_PER_SAMPLE ||
    input.readUInt8(AUDIO_OFFSET.reserved) !== 0
  ) {
    throw new AudioProtocolError('bad_header', 'Audio header flags are invalid');
  }

  const sampleRateHz = input.readUInt32LE(AUDIO_OFFSET.sampleRateHz);
  if (sampleRateHz !== AUDIO_SAMPLE_RATE_HZ) {
    throw new AudioProtocolError('bad_sample_rate', 'Audio sample rate is unsupported');
  }
  if (input.readUInt32LE(AUDIO_OFFSET.frameSamples) !== AUDIO_FRAME_SAMPLES) {
    throw new AudioProtocolError('bad_frame_samples', 'Audio frame sample count is invalid');
  }
  if (input.readUInt16LE(AUDIO_OFFSET.payloadBytes) !== AUDIO_FRAME_PAYLOAD_BYTES) {
    throw new AudioProtocolError('bad_payload_size', 'Audio payload size is invalid');
  }

  return {
    flags: input.readUInt8(AUDIO_OFFSET.flags),
    sampleRateHz,
    frameSamples: AUDIO_FRAME_SAMPLES,
    sequence: input.readUInt32LE(AUDIO_OFFSET.sequence),
    timestampMs: input.readUInt32LE(AUDIO_OFFSET.timestampMs),
    channelCount: AUDIO_CHANNEL_COUNT
  };
}

export function decodeFrame(input) {
  if (!Buffer.isBuffer(input) || input.length < AUDIO_WIRE_FRAME_BYTES) {
    throw new AudioProtocolError('truncated', 'Audio frame is truncated');
  }
  const header = decodeHeader(input);
  const expectedCrc = input.readUInt16LE(
    AUDIO_WIRE_HEADER_BYTES + AUDIO_FRAME_PAYLOAD_BYTES
  );
  const actualCrc = crc16CcittFalse(
    input.subarray(0, AUDIO_WIRE_HEADER_BYTES + AUDIO_FRAME_PAYLOAD_BYTES)
  );
  if (expectedCrc !== actualCrc) {
    throw new AudioProtocolError('bad_crc', 'Audio frame CRC is invalid');
  }

  const samples = new Int16Array(AUDIO_FRAME_SAMPLES);
  for (let i = 0; i < AUDIO_FRAME_SAMPLES; i += 1) {
    samples[i] = input.readInt16LE(AUDIO_OFFSET.payload + i * 2);
  }

  return {
    frame: {
      samples,
      sampleRateHz: header.sampleRateHz,
      timestampMs: header.timestampMs,
      sequence: header.sequence,
      channelCount: header.channelCount
    },
    flags: header.flags,
    consumed: AUDIO_WIRE_FRAME_BYTES
  };
}

export function findAudioMagic(data, start = 0) {
  if (!Buffer.isBuffer(data)) {
    throw new TypeError('data must be a Buffer');
  }
  for (let i = Math.max(0, start); i <= data.length - 4; i += 1) {
    if (data.readUInt32LE(i) === AUDIO_PROTOCOL_MAGIC) {
      return i;
    }
  }
  return -1;
}

function validateFrame(frame) {
  if (!frame || !frame.samples || frame.samples.length !== AUDIO_FRAME_SAMPLES) {
    throw new AudioProtocolError('bad_frame_samples', 'Frame must contain 160 samples');
  }
  if (frame.sampleRateHz !== AUDIO_SAMPLE_RATE_HZ) {
    throw new AudioProtocolError('bad_sample_rate', 'Frame sample rate must be 8000 Hz');
  }
  if ((frame.channelCount ?? AUDIO_CHANNEL_COUNT) !== AUDIO_CHANNEL_COUNT) {
    throw new AudioProtocolError('bad_channels', 'Frame must be mono');
  }
  for (const sample of frame.samples) {
    if (!Number.isInteger(sample) || sample < -32768 || sample > 32767) {
      throw new AudioProtocolError('bad_sample', 'PCM sample must fit int16');
    }
  }
}
