# 引用追踪结果（2026-10-30）

> 方法：用 OpenAlex 查**引用 17（Ma 2023）、26（Lai 2025）、27（LEPCNet）** 的论文，筛出涉及端侧部署的。
> 结论：**"MCU 只有 27 一篇"这个判断需要更正**——至少还有 1 篇 STM32 工作，外加 1 个新平台。

## 一、种子与引用数

| 种子 | OpenAlex ID | 被引次数 |
| --- | --- | --- |
| 17 Ma 2023 | W4388336701 | **30** |
| 26 Lai 2025 | W4415650356 | 0（太新）|
| 27 LEPCNet | W4387197161 | **26** |

## 二、筛出的候选（8 篇）

| # | 文献 | 年份 | 平台 | 任务 | 证据强度 | 判定 |
| --- | --- | --- | --- | --- | --- | --- |
| **A** | Ultraefficient Compressed PCG Classification on a **Custom Embedded Neural Accelerator**（IEEE JIoT）| 2025 | **STM32 + Neural-ART 加速器** | PCG 分类（**CirCor**）| 摘要级：compressive sensing + DL，未给数字 | **必读**（MCU 第二篇）|
| **B** | Wearable PCG-Based **Edge** Detection of Heart Valve Disorders Using **Tiny Physics-Informed NN**（IEEE TIM）| 2026 | 可穿戴 | **瓣膜病 + 边缘** | 无摘要（订阅）| **必读**（任务最贴近）|
| **C** | Mobile Phone-Based Heart Sound Detection via **Multifeatures Fusion**（IEEE TIM）| 2026 | **手机** | 心音检测（Yaseen 五类 + CinC2016 + 自采）| 摘要：三数据集 99.63 / 98.18 / **99.88**（Yaseen）| **必读**（手机第二篇）|
| **D** | **AI Heart Sound Denoising at the Edge**（IEEE IWASI）| 2025 | **RPi 4B + Qualcomm RB3 Gen 2** | **去噪**（不是分类）| 摘要级：U-Net/Spleeter 架构，两平台实测 | 参考（新平台）|
| **E** | A Lightweight CNN **Hardware Implementation** for Wearable Heart Sound Diagnosis（Circuits Syst Signal Process）| 2026 | 专用硬件 | 分类 | 无摘要 | 待读 |
| **F** | Efficient Training and Energy Saving Using **Ternary Hardware Acceleration** for PCG（IEEE LES）| 2025 | **三值逻辑 + CNFET**（研究性）| 二分类 | 摘要级 | 不适用（非实用硬件）|
| **G** | **Agent-guided pruning and quantization** strategy for lightweight heart sound classification（BSPC）| 2026 | 未说明 | 分类 + 剪枝/量化 | 无摘要 | 待读（与量化线相关）|
| **H** | PCG Classification Through **Averaging Transforms and Domain-Balanced Cross-Mix**（IEEE TCE）| 2026 | 未说明 | 分类 | 摘要级：**按心动周期切、周期标签继承自录音** | 参考（评测口径）|

## 三、四条对现有结论的更正/补充

### 1. **MCU 层级不再只有 27 一篇**

| 文献 | 平台 | 任务 |
| --- | --- | --- |
| 27 LEPCNet | STM32F405 | Yaseen 五分类 |
| **A（JIoT 2025）** | **STM32 + Neural-ART** | 压缩感知 + 分类（CirCor）|

→ "MCU 上做 PCG"已有两篇，但**仍是全领域最稀疏的一格**（对比 SBC 的 8 篇）。

### 2. **手机层级出现了第二篇**

**C（TIM 2026）**：内置麦克风 + 两级滤波 + 多特征融合（MBCF-Net），在 **Yaseen 五类**上报告 **99.88%**（高于 17 的 98.65%）。
→ **更正**：我此前误读为"约 88%"，实际是 99.63 / 98.18 / 99.88 三个数字。手机层级**不是精度更低**，而是**还没有同口径比较**。

### 3. **出现了一个新平台：Qualcomm RB3 Gen 2**（D）

它和树莓派一起做端上**去噪**，可以作为我们"三轨"之外的备选参考——但**去噪 ≠ 分类**，只能作旁证。

### 4. **H 的评测口径值得借鉴**

它按**心动周期**切分，并把**录音级标签继承到周期**——这正是我们"窗级 → 录音级"的做法，可作旁证（但同样没有患者级分组）。

## 四、下一步：三类动作

| 动作 | 对象 | 成本 |
| --- | --- | --- |
| **取全文精读** | A（JIoT 2025）、C（TIM 2026）、B（TIM 2026，可能拿不到）| 1–2 小时 |
| **只核摘要/表格** | D、H | 20 分钟 |
| **暂不追** | E、F、G | — |

## 五、检索经验（下次自己找时用）

1. **引用追踪比关键词准得多**：27 的 26 篇引用里，一次就捞出 3 篇真正端侧的；
2. **IEEE 期刊是主战场**（TIM / JIoT / TCAS / LES），但**摘要常被墙**——用 OpenAlex 的 `abstract_inverted_index` 能绕过；
3. **关键词"edge"+"PCG"** 在 2025–2026 出现频率明显上升，说明这个方向正在变热（对我们是好事，也是压力）。

---

## 六、反向追踪（41、42 引用了谁）——2026-10-30 补

方法：取 41（31 条引用）与 42（48 条引用）的 `referenced_works`，解析并按"端侧/平台"关键词过滤。

### 筛出的 4 条新线索

| 文献 | 年份 | 内容 | 判定 |
| --- | --- | --- | --- |
| **DS-CNN: Dual-Stream CNN for Wearable Devices**（IEEE TCE）| 2023 | 摘要原话 `can be considered for deployment on smart wearable devices` | **声称部署，无实测** |
| **Towards Smartphone-Based Heart Sound Classification**（SPCOM）| 2024 | 50 人（20 正常 + 30 病理）手机采集 | 待查（可能有端上结果）|
| **Detecting Heart Anomalies Using Mobile Phones**（IEEE BIBE）| 2019 | 开发了**手机 App**，可用数字听诊器或手机麦克风输入 | 待查（早，方法简单）|
| **FPGA Design of Digital Circuits for PCG Pre-Processing**（IEEE DSD）| 2024 | FPGA 做**包络提取等前端预处理**，再送 CNN | **前端加速，不是分类** |

### 三条判断

1. **"声称部署"是普遍的**：DS-CNN 是典型——摘要只写"can be considered for deployment"，没有时延、内存、掉点。这类论文**不能进我们的对比表**（用检索指南里的五个检查点筛）。
2. **FPGA 再次出现在"前端"**：DSD 2024 与 TIM 2024 都是把**分割/预处理**硬化——印证"FPGA 硬化前端、分类留给软件"的规律。
3. **两跳之后出现闭环**：41/42 引用的端侧文献，基本都是我们已知的（27、LEPCNet、手机质量研究、FPGA 前端）。**说明这个方向的文献已经基本收全**。

### 结论：检索到此可以停

| 层级 | 篇数（含本轮） | 是否还有未发现的 |
| --- | --- | --- |
| 手机 | 2–3 | 低（多为"App 开发"类，无掉点/时延）|
| SBC | 8 | 低 |
| MCU | **2** | 中（可能有 2026 新作，但需订阅）|
| FPGA | 3（含 DSD 2024）| 低（都是前端）|

**下一步不是继续搜，而是：① 想办法拿到 41 与 TIM 2026 的全文（学生可走学校 IEEE 订阅）；② 把"声称 vs 实测"作为一条筛选纪律写进文献表。**
