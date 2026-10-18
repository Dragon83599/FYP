# firmware/ — ESP32-S3 host firmware skeleton

ESP-IDF project for the PCG stethoscope **host** (the device that receives
`LKA1` packets over BLE, validates `AUD1` frames, decimates 8 kHz → 4 kHz, rings
the samples and feeds a 2 s window to the AI / display layer).

Status: **skeleton, build-verified, never run on hardware.** There is no ESP32-S3
board in this milestone, so the acceptance criterion was `idf.py build`, not
flashing. Heart rate is now real code, but it is an **estimate**; the murmur
model and the panel are still stubs. See "What is real vs stubbed" below before
quoting this as working.

---

## 输出契约

本固件必须遵守 `docs/mvp-output-contract.md`：

- **波形**是权威输出；当前 stub 只做 320 列 min/max 包络。
- **杂音分数**在推理接入后才出现，且必须带"筛查提示，非诊断"字样；当前 `ai_stub_infer()`
  明确返回 `NOT_IMPLEMENTED`，显示层显示 `murmur: n/a (no model)`，不得伪造分数。
- **心率**已移植到 C（`shared/src/hr_detector.c`）。只能以 `HR ~<bpm>` 显示（`~` 表示
  估算）；事件数 <4 或速率超出 30–220 bpm 时显示 `HR --`。不进报告、不用于报警
  （离线中位误差 1.19 bpm，但 **13.5 % 录音粗错**，因此不得去掉 `~`）。
- **S1/S2 不进 MVP**：嵌套患者级 F1 0.7975、高心率互换 0.273、139/516 条反相。
- 不得出现疾病诊断、杂音分级、位置/放射方向等超出 ADR-008 的结论。

## 1. ESP-IDF environment

Installed outside the repository (the repo's own CMake build must keep working
and `firmware/build/` must stay small):

| item | value |
| --- | --- |
| path | `/home/psr/esp/esp-idf` |
| version | `v5.5.6` (`git describe --tags`), commit `880a203ae603d08a94d06aa01e0d6761958e5e9e`, 2026-10-15 |
| install command | `./install.sh esp32s3` (no sudo; tools land in `~/.espressif`) |
| target | `esp32s3` |
| size on disk | ESP-IDF checkout 1.3 GB + `~/.espressif` tools/venv 3.9 GB ≈ **5.2 GB** |

`install.sh esp32s3` installs: `xtensa-esp-elf-gdb`, `xtensa-esp-elf`,
`riscv32-esp-elf` (ESP32-S3 ULP), `esp32ulp-elf`, `openocd-esp32`,
`esp-rom-elfs`. `ninja` is an "on request" tool and was installed separately
(`python tools/idf_tools.py install ninja`).

### Activate the environment in a new shell

```bash
export LD_LIBRARY_PATH=/home/psr/esp/libusb/lib:$LD_LIBRARY_PATH
. /home/psr/esp/esp-idf/export.sh
```

The first line is an environment quirk of this machine, not of the project:
`openocd-esp32` links against `libusb-1.0.so.0`, the system has no libusb
installed, and this milestone has no sudo. A real libusb (from the
`libusb-package` wheel) is kept at `/home/psr/esp/libusb/lib/libusb-1.0.so.0`.
Without that variable `. export.sh` aborts with
`tool openocd-esp32 has no installed versions`. On a machine with
`apt install libusb-1.0-0` the first line is unnecessary. `export.sh` only
affects the current shell, so it must be sourced in every new shell.

## 2. Build

```bash
cd firmware
idf.py set-target esp32s3     # once; creates sdkconfig from sdkconfig.defaults
idf.py build
idf.py size                   # flash/RAM breakdown
```

Clean rebuild:

```bash
idf.py fullclean && rm -f sdkconfig sdkconfig.old && idf.py set-target esp32s3 && idf.py build
```

The root `CMakeLists.txt` (the PC build of `shared/` and its ctest suite) is
**not** part of this project and was not modified. `firmware/` is an independent
ESP-IDF project; run `idf.py` from inside it.

### Verified build result (2026-10-18, ESP-IDF v5.5.6, xtensa-esp-elf 14.2.0)

`idf.py build` ends with:

```text
Project build complete. To flash, run:
 idf.py flash
```

```text
fyp_stethoscope_host.bin binary size 0x7d200 bytes.
Smallest app partition is 0x177000 bytes. 0xf9e00 bytes (67%) free.
Bootloader binary size 0x4ff0 bytes. 0x3010 bytes (38%) free.
```

`idf.py size`:

| memory type | used [B] | % | remain [B] | total [B] |
| --- | ---: | ---: | ---: | ---: |
| Flash Code | 322,056 | — | — | — |
| Flash Data (.rodata + appdesc) | 86,156 | — | — | — |
| DIRAM | 169,979 | 49.74 | 171,781 | 341,760 |
| &nbsp;&nbsp;.bss | 82,208 | 24.05 | | |
| &nbsp;&nbsp;.data | 16,388 | 4.80 | | |
| IRAM | 16,384 | 100 | 0 | 16,384 |
| RTC SLOW | 32 | 0.39 | 8,160 | 8,192 |
| RTC FAST | 24 | 0.29 | 8,168 | 8,192 |

**Total image size: 512,399 bytes** (`.bin` 512,512 bytes = `0x7d200` after
padding), i.e. about **500 KiB** in a 1,500 KiB factory partition. Of the
82,208 B of `.bss`, 77,048 B are owned by this project (25,600 B sample ring +
16,000 B AI window + 35,096 B heart-rate work buffer + 352 B detector struct);
the remaining 5,160 B is NimBLE/FreeRTOS state. No compiler warnings from
`main/` or `components/fyp_shared/`; the only build noise is CMake warnings
about ESP-IDF submodules that this target does not use (openthread, etc.).

Before the heart-rate integration the same tree measured: total image 505,267 B,
DIRAM 134,531/341,760 (39.36 %), `.bss` 46,760 B, IRAM 16,384/16,384 (100 %).
The integration therefore costs **+35,448 B of DIRAM** (35,096 B work buffer +
352 B detector struct, exactly the `.bss` delta), **+7,132 B of flash**
(+5,836 B code, +1,296 B rodata including the 516 B coefficient table) and
**+0 B of IRAM** — the full IRAM segment is untouched (ADR-016).

## 3. Layout

```text
firmware/
├── CMakeLists.txt                  ESP-IDF project entry (does not include the root CMakeLists)
├── sdkconfig.defaults              esp32s3 + NimBLE + 4 MB flash + 1500 KiB app
├── components/
│   └── fyp_shared/
│       └── CMakeLists.txt          compiles the four ../../../shared/src/*.c files unmodified
└── main/
    ├── CMakeLists.txt
    ├── app_main.c                  boot order, consumer task, HR detector state, statistics
    ├── app_pipeline.h/.c           mutex + static ring around host_pipeline (the only thread-safe entry point)
    ├── ble_central.h/.c            NimBLE central: scan by 128-bit UUID, connect, MTU, PHY, discovery, CCCD, reconnect
    ├── ai_stub.h/.c                inference call site, returns NOT_IMPLEMENTED
    └── display_stub.h/.c           LVGL-ready port struct with a no-op backend
```

### Why `shared/` is wrapped instead of copied

`shared/` has no `CMakeLists.txt`, and the hard rule for this workspace is that
`shared/`, `tests/`, `tools/`, `docs/` and the root `CMakeLists.txt` must not be
modified. `components/fyp_shared/CMakeLists.txt` therefore compiles the four
`shared/src/*.c` files by absolute path and exports `shared/include`. The PC
build and the firmware build compile the *same* files, so the ctest-covered
decimator/CRC/reassembly/heart-rate code cannot drift. `xtensa-esp32s3-elf-nm`
on the ELF confirms `host_pipeline_push`, `host_pipeline_read_newest_samples`,
`host_pipeline_decimator_process`, `audio_protocol_decode_frame`,
`link_a_reassembler_push`,
`hr_detector_init/push/push_hop/estimate/reset` and
`hr_detector_bandpass_taps` are all linked in.

The component contains one deliberate arithmetic mix: the data-path files
(`audio_protocol.c`, `link_a_protocol.c`, `host_pipeline.c`) stay integer-only
per ADR-014, while `hr_detector.c` is float32 by design (hr_detector.h D8: the
S3 has an FPU and the 20–85 Hz zero-phase filter is 2.3 MMAC per window). The
comment in the component CMakeLists records that split so "integer-only" is not
mistaken for a whole-library rule.

If a future change needs `EXTRA_COMPONENT_DIRS`, that requires adding a
`CMakeLists.txt` under `shared/` and therefore a decision in the ADR register
first.

## 4. What is real vs stubbed

