# 组会 PPT 大纲（v7，2026-10-31）· 说服：这个 FYP 走 PWA

> **目标**：说服导师接受「走 PWA」。结构：**背景 → 约束 → 现状 → 三条理由（复现前 / 复现时 / 复现后）→ 总结（分数 + 验收）→ 改进 → 计划 → 参考文献**。
> **11 页**，13–15 分钟。正文**一律用「作者 + 年份」**，文献编号只在笔记里用。
> 数字可追到 `docs/literature/`、`docs/engineering-decision-register.md`（ADR-050/051/052）。

---

## 1. 标题页（含明确主张）

- 瓣膜病心音五分类的端侧实现
- 副标题：**我建议走 PWA，三条理由**

**一句话主张**

> **这个 FYP 应该用手机浏览器（PWA）做端侧，而不是树莓派或单片机。**
> 理由三条，按「复现前 / 复现时 / 复现后」排：
> **① 复现前成本最低（硬件 + 学习）；② 复现时最容易 1:1 复现；③ 复现后效果最好（精度不掉、时延够用）。**

---

## 2. 背景：心音五分类与数据集选择

**心音是什么**：瓣膜开合产生 **S1 / S2** 两个主音；瓣膜狭窄或反流会改变音色、强度与节律——这是分类的物理依据。

**五个类别（Yaseen 的标签定义）**

| 类 | 名称 | 中文 | 病理 |
| --- | --- | --- | --- |
| **N** | Normal | 正常 | 无瓣膜病变 |
| **AS** | Aortic Stenosis | 主动脉瓣狭窄 | 瓣口变窄、血流受阻 |
| **MS** | Mitral Stenosis | 二尖瓣狭窄 | 二尖瓣口变窄 |
| **MR** | Mitral Regurgitation | 二尖瓣反流 | 关闭不全、血液倒流 |
| **MVP** | Mitral Valve Prolapse | 二尖瓣脱垂 | 瓣叶脱垂 |

**数据集候选与选择**

| 数据集 | 规模 | 任务 | 我们的用法 |
| --- | --- | --- | --- |
| **Yaseen 2018** | **1,000 条（5 类各 200）**、8 kHz、单声道 | **单标签五分类** | **✅ 主数据集** |
| CirCor 2022 | **5,282 条 / 1,568 人**、0–30 岁、4 kHz | 杂音检测 + 分级 | 备选（任务不同，**但有患者 ID**）|
| PASCAL 2011 | 数百条 | **多标签** | 不用 |
| CinC 2016 | 约 3,000 条（**规模待查证**）、2 kHz | **二分类**（正常/异常）| 不用 |

**为什么选 Yaseen**

| 理由 | 说明 |
| --- | --- |
| **唯一「单标签五分类、五类齐全」** | 与我们的课题完全一致；其它数据集是二分类、多标签或杂音分级 |
| **类别均衡**（各 200）| 不需要重采样，指标不会被类别比例扭曲 |
| 社区基线多 | **Ma 等、Lai 等、Zhu 等都用它** → 有可比数字 |
| 规模可控 | 1,000 条，剩余三周内能跑完**五折交叉验证** + 5 个改进 |

**必须承认的局限**：**Yaseen 没有患者 ID**（1,000 条来自 CD 与 48 个网站，无法追溯到人）→ 只能做**录音级分组**，不能做患者级；这也是为什么我们的折按**录音**分组。

---

## 3. 先划清问题与约束

| 项 | 内容 |
| --- | --- |
| 任务 | **单标签五分类**（数据集见第 2 页）|
| 端侧要求 | 推理在设备上完成，不联网 |

**约束推出一个结论**：**这条路上，最大的成本不是算法，是「把模型搬到某个平台上并测出数字」。**

---

## 4. 三条路：谁做过、做到什么程度

| 方向 | 代表工作 | **模型参数量** | PC 端精度 | 端侧精度 | 端上时延 | 篇数 |
| --- | --- | --- | --- | --- | --- | --- |
| **手机 / PWA** | **Ma 等（2023）**、**Vu 等（2024）** | **20,193** | 98.65% | 98.65% | **0.03–0.37 s** | **2** |
| 单板机（SBC）| **Lai 等（2025）**（树莓派）等 | **312,357** | 86.9–99.94% | 多为同模型 float | 1.87 ms – **9 s** | **9** |
| 单片机（MCU）| **Zhu 等（2024）**、**Ragusa 等（2025）**、**Mai 等（2026）** | **3,058–52,670** | 91.9–99.4% | **90.9–95.6%** | 5.31 ms – **1.58 s** | **3** |

**一个必须先说清的事实**：**三类的模型规模差了两个数量级**（MCU 3k–53k、PWA 20k、SBC 312k）。
**文献里没有「参数相当、同任务、三平台齐」的一组**——所以任何"谁更快/更准"的跨平台结论，**都必须自己控制变量测**（W4）。

