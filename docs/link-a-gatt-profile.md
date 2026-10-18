# 链路 A 的 BLE GATT Profile（候选 v1）

> 状态：**候选，未冻结**。需探头端与主机端双方确认后，才能与
> `docs/link-a-interface-freeze.md` 一起升级为冻结版本。
>
> 关联：ADR-003、ADR-005、ADR-010、`docs/link-a-interface-freeze.md`。
>
> 日期：2026-10-18
>
> 范围：只定义 GATT 层的服务、特征、属性、连接时序、绑定与重连语义。
> **不改变** `AUD1`（352 B 应用帧）、`LKA1`（22 B 开销、222 B 分片）的任何
> 字节格式与黄金测试向量。

## 0. 为什么需要这份文档

`link-a-interface-freeze.md` 冻结的是**应用层分包格式**，它明确写了"不规定
BLE 的物理层细节"。但主机和探头要真正连上，还缺一层 GATT 契约：

- 主机按什么 UUID 发现探头；
- 数据走哪个 characteristic、是 notify 还是 indicate；
- 控制指令（开始/停止测量、NACK）往哪里写；
- MTU、PHY、连接参数由谁在什么时机发起；
- 断线重连后 `session_id` 怎么换；
- 绑定信息存在哪、怎么解绑。

这些问题现在全仓库没有答案（`grep -ri uuid` 零命中）。没有它，主机端代码
只能自己拍一个值，和探头端对不上。

## 1. 决策问题

在同一套 `LKA1` 字节格式下，主机与探头之间的 BLE 服务应该怎样组织，才能
同时满足：

- 单向 158.4 kbps 应用层吞吐，P95 附加延迟 ≤ 15 ms；
- 主机能主动控制测量开始/停止，并可选发送 NACK；
- 探头无屏幕、无按键，只能 Just Works 配对，但必须拒绝未配对连接；
- 换探头、丢绑定、断线重连都有确定行为；
- 两端固件都能在 ESP-IDF / Nordic SDK 上直接实现，不需要私有协议扩展。

## 2. 候选方案

| 方案 | 组织方式 | 功能匹配 | 吞吐/延迟 | 开发难度 | 主要风险 | 结论 |
| --- | --- | --- | --- | --- | --- | --- |
| **0 现状** | 无 GATT 定义 | 0 | — | 无法实现 | 两端各自猜测，联调必然返工 | 不采用 |
| **A 单 characteristic** | 1 个 notify 承载所有 `LKA1` 包，控制指令复用同一条，靠 `type` 区分，主机用 write 回写 | 够用 | 数据和控制挤在一条连接事件序列上，控制指令可能排在一帧数据之后 | 最低 | 控制延迟不确定；无法对控制写做独立权限 | 备选 |
| **B 双 characteristic（推荐）** | notify 专走 DATA；write 专走控制（START/STOP/ACK/NACK） | 完整 | 控制通路独立，权限可单独设 | 低，多一个 att 表项 | 多占 1 个 handle | **当前选择** |
| **C 复用标准 profile** | 用 BLP（血压）或 HDP（已废弃） | 不匹配 | — | 高 | HDP 在 BLE 上已被蓝牙 SIG 弃用，BLP 语义完全不对 | 不采用 |

选择 B 的理由：控制指令和音频数据是两种完全不同的时序需求。音频必须每
15 ms 连接事件都发；控制指令是低频、必须可靠、可能重发。分开之后，控制
写可以用"有响应的 Write Request"，不会被 244 B 的音频 notification 挤掉。

## 3. 候选冻结值

### 3.1 UUID 规划

采用 128-bit 自定义 UUID，不用 16-bit 私有段（避免与 SIG 保留段和其他厂商
冲突）。最低 16 bit 用作同族序号，便于人工识别：

```text
基址：4c4b4131-0000-4000-8000-0000000000NN     （4c4b4131 = "LKA1" 的 ASCII）
```

| 角色 | 名称 | UUID | 属性 | 权限 |
| --- | --- | --- | --- | --- |
| 服务 | LKA1 Audio Service | `4c4b4131-0000-4000-8000-000000000001` | primary service | — |
| 特征 | Audio Data | `4c4b4131-0000-4000-8000-000000000002` | **Notify** | 链接必须已加密 |
| 特征 | Control | `4c4b4131-0000-4000-8000-000000000003` | **Write**（主机→探头） | 链接必须已加密 |
| 特征 | Status | `4c4b4131-0000-4000-8000-000000000004` | Notify + Read | 链接必须已加密 |
| 描述符 | CCCD | `0x2902`（标准） | Read + Write | 同上 |

