#!/usr/bin/env node
// Parallel, resumable chunk downloader for the CirCor 1.0.3 archive.
// The single-stream download stalls on restricted networks; several bounded
// Range requests keep making progress and can be resumed after interruption.

import { existsSync, mkdirSync, statSync } from "node:fs";
import { open, readFile, rm, writeFile } from "node:fs/promises";
import path from "node:path";

const defaultUrl =
  "https://physionet.org/content/circor-heart-sound/get-zip/1.0.3/";

function parseArgs(argv) {
  const options = {
    url: process.env.CIRCOR_URL ?? defaultUrl,
    output:
      process.env.CIRCOR_PARALLEL_OUTPUT ??
      path.resolve("data/raw/circor/circor-heart-sound-1.0.3.zip"),
    chunkBytes: 1024 * 1024,
    concurrency: 12,
    retries: 5,
    limitBytes: Number(process.env.CIRCOR_LIMIT_BYTES ?? 0),
  };
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index];
    const value = argv[index + 1];
    if (key === "--url") options.url = value;
    if (key === "--output") options.output = value;
    if (key === "--chunk-bytes") options.chunkBytes = Number(value);
    if (key === "--concurrency") options.concurrency = Number(value);
    if (key === "--retries") options.retries = Number(value);
    if (key === "--limit-bytes") options.limitBytes = Number(value);
  }
  return options;
}

async function head(url) {
  const response = await fetch(url, { method: "HEAD", redirect: "follow" });
  if (!response.ok) {
    throw new Error(`HEAD failed: HTTP ${response.status} ${response.statusText}`);
  }
  return Number(response.headers.get("content-length") ?? 0);
}

async function fetchChunk(url, start, end, retries) {
  let lastError;
  for (let attempt = 1; attempt <= retries; attempt += 1) {
    try {
      const response = await fetch(url, {
        headers: { Range: `bytes=${start}-${end}` },
        redirect: "follow",
      });
      if (response.status !== 206) {
        throw new Error(`expected HTTP 206, got ${response.status}`);
      }
      const buffer = Buffer.from(await response.arrayBuffer());
      if (buffer.length !== end - start + 1) {
        throw new Error(`short chunk: ${buffer.length} != ${end - start + 1}`);
      }
      return buffer;
    } catch (error) {
      lastError = error;
      await new Promise((resolve) => setTimeout(resolve, Math.min(2 ** attempt, 15) * 1000));
    }
  }
  throw new Error(`chunk ${start}-${end} failed: ${lastError?.message}`);
}

function chunkLength(index, chunkBytes, targetSize) {
  const start = index * chunkBytes;
  return Math.min(start + chunkBytes, targetSize) - start;
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  const totalSize = await head(options.url);
  if (totalSize <= 0) {
    throw new Error("remote did not report a content-length");
  }
  const targetSize = options.limitBytes > 0 ? Math.min(options.limitBytes, totalSize) : totalSize;
  const chunkCount = Math.ceil(targetSize / options.chunkBytes);
  const sidecarPath = `${options.output}.chunks.json`;
  mkdirSync(path.dirname(options.output), { recursive: true });

  let completed = new Set();
  if (existsSync(options.output) && existsSync(sidecarPath)) {
    const sidecar = JSON.parse(await readFile(sidecarPath, "utf8"));
    completed = new Set(sidecar.completed ?? []);
  }

  let handle;
  if (existsSync(options.output)) {
    handle = await open(options.output, "r+");
  } else {
    handle = await open(options.output, "w+");
    await handle.truncate(targetSize);
  }

  let nextChunk = 0;
  let doneBytes = 0;
  let doneChunks = 0;
  const failed = new Set();
  let sidecarWrite = Promise.resolve();
  const started = Date.now();
  const total = targetSize;

  function persistSidecar() {
    const payload = JSON.stringify({
      totalSize: targetSize,
      completed: [...completed].sort((a, b) => a - b),
    });
    sidecarWrite = sidecarWrite.then(() => writeFile(sidecarPath, payload)).catch(() => {});
    return sidecarWrite;
  }

  async function processChunk(index) {
    const start = index * options.chunkBytes;
    const end = Math.min(start + options.chunkBytes, targetSize) - 1;
    const buffer = await fetchChunk(options.url, start, end, options.retries);
    await handle.write(buffer, 0, buffer.length, start);
    completed.add(index);
    doneBytes += buffer.length;
    doneChunks += 1;
    const elapsed = (Date.now() - started) / 1000;
    const percent = ((doneBytes / total) * 100).toFixed(1);
    const speed = ((doneBytes / elapsed) / 1024).toFixed(1);
    console.log(
      `[circor-parallel] ${doneChunks}/${chunkCount} chunks, ${(doneBytes / 1e6).toFixed(1)}/${(total / 1e6).toFixed(1)} MB (${percent}%), ${speed} KB/s`,
    );
    await persistSidecar();
  }

  async function worker() {
    while (true) {
      const index = nextChunk++;
      if (index >= chunkCount) return;
      if (completed.has(index)) {
        doneBytes += chunkLength(index, options.chunkBytes, targetSize);
        doneChunks += 1;
        continue;
      }
      try {
        await processChunk(index);
      } catch (error) {
        failed.add(index);
        console.warn(`[circor-parallel] chunk ${index} deferred: ${error.message}`);
      }
    }
  }

  try {
    await handle.truncate(targetSize);
    await Promise.all(
      Array.from({ length: Math.min(options.concurrency, chunkCount) }, () => worker()),
    );

    for (let round = 1; round <= 3 && failed.size > 0; round += 1) {
      const queue = [...failed];
      failed.clear();
      console.log(`[circor-parallel] retry round ${round}: ${queue.length} chunks`);
      let cursor = 0;
      const retryWorker = async () => {
        while (true) {
          const position = cursor;
          cursor += 1;
          if (position >= queue.length) return;
          try {
            await processChunk(queue[position]);
          } catch (error) {
            failed.add(queue[position]);
          }
        }
      };
      await Promise.all(
        Array.from({ length: Math.min(4, queue.length) }, () => retryWorker()),
      );
    }
  } finally {
    await sidecarWrite.catch(() => {});
    await handle.close();
  }

  if (failed.size > 0) {
    console.error(
      `[circor-parallel] ${failed.size} chunks still missing; rerun the same command to resume`,
    );
    process.exitCode = 1;
    return;
  }

  if (targetSize === totalSize) {
    const finalSize = statSync(options.output).size;
    console.log(`[circor-parallel] complete: ${options.output} (${finalSize} bytes)`);
    await rm(sidecarPath, { force: true });
  } else {
    console.log(
      `[circor-parallel] probe complete: ${options.output} holds the first ${targetSize} bytes`,
    );
  }
}

main().catch((error) => {
  console.error(`[circor-parallel] ERROR: ${error.message}`);
  process.exitCode = 1;
});
