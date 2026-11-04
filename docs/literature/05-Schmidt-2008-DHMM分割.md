# 05 · Schmidt 2008 · DHMM 分割

> ⚠️ **读的是会议版，不是 README #05 的期刊版**：
> - 本笔记：Schmidt SE, Toft E, Holst-Hansen C, Graff C, Struijk JJ. *Segmentation of Heart Sound Recordings from an Electronic Stethoscope by a Duration Dependent Hidden-Markov Model.* **Computers in Cardiology 2008;35:345–349**（4 页，开放获取）。
> - README #05 指的是同一组的期刊版 *Physiol. Meas.* 2010;31(4)，**closed，未获取**。
> - 两版的方法与结论大体同源，但**数字不能互相替代**；引用期刊版时必须回期刊版。
>
> 阅读深度：全文（2026-10-24）。

## 引用

- 作者：SE Schmidt, E Toft, C Holst-Hansen, C Graff, JJ Struijk（Aalborg University / Aalborg Hospital）
- 标题：Segmentation of Heart Sound Recordings from an Electronic Stethoscope by a Duration Dependent Hidden-Markov Model
- 期刊/会议：Computers in Cardiology 2008;35:345–349
- 年份：2008
- DOI/链接：doi:10.1109/CIC.2008.4749049；PDF <http://cinc.org/Proceedings/2008/pdf/0345.pdf>
- 全文位置：`docs/refs/05-schmidt-2008-cinc.pdf`

## 数据

- 来源：Aalborg 医院心内科，**床旁**录制（多人病房），受试者为转诊做冠状动脉造影的患者
- 设备：**3M Littmann E4000** 电子听诊器；位置：**左侧第 4 肋间、胸骨旁**
- 规模：**100 人**，训练 **N=40**、测试 **N=60**
- 每条：**8 s**、16-bit、**4,000 Hz**
- 排除：**心律失常、以瓣膜病为主、噪声极大**的录音（测试前排除）
- 标注：人工分成 **4 个状态**——S1、收缩期静音（siSys）、S2、舒张期静音（siDia）
- 是否有患者 ID：有（按人划分训练/测试，**患者级不跨集**）

## 方法

- 预处理：**4 阶 Butterworth 带通 25–400 Hz** → **同态包络**（homomorphic envelogram）→ 用包络的 **97% 分位数**归一化
- 模型：**DHMM（Duration-dependent HMM）**，4 状态、**非遍历**（状态只能按固定顺序出现）
  - 状态转移概率 $a_{ij}$ + 每个状态的**时长分布** $p_j(d)$（这是与标准 HMM 的唯一区别）
  - 用**前向算法**递推，记录每步的最优 (d, i)，最后回溯
- 参数估计（关键设计）：
  - S1/S2 的包络均值、方差、**时长分布**：因个体差异小，**用 40 条训练集固定**
  - **收缩期/舒张期时长**：个体差异大，**每个受试者单独估计**，方法是用包络自相关，取 lag 0 到第一个明显峰的间距
- 评测：检测到的声音中心与人工标注中心**相距 <60 ms** 记为正确；开头 2 s 与结尾 1.5 s 不参与统计；**Wilson 区间**给 95% CI
- 对照：**标准 HMM**（同一包络输入）

## 结果（原文数字，Table 1）

| 模型 | 敏感度（95% CI）| 阳性预测值（95% CI）|
| --- | --- | --- |
| **DHMM** | **99.3%**（98.4–99.7）| **99.1%**（98.1–99.5）|
| 标准 HMM | 59.5%（56–63）| 55%（51.6–58.4）|

- 744 个 S1/S2 中识别出 **739** 个；60 条录音里 **57 条完全没有漏检**
- 「放错」的声音 7 个（>60 ms）
- 标准 HMM 的典型错误：**噪声尖峰被当成 S1/S2**
- 注意：**这是"事件定位"指标，不是分类指标**；和 01/03 的 accuracy 不是一回事

