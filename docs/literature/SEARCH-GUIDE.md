# 文献检索指南（自己找的时候用）

> 目标：**快速判断一篇论文是不是"真的把模型跑在端上"**，而不是"说可以部署"。

## 一、去哪儿找（按用途排序）

| 网站 | 地址 | 适合找什么 | 备注 |
| --- | --- | --- | --- |
| **Europe PMC** | europepmc.org | 生物医学全文（含 OA 全文 XML）| **首选**：我们 30+ 篇多来自这里 |
| **OpenAlex** | openalex.org | 跨库检索 + 引用关系 + 开放获取状态 | 免费 API，可批量 |
| **PubMed** | pubmed.ncbi.nlm.nih.gov | 医学索引 | 与 EPMC 重叠，界面更"医学" |
| **Google Scholar** | scholar.google.com | 全学科、引用追踪 | 找"谁引用了 27"最方便 |
| **Semantic Scholar** | semanticscholar.org | 语义相似 + 引用图 | 找"和这篇像的" |
| **IEEE Xplore** | ieeexplore.ieee.org | 工程/信号处理（TIM、TBME、JBHI）| 多数要订阅 |
| **arXiv** | arxiv.org | 预印本（快，但未评审）| 标"preprint" |
| **MDPI** | mdpi.com | Sensors / Bioengineering / Diagnostics | **全 OA**，可下全文 |
| **Frontiers** | frontiersin.org | 生理/医学工程 | 全 OA |
| **CNKI / 万方** | cnki.net / wanfangdata.com.cn | 中文硕博论文 | 需要机构账号 |
| **PhysioNet** | physionet.org | **数据集**（CinC2016、CirCor）| 找数据不找论文 |
| **Papers with Code** | paperswithcode.com | 有代码/有榜单的工作 | 找可复现实现 |

**不要用**：Sci-Hub（版权风险）、ResearchGate 的"全文"（多为作者自传，版本不确定）。

## 二、关键词：按五个维度组合

### 维度 1：任务（选一个）

```
heart sound | phonocardiogram | PCG | cardiac auscultation | heart murmur
valvular heart disease | VHD | aortic stenosis | mitral regurgitation
five-class | multi-class | murmur detection
```

### 维度 2：方法（选一个）

```
deep learning | CNN | 1D-CNN | 2D-CNN | ResNet | transformer
MFCC | log-mel | mel-spectrogram | wavelet | CWT | STFT
self-supervised | contrastive learning | knowledge distillation
```

### 维度 3：**平台（找端侧必加，这是关键）**

```
edge device | embedded | on-device | TinyML | microcontroller
MCU | Raspberry Pi | STM32 | ESP32 | Arduino | FPGA | smartphone | mobile
```

### 维度 4：**证据（筛"真部署"必加）**

```
deployment | inference time | latency | real-time | quantized | INT8
TFLite | TensorFlow Lite Micro | TFLM | model size | memory footprint
```

### 维度 5：数据（可选）

```
Yaseen | CirCor | PhysioNet Challenge 2016 | CinC 2016 | PASCAL
```

## 三、可直接粘贴的检索式

**Europe PMC / PubMed**（字段语法：`TITLE_ABS:`）：

```
TITLE_ABS:"heart sound" AND TITLE_ABS:(embedded OR microcontroller OR "Raspberry Pi" OR smartphone) AND TITLE_ABS:(deployment OR latency OR quantized)
```

```
TITLE_ABS:phonocardiogram AND TITLE_ABS:(TinyML OR "edge device" OR INT8) AND (PUB_YEAR:2022 OR PUB_YEAR:2023 OR PUB_YEAR:2024 OR PUB_YEAR:2025)
```

**OpenAlex**（网页或 API）：

```
https://api.openalex.org/works?search=heart%20sound%20edge%20deployment&per-page=50
```

**Google Scholar**（用引号 + 减号排噪）：

```
"heart sound" "Raspberry Pi" classification -review
"phonocardiogram" "STM32" OR "ESP32" inference
"heart murmur" smartphone "deep learning" deployment
```

**找"谁引用了 27"**（追踪法，最有效）：

```
Google Scholar → 搜 LEPCNet → 点"被引用次数" → 逐条看标题
```

## 四、三层漏斗（别一上来就读全文）

| 层 | 做什么 | 时间 |
| --- | --- | --- |
| **1. 标题筛** | 只看标题，剔除综述/纯硬件/纯分割 | 10 秒/篇 |
| **2. 摘要筛** | 看有没有**平台名 + 数字**（时延/内存/参数量）| 1 分钟/篇 |
| **3. 全文筛** | 只对有数字的读方法+结果段 | 20 分钟/篇 |

**一句话判据**：摘要里如果只有 `can be deployed on...` 而没有数字，**基本是"声称"而非"实测"**。

## 五、判断"是否真的部署"的五个检查点

| # | 检查点 | 通过标准 |
| --- | --- | --- |
| 1 | **有端上耗时吗** | 给出 ms/s，最好说明是否含预处理 |
| 2 | **有量化说明吗** | 写明 int8/int16/float、用什么引擎 |
| 3 | **有内存数字吗** | kB 或 MB，说明是模型还是峰值 |
| 4 | **有掉点吗** | 端上精度 vs 电脑端精度 |
| 5 | **划分说明白了吗** | 随机折 / 按患者 / 固定测试集；有无增强泄漏 |

**五个都缺 → 只能列为"声称部署"**，不能进我们的对比表。

## 六、我们还缺什么（优先找这几类）

| 缺口 | 建议关键词 |
| --- | --- |
| **MCU 上的 PCG 分类**（目前只有 27 一篇）| `heart sound AND (STM32 OR ESP32 OR microcontroller) AND classification` |
| **录音级 / 患者级评测** | `heart sound AND (patient-level OR recording-level) AND evaluation` |
| **噪声鲁棒** | `heart sound AND noise robustness AND SNR` |
| **跨平台比较** | `heart sound AND (comparison OR benchmark) AND (device OR platform)` |
| **量化对心音的影响** | `heart sound AND quantization AND (INT8 OR fixed-point)` |

## 七、读完怎么记（保持我们的格式）

1. 建 `docs/literature/NN-作者-年份-关键词.md`（下一个编号是 41）；
2. 五段式：**引用 / 数据 / 方法 / 结果 / 与我的 FYP 的关系 / 待查证**；
3. 数字必须能指到原文页码或表格；查不到写**待查证**；
4. 更新 `docs/literature/README.md` 总表；
5. 如果是"端侧"论文，同时更新 `edge-deployment-landscape.md`。
