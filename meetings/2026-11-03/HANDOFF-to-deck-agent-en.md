# Handoff to the slide-design agent (v7-EN, 2026-10-31)

> The source of content is **`outline-en.md`** (**11 slides, English**). You are designing **slides**, not writing a paper or a proposal.
> **The deck is an argument for one decision**: deploying the classifier as a **Progressive Web App (PWA)**.
> Structure: **Background → Scope → Prior work → Three rationales (before / during / after replication) → Summary and decision → Planned improvements → Two-week plan → References.**
> Citations in the body use **author–year** (e.g. "Ma et al., 2023"). **Do not use numeric note identifiers.**
> Speaker notes are provided in `SCRIPT-en.md`.

## 1. Purpose and audience

- **Undergraduate final-year project (FYP) group meeting**, **13–15 minutes** of presentation, followed by discussion.
- Audience: the supervisor and peers, who are familiar with machine learning but **not** with phonocardiography or embedded deployment.
- Language: **English**, formal academic reporting register. Complete sentences in body text; no contractions; no rhetorical questions.
- **No experimental results are reported.** Replication has not started; every accuracy figure is taken from the literature.

## 2. Authoritative sources

1. **`meetings/2026-11-03/outline-en.md`** — slide content (this is the master document)
2. `meetings/2026-11-03/SCRIPT-en.md` — verbatim speaker notes
3. `docs/engineering-decision-register.md` — decision records ADR-050/051/052
4. `docs/literature/edge-deployment-landscape.md` — survey of the 15 on-device studies
5. `docs/literature/17-*.md` — full-text notes on the study being replicated (Ma et al.)

**If any figure does not reconcile with the master document, stop and ask; do not recompute or round.**

## 3. Formatting requirements

| Item | Requirement |
| --- | --- |
| Aspect ratio | 16:9 (13.333 × 7.5 in) |
| Slide titles | Declarative statements, not topic labels ("Rationale 1 — Lowest total cost before replication") |
| Body font | ≥ 16 pt; tables ≥ 12 pt; table score ≤ 8 rows |
| Tables over prose | Approximately 80% of the content is tabular; use tables rather than paragraphs wherever possible |
| **Slide 8 (core)** | Three-row summary table + scoring table; **keep the slide short** — anticipated objections belong in the speaker notes, not on the slide |
| **Slide 5 note** | The cost criterion must be labelled **total input, not hardware price** — a footnote on the slide is sufficient |
| Source line | Small footer per slide: `→ Author et al., Year / ADR-0xx` |
| Speaker notes | Take verbatim from `SCRIPT-en.md` |
| Numbering | Avoid heavy numbers; where numbers are used (e.g. 20,193), keep them exact and set them in a consistent format |

## 4. Slide-by-slide outline (do not reorder)

| Slide | Content | Role |
| --- | --- | --- |
| **1** | Title and recommendation | State the recommendation and the three rationales immediately |
| **2** | Background: five-class classification and dataset selection | S1/S2 basis; five labels; four candidate datasets; four reasons for Yaseen; no subject IDs |
| **3** | Scope | Two requirements only: single-label five-class; on-device, offline inference |
| **4** | Prior work on the three routes | 2 / 9 / 3 studies; parameter column mandatory (20,193 / 312,357 / 3,058–52,670); caveat that no parameter-matched comparison exists |
| **5** | Rationale 1 (before replication): lowest total cost | Hardware / learning / environment / iteration; two misconceptions addressed; cost figure is total input |
| **6** | Rationale 2 (during replication): **simplest baseline, easiest to extend** | Three-column comparison: parameters, architecture, **where a modification lands**, cost of one modification, preprocessing chain; two observations (single-axis attribution / transparent headroom) |
| **7** | Rationale 3 (after replication): best on-device performance | Ma / Lai / Zhu; accuracy loss 0 / 0 / −3.8; accuracy bought with parameters (15.5×) |
| **8** | Summary: three rationales, one decision | Three-row table (stage / conclusion / evidence) + scoring 4.50 / 3.55 / 3.15 + decision + acceptance and fallback |
| **9** | Five single-axis improvements | One table: improvement / evidence / expected gain (E4) / effort; acceptance rule |
| **10** | Plan for the next two weeks | Deliverables; Week 2 and Week 3; Week 4 as contingency |
| **11** | References | GB/T 7714-style entries, 15 items |