| Area | State | Notes |
| --- | --- | --- |
| LKA1 → reassembly → AUD1 CRC → 8k→4k Q15 → ring | **real** | `shared/src/*.c`, covered by the PC ctest binaries. 160-frame ring = 3.2 s = 25,600 B .bss. |
| Thread safety | **real** | one static FreeRTOS mutex in `app_pipeline.c`; the BLE host task and the consumer task never touch `host_pipeline_*` directly. ADR-014 risk (1). |
| Heart rate | **real, estimate only** | `shared/src/hr_detector.c` consumes the same 2 s window every 1 s. Static `hr_detector_t` (352 B on Xtensa, 360 B per the x86-64 header) + 35,096 B caller-owned work buffer, both `.bss`/DIRAM: **no malloc, no IRAM**, +35,448 B DIRAM. Consumer task stack 4,096 → 8,192 B. Displays `HR ~<bpm>` when valid, `HR --` otherwise; **not a diagnosis, not for alarms or reports**. |
| BLE central | **real code, untested on air** | NimBLE GATTC: scan filtered by service UUID `4c4b4131-…-0001`, ATT MTU 247 requested before the CCCD write, 2M PHY requested, connection interval 11.25–30 ms, CCCD on Audio Data + Status, exponential reconnect 0.5/1/2/4 s capped at 30 s, 10 attempts. |
| Bonding | **configured, untested** | LE Secure Connections + Just Works, keys in NVS. The MITM exposure is accepted in `docs/link-a-gatt-profile.md` §6 and is not mitigated in code beyond single-connection behaviour. |
| Control START | **not sent (by design)** | The byte format is still a candidate (`docs/link-a-gatt-profile.md` §5.1). `BLE_CENTRAL_CONTROL_START_FROZEN` is `0`, so the firmware logs the warning and the probe is expected to stay IDLE. Do not flip it before the probe side confirms the format. |
| AI inference | **stub** | `ai_stub_infer()` returns `AI_STUB_STATUS_NOT_IMPLEMENTED`, probability `-1.0`. Expected input: 8000 int16 @ 4 kHz (2 s). Planned model: small 1D CNN, 41,105 params, **68.8 KiB INT8**, 5,722,944 MAC/window (ADR-007, E4 PC-only measurement). |
| Display | **stub** | No panel, no LVGL. The backend computes a 320-column min/max envelope (correct way to draw 8 kHz audio on a narrow panel) and logs it at DEBUG. Swap in LVGL through `display_stub_set_port()`. |
| Flash / partition | **assumption** | 4 MB flash, 1500 KiB factory app, no OTA. No board has been selected yet (ADR-006). |
| Flashing | **not done, not documented here** | No hardware. When a board arrives use Espressif's own instructions: <https://docs.espressif.com/projects/esp-idf/en/v5.5.6/esp32s3/get-started/> (`idf.py -p <PORT> flash monitor`). |

## 5. Design notes

### NimBLE instead of Bluedroid

Chosen for BLE-only RAM/flash footprint (ADR-008 needs the RAM for the sample
ring and the model). The full comparison — RAM, binary size, API complexity,
control-path parity, risk and fallback — is the comment block at the top of
`main/ble_central.h`. Fallback: port `ble_central.c` to Bluedroid behind the
same `ble_central.h` interface; no other file needs to change.

Two deviations from the defaults are documented where they are set:

- `CONFIG_BT_NIMBLE_MAX_BONDS=3` instead of 1. With 1, GCC 14 rejects upstream
  `ble_store_nvs.c:820` with `-Werror=array-bounds` (false positive). "One
  binding" is enforced by behaviour (one connection, UUID-filtered scanning),
  not by the storage array size. See the comment in `sdkconfig.defaults`.
- `CONFIG_BT_NIMBLE_SM_SC_ONLY=1` (secure-connections-only). If the probe turns
  out to support legacy pairing only, this must be relaxed — it is a behaviour
  change against the profile and therefore needs a decision, not a quick edit.

### Ring capacity is 160 frames, not the library default of 32

32 frames = 0.64 s < the 2 s AI window, so the AI could never read a window
(ADR-014 D7 flags this). 100 frames (exactly 2 s) leaves no slack for a 1 s step
plus scheduling jitter. 160 frames = 2 s window + 1 s step + 0.2 s margin, and
costs 9.6 KiB more .bss than the minimum. `overruns` climbing in the log is
expected for a rolling newest-window ring; it is not frame loss.

### Heart rate: real code, but an estimate

The detector is the portable C port of the heart-rate half of
`tools/ai/hs_s1s2.py` (`shared/src/hr_detector.c`, design and evidence in
`shared/include/hr_detector.h` D8–D13). The two-state S1/S2 labelling pass is
**not** ported: heart rate only needs stable event times.

- **Cadence.** The consumer loop is a 2 s window (8,000 samples @ 4 kHz) every
  1,000 ms, i.e. hop = n/2, exactly `hr_detector_push()`'s contract. A
  `_Static_assert` in `app_main.c` ties `APP_AI_WINDOW_STEP_MS` to
  `HR_DETECTOR_WINDOW_SAMPLES / 2`; if the step ever changes, the build fails
  and the call must become `hr_detector_push_hop(..., step_samples)`.
