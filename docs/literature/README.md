# 文献阅读笔记（索引）

> 用法：**每次读文献先看这一页**，再进对应的笔记文件。
> 新对话接手时，只需要读这一页 + 你正在读的那篇笔记。

## 约定

- 一篇文献一个文件：`docs/literature/NN-第一作者-年份-关键词.md`
- 每篇必须写清：**引用信息 / 数据 / 方法 / 结果 / 我认为重要的点 / 与 FYP 的关系 / 待查证**
- 数字必须来自原文，不能凭记忆；记不清就写"待查证"
- 凡是"老师/组会提到的观点"，单独记在 `docs/literature/insights-from-supervisor.md`

## 进度总表

| # | 文献 | 状态 | 关键结论 | 与我方向的关系 |
| --- | --- | --- | --- | --- |
| 01 | Yaseen, Son, Kwon 2018, Applied Sciences 8(12):2344 | 已读 | 自建 1,000 条（5×200，8 kHz）；MFCC 19 + DWT 24，SVM 融合 **97.9%**、DNN 92.1% | 数据 + A1 基线 |
| 02 | Alqudah, Alquran 2020, NetMAHIB, doi:10.1007/s13721-020-00272-5 | 待找 | 公开副本未获取（Springer closed）| 特征候选 |
| 03 | Jain, Choudhary, Singh 2022, ICETCI（已下载全文）| 已读 | coif5 五级 DWT→2942 维 + 1D-CNN；98.9% / F1 最低 MVP 98.18%；随机 70/30 | 主对标 |
| 04 | Zhao et al. 2024, Health Data Science 4:0182 | 已读 | 99 篇（VHD 13 篇）；kappa 0.3–0.48；Yaseen 上各法 92.1–99.32%，外部验证 87.31% | 领域地图 / 相关工作对照表 |
| 05 | Schmidt et al. 2008, CinC 35:345–349（期刊版 2010 未获取）| 已读（会议版）| DHMM 4 状态分割：99.3% 敏感度 / 99.1% PPV；**排除瓣膜病** | 预处理/分割 |
| 06 | Jacob et al. 2018, CVPR（pp. 2704–2713）| 已读 | INT8 只整数推理（per-tensor）；ResNet50 76.4→74.9；SSD 延迟↓最高 50% | 部署 |
| 07 | Krishnamoorthi 2018, arXiv:1806.08342 | 待读 | QAT | 部署 |
| 08 | 陈尧 2021, 四川大学 博士论文 | 待读（全文已获取）| 心音心电神经网络方法 | 背景 |
| 09 | 李婷 2015, 大连理工 博士论文 | 待找 | 循环平稳理论 | 背景 |
| 10 | 韩威 2020, 广东工业大学 博士论文 | 待读（全文已获取）| 小样本心音分类 | 背景 |
| 11 | Ghosh, Ponnalagu, Tripathy, Acharya 2020, BioMed Res Int 8843963 | 已读（PDF 已核）| SCT 时频 + 1200 维熵特征 → ELM-AE + KSRC；Yaseen **99.24%**，MHSMD **96.79%** | 特征候选 / 外部验证 |
| 12 | Ding et al. 2023, J Healthcare Eng 7382316 | 已读（PDF 已核）| 标准化 (x−μ)/σ + CWT(Morse) → 224×224 → GoogLeNet；overall **97.5%**、四类平均 98.75%；MVP 最弱（F1 94.46）| A4 对标 |
| 13 | Waaler et al. 2024, Front Cardiovasc Med 10:1170804 | 已读 | Tromsø 2,124 人；13 维 MFCC + 2 层 LSTM；AS **AUC 0.979**，AR **0.634**、MR **0.549**；按患者划分 | 真实队列对照 |
| 14 | Shiraga et al. 2023, Sensors 23:9834 | 已读 | 1,052 人；128 维 log-mel + 10 层 CNN，PCG+ECG stacking；AS AUC **0.974**（心音）、LVD **0.925**（ECG）、MR 仅 0.757 | A2 对标 / 多模态边界 |
| 15 | Chorba et al. 2021, JAHA 10(9):e019905 | 已读 | 训练 5,318 人 / 验证 962 人；34 层 1D ResNet（30 Hz 高通、2 kHz）；杂音 76.3/91.4%；AS 93.2/86.0%、MR 66.2/94.6% | 临床规模对照 |
| 16 | Makimoto et al. 2022, EHJ Digital Health 3(2):141 | 已读 | 556 人建模 / 132 人验证；log-Mel + CNN + 三位置集成；重度 AS 准确率 **95.7%**、F1 0.932 | 重度 AS 二分类 / 部署形态 |
| 17 | Ma et al. 2023, CMPB 243:107906 | 已读 | 轻量 1D-CNN **20,193 参数**；1 kHz / 2 s；Yaseen 10 折 **98.65%**，+SSL **99.435%**；手机 PWA 部署 | **端侧对标（核心）** |
| 18 | Soni et al. 2021, Patterns 2:100400 | 已读 | SimCLR 对比学习（ResNet-18）；心音 10% 标签下 0.773→0.857，**100% 标签下无增益**（0.930 vs ~0.925）| SSL 的边界证据 |
| 19 | Al-Issa & Alqudah 2022, Sci Rep 12:14297 | 已读 | Yaseen 五分类；CNN(48/32/16/64)+LSTM(64/32)；无增强 98.48%→增强 **99.87%**（**增强先于划分，疑泄漏**）；仅 LSTM 50.7% | 时域 vs 频域对照 / 反例 |
| 20 | Shuvo et al. 2021, CardioXNet, IEEE Access 9:36955 | 已读 | Yaseen 五分类端到端 CRNN；**99.6%**；**0.67M 参数 / 26M FLOPs / 7.96MB / 54.6ms** | 轻量对标（**04 漏收**）|
| 21 | Alkhodari & Fraiwan 2021, CMPB 200:105940 | 已读 | Yaseen 五分类 **99.32%**（10 折）；外部 CinC2016 **87.31%**（**二分类**，异常 TP 仅 **66.17%**）；3 层卷积 + BiLSTM；只取前 1.2 s | 外部掉点证据（注意任务变了）|
| 22 | Torre-Cruz et al. 2023, JBI 145:104475 | 已读 | **二分类**（200 正常 vs 800 异常），非五分类；ONMF（W 谱 / H 时间）+ 6 种 CNN；**H 比 STFT 高 35%（UjaNet）**；04 的"45%"其实是 H vs W | 表示路线对照（更正 04）|
| 23 | Baghel et al. 2020, CMPB 197:105750 | 已读 | 五分类 Yaseen；**7 层 CNN、279,525 参数**；10 折 **96.23%（不增强）→ 98.60%（增强）**；增强含变调/变速/平移/背景形变/加噪 | A2 对标 / 增强效应 |
| 24 | Khan et al. 2022, CMPB 219:106727 | 已读 | 五分类 Yaseen；**16 层卷积、9,514,921 参数**；STFT 功率谱（2048/812）；10 折 **98.879%** | 深模型对照（参数 34×、精度 +0.28）|
| 25 | Kannan et al. 2025, IEEE Access 13 | 已读 | 199 篇 VHD 综述（2016–2024，PRISMA）；含轻量/可穿戴；**表格为图片、无参数量汇总**；警告"丢弃低质量信号会夸大指标" | 新领域地图（补 04）|
| 26 | Lai et al. 2025, Sensors 25(21):6562 | 已读 | Yaseen 五分类 **99.6%**；**312,357 参数 / 4.5M FLOPs**；**树莓派 4 实测 1.87 ms**；BER 质量门 + Grad-CAM 选 MFCC 维度 | **最贴近端侧约束** |
| 27 | Zhu et al. 2024, LEPCNet, IEEE TIM 73:2511111 | 已读 | **52.67k 参数 / 1.59M FLOPs**；Yaseen 五分类 **99.4%**；**STM32F405（192KB SRAM）实测 245.1 mW、1.58 s/样本**，设备端 95.6% | **MCU 部署唯一硬证据** |
| 28 | Shuvo et al. 2023, NRC-Net, BSPC | 已读 | Yaseen 五分类；**肺音按 SNR 混合造噪声集**；CQT/CWT/STFT/MFCC 四变换对比；**CWT 干净 99.7%、含噪 97.4%**（VGG16 含噪 95.69%）| 噪声实验设计 |
| 29 | Sinha Roy et al. 2023, Conv-RF, IEEE OJIM 2:2500717 | 已读 | Yaseen 五类；SqueezeNet + 随机森林 **99.37%**；**ESP32 仅采集，推理在树莓派 4B** | IoT 反面提醒 |
| 30 | De Fazio et al. 2024, Sensors 24(12):3853 | 已读（摘要+结果）| CinC2016 二分类/多分类；**NN 类 232–453 kB 可上 MCU**；k-NN 需 5.6–14.1 MB（SBC）| **平台筛选的内存依据** |
| 31 | Banbury et al. 2021, MLPerf Tiny, arXiv:2106.07597 | 已读（摘要）| TinyML 行业基准；**同时测精度/时延/能耗**；四项任务**不含 PCG** | **"怎么比"的方法依据** |
| 32 | Novac et al. 2021, Sensors 21(9):2984 | 已读（摘要+结果）| 两 MCU × 三引擎（TFLite Micro / STM32Cube.AI / MicroAI）对比；**8-bit 未必优于 16-bit**；TFLite Micro 无自带测量工具 | **部署比较方法 + 引擎差异** |
| 33 | Liu, Springer & Clifford 2017, Physiol Meas 38(8):1730 | 已读（摘要）| HSMM 分割在 **8 个库 / 1,297 人 / 102,306 条**测试：**S1+systole F1 98.5%、S2+diastole 97.2%** | **ADR-039 对齐可行性的依据** |
| 34 | Oliveira et al. 2022, IEEE JBHI 26(6):2524 | 已读（全文）| CirCor：**5,282 条 / 1,568 人**、0–30 岁、4 kHz；杂音 缺席 73.0% / 有 19.5% / **低质量 7.6%**；原文"信息谱 <1 kHz，**2–4 kHz 足够**" | **2 kHz 的权威依据 + A6 数据源** |
| 35 | Yin et al. 2022, Med Devices (Auckl) 15:313 | 已读（全文）| CinC2016 二分类；HSMM 分割 + 小 DNN（1–5 层×100），测试 **87–88%**；**无"分割 vs 不分割"消融** | **ADR-039 的反面证据：文献没给分割增益** |
| 36 | Zhang et al. 2023, Sensors 23(5):2591 | 已读（全文）| ICBHI+Yaseen **11 类**；双谱 256×256 + CNN + 森林；**RPi Zero 2W 上约 9 s、占 142 MB**；**增强先于划分** | **"能上端≠能实时"的关键反例** |
| 37 | Zambrano-de la Torre et al. 2026, Bioengineering 13(5):532 | 已读（全文）| **Arduino UNO**（2 KB SRAM）上 int8 1D-CNN 心电三分类：**97.6%**、**<24 kB**、**200 ms/搏**；**PTQ 掉点 <1%** | **跨任务技术证据（PTQ 损失、MCU 余量）** |
| 39 | Cogent Eng 2020 | 已读（摘要）| **树莓派 + 自动分割 + ANN**，**9 类**瓣膜病 | **SBC 上多类 VHD 的先例** |
| 41 | Ragusa et al. 2025, IEEE JIoT | **已读（全文）** | STM32N6 + Neural-ART；**同模型三配置对比**：M7+M4 1.1 s / M55 391 ms / +NPU **18.7 ms**；端上 94.3%、1.51 mJ | **唯一硬件横向对比 + NPU 代价** |
| 42 | Zhao et al. 2026, IEEE TIM 75:4003410 | **已读（全文）** | 手机采集 + MBCF-Net；Yaseen 五分类 **99.88%**（比基线仅 +0.08）；**时延在 GPU 上测（0.18–0.27 s），非手机** | **"手机采集≠端上推理"的反例** |
| 44 | Vu & Tran 2024, arXiv:2412.03267 | **已读（全文）** | 手机端侧 **IConNet**；CinC2016 二分类 **UA 87.48 / F1 92.05**；**154k 参数、493 kB**；**未报端上时延** | **"手机能跑"的第二篇证据** |
| 43 | IEEE TIM 2026 | **已读（全文）** | **nRF52840（Cortex-M4）+ TFLM + int8**；TinyPINN；PhysioNet 91.87/93.94%；**端上 5.31 ms、58.9 µJ、掉 0.96 点**；全流程在 MCU | **MCU 最完整证据；推翻"nRF52840 算力不足"** |
| 40 | FPGA 两篇（2019 诊断 / 2024 TIM 分割）| 已读（摘要）| 2019：DE2 FPGA + DWT 实时诊断；2024：U-Net 分割上低成本 FPGA | **更正"FPGA 无分类"；说明 FPGA 主要做前端** |
| 38 | Saha et al. 2022, IEEE Sensors J | 已读（摘要）| 单片机级 ML 综述：**先按设备预算设计**，不是训练完再裁 | **方法学依据（ADR-026）** |

