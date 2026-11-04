/**
 * Load an exported layers-model with @tensorflow/tfjs from the local files.
 *
 * The browser fetches `model.json` and the shard over HTTP; Node has neither a
 * fetch-based IO handler nor tfjs-node installed, so the same artefacts are read
 * from disk and handed to the *same* `tf.loadLayersModel` through
 * `tf.io.fromMemory`.  The loader, the topology and the weight blobs are
 * identical to what the page uses — only the transport differs.
 */

import fs from "node:fs";
import path from "node:path";

export async function loadModel(modelJsonPath) {
  const tf = await import("@tensorflow/tfjs");
  await tf.setBackend("cpu");
  await tf.ready();

  const resolved = path.resolve(modelJsonPath);
  const modelJson = JSON.parse(fs.readFileSync(resolved, "utf8"));
  const manifest = modelJson.weightsManifest[0];
  const binPath = path.join(path.dirname(resolved), manifest.paths[0]);
  const bin = fs.readFileSync(binPath);
  const weightData = bin.buffer.slice(bin.byteOffset, bin.byteOffset + bin.byteLength);

  const started = performance.now();
  const model = await tf.loadLayersModel(
    tf.io.fromMemory({
      modelTopology: modelJson.modelTopology,
      weightSpecs: manifest.weights,
      weightData,
      format: modelJson.format,
      generatedBy: modelJson.generatedBy,
      convertedBy: modelJson.convertedBy,
      userDefinedMetadata: modelJson.userDefinedMetadata,
    }),
  );
  const loadMs = performance.now() - started;

  const predict = async (input) => {
    const tensor = tf.tensor3d(input, [1, input.length, 1], "float32");
    const output = model.predict(tensor);
    const values = Array.from(await output.data());
    tensor.dispose();
    output.dispose();
    return values;
  };

  return { tf, model, loadMs, predict };
}
