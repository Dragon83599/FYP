# Speaker notes (v7-EN, 11 slides, 13–15 minutes)

> Read the bold text; `(pause)` indicates a half-second pause.
> Register: formal academic reporting. Do not say "we achieved 99%" — replication has not started.
> Figures quoted here are from the literature; state the source each time a figure is used.

---

## Slide 1 — Title and recommendation (30 s)

> Good morning. In the past month I have settled the deployment route for this project, and today I would like to **present the recommendation for your consideration**.
> **The recommendation is that this project deploy its classifier as a mobile web application — a Progressive Web App — rather than on a single-board computer or a microcontroller.**
> There are three rationales, ordered by the project timeline: **the PWA has the lowest cost before replication, the easiest 1:1 reproduction during replication, and the best on-device performance after replication.**
> `(pause)` I am aware that the microcontroller route is less explored in the literature; I will address why it was not selected.

---

## Slide 2 — Background and dataset (90 s)

> I will begin with the necessary background.
> **Valve opening and closure produce the first and second heart sounds.** Stenosis or regurgitation changes timbre, intensity and rhythm; **this is the physical basis for classification from a phonocardiogram.**
> **The dataset defines five labels:** normal, aortic stenosis, mitral stenosis, mitral regurgitation, and mitral valve prolapse.
> `(pause)` I compared four candidate datasets. **Yaseen** contains 1,000 recordings, 200 per class, sampled at 8 kHz, and is the only **single-label five-class** set. CirCor contains 5,282 recordings but addresses murmur detection and grading; PASCAL is multi-label; CinC 2016 is a binary task.
> **Yaseen was selected for four reasons**: it is the only single-label five-class set with all classes present; the classes are balanced, so no resampling is required; **Ma et al., Lai et al. and Zhu et al. all report on it**, which provides comparable figures; and its size makes five-fold cross-validation feasible within the remaining schedule.
> `(pause)` One limitation must be stated: **the dataset provides no subject identifiers**, so grouping is performed at the **recording level**.

---

## Slide 3 — Scope (40 s)

> The scope is deliberately narrow. There are two requirements only: **single-label five-class classification**, and **inference on the device without network access**.
> `(pause)` These two requirements lead to one conclusion: **the dominant cost of this project is not algorithmic but deployment-related** — moving the model onto a platform and obtaining measured figures.

---

## Slide 4 — Prior work on the three routes (80 s)

> Three deployment routes have been explored in the literature.
> **Mobile**: Ma et al. ran a PWA in a mobile browser on the Yaseen five-class task, reporting **98.65%** at **0.03–0.37 s**; Vu et al. also reported on-device inference on a phone.
> **Single-board computer**: nine studies, the most crowded route, with accuracies from 86.9% to 99.94% — **deployability does not imply quality**.
> **Microcontroller**: three studies, with on-device accuracies between 90.9% and 95.6%.
> `(pause)` Two points require emphasis. First, **only Zhu et al. report on-device results on the Yaseen five-class task, at 1.58 s**. Second, **model size differs by roughly two orders of magnitude** across the routes — approximately 3,000 to 53,000 parameters for MCUs, 20,193 for the mobile study, and 312,357 for the single-board study. **No published study compares the routes with matched parameter counts**, so any cross-platform claim of speed must be established by a controlled experiment.
> In addition, two reviews — Zhao et al. (2024, 99 papers) and Kannan et al. (2025, 199 papers) — **do not jointly compare deployment, five-class accuracy and parameter count**.
> For completeness: running the model on a PC only would be risk-free but would not satisfy the on-device requirement, and is therefore treated as a baseline only.

---

## Slide 5 — Rationale 1: lowest total cost before replication (80 s)

