# 14 · Shiraga 2023 · 多模态 PCG+ECG

> 阅读深度：全文（2026-10-26），Sensors 16 页，开放获取。
> 数字均标表号；PDF 在 `docs/refs/14-shiraga-2023-sensors.pdf`。

## 引用

- 作者：Takeru Shiraga, Hisaki Makimoto, Benita Kohlmann, Christofori-Eleni Magnisali, Yoshie Imai, Yusuke Itani, Asuka Makimoto, Fabian Schölzel, Alexandru Bejinariu, Malte Kelm, Obaida Rana（三菱电机 + 杜塞尔多夫大学）
- 标题：Improving Valvular Pathologies and Ventricular Dysfunction Diagnostic Efficiency Using Combined Auscultation and Electrocardiography Data: A Multimodal AI Approach
- 期刊/会议：**Sensors 2023, 23, 9834**
- 年份：2023（发表 12 月 14 日）
- DOI/链接：doi:10.3390/s23249834
- 全文位置：`docs/refs/14-shiraga-2023-sensors.pdf`（CC BY）

## 数据

- 训练/开发：**1,052 名患者**（均做过超声），三处听诊 + 12 导联 ECG
- 临床验证：**独立 103 名患者**（与训练集不重叠）
- 听诊位置：**2RSB（右第二肋间）、Erb 点、心尖**
- 目标：**重度 AS**、**重度 MR**、**LVEF ≤ 40%（LVD）**
- 划分：**改良分层 4 折交叉验证**（按病情严重度分层，4 组互不重叠，**测试集共用**）+ 独立 103 人验证
- 患者 ID：**有，按患者划分**

## 方法

- **PCG 预处理**：**不做标准化/归一化**（原文：`these procedures did not affect the efficiency of model training during the preliminary experiments`）
  - 切成 **4 s** 段（按病种严重度决定时间步）
  - **STFT**：Hann 窗、**512 采样窗、64 采样跳**
  - **128 维 Mel 滤波器组** + 取对数
  - 数据增强：SpecAugment（频率/时间掩码）+ mixup
  - 输入形状 **243 × 128**
- **模型**：**10 层 CNN**（ReLU + BN，第 4/6/9/10 块有 MaxPool，末层全局池化）
- **ECG**：同样用 CNN，分 12 导联 / 肢体导联 / 胸导联几种输入
- **融合**：**stacking**——单模态 CNN 的输出概率作为特征，用 **Random Forest / XGBoost** 再训练
- 模型选择：**用测试集 AUC 选模型**（原文 `The evaluation was performed using test data`）⚠️
- 统计：bootstrap 2,000 次 + ANOVA + Tukey-Kramer HSD

## 结果（原文数字）

### 单模态（测试集 AUC ± 标准差，Table 5–7）

| 目标 | PCG 最好 | ECG 最好 |
| --- | --- | --- |
| **AS** | **2RSB 0.974 ± 0.011**（Erb 0.947、Apex 0.909）| 12 导联 0.780 |
| **MR** | Erb 0.714（2RSB 0.597、Apex 0.629）| **胸导联 0.787** |
| **LVD** | Apex 0.824（2RSB 0.775、Erb 0.712）| **12 导联 0.904** |

### 融合（stacking）

| 目标 | 最好的融合 | AUC |
| --- | --- | --- |
| AS | XGB(PCG3ch + ECG2pt) | **0.974** |
| MR | XGB(PCG3ch + ECG2pt) | **0.757** |
| LVD | XGB(ECG2pt) | **0.925** |

- **AS 靠心音，LVD 靠 ECG，MR 两边都弱**——原文的"模态贡献"分析显示贡献是**病种特异**的。
- **临床验证（103 人）整体低于测试集**，其中 **LVD 下降最明显**（原文：`the decline in performance was particularly pronounced in the rEF detection models`）。

### 2026-10-26 你的判断

> 这文章主要讲的还是PCG和ECG共同判断疾病，其中我只看了PCG有关部分，他不做标准化，但是没说为什么，使用了Mel 滤波器组 + 取对数作为预处理，但是判断选择了10层CNN（我的条件应该不支持构建10层），并且他只判断了AS, MR, and LVD，感觉对我的fyp帮助不大

**逐字保留，未改写。** 逐条核对：

