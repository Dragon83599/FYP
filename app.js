/**
 * Page logic for the PWA demo.
 *
 * Two things are demonstrated:
 *   1. a wav file can be classified in the browser, with all five class
 *      probabilities shown, and
 *   2. the browser's numbers agree with Keras on the same file to <1e-3.
 *
 * The model is `pwa/model/model.json` — the 20,193-parameter Ma et al. (2023)
 * network exported by `tools/ai/export_tfjs.py`.  Pre-processing lives in
 * `preprocess.js` and is byte-identical in intent to `tools/ai/repro_ma2023.py`.
 */

import { CLASS_ORDER, argmax, preprocess } from "./preprocess.js";
import { parseWav } from "./wav.js";

const TOLERANCE = 1e-3;          // W2 acceptance criterion
const LABEL_ZH = {
  AS: "主动脉瓣狭窄",
  MS: "二尖瓣狭窄",
  MR: "二尖瓣反流",
  MVP: "二尖瓣脱垂",
  N: "正常",
};

const els = {
  file: document.getElementById("file"),
  selfCheck: document.getElementById("self-check"),
  status: document.getElementById("status"),
  probs: document.getElementById("probs"),
  verdict: document.getElementById("verdict"),
  consistency: document.getElementById("consistency"),
  detail: document.getElementById("detail"),
  paramCount: document.getElementById("param-count"),
};

let model = null;

function setStatus(text, isError = false) {
  els.status.textContent = text;
  els.status.style.color = isError ? "var(--bad)" : "";
}

async function loadModel() {
  const started = performance.now();
  model = await tf.loadLayersModel("./model/model.json");
  // Warm up once so the first user-visible inference is not the graph build.
  const warm = tf.zeros([1, 2000, 1]);
  model.predict(warm).dispose();
  warm.dispose();
  const ms = performance.now() - started;
  const params = model.countParams();
  els.paramCount.textContent = params.toLocaleString();
  if (params !== 20193) {
    throw new Error(`模型参数是 ${params}，应为 20,193 —— 导出错了`);
  }
  return ms;
}

function predict(input) {
  const tensor = tf.tensor3d(input, [1, input.length, 1], "float32");
  const output = model.predict(tensor);
  const values = Array.from(output.dataSync());
  tensor.dispose();
  output.dispose();
  return values;
}

function renderProbabilities(probabilities) {
  const top = argmax(probabilities);
  els.probs.innerHTML = "";
  CLASS_ORDER.forEach((name, index) => {
    const row = document.createElement("tr");
    const pct = (probabilities[index] * 100).toFixed(2);
    row.innerHTML = `
      <td>${name} <span class="muted">${LABEL_ZH[name] ?? ""}</span></td>
      <td class="num">${pct}%</td>
      <td><div class="bar"><span style="width:${Math.min(100, probabilities[index] * 100)}%"></span></div></td>`;
    if (index === top) row.style.fontWeight = "600";
    els.probs.appendChild(row);
  });
  els.verdict.textContent =
    `判读：${CLASS_ORDER[top]}（${LABEL_ZH[CLASS_ORDER[top]] ?? ""}），` +
    `概率 ${(probabilities[top] * 100).toFixed(2)}%`;
}

function renderConsistency(rows) {
  els.consistency.innerHTML = "";
  for (const row of rows) {
    const tr = document.createElement("tr");
    const cls = row.passed ? "ok" : "bad";
    tr.innerHTML = `
      <td>${row.name}</td>
      <td class="num">${row.diff.toExponential(2)}</td>
      <td class="num">&lt; ${TOLERANCE}</td>
      <td class="${cls}">${row.passed ? "通过" : "不通过"}</td>`;
    els.consistency.appendChild(tr);
  }
}

