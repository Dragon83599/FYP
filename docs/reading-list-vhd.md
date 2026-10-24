# 参考文献清单：六篇知乎文章溯源（2026-10-24）

> 目的：那 6 篇里 5 篇是博客/翻译，**论文里不能引博客**。下面是找到的原始文献，
> 标注了哪些免费、哪些需要机构权限、哪些没找到原文。
>
> 用法：写"相关工作"时引本清单里的论文；博客只能作为工程实现参考，不进参考文献。

## 1. 数据集与特征（必读，3 篇）

| # | 文献 | 与哪篇知乎对应 | 获取 |
| --- | --- | --- | --- |
| 1 | **Yaseen, Son G-Y, Kwon S.** *Classification of Heart Sound Signal Using Multiple Features.* **Applied Sciences** 2018;8(12):2344. doi:10.3390/app8122344 | 数据集来源（第 1、2、4、6 篇都用它）| **开放获取**（MDPI），含 MFCC+DWT 基线 |
| 2 | **Alqudah AM, Alquran H, et al.** *Classification of heart sound short records using bispectrum analysis approach images and deep learning.* **Network Modeling Analysis in Health Informatics and Bioinformatics** 2020;9. doi:10.1007/s13721-020-00272-5 | 第 1、2 篇的**双谱**依据 | Springer，需机构权限；摘要可查 |
| 3 | **Jain PK（NIT Rourkela）, Choudhary RR, Singh MR（Central Univ. of Rajasthan）.** *A Lightweight 1-D Convolution Neural Network Model for Multi-class Classification of Heart Sounds.* **ICETCI 2022**（Mahindra University, Hyderabad, Aug 25–27）| 第 6 篇的原文 | ✅ **已下载并核对**：`docs/refs/2022-jain-icetci-dwt-1dcnn.pdf`（5 页，405 KB）；原文 <http://hdl.handle.net/2080/3735> |

> 第 3 篇直接给了 98.9%、敏感度 99.01%、特异度 99.72%，方法步骤写得最全。
> **已逐条核对知乎译文与原文一致**：8 kHz → 1 kHz、端点检测后定长 **2800 点**、
> coif5 五级 DWT → **2942 维**、50 epoch / 450 迭代 / lr 0.01 / batch 64 / SGD+momentum、
> **随机 70/30 划分**。**这是你最该先读的一篇。**
>
> 注意最后一点：它是**随机 70/30**，不是按录音分组——这正是我们可以改进的地方。

## 2. 综述（必读，1 篇）

| # | 文献 | 用途 | 获取 |
| --- | --- | --- | --- |
| 4 | **Zhao Q, Geng S, Wang B, et al.** *Deep Learning in Heart Sound Analysis: From Techniques to Clinical Applications.* **Health Data Science** 2024;4:0182. doi:10.34133/hds.0182. PMID 39387057 | 领域地图、数据集表、临床应用分类、**医生间 kappa 0.3–0.48** | ✅ 免费：[PMC11461928](https://pmc.ncbi.nlm.nih.gov/articles/PMC11461928/) |

## 3. 方法基础（按需读）

| # | 文献 | 用途 | 获取 |
| --- | --- | --- | --- |
| 5 | **Schmidt SE, Holst-Hansen C, Graff C, Toft E, Struijk JJ.** *Segmentation of heart sound recordings by a duration-dependent hidden Markov model.* **Physiological Measurement** 2010;31(4). PMID 20208091 | S1/S2 分割的经典方法（第 6 篇的"端点检测"、我们旧项目的 S1/S2 检测器都基于它）| PubMed 摘要免费，全文需订阅 |
| 6 | **待核对**：*Classifying Heart-Sound Signals Based on CNN Trained on MelSpectrum and Log-MelSpectrum Features*（Chen, Zhou 等）| 疑似第 4 篇"对数谱图"的原文 | ⚠️ **未确认**。第 4 篇是 deephub 的中文翻译，原文标题没写；这篇的设定（5 类、CNN/LSTM、0.9967）吻合，但需你在原文里核对 |
| 7 | **Jacob B, Kligys S, Chen B, et al.** *Quantization and Training of Neural Networks for Efficient Integer-Arithmetic-Only Inference.* **CVPR 2018** | INT8 量化的标准参考（部署用）| ✅ 免费：CVF Open Access |
| 8 | **Krishnamoorthi R.** *Quantizing deep convolutional networks for efficient inference: A whitepaper.* arXiv:1806.08342, 2018 | QAT（量化感知训练）| ✅ 免费：arXiv |

## 4. 中文博士论文（第 5 篇提到的三篇）

| # | 论文 | 单位/年份 | 获取 |
| --- | --- | --- | --- |
| 9 | 陈尧. 心音心电信号处理的神经网络方法 | 四川大学, 2021 | CNKI（需校园网）|
| 10 | 李婷. 基于循环平稳信号理论的心音信号处理方法研究 | 大连理工大学, 2015（导师 邱天爽）| CNKI |
| 11 | 韩威. 小样本心音分类方法研究 | 广东工业大学, 2020 | CNKI |

> 这三篇的价值：**李婷**讲循环平稳理论（解释"心音为什么是非平稳信号"），**韩威**讲小样本（对应我们 1000 条的数据量），**陈尧**讲神经网络 + 心音心电联合。
> 如果你能下到 PDF，放进 `docs/refs/`，我可以帮你提炼每篇的可引用点。

## 5. 没找到原文的

| 知乎文章 | 情况 |
| --- | --- |
| 第 5 篇 CWT + GoogLeNet | **没有找到对应论文**。它是一篇 MATLAB 演示（`cwtfilterbank` + `trainNetwork`），自己都承认"样本少、准确率不高（90%）"。**只能作工程参考，不能作为方法依据** |
| 第 2 篇 ResNet + 双谱 | 博客原创，无对应论文；其双谱方法可引第 2 条（Alqudah 2020）|
| 第 1 篇 AOCT | 同上；"AOCT" 无出处，建议改称 "4-block CNN" 并引 Alqudah |

## 6. 建议的阅读顺序

```
第 1 周：3（Jain 2022，免费全文，方法最全）→ 1（Yaseen 2018，数据集与基线）
第 2 周：2（Alqudah 2020，双谱）→ 4（HDS 2024 综述，建立领域地图）
第 3 周：5（Schmidt 2010，分割）→ 7、8（量化，为部署）
随时：9–11（中文博士论文，能下到就看）
```

## 7. 读每篇时只找四样东西

1. **数据**：用了哪个数据集、多少条、怎么划分（随机还是分组）？
2. **预处理**：采样率、滤波截止、窗长、是否端点检测？
3. **特征与模型**：具体维度、层数、参数量？
4. **结果与局限**：准确率之外有没有 F1/混淆矩阵？作者自己承认的局限是什么？

> 第 4 条最重要——**每篇的"局限"就是你可以做的改进**。