`Audio Data` 的单个 notification value 就是一个完整的 `LKA1` 包，
**上限 244 B**（= ATT MTU 247 − 3 B ATT 头），与
`link-a-interface-freeze.md` 第 3 节一致。

`Status` 用 4 B 位域上报探头状态，供主机做看门狗：

```text
byte0  state        0=IDLE 1=STREAMING 2=ERROR
byte1  error_code   0=OK 1=ADC_OVERRUN 2=BLE_CONGEST 3=BUFFER_FULL
byte2  drop_frames  自会话开始的整帧丢弃计数（低 8 位）
byte3  reserved     必须为 0
```

### 3.2 连接参数

| 参数 | 候选值 | 发起方 | 说明 |
| --- | --- | --- | --- |
| PHY | 2M，回退 1M | 主机（central） | `link-a-interface-freeze.md` 已冻结。2M 与 Coded PHY 互斥，本方案不用 Coded |
| ATT MTU | 247 | 主机在连接后立即发起 | ESP32-S3 上限 517，247 已满足 |
| 连接间隔 | 15 ms（最小 11.25 ms，最大 30 ms） | 主机发起 `Connection Parameter Update` | 与冻结文档一致 |
| Slave Latency | 0 | 主机 | 音频流不允许跳过连接事件 |
| Supervision Timeout | 4000 ms（≈ CONN_INT × 267，取 4 s） | 主机 | 丢链后 4 s 判定断开，平衡功耗与恢复速度 |
| Data Length Extension | 打开，TX OCTETS = 251 | 双方各自设置本地值 | DLE 不打开时 2M PHY 收益会被 27 B 封包吃掉 |

### 3.3 广播

- 探头使用 `ADV_IND`，在 **Scan Response** 中放 128-bit 服务 UUID
  （`ADV_IND` 的 31 B 主要放 flags + 厂商自定义，保证兼容性）。
- 主机扫描时按服务 UUID 过滤，**不按设备名前缀**匹配（设备名不是唯一标识）。
- 广播间隔建议 100 ms（可连接状态），连接后立即停止广播。

## 4. 连接建立时序

```text
主机（central）                              探头（peripheral）
   |                                            |
   |  扫描：按 service UUID 过滤                |  ADV_IND / SCAN_RSP
   |<-------------------------------------------|
   |  LE Create Connection                      |
   |------------------------------------------->|
   |                                            |
   |  Exchange MTU Request (247)                |  -> 双方 MTU = 247
   |------------------------------------------->|
   |  LL_PHY_REQ (2M preferred)                 |  -> 2M 或回退 1M
   |------------------------------------------->|
   |  Connection Parameter Update (15 ms)       |
   |------------------------------------------->|
   |  Service Discovery                          |
   |------------------------------------------->|
   |  Write CCCD = 0x0001 (Audio Data)          |  -> 开始 notify
   |------------------------------------------->|
   |  Write CCCD = 0x0001 (Status)              |
   |------------------------------------------->|
   |  Write Control: START (session_id = N)     |
   |------------------------------------------->|
   |<=========== LKA1 DATA notifications =======>|
```

顺序约束（双方实现必须一致）：

1. **MTU 必须在 CCCD 使能之前完成**。否则探头会按 20 B（默认 MTU）分片，
   造成 244 B 假设失效。
2. **PHY 与连接参数更新可以并行发起**，但主机必须在开始收 DATA 之前确认
   PHY 结果（1M 或 2M），并把实际值记入日志。
3. **START 之前探头不得发送 DATA**。主机在 START 之前收到的 DATA 必须计数
   并丢弃，不得进入 AI。
4. 主机在 START 之后 500 ms 内未收到第一个完整帧，判定为启动失败并重发
   START（最多 3 次）。

## 5. 数据通路与流控

- 探头按 `link-a-interface-freeze.md` 的规则把每个 352 B `AUD1` 帧拆成
  `222 B + 130 B` 两包，**尽量放在同一个连接事件内**。
- 每个连接事件最多发 2 个 notification（两片的 244 B + 152 B）。若控制器
  报告 `congestion`，降为每个连接事件 1 包，并把 `Status.error_code` 置 2。
