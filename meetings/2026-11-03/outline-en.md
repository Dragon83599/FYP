# Group Meeting Slides (v7-EN, 2026-10-31) — Recommendation: Progressive Web App (PWA)

> **Purpose**: to obtain supervisory approval for the PWA deployment route.
> **Structure**: Background → Scope → Prior work → Three rationales (before / during / after replication) → Summary and decision → Planned improvements → Two-week plan → References.
> **11 slides**, 13–15 minutes. Citations are given as **author–year** in the body; numeric note identifiers are used only in internal notes.

---

## 1. Title and Recommendation

**Valvular heart disease classification from phonocardiograms: lightweight models and on-device deployment**

**Recommendation**

> This project should deploy its classifier as a **mobile web application (Progressive Web App, PWA)** rather than on a single-board computer or a microcontroller.
>
> Three rationales, ordered chronologically:
> **① Lowest cost before replication; ② Easiest 1:1 reproduction during replication; ③ Best on-device performance after replication.**

---

## 2. Background: Five-Class Heart-Sound Classification and Dataset Selection

**Physical basis.** Valve opening and closure generate the first and second heart sounds (S1, S2). Stenosis or regurgitation alters timbre, intensity and rhythm; this is the basis for classification from a phonocardiogram (PCG).

**The five classes (label set of the Yaseen dataset)**

| Label | Condition | Pathology |
| --- | --- | --- |
| **N** | Normal | No valvular lesion |
| **AS** | Aortic stenosis | Narrowed aortic orifice; obstructed outflow |
| **MS** | Mitral stenosis | Narrowed mitral orifice |
| **MR** | Mitral regurgitation | Incomplete closure; retrograde flow |
| **MVP** | Mitral valve prolapse | Leaflet prolapse |

**Candidate datasets**

| Dataset | Size | Task | Use in this project |
| --- | --- | --- | --- |
| **Yaseen 2018** | **1,000 recordings (200 per class)**, 8 kHz, mono | **Single-label, five-class** | **Selected** |
| CirCor 2022 | **5,282 recordings / 1,568 subjects**, 0–30 y, 4 kHz | Murmur detection and grading | Alternative (different task; **has subject IDs**) |
| PASCAL 2011 | Several hundred recordings | **Multi-label** | Not used |
| CinC 2016 | approx. 3,000 recordings (**size to be verified**), 2 kHz | **Binary** (normal/abnormal) | Not used |

**Why the Yaseen dataset**

| Reason | Detail |
| --- | --- |
| **Only single-label five-class set with all classes present** | Matches the project task directly; the alternatives are binary, multi-label, or murmur grading |
| **Balanced classes** (200 each) | No resampling required; metrics are not distorted by class priors |
| **Comparable literature** | Ma et al., Lai et al. and Zhu et al. all report on it, providing reference numbers |
| Manageable size | 1,000 recordings; five-fold cross-validation plus five improvements fit the remaining schedule |

**Acknowledged limitation.** The Yaseen dataset **provides no subject identifiers** (1,000 recordings collected from a CD and 48 websites). Grouping is therefore performed at the **recording level**, not the subject level.

---

## 3. Scope

| Item | Specification |
| --- | --- |
| Task | **Single-label, five-class classification** (dataset in Slide 2) |
| Deployment requirement | Inference executed **on the device**, without network access |

> These two requirements lead to a single conclusion: **the dominant cost of this project is not algorithmic but deployment-related — moving a model onto a platform and obtaining measured figures.**

---

## 4. Prior Work on the Three Deployment Routes

| Route | Representative work | **Parameters** | Accuracy (PC) | Accuracy (on device) | On-device latency | Studies |
| --- | --- | --- | --- | --- | --- | --- |
| **Mobile / PWA** | **Ma et al. (2023)**, **Vu et al. (2024)** | **20,193** | 98.65% | 98.65% | **0.03–0.37 s** | **2** |
| Single-board computer | **Lai et al. (2025)**, others | **312,357** | 86.9–99.94% | Mostly identical (float) | 1.87 ms – **9 s** | **9** |
| Microcontroller | **Zhu et al. (2024)**, **Ragusa et al. (2025)**, **Mai et al. (2026)** | **3,058–52,670** | 91.9–99.4% | **90.9–95.6%** | 5.31 ms – **1.58 s** | **3** |

**An essential caveat.** Model size differs by roughly **two orders of magnitude** across the three routes (MCU 3k–53k; mobile 20k; SBC 312k). **No published study compares the three routes with matched parameter counts on the same task.** Any cross-platform claim of speed or accuracy must therefore be established by a controlled experiment within this project.

**Three observations**