**三点现状**
1. **SBC 最挤但最参差**（86.9 → 99.94）——「能上端」不等于「做得好」；
2. **MCU 最少**，且**做过 Yaseen 五分类端上评测的只有 Zhu 等一篇、用了 1.58 s**；
3. **手机两篇**，但只有 **Ma 等**同时报了精度与时延。

> 两篇综述（**Zhao 等 2024**：纳入 99 篇；**Kannan 等 2025**：纳入 199 篇）**都没把「端侧 + 五分类 + 参数量」放在一起比**——这格是空白。

**对照选项：不引入端侧（维持现状）**
只在 PC 上跑模型——**零风险、零成本，但不满足「端侧推理」这一课题要求**，只能作基线，不能作交付。

---

## 5. 理由一（复现前）：PWA 的成本最低

**比的是「硬件钱 + 要学什么 + 搭环境 + 迭代方式」**

| 项 | 手机 PWA | 树莓派 4B | 单片机（ESP32-S3 / STM32 等）|
| --- | --- | --- | --- |
| **硬件单价** | **0** | ¥400–600 + 电源 | **¥30–60**（最便宜）+ 麦克风/屏 |
| **要学什么** | **浏览器 + JS/TF.js**（我已有 Web 基础）| Linux + Python + TFLite | **C/HAL + 量化工具链 + 烧写** |
| 搭环境 | **打开浏览器** | 装系统、配驱动 | 装工具链、建工程、接线 |
| 迭代方式 | **改一行刷新即见** | 分钟级 | 烧写 + 串口调试 |

**这一项比的是「投入」（硬件 + 学习 + 环境 + 迭代），不是硬件价**——**单片机省的是钱，花的是时间**。

**先澄清两件容易误会的事**

| 误会 | 事实 |
| --- | --- |
| 「单片机最便宜」 | **硬件确实最便宜（¥30–60）**，但**工期 2–4 天**——比 PWA 多花 3 天，**是最后两周可用时间的 20%** |
| 「学 C 更硬核」 | 我要的是**把模型跑出数字**，不是练工具链；**学习成本也是成本** |

**PWA 的 6 条优势（只讲这 6 条）**

| 优势 | 省掉了什么 |
| --- | --- |
| **零硬件成本** | 屏幕、算力都自带（**采集用队友设备**）|
| **无 PCB / 电源 / 电池 / 散热** | 整条硬件链不用碰 |
| **无固件工具链** | 不烧写、不接串口、不怕变砖 |
| **开发即真机、刷新即见** | 迭代以秒计 |
| **浏览器自带 UI + TF.js/WebGL** | 界面与 GPU 加速免费 |
| **便携：手机随身带，无需额外设备** | 现场与随访都能用；不用准备开发板、屏幕、电源 |

**代价（必须说）**：JS 预处理要与 Python 逐点对齐，约 **1–2 天**。

---

## 6. 理由二（复现时）：基线结构最简单，最容易加新东西

**基线越简单，改动越容易归因**

| 项 | **Ma 等（2023）** | **Lai 等（2025）** | **Zhu 等（2024）** |
| --- | --- | --- | --- |
| 参数量 | **20,193** | 312,357 | 52,670 |
| 结构 | **3×(Conv 核 20 + Pool 4) + FC128 + softmax** | WCNN + **KWC 通道加权** + MFCC 维数网格搜索 | **分组卷积** + Springer 分割 + int8 |
| 改动落在哪 | **全在网络外围**：输入、表示、带宽、增强 | 特征层（MFCC）与网络结构**相互耦合** | 分割是**前置步骤**，动它要重跑对齐 |
| 加一个改动的代价 | **低**：只换输入或训练策略 | 中：要同时动特征与网络 | 高：分割与模型绑在一起 |
| 预处理链 | **3 步、端到端波形** | 整套 MFCC + BER 质量评估 | 分割 + 端到端 |

**两条读法**

| # | 读法 |
| --- | --- |
| 1 | **只有简单基线做得了"单轴"改进**：Ma 等只有 3 层卷积 + 1 层全连接，改采样率、换表示、加增强都**不碰网络内部**，差异才能干净地归到那一个变量上（这正是我们的判据）|
| 2 | **改进空间一眼看得清**：20,193 参数的模型，瓶颈在**输入信息与训练策略**，不在深度——所以五个改进全部落在外围，而不是重设计网络 |

> 反过来说：如果基线本身是"分组卷积 + 通道加权 + 分割"，**任何一个改动都要连带改好几处**，消融结论就不再干净。

---

## 7. 理由三（复现后）：PWA 的效果最好

**三篇同任务（Yaseen 五分类）的端上表现——正好代表三种部署策略**