> The first rationale concerns cost before replication. **The comparison concerns total input — hardware, learning effort, environment setup and iteration — not hardware price alone.**
> I will address two common misconceptions.
> **First, that the microcontroller is the cheapest option.** Its hardware is indeed the cheapest, at ¥30–60, but it requires **2–4 days** of work; three additional days correspond to **20% of the final two weeks**, which is considerably more expensive than a ¥400 board.
> **Second, that writing C is the more rigorous choice.** The objective is to obtain measured deployment figures, not toolchain proficiency; **learning effort is a real cost**.
> `(pause)` The PWA route requires no hardware purchase, no PCB or power-supply design, no firmware toolchain, and offers an edit–refresh iteration cycle; **the phone is already carried by the user**, so neither the point-of-care use nor follow-up requires a dedicated board, display or power supply, and the system is operational within half a day.
> The cost must be acknowledged as well: **aligning the JavaScript preprocessing with the Python implementation requires approximately one to two days.**

---

## Slide 6 — Rationale 2: the simplest baseline is the easiest to extend (80 s)

> The second rationale concerns the replication stage; the point is not how quickly the study can be copied, but **how readily the copied baseline can be extended.**
> **The Ma et al. baseline is minimal**: three convolutional layers and one dense layer, 20,193 parameters. Every planned modification acts **outside the network** — sampling rate, representation, bandwidth and augmentation leave the network internals untouched.
> `(pause)` By contrast, **in Lai et al. the MFCC feature stage is coupled to the network**, so changing features changes the network; **in Zhu et al. segmentation is a pre-requisite**, so changing it requires re-alignment and is bound to the model.
> This brings two advantages.
> **First, only a simple baseline permits single-axis ablation.** The requirement is to change one variable at a time and attribute the difference cleanly; with a complex baseline a single change touches several components and the ablation becomes confounded.
> **Second, the headroom is transparent.** For a 20,193-parameter model the limiting factors are input information and training strategy rather than depth, so all five improvements act on the periphery — **none of them redesigns the network.**

## Slide 7 — Rationale 3: best performance after replication (90 s)

> The third rationale concerns on-device performance. Three studies address the same task and represent three deployment strategies.
> **Ma et al.**: mobile, PWA, 20,193 parameters, float32, **98.65% with no accuracy loss**, latency 0.03–0.37 s.
> **Lai et al.**: Raspberry Pi, **312,357 parameters — 15.5 times those of Ma et al.** — also float32, **99.6% with no accuracy loss**, latency 1.87 ms.
> **Zhu et al.**: microcontroller, 52,670 parameters, **int8 with trimming**, 99.4% on a PC but **95.6% on the device**.
> `(pause)` Three observations.
> **First, accuracy loss originates from quantization and trimming, not from the platform.** Ma et al. and Lai et al. both run float32 with no loss; only Zhu et al. trimmed the model, losing 3.8 points.
> **Second, accuracy can be bought with parameters.** Lai et al. report the highest accuracy, but with 15.5 times the parameters and from a **grid peak without a confidence interval**.
> **Third, the PWA route offers the best trade-off**: roughly one-fifteenth of the parameters, a difference of 0.95 points, latency well within requirements, and no quantization loss.
> `(pause)` The only quantity that cannot be measured is energy: a smartphone cannot be connected to a power meter. This is not a limitation here, because the device is charged daily, the model is small, and no power budget has been specified. The remaining four quantities — latency, macro F1 with confidence intervals, agreement with Python, and peak memory — are measurable, with acceptance thresholds given on the next slide.

---

## Slide 8 — Summary: three rationales, one decision (90 s)

> I will consolidate the three rationales; they support a single decision.
> **Before replication**, the cost is the lowest: no hardware purchase and operational within half a day, while the microcontroller route requires 2–4 days, of which three additional days equal 20% of the final two weeks.
> **During replication**, the baseline is the easiest to extend: Ma et al. use three convolutional layers and one dense layer, and every planned modification acts on the periphery, whereas in the alternatives the feature stage or the segmentation is coupled to the model.
> **After replication**, performance is the best: no accuracy loss and sub-second latency, whereas the single-board study requires 15.5 times the parameters and the microcontroller study loses 3.8 points.
> `(pause)` When these considerations are weighted — cost 35, reproduction 30, measurability 20, novelty 15 — the totals are **4.50 for the PWA, 3.55 for the Raspberry Pi and 3.15 for the microcontroller**. **"Cost" here refers to total input, not hardware price**: the microcontroller is cheapest in hardware but most expensive in learning and debugging. **The microcontroller route gains 0.75 through novelty and remains 1.35 behind**; this is the only point on which the decision was finely balanced.
> **The decision is therefore to adopt the PWA route.** If a hardware deployment is required, the eligible Raspberry Pi 4B is the fallback, and the microcontroller the last resort.
> `(pause)` Acceptance criteria have also been defined: replication within **98.65% ±1.5**; on-device **median latency ≤1 s, P95 ≤2 s**, and agreement with Python within **1e-3**. If these are not met, the fallback sequence is: retest on another device, reduce the window to 1.5 s, retain the phone as a user interface with inference on a PC, or move to the Raspberry Pi.
> **The platform can change; the model and the evaluation protocol do not.**

