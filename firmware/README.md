# firmware/ — ESP32-S3 host firmware skeleton

ESP-IDF project for the PCG stethoscope **host** (the device that receives
`LKA1` packets over BLE, validates `AUD1` frames, decimates 8 kHz → 4 kHz, rings
the samples and feeds a 2 s window to the AI / display layer).

Status: **skeleton, build-verified, never run on hardware.** There is no ESP32-S3
board in this milestone, so the acceptance criterion was `idf.py build`, not
flashing. See "What is real vs stubbed" below before quoting this as working.

---

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
fyp_stethoscope_host.bin binary size 0x7b620 bytes.
Smallest app partition is 0x177000 bytes. 0xfb9e0 bytes (67%) free.
Bootloader binary size 0x4ff0 bytes. 0x3010 bytes (38%) free.
```

`idf.py size`:

| memory type | used [B] | % | remain [B] | total [B] |
| --- | ---: | ---: | ---: | ---: |
| Flash Code | 316,220 | — | — | — |
| Flash Data (.rodata + appdesc) | 84,860 | — | — | — |
| DIRAM | 134,531 | 39.36 | 207,229 | 341,760 |
| &nbsp;&nbsp;.bss | 46,760 | 13.68 | | |
| &nbsp;&nbsp;.data | 16,388 | 4.80 | | |
| IRAM | 16,384 | 100 | 0 | 16,384 |
| RTC SLOW | 32 | 0.39 | 8,160 | 8,192 |
| RTC FAST | 24 | 0.29 | 8,168 | 8,192 |

**Total image size: 505,267 bytes** (`.bin` 505,376 bytes after padding), i.e.
about **494 KiB** in a 1,500 KiB factory partition. Of the 46,760 B of `.bss`,
41,600 B are the two buffers this project owns (25,600 B sample ring +
16,000 B AI window); the rest is NimBLE/FreeRTOS state. No compiler warnings
from `main/` or `components/fyp_shared/`; the only build noise is CMake warnings
about ESP-IDF submodules that this target does not use (openthread, etc.).

## 3. Layout

```text
firmware/
├── CMakeLists.txt                  ESP-IDF project entry (does not include the root CMakeLists)
├── sdkconfig.defaults              esp32s3 + NimBLE + 4 MB flash + 1500 KiB app
├── components/
│   └── fyp_shared/
│       └── CMakeLists.txt          compiles ../../../shared/src/*.c unmodified
└── main/
    ├── CMakeLists.txt
    ├── app_main.c                  boot order, consumer task, statistics
    ├── app_pipeline.h/.c           mutex + static ring around host_pipeline (the only thread-safe entry point)
    ├── ble_central.h/.c            NimBLE central: scan by 128-bit UUID, connect, MTU, PHY, discovery, CCCD, reconnect
    ├── ai_stub.h/.c                inference call site, returns NOT_IMPLEMENTED
    └── display_stub.h/.c           LVGL-ready port struct with a no-op backend
```

### Why `shared/` is wrapped instead of copied

`shared/` has no `CMakeLists.txt`, and the hard rule for this workspace is that
`shared/`, `tests/`, `tools/`, `docs/` and the root `CMakeLists.txt` must not be
modified. `components/fyp_shared/CMakeLists.txt` therefore compiles the three
`shared/src/*.c` files by absolute path and exports `shared/include`. The PC
build and the firmware build compile the *same* files, so the ctest-covered
decimator/CRC/reassembly code cannot drift. `xtensa-esp32s3-elf-nm` on the ELF
confirms `host_pipeline_push`, `host_pipeline_read_newest_samples`,
`host_pipeline_decimator_process`, `audio_protocol_decode_frame` and
`link_a_reassembler_push` are all linked in.

If a future change needs `EXTRA_COMPONENT_DIRS`, that requires adding a
`CMakeLists.txt` under `shared/` and therefore a decision in the ADR register
first.

## 4. What is real vs stubbed

| Area | State | Notes |
| --- | --- | --- |
| LKA1 → reassembly → AUD1 CRC → 8k→4k Q15 → ring | **real** | `shared/src/*.c`, already covered by 3 ctest binaries on the PC. 160-frame ring = 3.2 s = 25,600 B .bss. |
| Thread safety | **real** | one static FreeRTOS mutex in `app_pipeline.c`; the BLE host task and the consumer task never touch `host_pipeline_*` directly. ADR-014 risk (1). |
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

1. Flash on the real board and confirm the boot banner and heap numbers.
2. Point the probe at this central and capture the console log: MTU (expect
   247), PHY (expect 2M, 1M is a logged downgrade), connection interval, CCCD
   order, first notification.
3. Freeze the Control START format with the probe owner, set
   `BLE_CENTRAL_CONTROL_START_FROZEN=1`, and verify §9 item 6 of the GATT
   profile (START/STOP round trip < 100 ms).
4. Run the 5-minute streaming test (GATT profile §9 item 4) and record
   `frames_ok`, `crc_errors`, `packet_errors` and the LKA1 sequence gaps.
5. Replace `ai_stub.c` with a real ESP-DL / TFLite Micro backend and re-measure
   peak RAM, P95 latency, current and BLE loss (ADR-007 failure thresholds).
6. Replace the display stub with an LVGL backend through `display_stub_set_port()`.
