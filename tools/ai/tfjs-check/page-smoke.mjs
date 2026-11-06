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
    this._innerHTML = "";
    this.disabled = false;
    this.style = {};
    this.listeners = {};
    this.value = "";
    this.files = [];
  }
  // In a browser, assigning innerHTML = "" drops the children; mirror that so a
  // re-render does not look like it appended a second table.
  get innerHTML() { return this._innerHTML; }
  set innerHTML(value) {
    this._innerHTML = value;
    if (value === "") this.children = [];
  }
  addEventListener(type, handler) { this.listeners[type] = handler; }
  appendChild(child) { this.children.push(child); return child; }
  fire(type, event = {}) { return this.listeners[type]?.(event); }
}

// A canvas stub that records what the drawing code does, so the waveform path
// is exercised instead of skipped: we assert it draws a polyline and a plausible
// number of peak markers for a three-beat clip.
const canvasCalls = { moveTo: 0, lineTo: 0, arc: 0 };

class StubCanvas extends StubElement {
  constructor(id) {
    super(id);
    this.width = 640;
    this.height = 180;
    this.clientWidth = 640;
    this.clientHeight = 180;
    this.style = {};
  }
  getContext() {
    return {
      setTransform() {},
      clearRect() {},
      beginPath() {},
      moveTo() { canvasCalls.moveTo += 1; },
      lineTo() { canvasCalls.lineTo += 1; },
      stroke() {},
      arc() { canvasCalls.arc += 1; },
      fill() {},
      strokeStyle: "", lineWidth: 1, fillStyle: "",
    };
  }
}

const elements = new Map();
globalThis.document = {
  getElementById(id) {
    if (!elements.has(id)) {
      elements.set(id, id === "waveform" ? new StubCanvas(id) : new StubElement(id));
    }
    return elements.get(id);
  },
  createElement() { return new StubElement("created"); },
};
globalThis.devicePixelRatio = 2;

globalThis.performance = globalThis.performance ?? { now: () => Date.now() };
// app.js touches a couple of `window` globals (the "did the model load" flag the
// inline diagnostic in index.html reads); a browser has window === globalThis.
globalThis.window = globalThis;
// diag.js reports the page address; Node has no location object.
globalThis.location = globalThis.location ?? {
  href: "http://localhost:8080/index.html",
};

globalThis.fetch = async (url) => {
  const relative = String(url).replace(/^https?:\/\/[^/]+\//, "").replace(/^\.\//, "");
  const file = path.join(PWA, relative);
  if (!fs.existsSync(file)) return { ok: false, status: 404, json: async () => { throw new Error("404"); } };
  const buffer = fs.readFileSync(file);
  const types = {
    ".html": "text/html",
    ".js": "text/javascript",
    ".json": "application/json",
    ".bin": "application/octet-stream",
    ".wav": "audio/wav",
    ".png": "image/png",
    ".md": "text/plain",
  };
  const contentType = types[path.extname(file)] ?? "application/octet-stream";
  return {
    ok: true,
    status: 200,
    headers: { get: (name) => (String(name).toLowerCase() === "content-type" ? contentType : null) },
    arrayBuffer: async () => buffer.buffer.slice(buffer.byteOffset, buffer.byteOffset + buffer.byteLength),
    json: async () => JSON.parse(buffer.toString("utf8")),
  };
};

// A fake Web Bluetooth, installed before app.js loads so the page takes the
// "supported" branch and the live path can be exercised.
const { encodeAudioToLinkPackets } = await import(
  "../../audio-sim/lib/pipeline.mjs"
);
const { LKA1_AUDIO_DATA_UUID, LKA1_CONTROL_UUID, LKA1_STATUS_UUID, LKA1_SERVICE_UUID } =
  await import("../../../pwa/gatt.js");

class FakeTarget {
  constructor() { this.listeners = new Map(); }
  addEventListener(type, handler) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(handler);
  }
  dispatch(type, event) { for (const h of this.listeners.get(type) ?? []) h(event); }
}

class FakeChar extends FakeTarget {
  constructor(uuid, writable = false) {
    super();
    this.uuid = uuid;
    this.value = null;
    this.properties = writable ? { write: true } : {};
    this.writes = [];
  }
  async startNotifications() { return this; }
  async writeValueWithResponse(bytes) { this.writes.push(Uint8Array.from(bytes)); }
  notify(bytes) {
    const copy = new Uint8Array(bytes);
    this.value = new DataView(copy.buffer, copy.byteOffset, copy.byteLength);
    this.dispatch("characteristicvaluechanged", { target: this });
  }
}

