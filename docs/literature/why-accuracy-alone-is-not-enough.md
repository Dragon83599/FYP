# 为什么不能只报准确率：19 篇文献里的 5 类陷阱（2026-10-26）

> 用途：写论文的"评估协议"或"相关工作"时，用本仓库**已读文献**里的具体案例，说明我们为什么采用
> 分组划分 + holdout + macro F1 + CI + 混淆矩阵。每条都能指到原文。

## 陷阱 1：同一结果，不同口径

| 文献 | 现象 | 数字 |
| --- | --- | --- |
| 01 Yaseen | 综述引的是 DNN 行，论文最好的是 SVM 行 | 92.1%（DNN）vs **97.9%**（SVM+融合）|
| 04 Zhao | 把 Ghosh 的 MHSMD 结果当成 Yaseen 结果 | 96.79%（MHSMD）vs **99.24%**（Yaseen）|
| 12 Ding | 摘要 overall 与正文"四类平均"混用 | 97.5%（overall）vs 98.75%（四类平均）|
| 14 Shiraga | 摘要 AUC 与表格 AUC 不是同一队列 | 摘要 0.93/0.80/0.75 vs 表格 0.974/0.787/0.925 |

**启示**：报数字必须写清"哪个子集、哪个平均方式"。

## 陷阱 2：划分与泄漏

| 文献 | 现象 | 影响 |
| --- | --- | --- |
| **19 Al-Issa** | **先增强（1,000→15,000）再 10 折** | 同一录音的增强副本跨折，98.48% → **99.87%** |
| 17 Ma | 2 s 不重叠切窗 + 随机 10 折（录音约 2.4 s）| 同录音的两个窗可能跨折；98.65%（SSL 后 99.435%）|
| 03 Jain | 随机 70/30，无录音分组 | 98.9% 无 CI |

**启示**：**先按原始录音分组，再在组内做增强**；我们的 GroupKFold 正是针对这一点。

## 陷阱 3：用测试集选模型

| 文献 | 原文 | 受影响的结果 |
| --- | --- | --- |
| 11 Ghosh | 超参按 validation **和 test** 的准确率选 | 99.23%（hold-out）|
| 12 Ding | 没有独立测试集，200 条"验证集"既调参又报结果 | 98.75% |
| 14 Shiraga | `The evaluation was performed using test data` | 0.974（AS）|
| 16 Makimoto | `selected ... based on the performance metrics in the test dataset` | 94% / F1 0.857 |

**启示**：调参只能在训练折内；最终测试只做一次。

## 陷阱 4：数据集内高分 ≠ 临床可用

| 文献 | 数据集内 | 外部/真实人群 |
| --- | --- | --- |
| 04（转引 Alkhodari）| 10 折 **99.32%** | CinC2016 **87.31%** |
| 11 Ghosh | Yaseen **99.24%** | MHSMD **96.79%** |
| 13 Waaler | — | AS AUC 0.979，但 **AR 0.634、MR 0.549** |
| 14 Shiraga | 测试集 0.974（AS）| 临床验证普遍下降，LVD 最明显 |
| 15 Chorba | 富集队列 AS 93.2/86.0 | 病例-对照设计，不能外推筛查 |
| 18 Soni | 内部 AUC 高 | 外部中心验证下降；作者归因**批次效应**（不同听诊器）|

**启示**：我们在 Yaseen 上的数字只能叫"数据集内性能"，必须与"临床可用"分开表述。

## 陷阱 5：指标定义不同，数字不可比

| 文献 | 问题 |
| --- | --- |
| 12 Ding | F1 定义为"敏感度与**特异度**的调和平均"，不是 precision/recall |
| 15 Chorba | 敏感度 76.3%；**去掉 1 级杂音后 90.0%**——取决于标签纳入标准 |
| 19 Al-Issa | 仅 LSTM 50.70% 被"CNN+LSTM 98.48%"掩盖 |
| 13 Waaler | 多标签二分类的 AUC，与五分类准确率不是一回事 |

**启示**：引用他人数字前先看定义；我们自己的指标要写公式。

## 我们的对策（对应 `plan-5class-vhd.md` §4）

| 陷阱 | 我们的做法 |
| --- | --- |
| 口径 | 所有表都标"5 类、macro 平均、测试折"；同时报 overall 与每类 F1 |
| 泄漏 | **GroupKFold（同一录音不跨折）**；增强只在训练折内做 |
| 选型 | 只在训练折内调参；另留 **holdout** 只在最后测一次 |
| 外推 | 只声称"数据集内性能"；外部验证列为未来工作 |
| 定义 | 写明 F1 = 每类 precision/recall 的调和平均，再 macro 平均 |

## 可直接用的论文措辞

> 中文：文献中报告的高准确率往往受三类因素影响：划分方式（随机切分导致的录音级泄漏）、模型选择（在测试集上调参）以及指标口径（不同定义的 F1、是否纳入轻度病例）。因此本研究采用按录音分组的交叉验证、独立 holdout、macro F1 与 bootstrap 置信区间，并同时报告每类 F1 与混淆矩阵。

> English: Reported accuracies in the literature are affected by three factors: split design (recording-level leakage from random splits), model selection (tuning on the test set), and metric definitions (differing F1 definitions and inclusion of mild cases). We therefore adopt recording-grouped cross-validation, an independent holdout set, macro F1 with bootstrap confidence intervals, and report per-class F1 together with the confusion matrix.
