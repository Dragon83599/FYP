# FYP：无线心音听诊器（PCG）

二体式无线听诊器：探头以 8 kHz 采集心音，经 BLE（LKA1 协议）送到主机 ESP32-S3，
主机做波形显示、杂音筛查和心率估算。

> **当前状态（2026-10-19）**：PC 侧证据链已完成——数据评估、模型对照、校准与工作点、
> 容量研究、泛化探针、INT8 量化、部署导出，全部有可复现报告。
> **唯一大缺口是上板实测**（尚未采购开发板，见 ADR-017）。
> 交接文档：[`docs/handoff-2026-10-19.md`](docs/handoff-2026-10-19.md)

## 系统基线

- 信号带宽 20–1000 Hz；采集 8 kHz、单声道、16-bit PCM、小端；AI 推理前带限抽取到 4 kHz
- 逻辑帧 20 ms = 160 采样；应用层 `AUD1` 352 B 帧（30 B 头 + 320 B PCM + 2 B CRC16）
- 链路 A：BLE 5.0 GATT，ATT MTU 247，单分片负载 222 B，持续吞吐目标 ≥320 kbps
- 探头端 8 kHz → BLE → 主机端重组 → 4 kHz → 环形缓冲 → 推理/显示

## 关键结果

### 杂音筛查（CirCor 1.0.3，874 人患者级 5 折）

| 模型 | 患者级 AUROC（折均值 ± 标准差）| 说明 |
| --- | ---: | --- |
| 逻辑回归（40 带 log-mel） | 0.789 ± 0.031 | 线性基线 |
| MFCC + Δ + 逻辑回归 | 0.801 ± 0.043 | 特征工程 |
| **小 CNN（41,105 参数）** | **0.848 ± 0.051** | 最终选择 |

### 容量研究（同冻结划分、同协议，只改容量）

| 模型 | 参数 | 患者级 AUROC | pooled |
| --- | ---: | ---: | ---: |
| 小 CNN | 41,105 | 0.848 | 0.814 |
| ResNet | 463,089 | 0.897 | 0.867 |
| TCN | 331,713 | 0.929 | 0.911 |

三种子复现：TCN−small 的优势 15/15 折对为正（效应量 +0.09…+0.15）。

### 部署格式（INT8）——结论反转

| 模型 | FP32 | INT8 | 掉点 | INT8 体积 |
| --- | ---: | ---: | ---: | ---: |
| 小 CNN | 0.8414 | 0.8213 | **0.0191** | **69,856 B** |
| TCN | 0.9289 | 0.8022 | **0.1433** | 395,818 B |

**TCN 的 FP32 优势在 INT8 下消失，且体积是 5.7 倍 → 最终部署模型是小 CNN。**

### 其它

- 校准：原始概率 ECE 0.22–0.24 → Platt 后 0.042（但斜率仍仅 0.71–0.78，概率值不直接展示）
- 工作点 W1：CNN 敏感度 0.777 / 特异度 0.797（0.80 门槛差 0.003，未守住，阈值需重定）
- 心率（设备条件）：中位误差 1.19 bpm，MAE 6.79，**13.5% 粗错** → 只作估算值显示
- S1/S2：macro F1 0.7975 未达标 → 按 ADR-008 回退，MVP 不展示

## 可运行验证

```bash
# 测试基线：C 5 / Node 16 / Python 246
cmake -S . -B build -DBUILD_TESTING=ON && cmake --build build && ctest --test-dir build --output-on-failure
npm test
.venv/bin/python -m unittest discover -s tests/python

# 一键复现已引用的数字（--fast 跳过两次 CNN 五折）
scripts/reproduce.sh --fast

# 协议与链路模拟（无需硬件）
npm run sim:demo

# 固件（独立 ESP-IDF 工程，v5.5.6）
. /home/psr/esp/esp-idf/export.sh
cd firmware && idf.py set-target esp32s3 && idf.py build
```

当前固件镜像 512,399 B，DIRAM 49.7%（余 171 KB）。`idf.py size` 报的 "IRAM 100%" 指 16 KB 的**指令专用窗口**，只影响新增 `IRAM_ATTR`/ISR，不是内存瓶颈——见 `docs/iram-and-display-budget.md`。

## 文档索引

