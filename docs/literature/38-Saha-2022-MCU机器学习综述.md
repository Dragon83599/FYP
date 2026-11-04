# 38 · Saha 2022 · 面向单片机级硬件的机器学习（综述）

> 阅读深度：**摘要级**（Europe PMC PMC9683383）。**只用来支撑"方法学"，不引用具体数字。**

## 引用

- 作者：Swapnil Sayan Saha, Sandeep Singh Sandha, Mani Srivastava
- 标题：Machine Learning for Microcontroller-Class Hardware: A Review
- 期刊：**IEEE Sensors Journal 2022**（DOI: 10.1109/jsen.2022.3210773）
- PMC9683383

## 它说了什么

- 单片机类设备的 ML 与服务器 ML 的**要求不同**：模型开发流程必须保证**算力与延迟在设备预算内**；
- 强调 **onboard ML**（板上推理）的独特约束：内存、功耗、确定性延迟；
- 给出的是一套**工作流**（从模型设计到部署），而不是某个任务的精度。

## 与我的 FYP 的关系

**能用的**：
- 为我们的"**部署前就定死结构（ADR-026，不裁剪）**"提供方法学依据：先按设备预算设计，而不是训练完再砍；
- 支持我们报告 **精度 + 时延 + 内存**三件套（与 31 MLPerf Tiny 一致）。

**不能用的**：
- 综述，无 PCG 数据，不能引用任何精度。

## 待查证

- 它对"MCU 上 int8 精度损失"的统计结论（若需要，可回读正文）。
