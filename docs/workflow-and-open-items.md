# 项目工作流与待办（2026-10-19）

> 两件事：① 跟队友确认什么；② 我接下来做什么。
> 图用 Mermaid，GitHub 上直接渲染。

## 1. 整体工作流

```mermaid
flowchart TB
    classDef done fill:#d4edda,stroke:#28a745,color:#14532d
    classDef now fill:#fff3cd,stroke:#ffc107,color:#664d03
    classDef next fill:#f8d7da,stroke:#dc3545,color:#842029
    classDef gate fill:#e2e3f5,stroke:#6f42c1,color:#3d2a75

    S([二体式无线听诊器]) --> A

    subgraph 阶段A["A · 接口与通路（已完成）"]
        A1[AUD1 / LKA1 协议<br/>黄金测试向量]:::done
        A2[主机数据通路 C 实现<br/>8 组 ctest]:::done
        A3[固件骨架<br/>512 KB / IRAM 100%]:::done
        A1 --> A2 --> A3
    end

    subgraph 阶段B["B · 数据与模型（已完成）"]
        B1[CirCor 校验<br/>942 人 / 3163 条]:::done
        B2[患者级 5 折<br/>冻结指纹 1f212dea…]:::done
        B3[三臂对照<br/>0.789 / 0.801 / 0.848]:::done
        B4[容量研究 + 三种子<br/>TCN 0.929，15/15 折为正]:::done
        B1 --> B2 --> B3 --> B4
    end

    subgraph 阶段C["C · 部署格式（已完成）"]
        C1[校准与工作点<br/>ECE 0.22→0.04]:::done
        C2[INT8 修正<br/>掉点 0.0191]:::done
        C3[TCN 过不了 INT8<br/>掉 0.1433]:::done
        C4[去归一化导出<br/>只剩 Conv/Pool/FC]:::done
        C1 --> C2 --> C3 --> C4
    end

    B4 --> C1
    A3 --> D0
    C4 --> D0

    D0{模型与平台选型}:::gate
    D0 -->|小 CNN INT8 0.821 / 69.9 KB| SEL[选定：41k 小 CNN + ESP32-S3]:::gate
    D0 -->|TCN INT8 0.802 / 396 KB| NO[否决]:::done
    D0 -->|树莓派| NO2[否决：精度理由消失]:::done

    SEL --> E

    subgraph 阶段E["E · 上板（未开始 · 当前瓶颈）"]
        E1[采购 S3 N16R8 + 320×240 屏]:::next
        E2[屏幕驱动 ST7789]:::next
        E3[AI 集成：INT8 模型进固件]:::next
        E4[IRAM 腾挪（100% 占满）]:::next
        E5[联调 11 项实测]:::next
        E1 --> E2 --> E3 --> E4 --> E5
    end

    E5 --> F

    subgraph 阶段F["F · 交付（部分完成）"]
        F1[论文与答辩材料]:::now
        F2[可选：TCN 的 QAT]:::now
    end
```

## 2. 剩余工作的分工泳道

```mermaid
flowchart LR
    subgraph 队友["探头端（队友）"]
        T1[确认模块能力<br/>MTU / PHY / DLE]
        T2[确认传感器与 ADC 型号]
        T3[ADC + DMA 连续采集 5 分钟]
        T4[LKA1 发送端接入]
        T5[供电与续航方案]
    end

    subgraph 共同["共同（需评审/联调）"]
        G1[冻结 GATT UUID 与连接时序]
        G2[交换并核对黄金测试向量]
        G3[真实 BLE 联调 11 项]
        G4[Wi-Fi 共存测试]
    end

    subgraph 我["主机端（我）"]
        M1[采购 S3 N16R8 + 屏]
        M2[屏幕驱动 + 界面]
        M3[INT8 模型集成]
        M4[腾出 IRAM]
        M5[时延 / 电流 / 峰值堆实测]
        M6[论文与答辩]
    end

    T1 --> G1 --> G3
    T2 --> G1
    T3 --> G2 --> G3
    T4 --> G3
    T5 --> G3
    M1 --> M2 --> M3 --> M4 --> M5 --> G3
    G3 --> G4 --> M6
    M5 --> M6
```

## 3. 要跟队友确认的事（按阻塞程度排序）

