/**
 * End-to-end check: raw 8 kHz wav -> JS pre-processing -> tfjs -> probabilities,
 * compared against the Keras reference for the same file.
 *
 * This is the check that actually matters for the acceptance criterion
 * ("同一 wav 在 Python 与浏览器各跑一遍，概率最大绝对差 <1e-3"), because it runs
 * the *browser's* pre-processing code (pwa/preprocess.js) rather than replaying
 * the tensor Python produced.  It reports both levels:
 *
 *   input      — max |JS pre-processed sample - Python pre-processed sample|
 *   probability— max |tfjs probability - Keras probability|
 *
 * Usage:
 *   node tools/ai/tfjs-check/e2e.mjs \
 *     --model pwa/model/model.json \
 *     --reference pwa/model/reference.json \
 *     --wav pwa/model/New_AS_001.wav
 */

import fs from "node:fs";
import path from "node:path";

import { readPcm16Wav } from "../../audio-sim/lib/wav.mjs";
import { preprocess, CLASS_ORDER } from "../../../pwa/preprocess.js";
import { loadModel } from "./load-model.mjs";

function parseArgs(argv) {
  const args = { tolerance: 1e-3, json: null };
  for (let i = 0; i < argv.length; i += 1) {
    const key = argv[i];
    const value = argv[i + 1];
    if (key === "--model") { args.model = value; i += 1; }
    else if (key === "--reference") { args.reference = value; i += 1; }
    else if (key === "--wav") { args.wav = value; i += 1; }
    else if (key === "--tolerance") { args.tolerance = Number(value); i += 1; }
    else if (key === "--json") { args.json = value; i += 1; }
    else throw new Error(`unknown argument ${key}`);
  }
  if (!args.model || !args.reference || !args.wav) {
    throw new Error("--model, --reference and --wav are required");
  }
  return args;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const reference = JSON.parse(fs.readFileSync(path.resolve(args.reference), "utf8"));
  const wav = readPcm16Wav(path.resolve(args.wav));
  if (wav.sampleRate !== 8000) {
    throw new Error(`expected an 8 kHz wav, got ${wav.sampleRate} Hz`);
  }

  // Same conversion the browser's AudioBuffer gives: int16 / 32768.
  const float = new Float64Array(wav.samples.length);
  for (let i = 0; i < wav.samples.length; i += 1) float[i] = wav.samples[i] / 32768;

  const { input, stats } = preprocess(float);
  const pythonInput = reference.input;
  let inputMaxDiff = 0;
  for (let i = 0; i < pythonInput.length; i += 1) {
    inputMaxDiff = Math.max(inputMaxDiff, Math.abs(input[i] - pythonInput[i]));
  }

  const { predict, loadMs, tf } = await loadModel(args.model);
  const started = performance.now();
  const probabilities = await predict(input);
  const predictMs = performance.now() - started;

  const keras = reference.keras_probabilities;
  const diffs = probabilities.map((p, i) => Math.abs(p - keras[i]));
  const maxAbsDiff = Math.max(...diffs);

  const result = {
    wav: path.resolve(args.wav),
    model: path.resolve(args.model),
    recording_id: reference.recording_id,
    backend: tf.getBackend(),
    class_order: CLASS_ORDER,
    stats,
    input_max_abs_diff: inputMaxDiff,
    max_abs_diff: maxAbsDiff,
    tolerance: args.tolerance,
    per_class_abs_diff: Object.fromEntries(CLASS_ORDER.map((c, i) => [c, diffs[i]])),
    keras_probabilities: Object.fromEntries(CLASS_ORDER.map((c, i) => [c, keras[i]])),
    tfjs_probabilities: Object.fromEntries(CLASS_ORDER.map((c, i) => [c, probabilities[i]])),
    load_ms: Number(loadMs.toFixed(2)),
    predict_ms: Number(predictMs.toFixed(3)),
    passed: maxAbsDiff < args.tolerance,
  };

  console.log(JSON.stringify(result, null, 2));
  if (args.json) {
    fs.mkdirSync(path.dirname(path.resolve(args.json)), { recursive: true });
    fs.writeFileSync(path.resolve(args.json), `${JSON.stringify(result, null, 2)}\n`);
  }
  if (!result.passed) {
    console.error(`FAIL: max |tfjs - keras| = ${maxAbsDiff.toExponential(3)} >= ${args.tolerance}`);
    process.exitCode = 1;
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
