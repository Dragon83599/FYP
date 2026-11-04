# 03 · Jain 2022 · DWT + 轻量 1D-CNN

> 阅读深度：全文（2026-10-24），5 页，含 Table I–III 与 Fig. 5/6。
> 数字均标页码/表号；PDF 在 `docs/refs/03-jain-2022-icetci.pdf`。

## 引用

- 作者：Puneet Kumar Jain（NIT Rourkela）, Ravi Raj Choudhary, Mamta Rani Singh（Central University of Rajasthan）
- 标题：A Lightweight 1-D Convolution Neural Network Model for Multi-class Classification of Heart Sounds
- 期刊/会议：ICETCI 2022（IEEE 会议；原报告给的是 <http://hdl.handle.net/2080/3735>）
- 年份：2022
- DOI/链接：会议论文，PDF 内未见 DOI；以 handle 链接为准
- 全文位置：`docs/refs/03-jain-2022-icetci.pdf`

## 数据

- 数据集：**公开数据集 [25] = Yaseen 那 1,000 条**（同 01；本文只写 "publicly available dataset [25]"，没写作者名）
- 规模：1,000 条，**5 类各 200**（AS / MR / MS / MVP / N）
- 采样率：原始 **8 kHz** → **降采到 1 kHz**
- 时长：原文写 `varies from 1.15 to 3.99 seconds`（与我们本地实测 1.16–3.99 s 基本一致；01 论文本身没给秒数）
- 划分：`randomly split into train (70%) and test (30%)`——**录音级随机划分，无患者分组、无 GroupKFold**
- 是否有患者 ID：无（数据集本身没有）

## 方法

- 预处理三步（p.2）：
  1. **降采样** 8 kHz → 1 kHz（理由是 FHS 与病理音都 <500 Hz）
  2. **归一化**：`x_norm(n) = x(n) / max(|x|)`——**峰值归一化，不是 z-score**
  3. **重定长**：识别 onset/offset 后用 Matlab `imresize`（**bicubic 插值**）拉伸到 **2800 样本**（≈2.8 s @1 kHz）
- 特征：**DWT，coif5，5 级分解**
  - 各子带（1 kHz 下）：D1 250–500、D2 125–250、D3 62.5–125、D4 31.25–62.5、D5 15.56–31.25、A5 0–15.56 Hz
  - 5 个细节 + 1 个近似系数**直接拼成 1-D 数组，长度 2942**
- 模型（Table I）：1 输入层（2942）+ Conv1[1,5]×16 + Pool1[2,16] + Conv2[1,5]×8 + Pool2[2,8] + FC[1,5]×1 + Softmax
  - 卷积/池化都 padding 保持尺寸（"same"）
  - **没有给参数量**
- 训练（p.3）：**50 epochs × 9 iterations = 450 iterations**，lr **0.01**，batch **64**，**SGD + momentum**
- 软件：Matlab R2020，Core-i9（10 核）/ 32 GB

## 结果（原文数字）

- 总体（摘要 + 结论）：**accuracy 98.9%**、sensitivity **99.01%**、specificity **99.72%**
- 每类（Table II）：

| 类 | 敏感度 | 特异度 | 精确率 | F1 |
| --- | --- | --- | --- | --- |
| AS | 1.0000 | 0.9956 | 0.9773 | 0.9885 |
| MR | 0.9839 | 1.0000 | 1.0000 | 0.9919 |
| MS | 0.9800 | 1.0000 | 0.9800 | 0.9899 |
| MVP | 0.9818 | 0.9953 | 0.9818 | 0.9818 |
| N | 1.0000 | 0.9952 | 0.9833 | 0.9916 |

- 原文自述：`All five classes are classified with an F-score of above 98.18%`；最低是 **MVP 的 F1 98.18%**，最低精确率是 **AS 的 97.73%**
- 对比表（Table III，原文转引）：Yaseen 2018 **97.6**、Ghosh 2019 **95.13**、Oh 2020 **97.0**、Ghosh 2020 **98.54**、本文 **98.90**
- 混淆矩阵：**只有 Fig. 5 的图**，无数值表

## Introduction 里的方法清单（2026-10-24，应你要求整理）

原文这一段：`In the literature, various methods have been proposed to classify heart sound based on classical machine learning techniques as well as deep learning techniques [1], [5].`（p.1–2）

