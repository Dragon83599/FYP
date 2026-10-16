import { readFileSync, writeFileSync } from 'node:fs';

export function readPcm16Wav(filePath) {
  const buffer = readFileSync(filePath);
  if (buffer.length < 12 || buffer.toString('ascii', 0, 4) !== 'RIFF' ||
      buffer.toString('ascii', 8, 12) !== 'WAVE') {
    throw new Error('Not a RIFF/WAVE file');
  }

  let offset = 12;
  let format = null;
  let data = null;
  while (offset + 8 <= buffer.length) {
    const chunkId = buffer.toString('ascii', offset, offset + 4);
    const chunkSize = buffer.readUInt32LE(offset + 4);
    const chunkStart = offset + 8;
    const chunkEnd = chunkStart + chunkSize;
    if (chunkEnd > buffer.length) {
      throw new Error(`Truncated WAV chunk: ${chunkId}`);
    }

    if (chunkId === 'fmt ') {
      format = {
        audioFormat: buffer.readUInt16LE(chunkStart),
        channels: buffer.readUInt16LE(chunkStart + 2),
        sampleRate: buffer.readUInt32LE(chunkStart + 4),
        bitsPerSample: buffer.readUInt16LE(chunkStart + 14)
      };
    } else if (chunkId === 'data') {
      data = buffer.subarray(chunkStart, chunkEnd);
    }
    offset = chunkEnd + (chunkSize % 2);
  }

  if (!format || !data) {
    throw new Error('WAV is missing fmt or data chunk');
  }
  if (format.audioFormat !== 1 || format.bitsPerSample !== 16) {
    throw new Error('Only PCM 16-bit WAV is supported');
  }
  if (data.length % 2 !== 0) {
    throw new Error('WAV data size is not aligned to int16');
  }

  const samples = new Int16Array(data.length / 2);
  for (let i = 0; i < samples.length; i += 1) {
    samples[i] = data.readInt16LE(i * 2);
  }
  return { ...format, samples };
}

export function writePcm16Wav(filePath, samples, sampleRate, channels = 1) {
  if (!Number.isInteger(sampleRate) || sampleRate <= 0) {
    throw new Error('Invalid sample rate');
  }
  if (!Number.isInteger(channels) || channels <= 0) {
    throw new Error('Invalid channel count');
  }

  const dataBytes = samples.length * 2;
  const output = Buffer.alloc(44 + dataBytes);
  output.write('RIFF', 0, 'ascii');
  output.writeUInt32LE(36 + dataBytes, 4);
  output.write('WAVE', 8, 'ascii');
  output.write('fmt ', 12, 'ascii');
  output.writeUInt32LE(16, 16);
  output.writeUInt16LE(1, 20);
  output.writeUInt16LE(channels, 22);
  output.writeUInt32LE(sampleRate, 24);
  output.writeUInt32LE(sampleRate * channels * 2, 28);
  output.writeUInt16LE(channels * 2, 32);
  output.writeUInt16LE(16, 34);
  output.write('data', 36, 'ascii');
  output.writeUInt32LE(dataBytes, 40);

  for (let i = 0; i < samples.length; i += 1) {
    output.writeInt16LE(clampInt16(Math.round(samples[i])), 44 + i * 2);
  }
  writeFileSync(filePath, output);
}

export function toMono(samples, channels) {
  if (channels === 1) return Int16Array.from(samples);
  if (!Number.isInteger(channels) || channels <= 0 || samples.length % channels !== 0) {
    throw new Error('Invalid interleaved channel data');
  }
  const frames = samples.length / channels;
  const mono = new Int16Array(frames);
  for (let i = 0; i < frames; i += 1) {
    let sum = 0;
    for (let channel = 0; channel < channels; channel += 1) {
      sum += samples[i * channels + channel];
    }
    mono[i] = clampInt16(Math.round(sum / channels));
  }
  return mono;
}

export function clampInt16(value) {
  if (value < -32768) return -32768;
  if (value > 32767) return 32767;
  return value;
}
