# probe-sim：用一块 ESP32-S3 假装探头

> 用途：队友的探头还没做出来，用一块开发板先把 **BLE → 手机 PWA** 这条链路调通。
> 它**不是**音频设备——没有麦克风、没有 ADC，只把一段存在 flash 里的录音按
> **50 帧/秒**打包成 `LKA1` 通知发出去。
> 协议见 `docs/link-a-gatt-profile.md` 与 `docs/link-a-interface-freeze.md`。

## 1. 它能证明什么、不能证明什么

| 能 | 不能 |
| --- | --- |
| 广播、服务发现、Notify 订阅、MTU 协商 | 真实麦克风的底噪与频响 |
| **按 20 ms 节奏推 16 kB/s**，手机端实时重组 | 队友板子的实际天线与布局 |
| 控制指令 START/STOP、Status 计数 | 最终产品的功耗与体积 |
| 手机能不能真的连上并算出概率 | 他那个模块的 MTU 上限 |

## 2. 烧录

前提：ESP-IDF 5.5 已安装。本机激活时需要 `libusb`（openocd 依赖）：

```bash
export LD_LIBRARY_PATH="$HOME/esp/libusb/lib:$LD_LIBRARY_PATH"
. ~/esp/esp-idf/export.sh

cd firmware/probe-sim
idf.py set-target esp32s3
idf.py build
idf.py -p /dev/ttyACM0 flash monitor     # Windows 上是 -p COM3
```

板子：**ESP32-S3-DevKitC-1**，走 USB 线直接烧，不需要下载器。
（线必须能传数据，纯充电线不行。）

## 3. 烧完之后应该看到

串口日志（`idf.py monitor`）：

```
I (312) probe-sim: embedded audio: 20849 samples (2.61 s at 8 kHz)
I (325) probe-sim: advertising, waiting for the phone
```

手机连上后：

```
I (1520) probe-sim: connected, handle=0
I (1521) probe-sim: mtu=247 (channels 0)
I (1522) probe-sim: connection params: 0
I (2100) probe-sim: START session=2 token=1
I (3100) probe-sim: frames=50 packets=100 dropped=0
```

**关键数字**：`mtu=247`、`frames=50`（每秒 50 帧）、`packets=100`（每帧 2 包）、
`dropped=0`。前三项任一不对，就是链路有问题，先查那个，别查模型。

## 4. 手机端

需要**安卓 + Chrome/Edge**（Web Bluetooth 只有这两个支持；iOS 和华为自带浏览器都不行）。

页面里的"实时"入口会按服务 UUID 过滤设备：

```
4c4b4131-0000-4000-8000-000000000001
```

连上后写 Control 特征发 START，然后订阅 Audio Data 的通知。

> 手机端代码（`pwa/bluetooth.js`）尚未编写，见 §6。

## 5. 两套编码器，一套字节

固件里的 `main/lka1_encode.c` 与主机上的 Node/Python 实现**必须产出同样的字节**。
这条约束不是靠"仔细写"保证的，而是靠黄金向量：

```bash
cmake -S . -B build -DBUILD_TESTING=ON && cmake --build build
ctest --test-dir build -R lka1_vectors --output-on-failure
```

测试用的是仓库里**已经写死**的两条向量：

| 向量 | 来源 | CRC |
| --- | --- | --- |
| DATA（session 0x1234、seq 7、10 字节载荷）| `docs/link-a-interface-freeze.md` | `0x365E` |
| COMMAND（START、token 1）| `docs/link-a-gatt-profile.md` §5.1 | `0x1132` |

改固件编码器 → 这两条测试会先红，不用等板子。

## 6. 还没做的部分

| 项 | 状态 |
| --- | --- |
| `pwa/bluetooth.js`（手机端 Web Bluetooth）| ⬜ 未写 |
| 页面上的"实时"入口 | ⬜ 未接 |
| 实测：MTU、2M PHY 是否真开、丢帧率、P95 延迟 | ⬜ 等板子到货 |
| Wi-Fi 共存（如果探头同时要用 Wi-Fi）| ⬜ 不在本模拟器范围 |

## 6.1 实测记录（2026-11-08）

ESP32-S3-DevKitC-1，手机端安卓 Edge，真实 BLE 无线电：

| 项 | 值 |
| --- | --- |
| ATT MTU | **247** |
| PHY | **2M（tx=2 rx=2）** |
| 连接间隔 | **7.50 ms**（协商结果；请求的是 15 ms）|
| 连续运行 | **16,000 帧 / 32,000 包 / 320 秒** |
| 丢帧 | **0** |
| notify 失败 | **0** |
| 手机端 CRC 错 / 分片错 / 序号丢帧 | **0 / 0 / 0** |

详细清单见 `docs/link-a-gatt-profile.md` §9.1。

## 6.2 上手时踩过的三个坑（都花了时间，记下来）

| # | 现象 | 真因 | 修法 |
| --- | --- | --- | --- |
| 1 | 烧完一启动就 `abort()` | NimBLE 的 `ble_gatts_count_cfg` 返回 `BLE_HS_EINVAL`：**每个特征都必须有 `access_cb`**，即使是只做 Notify 的 | 给 Audio / Status 都加回调 |
| 2 | 手机蓝牙列表里**看不到设备名** | 128-bit 服务 UUID（18 B）+ flags（3 B）已经把 31 B 的广播包占满，放不下名字 | 名字放进 **Scan Response**（`ble_gap_adv_rsp_set_fields`）|
| 3 | **START 收到了，但一帧都不发** | `ble_gap_adv_start(..., NULL, NULL)` —— **GAP 事件回调没注册**，连接句柄永远是 `NONE`，发送循环每次直接 return | 传 `gap_event`；并在写入回调里兜底补上句柄 |

第 3 个最隐蔽：写入走 GATT，不依赖那个回调，所以 START 照常打印，看起来"命令收到了"。
是加了 `mtu=0` 这一行诊断才定位到的——**"命令收到"不等于"链路建立"**。

## 7. 已知的简化（写论文时要说明）

- **无麦克风**：音频来自 flash 里的固定录音，所以"采集质量"这一环没有被验证。
- **无功耗优化**：开发板常供电，没有测电池续航；连接参数虽然发了更新请求，但没做省电策略。
- **单连接**：`CONFIG_BT_NIMBLE_MAX_CONNECTIONS=1`，不模拟多主机。
- **没有实现 2M PHY 的确认逻辑**：只发起了请求，实际是否切换成功要看串口日志里的 PHY 事件，**这一项必须实测记录**，不能假设。