## 2.2 节（DHMM 模型构建）解读（2026-10-24 应你要求）

### 1. 任务形式化

把心音逐帧看成一串观测 $O_1,\dots,O_T$（本文的观测 = **同态包络的幅度**）。真实状态 $q_t$ 是隐藏的，取值只有 4 个：`S1 / siSys / S2 / siDia`，并且**只能按这个顺序循环**（非遍历，non-ergodic）。目标是求最可能的状态序列：

$$Q^*=\arg\max_Q P(Q\mid O,\lambda)$$

### 2. 与标准 HMM 的唯一区别：显式时长

标准 HMM 的转移概率 $a_{ij}=P(q_{t+1}=S_j\mid q_t=S_i)$ **与"已经在这个状态待了多久"无关**。这会造成一个几何分布式的隐含时长——大多数马尔可夫过程"待 1 帧就走"的概率最大，而心音的 S1（约 100–150 ms）、收缩期（约 300 ms）都有明确的生理时长。

本文给每个状态加一个**时长分布** $p_j(d)$，转移概率变成"在状态 $j$ 待满 $d$ 帧后离开"的概率。这就是 DHMM 与 HMM 的全部差别。

**Fig. 2 的例子**：收缩期在 220 ms 处有一个噪声尖峰。标准 HMM 会因为尖峰幅度像 S2 而**提前跳到 S2**；DHMM 因为"收缩期才过了 220 ms、远没到典型时长"，把这次跳转压下去。

### 3. 前向递推（Eq. 3–5）

记 $\delta_t(j)$ 为"**状态 $j$ 恰好在第 $t$ 帧结束**"的概率。假设它从 $t-d$ 开始、由状态 $i$ 转来，则：

$$\delta_t(j,i,d)=\underbrace{\delta_{t-d}(i)}_{\text{前一段的结束概率}}\cdot\underbrace{a_{ji}}_{\text{转移}}\cdot\underbrace{p_j(d)}_{\text{时长}}\cdot\underbrace{\prod_{s=0}^{d-1} b_j(O_{t-s})}_{\text{这 }d\text{ 帧由 }j\text{ 生成的概率}}$$

$$\delta_t(j)=\max_d\max_{i\ne j}\ \delta_t(j,i,d)$$

同时对 $d$ 和 $i$ 取最大，并**记录**取到最大值的 $(d,i)$；最后从末尾**回溯**得到整条状态序列。

- $b_j(O_t)$：状态 $j$ 生成观测 $O_t$ 的概率（本文用包络的均值/方差描述，**按高斯理解是合理推测，原文只写"observation probability distribution"**）。
- 复杂度：暴力枚举所有 $Q$ 是 $N^T$ 级；前向递推是 $O(T\cdot N\cdot D)$（$D$ 为时长范围），这是它能实时跑的原因。

### 4. 参数怎么来（2.3 节，和模型配套）

| 参数 | 估计方式 | 原因（原文） |
| --- | --- | --- |
| S1/S2 的包络均值、方差、时长分布 | **40 条训练集固定** | 归一化后个体差异小 |
| 收缩期/舒张期时长分布 | **每个受试者单独估**（包络自相关，lag 0 到第一个明显峰）| 心率个体差异大 |

### 5. 为什么标准 HMM 只有 59.5%

原文的归因：HMM 把**噪声尖峰误当成 S1/S2**。DHMM 靠时长分布约束住了这种跳转，所以 99.3%。注意这是**同一份包络、同一套数据**的对照，属于干净的比较。

### 6. 对我们实现的三点含义

1. 观测只用了**一维包络**，不需要 CNN；4 状态 + 时长分布的前向递推在 ESP32-S3 上算力压力很小（但**尚未实测**）。
2. 需要估的参数很少：4 个状态的时长分布 + 均值/方差，可全部离线算好烧进固件。
3. 它排除了瓣膜病；我们的数据里杂音会落在 siSys/siDia 内，**时长分布会被杂音拉长**——这是直接拿来用会踩的坑。

