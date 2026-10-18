/*
 * ble_central.h - NimBLE BLE central for the link-A GATT contract.
 *
 * Implements docs/link-a-gatt-profile.md as far as it is frozen:
 *   service        4c4b4131-0000-4000-8000-000000000001  (LKA1 Audio Service)
 *   Audio Data     4c4b4131-0000-4000-8000-000000000002  Notify  -> LKA1 bytes
 *   Control        4c4b4131-0000-4000-8000-000000000003  Write    host -> probe
 *   Status         4c4b4131-0000-4000-8000-000000000004  Notify + Read
 *   CCCD           0x2902
 *
 * Connection ordering enforced here (profile section 4):
 *   1. ATT MTU 247 is exchanged BEFORE the Audio Data CCCD is written. The
 *      state machine refuses to subscribe until BLE_GAP_EVENT_MTU arrives, and
 *      an explicit MTU request is issued right after connect.
 *   2. 2M PHY is requested after connect; the result is logged (1M or 2M).
 *   3. Connection interval 11.25..30 ms with latency 0 is requested at connect
 *      time and re-checked once the link is up.
 *   4. A Control START is only sent if its byte format is frozen; the current
 *      profile leaves it as a candidate (profile section 5.1), so it is
 *      compiled out by default and logged instead.
 *
 * ---- NimBLE vs Bluedroid (decision for this firmware) ----------------------
 * Evidence levels: primary = ESP-IDF v5.5.6 source/Kconfig and Espressif
 * documentation in this checkout; estimate = engineering calculation from the
 * Kconfig defaults; independent = public project experience.
 *
 * Metric            | NimBLE (chosen)              | Bluedroid
 * ------------------+------------------------------+---------------------------
 * Role fit          | BLE-only host, exactly what   | dual-mode (BR/EDR + BLE);
 *                   | this product needs. Central + | classic-BT code paths are
 *                   | GATTC are first-class.        | dead weight on an S3.
 * RAM (primary,     | host task 5 KiB stack +       | commonly ~2x NimBLE host
 * Kconfig defaults) | ~15-25 KiB host heap         | RAM in ESP-IDF BLE examples
 *                   | (estimate; MSYS pools are     | (estimate, not measured
 *                   | 12 x 256 B + 6 x 1024 B)      | here)
 * Flash / binary    | smaller: no BR/EDR, no        | larger (classic BT + BLE
 * size (primary:    | Bluedroid BTA/BTM layers      | stacks). ESP-IDF docs
 * Espressif docs)   |                               | recommend NimBLE for
 *                   |                               | BLE-only to save memory.
 * API complexity    | raw NimBLE host API: more     | esp_ble_gap_* / esp_ble_gattc_*
 *                   | callbacks to wire (scan,      | wrapper is easier to start,
 *                   | connect, discover, CCCD), but | but its central-role GATT
 *                   | no callback trampolines and   | client setup is verbose too.
 *                   | the code maps 1:1 to HCI.     |
 * Control           | full: ble_gap_set_prefered_   | full via esp_ble_gap_set_*;
 * (MTU/PHY/params)  | le_phy, ble_gattc_exchange_   | MTU/PHY calls are equally
 *                   | mtu, ble_gap_update_params    | available.
 * Risk              | fewer examples for GATTC      | most Arduino/ESP32 tutorials
 *                   | discovery; API churn between  | use it, so more copy-paste
 *                   | IDF majors (ble_gattc.h moved | material.
 *                   | into ble_gatt.h in NimBLE 1.8)|
 *
 * Decision: NimBLE.
 *   - The product only needs BLE (ADR-008); Bluedroid's dual-mode support buys
 *     nothing and costs RAM and flash that the AI model budget (ADR-007) and
 *     the 25.6 KiB sample ring need.
 *   - The GATT client flow here is written against NimBLE's HCI-level API, so
 *     the MTU-before-CCCD ordering is explicit instead of hidden inside a
 *     wrapper.
 *   - Cost: this file is longer than a Bluedroid equivalent and the GATTC
 *     discovery code is the part most likely to need adjustment on real
 *     hardware.
 * Fallback: if NimBLE GATTC discovery proves unreliable against the probe,
 * port this file to Bluedroid behind the same ble_central.h interface; nothing
 * outside ble_central.c knows which host stack is used. Bluedroid would then
 * cost extra RAM/flash but no protocol change.
 * Verification (hardware): the 11-item checklist in
 * docs/link-a-gatt-profile.md section 9, in particular #2 (MTU 247 before
 * CCCD), #3 (PHY logged), #4 (5-minute stream) and #9 (reconnect).
 */