- ✅ **核心是 PCG+ECG 融合**：对。它的卖点是"模态贡献是病种特异的"。
- ⚠️ **"不做标准化没说为什么"**：**说了一句，但没给数据**。原文：`the heart sound data were not standardized or normalized, as these procedures did not affect the efficiency of model training during the preliminary experiments`——做过预实验，结论是"没影响"，但既没给数字也没给对比。
- ✅ **Mel 滤波器组 + 取对数**：128 维 log-mel，STFT（Hann、512 窗、64 跳），4 s 段，输入 243×128。
- ✅ **10 层 CNN 对我们的条件太重**：它没有报参数量；我们应参照 Ma 2023（**20,193 参数**），不是这篇。
- ✅ **只判 AS / MR / LVD**：准确说是**重度 AS、重度 MR、LVEF ≤ 40%**，三个独立二分类，不是五分类。

**但有一处你可能没注意：融合其实没有赢过最好的单模态。**

| 病 | 最好的单模态 | 最好的融合 | 谁赢 |
| --- | --- | --- | --- |
| AS | PCG1（2RSB）**0.974** | XGB(PCG3ch+ECG1pt) 0.974 | 平 |
| MR | ECG 胸导联 **0.787** | XGB(PCG3ch+ECG2pt) 0.757 | **单模态赢** |
| LVD | XGB(ECG2pt) **0.925** | XGB(PCG3ch+ECG1pt) 0.894 | **单模态赢** |

原文也承认：`determining the superior model among the various models was not conclusively possible due to the lack of statistical significance`。所以它真正证明的是"**不同病依赖不同模态**"，不是"融合更好"。

**还有一处数字对不上（值得记）**

- **摘要**写：AS **0.93**、MR **0.80**、LVD **0.75**；
- **Table 5–7** 里测试集最好成绩是：AS **0.974**、MR **0.787**、LVD **0.925**。

两组数不一致。从上下文推测摘要用的是**临床验证队列（103 人）**的结果（正文说验证集"generally inferior"），但**原文没有明说**。→ 引用时必须写清是测试集还是验证集，记入待查证。

**结论：对 FYP 的帮助确实有限，但可以拿三点**

1. **MR 从心音很难**（PCG 0.714，连 ECG 的 0.787 都比它高）——可当我们"MR 是最难类"的外部佐证；
2. **SpecAugment + mixup** 两种便宜的数据增强，可以搬到 A2；
3. 反面教材：**用测试集选模型** + **摘要数字与表格不一致**——提醒我们报告时统一口径。

## 我认为重要的点（原话/我的理解）

- **（你还没有转述这篇的原话。）** 你转述时我逐字记在这里，不改写。

## 与我的 FYP 的关系

- 可借鉴：
  1. **和我们的 A2 几乎同构**：log-mel（128）× CNN。差别在窗口长度（它 4 s、我们 2 s）和 CNN 深度（它 10 层）。
  2. **它明确说"不归一化也没影响"**——与 Ding 的标准化做法相反。这说明"要不要 z-score"是**任务相关**的，不能当教条；我们可以在 A1/A2 上各试一次。
  3. **多模态结论**：AS 几乎全靠心音（0.974 vs ECG 0.780），LVD 几乎全靠 ECG（0.925 vs 0.775）。**如果将来要扩到 LVD/心衰，光做心音不够**——这是很硬的一条边界。
- 可改进 / 注意：
  - **模型选择用测试集**（原文明确），所以 0.974 这些数字偏乐观；
  - 测试集是 4 折共用的，独立性有限；
  - 临床验证只有 103 人，且**性能普遍下降**——第三次见到"同分布→外部掉点"（前两次：04 的 Alkhodari 99.32→87.31、11 的 99.24→96.79）。
- 不能用的部分：
  - 不能把它的 AUC 和 Yaseen 上的 accuracy 横比（任务、类别、人群都不同）；
  - 不能引它支持"多模态一定更好"——MR 上融合只从 0.787 提到 0.757（反而更低），说明 stacking 不是稳赚。

## 待查证

- 4 s 段与"按严重度决定时间步"的具体对应关系（Table 3 只给了 ECG 的增强倍数）。
- stacking 的 RF/XGB 超参（只说 grid search 最大化 F1）。
- 临床验证的 103 人里三种病的例数分布（Table 2 有，但未逐一核对）。
- 它用的听诊设备型号与采样率（正文似乎未强调，需回原文确认）。
