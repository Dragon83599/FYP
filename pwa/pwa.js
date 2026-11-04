/**
 * PWA plumbing: Service Worker registration and the install prompt.
 *
 * Kept out of app.js and written as a classic script so it still runs when the
 * modules do not (an old browser can still be told "use HTTPS to install").
 *
 * The honest bit: a Service Worker only registers in a secure context. On the
 * demo's http:// LAN address the browser refuses, so the page says "装不了，
 * 原因见下" rather than showing a button that does nothing.
 */
(function () {
  "use strict";

  var stateEl = document.getElementById("pwa-state");
  var installEl = document.getElementById("install");
  var deferredPrompt = null;

  function setState(text, bad) {
    if (!stateEl) return;
    stateEl.textContent = text;
    stateEl.style.color = bad ? "var(--bad)" : "";
  }

  function isSecure() {
    return (
      location.protocol === "https:" ||
      location.hostname === "localhost" ||
      location.hostname === "127.0.0.1"
    );
  }

  // --- install prompt -----------------------------------------------------
  window.addEventListener("beforeinstallprompt", function (event) {
    event.preventDefault();
    deferredPrompt = event;
    if (installEl) {
      installEl.style.display = "";
      installEl.disabled = false;
    }
  });

  if (installEl) {
    installEl.addEventListener("click", function () {
      if (!deferredPrompt) return;
      deferredPrompt.prompt();
      deferredPrompt.userChoice.then(function (choice) {
        setState(choice.outcome === "accepted" ? "已安装到桌面" : "已取消安装");
        deferredPrompt = null;
        installEl.style.display = "none";
      });
    });
  }

  window.addEventListener("appinstalled", function () {
    installEl.style.display = "none";
    setState("已安装：下次可以直接从桌面图标打开，不需要浏览器地址栏");
  });

  // --- service worker -----------------------------------------------------
  if (!("serviceWorker" in navigator)) {
    setState("这个浏览器不支持 Service Worker，无法离线打开（推理本身仍可用）", true);
    return;
  }

  if (!isSecure()) {
    setState(
      "当前是 http:// 局域网地址，浏览器不允许注册 Service Worker —— " +
        "所以现在关掉电脑就打不开。要用成真正的 PWA，需部署到 HTTPS（见 pwa/DEPLOY.md）。",
      true,
    );
    return;
  }

  window.addEventListener("load", function () {
    navigator.serviceWorker
      .register("./sw.js")
      .then(function (registration) {
        if (registration.active) {
          setState("离线可用：已缓存全部文件，断网也能打开和推理");
        } else {
          setState("正在缓存文件（首次需要几秒），完成后即可断网使用…");
        }
        registration.addEventListener("updatefound", function () {
          setState("发现新版本，正在更新缓存…");
        });
      })
      .catch(function (error) {
        setState("Service Worker 注册失败：" + (error && error.message ? error.message : error), true);
      });
  });
})();
