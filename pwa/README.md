# PWA 演示页（端侧推理）

> 5 类心音分类的浏览器实现：**20,193 参数**的 Ma 等（2023）轻量 CNN，
> 全部推理在本机完成，不联网。对应决策 **ADR-054**，证据记录 `docs/tasks/w2-repro-ma2023.md`。

## 1. 跑起来（局域网形态，答辩现场用这条）

```bash
scripts/serve-pwa.sh          # 起服务并直接打印手机该打开的地址
scripts/serve-pwa.sh 9000     # 换端口
```

脚本会打印本机地址与局域网地址，例如 `http://192.168.118.138:8080/`；
手机连同一热点后打开局域网那条即可。等价的原始命令是
`cd pwa && python3 -m http.server 8080 --bind 0.0.0.0`。

- **不需要 HTTPS**：本页不注册 Service Worker，只用 `http://` + 本地文件，手机可直接打开。
- 若场地 Wi-Fi 隔离客户端，用队友设备或笔记本开热点（见 `docs/tasks/pwa-demo-hosting-options.md`）。

## 2. 页面上能做什么

| 功能 | 说明 |
| --- | --- |
| 选 wav 分类 | 只接受 **8 kHz**、PCM 的 wav；显示五类概率条与判读 |
| 一致性自检 | 点一下就自动加载 `model/New_AS_001.wav`，用**页面自己的预处理**跑一遍，与 Keras 的参考概率比，判据 **<1e-3** |
| 测 20 次时延 | 预热 1 次后跑 20 次，报**中位 / P95 / 最小 / 最大**，并按 W2 判据（中位 ≤1 s、P95 ≤2 s）给"达标/未达标" |
| 复制结果 | 把自检 + 时延的完整 JSON（含后端、tfjs 版本、UA）复制到剪贴板，便于留证或贴回报告 |

自检给两行结果：

| 检查 | 含义 |
| --- | --- |
| 模型（同一张量） | 直接喂 Python 预处理好的 2,000 点 —— 只测模型数值 |
| 端到端（同一 wav） | 浏览器自己读 wav → 重采样 → 裁/补 → min-max → 推理 —— 测整条通路 |

**两行都要通过**才说明"Python 与浏览器一致"。只测第一行会漏掉预处理差异：
我们就是这样抓到过一个"抽取偏移 77 点"的 bug（概率差 2.4e-7 看着通过，但样本级差 1.70）。

## 3. 文件

| 文件 | 作用 |
| --- | --- |
| `index.html` | 页面骨架 |
| `app.js` | 加载模型、跑自检、渲染结果 |
| `preprocess.js` | 与 `tools/ai/repro_ma2023.py` 同口径的预处理（1 kHz / 2,000 点 / min-max）|
| `filters/resample-poly.js` | **scipy 自己的 FIR 系数**（169 taps），由 `tools/ai/make_pwa_filters.py` 生成 |
| `wav.js` | 直接解析 PCM，不用 `decodeAudioData`（后者会按设备采样率重采样）|
| `model/model.json` + `group1-shard1of1.bin` | 模型（6,603 B + 80,772 B）|
| `model/reference.json` + `New_AS_001.wav` | 自检用的参考张量与同一段录音 |
| `vendor/tf.min.js` | TensorFlow.js 4.22.0（1.5 MB，本地自带，不依赖 CDN）|

## 4. 已验证 / 未验证

| 项 | 状态 | 证据 |
| --- | --- | --- |
| 模型数值（Python ↔ tfjs） | ✅ 最大差 **1.19e-7** | `node tools/ai/tfjs-check/check.mjs` |
| 端到端（wav → 概率） | ✅ 最大差 **1.19e-7**，预处理样本级差 **0** | `node tools/ai/tfjs-check/e2e.mjs` |
| 页面代码路径 | ✅ 用 DOM shim 跑通加载 + 自检 | `node tools/ai/tfjs-check/page-smoke.mjs` |
| 静态完整性（id/路径/参数量） | ✅ 6 项，进 CI | `npm test` |
| 时延测量按钮（Node CPU 后端） | ✅ 中位 **12.0 ms**、P95 **13.7 ms**（20 次，预热 1 次） | `node tools/ai/tfjs-check/page-smoke.mjs` |
| **真实浏览器渲染与 WebGL 后端** | ⏳ **未在本机验证**（无浏览器） | 需现场打开页面，状态行会显示后端名 |
| **手机端时延**（中位 ≤1 s / P95 ≤2 s） | ⏳ 未测 | 现场点"测 20 次时延"，把数字复制回来 |

**注意**：`PASS` 的前提是页面用 `model/model.json` 这份权重；换模型后必须重跑上面三条命令。

## 5. 重新生成

```bash
# 1) 训练全量模型（约 2 min）
.venv-tf/bin/python tools/ai/repro_ma2023.py --final-only --early-stopping none --epochs 100 \
    --save-final-model data/reports/repro-ma2023/model.keras

# 2) 导出到 pwa/model（含自检用的 reference.json）
.venv-tf/bin/python tools/ai/export_tfjs.py --model data/reports/repro-ma2023/model.keras \
    --out pwa/model --reference pwa/model/reference.json
cp data/raw/yaseen/extracted/AS/New_AS_001.wav pwa/model/

# 3) 重采样系数（只在 scipy 版本变化时需要）
.venv/bin/python tools/ai/make_pwa_filters.py
```
