/**
 * Model-only consistency check: replay the exact tensor Python produced and
 * compare the five-class probabilities against Keras.
 *
 * This isolates the export from the pre-processing.  `e2e.mjs` runs the other
 * half (the same wav through the browser's pre-processing); both must pass, see
 * pwa/README.md for why checking only one of them hides real bugs.
 *
 *   node tools/ai/tfjs-check/check.mjs \
 *     --model ../../../pwa/model/model.json \
 *     --reference ../../../pwa/model/reference.json
 */

import fs from "node:fs";
import path from "node:path";

import { loadModel } from "./load-model.mjs";

function parseArgs(argv) {
  const args = { tolerance: 1e-3, json: null };
  for (let i = 0; i < argv.length; i += 1) {
    const key = argv[i];
    const value = argv[i + 1];
    if (key === "--model") { args.model = value; i += 1; }
    else if (key === "--reference") { args.reference = value; i += 1; }
    else if (key === "--tolerance") { args.tolerance = Number(value); i += 1; }
    else if (key === "--json") { args.json = value; i += 1; }
    else throw new Error(`unknown argument ${key}`);
  }
  if (!args.model || !args.reference) throw new Error("--model and --reference are required");
  return args;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const reference = JSON.parse(fs.readFileSync(path.resolve(args.reference), "utf8"));
  if (!Array.isArray(reference.input) || reference.input.length !== 2000) {
    throw new Error(`reference input must be 2,000 samples, got ${reference.input?.length}`);
  }

  const { predict, loadMs, tf } = await loadModel(args.model);
  const tensor = Float32Array.from(reference.input);
  const started = performance.now();
  const probabilities = await predict(tensor);
  const predictMs = performance.now() - started;

  const keras = reference.keras_probabilities;
  const diffs = probabilities.map((value, index) => Math.abs(value - keras[index]));
  const maxAbsDiff = Math.max(...diffs);
  const argmax = (values) => values.indexOf(Math.max(...values));
  const classOrder = reference.class_order;

  const result = {
    model: path.resolve(args.model),
    reference: path.resolve(args.reference),
    recording_id: reference.recording_id,
    class_order: classOrder,
    backend: tf.getBackend(),
    tfjs: tf.version.tfjs,
    tolerance: args.tolerance,
    max_abs_diff: maxAbsDiff,
    per_class_abs_diff: Object.fromEntries(classOrder.map((c, i) => [c, diffs[i]])),
    keras_probabilities: Object.fromEntries(classOrder.map((c, i) => [c, keras[i]])),
    tfjs_probabilities: Object.fromEntries(classOrder.map((c, i) => [c, probabilities[i]])),
    keras_prediction: classOrder[argmax(keras)],
    tfjs_prediction: classOrder[argmax(probabilities)],
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
