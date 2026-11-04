# 29 · Conv-RF IoT 2023 · SqueezeNet + 随机森林

> 阅读深度：全文（2026-10-28），17 页，IEEE OJIM 开放获取。
> **重要提醒：它的 ESP32 只负责采集与上传，推理在树莓派 4B 上跑**——不是"ESP32 上推理"。
> **阅读归属**：助手全文通读；**用户尚未亲读**（2026-10-28 核对）。

## 引用

- 作者：Tanmay Sinha Roy, Joyanta Kumar Roy, Nirupama Mandal（Haldia Institute / Narula Institute / IIT (ISM) Dhanbad）
- 标题：Conv-Random Forest-Based IoT: A Deep Learning Model Based on CNN and Random Forest for Classification and Analysis of Valvular Heart Diseases
- 期刊/会议：IEEE Open Journal of Instrumentation and Measurement 2023;2（文章号 2500717）
- 年份：2023
- DOI/链接：doi:10.1109/OJIM.2023.3320765
- 全文位置：`docs/refs/29-roy-2023-ojim.pdf`（17 页）

## 数据

- **数据集 1：GitHub 上的 Yaseen 仓库**（`yaseen21khan/Classification-of-Heart-Sound-Signal-Using-Multiple-Features`）——**五类：NS / MR / MS / MVP / AS**
  - 每条 5–10 s、**44 kHz**、带宽 65–500 Hz
- 还用了 Kaggle、PhysioNet、PASCAL 三个数据集
- 划分：**五折交叉验证** + 85%/15% 训练/测试
- ⚠️ **摘要写"七类"，但正文数据与输出层都是五类**——内部不一致

## 方法

- **SqueezeNet**（5 个 fire block + 2 个卷积 + 2 个 maxpool + GAP）提特征
- **随机森林（RF）** 做分类（替代 softmax 层）
- 硬件：
  - **ESP32**：12-bit ADC、**44.1 kHz** 采样、WiFi，**只负责采集与上传**
  - **树莓派 4B（工业版）**：做全部处理与推理，接 7 寸触摸屏
  - 传统听诊器胸件 + 前置放大 + 滤波

## 结果（原文数字）

| 模型 | 准确率 |
| --- | --- |
| SqueezeNet（softmax）| 98.65% |
| **SqueezeNet + RF（Conv-RF）** | **99.37 ± 0.05%** |

- 敏感度 99.5 ± 0.12%、特异度 98.9 ± 0.03%

## 逐节跟读（2026-10-28）

| 站 | 页码 | 内容 |
| --- | --- | --- |
| 1 | p3–4 | 四个数据集：**GitHub（= Yaseen，五类）、Kaggle、PASCAL、PhysioNet** |
| 2 | p5–6 | **硬件链路**：听诊器胸件 → 前置放大/滤波 → **ESP32（12-bit ADC、44.1 kHz、WiFi）** → **树莓派 4B（推理 + 7 寸屏）** |
| 3 | p7–9 | 模型：**SqueezeNet（5 个 fire block）** + **随机森林**替代 softmax |
| 4 | p10–12 | 结果：SqueezeNet **98.65%** → **Conv-RF 99.37 ± 0.05%** |
| 5 | — | 三处问题（见下）|

### 第 2 站的要点：ESP32 到底干了什么

```
麦克风 → 放大/滤波 → ESP32：采样 44.1 kHz、12-bit ADC → 通过 WiFi 发送
                                              ↓
                       树莓派 4B：预处理 + CNN + RF → 7 寸屏显示
```

**ESP32 只负责采集与上传，不做推理。** 所以标题里的 "IoT" 指的是"联网设备"，不是"端侧智能"。

### 第 3 站的要点：用 RF 替代 softmax

- SqueezeNet 的最后一层（softmax）被**随机森林**取代；
- RF 的输入是 CNN 提取的**特征向量**；
- 好处：小样本时 RF 比 softmax 稳；坏处：**推理要跑两段**（CNN + 50 棵左右的树），端侧收益有限。

### 第 5 站：三处问题

**① 摘要写"七类"，正文与输出层是五类**

> `the multiclass classification is done for seven types of valvular heart sounds`

但架构输出是 **5 个节点**、数据是 5 类（NS/MR/MS/MVP/AS）——**内部不一致**。

**② 44 kHz 采样对心音是浪费**

- 心音有效带宽 65–500 Hz，**1 kHz 采样就够**（27 用 1 kHz）；
- 它用 44.1 kHz，**数据量 44 倍**，对 ESP32 的传输与功耗都不利；
- 原文**没有做降采样论证**。

**③ "IoT + ESP32" 容易被误读**

标题、摘要都强调 IoT 与 ESP32，但**推理在树莓派上**。这是综述里最容易被转述错的类型。

## 我认为重要的点（原话/你的理解）

- **2026-10-28，自测题回答（原话）**：> 去掉随机森林只跑CNN
  - 方向对，但**RF 不是瓶颈**：RF 作用在 CNN 输出的低维特征上，推理开销是**微秒级**，砍掉它只省流程复杂度，不省算力；
  - 真正的三个瓶颈，按影响排序：

| 项 | 现状 | 对端侧的影响 | 该不该砍 |
| --- | --- | --- | --- |
| **① 采样率 44.1 kHz** | 心音有效带宽仅 65–500 Hz | 数据量是 1 kHz 的 **44 倍**，传输/功耗/计算全被拖累 | **必须降到 1–4 kHz** |
| **② SqueezeNet 规模** | 标准 SqueezeNet 约 **1.2 M 参数**（公开规格）| 是我们的 **8 倍**预算（≤150k）| **必须换小模型** |
| **③ 2D 卷积** | 输入是二维图 | 2D 卷积比 1D 更耗内存与算力 | **应改 1D 或深度可分离** |
| ④ RF 分类器 | 特征维度低 | 微秒级 | 可留可去，影响小 |

  - 结论：**砍 RF 只是"简化"，砍采样率与模型规模才是"能跑"**。


- **（你还没有转述这篇的原话。）** 你转述时我逐字记在这里，不改写。

## 与我的 FYP 的关系

- **最有用的一条提醒**：论文标题写着 IoT、正文出现 ESP32，很容易被误读成"ESP32 上跑模型"——**实际上推理在树莓派上**。我们写相关工作时要避免这种误读，也要在自己的实验里明确区分"采集端"与"推理端"。
- 可借鉴：
  - **SqueezeNet 的 fire block**（1×1 压缩 + 1×1/3×3 扩展）是成熟的轻量化结构，可作 A2 的候选；
  - **CNN 特征 + 树外分类器（RF）**：RF 本身很小，但需要把 CNN 特征送进树莓派，端侧收益有限。
- 需要注意：
  - **44 kHz 采样**对心音是浪费（我们 4 kHz 已足够），它没有做降采样论证；
  - 99.37% 是随机划分下的数据集内结果；
  - "七类 vs 五类"的不一致说明写作/实验记录不严谨。

## 待查证

- 五折 CV 是否分层、是否固定种子。
- 99.37% 是在哪个数据集上得到的（GitHub 还是合并集）。
- RF 的树数与深度；CNN 特征维度。
