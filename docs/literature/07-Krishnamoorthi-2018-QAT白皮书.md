# 07 · Krishnamoorthi 2018 · QAT 白皮书

> 状态：**待读**（2026-10-26）。全文已下载，未读。
> 用户 2026-10-24 表示"量化回来再说"，本篇暂缓。

> **阅读状态（2026-10-28）**：用户决定不读本文；本笔记只有目录与引用信息，**未通读，不可引用其内容**。

## 引用

- 作者：Raghuraman Krishnamoorthi（Google）
- 标题：Quantizing deep convolutional networks for efficient inference: A whitepaper
- 期刊/会议：arXiv 预印本
- 年份：2018（6 月）
- DOI/链接：arXiv:1806.08342；<https://arxiv.org/abs/1806.08342>
- 全文位置：`docs/refs/07-krishnamoorthi-2018-qat.pdf`（36 页）

## 读完前可先知道的（来自目录，不是内容结论）

| 章 | 内容 |
| --- | --- |
| 2 | 量化器设计：均匀仿射、均匀对称、随机量化；反向传播中如何模拟量化；**量化粒度**（per-tensor vs per-channel）|
| 3.1 | **训练后量化（PTQ）**：只量化权重 / 权重+激活 |
| 3.2 | **量化感知训练（QAT）**：算子变换、BatchNorm 处理、低比特网络 |
| 4 | 训练最佳实践 |

> 以上只是目录结构，**不是读后结论**；引用前必须通读对应章节。

## 数据 / 方法 / 结果

（未读，不填。）

## 我认为重要的点（原话/我的理解）

- （尚无。）

## 与我的 FYP 的关系

- 这是 06（Jacob 2018，INT8 只整数推理）的**配套**：06 讲推理侧的定点方案，07 讲训练侧怎么让模型适应量化（PTQ vs QAT）。
- ADR-007 系列已有"QInt8 + Percentile 99.99"的配方；这篇可以用来核对我们的做法与 Google 的标准流程差异。
- 待读时重点看：**per-channel 权重量化**、**BatchNorm 折叠**、**低比特（4 bit）** 是否与我们模型相关。

## 待查证

- 与我们当前工具链（TFLite / esp-dl）的量化实现是否一致。
- 白皮书里的数据集（ImageNet）与我们的心音任务的差异。
