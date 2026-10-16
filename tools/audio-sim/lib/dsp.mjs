import { clampInt16 } from './wav.mjs';

export function generateSineWave({
  frequencyHz,
  sampleRate = 8000,
  durationSeconds,
  amplitude = 0.5
}) {
  const sampleCount = Math.round(sampleRate * durationSeconds);
  const samples = new Int16Array(sampleCount);
  for (let i = 0; i < sampleCount; i += 1) {
    const value = Math.sin((2 * Math.PI * frequencyHz * i) / sampleRate);
    samples[i] = clampInt16(Math.round(value * amplitude * 32767));
  }
  return samples;
}

export function designLowpass({ sampleRate, cutoffHz, taps = 63 }) {
  if (taps < 3 || taps % 2 === 0) {
    throw new Error('Low-pass FIR taps must be an odd number >= 3');
  }
  if (cutoffHz <= 0 || cutoffHz >= sampleRate / 2) {
    throw new Error('Cutoff must be between 0 and Nyquist');
  }

  const middle = (taps - 1) / 2;
  const normalizedCutoff = cutoffHz / sampleRate;
  const coefficients = new Float64Array(taps);
  let sum = 0;

  for (let i = 0; i < taps; i += 1) {
    const n = i - middle;
    const ideal = 2 * normalizedCutoff * sinc(2 * normalizedCutoff * n);
    const window = 0.42 -
      0.5 * Math.cos((2 * Math.PI * i) / (taps - 1)) +
      0.08 * Math.cos((4 * Math.PI * i) / (taps - 1));
    coefficients[i] = ideal * window;
    sum += coefficients[i];
  }

  for (let i = 0; i < coefficients.length; i += 1) {
    coefficients[i] /= sum;
  }
  return coefficients;
}

export function applyLowpass(samples, coefficients) {
  const output = new Float64Array(samples.length);
  for (let i = 0; i < samples.length; i += 1) {
    let value = 0;
    for (let tap = 0; tap < coefficients.length; tap += 1) {
      const index = i - tap;
      if (index >= 0) value += samples[index] * coefficients[tap];
    }
    output[i] = value;
  }
  return output;
}

export function decimate8kTo4k(samples, {
  cutoffHz = 1700,
  taps = 63
} = {}) {
  const coefficients = designLowpass({ sampleRate: 8000, cutoffHz, taps });
  const filtered = applyLowpass(samples, coefficients);
  const output = new Int16Array(Math.floor(samples.length / 2));
  for (let i = 0; i < output.length; i += 1) {
    output[i] = clampInt16(Math.round(filtered[i * 2]));
  }
  return output;
}

export class StreamingDecimator8kTo4k {
  constructor({ cutoffHz = 1700, taps = 63 } = {}) {
    this.coefficients = designLowpass({ sampleRate: 8000, cutoffHz, taps });
    this.history = new Float64Array(taps);
    this.phase = 0;
  }

  process(input) {
    const output = [];
    for (const sample of input) {
      for (let i = this.history.length - 1; i > 0; i -= 1) {
        this.history[i] = this.history[i - 1];
      }
      this.history[0] = sample;

      if (this.phase === 0) {
        let value = 0;
        for (let i = 0; i < this.coefficients.length; i += 1) {
          value += this.history[i] * this.coefficients[i];
        }
        output.push(clampInt16(Math.round(value)));
      }
      this.phase ^= 1;
    }
    return Int16Array.from(output);
  }
}

export function rms(samples) {
  if (samples.length === 0) return 0;
  let sum = 0;
  for (const sample of samples) sum += sample * sample;
  return Math.sqrt(sum / samples.length);
}

function sinc(value) {
  if (Math.abs(value) < 1e-12) return 1;
  return Math.sin(Math.PI * value) / (Math.PI * value);
}