## 5. Four points of intellectual honesty that must be preserved

1. **Slide 4** — no published study compares the three routes with matched parameter counts; cross-platform speed claims require a controlled experiment.
2. **Slide 7** — the MCU-based study loses 3.8 points because the model was **trimmed before deployment**, not because of the platform.
3. **Slide 8** — the MCU route gains 0.75 on novelty and remains **1.35 behind**; the decision was finely balanced.
4. **Slide 9** — every expected gain is an **engineering estimate (E4)**, not a measured or published result.

## 6. Numeric reporting rule (mandatory)

Every accuracy figure must state **where it was measured** and **in what numeric format**:

| Correct | Incorrect |
| --- | --- |
| `on-device, STM32F405, int8: 95.6%` | `95.6%` |
| `PC, float32: 99.4%` | `99.4%` |
| `on-device: not reported` | PC figure substituted into the on-device column |

Source: ADR-031 supplement. For the same model the two settings can differ by 3–4 points (Zhu et al.: 99.4 → 95.6).

## 7. Prohibited

1. Do **not** invent results. Replication has not started; no "we obtained X%" statement is permissible.
2. Do **not** alter figures, including decimal places.
3. Do **not** add a "questions for the supervisor" or "pending decision" slide.
4. Do **not** use promotional language ("state of the art", "breakthrough", "world-leading").
5. Do **not** present Ragusa et al.'s latency as smartphone inference (it was measured on GPUs / a custom NPU).
6. Do **not** cite Zhang et al.'s 99.94% as a credible figure (augmentation performed before splitting; leakage).
7. Do **not** merge or split slides without stating the reason.

## 8. Three visual anchors

| Slide | Content | Treatment |
| --- | --- | --- |
| 1 | Recommendation + three rationales | Horizontal three-step strip ordered by timeline |
| 4 or 8 | Scoring | Compact matrix; **show the arithmetic** (1.75 + 1.50 + 0.80 + 0.45 = 4.50) |
| 10 | Two-week plan | Two-row timeline table with acceptance criteria in the right column |

## 9. Key figures (use exactly)

| Figure | Meaning |
| --- | --- |
| **20,193 / approx. 81 kB / 98.65%** | Ma et al.: parameters / model size (estimate) / accuracy (CI 98.555–98.745) |
| **0.03–0.37 s / 0.6 mAh** | Ma et al.: on-device latency / energy for 10 inferences |
| **312,357 (15.5×)** | Lai et al.: parameters |
| **52,670 / 1.58 s / −3.8 points** | Zhu et al.: parameters / latency on STM32F405 / accuracy loss (trimming) |
| **3,058** | Mai et al.: parameters (416.9 kFLOPs) |
| **5.31 ms / 58.9 µJ / 90.91%** | Mai et al.: latency / energy / on-device accuracy (int8) |
| **18.7 ms / 1.51 mJ** | Ragusa et al.: latency / energy (NPU) |
| **4.50 / 3.55 / 3.15** | Weighted scores: PWA / Raspberry Pi / MCU |
| **0.95 / 1.35** | PWA advantage over Raspberry Pi / MCU |
| **±1.5 / ±3** | Replication acceptance / failure threshold (in percentage points) |
| **≤1 s / ≤2 s / 1e-3** | On-device acceptance: median / P95 / agreement with Python |
| **+2.37 / +1.95 / −0.96** | Baghel et al. augmentation gain / Shuvo et al. representation gain / Mai et al. quantization loss |

## 10. Pre-delivery checklist

- [ ] Slide 1 states the recommendation in one sentence.
- [ ] Slide 4 retains the **parameter** column for all three routes.
- [ ] Slide 5 labels the cost criterion as total input, with a note that the MCU is cheapest in hardware.
- [ ] Slide 7 shows **0 / 0 / −3.8** accuracy loss and the 15.5× parameter ratio.
- [ ] Slide 8 shows the arithmetic behind 4.50 / 3.55 / 3.15.
- [ ] Every accuracy figure states the measurement setting and numeric format.
- [ ] No slide implies that results have been obtained.
- [ ] Reference slide lists all 15 sources cited in the body.