- **Memory and IRAM.** `static hr_detector_t s_hr_detector` (352 B on the
  32-bit Xtensa build) plus `static float s_hr_work[HR_DETECTOR_WORK_FLOATS]`
  (8,774 floats = 35,096 B). Both are `.bss`/DIRAM; the code and the 129-tap
  coefficient table are flash/rodata, so IRAM stays exactly full but unchanged.
  Cost: DIRAM 39.36 % → 49.74 % (+35,448 B), flash +7,132 B.
- **Stack.** `APP_AI_TASK_STACK_BYTES` was raised 4,096 → 8,192 B because
  `hr_detector_push()` is now the deepest call in the consumer task. Measured
  with `-fstack-usage`: `hr_detector_push_hop` 1,216 B,
  `hr_detector_estimate` 448 B, `hr_envelope_quantile` 320 B on
  `xtensa-esp32s3-elf-gcc -O2` (the x86-64 header measurement is 1,760 B, so
  the Xtensa frame is smaller, not larger; 8 KiB still keeps >5× margin).
- **Output wording.** `HR ~72` when `hr_detector_estimate()` says valid,
  otherwise `HR --` (< 4 events, or bpm outside 30–220). The tilde is required:
  the offline median error is 1.19 bpm but 13.5 % of recordings are grossly
  wrong, so `HR 72` would over-claim. The display also draws the static caption
  `estimated, not a diagnosis`. The murmur line is unchanged
  (`murmur: n/a (no model)`).
- **Session restart.** A disconnect/reconnect must use a new `session_id`
  (`docs/link-a-gatt-profile.md` §5), and `host_pipeline` counts every session
  change in `session_restarts`. The consumer polls that counter once per window
  and calls `hr_detector_reset()` there — in the only task that touches the
  detector. It is never called from the NimBLE callback, so the detector's
  "not thread-safe" rule is not broken. A run of consumer steps without a full
  window (a data gap) also resets, because the detector's absolute window
  cursor and event times would otherwise be carried into the new data
  (hr_detector.h D12). Finally, a successful read can still be a **stale**
  window: when the probe stops streaming, the ring keeps serving the same 2 s
  samples. The consumer compares `host_pipeline_stats.frames_ok` between
  windows and only pushes when it advanced; otherwise it resets the detector
  and shows `HR --`, so duplicate samples cannot fabricate a ~60 bpm reading.
- **Runtime is not measured on the S3.** The 2 s window is ~2.2 MMAC; the PC
  (Release, one core) takes 1.48 ms, so at 240 MHz expect roughly 10–20 ms per
  push. The 1 s step has the room, but this needs an on-board measurement
  before it is called budgeted.

### MTU before CCCD, and one ATT procedure at a time

The profile requires ATT MTU 247 **before** the Audio Data CCCD is enabled;
otherwise the probe fragments at 20 B and the 244 B LKA1 assumption is false.
`try_subscribe()` refuses to write any CCCD until `BLE_GAP_EVENT_MTU` has been
seen, and it logs the MTU it used. A 2 s timeout falls back to the stack's MTU
with an error log rather than hanging silently. NimBLE keeps a single
outstanding ATT procedure per connection, so service discovery is also started
from the MTU event (starting it while the MTU request is pending returns
`EBUSY`).

## 6. Next step once hardware arrives

1. Flash on the real board and confirm the boot banner, the heap numbers and
   the `heart-rate detector ready` line.
2. Point the probe at this central and capture the console log: MTU (expect
   247), PHY (expect 2M, 1M is a logged downgrade), connection interval, CCCD
   order, first notification.
3. **Check heart rate against a reference** (pulse oximeter or the probe's own
   reading) while the log prints `heart rate: HR ~<n> (bpm=..., events=...)`
   once per second. Confirm: the panel and the log show the same value; `HR --`
   appears until ≥4 events; the bpm is plausible at 30–220; and the value
   recovers after a disconnect/reconnect with HR history reset. Measure the
   actual `hr_detector_push()` duration on the S3 (expected 10–20 ms) and
   confirm the consumer still keeps its 1 s cadence.
4. Freeze the Control START format with the probe owner, set
   `BLE_CENTRAL_CONTROL_START_FROZEN=1`, and verify §9 item 6 of the GATT
   profile (START/STOP round trip < 100 ms).
5. Run the 5-minute streaming test (GATT profile §9 item 4) and record
   `frames_ok`, `crc_errors`, `packet_errors` and the LKA1 sequence gaps.
6. Replace `ai_stub.c` with a real ESP-DL / TFLite Micro backend and re-measure
   peak RAM, P95 latency, current and BLE loss (ADR-007 failure thresholds).
7. Replace the display stub with an LVGL backend through `display_stub_set_port()`.