| # | 确认项 | 为什么卡我 | 需要的交付物 | 阻塞级别 |
| --- | --- | --- | --- | --- |
| 1 | **实际 ATT MTU**（247？158？） | 不是 247 就要重算分片数、吞吐和缓冲；直接影响主机端重组代码 | 实测值 + 模块型号 | 🔴 高 |
| 2 | **是否加 `type=4 COMMAND`（START/STOP）** | 控制指令走 LKA1 还是独立特征，决定主机控制代码写一套还是两套 | 明确结论 | 🔴 高 |
| 3 | **模块射频能力**：128-bit 服务、2 特征、2M PHY、DLE 251 | 决定 GATT profile 能否落地 | datasheet 截图 / 实测 | 🔴 高 |
| 4 | **congestion 行为与错误码** | 决定 `Status.error_code=2` 能否上报；影响丢帧诊断 | 实测或厂商文档 | 🟡 中 |
| 5 | **Connection Parameter Update 接受度**（15 ms / latency 0） | 延迟预算和功耗都依赖它 | 实测 | 🟡 中 |
| 6 | **IO 能力（Just Works 还是 Passkey）** | 若只能 Just Works，安全方案要按 ADR-010 回退 | 明确 | 🟡 中 |
| 7 | **传感器 / 探头 MCU / ADC 型号** | 决定带宽、噪声底、电池与外壳 | 选型结论 | 🟡 中 |
| 8 | **8 kHz 采样时钟来源与精度**（ppm） | 时钟偏差会让两端缓冲漂移，影响 5 分钟连续流 | 晶振型号 / 实测 | 🟡 中 |
| 9 | **供电与续航目标**（单次时长、充电、目标续航） | 决定电池与充电芯片 | 数字目标 | 🟢 低 |
| 10 | **探头固件能否对上黄金向量**（CRC16、字节序） | 联调第一道关，越早越好 | 一份 `probe.lka` 样例 | 🟢 低 |

> 建议直接把 `docs/link-a-gatt-profile.md` §10 的文本发给队友，它就是按这 10 条写的。

## 4. 我今后的任务（按依赖排序）

| # | 任务 | 依赖 | 验收判据 | 预估 |
| --- | --- | --- | --- | --- |
| 1 | **采购 ESP32-S3-DevKitC-1 N16R8 + 320×240 SPI 屏** | 无 | 到货、能跑 `idf.py flash` | 1 天（等物流）|
| 2 | **腾 IRAM**：把非热路径放 flash、压缩缓冲、必要时用 PSRAM | 无 | `idf.py size` 后 IRAM < 95%，功能不减 | 2–3 天 |
| 3 | **屏幕驱动**（ST7789，SPI，320×240） | 1、2 | 波形 + `HR ~72` + 分数同屏刷新 ≥10 fps | 2–3 天 |
| 4 | **INT8 模型集成**（ESP-DL 或 TFLite Micro） | 1、2 | 板上输出与 PC ONNX 的 logit 差 < 1e-2；单窗 < 200 ms | 3–5 天 |
| 5 | **上板 11 项实测**（配合队友） | 3、4 + 队友 | 按 `link-a-gatt-profile.md` §9 逐条记录 | 2–3 天 |
| 6 | **电流与续航实测** | 5 | 平均电流、目标续航达成或记录差距 | 1 天 |
| 7 | **论文与答辩材料** | 5 | 结果表 + 决策对比页 + 失败分析页 | 3–5 天 |
| 8 | 可选：TCN 的 QAT | 无 | TCN INT8 掉点 ≤0.03 且体积可接受 | 1–2 周 |

## 5. 关键路径

```text
买板 → 腾 IRAM → 屏幕驱动 ┐
                          ├→ 上板 11 项 → 电流 → 论文
        INT8 模型集成 ────┘
队友：模块确认 → GATT 冻结 → 联调 ┘
```

**最短路径上的三个卡点**：

1. **IRAM 100% 占满**——不腾出来，屏幕和 AI 都加不进去；这是第一个必须解决的问题；
2. **队友的 MTU 实测值**——决定主机端重组与吞吐是否要改；
3. **`type=4 COMMAND` 的定夺**——一旦主机端控制代码写完再改，返工成本高。

## 6. 现在就能做的三件事

1. **今天下单**：ESP32-S3-DevKitC-1 N16R8 + 2.8" 320×240 SPI TFT（ADR-017）；
2. **今天发确认单**：把 §3 的 10 条（或 GATT 文档 §10）发给队友；
3. **不等板子**：先做 IRAM 腾挪的方案分析（读 map 文件、列出可移到 flash 的段），这一步不需要硬件。
