/**
 * Minimal RIFF/WAVE parser for the browser.
 *
 * The page deliberately does not use `AudioContext.decodeAudioData`: browsers
 * resample to the context's sample rate, so the samples would depend on the
 * device's audio setting and could never be compared with Python at 1e-3.
 * Reading the PCM directly keeps the input to the pipeline identical to what
 * `tools/ai/repro_ma2023.py` reads (int16 / 32768).
 *
 * Supported: PCM 8/16/24/32-bit integer and 32-bit float, 1 or 2 channels.
 * Anything else is rejected with a message rather than silently converted.
 */

export function parseWav(arrayBuffer) {
  const view = new DataView(arrayBuffer);
  if (view.byteLength < 12) throw new Error("文件太小，不是 wav");
  const riff = String.fromCharCode(...new Uint8Array(arrayBuffer, 0, 4));
  const wave = String.fromCharCode(...new Uint8Array(arrayBuffer, 8, 4));
  if (riff !== "RIFF" || wave !== "WAVE") throw new Error("不是 RIFF/WAVE 文件");

  let offset = 12;
  let fmt = null;
  let data = null;
  while (offset + 8 <= view.byteLength) {
    const id = String.fromCharCode(...new Uint8Array(arrayBuffer, offset, 4));
    const size = view.getUint32(offset + 4, true);
    const start = offset + 8;
    const end = start + size;
    if (end > view.byteLength) throw new Error(`wav 的 ${id} 块越界`);
    if (id === "fmt ") {
      fmt = {
        format: view.getUint16(start, true),
        channels: view.getUint16(start + 2, true),
        sampleRate: view.getUint32(start + 4, true),
        bitsPerSample: view.getUint16(start + 14, true),
      };
    } else if (id === "data") {
      data = { start, size };
    }
    offset = end + (size % 2);
  }
  if (!fmt || !data) throw new Error("wav 缺少 fmt 或 data 块");

  const bytesPerSample = fmt.bitsPerSample / 8;
  const frames = Math.floor(data.size / (bytesPerSample * fmt.channels));
  const samples = new Float64Array(frames);

  // PCM integer formats: scale by the negative full-scale value, exactly as
  // Python's int16 / 32768 does, so the two sides see the same numbers.
  const readFrame = (frame) => {
    const base = data.start + frame * bytesPerSample * fmt.channels;
    const mono = new Float64Array(fmt.channels);
    for (let ch = 0; ch < fmt.channels; ch += 1) {
      const at = base + ch * bytesPerSample;
      if (fmt.format === 3 && fmt.bitsPerSample === 32) {
        mono[ch] = view.getFloat32(at, true);
      } else if (fmt.format === 1 && fmt.bitsPerSample === 16) {
        mono[ch] = view.getInt16(at, true) / 32768;
      } else if (fmt.format === 1 && fmt.bitsPerSample === 8) {
        mono[ch] = (view.getUint8(at) - 128) / 128;
      } else if (fmt.format === 1 && fmt.bitsPerSample === 24) {
        const b0 = view.getUint8(at);
        const b1 = view.getUint8(at + 1);
        const b2 = view.getInt8(at + 2);
        mono[ch] = ((b2 << 16) | (b1 << 8) | b0) / 8388608;
      } else if (fmt.format === 1 && fmt.bitsPerSample === 32) {
        mono[ch] = view.getInt32(at, true) / 2147483648;
      } else {
        throw new Error(`不支持的格式：format=${fmt.format}, ${fmt.bitsPerSample} bit`);
      }
    }
    return mono;
  };

  let clipped = 0;
  for (let frame = 0; frame < frames; frame += 1) {
    const mono = readFrame(frame);
    let value = mono[0];
    if (mono.length > 1) {
      for (let ch = 0; ch < mono.length; ch += 1) value += mono[ch];
      value /= mono.length;
    }
    samples[frame] = value;
    if (value > 1 || value < -1) clipped += 1;
  }

  return {
    sampleRate: fmt.sampleRate,
    channels: fmt.channels,
    bitsPerSample: fmt.bitsPerSample,
    samples,
    clipped,
  };
}