- 主机完成 `AUD1` CRC 校验后才把帧交给显示、回放或 AI。校验失败计数并在
  重连时上报。
- **不使用 GATT indicate**。indicate 需要逐包确认，会把 15 ms 连接预算吃光。

### 5.1 可选控制扩展：`LKA1` type=4 COMMAND

冻结文档定义了 `type = 1 DATA / 2 ACK / 3 NACK`，但没有 START/STOP。建议
在 **v1.1** 增加 `type = 4 COMMAND`，格式复用现有 22 B 头，固定 30 B：

| 字段 | 值 |
| --- | --- |
| `type` | 4 |
| `fragment_index` | 0 |
| `fragment_count` | 1 |
| `fragment_offset` | 0 |
| `fragment_bytes` | 8 |
| payload[0] | `cmd_id`：1=START、2=STOP、3=SET_GAIN、4=GET_STATUS |
| payload[1] | `status`：请求填 0，响应填 0=OK、非 0=错误码 |
| payload[2:4] | `arg`（uint16，小端） |
| payload[4:8] | `token`（uint32，小端，主机请求序号，响应原样回填） |

**候选测试向量**（START，`session_id = 0x1234`，`token = 1`），30 字节：

```text
4C 4B 41 31 01 04 00 00 34 12 00 00 00 00 00 01
00 00 08 00 01 00 00 00 01 00 00 00 32 11
```

CRC-16/CCITT-FALSE 对前 28 字节计算得 `0x1132`，按小端上线为 `32 11`。
该值已用同一算法复算冻结的 DATA 向量（`0x365E` → 上线 `5E 36`）交叉验证。

> 注意：这是**对已冻结接口的扩展**，需要版本号升到 v1.1、补进
> `link-a-interface-freeze.md` 并加对应 C/Node 测试，**不能静默修改**。
> 在双方确认前，控制指令也可以先走独立的 Control 特征（自定义 8 B 帧），
> 代价是两套编码。

## 6. 配对与绑定

| 项目 | 候选值 | 理由 |
| --- | --- | --- |
| 配对方式 | LE Secure Connections + bonding | 已由冻结文档要求 |
| IO 能力 | 探头 `NoInputNoOutput`，主机 `NoInputNoOutput` | 探头无屏无键；主机也无需在弹出的 UI 上输入 |
| 关联模型 | **Just Works** | 唯一可行的组合。**没有 MITM 防护**，必须用下面的补偿措施 |
| 加密要求 | 数据和控制特征在链接加密后才可访问 | 拒绝未加密读写 |
| 绑定存储 | 主机存 1 个 peer 绑定信息（NVS）；新探头配对时覆盖旧绑定 | 毕业设计单探头场景，避免绑定表管理复杂度 |
| 解绑 | 主机长按按键 5 s → 擦除 NVS 绑定并重启 | 无屏幕时的唯一可靠解绑路径 |

**Just Works 的风险要写进文档而不是回避**：攻击者可在配对瞬间做中间人。
补偿措施是绑定后白名单（只接受已绑定地址）、拒绝重新配对、解绑需物理操作。
这不能消除风险，只能降低。若后续需要更强安全，必须给探头加按键（Passkey
或 OOB），这属于硬件改动，需新 ADR。

## 7. 重连与会话语义

- `session_id` 由主机在每次 **START** 时分配：`(上一个 session_id + 1) & 0xFFFF`，
  从 1 开始，0 保留为无效值。
- 断线重连后**必须换新 `session_id`**，防止旧包被误重组（冻结文档第 5 节已要求）。
- 探头在收到 START 前保持 IDLE；收到 STOP 或 60 s 无数据请求后停止 notify。
- 主机重连策略：断开后 500 ms 开始重连，指数退避 0.5/1/2/4 s，上限 30 s；
  连续 10 次失败后停止并向用户报错（不无限重试，避免电池耗尽）。
- 主机每次判定"整帧丢失"，必须区分两种情况：链路丢包（`LKA1` 缺片）和
  **探头根本没发**（序号跳变）。两者在 `Status.drop_frames` 与主机统计里
  必须能对上，否则无法定位问题。

## 8. 时间基准对齐

`AUD1` 的 `timestamp_ms` 来自探头本地单调时钟，主机时钟与之无关。首版采用
最小假设的估计方法：

