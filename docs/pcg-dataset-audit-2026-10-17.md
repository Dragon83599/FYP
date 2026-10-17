# 心音 PCG 数据集逐平台审计（2026-10-17）

> 状态：候选审计，不是数据集采购或模型训练的最终冻结。
> 关联：ADR-007、ADR-008、`docs/ai-baseline-plan.md`。
> 范围：边缘心音筛查第一版所需的信号质量、S1/S2、心率、收缩期/舒张期、
> 杂音有/无和风险提示。不把具体疾病诊断、杂音分级、位置或放射方向纳入 MVP。

## 1. 证据等级和统计口径

| 等级 | 本次含义 |
| --- | --- |
| E1 | 数据集官方页、官方 API、DOI、PhysioNet 页面或官方文件清单 |
| E2 | 论文/可复现公开文件检查，例如本次读取 WAV/AIFF/CAF 头、ZIP 中央目录、CSV |
| E3 | 平台自述、搜索结果或未说明条件的镜像页，只能用于发现 |
| E4 | 工程估算、假设或经验值，不能单独冻结数据集 |

“受试者数”和“录音数”优先采用官方总量；如果开放下载只有训练子集，会同时写出
公开子集数量。镜像页、转载页和搜索摘要不能替代原始来源。

## 2. 平台逐项核对

| 平台 | 是否核验到 PCG | 本次实际发现 | 结论 |
| --- | --- | --- | --- |
| 遇见数据集 | 有索引候选 | “心音”意图接口返回 20 个候选 ID 和大量相关标签；详情接口返回“非法操作”，无法核对文件、许可证和患者级字段 | 只作发现入口，不作证据源 |
| Kaggle | 有，但以镜像为主 | 存在 CirCor、PhysioNet 2016、PASCAL 和 HLS-CMDS 镜像；`kinguistics/heartbeat-sounds` 实为 PASCAL 2011，平台标 CC0，而原始站未声明该许可证 | 不作为主要下载源 |
| GitHub（用户给出的 bio-datasets） | 否 | 该链接实际指向 `bioml-tools/bio-datasets`，内容是分子/生物数据构建器，不是心音数据；GitHub 上另有 HLS-CMDS、ZCHSound 等独立仓库 | 必须逐仓库追踪原始论文、许可证和版本 |
| Hugging Face | 有，但以第三方镜像为主 | `bjornsing/PCG-signals` 为 471,229,960 B，大小与 CirCor 包一致，却标 CC BY 4.0；原 CirCor 为 ODC-BY 1.0。其他条目多为子集或未说明来源 | 不作为训练或许可证依据 |
| arXiv | 否，arXiv 不托管数据集 | 可找到 CirCor、BMD-HS 等论文；BMD-HS 论文给出 864 条录音，但截至本次审计，其官方 GitHub 链接返回 404 | 只能用于论文线索和证据复核 |
| OpenDataLab | 有 | 有 CirCor 1.0.1 镜像，473,148,379 B，标 ODC-BY 1.0；搜索 PCG、phonocardiogram、心音、murmur 未找到其他可核验 PCG | 可作备用镜像，不替代 PhysioNet 1.0.3 |
| 阿里云天池 | 未核验到 | 站内定向搜索主要命中心脏病表格、语音等；官方数据集接口返回 403，无法完成全量复核 | 不能据此认为平台没有，但当前无可用证据 |
| Google Dataset Search | 索引服务，不托管数据 | 搜索返回 100+ 条，原始提供方仍是 Kaggle、Mendeley、PhysioNet 等；同时发现 CQUniversity 的 PhysioNet 2016 镜像被重新标为 CC BY-NC-SA 4.0 | 只作跨站发现和许可证冲突检查 |
| FindData | 未核验到 | 平台是科学数据搜索引擎；搜索接口需要前端签名，本次请求返回 400 | 只作发现入口，不作原始数据来源 |
| Mendeley Data | 有，且有原始数据集 | 找到 Children Heart Sound、HLS-CMDS、Heart Valve Regurgitation、HSBP-DB、胎儿 PCG 等；文件 API 可核对文件数和字节数 | 可作候选原始来源，但逐数据集核对许可证 |
| ModelScope 魔搭 | 否 | 官方数据 API 对 `heart sound`、`phonocardiogram`、`PCG`、`心音`、`murmur`、`CirCor` 均返回 0 条 | 不纳入本项目数据集池 |

