# 工作流每一步的证据对照（2026-10-29）

> 规则：**每一行要么能指到文献编号 + 具体数字/原话，要么明确标注"无文献依据（E4 工程判断）"**。
> 文献编号见 `docs/literature/README.md`；数字出处见各篇笔记。

## 一、数据层

| 决策 | 文献依据 | 具体数字/原话 | 等级 |
| --- | --- | --- | --- |
| 主数据集 = Yaseen | **01** | 1,000 条，5 类各 200，8 kHz，3–5 s | 一手 |
| 五分类单标签 | **01** | 类别为 N/AS/MS/MR/MVP | 一手 |
| 无患者 ID → 录音级分组 | **T1 汇总** | Yaseen 论文均未提供患者编号 | 一手（数据本身）|
| CirCor 仅作无标签预训练 | **04**（Table 1）+ **17** + **18** | CirCor 5,272 条 / 1,568 人、含患者元数据；17 用 SSL 在 Yaseen 上 98.65→99.435；18 在 100% 标签下无增益 | 一手 + 事实 |
| 不做 CinC2016 外部验证 | **21**、**26**、**27** | 三家做外部验证都从 99% 掉到 **87.31 / 90.74 / 93.1**，且任务变二分类 | 一手 |

## 二、预处理

| 步骤/参数 | 文献依据 | 具体数字/原话 | 等级 |
| --- | --- | --- | --- |
| **2 kHz 采样** | **20** | `resampled at 2 kHz ... preserves important heart sound frequency components ... lowers the computational cost` | **一手（明确给理由）** |
| 同上 | **15** | 30 Hz 高通 + **降采到 2,000 Hz** | 一手（事实）|
| 同上（不取 1 kHz）| **22** | 异常成分（clicks/snaps/murmurs）常在 **500–600 Hz**；1 kHz 的 Nyquist=500 Hz 会砍在带边 | 一手 |
| 同上（1 kHz 可行性）| **27** | 1 kHz、1.5 s，五分类 **99.4%**、52.67k 参数 | 一手（无理由）|
| **带通下限 20 Hz** | **15** | 8 阶 Butterworth **高通 30 Hz** | 一手（邻证）|
| **带通上限 1000 Hz** | **22**、**23**、**27** | 22 指异常成分到 600 Hz；23 用 20–150 Hz 仍 98.6%（不可外推）；27 用 20–400 Hz | 一手（推理：保留 >600 Hz 需要 >1 kHz 上限）|
| **z-score** | **21** | MODWT 平滑 + **z-score 归一化** | 一手 |
| 端点检测（能量阈值）| **无** | 27 用 Springer 分割、23 用 TF patch；**没有文献用纯能量端点** | **E4 工程判断** |
| 切窗 2 s / 1 s | **17** | 1 kHz、**2 s 窗**、98.65% | 一手 |
| 1.5 s 对照臂 | **27** | **1.5 s** 分段，99.4% | 一手 |
| 不用 1 s 窗 | **24** | 定长 **1 s**（Yaseen 约 3 s，丢两个心动周期）| 反例 |

## 三、实验臂

| 臂 | 文献依据 | 具体数字 | 等级 |
| --- | --- | --- | --- |
| **A1** MFCC + SVM/LR | **01** | MFCC+DWT，SVM 97.9 / DNN 92.1 / kNN 97.4 | 一手 |
| A1 也可用 MFCC 单独 | **26**、**13** | 26 用 MFCC 17×13；13 用 13 维 MFCC | 一手 |
| **A2** log-mel + 2D CNN | **14** | 128 维 log-mel、243×128 输入、10 层 CNN | 一手 |
| A2 变体可用 PSD | **24** | 功率谱图（窗 2048 / hop 812）| 一手 |
| **A3** 1D 波形 + 1D CNN | **17**、**20**、**27** | 17：20,193 参数、98.65%；20：2 kHz 原始波形、0.67M、99.6%；27：1 kHz、52.67k、99.4% | 一手 |
| **A6** SSL 四组 | **17**、**18** | 17：98.65→**99.435**（+0.78）；18：10% 标签 0.773→0.857，**100% 标签无增益**（0.930 vs ~0.925）| 一手 |
| 不做 A4（CWT）| **12** | GoogLeNet + CWT 224×224，未报参数量/时延 | 反例（成本不明）|
| 不做 A5（双谱）| **02** | 无全文，无法复现 | 缺口 |

## 四、评估

| 项 | 文献依据 | 具体问题/数字 | 等级 |
| --- | --- | --- | --- |
| 按录音分组 | **T1** | Yaseen 无患者 ID；所有 Yaseen 论文均随机划分 | 一手 |
| 嵌套 CV（内层选超参）| **12**、**16**、**26** | 12 无独立测试集；16 `selected ... based on the performance metrics in the test dataset`；26 的 17×13 在全量 10 折上选 | 反例 |
| 15% holdout 只测一次 | **12**、**16** | 同上；另有 11 按 validation+test 选超参 | 反例 |
| macro F1 + CI | **T4** | 22 篇里几乎无 CI；19/23/24 只报点估计 | 反例 + 方法学 |
| 增强只在训练折内 | **19** | 先增强（1,000→15,000）再 10 折，98.48→**99.87** | 反例（一手）|
| 质量门必报覆盖率 | **26** + **25** | 26：Yaseen BER **707.07**、PhysioNet **24.96**、"unsure" **2.06**，低 BER↔高误分类；25：`rejecting low-quality ... could exaggerate genuine test features` | 一手 + 警告 |