1. **SBC is the most crowded route but the least consistent** (86.9 → 99.94%); deployability does not imply quality.
2. **MCU is the least explored**; only **Zhu et al.** report on-device results on the Yaseen five-class task, at **1.58 s** per inference.
3. **Two mobile studies exist**, but only **Ma et al.** report both accuracy and latency.

> Two reviews (**Zhao et al. 2024**, 99 papers; **Kannan et al. 2025**, 199 papers) **do not jointly compare on-device deployment, five-class accuracy and parameter count**; this remains a gap.

**Reference option: no on-device deployment.** Running the model on a PC only is risk-free, but it does not satisfy the on-device requirement and can serve only as a baseline.

---

## 5. Rationale 1 (Before Replication): Lowest Total Cost

**The comparison concerns total input — hardware, learning effort, environment setup and iteration — not hardware price alone.**

| Item | Mobile / PWA | Raspberry Pi 4B | MCU (ESP32-S3 / STM32, etc.) |
| --- | --- | --- | --- |
| **Hardware price** | **¥0** | ¥400–600 + PSU | **¥30–60 (lowest)** + microphone/display |
| **Learning required** | **Browser + JS/TF.js** (existing web background) | Linux + Python + TFLite | **C/HAL + quantization toolchain + flashing** |
| Environment setup | **Open a browser** | Install OS and drivers | Install toolchain, create project, wire hardware |
| Iteration | **Edit–refresh cycle** | Minutes | Flash + serial debugging |

> **MCU saves money and costs time**; over the final two weeks, time is more expensive than a ¥400 board.

**Two common misconceptions, addressed**

| Misconception | Clarification |
| --- | --- |
| "The MCU is the cheapest option" | Hardware is indeed cheapest (¥30–60), but the **development effort is 2–4 days**; three additional days represent **20%** of the final two weeks |
| "Writing C is the more rigorous choice" | The objective is **measured deployment figures**, not toolchain proficiency; learning effort is a real cost |

**Six advantages of the PWA route**

| Advantage | What is avoided |
| --- | --- |
| **Zero hardware cost** | Display and compute are already available (acquisition handled by a teammate) |
| **No PCB, power supply, battery or thermal design** | No hardware chain to build |
| **No firmware toolchain** | No flashing, no serial console, no bricking risk |
| **Development on the target device** | Iteration measured in seconds |
| **Browser UI + TF.js/WebGL** | Interface and GPU acceleration at no cost |
| **Portable: the phone is already carried; no extra device** | Usable at the point of care; no board, display or power supply to prepare |

**Cost to be acknowledged.** The JavaScript preprocessing must be aligned point-by-point with the Python implementation: approximately **1–2 days**.

---

## 6. Rationale 2 (During Replication): The Simplest Baseline Is the Easiest to Extend

**The simpler the baseline, the cleaner the attribution of any change**

| Item | **Ma et al. (2023)** | **Lai et al. (2025)** | **Zhu et al. (2024)** |
| --- | --- | --- | --- |
| Parameters | **20,193** | 312,357 | 52,670 |
| Architecture | **3 × (Conv1D, kernel 20 + max-pool 4) + FC 128 + softmax** | WCNN + **KWC channel weighting** + MFCC dimensionality search | **Grouped convolution** + Springer segmentation + int8 |
| Where a modification lands | **Entirely outside the network**: input, representation, bandwidth, augmentation | Feature stage (MFCC) is **coupled** to the network | Segmentation is a **pre-requisite**; changing it requires re-alignment |
| Cost of adding one modification | **Low**: change the input or the training strategy only | Moderate: features and network must be changed together | High: segmentation and model are interdependent |
| Preprocessing chain | **Three steps, raw waveform** | Full MFCC pipeline + BER quality assessment | Segmentation + end-to-end model |

**Two observations**

1. **Only a simple baseline supports single-axis ablation.** With three convolutional layers and one dense layer, changing the sampling rate, the representation or the augmentation leaves the network internals untouched, so the effect can be attributed to that single variable (the acceptance rule used in this project).
2. **The headroom is transparent.** For a 20,193-parameter model the limiting factors are input information and training strategy, not depth; the five planned improvements therefore all act on the periphery rather than on a redesign.

> Conversely, when the baseline already combines grouped convolution, channel weighting and segmentation, any single change touches several components and the ablation becomes confounded.

---

## 7. Rationale 3 (After Replication): Best On-Device Performance

**Three studies on the same task (Yaseen five-class), representing three deployment strategies**

