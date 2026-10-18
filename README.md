# FYP

本仓库当前处于阶段 0：数据与硬件接口冻结。

## 已确认的产品与链路基线

- 产品形态：二体式，无线探头 + 独立主机。
- 探头与主机：必须无线连接，候选方案为 BLE 5.0 GATT。
- 主机与外部设备：通过 Wi-Fi 连接手机、本地服务器或局域网网关。
- 支持主机 SoftAP、外部 AP 和手机热点；手机可继续作为云端上传网关。
- 主机脱离手机和服务器时仍能完成采集、显示、回放和边缘初步诊断。
- 分工建议：队友负责探头端，本人负责主机端；Link A 两端和协议由双方共同冻结。
- 最新组会结论见 `docs/group-meeting-2026-10-06-decisions.md`。

## 阶段 0 产物

- `AGENTS.md`：项目工程决策规范，所有后续对话必须遵守。
- `docs/engineering-decision-guide.md`：候选方案对比、证据等级和 ADR 模板。
- `docs/engineering-decision-register.md`：当前硬件、协议和架构决策登记表。
- `docs/progress-2026-10-08.md`：10 月 7 日至 8 日的协议、AI、学习和决策进度摘要。
- `docs/progress-2026-10-17.md`：10 月 17 日的数据集审计与决策进度。
- `docs/progress-2026-10-18.md`：链路 A 的 GATT profile 和 S1/S2 事件级评估进度。
- `docs/s1s2-detector-baseline.md`：S1/S2 DSP 检测器的方法、合成数据实测与失败模式。
- `docs/mvp-output-contract.md`：MVP 输出契约（波形/杂音/心率/ S1-S2 各自的证据与措辞要求）。
- `docs/host-resource-budget.md`：主机平台资源预算（ADR-006 的量化输入）。
- `docs/ai-model-resource-budget-guide.md`：从数据、训练、量化到目标板资源预算的入门指南。
- `docs/ai-baseline-plan.md`：第一版心音 AI 的候选方案对比、实测预算和验证计划。
- `docs/phase0-interface-freeze.md`：v0.2 接口基线、决策理由、测试向量和验收清单。
- `docs/link-a-interface-freeze.md`：探头到主机的 `LKA1` 分包与重组协议。
- `docs/link-a-gatt-profile.md`：链路 A 的 BLE GATT 服务、时序、绑定与重连语义（候选 v1）。
- `docs/connectivity-topology.md`：BLE 探头链路和 Wi-Fi 主机链路的拓扑决策。
- `docs/implementation-roadmap.md`：软件实施进度和硬件接入条件。
- `shared/include/audio_protocol.h`：探头固件与主机软件共同使用的 C/C++ 兼容接口定义。
- `shared/include/link_a_protocol.h`：无线链路 A 的分片和重组接口。
- `shared/include/host_pipeline.h`：主机侧数据通路，包重组 -> AUD1 校验 -> 8k→4k 抽取 -> 环形缓冲。

## 可运行验证

```text
npm test
npm run sim:demo
node tools/audio-sim/probe-simulator.mjs input-8k.wav probe.lka
node tools/audio-sim/host-receiver.mjs probe.lka output-8k.wav --ai ai-input-4k.wav

cmake -S . -B build -DBUILD_TESTING=ON && cmake --build build && ctest --test-dir build --output-on-failure
python -m unittest discover -s tests/python -v
python tools/ai/synthetic_pcg.py --out-dir data/processed/synthetic-pcg --subjects 12
python tools/ai/evaluate_s1s2.py --dataset data/processed/synthetic-pcg --out data/reports/s1s2-synthetic.json
```

Node 测试不需要第三方依赖。演示程序会生成 8 kHz 原始 WAV、经模拟链路
接收的 WAV，以及抽取后的 4 kHz AI 输入 WAV。Python 部分需要 NumPy，
见 `tools/ai/README.md`；最后一个命令输出 S1/S2 事件级 F1、互换率和边界误差。

固件是独立的 ESP-IDF 工程，需要先装 ESP-IDF v5.5.6 并激活环境：

```text
. /home/psr/esp/esp-idf/export.sh
cd firmware && idf.py set-target esp32s3 && idf.py build
```

当前镜像 505,267 B，DIRAM 39.4%，**IRAM 已 100% 占满**；加显示或 AI 前必须先腾 IRAM。

## 当前基线

- 有效信号带宽：20~1000 Hz。
- 采集：8 kHz、单声道、16-bit PCM、小端。
- AI 推理：带限抽取到 4 kHz。
- 逻辑帧：20 ms，即每声道 160 个 `int16_t` 样本。
- 时间信息：`uint32_t sequence` 和 `uint32_t timestamp_ms`。
- 探头到主机链路：无线，制式待定，应用层最低约 140.8 kbps。
- 链路 A 候选方案：BLE 5.0 GATT，ATT MTU 247，单分片负载 222 B，持续吞吐至少 320 kbps。
- 主机到外部链路：Wi-Fi 连接手机或服务器。

## 下一步

1. 按冻结文档第 9 节确认 BLE 5.0 模块、主机 Wi-Fi 方案和两段链路的器件选型。
2. 分别填写探头主控、主机主控、传感器、ADC 路径和无线模块的最终值。
3. 双方用第 6.1 节测试向量核对 header 与 CRC。
4. 将 `shared/src/audio_protocol.c` 和 `shared/src/link_a_protocol.c` 接入
   探头与主机固件。
5. 完成两段无线链路的连续传输、共存和故障测试。
6. 完成第 8 节全部验收项后，将文档状态改为“已冻结”并升级版本号。

## 参考资料

- `docs/group-meeting-2026-10-06-decisions.md`：组会后确认的产品形态与链路结论。
- `docs/group-meeting-2026-10-06-detailed-reference.md`：会前详细参考，已不再是当前连接方案的最终口径。
- [`docs/reimbursement/README.md`](docs/reimbursement/README.md)：311 FYP 采购、报销和开票资料归档，以及材料核对清单。

## 仓库结构

- `assets/`：当前系统架构图、产品图、原始需求图和老师板书图。
- `assets/archive/`：更新前的 v1 架构图和产品图。
- `docs/`：阶段 0 基线、组会确认结论和会前参考。
- `docs/reimbursement/`：脱敏后的采购报销 SOP、单位开票资料和操作清单。
- `meetings/2026-10-06/`：2026-10-06 组会大纲、讲稿、PPTX 和生成脚本。
- `meetings/templates/engineering-decision-slides.md`：后续组会方案对比页模板。
- `shared/`：探头固件与主机软件共用的 C/C++ 接口定义和实现，含主机侧数据通路。
- `tools/audio-sim/`：零依赖的协议与音频链路模拟器。
- `tools/ai/`：CirCor 数据准备、合成 PCG、逻辑回归与 1D CNN 基线和资源预算脚本。
- `firmware/`：ESP32-S3 主机固件（ESP-IDF v5.5.6），复用 `shared/` 的数据通路。
- `tests/`：C 和 Node 自动化测试；`test_end_to_end_chain.c` 覆盖完整接收链（协议→数据通路→心率）。