---

## Slide 9 — Five single-axis improvements (70 s)

> Five improvements will be evaluated on the Ma et al. baseline, each modifying a single factor.
> **Sampling rate from 1 kHz to 2 kHz**: Oliveira et al. report that 2–4 kHz is sufficient, and Torre-Cruz et al. report abnormal components up to 500–600 Hz, whereas 1 kHz sampling has a 500 Hz Nyquist limit and therefore truncates the band.
> **S1 alignment**: Liu et al. report a segmentation F1 of 98.5, but no study compares segmented and unsegmented pipelines, so the benefit is unquantified; this requires one day to test.
> **Input representation**: waveform, MFCC and log-mel representations are compared using an identical model, following Shuvo et al., who report a 1.95-point difference between transformations.
> **Augmentation restricted to training folds**: Baghel et al. report a gain of 2.37 points.
> **Bandwidth**: 20–400 Hz and 20–150 Hz will be tested, since spectral power lies mainly below 500 Hz.
> `(pause)` The acceptance rule is uniform: one axis at a time, a paired-bootstrap interval excluding zero, and a gain of at least 0.5 points. **The expected gains are engineering estimates (E4), not measured or published values**; measurements will supersede them.

---

## Slide 10 — Plan for the next two weeks (50 s)

> The deliverables are a model that runs on a smartphone, together with four measured quantities: median and P95 latency, macro F1 with a 95% confidence interval, agreement with the Python implementation, and peak memory.
> `(pause)` **In Week 2** the PWA pipeline will be completed and Ma et al. will be replicated on a PC, with the acceptance criterion of 98.65% ±1.5. **In Week 3** the five improvements will be evaluated, with the criterion of at least two statistically significant gains.
> **The remaining time, Week 4, will be used for on-device measurement and demonstration refinement**; if Week 3 overruns, it will be used for completion.

---

## Slide 11 — References (10 s)

> The final slide lists all sources cited in the presentation, in full bibliographic form. The body uses author–year citations throughout.
> `(do not read the list; refer to the relevant entry if a question arises)`

---

## Timing

| Section | Slides | Minutes |
| --- | --- | --- |
| Recommendation | 1 | 0.5 |
| Background and dataset | 2 | 1.5 |
| Scope | 3 | 0.7 |
| Prior work | 4 | 1.3 |
| Rationales 1–3 | 5–7 | 4.2 |
| Summary and decision | 8 | 1.5 |
| Improvements | 9 | 1.2 |
| Two-week plan | 10 | 0.8 |
| References | 11 | 0.2 |
| **Total** | | **≈ 11.9** |

> Reserve one to two minutes for questions. If time is short, condense Slide 9 by naming the five improvements only.

## Anticipated questions (not on the slides)

**Q: Is a phone genuinely an edge device?**
> Yes. Inference runs in the browser on the device, without network access; the study by Ma et al. is itself a mobile deployment.

**Q: Why not the Raspberry Pi, which is more established?**
> It is established, but it costs ¥400–600, draws 2.7–5 W, and is not portable; its two original roles — on-device evidence and a floating-point baseline — are already covered by the PWA and by the PC.

**Q: Is the PWA insufficiently accurate?**
> Ma et al. report 98.65% on a phone. The WebGL texture limit of 4096 constrains model size, which is precisely the regime of this project.

**Q: Why was the microcontroller route excluded, given that it is the least explored?**
> That is the principal cost: the route is unexplored, and exploiting it would require 2–4 days of toolchain work with a high probability of failure within the available schedule.