**状态与交接**
- `docs/handoff-2026-10-19.md`：当前状态、已确认数字、已知缺陷、下一步（**新对话先读这个**）
- `docs/implementation-roadmap.md`：软件实施进度
- `docs/workflow-and-open-items.md`：工作流摘要、与队友的确认清单、我的后续任务
- `docs/fyp-overall-workflow.md`：**全流程 9 阶段 / 89 任务**（含负责人、证据、验收、关键路径）
- `meetings/2026-10-20/fyp-workflow.html`：可打钩的交互清单（单文件，勾选进度存本地）
- `docs/iram-and-display-budget.md`：IRAM/DIRAM 真实预算与显示方案对比
- `docs/progress-2026-10-18.md`、`docs/progress-2026-10-17.md`、`docs/progress-2026-10-08.md`：逐日进度

**决策记录（ADR-001…018）**
- `docs/engineering-decision-register.md`：全部决策、证据等级、验证与回退
- `docs/engineering-decision-guide.md`：候选对比与证据等级规范

**接口与协议**
- `docs/phase0-interface-freeze.md`：v0.2 接口基线、测试向量、验收清单
- `docs/link-a-interface-freeze.md`：`LKA1` 分包与重组
- `docs/link-a-gatt-profile.md`：BLE GATT 服务、时序、绑定与重连
- `docs/connectivity-topology.md`：BLE 探头链路与 Wi-Fi 主机链路拓扑
- `shared/include/`：`audio_protocol.h`、`link_a_protocol.h`、`host_pipeline.h`

**AI 与评估**
- `docs/mvp-output-contract.md`：MVP 输出契约（波形/杂音/心率/S1-S2 的措辞要求）
- `docs/fyp-scope-and-sufficiency.md`：范围契约（必须/应该/不做）与答辩检查表
- `docs/edge-ai-primer.md`：边缘 AI 基础速查（参数量/MACs/INT8/内存层次）
- `docs/host-resource-budget.md`：主机平台资源预算
- `docs/ai-baseline-plan.md`、`docs/ai-model-resource-budget-guide.md`：基线与预算指南
- `docs/s1s2-detector-baseline.md`：S1/S2 检测器的方法、实测与失败模式

**会议**
- `meetings/2026-10-20/outline.md`：两周进展汇报大纲（11 页）
- `meetings/templates/engineering-decision-slides.md`：决策对比页模板

## 仓库结构

- `shared/`：探头与主机共用的 C 接口与实现（协议、重组、主机数据通路）
- `firmware/`：ESP32-S3 主机固件（ESP-IDF v5.5.6），复用 `shared/`
- `tools/ai/`：CirCor 准备、特征、三臂模型、交叉验证、校准、量化、部署导出
- `tools/audio-sim/`：零依赖的协议与音频链路模拟器
- `tests/`：C（ctest）、Node、Python 三套测试
- `docs/`：交接、决策、接口、评估文档
- `meetings/`：组会材料与模板
- `assets/`：架构图、产品图、原始需求图
- `data/`：数据集与报告（**gitignore，不入库**）

## 已知边界

1. **没有任何上板数据**：时延、电流、峰值堆、屏幕均未测
2. **单数据集**：CirCor（儿科/青年，单一采集与标注协议），无外部队列
3. **INT8 只在 PC 侧（ONNX Runtime）验证**，ESP-DL/TFLite Micro 未复测
4. **校准不可跨中心迁移**：换中心必须重定阈值（BA 曾从 0.77 跌到 0.596）
5. **S1/S2 未达标**（0.7975 < 0.80）
6. **线程数影响绝对精度**：4→2 线程时小 CNN pooled 0.814→0.770；配对 delta 不受影响

## 下一步

1. 采购 ESP32-S3-DevKitC-1 **N16R8**（16 MB flash / 8 MB PSRAM）+ 320×240 SPI 屏（ADR-017）
2. 上板实测 11 项：烧录启动、MTU/PHY/CCCD、5 分钟丢帧率、P95 推理时延、峰值堆、平均电流、屏幕显示
3. 可选：TCN 的 QAT（唯一能把 TCN 拉回可部署的路径，但非必需）

## 参考资料

- `docs/group-meeting-2026-10-06-decisions.md`：组会确认的产品形态与链路结论
- `docs/reimbursement/README.md`：采购、报销与开票资料归档

## 环境

- WSL2；不要在 `/mnt/e` 上跑重活（9p 约 75 KB/s）
- Python：`/home/psr/FYP/.venv/bin/python`
- ESP-IDF：`/home/psr/esp/esp-idf` v5.5.6
