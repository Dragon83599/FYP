# 37 · Zambrano 2026 · 8 位 Arduino UNO 上的 TinyML 心电分类

> 阅读深度：全文（Europe PMC PMC13203760，CC-BY）。
> **为什么读它**：PCG 端侧文献只有"手机/SBC/MCU"三类；这篇是**跨任务证据**——同样的技术（int8 + TFLM + 1D CNN）在**资源更差**的芯片上是什么表现。

## 引用

- 作者：Zambrano-de la Torre M 等（墨西哥）
- 标题：Real-Time Cardiac Arrhythmia Classification Using TinyML on Ultra-Low-Cost Microcontrollers: A Feasibility Study for Resource-Constrained Environments
- 期刊：**Bioengineering 2026;13(5):532**
- DOI：10.3390/bioengineering13050532；PMC13203760

## 关键数字（原文）

| 项 | 数值 |
| --- | --- |
| 平台 | **Arduino UNO**（8 位 ATmega328P，2 KB SRAM / 32 KB Flash）|
| 任务 | 心电三分类（正常 / 室性 / 室上性）|
| 模型 | **量化 1D-CNN**，TFLite Micro（TFLM）|
| 量化 | **INT8 训练后量化（PTQ）**，体积降 **约 75%**，精度损失 **<1%** |
| 精度 | **97.6%** |
| 内存 | **< 24 KB** |
| **推理耗时** | **约 200 ms / 每个心搏** |
| 数据 | MIT-BIH（48 条记录 / 47 人）|

## 我认为重要的点（原话/你的理解）

- （尚未转述）

## 与我的 FYP 的关系

**三条可直接引用的技术证据**：

1. **PTQ 的掉点可以 <1%**——支持我们"先 PTQ、掉点 >2 点才转 QAT"的策略（ADR-032 补充之三）；
2. **<24 KB 的 int8 模型能在 2 KB SRAM 的 8 位单片机上跑**——说明"模型小到几十 KB"是 MCU 的舒适区，我们的 A3（权重 ≈40 kB）在内存上不吃紧；
3. **200 ms/心搏** 说明即使是 8 位 Arduino 也能做到"近实时"——我们"RTF≤1.2"的目标在 S3 上不算激进。

**不能用的**：
- 它是 **ECG 三分类**，不是 PCG 五分类；**不能拿它的 97.6% 对比心音结果**；
- 它的 200 ms 只算**网络推理**，不含滤波与 R 波检测（原文明确说明）。

## 待查证

- 1D-CNN 的具体结构（层数/通道/核长）未在摘要级提取。