1. 会话开始时记录前 100 个完整帧的 `(host_rx_ms − timestamp_ms)`；
2. 取该样本的**中位数**作为初始偏移 `offset0`（中位数抗单次重传毛刺）；
3. 之后每收到一个完整帧更新一次滑动中位数（窗口 200 帧），得到 `offset(t)`；
4. 显示和回放使用 `timestamp_ms + offset(t)`；若 `|offset(t) − offset0| > 50 ms`，
   记一次时钟漂移告警。

局限：这个方法把链路延迟当成了时钟偏移，绝对精度受 P95 延迟影响（≤15 ms）。
对波形显示和 S1/S2 标注足够，**不足以做心音与心电的逐拍对齐**。若未来需要
跨设备同步，必须加硬件时间戳或 BLE 时间同步，属于新 ADR。

## 9. 验证清单

每条都要有实测记录，不能只写"已实现"。

| # | 测试 | 通过判据 |
| --- | --- | --- |
| 1 | 扫描只命中带本服务 UUID 的设备 | 不按名称匹配；周围其他 BLE 设备不出现 |
| 2 | MTU 交换 | 双方读到 247；先于 CCCD 使能 |
| 3 | PHY 协商 | 记录实际 2M 或 1M；不得静默回退而不记录 |
| 4 | 5 分钟连续流 | 应用层吞吐 ≥ 320 kbps；完整帧丢失率 ≤ 0.1%；无连续 3 帧丢失 |
| 5 | 延迟 | `LKA1` 附加延迟 P95 ≤ 15 ms、最大 ≤ 30 ms |
| 6 | 控制通路 | START/STOP 往返 < 100 ms；连发 100 次无丢 |
| 7 | 绑定 | 重启后免配对重连；未绑定设备无法读写数据特征 |
| 8 | 解绑 | 长按 5 s 后旧主机无法再连，新主机可配对 |
| 9 | 重连 | 断线后 `session_id` 递增；旧包不混入新会话 |
| 10 | Wi-Fi 共存 | 主机同时跑 Wi-Fi station + SoftAP 时，第 4 项指标仍满足 |
| 11 | 拥塞 | 人为注入 congestion 时探头降到每事件 1 包且 `Status.error_code=2` |

### 失败判据与回退

| 失败情况 | 回退动作 |
| --- | --- |
| 2M PHY 下丢帧率超标 | 固定 1M PHY，重新测吞吐；若 < 320 kbps 则连接间隔降到 11.25 ms |
| MTU 协商不到 247 | 回退 158（分片 134 B，一帧 3 包）；吞吐余量随之下降，需重算 |
| 双特征实现导致控制延迟过大 | 回退方案 A（单特征 + `type` 复用） |
| Just Works 被安全评审否决 | 探头增加按键 → Passkey，硬件改动，需新 ADR |
| Wi-Fi 共存无法满足 | 按 ADR-003 评估 ESP-NOW；仍不满足则两段链路分时（牺牲并发） |

## 10. 发给探头端队友的确认项

```text
链路 A 补了一份 GATT profile 候选（docs/link-a-gatt-profile.md），
字节格式完全沿用已冻结的 LKA1，不改任何黄金向量。请确认：

1. 你的模块是否支持 128-bit 自定义服务 + 2 个特征（Notify / Write）？
2. 能否协商 ATT MTU 247？若上限是 158 或更小，请直接给实际值。
3. 能否开 2M PHY 和 Data Length Extension（TX OCTETS 251）？
4. 探头能否接受主机发起的 Connection Parameter Update（15 ms、latency 0）？
5. 你的模块 IO 能力是否只能 NoInputNoOutput（即只能 Just Works）？
6. 探头在 Controller 报 congestion 时，会不会丢 notification？
   如果会，请给出可观测的错误码，我们好在 Status 特征里上报。
7. LKA1 是否要加 type=4 COMMAND（START/STOP）？还是控制指令走独立特征？
   两种我都能配合，但必须现在定，否则主机端控制代码要写两套。
8. 请给出模块型号、实测 MTU、实测吞吐、P95 延迟、断连重连行为。
```

## 11. 与冻结文档的关系

- 本文**不修改** `link-a-interface-freeze.md` 的任何内容。
- 若第 5.1 节的 `type=4 COMMAND` 被采纳：`LKA1` 版本升到 v1.1，在冻结文档
  追加一节，保留 v1 的测试向量，并新增对应的 C/Node 测试。
- 在双方确认前，本文状态保持"候选"，不得被称为"已冻结"。
