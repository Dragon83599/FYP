/**
 * Static checks for the runoob study-path page (docs/learn/runoob-path.html).
 *
 * The page is a single self-contained file with the curriculum in one inline
 * array. These tests keep it honest without a browser: the script must parse,
 * every entry must carry a title/why/check, and every link must actually point
 * at runoob -- a typo there is invisible until someone clicks it in front of a
 * supervisor.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const html = readFileSync(path.join(ROOT, "docs/learn/runoob-path.html"), "utf8");
const script = (html.match(/<script>([\s\S]*?)<\/script>/) || [])[1];

function items() {
  const block = script.match(/const ITEMS = (\[[\s\S]*?\n\];)/)[1].replace(/;$/, "");
  return new Function("return " + block)();
}

test("the page script parses", () => {
  assert.ok(script, "inline script not found");
  assert.doesNotThrow(() => new Function(script));
});

test("every element id the script looks up exists in the markup", () => {
  const ids = new Set([...html.matchAll(/id="([^"]+)"/g)].map((m) => m[1]));
  const used = [...script.matchAll(/getElementById\("([^"]+)"\)/g)].map((m) => m[1]);
  assert.ok(used.length >= 5);
  for (const id of used) assert.ok(ids.has(id), `script uses #${id} but the markup has no such id`);
});

test("every entry is complete and ordered", () => {
  const list = items();
  assert.ok(list.length >= 40, `expected at least 40 entries, got ${list.length}`);
  for (const [index, item] of list.entries()) {
    const where = `entry ${index + 1} (${item.t ?? "no title"})`;
    assert.ok(item.t && item.t.length > 3, `${where}: missing title`);
    assert.ok(item.why && item.why.length > 5, `${where}: missing why`);
    assert.ok(item.check && item.check.length > 5, `${where}: missing check`);
    assert.ok(["must", "opt", "skip"].includes(item.tier), `${where}: bad tier ${item.tier}`);
  }
});

test("every link points at runoob over https", () => {
  const bad = items().filter((item) => !/^https:\/\/www\.runoob\.com\/\S+\.html$/.test(item.u));
  assert.deepEqual(bad.map((item) => `${item.t} -> ${item.u}`), []);
});

test("entries runoob cannot cover carry an official-doc link and a reason", () => {
  // Three SciPy signal chapters, fetch, and Keras are not (or not usefully)
  // covered by runoob; each must point somewhere that is, and say why.
  const list = items();
  const alt = list.filter((item) => item.alt);
  assert.ok(alt.length >= 5, `expected at least 5 official-doc fallbacks, got ${alt.length}`);
  for (const item of alt) {
    assert.match(item.alt, /^https:\/\//, `${item.t}: alt link must be https`);
    assert.ok(item.note && item.note.length > 10, `${item.t}: an alt link needs a note saying why`);
  }
  const titles = alt.map((item) => item.t).join(" ");
  for (const topic of ["SciPy", "AJAX", "Keras"]) {
    assert.ok(titles.includes(topic), `no official-doc fallback for ${topic}`);
  }
});

test("the required core is present and is a large share of the list", () => {
  const list = items();
  const must = list.filter((item) => item.tier === "must");
  assert.ok(must.length >= 25, `expected at least 25 must-have entries, got ${must.length}`);
  // the concepts this project actually depends on
  const text = list.map((item) => item.t).join(" ");
  for (const topic of ["交叉验证", "数据泄漏", "分类指标", "统计学基础", "SciPy", "Git", "HTTP"]) {
    assert.ok(text.includes(topic), `the path is missing a core topic: ${topic}`);
  }
});

test("the page says where progress is stored and that it is local", () => {
  assert.match(html, /localStorage/);
  assert.match(html, /进度保存在本浏览器/);
});
