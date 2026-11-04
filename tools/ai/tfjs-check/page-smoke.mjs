/**
 * Run `pwa/app.js` outside a browser with a minimal DOM shim.
 *
 * The point is to execute the page's *actual* code path — `main()`, model load,
 * file handling, the self-check — so a typo or a wrong element id fails here
 * instead of in front of an examiner.  It is not a rendering test: the shim
 * records text instead of painting it.
 *
 * `fetch` is shimmed to read from `pwa/` on disk, and the global `tf` is the
 * same package the page loads from `vendor/tf.min.js`.
 *
 *   node tools/ai/tfjs-check/page-smoke.mjs
 */

import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

const PWA = path.resolve(path.dirname(new URL(import.meta.url).pathname), "../../../pwa");

class StubElement {
  constructor(id) {
    this.id = id;
    this.children = [];
    this.textContent = "";
    this.innerHTML = "";
    this.disabled = false;
    this.style = {};
    this.listeners = {};
    this.value = "";
    this.files = [];
  }
  addEventListener(type, handler) { this.listeners[type] = handler; }
  appendChild(child) { this.children.push(child); return child; }
  fire(type, event = {}) { return this.listeners[type]?.(event); }
}

const elements = new Map();
globalThis.document = {
  getElementById(id) {
    if (!elements.has(id)) elements.set(id, new StubElement(id));
    return elements.get(id);
  },
  createElement() { return new StubElement("created"); },
};

globalThis.performance = globalThis.performance ?? { now: () => Date.now() };
// app.js touches a couple of `window` globals (the "did the model load" flag the
// inline diagnostic in index.html reads); a browser has window === globalThis.
globalThis.window = globalThis;

globalThis.fetch = async (url) => {
  const relative = String(url).replace(/^https?:\/\/[^/]+\//, "").replace(/^\.\//, "");
  const file = path.join(PWA, relative);
  if (!fs.existsSync(file)) return { ok: false, status: 404, json: async () => { throw new Error("404"); } };
  const buffer = fs.readFileSync(file);
  return {
    ok: true,
    status: 200,
    arrayBuffer: async () => buffer.buffer.slice(buffer.byteOffset, buffer.byteOffset + buffer.byteLength),
    json: async () => JSON.parse(buffer.toString("utf8")),
  };
};

const tf = await import("@tensorflow/tfjs");
await tf.setBackend("cpu");
await tf.ready();
globalThis.tf = tf;

await import(pathToFileURL(path.join(PWA, "app.js")).href);
// main() is async; give it the microtasks it needs.
await new Promise((resolve) => setTimeout(resolve, 3000));

const status = elements.get("status")?.textContent ?? "";
const params = elements.get("param-count")?.textContent ?? "";
const check = elements.get("self-check");
const consistencyRows = elements.get("consistency")?.children.length ?? 0;
const detail = elements.get("detail")?.textContent ?? "";

console.log(JSON.stringify({
  status_after_load: status,
  parameter_count_text: params,
  self_check_button_wired: Boolean(check?.listeners?.click),
  consistency_rows_before_click: consistencyRows,
}, null, 2));

if (!/模型已加载/.test(status)) {
  console.error("page did not reach the loaded state");
  process.exitCode = 1;
}
if (params.replace(/[^0-9]/g, "") !== "20193") {
  console.error(`page shows parameter count ${params}, expected 20,193`);
  process.exitCode = 1;
}
if (!check?.listeners?.click) {
  console.error("the self-check button has no handler");
  process.exitCode = 1;
}

// Now exercise the self-check itself (loads the reference wav and compares).
check.disabled = false;
await check.fire("click");
await new Promise((resolve) => setTimeout(resolve, 4000));

const finalStatus = elements.get("status")?.textContent ?? "";
const rows = elements.get("consistency")?.children.length ?? 0;
console.log(JSON.stringify({
  status_after_self_check: finalStatus,
  consistency_rows: rows,
  detail: detail.slice(0, 400),
}, null, 2));

if (!/一致性自检通过/.test(finalStatus)) {
  console.error("self-check did not pass in the shim");
  process.exitCode = 1;
}
if (rows !== 2) {
  console.error(`expected 2 consistency rows, got ${rows}`);
  process.exitCode = 1;
}

// Latency check: the page has to be able to produce the W2 acceptance numbers.
const bench = elements.get("benchmark");
if (!bench?.listeners?.click) {
  console.error("the latency button has no handler");
  process.exitCode = 1;
} else {
  bench.disabled = false;
  await bench.fire("click");
  await new Promise((resolve) => setTimeout(resolve, 3000));
  const latency = elements.get("latency")?.textContent ?? "";
  console.log(JSON.stringify({ latency_text: latency }, null, 2));
  if (!/中位 .* ms/.test(latency)) {
    console.error("the latency run produced no median");
    process.exitCode = 1;
  }
}