| 项 | **Ma 等（2023）** | **Lai 等（2025）** | **Zhu 等（2024）** |
| --- | --- | --- | --- |
| 平台 / 精度制式 | 手机 PWA / **float32** | **树莓派 4B** / **float32** | STM32F405 / **int8 + 裁剪** |
| 参数量 | **20,193** | **312,357**（**15.5×**）| 52,670 |
| PC 精度 | 98.65% | **99.6%**（网格峰值，无 CI）| 99.4% |
| 端侧精度 | **98.65%** | **99.6%** | 95.6% |
| **掉点** | **0** | **0**（未量化）| **−3.8**（裁剪）|
| 端上时延 | **0.03–0.37 s** | **1.87 ms/窗**（是否含 MFCC 未说明）| 1.58 s |

**为什么说「效果最好」——三条读法**

| # | 读法 |
| --- | --- |
| 1 | **掉点只看量化与裁剪**：Ma 与 Lai 都跑 float32 → **掉点 0**；Zhu 上板前裁了模型 → **−3.8** |
| 2 | **精度可以用参数堆**：Lai 99.6% 是三篇最高，但模型 **312,357**（是 Ma 的 **15.5 倍**），且是**网格峰值、无置信区间** |
| 3 | **PWA 的性价比最高**：Ma 用 **1/15.5** 的参数量拿到 **98.65%**（只差 0.95 点），**时延 0.03–0.37 s 足够**，且**没有量化掉点** |

> 一句话：**平台决定「能不能跑、多快」，量化与裁剪决定「掉多少点」，参数量决定「精度上限」**——PWA 这一列在三者之间取得了最好的平衡。

**唯一的缺口是能耗**：手机接不了功率计，**不测不报**——手机天天充电、模型小、无功耗 KPI，所以不构成问题。
其余四项（时延 / macro F1 + 95% CI / 与 Python 一致性 / 峰值内存）都能测，**判据见第 8 页**。

---

## 8. 总结：三条理由指向同一个结论

| 阶段 | 一句话 | 关键证据（页）|
| --- | --- | --- |
| **复现前** | **成本最低** | 硬件 **¥0**、**半天**能上；单片机硬件最便宜（¥30–60）但**工期 2–4 天**，多花的三天 = 最后两周的 **20%**（第 5 页）|
| **复现时** | **最容易加新东西** | Ma 等只有 **3 层卷积 + 1 层全连接（20,193 参数）**，改动全在外围（输入/表示/带宽/增强）；Lai 等特征与网络耦合，Zhu 等分割是前置步骤（第 6 页）|
| **复现后** | **效果最好** | Ma 等**掉点 0**、时延 0.03–0.37 s；Lai 等 99.6% 但**参数 15.5 倍**；Zhu 等 **−3.8 点**（第 7 页）|

**折算成分数**（权重：成本 35 / 复现 30 / 自证 20 / 独特性 15）

| | **PWA** | 树莓派 | 单片机 |
| --- | --- | --- | --- |
| 四项得分（**综合成本**/复现/自证/独特）| 5 / 5 / 4 / 3 | 3 / 4 / 5 / 2 | 2 / 3 / 4 / 5 |
| **加权总分** | **4.50** | 3.55 | 3.15 |

> **「综合成本」不是硬件价**：单片机硬件最便宜（¥30–60），但**学习与调试成本最高**，所以成本这一项仍是 2 分（见第 5 页）。
> 单片机靠「独特性满分」拉回 0.75 分，**仍差 1.35**——这是我唯一犹豫的地方。

**结论**：**走 PWA**。若导师坚持要有硬件端侧：**次优树莓派 4B**（0.5–1 天、五项都能测），**最次 MCU**（2–4 天、失败概率高）。

**验收与回退（各一行）**
- **验收**：复现落 **98.65% ±1.5**；端上**中位 ≤1 s、P95 ≤2 s**、与 Python 概率差 **<1e-3**。
- **回退**：换机型重测 → 减窗长 1.5 s → 手机只做界面 + PC 推理 → 转树莓派。

> 一句话：**平台可以换，模型和评估不换。**

---

## 9. 五个单点改进（在 Ma 等的基础上）

| # | 改进 | 依据 | 预期（E4）| 成本 |
| --- | --- | --- | --- | --- |
| **I1** | 采样率 1 → **2 kHz** | Oliveira 等（2022：2–4 kHz 够用）+ Torre-Cruz 等（2023：异常成分 500–600 Hz）| +0.5~2 点 | 半天 |
| **I3** | **S1 对齐**（平移不重切）| Liu 等（2017：分割 F1 98.5）；Yin 等（2022）无消融 → **文献空白** | +0~2 点 | 1 天 |
| **I4** | **表示对比**（波形/MFCC/log-mel）| Shuvo 等（2023：同模型下 CWT 最优 +1.95）| +1~3 点 | 1 天 |
| **I7** | **训练折内增强** | Baghel 等（2020：**+2.37 点**）| +1~2 点 | 半天 |
| **I12** | **频带宽度** 20–1000 → 试 20–400/20–150 | Baghel 等（20–150）、Zhu 等（20–400）、Oliveira 等（功率谱 <500 Hz）| +0~1.5 点 | 半天 |

