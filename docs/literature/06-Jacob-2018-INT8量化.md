# 06 · Jacob 2018 · INT8 整数推理

> 阅读深度：全文（2026-10-24），10 页，含 Table 4.1–4.5 与 Fig. 1.1/4.1/4.2。
> 数字均标表号；PDF 在 `docs/refs/06-jacob-2018-cvpr.pdf`（CVF 开放获取）。

> **阅读状态（2026-10-28）**：用户决定不读本文；笔记为助手通读整理。引用前请核对摘要与关键表。

## 引用

- 作者：Benoit Jacob, Skirmantas Kligys, Bo Chen, Menglong Zhu, Matthew Tang, Andrew Howard, Hartwig Adam, Dmitry Kalenichenko（Google）
- 标题：Quantization and Training of Neural Networks for Efficient Integer-Arithmetic-Only Inference
- 期刊/会议：**CVPR 2018**，pp. 2704–2713
- 年份：2018
- DOI/链接：CVF Open Access；<https://openaccess.thecvf.com/content_cvpr_2018/papers/Jacob_Quantization_and_Training_CVPR_2018_paper.pdf>
- 全文位置：`docs/refs/06-jacob-2018-cvpr.pdf`

## 数据

（本文是量化方法论文，实验数据不是心音；列出以便核对规模）

- ImageNet 分类：ResNet-50/100/150、Inception v3、MobileNet（不同 depth multiplier 与分辨率）
- COCO 检测：MobileNet SSD
- 人脸检测与属性分类：Flickr-based 数据集（沿用 [10] 的协议）
- 硬件：Qualcomm Snapdragon 835（LITTLE / big）、Snapdragon 821 big（Pixel 1/2）

## 方法

### 1. 量化表示（Eq. 1–2）

$$r = S\,(q - Z)$$

- $q$：量化整数；$S$：scale（float）；$Z$：zero-point（与 $q$ 同类型）。
- 权重 **int8**、激活 **uint8**、**bias 用 int32**（Eq. 11：$S_{bias}=S_1S_2$、$Z_{bias}=0$）。

### 2. 整数矩阵乘（Eq. 3–6）

由 $S_3(q_3-Z_3)=\sum S_1(q_1-Z_1)S_2(q_2-Z_2)$ 得：

$$q_3 = Z_3 + M\sum (q_1-Z_1)(q_2-Z_2),\qquad M:=\frac{S_1S_2}{S_3}$$

- 唯一的非整数是 $M$；它**只依赖 scale，可离线算**，且实测恒在 $(0,1)$，可写成 $M=2^{-n}M_0$（$M_0\in[0.5,1)$），用**定点乘法 + 移位**实现。
- 这样整条推理链**不含浮点**（ReLU6 也只用 8-bit 整数）。

### 3. 量化器（Eq. 12）

$q(r)=\left\lfloor \frac{\mathrm{clamp}(r,a,b)-a}{s}\right\rceil s + a$，$s=\frac{b-a}{n-1}$，8 bit 时 $n=256$。

### 4. 范围怎么定（3.1 节）

| 对象 | 做法 |
| --- | --- |
| 权重 | 取 $a=\min w,\ b=\max w$；**微调使 int8 只落在 [−127,127]，不用 −128** |
| 激活 | 训练中收集范围，用 **EMA**（平滑系数接近 1）聚合；**训练初期（5 万–200 万步）完全关闭激活量化**，让网络先稳定 |

### 5. 量化感知训练（3 节 + Algorithm 1）

- 在前向图里插入 **fake quantization** 节点模拟量化，反向仍用浮点、权重与 bias 保持浮点；
- 训练收敛后，再生成真正的低比特推理图；
- 与"训练后量化（PTQ）"的差别：PTQ 在小模型上会明显掉点，原因（原文）是**同一层不同输出通道的权重范围可差 100 倍以上**，而第 2 节要求同层共享一个 scale，导致范围小的通道相对误差很大。

### 6. 一个必须记住的边界

**这篇是 per-tensor（每层一个 scale）**，不是 per-channel。原文把"通道间范围差异"列为 PTQ 的失败原因，用 QAT 去缓解；per-channel 权重量化是后来 TFLite 的改进，**不在本文**。