## 四之二、评估六项的依据与强度（2026-10-29 补）

| 项 | 最硬的依据 | 具体内容 | 强度 |
| --- | --- | --- | --- |
| **① 评估单位（窗级+录音级）** | **17** 的切窗反例 + 数据事实 | 17 用 2 s 不重叠切窗 + 随机 10 折，而 Yaseen 约 2.4 s → 同录音的窗可能跨折；**所有用 Yaseen 的论文都无患者 ID**（T1）| **强（一手）** |
| **② 录音级聚合（soft voting）** | **无文献** | 29 篇里**没有一篇报告录音级聚合方式** | **弱（纯 E4）** |
| **③ 显著性（配对 bootstrap）** | **01**、**23 vs 24**、**26** | 01 的 97.9 vs 97.4 无 std/CI；23 与 24 差 0.279 点 ≈ 2,000 次预测里 5.6 个样本，二项 CI ±0.46 / ±0.51 大幅重叠；26 的 17×13 网格跨度 1.2 点、无重复 | **中**（"需要检验"=一手；"用哪种检验"=方法学）|
| **④ 指标（F1 族 + 混淆 + kappa）** | **20**、**21**、**01** | 20 逐类表：N 类 1.000，四病变类 97.8–98.4 → 难在病变之间；21 报 Cohen's kappa 97.87%；01 报 accuracy（92.1/97.9/97.4）无 CI | **强（一手）** |
| **⑤ 种子（集成 + 报 std）** | **26**、**17** | 26 网格无重复；17 维那一行 99.6 与 98.9 并存（同一配置内差 0.7 点）| **中**（反例一手；做法=E4）|
| **⑥ 拒识（训练折分位数 + 覆盖率）** | **26** 的 BER 数字 + **25** 的原话 | Yaseen BER **707.07**、PhysioNet **24.96**、"unsure" **2.06**、极端 **0.0001**；`lower BER ... higher misclassification`；25：`rejecting low-quality ... could exaggerate genuine test features`；12 无测试集、16 在测试集上调参 | **强（一手）** |

**汇总强弱**：⑥ 拒识、④ 指标、① 单位 = **强**；③ 检验、⑤ 种子 = **中**；② 聚合 = **弱（纯 E4）**。

**两处必须如实写进论文的地方**

1. **soft voting 不是文献做法**——写"本文采用概率平均聚合"，**不能写"文献常用"**；
2. **录音级评估在 Yaseen 上无先例**——是补空白，也意味着**不能与 17/20/27 的数字直接比较**，只能作臂间内部对照。

## 五、部署

| 决策 | 文献依据 | 具体数字 | 等级 |
| --- | --- | --- | --- |
| 双轨（树莓派参考）| **26** | 树莓派 4：**1.87 ms / 1.4 s**（RTF≈0.0013）| 一手 |
| 双轨（S3 部署）| **27** | STM32F405（192 KB SRAM）：**1.58 s / 1.5 s**、**245.1 mW**、52.67k 参数、59 kB SRAM | 一手 |
| 时延指标用 RTF | **27**、**26**、**20** | 27 RTF≈1.05；26 RTF≈0.0013；20 仅报 54.6 ms（平台未写）| 一手 + 反例 |
| 先 PTQ 再 QAT | **06** | ResNet50 INT8 量化后 **76.4→74.9**（掉 1.5 点）| 一手（量化论文）|
| 上板精度单列 | **27** | 上板是"简化版模型"（删一个分组卷积、调 LSTM），掉 **3.3/3.8 点** | 一手（反例）|

## 六、证据缺口（**没有文献支持，属工程判断**）

| 项 | 现状 | 怎么处理 |
| --- | --- | --- |
| 端点检测（能量阈值）| 无文献 | 标注为工程实现；可用 27 的 Springer 分割做对照臂（若时间允许）|
| 15% holdout 比例 | 无文献规定 | 写"工程选择"，敏感性可试 10%/20% |
| 拒识阈值（BER 或 SNR）| 26 只给相关性，未给阈值 | 必须**在训练折内**用 ROC 或分位数定阈值，报覆盖率曲线 |
| PTQ 掉点阈值 2 点 | 06 给的是 1.5 点（ResNet50）| 引用 06 作为参考，2 点作为我们的宽松上限 |
| 2 kHz 在真实录音上的精度 | 20 用的是 Yaseen 本身 | 必须做 2 vs 4 kHz 对照实验（ADR-029 失败判据）|

## 七、论文里可直接用的三句

1. **采样率**：`PCG signals were resampled to 2 kHz, following CardioXNet (Shuvo et al., 2021), which showed that 2 kHz preserves the relevant heart-sound components while lowering computational cost; 1 kHz was avoided because abnormal components extend to 500–600 Hz (Torre-Cruz et al., 2023).`
2. **评估**：`All splits were grouped at the recording level and hyperparameters were selected only within the inner folds, because prior work that tuned on the test set (Ding et al., 2023; Makimoto et al., 2022) reported optimistically biased results.`
3. **部署**：`Accuracy was reported separately for the floating-point reference implementation (Raspberry Pi) and the quantised deployment (ESP32-S3), since prior work reported a 3.3–3.8 point drop after model simplification for hardware (Zhu et al., 2024).`
