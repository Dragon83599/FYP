# 25 · Kannan 2025 · VHD 综述（PRISMA）

> 阅读深度：全文（2026-10-28），21 页，IEEE Access 开放获取。
> **比 04（2024）更新、更聚焦 VHD**；但它的表格是图片，数字无法直接提取——只能当"地图"，不能当"数据源"。
> **阅读归属**：用户已亲读（助手核对过对话记录）。

## 引用

- 作者：Ayappasamy Kannan, Manob Jyoti Saikia, Sushant Kumar, Sumit Datta（Memphis / Digital University Kerala / IIT Kanpur）
- 标题：Detection of Valvular Heart Diseases From PCG Signals Using Machine and Deep Learning Models: A Review
- 期刊/会议：**IEEE Access 2025;13**（DOI 10.1109/ACCESS.2025.3583263）
- 年份：2025（6 月 25 日在线）
- 全文位置：`docs/refs/25-kannan-2025-access.pdf`（21 页）

## 范围与方法

- **199 篇**文献，覆盖 **2016–2024**（部分检索从 2014 起）
- 来源：IEEE、Science Direct、Frontiers、MDPI、Computing in Cardiology，以及 **MHSL、Github、PhysioNet** 三个数据集库
- 方法：**PRISMA 2018** 流程
- 结构：PCG 处理（预处理 / 特征提取 / 特征选择 / 分类）→ 近期模型 → **实际落地（含轻量 SSL、可穿戴）** → 降噪 → 数据集 → PRISMA → 性能评估（指标、比较、局限、未来）→ 讨论

## 它明确提到的三件事（与我们的方向直接相关）

1. **轻量与自监督**：引用 **Ma 2023**（轻量模型 + SSL，参数量约为同类 1/10），放在 "LIGHT WEIGHT CNN AND SELF-SUPERVISED LEARNING" 小节——说明"端侧轻量"已被综述承认为独立方向。
2. **可穿戴/嵌入式**：讨论低功耗神经网络部署在嵌入式设备，并与 **ASIC / FPGA** 比较成本与可更新性。
3. **一个很实用的警告**：
   > `The signals are rejected in the low-quality parts during the classification process. In an uncontrolled population with poor signal quality, this could exaggerate genuine test features.`
   → **丢弃低质量信号会抬高测试指标**。这条正好支撑我们"质量门要单独报告、不能静默丢弃"的设计。

## 它列的局限（原文叙述段）

> 信号变异、噪声与伪迹、数据集有限、类别不平衡、观察者间/内变异、心音复杂度、特征提取受限、过拟合、泛化、**缺乏标准化**、监管与伦理约束

未来方向：

- **Transformer / attention** 用于心音分割与分类；
- **直接用原始波形 + 可学习参数**（批评"scalogram 层里塞不可学习参数"）；
- 降噪以适配非受控环境；
- 远程医疗与可穿戴集成。

## B. PRACTICAL ADOPTION IN REAL-WORLD 逐条解读（2026-10-28）

这一节共 **5 个小节**，是整篇综述里对我们最有价值的部分——它把"落地"拆成了具体的技术选择。

### 1) 轻量 CNN + 自监督（Light weight CNN and SSL）

- 场景：**用手机麦克风做杂音筛查**；
- 引 **Ma 2023**：轻量 CNN + SSL（无标签预训练），参数量约为早期 DL 模型的 **1/10**；SSL 提升精度与鲁棒性、**减少收敛所需 epoch**；
- 形态：**手机 App 内推理 + 端上微调**。

→ 对应我们的 17（Ma）。

### 2) 轻量 1D CNN 的架构原则

原文给的"瘦身"四条：

1. **不用复杂部件**（enhancer / extractor / 残差块）——干净数据上浅层 CNN 就够；
2. **小卷积核**（SqueezeNet 的经验：在固定滤波器预算下，小核显著减参数）；
3. **尽早池化**，把进入全连接前的特征压小；
4. 具体配置：输入 **2000 点** → 3 组卷积-池化（核 **20**、滤波器 **16/8/4**、池化 **4**）→ **FC 128** → softmax 5；总计 **20,193 参数**。

→ 这就是 Ma 的模型，被综述当作"轻量 1D CNN 的范式"来复述。

### 3) 手机原型（Prototype smartphones）

- Keras → **TensorFlow.js** → **PWA（渐进式 Web 应用）**；
- UI 用 Nuxt.js + BootstrapVue；
- App 内置 **Yaseen 的 100 条样本**，用于**端上推理与微调**。