const fakeChars = {
  audio: new FakeChar(LKA1_AUDIO_DATA_UUID),
  control: new FakeChar(LKA1_CONTROL_UUID, true),
  status: new FakeChar(LKA1_STATUS_UUID),
};
const fakeDevice = new FakeTarget();
fakeDevice.name = "probe-sim";
fakeDevice.gatt = {
  connected: false,
  async connect() {
    this.connected = true;
    return {
      async getPrimaryService() {
        return {
          async getCharacteristic(uuid) {
            if (uuid === LKA1_AUDIO_DATA_UUID) return fakeChars.audio;
            if (uuid === LKA1_CONTROL_UUID) return fakeChars.control;
            if (uuid === LKA1_STATUS_UUID) return fakeChars.status;
            throw new Error(`unexpected ${uuid}`);
          },
        };
      },
    };
  },
  disconnect() { this.connected = false; fakeDevice.dispatch("gattserverdisconnected", {}); },
};
const fakeBluetooth = { async requestDevice() { return fakeDevice; } };
Object.defineProperty(globalThis, "navigator", {
  value: { bluetooth: fakeBluetooth, userAgent: "Node.js/24" },
  configurable: true,
  writable: true,
});

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

// One-tap sample: exercises the waveform drawing on a real clip.
const sampleButtons = elements.get("samples")?.children ?? [];
if (sampleButtons.length === 0) {
  console.error("no sample buttons were rendered");
  process.exitCode = 1;
} else {
  await sampleButtons[0].listeners.click();
  await new Promise((resolve) => setTimeout(resolve, 1500));
  const note = elements.get("waveform-note")?.textContent ?? "";
  const result = elements.get("samples-result")?.textContent ?? "";
  console.log(JSON.stringify({
    samples_rendered: sampleButtons.length,
    sample_result: result,
    waveform_note: note,
    canvas_calls: canvasCalls,
  }, null, 2));
  if (canvasCalls.lineTo < 100) {
    console.error(`waveform drew only ${canvasCalls.lineTo} segments`);
    process.exitCode = 1;
  }
  // 2 s at 40-200 bpm is 1.3-6.7 beats; the 250 ms refractory caps it at 8.
  if (canvasCalls.arc < 2 || canvasCalls.arc > 8) {
    console.error(`expected a handful of peak markers, drew ${canvasCalls.arc}`);
    process.exitCode = 1;
  }
  if (!/2,000 点/.test(note)) {
    console.error("waveform caption does not describe the 2,000-point input");
    process.exitCode = 1;
  }
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

// Live capture: connect to the fake probe, start, push two seconds of audio and
// require the page to classify it and to report link statistics.
const bleConnect = elements.get("ble-connect");
const bleStart = elements.get("ble-start");
if (!bleConnect?.listeners?.click || !bleStart?.listeners?.click) {
  console.error("the live-capture buttons are not wired");
  process.exitCode = 1;
} else {
  bleConnect.disabled = false;
  await bleConnect.fire("click");
  await new Promise((resolve) => setTimeout(resolve, 300));
  if (!fakeChars.audio.listeners.has("characteristicvaluechanged")) {
    console.error("audio notifications were not subscribed");
    process.exitCode = 1;
  }
  await bleStart.fire("click");

  // 2 s of 8 kHz audio = 100 frames of 160 samples
  const samples = new Int16Array(16000);
  for (let i = 0; i < samples.length; i += 1) {
    samples[i] = Math.round(8000 * Math.sin((2 * Math.PI * 60 * i) / 8000));
  }
  const { packets } = encodeAudioToLinkPackets(samples, { sessionId: 3, maxFragmentPayload: 222 });
  for (const packet of packets) fakeChars.audio.notify(packet);
  await new Promise((resolve) => setTimeout(resolve, 500));

  const stats = elements.get("ble-stats")?.textContent ?? "";
  const probRows = elements.get("probs")?.children.length ?? 0;
  console.log(JSON.stringify({
    ble_state: elements.get("ble-state")?.textContent ?? "",
    ble_stats: stats,
    probability_rows: probRows,
    control_writes: fakeChars.control.writes.map((w) => Array.from(w)),
  }, null, 2));
  if (!/帧 100/.test(stats)) {
    console.error(`expected 100 frames in the stats line, got: ${stats}`);
    process.exitCode = 1;
  }
  if (probRows !== 5) {
    console.error(`expected five probability rows after live capture, got ${probRows}`);
    process.exitCode = 1;
  }
  if (fakeChars.control.writes.length !== 1 || fakeChars.control.writes[0][0] !== 1) {
    console.error("START was not written exactly once");
    process.exitCode = 1;
  }
}

// Diagnostic: it must run even when app.js is broken, so exercise it directly.
await import(pathToFileURL(path.join(PWA, "diag.js")).href);
if (typeof globalThis.__pwaDiag !== "function") {
  console.error("diag.js did not define __pwaDiag");
  process.exitCode = 1;
} else {
  const report = await globalThis.__pwaDiag();
  console.log(JSON.stringify({ diag: report.split("\n").slice(0, 8) }, null, 2));
  if (!/OK \.\/model\/model\.json/.test(report)) {
    console.error("diagnostic did not confirm model.json is reachable");
    process.exitCode = 1;
  }
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
