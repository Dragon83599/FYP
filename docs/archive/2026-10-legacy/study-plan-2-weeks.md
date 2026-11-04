# 未来两周学习计划（2026-10-20 → 11-02）

> ⚠️ 2026-10-23 方向调整：任务已改为 Yaseen 数据集的五分类瓣膜病（见 ADR-020 与 `docs/plan-5class-vhd.md`）。
> 本文的**方法与原理部分仍然适用**，但具体的 CirCor 数字只作为旧方向的对照。

> 背景：组会老师强调"以工程师角度考虑速度/大小/功耗"，要能讲清原理，而不是"向 AI 许愿"。
> 目标不是写新代码，而是**把已经做过的事讲到能回答追问**。
> 每天投入约 1.5 小时，两周共约 20 小时。

## 1. 两周后的验收标准

能不看稿回答下面四类问题，且每个答案都能指出一个仓库里的数字：

1. **模型**：为什么是这个结构？参数怎么来的？感受野多大？
2. **训练与评估**：为什么按患者划分？AUROC 和 F1 分别回答什么？
3. **量化与部署**：INT8 为什么掉点？为什么 TCN 掉得更多？为什么最后选小 CNN？
4. **工程取舍**：为什么不上树莓派？为什么不做 S1/S2？功耗怎么估的？

## 2. 第一周：把"我们自己的东西"讲透

不读论文，只读仓库 + 自测。

| 天 | 主题 | 具体动作 | 产出 |
| --- | --- | --- | --- |
| D1 | 系统全貌 | 读 `README.md` + `docs/handoff-2026-10-19.md`，画一张自己的系统图 | 手绘系统图 1 张 |
| D2 | 数据 | 读 `docs/engineering-decision-register.md` 的 ADR-009/013；打开 `data/processed/circor/splits.csv` 看划分 | 能解释"为什么患者级" |
| D3 | 模型结构 | 读 `tools/ai/hs_cnn.py` 的 `Small1DCNN`；手算感受野 | 感受野计算过程 |
| D4 | 训练与评估 | 读 `tools/ai/cross_validate.py` 的报告逻辑；跑一次 `scripts/reproduce.sh --fast` | 能解释 5 折与阈值来源 |
| D5 | 校准与工作点 | 读 `data/reports/workpoint-summary.md`；理解 ECE/Brier/敏感度/特异度/F1 | 能解释"0.80 门槛没守住" |
| D6 | 量化 | 读 `data/reports/int8-cv-v2/summary.md`；理解 scale/zero-point | 能解释 0.0937 → 0.0191 |
| D7 | 复盘 + 自测 | 做 `docs/principle-self-test-30.md` 的前 15 题，不会的标出来 | 错题清单 |

## 3. 第二周：文献 + 模拟问答

| 天 | 主题 | 材料 |
| --- | --- | --- |
| D8 | 数据集论文 | CirCor 原论文（见第 4 节） |
| D9 | 评测方法 | PhysioNet/CinC 2016 challenge 论文 |
| D10 | S1/S2 分割 | Schmidt 2010（我们的失败部分） |
| D11 | 量化原理 | Jacob 2018（INT8 整数推理） |
| D12 | QAT | Krishnamoorthi 2018（为什么 TCN 需要 QAT） |
| D13 | 综述与定位 | "A Comprehensive Survey on Heart Sound Analysis in the Deep Learning Era" |
| D14 | 模拟答辩 | 做后 15 题；录一段 5 分钟的系统讲解，回放找卡壳处 |

## 4. 文献清单（6 篇，按阅读顺序）

> 第 6 篇（Health Data Science 2024 综述）优先级可以提前：它同时覆盖数据集、技术、临床应用，是写论文相关工作章最快的一篇。

| # | 论文 | 为什么读 | 读的时候找什么 |
| --- | --- | --- | --- |
| 1 | Oliveira et al., *The CirCor DigiScope Dataset: From Murmur Detection to Murmur Classification*, IEEE JBHI 2022 | 我们用的数据集 | 标注流程、murmur 与 outcome 的定义、官方 baseline 的指标口径 |
| 2 | Liu et al., *Classification of Heart Sound Recordings: The PhysioNet/Computing in Cardiology Challenge 2016* | 心音评测的经典设定 | 为什么有 Noisy 类、评分怎么算、和我们的差异 |
| 3 | Schmidt et al., *Segmentation of heart sound recordings by a duration-dependent hidden Markov model*, Physiol. Meas. 2010 | 我们 S1/S2 检测器的同类方法 | 状态机怎么定 S1/S2、为什么高心率容易错 |
| 4 | Jacob et al., *Quantization and Training of Neural Networks for Efficient Integer-Arithmetic-Only Inference*, CVPR 2018 | INT8 量化的标准参考 | scale/zero-point、per-channel 与 per-tensor 的差别 |
| 5 | Krishnamoorthi, *Quantizing deep convolutional networks for efficient inference: A whitepaper*, arXiv 1806.08342 | 为什么 PTQ 不够、QAT 怎么做 | 量化误差来源、QAT 的训练流程 |
| 6 | **Zhao Q, Geng S, Wang B, et al. *Deep Learning in Heart Sound Analysis: From Techniques to Clinical Applications*. Health Data Science. 2024;4:0182. doi:10.34133/hds.0182**（[PMC11461928](https://pmc.ncbi.nlm.nih.gov/articles/PMC11461928/)） | **本领域最新、最贴我们项目的综述**：99 篇原始研究、数据集表、技术流程、临床应用分类 | ① 数据集表（CirCor 在哪、别人用什么）；② 技术流程（预处理→特征→模型）；③ 临床应用 6 类；④ 局限与未来方向；⑤ 可引用的数字（见下）|

> 阅读方式：每篇只读**摘要 + 方法 + 结果表**，30–40 分钟；写 5 行笔记：
> ① 它解决什么问题 ② 用了什么数据 ③ 关键指标与数字 ④ 和我们的异同 ⑤ 能引用到论文哪一章。

## 5. 每日节奏模板（1.5 h）

```
30 min  读（论文或代码）
45 min  动手：跑一个命令 / 手算一个数 / 画一张图
15 min  写笔记：3 句话 + 1 个仍不懂的问题
```

**关键**：每天留"一个仍不懂的问题"，第二天先解决它。两周下来就是 14 个问题的答案。

## 6. 两个必须产出的东西

1. **一页原理速查**（A4）：系统图 + 关键数字表 + 五个"为什么"；
2. **六篇文献笔记**：每篇 5 行，最后汇成 1 页"我们的工作与文献的关系"。

## 7. 自测方法

`docs/principle-self-test-30.md` 有 30 题。用法：

- 先自己答，再对照"标准答案"；
- 答不出来的题，回到对应文件看 10 分钟，再答一次；
- **不要背答案，要能用自己的话讲**——老师问的是理解，不是原文。

## 8. 如果时间不够

优先级：**第 1 周 > 第 2 周的前 4 篇 > 后 2 篇**。
其中第一周的第 3、5、6 天（模型结构、校准、量化）是老师最可能追问的三块。
