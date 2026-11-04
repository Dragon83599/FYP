/**
 * On-page diagnostics for "模型加载中 卡住" (plain ES5 on purpose).
 *
 * A phone has no developer console, and the failure modes look identical from
 * the outside: missing file, server root wrong, browser too old to parse the
 * modules, or a backend that never initialises. This script runs a few checks
 * that work even when app.js does not, and prints one line each:
 *
 *   file    - did the fetch succeed, and how many bytes came back
 *   module  - can the browser parse and evaluate an ES module at all
 *   tf      - is the TensorFlow.js bundle present and which version
 *   backend - does a backend initialise within 5 seconds
 *
 * Loaded with a normal <script> tag (not type="module") so a browser that
 * cannot parse modules still runs it.
 */
(function () {
  "use strict";

  var DIAG_VERSION = "diag-4 (2026-11-04)";

  function el(id) {
    return document.getElementById(id);
  }

  function line(out, label, ok, text) {
    out.push((ok === null ? "… " : ok ? "OK " : "FAIL ") + label + "：" + text);
  }

  function withTimeout(promise, ms, label) {
    return Promise.race([
      promise,
      new Promise(function (_, reject) {
        setTimeout(function () {
          reject(new Error(label + " 超时 " + ms / 1000 + " 秒"));
        }, ms);
      }),
    ]);
  }

  // Does this browser execute a <script type="module"> at all? Some stock
  // Android browsers accept the tag, fetch the file, and never run it -- which
  // is indistinguishable from a network failure without this probe.
  function probeModuleScript() {
    return new Promise(function (resolve) {
      window.__moduleProbe = false;
      try {
        var script = document.createElement("script");
        script.type = "module";
        script.textContent = "window.__moduleProbe = true;";
        document.head.appendChild(script);
      } catch (error) {
        resolve(false);
        return;
      }
      setTimeout(function () { resolve(window.__moduleProbe === true); }, 1500);
    });
  }

  function fetchSize(url) {
    return fetch(url, { cache: "no-store" })
      .then(function (response) {
        return response.arrayBuffer().then(function (buffer) {
          return {
            status: response.status,
            bytes: buffer.byteLength,
            type: response.headers.get("content-type") || "(none)",
          };
        });
      })
      .catch(function (error) {
        return { status: 0, bytes: 0, error: error.message || String(error) };
      });
  }

  window.__pwaDiag = function () {
    var out = [];
    var pre = el("diag");
    var status = el("status");

    function render() {
      var text = out.join("\n");
      if (pre) {
        pre.textContent = "诊断 " + DIAG_VERSION + "\n" + text;
        pre.style.display = "block";
      }
      if (status) {
        status.textContent = "加载未完成，诊断结果见下方（把这段发给开发者即可）";
        status.style.color = "var(--bad)";
      }
    }

    out.push("页面地址：" + location.href);
    out.push("浏览器：" + navigator.userAgent);
    out.push("");

    var assets = ["./vendor/tf.min.js", "./model/model.json", "./model/group1-shard1of1.bin", "./app.js"];
    return assets
      .reduce(function (chain, url) {
        return chain.then(function () {
          return fetchSize(url).then(function (result) {
            var ok = result.status === 200 && result.bytes > 0;
            line(out, url, ok, ok
              ? result.bytes + " 字节，" + result.type
              : "HTTP " + result.status + " " + (result.error || ""));
            return null;
          });
        });
      }, Promise.resolve())
      .then(function () {
        return probeModuleScript().then(function (supported) {
          line(out, "静态 module 脚本", supported,
            supported ? "可以执行" : "不执行（浏览器不支持或已禁用 ES module）");
          return null;
        });
      })
      .then(function () {
        // Can this browser evaluate an ES module at all?
        var bust = "?diagnose=" + Date.now();
        return import("./preprocess.js" + bust)
          .then(function () {
            line(out, "ES module 解析", true, "可以（preprocess.js 已执行）");
            return null;
          })
          .catch(function (error) {
            line(out, "ES module 解析", false, "浏览器太旧：" + (error.message || error));
            return null;
          });
      })
      .then(function () {
        if (typeof tf === "undefined") {
          line(out, "TensorFlow.js", false, "tf 未定义（vendor/tf.min.js 没执行成功）");
          render();
          return null;
        }
        line(out, "TensorFlow.js", true, "版本 " + tf.version.tfjs);
        return withTimeout(tf.setBackend("cpu"), 5000, "cpu 后端")
          .then(function () { return tf.ready(); })
          .then(function () {
            line(out, "CPU 后端", true, tf.getBackend());
            return null;
          })
          .catch(function (error) {
            line(out, "CPU 后端", false, error.message || String(error));
            return null;
          });
      })
      .then(function () {
        // WebGL is optional; report it separately so a CPU fallback is not
        // mistaken for a failure.
        if (typeof tf === "undefined") { render(); return null; }
        return withTimeout(tf.setBackend("webgl"), 5000, "webgl 后端")
          .then(function () { return tf.ready(); })
          .then(function () {
            var name = tf.getBackend();
            line(out, "WebGL 后端", name === "webgl", name);
            return null;
          })
          .catch(function (error) {
            line(out, "WebGL 后端", false, (error.message || String(error)) + "（可用 CPU 代替）");
            return null;
          });
      })
      .then(function () {
        render();
        return out.join("\n");
      })
      .catch(function (error) {
        out.push("诊断本身出错：" + (error && error.message ? error.message : error));
        render();
        return out.join("\n");
      });
  };
})();