async function classifyArrayBuffer(buffer, label) {
  const wav = parseWav(buffer);
  if (wav.sampleRate !== 8000) {
    throw new Error(
      `采样率是 ${wav.sampleRate} Hz，本演示只接受 8 kHz（本项目采集链固定 8 kHz，见 ADR-002）`,
    );
  }
  const startedPre = performance.now();
  const { input, stats } = preprocess(wav.samples);
  const preMs = performance.now() - startedPre;
  const started = performance.now();
  const probabilities = predict(input);
  const inferMs = performance.now() - started;
  renderProbabilities(probabilities);
  setStatus(
    `${label}：${stats.inputSamples.toLocaleString()} 点 @8 kHz → 1 kHz → ` +
    `${stats.cropped ? "裁到" : stats.padded ? "补零到" : "正好"} 2,000 点 | ` +
    `预处理 ${preMs.toFixed(1)} ms · 推理 ${inferMs.toFixed(1)} ms`,
  );
  return { probabilities, input, wav, stats };
}

async function onFile(event) {
  const file = event.target.files?.[0];
  if (!file) return;
  try {
    setStatus(`读取 ${file.name}…`);
    const buffer = await file.arrayBuffer();
    await classifyArrayBuffer(buffer, file.name);
  } catch (error) {
    setStatus(String(error.message ?? error), true);
  }
}

async function runSelfCheck() {
  els.selfCheck.disabled = true;
  els.detail.textContent = "自检中…";
  try {
    const reference = await (await fetch("./model/reference.json")).json();
    const wavBuffer = await (await fetch("./model/New_AS_001.wav")).arrayBuffer();

    // (a) model only: replay the exact tensor Python produced
    const tensorInput = Float32Array.from(reference.input);
    const tensorProbs = predict(tensorInput);
    const tensorDiff = Math.max(
      ...tensorProbs.map((value, index) => Math.abs(value - reference.keras_probabilities[index])),
    );

    // (b) end to end: the same wav through the browser's own pre-processing
    const { probabilities, input } = await classifyArrayBuffer(wavBuffer, "New_AS_001.wav");
    const e2eDiff = Math.max(
      ...probabilities.map((value, index) => Math.abs(value - reference.keras_probabilities[index])),
    );
    const inputDiff = Math.max(
      ...Array.from(input).map((value, index) => Math.abs(value - reference.input[index])),
    );

    const rows = [
      { name: "模型（同一张量）", diff: tensorDiff, passed: tensorDiff < TOLERANCE },
      { name: "端到端（同一 wav）", diff: e2eDiff, passed: e2eDiff < TOLERANCE },
    ];
    renderConsistency(rows);

    els.detail.textContent = JSON.stringify(
      {
        参考录音: reference.recording_id,
        参考标签: reference.label,
        类别顺序: CLASS_ORDER,
        Keras概率: Object.fromEntries(CLASS_ORDER.map((c, i) => [c, reference.keras_probabilities[i]])),
        浏览器概率: Object.fromEntries(CLASS_ORDER.map((c, i) => [c, probabilities[i]])),
        预处理样本最大差: inputDiff,
        推理时延_ms: Number((performance.now()).toFixed(1)),
        后端: tf.getBackend(),
      },
      null,
      2,
    );

    const allPassed = rows.every((row) => row.passed);
    setStatus(
      allPassed
        ? `一致性自检通过：端到端最大差 ${e2eDiff.toExponential(2)} < ${TOLERANCE}`
        : `一致性自检失败：端到端最大差 ${e2eDiff.toExponential(2)} ≥ ${TOLERANCE}`,
      !allPassed,
    );
  } catch (error) {
    setStatus(`自检失败：${error.message ?? error}`, true);
    els.detail.textContent = String(error.stack ?? error);
  } finally {
    els.selfCheck.disabled = false;
  }
}

async function main() {
  try {
    const loadMs = await loadModel();
    setStatus(
      `模型已加载（${model.countParams().toLocaleString()} 参数，` +
      `后端 ${tf.getBackend()}，${loadMs.toFixed(0)} ms）。选一个 8 kHz wav，或直接跑自检。`,
    );
  } catch (error) {
    setStatus(`模型加载失败：${error.message ?? error}`, true);
    return;
  }
  els.file.addEventListener("change", onFile);
  els.selfCheck.addEventListener("click", runSelfCheck);
}

main();