| Item | **Ma et al. (2023)** | **Lai et al. (2025)** | **Zhu et al. (2024)** |
| --- | --- | --- | --- |
| Platform / numeric format | Mobile PWA / **float32** | **Raspberry Pi 4B** / **float32** | STM32F405 / **int8 + trimming** |
| Parameters | **20,193** | **312,357** (**15.5×**) | 52,670 |
| Accuracy (PC) | 98.65% | **99.6%** (grid peak, no CI) | 99.4% |
| Accuracy (on device) | **98.65%** | **99.6%** | 95.6% |
| **Accuracy loss** | **0** | **0** (no quantization) | **−3.8** (trimming) |
| On-device latency | **0.03–0.37 s** | **1.87 ms/window** (inclusion of MFCC unspecified) | 1.58 s |

**Three observations**

| # | Observation |
| --- | --- |
| 1 | **Accuracy loss originates from quantization and trimming, not from the platform.** Ma et al. and Lai et al. both run float32 with no loss; Zhu et al. trimmed the model before deployment and lost 3.8 points. |
| 2 | **Accuracy can be bought with parameters.** Lai et al. report the highest accuracy (99.6%), but with **312,357 parameters (15.5× Ma et al.)**, and the figure is a **grid peak without a confidence interval**. |
| 3 | **The PWA route offers the best trade-off.** Ma et al. reach **98.65%** with roughly **1/15.5** of the parameters (a difference of 0.95 points), at a latency of 0.03–0.37 s, with **no quantization loss**. |

> **Platforms determine feasibility and speed; quantization and trimming determine accuracy loss; parameter count determines the achievable accuracy ceiling.**

**The only gap is energy.** Smartphone power consumption cannot be measured with a power meter and is therefore **neither measured nor reported**; this is not a limitation in the present setting (the phone is charged daily, the model is small, and no power budget is specified).
The remaining four quantities — latency, macro F1 with 95% CI, agreement with the Python implementation, and peak memory — are all measurable; **acceptance thresholds are given in Slide 8**.

---

## 8. Summary: The Three Rationales Support One Decision

| Stage | Conclusion | Key evidence (slide) |
| --- | --- | --- |
| **Before replication** | **Lowest cost** | Hardware **¥0**, operational within half a day; the MCU is cheapest in hardware (¥30–60) but requires **2–4 days**, with the three additional days equal to **20%** of the final two weeks (Slide 5) |
| **During replication** | **Easiest to extend** | Ma et al. use **three convolutional layers and one dense layer (20,193 parameters)**; all planned modifications act on the periphery (input, representation, bandwidth, augmentation), whereas in the other two the feature stage or segmentation is coupled to the model (Slide 6) |
| **After replication** | **Best performance** | Ma et al.: **zero accuracy loss**, 0.03–0.37 s; Lai et al.: 99.6% with **15.5×** the parameters; Zhu et al.: **−3.8 points** (Slide 7) |

**Weighted scoring** (weights: cost 35 / reproduction 30 / measurability 20 / novelty 15)

| | **PWA** | Raspberry Pi | MCU |
| --- | --- | --- | --- |
| Scores (cost / reproduction / measurability / novelty) | 5 / 5 / 4 / 3 | 3 / 4 / 5 / 2 | 2 / 3 / 4 / 5 |
| **Weighted total** | **4.50** | 3.55 | 3.15 |

> **"Cost" refers to total input, not hardware price**: the MCU is cheapest in hardware but most expensive in learning and debugging, hence a score of 2.
> The MCU gains 0.75 through full marks on novelty and remains **1.35 behind**; this is the only point on which the decision was finely balanced.

**Decision.** Adopt the **PWA** route. If a hardware-based deployment is required, the **Raspberry Pi 4B is the fallback** (0.5–1 day of work, all five quantities measurable) and the **MCU the last resort** (2–4 days, high failure probability).

**Acceptance criteria and fallback**
- **Acceptance:** replication within **98.65% ±1.5**; on-device **median ≤1 s, P95 ≤2 s**, and agreement with Python within **1e-3**.
- **Fallback:** retest on another device/browser → reduce the window to 1.5 s → keep the phone as UI only and run inference on a PC (without claiming on-device inference) → move to the Raspberry Pi.

> **The platform can change; the model and the evaluation protocol do not.**

---

## 9. Five Single-Axis Improvements (on the Ma et al. Baseline)

| # | Improvement | Evidence | Expected gain (E4) | Effort |
| --- | --- | --- | --- | --- |
| **I1** | Sampling rate 1 → **2 kHz** | Oliveira et al. (2022): 2–4 kHz sufficient; Torre-Cruz et al. (2023): abnormal components up to 500–600 Hz | +0.5 to +2 points | 0.5 day |
| **I3** | **S1 alignment** (translation only, no re-segmentation) | Liu et al. (2017): segmentation F1 98.5; Yin et al. (2022) report no ablation → **open question** | 0 to +2 points | 1 day |
| **I4** | **Input representation comparison** (waveform / MFCC / log-mel) | Shuvo et al. (2023): under an identical model, CWT performs best (+1.95) | +1 to +3 points | 1 day |
| **I7** | **Augmentation restricted to training folds** | Baghel et al. (2020): **+2.37 points** | +1 to +2 points | 0.5 day |
| **I12** | **Bandwidth** 20–1000 Hz → test 20–400 / 20–150 Hz | Baghel et al. (20–150 Hz), Zhu et al. (20–400 Hz), Oliveira et al. (spectral power mainly below 500 Hz) | 0 to +1.5 points | 0.5 day |

