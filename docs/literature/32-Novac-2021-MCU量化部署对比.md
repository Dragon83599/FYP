# 32 · Novac 2021 · MCU 上的推理引擎对比

> 阅读深度：摘要 + 方法/结果段（Europe PMC 全文 PMC8122998）；未逐节精读。
> 用途：证明"**平台/引擎之间要实测比较**"，并给出可借用的比较维度。

## 引用

- 作者：Pierre-Emmanuel Novac, Ghouthi Boukli Hacene, Alain Pegatoquet, Benoît Miramond, Vincent Gripon
- 标题：Quantization and Deployment of Deep Neural Networks on Microcontrollers
- 期刊/会议：**Sensors 2021;21(9):2984**
- DOI：10.3390/s21092984；PMC8122998

## 它做了什么

- 提出 **MicroAI** 框架：训练 → 量化 → 部署一条链；
- 支持 **float32 / int8 / int16**；
- 在 **两块 Cortex-M4F MCU** 上实测：**STM32L452RE** 与 **Ambiq Apollo3**；
- 与 **两个现有推理引擎**对比：**TensorFlow Lite for Microcontrollers** 与 **STM32Cube.AI**；
- 数据集：UCI-HAR、Spoken MNIST、GTSRB（**无 PCG**）。

## 结果里对我们最有用的三点

1. **比较维度是"精度 / 内存 / 功耗"三者**——与 MLPerf Tiny 的"精度/时延/能耗"一致；
2. **8-bit 不一定优于 16-bit**：在 Spoken MNIST 上，8-bit 在"精度 vs 内存"的比值上并不比 16-bit 好；
3. **工具链差异会影响可比性**：
   - 原文指出 **TFLite Micro 不自带测量工具**（`does not come with tools to measure metrics such as the inference time or the RAM and ROM usage`）；
   - **STM32Cube.AI 自带测试应用**，可直接给 inference time、ROM、RAM。

## 我认为重要的点（原话/你的理解）

- （尚未转述）

## 与我的 FYP 的关系

**能用的**：
1. **部署比较必须实测**，且至少要报"精度 + 内存 + 时延"——支撑我们 spike 的设计；
2. **引擎选择会影响结果**：同一模型在 TFLite Micro 与 STM32Cube.AI 上表现不同 → 我们报数时必须写明**引擎与版本**；
3. **8-bit 未必划算**：我们的 PTQ 若掉点，可尝试 16-bit（而不是直接上 QAT）作为中间方案。

**不能用的**：
- 它的任务是 HAR/SMNIST/GTSRB，**不是 PCG**，不能引用其数字到我们身上。

## 待查证

- 具体内存/功耗数字（表格未逐项提取）。
- MicroAI 与我们工具链（esp-dl / TFLite Micro）的兼容性。