### 2026-10-24 你的判断

> 所以说这个文章检测S1、S2依赖的是健康人的数据，而健康人siSys和siSia是没有声音的，所以我这个FYP用不上

**逐字保留，未改写。** 三处要分开看：

- **对的部分**：模型确实把两个静音期当作状态（`silent systole (siSys)`、`silent diastole (siDia)`），观测是包络幅度；瓣膜病杂音会填进这两段，抬高幅度。**作者自己也承认**：`Heart valve disease may cause an amplitude increase in the diastolic or systolic periods. Therefore estimates of the diastolic and systolic amplitude distributions could be erroneous`。
- **不对的部分**：受试者**不是健康人**。原文：`from patients referred for coronary arterial angiography`，排除的是 `arrhythmia, dominating heart valve disease or extremely noisy recordings`——即**疑似冠心病的患者**，只是把"以瓣膜病为主"的人排掉了。
- **需要区分"方法"与"参数"**：DHMM 的形式（4 状态 + 时长分布）本身不排斥杂音，**不迁移的是它训练出来的幅度分布与时长分布**。理论上可以在瓣膜病数据上重训；但**没有文献验证过**这条路在我们这份数据上可行，属于"候选"，不是"已知可行"。

另外一层更根本的：**我们的五分类计划里没有 S1/S2 分割这一步**（`plan-5class-vhd.md` §2 只有解包 → 端点检测 → 带通 → z-score → 2 s 切窗）。所以 DHMM 用不上，**不是因为瓣膜病，而是因为流程里根本没有这一步**；它只和旧方向 MVP 的心率显示（ADR-008 / `docs/s1s2-detector-baseline.md`）相关。

## 我认为重要的点（原话/我的理解）

- **（你还没有转述这篇的原话。）** 你转述时我逐字记在这里，不改写。

## 与我的 FYP 的关系

- 可借鉴：
  1. **包络 + 97% 分位数归一化**是轻量、可解释的前端，比我们现在的 z-score 更针对"S1/S2 幅度"；
  2. **时长分布只用训练集固定、收缩/舒张时长按人估**这个折中，正好对应我们"不能做患者级划分"的处境；
  3. 4 kHz 采样与 plan 的 4 kHz 一致；评测用 **±60 ms 容差 + Wilson CI**，比我们现在的 macro F1 更贴近"定位"任务。
- 可改进 / 必须警惕：
  - **它明确排除了瓣膜病患者**（原文：`did not include patients with arrhythmias or heart valve disease`），并解释瓣膜病会使收缩/舒张期幅度升高，"estimates of the diastolic and systolic amplitude distributions could be erroneous"。**我们的 5 类全是瓣膜病（加正常）**——所以 99.3% 不能直接搬过来；
  - 带宽 **25–400 Hz** 与 ADR-002 的 **20–1000 Hz** 不同；
  - 只有 4 个状态、无杂音建模；瓣膜病杂音会落在 siSys/siDia 里。
- 不能用的部分：
  - 不能引它的 99.3% 当作"我们的 S1/S2 检测器应达到的水平"；
  - 不能把它当期刊版（2010）引用——两版是不同的出版物。
- **仓库现状（仅指认）**：`docs/s1s2-detector-baseline.md` 目前用的是"包络 + 间期规则"，**既没引 DHMM 也没引这篇**；我们 ADR-008 的 0.80 门槛与这里的 99.3% 之间差了一个数量级的任务难度（他们是干净 4 状态、排除瓣膜病）。

## 待查证

- 期刊版（Physiol. Meas. 2010;31(4)）与本会议版的差异：样本量、指标、是否纳入瓣膜病——需拿到期刊版才能写"Schmidt 2010 报告 X"。
- 2010 版的 DOI 与页码（README 只写 31(4)）。
- 原文的"homomorphic envelogram"具体实现（倒谱低通？）只给了引用 [11]，没给参数。
