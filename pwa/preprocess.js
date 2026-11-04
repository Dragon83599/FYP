/**
 * The browser half of the Ma et al. (2023) pre-processing chain.
 *
 * It must produce the *same* 2,000-point tensor as `tools/ai/repro_ma2023.py`:
 *
 *   8 kHz PCM  ->  resample to 1 kHz  ->  crop/pad to 2,000  ->  min-max [-1, 1]
 *
 * Every step mirrors the Python function of the same name, and the resampler
 * reuses scipy's own FIR taps (pwa/filters/resample-poly.js, generated and
 * verified by tools/ai/make_pwa_filters.py).  Without that, the "<1e-3 on the
 * same wav" acceptance check would be measuring the resampler, not the model.
 *
 * Pure functions, no DOM, so the Node check (tools/ai/tfjs-check/check.mjs)
 * exercises exactly the code the page runs.
 */

import { H_PADDED, N_PRE_REMOVE, RESAMPLE_DOWN } from "./filters/resample-poly.js";

export const FS_ORIG = 8000;
export const FS_TARGET = 1000;
export const N_SAMPLES = 2000;
export const CLASS_ORDER = ["AS", "MS", "MR", "MVP", "N"];

/**
 * scipy.signal.resample_poly(x, 1, 8): full convolution with the padded FIR,
 * decimate by 8, then drop the leading `N_PRE_REMOVE` samples.  Matches scipy
 * to ~1e-15 in float64 (see tools/ai/make_pwa_filters.py).
 */
export function resamplePoly1to8(x) {
  const nIn = x.length;
  const nOut = Math.floor(nIn / RESAMPLE_DOWN) + (nIn % RESAMPLE_DOWN ? 1 : 0);
  const h = H_PADDED;
  const fullLength = nIn + h.length - 1;

  // y_full[i] = sum_k x[i - k] * h[k], with x zero outside [0, nIn)
  const y = new Float64Array(fullLength);
  for (let k = 0; k < h.length; k += 1) {
    const hk = h[k];
    if (hk === 0) continue;
    const start = Math.max(0, k - (nIn - 1));
    const end = Math.min(fullLength - 1, k + nIn - 1);
    for (let i = start; i <= end; i += 1) {
      const xi = i - k;
      if (xi >= 0 && xi < nIn) y[i] += x[xi] * hk;
    }
  }

  const out = new Float64Array(nOut);
  for (let j = 0; j < nOut; j += 1) {
    // scipy decimates first (y[::down]) and then drops the first n_pre_remove
    // *decimated* samples, so the full-rate index is (N_PRE_REMOVE + j) * down.
    out[j] = y[(N_PRE_REMOVE + j) * RESAMPLE_DOWN];
  }
  return out;
}

/** Crop from the start, or zero-pad at the tail, to exactly N_SAMPLES. */
export function cropOrPad(x) {
  if (x.length === N_SAMPLES) {
    return { samples: Float64Array.from(x), cropped: false, padded: false };
  }
  const out = new Float64Array(N_SAMPLES);
  if (x.length > N_SAMPLES) {
    out.set(x.subarray(0, N_SAMPLES));
    return { samples: out, cropped: true, padded: false };
  }
  out.set(x);
  return { samples: out, cropped: false, padded: true };
}

/** Min-max to [-1, 1]; a constant clip becomes zeros, like the Python side. */
export function minMaxScale(x) {
  let lo = Infinity;
  let hi = -Infinity;
  for (let i = 0; i < x.length; i += 1) {
    if (x[i] < lo) lo = x[i];
    if (x[i] > hi) hi = x[i];
  }
  const out = new Float64Array(x.length);
  if (!(hi > lo)) return out;
  const span = hi - lo;
  for (let i = 0; i < x.length; i += 1) {
    out[i] = ((x[i] - lo) / span) * 2 - 1;
  }
  return out;
}

/**
 * Full pipeline from an 8 kHz mono float signal (as AudioBuffer.getChannelData
 * returns it) to the float32 tensor Conv1D expects.
 */
export function preprocess(samples8k) {
  if (samples8k.length === 0) throw new Error("empty signal");
  const down = resamplePoly1to8(samples8k);
  const { samples, cropped, padded } = cropOrPad(down);
  const scaled = minMaxScale(samples);
  const modelInput = new Float32Array(N_SAMPLES);
  for (let i = 0; i < N_SAMPLES; i += 1) modelInput[i] = scaled[i];
  return {
    input: modelInput,
    stats: {
      inputSamples: samples8k.length,
      afterResample: down.length,
      cropped,
      padded,
    },
  };
}

/** Highest-scoring class index, first wins on ties (same rule as argmax()). */
export function argmax(values) {
  let best = 0;
  for (let i = 1; i < values.length; i += 1) {
    if (values[i] > values[best]) best = i;
  }
  return best;
}