| 引用 | 特征 / 输入 | 模型 | 任务 | 原文给的数字 |
| --- | --- | --- | --- | --- |
| N mei et al. [6] | wavelet scattering transform（时频）| SVM | 正常 vs 异常 | 未给 |
| Q Mubarak et al. [7] | 时域特征（均值、心音/心动周期标准差比、峰度、分形维数、Hjorth 参数）+ DWT 系数 | SVM | 定位 S1/S2（分割）| 未给 |
| [8] | one-dimensional binary patterns | SVM、KNN | 多类 | 未给 |
| S K Gosh et al. [9] | chirplet 变换 | multi-class composite classifier | 疾病识别 | 未给 |
| [10] / [11] / [12] | —（未展开）| decision tree / random forest / discriminant analysis | — | 未给 |
| S L Oh et al. [15] | 小波类 | deep wavenet | 多类 | 未给 |
| S B Shuvo [16] | —（未展开）| **CardioXNet**（轻量 DL）| 五分类 | **98.2%** |
| [17] | —（未展开）| **CNN + Bi-LSTM** | 五分类 | **99.3%** |
| N Baghel et al. [18] | —（未展开）| CNN | 五分类 | **98.6%** |
| [19] | variable-Q 变换 + hybrid constant-Q 变换 + MFCC | 五层 CNN | 五分类 | 未给 |
| [20] / [21] | —（未展开）| DL | 二分类（正常 vs 异常）| 未给 |
| [22] / [23] | —（未展开）| DL | S1/S2 分割 | 未给 |

- **有数字的只有三处**：[16] 98.2%、[17] 99.3%、[18] 98.6%，全部是"五分类 + accuracy"，**没有敏感度/特异度、没有划分方式**。
- **这一段完全没说每个方法用的哪个数据集**（Table III 也只给指标、不给数据集的划分细节）→ 想横向比较必须回各篇原文。
- 作者对 [17] 的两点批评（原文）：`performed k-fold cross-validation instead of a separate train and test dataset`、`does not exploit the multi-domain analysis due to which a complex model was developed`。
- 作者的总体判断（原文）：传统 ML `significantly varies according to the selected features`，且 `low generalisation capability`；SVM 因为有非线性核而 `produced promising results`。
- **待查证**：[8] 的 "one-dimensional binary patterns" 是否为 1D-LBP；[6] 的 "N mei"、[16] 的 "S B Shuvo" 姓名缩写是否与原文献一致。

## Table III 的对比方法（2026-10-24 应你要求整理）

Table III 本身只给数字，方法要从**参考文献标题 + 正文**补齐。对应关系如下：

| Table III 行 | 引用 | 方法（依据）| 类别数 | Table III 结果 |
| --- | --- | --- | --- | --- |
| Yaseen et al. 2018 | [30] | MFCC + DWT 特征 → SVM / DNN / KNN（01 已读全文）| 5 类 | 逐类 sens/spec；总 **97.6** |
| S.K. Ghosh et al. 2019 | [11] | 时频**幅值与相位**特征（IEEE Sensors Letters 标题）| **4 类（无 MVP）** | 只给每类 accuracy：AS 96.77 / MR 90.55 / MS 89.77 / N 98.55；总 **95.13** |
| Shu Lih Oh et al. 2020 | [15] | **deep wavenet**（小波 + 深度网络）| 5 类 | 总 **97.0** |
| S.K. Ghosh et al. 2020 | [9] | **chirplet 变换 + multiclass composite classifier** | **4 类（无 MVP）** | 总 **98.54** |
| Proposed | — | coif5 五级 DWT → 2942 维 → 1D-CNN | 5 类 | 总 **98.90** |

**四点必须注意**（全部可从 Table III / 参考文献核对）：

1. **两行 Ghosh 是 4 类**（AS/MR/MS/N，**没有 MVP**），它们的 overall accuracy 与 5 类**不可比**；但作者把它们和 5 类结果放在同一张表里，并据此写 "superior performance"。
2. **对比行的数据集与划分全都没写**。全文只说明了本文自己的 `randomly split into train (70%) and test (30%)`；其余四行的数据来源、划分方式、是否交叉验证一律缺。
3. **引言与参考文献对不上**：正文说 `random forest [11]`、`discriminant analysis [12]`，但 [11] 实际是 Ghosh 的"时频幅值/相位"、[12] 是 Amit 的"聚类分析与分类"。
4. Yaseen 被记作 **97.6**，而 01 原文最好成绩是 **97.9**（SVM + MFCC+DWT）。引用时以 01 原文为准，97.6 可能是四舍五入或另一配置。

## 我认为重要的点（原话/我的理解）

### 2026-10-24 你标出的句子（已更正）

> 我画错句子了，应该是这句However, the classical machine learning
> techniques significantly varies according to the selected fea
> tures.

**逐字保留，未改写**（你的更正说明也一并保留）。这句在 **Introduction，p.2**，属作者观点。核对：

- **和 01 的数据高度吻合**（同数据集、同五折）：SVM 在 MFCC 91.6% → DWT 92.3% → 融合 **97.9%**；KNN 在 MFCC 80.2% → DWT 91.8% → 融合 97.4%，**跨度 17.2 个百分点**。所以"经典方法对特征选择非常敏感"这句，**在 01 上有直接支撑**。
- **但"只有经典方法如此"这个隐含对比撑不住**：01 里 DNN 同样随特征变化（86.5% → 87.8% → 92.1%，跨度 5.6 个百分点）。三个分类器都随特征变，只是幅度不同（KNN 17.2 > SVM 6.3 > DNN 5.6）。
- 语法：`techniques ... varies` 主谓不一致，应为 **vary**；引用时注意。
- **对我们的用处**：这句可以当我们 A1–A5"特征臂"设计的动机引文——它说的正是"特征选错，经典模型掉得很厉害"。但要配一句限定：DL 也并非对特征免疫。
- 需要注意：这是同分布测试集上的 accuracy 差异，**不是泛化能力**的差异。

