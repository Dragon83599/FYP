# 34 · Oliveira 2022 · CirCor DigiScope 数据集

> 阅读深度：全文（Europe PMC PMC9253493，CC-BY）；**这是我们做无标签预训练的数据源，第一次读它的原始论文**。

## 引用

- 作者：Jorge Oliveira, Francesco Renna, Paulo Dias Costa, Marcelo Nogueira, Cristina Oliveira, Carlos Ferreira, Alípio Jorge, Sandra Mattos, Thaís Hatem, Thaís Tavares, Andoni Elola, Ali Bahrami Rad, Reza Sameni, Gari D. Clifford, Miguel T. Coimbra
- 标题：The CirCor DigiScope Dataset: From Murmur Detection to Murmur Classification
- 期刊/会议：**IEEE J Biomed Health Inform 2022;26(6):2524–2535**
- DOI：10.1109/JBHI.2021.3137048；PMC9253493
- 全文：`docs/refs/34-oliveira-2022-jbhi.xml`（Europe PMC XML）

## 数据（原文数字）

| 项 | 值 |
| --- | --- |
| 录音数 | **5,282** |
| 患者数 | **1,568** |
| 年龄 | **0–30 岁**（儿科为主）|
| 时长 | **5–168 s** |
| 采样率 | **4 kHz** |
| 录音位置 | **4 个听诊区**（主动脉、肺动脉、三尖瓣、二尖瓣）|
| 人工标注心音数 | **215,780** |
| 标注内容 | 每个杂音的**时相、形态、音调、分级、性质**；以及出现位置与最响位置 |

> ⚠️ 04（Zhao 2024）的 Table 1 写 **5,272**，原文是 **5,282**——转引差 10 条，引用时以原文为准。

## 标签分布（原文 Table V，按患者计）

| 类别 | 人数 | 占比 |
| --- | --- | --- |
| **无杂音** | 1,144 | **73.0%** |
| **有杂音** | 305 | **19.5%** |
| **低质量（无法判读）** | 119 | **7.6%** |
| 合计 | 1,568 | 100% |

有杂音的时相分布（部分）：收缩早期 103（32.8%）、收缩中期 27（8.6%）、全收缩期 171（约 54%）等。

## 一条对本项目极有价值的原话

> `the informative spectra of the PCG is below 1 kHz (with its power spectrum mainly concentrated below 500 Hz) ... a sampling frequency of **2 kHz to 4 kHz is fully adequate** for all human- and machine-based heart sound diagnosis`

**这是我们"2 kHz 采样"最直接的一手依据**——比之前引 20（CardioXNet）更权威，因为这是数据集论文对频谱的专门讨论。

## 我认为重要的点（原话/你的理解）

- （尚未转述）

## 与我的 FYP 的关系

**能用的**：
1. **2 kHz 的权威依据**（见上），可直接写进方法章节；
2. **A6 无标签预训练的合法来源**：5,282 条（本地公开部分是 3,163 条 / 942 人），有患者 ID、有元数据；
3. **患者级分组**：这是 Yaseen 做不到的，CirCor 可以；
4. **质量门的现实依据**：7.6% 的录音被判"低质量"，说明**"无法判读"是真实存在的第三类**，不是我们臆想的。

**不能用的**：
- **没有 VHD 类型标签**（只有杂音有无与描述），不能用于五分类；
- 人群是 **0–30 岁儿科**，与 Yaseen（成人教学录音）有域差。

## 待查证

- 本地 3,163 条与原文 5,282 条的差异：是否只是"公开的 training 部分"（我们之前按 942 人推断是）。
- 官方训练/测试划分的边界（Challenge 版本是否另发标签）。
- 4 个听诊区的录音是否**同一患者多条**——这决定了按患者分组与按录音分组的差距有多大。