## 3. 数据集对比

### 3.1 主表

| 数据集和官方链接 | PCG | 采样率、时长、受试者、录音数 | 标签 | 患者级 | 元数据 | 许可证和获取 | 证据 | 用途判断 | 主要限制 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| [CirCor DigiScope 1.0.3](https://physionet.org/content/circor-heart-sound/1.0.3/) | 是 | 4 kHz；4.8-80.4 s；官方总 1,568 人/5,272 条；公开训练集 942 人/3,163 条，排除 Unknown 后 874 人/3,007 条；总量 33.5 h | Murmur Present/Absent/Unknown；S1/S2 分割；Outcome Normal/Abnormal；高质量片段可筛信号质量 | 是，显式 subject ID，官方按患者隔离 | 年龄类别、性别、身高、体重、孕期、听诊位置、campaign；设备需从论文补充 | ODC-BY 1.0；开放下载，无需申请；ZIP 449.5 MB，解压 558.9 MB | E1 官方页；E2 本地完整性与头文件复核 | MVP 主训练集；S1/S2 首选；风险提示候选 | 儿科为主、类别不平衡、40% 私有、无统一设备字段 |
| [ZCHSound](https://github.com/WeiJieOvO/ZCHSound-Dataset) | 是 | 8 kHz、16-bit、单声道；实际 1,259 条/1,259 人，941 clean + 318 noise；6.46-82.66 s，均值 23.77 s，总量 8.31 h；ZIP 339.6 MB | 诊断 Normal/VSD/ASD/PFO/PDA；clean/noise 质量分组；无杂音标签，无 S1/S2 边界 | 是，一人一条，文件名即 subject ID | CSV 有性别、年龄天数和诊断；传感器、采样率、位置、检查所见、检查结论、信号质量列存在但 941 条 clean 记录均为空 | 论文页为 CC BY 4.0；GitHub 无 LICENSE；官网当前处于备案不可用；需作者确认数据许可 | E1 DOI/仓库；E2 论文和 ZIP/CSV/WAV 审计 | 外部儿科验证；信号质量候选；未来疾病诊断，不进入 MVP 杂音主训练 | README 的 685 人/50 h 与当前 ZIP 不符；单一听诊位置；数据许可未冻结 |
| [PASCAL CHSC2011 Dataset A](https://www.peterjbentley.com/heartchallenge/) | 是 | 实际 CAF 44.1 kHz、16-bit；1-30 s；受试者数未披露；176 条 | Normal、Murmur、Extra Heart Sound、Artifact；仅 normal 训练集给 S1/S2 位置 | 否，文件名只有时间戳，无法可靠患者分组 | iStethoscope Pro iPhone 来源；无年龄、性别、患者 ID、标准位置字段 | 官方页未声明明确许可证；开放下载；WAV 包约 85.3 MB | E1 官方页；E2 本地文件头 | 噪声/伪影压力测试；S1/S2 辅助 | 许可证不清、无患者级切分、测试标签 withheld |
| [PASCAL CHSC2011 Dataset B](https://www.peterjbentley.com/heartchallenge/) | 是 | 实际 AIFF 4 kHz、16-bit；1-30 s；文件名可恢复 171 个 ID；656 条，其中 461 条训练有标签、195 条测试无标签 | Normal、Murmur、Extrasystole；normal 训练集给 S1/S2 位置 | 可重建；但官方训练/测试有 125 个患者 ID 重叠，不能沿用官方切分 | DigiScope 来源；文件名含记录编号；缺少年龄、性别和明确位置字典 | 未声明明确许可证；开放下载；B 包约 30.2 MB | E1 官方页；E2 本地文件头与 ID 统计 | 杂音外部验证和患者级重划分候选 | 含未公开标签测试集；需要完全重做 patient split；许可证不清 |
| [PhysioNet/CinC 2016](https://physionet.org/content/challenge-2016/1.0.0/) | 是 | 2 kHz；5 s 到 120 s 以上；公开训练 3,126 条；官方页未给逐条 subject ID，配套论文核心集口径为 2,435 条/1,297 人 | Normal、Abnormal、Noisy/Unsure；更新质量标注；提供手工 S1/S2 分割；无独立 murmur Present/Absent | 部分满足；官方训练/测试患者隔离，但公开包缺 patient ID，不能自行重做可信患者级切分 | 数据库 A-F、九个位置和多种设备；年龄、性别主要只有论文汇总，不是逐条字段 | ODC-BY 1.0；开放下载；`training.zip` 181.3 MB，完整项目 1,011.4 MB | E1 官方页；E2 论文 | 外部压力测试；S1/S2 研究备选；不进入主患者级基线 | 患者映射缺失、标签只有正常/异常、设备异质 |
| [HLS-CMDS](https://data.mendeley.com/datasets/8972jxbpmp/3) | 是 | 4 kHz、16-bit、单声道；每条 15 s；临床模拟人，不是真实患者；535 条，其中 50 条心音、50 条肺音、145 条混合音和 290 条源分量 | Normal、S3/S4、房颤、AV block、多种收缩期/舒张期杂音等；无 S1/S2 边界 | 否，模拟人；不能做患者级结论 | 性别设定、心音类型、肺音类型、12 个听诊位置；CSV 完整 | MIT；GitHub 42.5 MB；Mendeley/Kaggle 有镜像 | E1 DOI/README；E2 WAV 头与 CSV | 流水线、算法回归、演示；不能做临床验证 | 合成/过滤音频，无真实人群和患者级统计 |
| [Children Heart Sound - Normal & Abnormal](https://data.mendeley.com/datasets/5447z7m2rr/1) | 是 | 60 条 WAV；正常/异常各 30 条；本次样本观察到 22.05 kHz 和 44.1 kHz 混用；样本约 6.1-9.0 s；受试者数未披露 | Normal/Abnormal | 否，`0_*`/`1_*` 不是可验证患者 ID | 无年龄、性别、设备、位置、质量字段 | CC BY 4.0；开放下载；60 条 WAV 约 32 MB | E1 DOI/API；E2 样本头 | 冒烟测试可选；不建议用于训练或验证 | 太小、采样率不统一、无患者级信息 |
| [HSBP-DB](https://data.mendeley.com/datasets/fwx556cgjy/1) | 是 | 4 kHz；50 名成人、400 条；休息 5 次/人，运动后 3 次/人；ZIP 27.26 MB | 血压四分类 Normal/Elevated/HTN-1/HTN-2；不是杂音或心脏瓣膜诊断 | 是，文件名含 participant ID 和 session | 性别/年龄汇总、参与者和 session、休息/运动、SBP/DBP/MAP；Littmann CORE 8480，Erb 点 | CC BY 4.0；开放下载 | E1 DOI；E2 文件 API | 未来成人风险或血压研究备选；不属于当前 MVP 输出 | 无杂音/S1-S2；单中心年轻成人；血压不是当前产品输出 |
| [Heart Sound Regurgitation](https://data.mendeley.com/datasets/jfbdvjw6sf/1) | 是 | 4 kHz、16-bit；100 名受试者、400 条；6.06-30 s，均值 20.1 s；ZIP 64.35 MB | AR、MR、TR、ME、Normal；每名受试者多个位置 | 是，文件名含患者 ID 和位置字母 | Littmann 3200；三个听诊位置；无年龄/性别字段 | CC BY 4.0；开放下载 | E1 DOI/API；E2 文件头和文件名统计 | 未来瓣膜反流疾病诊断候选 | 无 S1/S2、无信号质量、无杂音分级；需确认标签字典 |

### 3.2 发现但不能作为可用候选的数据

| 名称 | 发现 | 结论 |
| --- | --- | --- |
| BUET BMD-HS | 论文称 864 条、5 类、多标签瓣膜病；官方 GitHub 链接在 2026-10-17 返回 404 | 只有论文，当前不可下载，不进入候选 |
| CQUniversity PCG 数据集 | DataCite 显示 10.25946/32249484，内容明确是 PhysioNet 2016 的 3,126 条镜像，许可证改为 CC BY-NC-SA 4.0 | 与原始 ODC-BY 冲突，使用 PhysioNet 原站 |
| Kaggle Heartbeat Sounds | PASCAL A/B 镜像，`set_a.csv`、`set_b.csv` 和 timing 文件；平台标 CC0 | 许可不能覆盖原作者未声明许可，不能按 CC0 使用 |
| Hugging Face PCG 条目 | `bjornsing/PCG-signals`、`luyangliuable/circor-heart-sound` 等 | 大小、样本和许可证显示为镜像/子集，不作为原始依据 |
| OpenDataLab CirCor | 1.0.1 镜像，473 MB，页面仍写 5,282 条 | 版本旧且统计与 1.0.3 不一致，使用 1.0.3 原站 |

## 4. 候选方案比较

决策问题：在当前 CirCor 已成为主训练集的条件下，第一版是否立即引入额外数据集，
以及引入哪一组，才能提高信号质量、S1/S2、杂音和风险提示的泛化能力，同时不增加
无法验证的许可和患者级划分风险。

| 指标 | 方案 0：维持 CirCor-only | 方案 A：CirCor + ZCHSound + PASCAL B | 方案 B：CirCor + PhysioNet 2016 | 证据和说明 |
| --- | --- | --- | --- | --- |
| 功能匹配 | 杂音、S1/S2、质量、风险都有，但仅儿科巴西队列 | 增加中国儿科质量/诊断和真实噪声/杂音；覆盖最全 | 增加跨设备正常/异常压力测试；缺独立杂音标签 | E1+E2 |
| 数据规模 | 3,163 条公开训练 | 增加 1,259 + 461 条有标签录音 | 增加 3,126 条训练录音 | E1+E2 |
| 患者级划分 | 完整支持 | CirCor/ZCHSound 完整；PASCAL B 需重切且丢弃无标签测试 | PhysioNet 2016 缺公开 patient ID，难以自行重切 | E1+E2 |
| 信号链路 | 全部可 4 kHz | PASCAL A/B 与 ZCHSound 有不同原始采样率，需要统一重采样 | 2 kHz 上采样到 4 kHz，信息不会增加 | E2 |
| 功耗/体积/供应链 | 无硬件影响 | 无硬件影响；只增加约 0.9 GB 原始数据和训练时间 | 无硬件影响；增加约 0.18 GB | E4 |
| 开发难度 | 最低 | 中；重点是许可核验、采样率、患者 ID 和队列差异 | 中；重点是 source/device 偏移和患者级限制 | E2+E4 |
| 许可风险 | 低 | 中高；ZCHSound 数据许可、PASCAL 许可未冻结 | 低；ODC-BY 1.0 | E1+E3 |
| 进度风险 | 低 | 中；需要先完成许可和元数据审计 | 低到中 | E4 |
| 可测试性 | 高 | 高，前提是先固定 manifest、hash 和拆分协议 | 中，patient ID 缺失降低可复现性 | E1+E2 |

当前候选选择是方案 A 的“有条件获取”：立即使用 CirCor；下载并审计 ZCHSound；
PASCAL B 只在许可证或作者书面条件确认后进入训练。该结论不是数据集冻结，也不改变
ADR-007/ADR-008 的边缘推理约束和 AUD1/LKA1 接口。

## 5. 第一版下载和接入顺序

1. CirCor 1.0.3：必选。用官方 SHA256/CRC 校验，保留 942 人/3,163 条完整训练包，
   以 874 人/3,007 条 Unknown 排除后的队列作为可复现基线。
2. ZCHSound：值得下载并审计。先核对当前 ZIP 的 1,259 条与两份 CSV，确认数据许可
   后再合并；在许可未书面确认前，不进入正式训练集。
3. PASCAL Dataset B：有条件第三项。它补充真实噪声杂音和 S1/S2，但必须只用 461 条
   有标签记录，按文件名患者 ID 重新做患者级 5 折，禁止沿用官方 461/195 切分。
4. PASCAL Dataset A 仅在需要 Artifact 和手机录音鲁棒性时下载；它不能做患者级结论。

## 6. 未来备选

| 任务 | 备选 | 进入条件 |
| --- | --- | --- |
| S1/S2 | PhysioNet 2016 手工分割；PASCAL normal 分割；CirCor 分割 | 先统一事件定义、20 ms 帧级评估和 ±100 ms 事件容差 |
| 疾病诊断 | ZCHSound；Heart Sound Regurgitation；BMD-HS | BMD-HS 必须恢复可下载官方来源；其余需确认标签字典和许可 |
| 成人泛化 | HSBP-DB、PhysioNet 2016 | 只能作为外部压力测试，不把血压分类冒充心音疾病诊断 |
| 演示和回归测试 | HLS-CMDS | 只验证管线、显示和量化，不用于临床指标 |

## 7. 每个数据集还需补充的字段

| 数据集 | 必补字段 |
| --- | --- |
| CirCor | 设备型号/序列、统一信号质量分数、是否成人、Outcome 的临床定义和风险等级 |
| ZCHSound | 数据集书面许可证、有效的传感器/采样率/位置/检查所见/检查结论/信号质量字段、杂音和 S1/S2 标注 |
| PASCAL A/B | 许可证、患者 ID、年龄/性别、设备、位置字典、每条录音时长、患者级官方切分、测试标签 |
| PhysioNet 2016 | record-to-subject 映射、逐条 age/sex/source/device/location、统一质量分数、测试标签 |
| HLS-CMDS | 真实患者验证集、设备型号、S1/S2 边界、信号质量等级 |
| Children | 受试者 ID、年龄/性别、设备、位置、统一采样率、信号质量 |
| HSBP-DB | 杂音、S1/S2、瓣膜病标签；如果产品不输出血压，明确其只作外部风险研究 |
| Heart Sound Regurgitation | 年龄/性别、位置字典、信号质量、S1/S2、杂音分级 |
| BMD-HS | 可访问的官方下载、许可证、患者 ID、录音清单和完整性校验 |

## 8. 验收、失败判据和回退

验收：

- 每个数据集保存官方 URL、版本、发布日期、许可证文本和 SHA256 manifest。
- 复核录音数、受试者数、采样率、位深、单条时长和缺失字段。
- 所有训练/验证/测试折按患者分组；报告患者 ID 重叠数，必须为 0。
- 统一重采样到 4 kHz 后，用同一 2 s/1 s 窗口协议比较 CirCor-only 与扩展集。

失败判据：

- 许可证无法确认，或镜像许可证与原站冲突。
- 公开包的 patient ID 不足，且无法从可信元数据恢复患者分组。
- 扩展集加入后患者级 AUROC/BA 没有提高，或敏感度/特异度工作点恶化。
- 预处理、采样率或标签语义无法统一，导致标签泄漏或窗口错配。

回退：

- 许可证失败：保留 CirCor-only 基线，其他数据只做离线探索，不进入模型。
- 患者级划分失败：不把该数据用于训练或调参，只作描述性外部测试。
- 外部数据反而降低指标：先检查 domain shift 和阈值校准，再回退到 CirCor-only；
  不改变边缘推理约束，也不把推理移到手机或服务器。

## 9. 审计来源

- CirCor: <https://physionet.org/content/circor-heart-sound/1.0.3/>
- ZCHSound: <https://github.com/WeiJieOvO/ZCHSound-Dataset>
- ZCHSound DOI: <https://doi.org/10.1109/TBME.2023.3348800>
- PASCAL: <https://www.peterjbentley.com/heartchallenge/>
- PhysioNet 2016: <https://physionet.org/content/challenge-2016/1.0.0/>
- HLS-CMDS: <https://data.mendeley.com/datasets/8972jxbpmp/3>
- Children: <https://data.mendeley.com/datasets/5447z7m2rr/1>
- HSBP-DB: <https://data.mendeley.com/datasets/fwx556cgjy/1>
- Regurgitation: <https://data.mendeley.com/datasets/jfbdvjw6sf/1>
- BMD-HS paper: <https://arxiv.org/abs/2409.00724>