**Acceptance rule:** one axis at a time; a paired-bootstrap confidence interval excluding zero **and** a gain of at least **0.5 points**.

> **E4 denotes engineering estimate** — a predicted gain derived from first principles, neither measured nor reported in the source publications. Measurements will supersede these values.

---

## 10. Plan for the Next Two Weeks

**Deliverables.** A model that runs on a smartphone, together with four measured quantities: median and P95 latency, macro F1 with 95% CI, agreement with the Python implementation, and peak memory.

| Week | Work | Acceptance |
| --- | --- | --- |
| **Week 2** | PWA pipeline operational; **replication of Ma et al. on PC** | Results on the phone; **98.65% ±1.5** |
| **Week 3** | Five improvements (**I1 → I7 → I12 → I3 → I4**) | At least **2** improvements statistically significant |

> Remaining time (Week 4): on-device measurement and demonstration refinement (median/P95 latency, agreement within 1e-3); if Week 3 slips, Week 4 is used for completion.

---

## 11. References

1. MA Y, et al. An edge-device-compatible algorithm for valvular heart diseases screening using phonocardiogram signals with a lightweight convolutional neural network and self-supervised learning[J]. **Computer Methods and Programs in Biomedicine**, 2024, 243: 107906.
2. VU T, et al. Detecting abnormal heart sound using mobile phones and on-device IConNet[EB/OL]. **arXiv:2412.03267**, 2024.
3. LAI C, et al. A Robust and Efficient Workflow for Heart Valve Disease Detection from PCG Signals: Integrating WCNN, MFCC Optimization, and Signal Quality Evaluation[J]. **Sensors**, 2025, 25(21): 6562.
4. ZHU J, et al. LEPCNet: A Lightweight End-to-End PCG Classification Neural Network Model for Wearable Devices[J]. **IEEE Transactions on Instrumentation and Measurement**, 2024, 73: 2511111.
5. RAGUSA D, et al. Ultraefficient Compressed Phonocardiogram Classification on a Custom Embedded Neural Accelerator[J]. **IEEE Internet of Things Journal**, 2025.
6. MAI N-D, ULLAH K, CHUNG W-Y. Wearable PCG-Based Edge Detection of Heart Valve Disorders Using Tiny Physics-Informed Neural Networks[J]. **IEEE Transactions on Instrumentation and Measurement**, 2026, 75: 2515317.
7. OLIVEIRA J, et al. The CirCor DigiScope Dataset: From Murmur Detection to Murmur Classification[J]. **IEEE Journal of Biomedical and Health Informatics**, 2022, 26(6): 2524–2535.
8. TORRE-CRUZ J, et al. Detection of valvular heart diseases combining orthogonal non-negative matrix factorization and convolutional neural networks in PCG signals[J]. **Journal of Biomedical Informatics**, 2023, 145: 104475.
9. BAGHEL N, et al. Automatic diagnosis of multiple cardiac diseases from PCG signals using convolutional neural network[J]. **Computer Methods and Programs in Biomedicine**, 2020, 197: 105750.
10. SHUVO S B, et al. NRC-Net: Automated noise robust cardio net for detecting valvular cardiac diseases using optimum transformation method with heart sound signals[J]. **Biomedical Signal Processing and Control**, 2023.
11. LIU C, SPRINGER D, CLIFFORD G D. Performance of an open-source heart sound segmentation algorithm on eight independent databases[J]. **Physiological Measurement**, 2017, 38(8): 1730.
12. YIN Y, et al. Design of Abnormal Heart Sound Recognition System Based on HSMM and Deep Neural Network[J]. **Medical Devices: Evidence and Research**, 2022, 15: 313–322.
13. ZHAO Q, et al. Deep Learning in Heart Sound Analysis: From Techniques to Clinical Applications[J]. **Health Data Science**, 2024, 4: 0182.
14. KANNAN R, et al. Detection of Valvular Heart Diseases From PCG Signals Using Machine and Deep Learning Models: A Review[J]. **IEEE Access**, 2025, 13.
15. YASEEN, SON G Y, KWON S. Classification of heart sound signal using multiple features[J]. **Applied Sciences**, 2018, 8(12): 2344.