## 结果（原文数字）

### ResNet（ImageNet，Table 4.1）

| 深度 | 浮点 | 整数量化 |
| --- | --- | --- |
| 50 | 76.4% | 74.9% |
| 100 | 78.0% | 76.6% |
| 150 | 78.8% | 76.7% |

原文：`Accuracies of integer-only quantized networks are within 2% of their floating-point counterparts.`

### 不同量化方案对比（ResNet-50，Table 4.2）

| 方案 | BWN | TWN | INQ | FGQ | **本文** |
| --- | --- | --- | --- | --- | --- |
| 权重位宽 | 1 | 2 | 5 | 2 | **8** |
| 激活位宽 | float32 | float32 | float32 | 8 | **8** |
| 精度 | 68.7% | 72.5% | 74.8% | 70.8% | **74.9%** |

### Inception v3（Table 4.3）

| 激活函数 | 浮点 | 8 bit | 7 bit |
| --- | --- | --- | --- |
| **ReLU6** | 78.4% | **75.4%** | 75.0% |
| ReLU | 78.3% | 74.2% | 73.7% |

→ ReLU6 掉点更少（原文解释：ReLU6 把激活限制在 [0,6]，范围固定更好量化）。

### 延迟（MobileNet / COCO / 人脸，Table 4.4–4.5）

- **MobileNet 分类**：相同延迟预算下，8-bit 精度更高；Snapdragon 835 LITTLE 在 33 ms（30 fps 实时）处，差距约 **10%**（Fig. 1.1c）。
- **MobileNet SSD on COCO**（Table 4.4，DM=100%）：mAP 22.1 → **21.7**；延迟 LITTLE **778 → 687 ms**、big **370 → 272 ms**；原文称最高 **50% 运行时间下降**、相对精度仅 **−1.8%**。
- **人脸检测**（Table 4.5，25% DM）：LITTLE 100 → **67 ms**、big 44 → **28 ms**；25% 模型量化后**能实时**（约 36 fps），浮点版 23 fps。
- 原文结论：量化在 Snapdragon 821 上收益较小，因为那代浮点优化更好——**收益依赖硬件**。

## 我认为重要的点（原话/我的理解）

- **（你还没有转述这篇的原话。）** 你转述时我逐字记在这里，不改写。

## 与我的 FYP 的关系

- 可借鉴：
  1. **bias 用 int32、scale 取 $S_1S_2$**、$M$ 用定点乘法——这是 INT8 推理能完全去浮点的关键，直接对应我们固件里的量化算子；
  2. **ReLU6** 比 ReLU 更耐量化（Inception 上差 1.2 个点）——我们模型若用 ReLU，可以考虑换 ReLU6；
  3. **训练初期关闭激活量化**（5 万–200 万步）是可操作的小技巧；
  4. 评测应报**延迟-精度曲线**，而不是只报"量化后掉了多少点"。
- 可改进 / 注意：
  - 本文实验都是 ImageNet/COCO 的大模型，**没有一个是 150k 参数以内的小模型**；小模型的量化结论要自己测；
  - **per-tensor** 是本文的设定，我们的工具链若支持 per-channel，量化误差会更小（但不是本文能背书的）；
  - 它的延迟是在 Snapdragon 上测的，**ESP32-S3 的 INT8 SIMD 完全不同**，不能直接引用。
- 不能用的部分：
  - 不能引 "within 2%" 来保证我们的模型也只掉 2 个点——模型规模、任务、数据都不同；
  - 不能说"本文用了 per-channel"。

## 待查证

- 我们现有量化配方（`QInt8 + Percentile 99.99`，见 ADR-007）与本文 **min/max + EMA** 的差别有多大——需要在本项目模型上对比，不能靠推理。
- ESP-IDF / esp-dl / TFLite Micro 在我们这条工具链上用的是 per-tensor 还是 per-channel。
- 我们模型的激活范围是否适合 ReLU6（当前用的是 ReLU 还是 ReLU6，要到 `tools/ai/` 里确认）。