**判据**：单轴 + 配对 bootstrap CI 不含 0 且 **≥0.5 点**才纳入。

> **E4 = 工程估算**：按原理推的预期，**不是实测、不是文献结论**；做完实验后以实测为准。

---

## 10. 未来两周的计划

**交付物**：手机能跑 + 四项数字（时延中位/P95、macro F1 + CI、与 Python 一致性、峰值内存）。

| 周 | 做什么 | 验收 |
| --- | --- | --- |
| **W2** | PWA 跑通 + **PC 上复现 Ma 等** | 手机出结果；**98.65% ±1.5** |
| **W3** | 五个改进（**I1 → I7 → I12 → I3 → I4**）| 至少 **2 条**显著 |

> 余下时间（W4）：端上实测与演示打磨（时延中位/P95、一致性 <1e-3）；若 W3 延误，W4 用于收尾。

---

## 11. 参考文献

1. MA Y, et al. An edge-device-compatible algorithm for valvular heart diseases screening using phonocardiogram signals with a lightweight convolutional neural network and self-supervised learning[J]. **Computer Methods and Programs in Biomedicine**, 2024, 243: 107906.（在线 2023-11-04；doi:10.1016/j.cmpb.2023.107906）
2. VU T, et al. Detecting abnormal heart sound using mobile phones and on-device IConNet[EB/OL]. **arXiv:2412.03267**, 2024.
3. LAI C, et al. A Robust and Efficient Workflow for Heart Valve Disease Detection from PCG Signals: Integrating WCNN, MFCC Optimization, and Signal Quality Evaluation[J]. **Sensors**, 2025, 25(21): 6562.
4. ZHU J, et al. LEPCNet: A Lightweight End-to-End PCG Classification Neural Network Model for Wearable Devices[J]. **IEEE Transactions on Instrumentation and Measurement**, 2024, 73: 2511111.
5. RAGUSA D, et al. Ultraefficient Compressed Phonocardiogram Classification on a Custom Embedded Neural Accelerator[J]. **IEEE Internet of Things Journal**, 2025（doi:10.1109/JIOT.2025.3635785）.
6. MAI N-D, ULLAH K, CHUNG W-Y. Wearable PCG-Based Edge Detection of Heart Valve Disorders Using Tiny Physics-Informed Neural Networks[J]. **IEEE Transactions on Instrumentation and Measurement**, 2026, 75: 2515317.
7. OLIVEIRA J, et al. The CirCor DigiScope Dataset: From Murmur Detection to Murmur Classification[J]. **IEEE Journal of Biomedical and Health Informatics**, 2022, 26(6): 2524–2535.
8. TORRE-CRUZ J, et al. Detection of valvular heart diseases combining orthogonal non-negative matrix factorization and convolutional neural networks in PCG signals[J]. **Journal of Biomedical Informatics**, 2023, 145: 104475.
9. BAGHEL N, et al. Automatic diagnosis of multiple cardiac diseases from PCG signals using convolutional neural network[J]. **Computer Methods and Programs in Biomedicine**, 2020, 197: 105750.
10. SHUVO S B, et al. NRC-Net: Automated noise robust cardio net for detecting valvular cardiac diseases using optimum transformation method with heart sound signals[J]. **Biomedical Signal Processing and Control**, 2023（doi:10.1016/j.bspc.2023.105272）.
11. LIU C, SPRINGER D, CLIFFORD G D. Performance of an open-source heart sound segmentation algorithm on eight independent databases[J]. **Physiological Measurement**, 2017, 38(8): 1730.
12. YIN Y, et al. Design of Abnormal Heart Sound Recognition System Based on HSMM and Deep Neural Network[J]. **Medical Devices: Evidence and Research**, 2022, 15: 313–322.
13. ZHAO Q, et al. Deep Learning in Heart Sound Analysis: From Techniques to Clinical Applications[J]. **Health Data Science**, 2024, 4: 0182.
14. KANNAN R, et al. Detection of Valvular Heart Diseases From PCG Signals Using Machine and Deep Learning Models: A Review[J]. **IEEE Access**, 2025, 13.
15. YASEEN, SON G Y, KWON S. Classification of heart sound signal using multiple features[J]. **Applied Sciences**, 2018, 8(12): 2344.（数据集来源）
