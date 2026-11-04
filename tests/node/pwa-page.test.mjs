/**
 * Static checks for the PWA demo (no browser, no tfjs needed).
 *
 * These catch the failure modes that a numeric check cannot: an element id
 * renamed in `index.html` but not in `app.js`, a module path that 404s, or a
 * model export whose declared shapes no longer add up to 20,193 parameters.
 * The numeric behaviour is covered by `tools/ai/tfjs-check/*`.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const PWA = path.join(ROOT, "pwa");

function read(file) {
  return readFileSync(path.join(PWA, file), "utf8");
}

test("every element id used by app.js exists in index.html", () => {
  const html = read("index.html");
  const htmlIds = new Set([...html.matchAll(/id="([^"]+)"/g)].map((m) => m[1]));
  const js = read("app.js");
  const used = [...js.matchAll(/getElementById\("([^"]+)"\)/g)].map((m) => m[1]);
  assert.ok(used.length > 0, "app.js should look up at least one element");
  for (const id of used) {
    assert.ok(htmlIds.has(id), `app.js uses #${id} but index.html has no such id`);
  }
});

test("the page references only files that exist", () => {
  const html = read("index.html");
  const refs = [
    ...html.matchAll(/(?:src|href)="\.\/([^"]+)"/g),
  ].map((m) => m[1]);
  assert.ok(refs.includes("app.js"));
  for (const ref of refs) {
    assert.ok(existsSync(path.join(PWA, ref)), `index.html references ./${ref}, which is missing`);
  }
});

test("app.js imports only files that exist", () => {
  const js = read("app.js");
  const imports = [...js.matchAll(/from\s+"\.\/([^"]+)"/g)].map((m) => m[1]);
  assert.ok(imports.length >= 2, "app.js should import the pre-processing and wav modules");
  for (const ref of imports) {
    assert.ok(existsSync(path.join(PWA, ref)), `app.js imports ./${ref}, which is missing`);
  }
});

test("preprocess.js ships the frozen class order and 2,000-sample contract", () => {
  const js = read("preprocess.js");
  assert.match(js, /CLASS_ORDER\s*=\s*\["AS",\s*"MS",\s*"MR",\s*"MVP",\s*"N"\]/);
  assert.match(js, /N_SAMPLES\s*=\s*2000/);
  assert.match(js, /FS_TARGET\s*=\s*1000/);
});

test("the exported model declares 20,193 parameters and a matching shard", () => {
  const modelJson = JSON.parse(read("model/model.json"));
  assert.equal(modelJson.format, "layers-model");
  const weights = modelJson.weightsManifest.flatMap((group) => group.weights);
  const params = weights.reduce(
    (sum, spec) => sum * 1 + spec.shape.reduce((a, b) => a * b, 1),
    0,
  );
  assert.equal(params, 20193, `exported weights add up to ${params}, expected 20,193`);

  const shard = modelJson.weightsManifest[0].paths[0];
  const bytes = readFileSync(path.join(PWA, "model", shard)).length;
  assert.equal(bytes, params * 4, "float32 shard must be exactly parameters x 4 bytes");

  const classes = modelJson.modelTopology.config.layers.at(-1).config.units;
  assert.equal(classes, 5, "the output layer must have five units");
  assert.equal(
    modelJson.modelTopology.config.layers.at(-1).config.activation,
    "softmax",
  );
});

test("the reference clip is the one the export recorded", () => {
  const reference = JSON.parse(read("model/reference.json"));
  assert.equal(reference.class_order.join(","), "AS,MS,MR,MVP,N");
  assert.equal(reference.input.length, 2000);
  assert.equal(reference.keras_probabilities.length, 5);
  const sum = reference.keras_probabilities.reduce((a, b) => a + b, 0);
  assert.ok(Math.abs(sum - 1) < 1e-3, `probabilities sum to ${sum}`);
  assert.ok(
    existsSync(path.join(PWA, "model", `${reference.recording_id}.wav`)),
    `the reference wav for ${reference.recording_id} is not next to model.json`,
  );
});

test("the manifest makes the page installable", () => {
  const manifest = JSON.parse(read("manifest.json"));
  assert.ok(manifest.name && manifest.short_name);
  assert.equal(manifest.display, "standalone");
  assert.equal(manifest.start_url, "./index.html");
  assert.equal(manifest.scope, "./");
  assert.ok(Array.isArray(manifest.icons) && manifest.icons.length >= 2);
  for (const icon of manifest.icons) {
    assert.ok(existsSync(path.join(PWA, icon.src)), `manifest icon ${icon.src} is missing`);
  }
  const html = read("index.html");
  assert.match(html, /rel="manifest"/, "index.html must link the manifest");
});

test("the service worker precaches every file the page fetches", () => {
  // A file the page requests but the worker does not cache is exactly the bug
  // that shows up as "works at the demo, blank screen when offline".
  const sw = read("sw.js");
  const list = sw.match(/const PRECACHE = \[([\s\S]*?)\];/);
  assert.ok(list, "sw.js must declare PRECACHE");
  const cached = new Set([...list[1].matchAll(/"\.\/([^"]*)"/g)].map((m) => m[1]));

  const sources = ["index.html", "app.js", "diag.js", "pwa.js", "preprocess.js", "wav.js"]
    .map((file) => read(file))
    .join("\n");
  const requested = new Set(
    [...sources.matchAll(/(?:fetch\(|src=|href=|import\(|from )"\.\/([^"]+)"/g)].map((m) => m[1]),
  );
  for (const file of requested) {
    assert.ok(cached.has(file), `sw.js does not precache ./${file}, which the page fetches`);
  }
  assert.ok(cached.has("model/group1-shard1of1.bin"), "the weights must be cached");
  assert.ok(cached.has("vendor/tf.min.js"), "the TF.js bundle must be cached");

  // The sample clips are listed in samples/index.json rather than in the source,
  // so they need their own check: a clip that is not cached makes the one-tap
  // demo fail only when the phone is offline.
  const samples = JSON.parse(read("samples/index.json"));
  assert.ok(samples.items.length >= 5, "expected one-tap samples for the demo");
  for (const item of samples.items) {
    assert.ok(existsSync(path.join(PWA, "samples", item.file)), `samples/${item.file} is missing`);
    assert.ok(cached.has(`samples/${item.file}`), `sw.js does not precache samples/${item.file}`);
  }
});

test("the icons are real PNGs at the advertised sizes", () => {
  for (const size of [192, 512]) {
    const file = path.join(PWA, "icons", `icon-${size}.png`);
    assert.ok(existsSync(file), `${file} is missing`);
    const header = readFileSync(file).subarray(0, 24);
    assert.equal(header.subarray(0, 8).toString("hex"), "89504e470d0a1a0a", "not a PNG");
    assert.equal(header.readUInt32BE(16), size, "width mismatch");
    assert.equal(header.readUInt32BE(20), size, "height mismatch");
  }
});
