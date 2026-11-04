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
  benchmark: document.getElementById("benchmark"),
  copy: document.getElementById("copy"),
  latency: document.getElementById("latency"),
  status: document.getElementById("status"),
  probs: document.getElementById("probs"),
  verdict: document.getElementById("verdict"),
  consistency: document.getElementById("consistency"),
  detail: document.getElementById("detail"),
  paramCount: document.getElementById("param-count"),
};

let model = null;
// The last tensor that went through the model, kept so the latency check can
// time the same input the self-check measured.
let lastInput = null;
// Everything the page knows after a self-check, so "复制结果" can hand the
// numbers to whoever asked for the browser evidence.
let lastResult = null;

function setStatus(text, isError = false) {
  els.status.textContent = text;
  els.status.style.color = isError ? "var(--bad)" : "";
}

/** Reject after `ms` so a hung backend cannot freeze the page forever. */
function withTimeout(promise, ms, label) {
  return Promise.race([
    promise,
    new Promise((_, reject) =>
      setTimeout(() => reject(new Error(`${label} 超过 ${ms / 1000} 秒没有响应`)), ms)),
  ]);
}

/**
 * Pick a backend, preferring WebGL but never depending on it.
 *
 * A machine with a broken or software-only WebGL context can leave
 * `setBackend("webgl")` pending forever, which used to look identical to a slow
 * download. Each backend gets a deadline; CPU is always the fallback, and the
 * chosen one is printed so the numbers are interpretable.
 */
async function initBackend() {
  for (const name of ["webgl", "cpu"]) {
    setStatus(`正在初始化 TensorFlow.js 后端：${name}…`);
    try {
      await withTimeout(tf.setBackend(name), 5000, `${name} 后端初始化`);
      await withTimeout(tf.ready(), 5000, `${name} 后端就绪`);
      if (tf.getBackend() === name) return name;
    } catch (error) {
      console.warn(`backend ${name} unavailable:`, error);
    }
  }
  throw new Error("webgl 与 cpu 两个后端都无法初始化");
}

async function loadModel() {
  const started = performance.now();
  const backend = await initBackend();
  setStatus(`后端 ${backend}，正在下载模型（87 KB）…`);
  model = await withTimeout(
    tf.loadLayersModel("./model/model.json"),
    30000,
    "模型下载/解析",
  );
  // Warm up once so the first user-visible inference is not the graph build.
  // Shader compilation happens here, which is also where a broken WebGL shows up.
  setStatus(`后端 ${backend}，正在预热推理…`);
  try {
    const warm = tf.zeros([1, 2000, 1]);
    model.predict(warm).dispose();
    warm.dispose();
  } catch (error) {
    console.warn("warm-up failed, falling back to cpu:", error);
    await tf.setBackend("cpu");
    await tf.ready();
    const warm = tf.zeros([1, 2000, 1]);
    model.predict(warm).dispose();
    warm.dispose();
  }
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
  lastInput = input;
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

    lastResult = {
      checked_at: new Date().toISOString(),
      reference: reference.recording_id,
      class_order: CLASS_ORDER,
      keras_probabilities: Object.fromEntries(
        CLASS_ORDER.map((c, i) => [c, reference.keras_probabilities[i]]),
      ),
      browser_probabilities: Object.fromEntries(
        CLASS_ORDER.map((c, i) => [c, probabilities[i]]),
      ),
      tensor_max_abs_diff: tensorDiff,
      end_to_end_max_abs_diff: e2eDiff,
      preprocessed_sample_max_abs_diff: inputDiff,
      tolerance: TOLERANCE,
      backend: tf.getBackend(),
      tfjs: tf.version.tfjs,
      user_agent: navigator.userAgent,
      passed: rows.every((row) => row.passed),
    };

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

/**
 * Time N warm inferences and report the median and P95.
 *
 * The W2 end-side acceptance is median <= 1 s and P95 <= 2 s, so the page has to
 * be able to produce those two numbers itself rather than a single timing from
 * whichever run happened to be visible.
 */
async function runBenchmark() {
  els.benchmark.disabled = true;
  try {
    let input = lastInput;
    if (!input) {
      const reference = await (await fetch("./model/reference.json")).json();
      input = Float32Array.from(reference.input);
    }
    predict(input);   // discard one warm-up run
    const runs = 20;
    const times = [];
    for (let i = 0; i < runs; i += 1) {
      const started = performance.now();
      predict(input);
      times.push(performance.now() - started);
    }
    const sorted = [...times].sort((a, b) => a - b);
    const median = sorted[Math.floor(sorted.length / 2)];
    const p95 = sorted[Math.min(sorted.length - 1, Math.ceil(sorted.length * 0.95) - 1)];
    const verdict = median <= 1000 && p95 <= 2000
      ? `达标（中位 ≤1 s、P95 ≤2 s）`
      : `未达标（判据：中位 ≤1 s、P95 ≤2 s）`;
    els.latency.textContent =
      `时延 ${runs} 次：中位 ${median.toFixed(1)} ms，P95 ${p95.toFixed(1)} ms，` +
      `最小 ${sorted[0].toFixed(1)} / 最大 ${sorted.at(-1).toFixed(1)} ms — ${verdict}`;
    const record = { backend: tf.getBackend(), runs, median_ms: Number(median.toFixed(3)),
                     p95_ms: Number(p95.toFixed(3)), min_ms: Number(sorted[0].toFixed(3)),
                     max_ms: Number(sorted.at(-1).toFixed(3)), verdict };
    lastResult = { ...(lastResult ?? {}), latency: record };
    return record;
  } finally {
    els.benchmark.disabled = false;
  }
}

async function copyResult() {
  if (!lastResult) {
    setStatus("先跑一次一致性自检（或时延测试），再复制结果", true);
    return;
  }
  const text = JSON.stringify(lastResult, null, 2);
  try {
    await navigator.clipboard.writeText(text);
    setStatus("结果已复制到剪贴板，可直接粘贴给导师/队友");
  } catch {
    // Clipboard needs a secure context in some browsers; show it instead.
    els.detail.textContent = text;
    setStatus("本环境不允许写剪贴板，结果已显示在下方，请手动复制");
  }
}

async function main() {
  try {
    const loadMs = await loadModel();
    window.__pwaReady = true;
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
  els.benchmark.addEventListener("click", runBenchmark);
  els.copy.addEventListener("click", copyResult);
}

main();
