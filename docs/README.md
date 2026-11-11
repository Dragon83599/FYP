# docs 目录导航（2026-11-11 更新）

> **新来的先读 [`handoff-2026-11-11.md`](handoff-2026-11-11.md)**：当前状态、三个存储位置、
> 以及**本文这张导航里也过时了的条目**（本文的"29 篇 / ADR-033"等数字停在 2026-10-29）。
>
> 文件变多之后，按"**现在要用的**"和"**归档的**"分开。新来的先看这张表。

## 一、当前主线（五分类 VHD 端侧筛查）

| 文件 | 作用 | 什么时候看 |
| --- | --- | --- |
| **`workflow.md`** | **执行手册**（参数已定，可照做）| **每次动手前** |
| `plan-5class-vhd.md` | 设计文档：为什么这样设计、各节依据 | 写论文/答辩 |
| `engineering-decision-register.md` | 决策历史（ADR-001…057，**040 为跳号**）| 改参数前必查 |
| `workflow-evidence.md` | 每个步骤的逐条文献证据 + 强弱 | 被问"依据是什么" |
| `parameter-ledger.md` | 每个参数的出处（文献 / E4）| 写方法章节 |
| `parameter-rationale.md` | 每个参数的一句话理由 + 怎么验证 | 答辩前背 |
| `engineering-decision-guide.md` | 怎么写 ADR 的规则 | 新增决策时 |
| **`handoff-2026-11-11.md`** | **交接文档**：当前状态、三个存储位置、文档陷阱、下一步 | **换对话开工前** |
| `tasks/w3-plan.md` | **唯一任务队列**（T1–T13，验收标准与状态）| 每次动手前 |
| `w3-robustness.md` | 鲁棒性实验的全部结果与结论（12 节）| 写实验章节 |
| `deployment-platform-options.md` | 15 个部署平台候选 + 筛选 | 选板时 |
| `tasks/w1-preprocess-checklist.md` | W1 开工清单 | 第一步 |
| `tasks/w3-spike-plan.md` | 部署 spike 清单 | W3 |

## 二、文献

| 文件 | 作用 |
| --- | --- |
| `literature/README.md` | 29 篇的总表 + 阅读状态（谁读过）|
| `literature/01–29-*.md` | 每篇的笔记（含原话与纠正）|
| `literature/comparison-matrix.md` | **T1/T1b/T2/T3/T3b/T4 六张对比表** |
| `literature/why-accuracy-alone-is-not-enough.md` | 12 个反例（泄漏/选型/口径）|
| `literature/feature-model-inventory.md` | 特征与模型的穷举 + 筛选 |
| `literature/reading-guide-unread.md` | 未读篇目的 20–30 分钟导读 |
| `refs/` | PDF / XML 全文（按 `年份-作者-关键词` 命名）|

## 三、学习材料

| 文件 | 作用 |
| --- | --- |
| `learn/parameter-basics.md` | 参数扫盲：采样率、帧、mel、卷积、GAP、过拟合…（15 节）|

## 四、过程记录

| 文件 | 作用 |
| --- | --- |
| **`tasks/w1-report-2026-10-30.md`** | **W1 实施报告**：产物哈希、逐步结果、验收对照、6 处文档冲突 |
| `tasks/workflow-decisions.md` | 工作流逐步确认的 11 条 |
| `tasks/2026-10-29-workflow-revision-brief.md` | 那天的任务卡 |
| `tasks/2026-10-29-revision-summary.md` | 改了什么/为什么 |
| `progress-2026-10-17.md`、`progress-2026-10-18.md` | 进度快照 |
| `handoff-2026-10-18/19.md` | 交接记录 |
| `pcg-dataset-audit-2026-10-17.md` | 数据集审计（8k/时长/类别）|

## 五、归档（旧方向：BLE / 固件 / S1S2 / 杂音检测）

`archive/2026-10-legacy/` 里是 2026-10 之前的方向留下的文档（蓝牙名词表、两周计划、六篇参考文章拆解等）。
**不再维护**，但保留可查。

旧方向仍在引用中的文件（未归档）：`mvp-output-contract.md`、`phase0-interface-freeze.md`、`link-a-*`、
`host-resource-budget.md`、`iram-and-display-budget.md`、`connectivity-topology.md`、`ai-baseline-plan.md` 等。

## 六、整理规则（以后新加文件时）

1. **当前主线**的文档放 `docs/` 根目录，数量控制在 10 个以内；
2. 文献一律进 `docs/literature/`，命名 `NN-作者-年份-关键词.md`；
3. 过程记录进 `docs/tasks/`；
4. 学习材料进 `docs/learn/`；
5. 旧方向文档进 `docs/archive/<年月>-<主题>/`，**移动后更新引用路径**；
6. **不删除**任何有历史价值的文档——归档即可。
