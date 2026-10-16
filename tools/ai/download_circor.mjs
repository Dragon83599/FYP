#!/usr/bin/env node
// Download the public CirCor DigiScope 1.0.3 archive.
// Source: https://physionet.org/content/circor-heart-sound/1.0.3/
// License: Open Data Commons Attribution License v1.0 (ODC-By 1.0).

import { createWriteStream, existsSync, mkdirSync, statSync } from "node:fs";
import { rename, rm } from "node:fs/promises";
import { Readable } from "node:stream";
import { pipeline } from "node:stream/promises";
import path from "node:path";

const archiveUrl =
  process.env.CIRCOR_URL ??
  "https://physionet.org/content/circor-heart-sound/get-zip/1.0.3/";
const outputDir = process.env.CIRCOR_DIR ?? path.resolve("data/raw/circor");
const zipPath = path.join(outputDir, "circor-heart-sound-1.0.3.zip");
const partPath = `${zipPath}.part`;

mkdirSync(outputDir, { recursive: true });

async function main() {
  if (existsSync(zipPath)) {
    const size = statSync(zipPath).size;
    console.log(`[circor] archive already present: ${zipPath} (${size} bytes)`);
    return;
  }

  const resumeFrom = existsSync(partPath) ? statSync(partPath).size : 0;
  const headers = resumeFrom > 0 ? { Range: `bytes=${resumeFrom}-` } : {};
  console.log(
    `[circor] downloading ${archiveUrl}${resumeFrom > 0 ? ` (resume at ${resumeFrom})` : ""}`,
  );

  const response = await fetch(archiveUrl, { headers, redirect: "follow" });
  if (!response.ok || !response.body) {
    throw new Error(`download failed: HTTP ${response.status} ${response.statusText}`);
  }

  const expected = Number(response.headers.get("content-length") ?? 0);
  if (resumeFrom > 0 && response.status !== 206) {
    throw new Error("server ignored Range request; delete the .part file and retry");
  }

  let received = resumeFrom;
  let lastReport = 0;
  const progress = new TransformStream({
    transform(chunk, controller) {
      received += chunk.byteLength;
      if (received - lastReport >= 10 * 1024 * 1024) {
        lastReport = received;
        const total = expected > 0 ? ` / ${(resumeFrom + expected) / 1e6} MB` : "";
        console.log(`[circor] ${(received / 1e6).toFixed(1)} MB${total}`);
      }
      controller.enqueue(chunk);
    },
  });

  await pipeline(
    Readable.fromWeb(response.body.pipeThrough(progress)),
    createWriteStream(partPath, { flags: resumeFrom > 0 ? "a" : "w" }),
  );
  await rename(partPath, zipPath);
  console.log(`[circor] saved ${zipPath} (${statSync(zipPath).size} bytes)`);
}

main().catch(async (error) => {
  console.error(`[circor] ERROR: ${error.message}`);
  await rm(partPath, { force: true }).catch(() => {});
  process.exitCode = 1;
});
