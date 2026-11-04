# 导读：助手读过、你尚未亲读的 6 篇（2026-10-26）

> 用途：这 6 篇的笔记是**助手通读后写的**，你还没有逐段读过。
> 引用前至少过一遍下面列出的章节与数字；每篇约 20–30 分钟。

## 优先级

| 优先 | 篇目 | 理由 | 预计 |
| --- | --- | --- | --- |
| 1 | **17 Ma 2023**（你已读，跳过）| — | — |
| 1 | **13 Waaler 2024** | 唯一按患者划分的真实队列，是"高准确率不可信"的最硬证据 | 25 min |
| 2 | **15 Chorba 2021** | 临床规模最大（962 人验证），但结论常被误读 | 25 min |
| 2 | **16 Makimoto 2022** | 部署形态（手机 App）+ 小样本验证的典型 | 20 min |
| 3 | **14 Shiraga 2023** | 多模态边界（AS 靠心音、LVD 靠 ECG）| 20 min |
| 3 | **18 Soni 2021** | 直接冲击 ADR-024：满标签下 SSL 无增益 | 20 min |
| 4 | **06 Jacob 2018** | 量化，你已说"回来再说" | 30 min |

---

## 13 · Waaler 2024（Tromsø）

**读**：Abstract → Methods 的 `Performance estimation and model comparison`（讲按患者划分 + 预留 holdout）→ Results 的 AUC 表 → Discussion 的 limitations。

**必须核对的数字**
- 2,124 人 / 8,496 条
- AS：敏感度 90.9%、特异度 94.5%、AUC 0.979
- AR 0.634、MR 0.549（+临床变量后 0.766 / 0.677）

**读完应能回答**
1. 为什么作者说"AS 能筛、AR/MR 不能"？
2. 它用什么办法避免"用测试集调参"？
3. 它的结论能不能直接套到 Yaseen 的五分类上？为什么不能？

## 15 · Chorba 2021（JAHA）

**读**：Abstract → `Algorithm Development` → `Murmur Detection Performance` → `Valvular Disease Screening Performance` → Discussion 的 limitations。

**必须核对的数字**
- 训练 5,318 人 / 5,878 条；**验证 962 人**
- 杂音 76.3% / 91.4%（去 1 级后 90.0%）
- AS 93.2/86.0；MR 66.2/94.6

**读完应能回答**
1. 为什么"5,318 人"不是验证人数？
2. 验证集是连续筛查人群还是病例-对照富集？这对指标有什么影响？
3. 它和 3 位专家比，谁更好？在哪种病上差距最大？

## 16 · Makimoto 2022（EHJ DH）

**读**：Abstract → `Dataset preparation`（5 折 + 固定测试集）→ `Recognition by case and ensemble` → `Clinical validation`。

**必须核对的数字**
- 886 人入组、556 人建模、132 人临床验证
- 测试集 E1 集成：94%、F1 0.857
- App：95.7%、F1 0.932、敏感度 97.6%、特异度 94.4%

**读完应能回答**
1. 为什么测试集只有 100 例也能发文章？它的统计口径是什么？
2. 1:1:1 的集成权重是怎么定的？
3. 132 人验证里"没有 AS"和"轻度 AS"各多少例？这说明样本构成有什么问题？

## 14 · Shiraga 2023（Sensors）

**读**：Abstract → `2.3 Data Preprocessing`（log-mel 参数）→ Results 的 Table 5–7 → Discussion 的模态贡献部分。

**必须核对的数字**
- 1,052 人训练 + 103 人验证
- AS：心音 2RSB **0.974** vs ECG 0.780
- LVD：ECG 0.925 vs 心音 0.775
- MR：最好也只有 0.787

**读完应能回答**
1. 它为什么说"不同病依赖不同模态"？
2. 它有没有做归一化？和 Ding 的做法冲突吗？
3. 临床验证为什么下降？哪一类下降最多？

## 18 · Soni 2021（Patterns）

**读**：Introduction 最后两段 → `Evaluating initializations via end-to-end fine-tuning` → `Discussion`。

**必须核对的数字**
- 心音微调：10% 标签 0.773 → 0.857；**100% 标签 0.930 → ~0.925**
- 肺音元数据：0.512 → 0.854

**读完应能回答**
1. 为什么"低标签有用"不能推出"我们也有用"？
2. 线性探针和微调的区别是什么？为什么两个都要报？
3. 它的结论对 ADR-024 意味着什么？

## 06 · Jacob 2018（CVPR）

**读**：Section 2（量化方案，Eq. 1–6）、Section 3.1（范围怎么定）、Table 4.1–4.5。

**必须核对的数字**
- ResNet-50：76.4% → 74.9%
- MobileNet SSD：22.1 → 21.7 mAP，延迟最高降 50%
- 这篇是 **per-tensor**，不是 per-channel

**读完应能回答**
1. 为什么 bias 要用 int32？
2. "训练初期关闭激活量化"是为了解决什么？
3. 它的"within 2%"能不能套到我们 150k 参数的小模型上？