## 已建笔记

- 04 → `docs/literature/04-Zhao-2024-心音深度学习综述.md`（2026-10-24，全篇通读）
- 01 → `docs/literature/01-Yaseen-2018-数据集与多特征.md`（2026-10-24，全文；PDF 已存 `docs/refs/`）
- 02 → `docs/literature/02-Alqudah-2020-双谱.md`（2026-10-24，仅引用信息；全文待找）
- 03 → `docs/literature/03-Jain-2022-DWT-1DCNN.md`（2026-10-24，全文）
- 05 → `docs/literature/05-Schmidt-2008-DHMM分割.md`（2026-10-24，会议版全文；期刊版未获取）
- 06 → `docs/literature/06-Jacob-2018-INT8量化.md`（2026-10-24，全文；PDF 已存 `docs/refs/`）
- 11 → `docs/literature/11-Ghosh-2020-SCT核稀疏网络.md`（2026-10-24，全文 XML）
- 12 → `docs/literature/12-Ding-2023-CWT-GoogLeNet.md`（2026-10-24，全文 XML）
- 13 → `docs/literature/13-Waaler-2024-Tromso真实队列.md`（2026-10-26，全文 PDF）
- 14 → `docs/literature/14-Shiraga-2023-多模态PCG-ECG.md`（2026-10-26，全文 PDF）
- 15 → `docs/literature/15-Chorba-2021-数字听诊器杂音检测.md`（2026-10-26；数字取 JAHA 正式版 XML，PDF 是预印本）
- 16 → `docs/literature/16-Makimoto-2022-重度AS手机筛查.md`（2026-10-26，全文 XML）
- 17 → `docs/literature/17-Ma-2023-端侧轻量CNN.md`（2026-10-26，全文 PDF，用户提供）
- 18 → `docs/literature/18-Soni-2021-对比学习.md`（2026-10-26，全文 XML）
- 19 → `docs/literature/19-AlIssa-2022-CNN-LSTM轻量.md`（2026-10-26，全文 PDF）
- 20 → `docs/literature/20-CardioXNet-2021-轻量CRNN.md`（2026-10-27，arXiv PDF）
- 21 → `docs/literature/21-Alkhodari-2021-CNN-BiLSTM.md`（2026-10-28，全文 PDF）
- 22 → `docs/literature/22-TorreCruz-2023-ONMF-CNN.md`（待读）
- 23 → `docs/literature/23-Baghel-2020-CNN.md`（待读）
- 24 → `docs/literature/24-Khan-2022-CardiNet.md`（待读）
- 25 → `docs/literature/25-Kannan-2025-VHD综述.md`（2026-10-28，全文 PDF）
- 26 → `docs/literature/26-Lai-2025-WCNN-MFCC优化.md`（2026-10-28，全文 PDF）
- 27 → `docs/literature/27-LEPCNet-2024-端到端MCU.md`（2026-10-28，全文 PDF，用户提供）
- 28 → `docs/literature/28-NRCNet-2023-噪声鲁棒四变换.md`（2026-10-28，arXiv PDF）
- 29 → `docs/literature/29-ConvRF-2023-SqueezeNet随机森林IoT.md`（2026-10-28，全文 PDF，用户提供）
- 30 → `docs/literature/30-DeFazio-2024-嵌入式PCG分类器内存.md`（2026-10-29，EPMC 全文，未精读）
- 31 → `docs/literature/31-Banbury-2021-MLPerfTiny基准.md`（2026-10-29，arXiv 摘要，未读全文）
- 32 → `docs/literature/32-Novac-2021-MCU量化部署对比.md`（2026-10-29，EPMC 全文，未精读）
- 33 → `docs/literature/33-Liu-2017-HSMM分割八库评测.md`（2026-10-30，仅摘要，全文闭源）
- 34 → `docs/literature/34-Oliveira-2022-CirCor数据集.md`（2026-10-30，全文 XML）
- 35 → `docs/literature/35-Yin-2022-HSMM加DNN.md`（2026-10-30，全文 XML）
- 36 → `docs/literature/36-Zhang-2023-Zero2W部署.md`（2026-10-30，全文 XML）
- 37 → `docs/literature/37-Zambrano-2026-TinyML心电.md`（2026-10-30，全文 XML）
- 38 → `docs/literature/38-Saha-2022-MCU机器学习综述.md`（2026-10-30，摘要）
- 07 → `docs/literature/07-Krishnamoorthi-2018-QAT白皮书.md`（2026-10-26，仅目录+引用，待读）
- 08 → `docs/literature/08-陈尧-2021-心音心电神经网络.md`（2026-10-26，仅题录，待找）
- 09 → `docs/literature/09-李婷-2015-循环平稳理论.md`（2026-10-26，仅题录，待找）
- 10 → `docs/literature/10-韩威-2020-小样本心音分类.md`（2026-10-26，仅题录，待找）