### 2026-10-24 你的理解

> 可以写进，我觉得这句话给我的启迪是，我的任务是找到最好的特征提取+模型的组合

**逐字保留，未改写。** 这句话把任务从"归因"改成了"选优"。**2026-10-24 你随后选择 A（归因）**：五条臂全部报满、不挑赢家；部署选型另按预算约束决定。已记入 `plan-5class-vhd.md` §3.0 与 **ADR-022**。

### 2026-10-24 你第一次标错的句子（保留备查）

> 我觉得作者这里面说的一句话很有用：the classical methods have low generalisation
> capability than the recently emerged deep learning techniques.


> 我觉得作者这里面说的一句话很有用：the classical methods have low generalisation
> capability than the recently emerged deep learning techniques.

**逐字保留，未改写。** 核对：

- 位置：**Introduction，p.2**，原句完整为：`However, the classical machine learning techniques significantly varies according to the selected features. Moreover, the classical methods have low generalisation capability than the recently emerged deep learning techniques.`
- 语法：`low ... than` 不合法，应为 **lower ... than**；直接引用时要么照原样加 [sic]，要么改写。
- **这句是作者的主张，不是本文的实验结论**：全文没有"传统 ML vs DL"的对照实验，它自己只跑了一个 1D-CNN。所以它能作为"观点引用"，**不能**作为"证据引用"。
- **同一数据集上的反证**（我们自己读过的）：01 在 Yaseen 上，SVM 融合 **97.9%** > KNN **97.4%** > DNN **92.1%**；且 01 作者把 DNN 较差归因于**数据量不足**（`we have very limited amount of data available ... DNN requires thousands of files and features`）。
- 04 的相关论述也不站在"DL 一定泛化更好"这一边：它把**数据稀缺**列为第一问题，并说预处理"risks filtering out valuable information"。
- 还有一层：这句说的是 **generalisation**（对未见数据的泛化），而 01/03 报的都是**同分布测试集**上的 accuracy——两者不是同一个量。用 in-dataset 高分去支持"泛化更好"，逻辑上不成立。

## 与我的 FYP 的关系

- 可借鉴：
  1. **A1 的另一个参照点**：同为 Yaseen，MFCC+DWT 97.9%（01）vs DWT+1D-CNN 98.9%（本文）。差别不在"特征更强"，而在**模型从 SVM 换成了 CNN**。
  2. **它的 DWT 参数比 01 完整**：coif5、5 级、给出各子带频率边界——这是我们复现 DWT 可以直接抄的配置（01 缺小波族）。
  3. 它的 1 kHz 降采样理由（病理音 <500 Hz）可以引，但**要注意与 ADR-002 的 20–1000 Hz 带宽和 plan 里的 4 kHz 不一致**（我们是 4 kHz 抽取，它是 1 kHz）。
- 可改进（作者的局限）：
  - **随机 70/30，没有按录音分组**；窗口/片段若来自同一录音会泄漏（本文是整段定长，泄漏风险低于滑窗，但仍无分组）；
  - 只报一次划分的结果，**没有 k 折、没有方差/CI**；
  - **imresize 拉伸**会改变时间尺度与频率内容，作者把它当优点（"overcome the variation in the frequency of components"），但没有对照实验证明它比"补零/截断"更好；
  - 峰值归一化对异常尖峰敏感（我们用的是 z-score），两者的差别没有讨论。
- 不能用的部分：
  - 不能把 **98.9%** 当作"该数据集的上限"：它是随机 70/30 的单次结果，且与 01 的 97.9% 一样没有置信区间；
  - 不能引它证明"1D-CNN 优于 SVM"——两者是**不同论文、不同划分、不同特征**，属于并列比较，不是同一实验内的对照。

## 待查证

- **9 iterations/epoch × batch 64 ≈ 576 样本**，与"70% 训练集 = 700 条"对不上（700/64 ≈ 11）→ 是笔误、还是训练集另有规模？
- 正文只说 train/test，但 Fig. 6 画了 **validation** 曲线 → 验证集从哪来没写。
- 摘要的 **sensitivity 99.01%** 用 Table II 五类敏感度平均算是 **98.91%**，口径不明（加权？只算某几类？）。
- **参数量**：标题称 "lightweight"，但全文没给参数量/MACs → 对我们 ESP32-S3 的 ≤150k 预算**无法直接引用**，只能自己按 Table I 估。
- Table III 里 "Yaseen 2018 = 97.6" 与 04 综述的 92.1 不一致：**97.6 更接近 01 的最好成绩 97.9**（SVM），进一步印证 92.1 是 DNN 行。