#ifndef BLE_CENTRAL_H
#define BLE_CENTRAL_H

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

/* ATT MTU required by the profile (LKA1 fragment payload = 244 B). */
#define BLE_CENTRAL_ATT_MTU 247u

/* Link-A LKA1 notification can never exceed MTU - 3 ATT bytes. */
#define BLE_CENTRAL_MAX_NOTIFICATION_BYTES (BLE_CENTRAL_ATT_MTU - 3u)

/*
 * Connection timing from GATT profile section 3.2, in the BLE 1.25 ms / 10 ms
 * units used on the air. The nominal interval is 15 ms; the accepted range is
 * 11.25..30 ms so the probe may pick within it.
 */
#define BLE_CENTRAL_CONN_ITVL_MIN_UNITS 9u      /* 11.25 ms */
#define BLE_CENTRAL_CONN_ITVL_MAX_UNITS 24u     /* 30.00 ms */
#define BLE_CENTRAL_CONN_ITVL_NOMINAL_UNITS 12u /* 15.00 ms */
#define BLE_CENTRAL_SUPERVISION_TIMEOUT_UNITS 400u /* 4000 ms */

/* Scan timing for discovery (0.625 ms units via the NimBLE helpers). */
#define BLE_CENTRAL_SCAN_ITVL_MS 100u
#define BLE_CENTRAL_SCAN_WINDOW_MS 80u
/* A scan that finds nothing is a failed reconnect attempt (profile section 7). */
#define BLE_CENTRAL_SCAN_DURATION_MS 10000

/* Reconnect policy from GATT profile section 7. */
#define BLE_CENTRAL_RECONNECT_BASE_MS 500u
#define BLE_CENTRAL_RECONNECT_MAX_MS 30000u
#define BLE_CENTRAL_RECONNECT_MAX_ATTEMPTS 10u

typedef struct {
    bool connected;
    uint16_t conn_handle;
    uint16_t att_mtu;
    uint8_t tx_phy; /* BLE_GAP_LE_PHY_1M(1) / 2M(2) / CODED(3); 0 = unknown */
    uint8_t rx_phy;
    uint16_t conn_itvl_1_25ms;
    uint32_t reconnect_attempts;
    uint32_t notifications;
    uint32_t push_ok;      /* pipeline accepted (COMPLETE / COMPLETE_NEW) */
    uint32_t push_rejected;/* pipeline rejected (CRC/format/framing)      */
    bool ready;            /* MTU + discovery + CCCD all done            */
} ble_central_link_info_t;

/*
 * Bring up NVS, the ESP32-S3 BT controller, the NimBLE host task and the GAP
 * state machine. Does not start scanning; call ble_central_start_scan() after
 * the rest of the application is initialised.
 */
esp_err_t ble_central_init(void);

/* Begin scanning for the LKA1 service UUID (safe to call once). */
esp_err_t ble_central_start_scan(void);

/* Snapshot of the link/state counters for logging. Never blocks. */
void ble_central_get_link_info(ble_central_link_info_t *out);

/* "1M"/"2M"/"Coded"/"?" for logging. */
const char *ble_central_phy_name(uint8_t phy);

#ifdef __cplusplus
}
#endif

#endif /* BLE_CENTRAL_H */