## 阅读状态说明（2026-10-26）

**"已读"≠ 你亲读过。** 截至 2026-10-28 逐条核对（29 篇）：

| 状态 | 篇目 | 数量 |
| --- | --- | --- |
| **你亲读并讨论过** | 01、03、04、05、11、12、13、17、18、19、21、25、26 | **13** |
| **你部分读过** | 14（只读 PCG 相关部分） | 1 |
| **助手读过、你未亲读** | **20、22、23、24、27、28、29** | **7** |
| **决定不读**（你明确表态） | 06、07、08、10、15、16 | 6 |
| **决定不读且无全文** | 09 | 1 |
| **无全文（从未读到）** | 02 | 1 |

> 合计 13 + 1 + 7 + 6 + 1 + 1 = **29**。

**说明**

- **20（CardioXNet）** 是助手读的；你当时只问过它的数字与文献名，未表示读过。
- **22/23/24** 是 2026-10-28 助手新读并补写的笔记。
- **26** 你读过正文结尾"五个贡献"那段并与我讨论，计入亲读。
- **06–10、15、16** 按你的表态记为"决定不读"；其中 **06** 助手读过、**07/08/09/10** 助手也只整理了题录，**均不可引用其内容**。

## 状态记号

- `待找`：还没拿到全文
- `待读`：有全文，没开始
- `待精读`：读过摘要/浏览，需要细读方法
- `已读`：能复述方法、结果、局限
- `已用`：已写进论文/已用于实验