→ 工程实现细节，可参考。

### 4) 近期轻量化方法（Recent lightweight PCG methods）

- 核心手段：**深度可分离卷积（depthwise separable）** 与 **分组卷积（group convolution）**，替代传统卷积以降乘加；
- 结构顺序：depthwise conv → maxpool → pointwise conv（1×1）→ conv；
- 依据 MobileNet（Howard）的经验：**参数与运算都显著下降**。

→ 这是"减 FLOPs"最直接的手段，比"减层数"更有效。

### 5) 智能可穿戴传感器（Intelligent wearable sensors）

**这是全篇最值得抄的一段**——它给出了一个真实的 MCU 方案：

| 项 | 参数 |
| --- | --- |
| 麦克风 | **MEMS 数字麦克风** |
| MCU | **STM32F405RGT6**（ARM Cortex-M4F，带 FPU）|
| 内存 | **192 KB SRAM / 1 MB Flash** |
| 供电 | 300 mAh 锂电池 + LDO 2.5 V |
| 推理 | 神经网络**跑在 MCU 上**，预处理也在 MCU 上 |

原文还强调：相比 **ASIC / FPGA**，MCU 方案**开发成本低、模型可更新**。

同一节还夹了一段**多模态（ECG+PCG）**：z-score 归一化、**1.28 s 窗 / 0.64 s 步长**滑窗切段、多尺度特征优化（MFO）、**渐进式分组卷积**。

### 对我们项目的映射

| 综述里的做法 | 我们怎么用 |
| --- | --- |
| STM32F405（192 KB SRAM / 1 MB Flash）跑 CNN | **直接支持我们 ESP32-S3 的目标是现实的**——同类 MCU 已有人跑通；S3 的 512 KB SRAM 更宽裕 |
| 深度可分离 + 分组卷积 | A2（2D CNN）若要压 FLOPs，优先换 depthwise separable，而不是砍层数 |
| 早池化 + 小核 + FC 缩小 | 和 Ma 的 20k 参数思路一致，可用于 A3 |
| 手机 PWA + 端上微调 | 若将来做演示，比"只上板"更易展示 |
| 1.28 s 窗 / 0.64 s 步长 | 可作为我们 2 s / 1 s 的对照设置（他们的窗更短）|
| 内置 100 条样本用于端上微调 | 端上微调在 MCU 上不现实，但我们可以在 PC 上模拟 |

### 一处要注意

这一节**大量复述 Ma 2023（17）**，本身没有新实验；STM32 那段也没给出**模型大小、推理时延、准确率**。所以它证明的是"这条路有人走通过"，**不是"这条路有多快/多准"**。

## 我认为重要的点（原话/你的理解）

- **（你还没有转述这篇的原话。）** 你转述时我逐字记在这里，不改写。

## 与我的 FYP 的关系

- 可用之处：
  1. 作为**比 04 更新的 VHD 地图**：写引言可以说"截至 2025 年，VHD 的 PCG 分类仍以 CNN/DNN 为主，轻量与可穿戴是公认方向"；
  2. 它列的**局限**能对上我们 plan §1 的四条限制（数据、来源、许可、单标签）；
  3. **"丢弃低质量信号会夸大指标"** 可作为我们报告质量门的依据。
- 不能用的地方：
  - **Table 1–5 是图片**，正文提取不到表格数字；引用具体准确率必须回原始文献；
  - **全篇没有出现 "Yaseen" 这个名字**（只在摘要提了一次 "Github"），无法通过它定位 Yaseen 五分类工作；
  - **没有参数量的横向汇总**——"轻量"只是定性提及，不能当证据；
  - 它是**综述**，数字都是转引，不能当一手证据。

## 与 04 的分工

| | 04（Zhao 2024）| 25（Kannan 2025）|
| --- | --- | --- |
| 范围 | 全部心音 DL（99 篇）| **只 VHD**（199 篇）|
| 强项 | 数据集与模型横向表 | **更聚焦、更新、含可穿戴/嵌入式讨论** |
| 弱项 | 无部署维度 | 表格是图片、无参数量汇总 |

→ 写"相关工作"可**并用**：04 提供数据集与模型的横向对照，25 提供更新的方向判断与落地讨论。

## 待查证

- 199 篇的纳入/排除计数（PRISMA 流程图在文中，数字需从图里读）。
- Table 2 是否包含 Yaseen/Github 五分类条目（表是图片）。
- "轻量"相关陈述除 Ma 外还引了哪些具体工作。
