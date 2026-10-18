#include "ble_central.h"

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "esp_log.h"
#include "esp_timer.h"
#include "nvs_flash.h"

#include "nimble/nimble_port.h"
#include "nimble/nimble_port_freertos.h"
#include "host/ble_hs.h"
#include "host/ble_gap.h"
#include "host/ble_gatt.h"
#include "host/ble_att.h"
#include "host/ble_sm.h"
#include "host/ble_store.h"
#include "host/ble_uuid.h"
#include "host/util/util.h"
#include "os/os_mbuf.h"

#include "app_pipeline.h"

static const char *TAG = "ble_central";

/* ------------------------------------------------------------------------- */
/* GATT contract: docs/link-a-gatt-profile.md section 3.1                    */
/*                                                                           */
/* Stored MSB-first, exactly as written in the document. NimBLE stores 128-bit*/
/* UUIDs little-endian (value[0] = least significant byte, same order as on   */
/* the air), so uuid128_from_canonical() reverses the bytes once at init and  */
/* the result is verified by printing it back with ble_uuid_to_str().        */
/* ------------------------------------------------------------------------- */
static const uint8_t k_uuid_service[16] = {
    0x4c, 0x4b, 0x41, 0x31, 0x00, 0x00, 0x40, 0x00,
    0x80, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01,
};
static const uint8_t k_uuid_audio_data[16] = {
    0x4c, 0x4b, 0x41, 0x31, 0x00, 0x00, 0x40, 0x00,
    0x80, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x02,
};
static const uint8_t k_uuid_control[16] = {
    0x4c, 0x4b, 0x41, 0x31, 0x00, 0x00, 0x40, 0x00,
    0x80, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x03,
};
static const uint8_t k_uuid_status[16] = {
    0x4c, 0x4b, 0x41, 0x31, 0x00, 0x00, 0x40, 0x00,
    0x80, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x04,
};

/* Expected ble_uuid_to_str() renderings, used to self-check the byte order. */
#define UUID_SERVICE_STR "4c4b4131-0000-4000-8000-000000000001"
#define UUID_AUDIO_STR   "4c4b4131-0000-4000-8000-000000000002"
#define UUID_CONTROL_STR "4c4b4131-0000-4000-8000-000000000003"
#define UUID_STATUS_STR  "4c4b4131-0000-4000-8000-000000000004"

/*
 * Control START is NOT sent by default.
 *
 * docs/link-a-gatt-profile.md section 3.1 requires the host to write a START to
 * the Control characteristic, but the byte format is still a *candidate*
 * (section 5.1: either LKA1 type=4 COMMAND, not yet in the frozen v1.1
 * interface, or an 8-byte custom frame). Sending a guess would violate the
 * "do not silently change a frozen interface" rule in AGENTS.md, so the write
 * is compiled out and logged. Flip to 1 only after the format is frozen and
 * the probe side confirms it; the candidate bytes from the document are kept
 * below for that day.
 */
#define BLE_CENTRAL_CONTROL_START_FROZEN 0

#if BLE_CENTRAL_CONTROL_START_FROZEN
/* Candidate LKA1 type=4 COMMAND START, session_id 0x1234, token 1 (doc 5.1). */
static const uint8_t k_candidate_start[30] = {
    0x4C, 0x4B, 0x41, 0x31, 0x01, 0x04, 0x00, 0x00, 0x34, 0x12,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x08, 0x00,
    0x01, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x32, 0x11,
};
#endif

/* Discovery sub-state machine. GATTC callbacks are asynchronous and ordered. */
typedef enum {
    DISC_IDLE = 0,
    DISC_SERVICE,      /* looking for the 128-bit service                    */
    DISC_CHARS,        /* enumerating characteristics inside it              */
    DISC_DSC_AUDIO,    /* CCCD of Audio Data                                  */
    DISC_DSC_STATUS,   /* CCCD of Status                                      */
    DISC_DONE,
} discovery_phase_t;

#define BLE_CENTRAL_MAX_CHARS 8

typedef struct {
    uint16_t def_handle;
    uint16_t val_handle;
    ble_uuid_any_t uuid;
} discovered_chr_t;

typedef struct {
    ble_uuid_any_t service;
    ble_uuid_any_t audio_data;
    ble_uuid_any_t control;
    ble_uuid_any_t status;

    uint8_t own_addr_type;
    bool synced;
    bool scan_requested;
    bool connected;
    bool connecting;
    bool ready;

    uint16_t conn_handle;
    uint16_t att_mtu;
    uint8_t tx_phy;
    uint8_t rx_phy;
    bool mtu_done;
    bool phy_done;
    bool subscribed;
    bool discovery_started;

    ble_addr_t peer_addr;

    uint16_t svc_start;
    uint16_t svc_end;

    discovered_chr_t chrs[BLE_CENTRAL_MAX_CHARS];
    uint8_t chr_count;

    discovery_phase_t phase;

    uint16_t audio_val_handle;
    uint16_t audio_cccd_handle;
    uint16_t audio_end_handle;
    uint16_t status_val_handle;
    uint16_t status_cccd_handle;
    uint16_t status_end_handle;
    uint16_t control_val_handle;

    uint32_t reconnect_attempts; /* consecutive failures, reset on connect */
    uint32_t notifications;
    uint32_t push_ok;
    uint32_t push_rejected;
    uint16_t next_session_id;

    /* Owned by the NimBLE host task; the counter snapshot below is the only
     * cross-task read, and it is a plain word copy (documented as racy). */
    uint32_t bad_length_notifications;
} ble_state_t;

static ble_state_t s_state;
/* Single writer (NimBLE host task) per event; reads in push are diagnostics. */

static esp_timer_handle_t s_reconnect_timer;
static esp_timer_handle_t s_mtu_timer;

/* One LKA1 notification is at most 244 B at MTU 247; 256 leaves slack for a
 * misbehaving peer without a heap allocation. */
static uint8_t s_notify_buf[256];

/* ------------------------------------------------------------------------- */
/* Helpers                                                                   */
/* ------------------------------------------------------------------------- */

static void uuid128_from_canonical(ble_uuid_any_t *out, const uint8_t canonical[16])
{
    uint8_t wire[16];

    /* Canonical text order is MSB first; NimBLE keeps value[] little-endian. */
    for (size_t i = 0; i < sizeof(wire); i++) {
        wire[i] = canonical[sizeof(wire) - 1u - i];
    }
    (void)ble_uuid_init_from_buf(out, wire, sizeof(wire));
}

static const char *phy_name(uint8_t phy)
{
    switch (phy) {
    case BLE_GAP_LE_PHY_1M:
        return "1M";
    case BLE_GAP_LE_PHY_2M:
        return "2M";
    case BLE_GAP_LE_PHY_CODED:
        return "Coded";
    default:
        return "unknown";
    }
}

const char *ble_central_phy_name(uint8_t phy)
{
    return phy_name(phy);
}

static void addr_to_str(const ble_addr_t *addr, char out[18])
{
    (void)snprintf(out, 18, "%02x:%02x:%02x:%02x:%02x:%02x",
                   addr->val[5], addr->val[4], addr->val[3],
                   addr->val[2], addr->val[1], addr->val[0]);
}

static uint8_t phy_of_conn(uint16_t conn_handle, uint8_t *tx, uint8_t *rx)
{
    uint8_t ltx = 0;
    uint8_t lrx = 0;

    if (ble_gap_read_le_phy(conn_handle, &ltx, &lrx) != 0) {
        return 0;
    }
    if (tx != NULL) {
        *tx = ltx;
    }
    if (rx != NULL) {
        *rx = lrx;
    }
    return 1;
}

/* ------------------------------------------------------------------------- */
/* Forward declarations                                                      */
/* ------------------------------------------------------------------------- */

/* Provided by NimBLE's store/config backend and used by the examples; there is
 * no public header for it in NimBLE 1.8, so it is declared here (matching
 * examples/bluetooth/nimble/blecent/main/main.c). */
void ble_store_config_init(void);

static int gap_event_cb(struct ble_gap_event *event, void *arg);
static int svc_disc_cb(uint16_t conn_handle, const struct ble_gatt_error *error,
                       const struct ble_gatt_svc *service, void *arg);
static int chr_disc_cb(uint16_t conn_handle, const struct ble_gatt_error *error,
                       const struct ble_gatt_chr *chr, void *arg);
static int dsc_disc_cb(uint16_t conn_handle, const struct ble_gatt_error *error,
                       uint16_t chr_val_handle, const struct ble_gatt_dsc *dsc,
                       void *arg);
static void scan_start(void);
static void schedule_reconnect(const char *why);
static void start_service_discovery(void);
static void try_subscribe(void);
static void send_control_start(void);

/* ------------------------------------------------------------------------- */
/* Scanning / connecting                                                     */
/* ------------------------------------------------------------------------- */

static void scan_start(void)
{
    struct ble_gap_disc_params params = {0};
    int rc;

    if (!s_state.synced) {
        return;
    }
    if (s_state.connected || s_state.connecting) {
        return;
    }

    memset(&params, 0, sizeof(params));
    params.itvl = (uint16_t)BLE_GAP_SCAN_ITVL_MS(BLE_CENTRAL_SCAN_ITVL_MS);
    params.window = (uint16_t)BLE_GAP_SCAN_WIN_MS(BLE_CENTRAL_SCAN_WINDOW_MS);
    params.filter_policy = BLE_HCI_SCAN_FILT_NO_WL;
    params.limited = 0;
    /*
     * Active scan: the profile puts the 128-bit service UUID in the SCAN_RSP,
     * so a passive scan would never see it. Duplicate filtering is off because
     * the controller would then report a device only once, and we need the
     * SCAN_RSP (a separate report) to match on.
     */
    params.passive = 0;
    params.filter_duplicates = 0;

    rc = ble_gap_disc(s_state.own_addr_type, BLE_CENTRAL_SCAN_DURATION_MS,
                      &params, gap_event_cb, NULL);
    if (rc != 0) {
        ESP_LOGE(TAG, "ble_gap_disc failed rc=%d", rc);
        schedule_reconnect("scan start failed");
        return;
    }
    ESP_LOGI(TAG, "scanning for service UUID " UUID_SERVICE_STR " (%u ms, active)",
             (unsigned)BLE_CENTRAL_SCAN_DURATION_MS);
}

static void connect_to(const ble_addr_t *addr)
{
    struct ble_gap_conn_params params = {0};
    char addr_str[18];
    int rc;

    memset(&params, 0, sizeof(params));
    params.scan_itvl = (uint16_t)BLE_GAP_SCAN_ITVL_MS(BLE_CENTRAL_SCAN_ITVL_MS);
    params.scan_window = (uint16_t)BLE_GAP_SCAN_WIN_MS(BLE_CENTRAL_SCAN_WINDOW_MS);
    params.itvl_min = BLE_CENTRAL_CONN_ITVL_MIN_UNITS;
    params.itvl_max = BLE_CENTRAL_CONN_ITVL_MAX_UNITS;
    params.latency = 0; /* audio: must not skip connection events */
    params.supervision_timeout = BLE_CENTRAL_SUPERVISION_TIMEOUT_UNITS;
    params.min_ce_len = 0;
    params.max_ce_len = 0;

    (void)ble_gap_disc_cancel();

    s_state.connecting = true;
    rc = ble_gap_connect(s_state.own_addr_type, addr, BLE_CENTRAL_SCAN_DURATION_MS,
                         &params, gap_event_cb, NULL);
    if (rc != 0) {
        s_state.connecting = false;
        addr_to_str(addr, addr_str);
        ESP_LOGW(TAG, "ble_gap_connect(%s) failed rc=%d", addr_str, rc);
        schedule_reconnect("connect failed");
        return;
    }
    addr_to_str(addr, addr_str);
    ESP_LOGI(TAG, "connecting to %s (itvl %u..%u x1.25ms, latency 0, timeout %u ms)",
             addr_str, BLE_CENTRAL_CONN_ITVL_MIN_UNITS,
             BLE_CENTRAL_CONN_ITVL_MAX_UNITS,
             BLE_CENTRAL_SUPERVISION_TIMEOUT_UNITS * 10u);
}

static void reconnect_timer_cb(void *arg)
{
    (void)arg;
    scan_start();
}

static void mtu_timer_cb(void *arg)
{
    (void)arg;
    if (!s_state.connected || s_state.mtu_done) {
        return;
    }
    /*
     * Safety net. The profile requires MTU 247 before the CCCD write; if the
     * peer never answers the ATT MTU request we must not hang forever with the
     * probe silent. Fall back to whatever the stack has and say so loudly.
     */
    s_state.att_mtu = ble_att_mtu(s_state.conn_handle);
    s_state.mtu_done = true;
    ESP_LOGE(TAG,
             "MTU exchange timed out; continuing with MTU %u. LKA1 fragments will be <= %u B "
             "(profile requires %u)",
             (unsigned)s_state.att_mtu, (unsigned)(s_state.att_mtu - 3u),
             (unsigned)BLE_CENTRAL_ATT_MTU);
    start_service_discovery();
    try_subscribe();
}

static void schedule_reconnect(const char *why)
{
    uint32_t delay_ms;

    if (s_state.reconnect_attempts >= BLE_CENTRAL_RECONNECT_MAX_ATTEMPTS) {
        /*
         * Profile section 7: stop after 10 consecutive failures instead of
         * draining the battery forever. The only recovery is a reboot (or a
         * future button/LVGL "retry" action).
         */
        ESP_LOGE(TAG,
                 "giving up after %u reconnect attempts (%s); reboot to retry",
                 (unsigned)BLE_CENTRAL_RECONNECT_MAX_ATTEMPTS, why);
        return;
    }

    /* 500 ms, 1 s, 2 s, 4 s, ... capped at 30 s (profile section 7). */
    delay_ms = BLE_CENTRAL_RECONNECT_BASE_MS << s_state.reconnect_attempts;
    if (delay_ms > BLE_CENTRAL_RECONNECT_MAX_MS) {
        delay_ms = BLE_CENTRAL_RECONNECT_MAX_MS;
    }
    s_state.reconnect_attempts++;

    ESP_LOGW(TAG, "reconnect %u/%u in %u ms (%s)",
             (unsigned)s_state.reconnect_attempts,
             (unsigned)BLE_CENTRAL_RECONNECT_MAX_ATTEMPTS,
             (unsigned)delay_ms, why);
    (void)esp_timer_stop(s_reconnect_timer);
    (void)esp_timer_start_once(s_reconnect_timer, (uint64_t)delay_ms * 1000u);
}

/* ------------------------------------------------------------------------- */
/* GATT discovery                                                            */
/* ------------------------------------------------------------------------- */

/*
 * NimBLE keeps ONE outstanding ATT procedure per connection, so service
 * discovery must not be started while the ATT MTU exchange is still in flight
 * (ble_gattc_* would return BLE_HS_EBUSY). Both the MTU event and the MTU
 * fallback timer call this function, and it is idempotent.
 */
static void start_service_discovery(void)
{
    int rc;

    if (!s_state.connected || s_state.phase != DISC_SERVICE ||
        s_state.discovery_started) {
        return;
    }
    s_state.discovery_started = true;

    rc = ble_gattc_disc_svc_by_uuid(s_state.conn_handle, &s_state.service.u,
                                    svc_disc_cb, NULL);
    if (rc != 0) {
        ESP_LOGE(TAG, "service discovery failed to start rc=%d", rc);
        (void)ble_gap_terminate(s_state.conn_handle, BLE_ERR_REM_USER_CONN_TERM);
        return;
    }
    ESP_LOGI(TAG, "discovering service " UUID_SERVICE_STR);
}

static uint16_t chr_end_handle(uint8_t index)
{
    if (index + 1u < s_state.chr_count) {
        return (uint16_t)(s_state.chrs[index + 1u].def_handle - 1u);
    }
    return s_state.svc_end;
}

static void classify_characteristics(void)
{
    s_state.audio_val_handle = 0;
    s_state.audio_cccd_handle = 0;
    s_state.status_val_handle = 0;
    s_state.status_cccd_handle = 0;
    s_state.control_val_handle = 0;

    for (uint8_t i = 0; i < s_state.chr_count; i++) {
        const discovered_chr_t *chr = &s_state.chrs[i];
        if (ble_uuid_cmp(&chr->uuid.u, &s_state.audio_data.u) == 0) {
            s_state.audio_val_handle = chr->val_handle;
            s_state.audio_end_handle = chr_end_handle(i);
        } else if (ble_uuid_cmp(&chr->uuid.u, &s_state.status.u) == 0) {
            s_state.status_val_handle = chr->val_handle;
            s_state.status_end_handle = chr_end_handle(i);
        } else if (ble_uuid_cmp(&chr->uuid.u, &s_state.control.u) == 0) {
            s_state.control_val_handle = chr->val_handle;
        }
    }

    ESP_LOGI(TAG,
             "service handles %u..%u: audio val %u (range to %u), control val %u, status val %u (range to %u)",
             (unsigned)s_state.svc_start, (unsigned)s_state.svc_end,
             (unsigned)s_state.audio_val_handle, (unsigned)s_state.audio_end_handle,
             (unsigned)s_state.control_val_handle,
             (unsigned)s_state.status_val_handle, (unsigned)s_state.status_end_handle);

    if (s_state.audio_val_handle == 0) {
        ESP_LOGE(TAG, "probe has no Audio Data characteristic " UUID_AUDIO_STR);
    }
    if (s_state.control_val_handle == 0) {
        ESP_LOGW(TAG, "probe has no Control characteristic " UUID_CONTROL_STR
                      " (START cannot be sent)");
    }
}

static int svc_disc_cb(uint16_t conn_handle, const struct ble_gatt_error *error,
                       const struct ble_gatt_svc *service, void *arg)
{
    (void)conn_handle;
    (void)arg;

    if (error->status == 0 && service != NULL) {
        s_state.svc_start = service->start_handle;
        s_state.svc_end = service->end_handle;
        ESP_LOGI(TAG, "found LKA1 service: handles %u..%u",
                 (unsigned)service->start_handle, (unsigned)service->end_handle);
        return 0;
    }

    if (error->status == BLE_HS_EDONE) {
        if (s_state.svc_start == 0) {
            ESP_LOGW(TAG, "probe does not expose service " UUID_SERVICE_STR);
            (void)ble_gap_terminate(s_state.conn_handle, BLE_ERR_REM_USER_CONN_TERM);
            return 0;
        }
        s_state.chr_count = 0;
        s_state.phase = DISC_CHARS;
        int rc = ble_gattc_disc_all_chrs(s_state.conn_handle, s_state.svc_start,
                                         s_state.svc_end, chr_disc_cb, NULL);
        if (rc != 0) {
            ESP_LOGE(TAG, "disc_all_chrs failed rc=%d", rc);
            (void)ble_gap_terminate(s_state.conn_handle, BLE_ERR_REM_USER_CONN_TERM);
        }
        return 0;
    }

    ESP_LOGW(TAG, "service discovery error status=%d", error->status);
    return 0;
}

static int chr_disc_cb(uint16_t conn_handle, const struct ble_gatt_error *error,
                       const struct ble_gatt_chr *chr, void *arg)
{
    (void)conn_handle;
    (void)arg;

    if (error->status == 0 && chr != NULL) {
        if (s_state.chr_count < BLE_CENTRAL_MAX_CHARS) {
            discovered_chr_t *slot = &s_state.chrs[s_state.chr_count++];
            slot->def_handle = chr->def_handle;
            slot->val_handle = chr->val_handle;
            slot->uuid = chr->uuid;
            char uuid_str[BLE_UUID_STR_LEN];
            ESP_LOGD(TAG, "chr def %u val %u props 0x%02x uuid %s",
                     (unsigned)chr->def_handle, (unsigned)chr->val_handle,
                     chr->properties, ble_uuid_to_str(&chr->uuid.u, uuid_str));
        } else {
            ESP_LOGW(TAG, "more than %u characteristics, ignoring the rest",
                     (unsigned)BLE_CENTRAL_MAX_CHARS);
        }
        return 0;
    }

    if (error->status == BLE_HS_EDONE) {
        classify_characteristics();
        if (s_state.audio_val_handle == 0) {
            (void)ble_gap_terminate(s_state.conn_handle, BLE_ERR_REM_USER_CONN_TERM);
            return 0;
        }
        s_state.phase = DISC_DSC_AUDIO;
        int rc = ble_gattc_disc_all_dscs(s_state.conn_handle,
                                         s_state.audio_val_handle,
                                         s_state.audio_end_handle,
                                         dsc_disc_cb, NULL);
        if (rc != 0) {
            ESP_LOGE(TAG, "disc_all_dscs(audio) failed rc=%d", rc);
            (void)ble_gap_terminate(s_state.conn_handle, BLE_ERR_REM_USER_CONN_TERM);
        }
        return 0;
    }

    ESP_LOGW(TAG, "characteristic discovery error status=%d", error->status);
    return 0;
}

static int dsc_disc_cb(uint16_t conn_handle, const struct ble_gatt_error *error,
                       uint16_t chr_val_handle, const struct ble_gatt_dsc *dsc,
                       void *arg)
{
    (void)conn_handle;
    (void)arg;

    if (error->status == 0 && dsc != NULL) {
        if (dsc->uuid.u.type == BLE_UUID_TYPE_16 &&
            dsc->uuid.u16.value == BLE_GATT_DSC_CLT_CFG_UUID16) {
            if (chr_val_handle == s_state.audio_val_handle) {
                s_state.audio_cccd_handle = dsc->handle;
            } else if (chr_val_handle == s_state.status_val_handle) {
                s_state.status_cccd_handle = dsc->handle;
            }
            ESP_LOGD(TAG, "CCCD for val handle %u at %u",
                     (unsigned)chr_val_handle, (unsigned)dsc->handle);
        }
        return 0;
    }

    if (error->status == BLE_HS_EDONE) {
        if (s_state.phase == DISC_DSC_AUDIO) {
            if (s_state.status_val_handle != 0) {
                s_state.phase = DISC_DSC_STATUS;
                int rc = ble_gattc_disc_all_dscs(s_state.conn_handle,
                                                 s_state.status_val_handle,
                                                 s_state.status_end_handle,
                                                 dsc_disc_cb, NULL);
                if (rc != 0) {
                    ESP_LOGE(TAG, "disc_all_dscs(status) failed rc=%d", rc);
                    (void)ble_gap_terminate(s_state.conn_handle, BLE_ERR_REM_USER_CONN_TERM);
                }
                return 0;
            }
            s_state.phase = DISC_DONE;
            try_subscribe();
            return 0;
        }

        if (s_state.phase == DISC_DSC_STATUS) {
            s_state.phase = DISC_DONE;
            try_subscribe();
            return 0;
        }
        return 0;
    }

    ESP_LOGW(TAG, "descriptor discovery error status=%d", error->status);
    return 0;
}

/* ------------------------------------------------------------------------- */
/* Subscribing (CCCD) - only after the MTU event                             */
/* ------------------------------------------------------------------------- */

static int cccd_status_cb(uint16_t conn_handle, const struct ble_gatt_error *error,
                          struct ble_gatt_attr *attr, void *arg)
{
    (void)conn_handle;
    (void)attr;
    (void)arg;

    if (error->status != 0) {
        ESP_LOGE(TAG, "Status CCCD write failed status=%d", error->status);
        (void)ble_gap_terminate(s_state.conn_handle, BLE_ERR_REM_USER_CONN_TERM);
        return 0;
    }

    s_state.subscribed = true;
    s_state.ready = true;
    ESP_LOGI(TAG,
             "notifications enabled (audio + status); MTU %u, PHY %s; probe may stream after START",
             (unsigned)s_state.att_mtu, phy_name(s_state.tx_phy));
    send_control_start();
    return 0;
}

static int cccd_audio_cb(uint16_t conn_handle, const struct ble_gatt_error *error,
                         struct ble_gatt_attr *attr, void *arg)
{
    uint8_t value[2] = {0x01, 0x00}; /* notify = 1 */
    int rc;

    (void)conn_handle;
    (void)attr;
    (void)arg;

    if (error->status != 0) {
        ESP_LOGE(TAG, "Audio Data CCCD write failed status=%d", error->status);
        (void)ble_gap_terminate(s_state.conn_handle, BLE_ERR_REM_USER_CONN_TERM);
        return 0;
    }
    ESP_LOGI(TAG, "Audio Data subscriptions enabled (CCCD %u)",
             (unsigned)s_state.audio_cccd_handle);

    if (s_state.status_cccd_handle == 0) {
        /* No Status characteristic: fine for the audio path, log and continue. */
        s_state.subscribed = true;
        s_state.ready = true;
        ESP_LOGW(TAG, "probe has no Status CCCD; watchdog status unavailable");
        send_control_start();
        return 0;
    }

    rc = ble_gattc_write_flat(s_state.conn_handle, s_state.status_cccd_handle,
                              value, sizeof(value), cccd_status_cb, NULL);
    if (rc != 0) {
        ESP_LOGE(TAG, "Status CCCD write failed rc=%d", rc);
        (void)ble_gap_terminate(s_state.conn_handle, BLE_ERR_REM_USER_CONN_TERM);
    }
    return 0;
}

static void try_subscribe(void)
{
    uint8_t value[2] = {0x01, 0x00}; /* notify = 1, little-endian */
    int rc;

    if (!s_state.connected || s_state.subscribed) {
        return;
    }
    if (!s_state.mtu_done) {
        /*
         * Hard ordering constraint from profile section 4: the probe fragments
         * at 20 B until the MTU is raised, so subscribing early would make the
         * 244 B LKA1 assumption false. Never remove this gate.
         */
        ESP_LOGD(TAG, "waiting for MTU before enabling CCCD");
        return;
    }
    if (s_state.phase != DISC_DONE) {
        return;
    }
    if (s_state.audio_cccd_handle == 0) {
        ESP_LOGE(TAG, "Audio Data has no CCCD (0x2902); cannot enable notifications");
        (void)ble_gap_terminate(s_state.conn_handle, BLE_ERR_REM_USER_CONN_TERM);
        return;
    }

    /*
     * Log proved ordering. This line is what the hardware verification item
     * #2 ("MTU before CCCD") will check in the console log.
     */
    ESP_LOGI(TAG,
             "enabling Audio Data notifications now that MTU is known (%u B); fragment payload %u B",
             (unsigned)s_state.att_mtu, (unsigned)(s_state.att_mtu - 3u));

    rc = ble_gattc_write_flat(s_state.conn_handle, s_state.audio_cccd_handle,
                              value, sizeof(value), cccd_audio_cb, NULL);
    if (rc != 0) {
        ESP_LOGE(TAG, "Audio Data CCCD write failed rc=%d", rc);
        (void)ble_gap_terminate(s_state.conn_handle, BLE_ERR_REM_USER_CONN_TERM);
    }
}

static void send_control_start(void)
{
    s_state.next_session_id = (uint16_t)(s_state.next_session_id + 1u);
    if (s_state.next_session_id == 0) {
        s_state.next_session_id = 1;
    }

#if BLE_CENTRAL_CONTROL_START_FROZEN
    if (s_state.control_val_handle != 0) {
        int rc = ble_gattc_write_flat(s_state.conn_handle, s_state.control_val_handle,
                                      k_candidate_start, sizeof(k_candidate_start),
                                      NULL, NULL);
        ESP_LOGI(TAG, "sent Control START session_id=0x%04x rc=%d",
                 (unsigned)s_state.next_session_id, rc);
        return;
    }
    ESP_LOGE(TAG, "no Control characteristic; cannot start the probe");
#else
    ESP_LOGW(TAG,
             "Control START NOT sent: byte format is still a candidate "
             "(docs/link-a-gatt-profile.md section 5.1). Next session_id would be 0x%04x. "
             "The probe stays IDLE until the format is frozen.",
             (unsigned)s_state.next_session_id);
#endif
}

/* ------------------------------------------------------------------------- */
/* GAP events                                                                */
/* ------------------------------------------------------------------------- */

static void on_connected(uint16_t conn_handle)
{
    struct ble_gap_conn_desc desc;
    char addr_str[18];

    s_state.connected = true;
    s_state.connecting = false;
    s_state.conn_handle = conn_handle;
    s_state.mtu_done = false;
    s_state.phy_done = false;
    s_state.subscribed = false;
    s_state.discovery_started = false;
    s_state.ready = false;
    s_state.att_mtu = 0;
    s_state.tx_phy = 0;
    s_state.rx_phy = 0;
    s_state.svc_start = 0;
    s_state.svc_end = 0;
    s_state.chr_count = 0;
    s_state.phase = DISC_SERVICE;
    s_state.audio_val_handle = 0;
    s_state.audio_cccd_handle = 0;
    s_state.status_val_handle = 0;
    s_state.status_cccd_handle = 0;
    s_state.control_val_handle = 0;

    /* A successful link resets the backoff ladder. */
    s_state.reconnect_attempts = 0;
    (void)esp_timer_stop(s_reconnect_timer);

    addr_to_str(&s_state.peer_addr, addr_str);
    if (ble_gap_conn_find(conn_handle, &desc) == 0) {
        ESP_LOGI(TAG,
                 "connected to %s handle=%u itvl=%u x1.25ms latency=%u timeout=%u x10ms",
                 addr_str, (unsigned)conn_handle, (unsigned)desc.conn_itvl,
                 (unsigned)desc.conn_latency, (unsigned)desc.supervision_timeout);
        /* Ask for the profile's 15 ms if the established link is outside range. */
        if (desc.conn_itvl < BLE_CENTRAL_CONN_ITVL_MIN_UNITS ||
            desc.conn_itvl > BLE_CENTRAL_CONN_ITVL_MAX_UNITS ||
            desc.conn_latency != 0) {
            struct ble_gap_upd_params upd = {0};
            upd.itvl_min = BLE_CENTRAL_CONN_ITVL_MIN_UNITS;
            upd.itvl_max = BLE_CENTRAL_CONN_ITVL_MAX_UNITS;
            upd.latency = 0;
            upd.supervision_timeout = BLE_CENTRAL_SUPERVISION_TIMEOUT_UNITS;
            upd.min_ce_len = 0;
            upd.max_ce_len = 0;
            int rc = ble_gap_update_params(conn_handle, &upd);
            ESP_LOGI(TAG, "connection interval outside profile range; update rc=%d", rc);
        }
    } else {
        ESP_LOGW(TAG, "connected (handle=%u) but the connection descriptor is unavailable",
                 (unsigned)conn_handle);
    }

    /*
     * Ordering (profile section 4): MTU first, then PHY / conn params, then
     * service discovery, then CCCD. Discovery is deliberately NOT started
     * here: NimBLE allows only one outstanding ATT procedure per connection, so
     * the MTU exchange must complete (BLE_GAP_EVENT_MTU) before service
     * discovery can be issued. start_service_discovery() runs from on_mtu() or,
     * if the peer never answers, from the MTU fallback timer.
     */
    int rc = ble_gattc_exchange_mtu(conn_handle, NULL, NULL);
    if (rc != 0) {
        ESP_LOGW(TAG, "ATT MTU request returned rc=%d; waiting for the stack to report MTU",
                 rc);
    }
    (void)esp_timer_stop(s_mtu_timer);
    (void)esp_timer_start_once(s_mtu_timer, 2000u * 1000u);

    rc = ble_gap_set_prefered_le_phy(conn_handle, BLE_GAP_LE_PHY_2M_MASK,
                                     BLE_GAP_LE_PHY_2M_MASK, 0);
    if (rc != 0) {
        ESP_LOGW(TAG, "2M PHY request failed rc=%d; link stays at 1M", rc);
        uint8_t tx = 0;
        uint8_t rx = 0;
        if (phy_of_conn(conn_handle, &tx, &rx)) {
            s_state.tx_phy = tx;
            s_state.rx_phy = rx;
        }
    }
}

static void on_disconnected(int reason)
{
    ESP_LOGW(TAG, "disconnected reason=0x%02x; notifications=%u push_ok=%u push_rejected=%u",
             (unsigned)reason, (unsigned)s_state.notifications,
             (unsigned)s_state.push_ok, (unsigned)s_state.push_rejected);

    s_state.connected = false;
    s_state.connecting = false;
    s_state.subscribed = false;
    s_state.ready = false;
    s_state.mtu_done = false;
    s_state.phase = DISC_IDLE;
    (void)esp_timer_stop(s_mtu_timer);

    schedule_reconnect("link lost");
}

static void on_disc(const struct ble_gap_disc_desc *disc)
{
    struct ble_hs_adv_fields fields;
    char addr_str[18];

    if (s_state.connected || s_state.connecting) {
        return;
    }
    if (disc == NULL || disc->data == NULL) {
        return;
    }
    memset(&fields, 0, sizeof(fields));
    if (ble_hs_adv_parse_fields(&fields, disc->data, disc->length_data) != 0) {
        return;
    }

    /*
     * Filter by the 128-bit service UUID, never by device name (profile
     * section 3.3: the name is not a unique identity). The UUID normally
     * arrives in the SCAN_RSP, which is a second report for the same address.
     */
    if (fields.num_uuids128 == 0 || fields.uuids128 == NULL) {
        return;
    }
    for (uint8_t i = 0; i < fields.num_uuids128; i++) {
        if (ble_uuid_cmp(&s_state.service.u,
                         (const ble_uuid_t *)&fields.uuids128[i]) != 0) {
            continue;
        }
        addr_to_str(&disc->addr, addr_str);
        ESP_LOGI(TAG, "LKA1 service advertised by %s (rssi %d dBm)", addr_str,
                 (int)disc->rssi);
        memcpy(&s_state.peer_addr, &disc->addr, sizeof(s_state.peer_addr));
        connect_to(&disc->addr);
        return;
    }
}

static void on_mtu(uint16_t conn_handle, uint16_t mtu)
{
    if (!s_state.connected || conn_handle != s_state.conn_handle) {
        return;
    }
    (void)esp_timer_stop(s_mtu_timer);

    s_state.att_mtu = mtu;
    s_state.mtu_done = true;

    ESP_LOGI(TAG, "ATT MTU negotiated = %u (profile %u, LKA1 payload %u B)",
             (unsigned)mtu, (unsigned)BLE_CENTRAL_ATT_MTU,
             (unsigned)(mtu >= 3u ? mtu - 3u : 0u));
    if (mtu < BLE_CENTRAL_ATT_MTU) {
        ESP_LOGW(TAG,
                 "MTU below the profile value: fragments shrink and throughput headroom is lost; "
                 "profile fallback is 158 (3 fragments/frame). Re-run the throughput budget.");
    }
    /* The ATT procedure slot is free now; discovery and then the CCCD write can
     * proceed in that order. */
    start_service_discovery();
    try_subscribe();
}

static int gap_event_cb(struct ble_gap_event *event, void *arg)
{
    (void)arg;

    switch (event->type) {
    case BLE_GAP_EVENT_CONNECT:
        if (event->connect.status == 0) {
            on_connected(event->connect.conn_handle);
        } else {
            ESP_LOGW(TAG, "connection failed status=%d", event->connect.status);
            s_state.connecting = false;
            schedule_reconnect("connect attempt failed");
        }
        return 0;

    case BLE_GAP_EVENT_DISCONNECT:
        on_disconnected(event->disconnect.reason);
        return 0;

    case BLE_GAP_EVENT_DISC:
        on_disc(&event->disc);
        return 0;

    case BLE_GAP_EVENT_DISC_COMPLETE:
        /*
         * Also fires when we cancel the scan to connect; only treat it as a
         * failed attempt when no connection is in flight.
         */
        if (!s_state.connected && !s_state.connecting) {
            ESP_LOGW(TAG, "scan finished without a connectable LKA1 probe (reason=0x%02x)",
                     (unsigned)event->disc_complete.reason);
            schedule_reconnect("scan timeout");
        }
        return 0;

    case BLE_GAP_EVENT_MTU:
        on_mtu(event->mtu.conn_handle, event->mtu.value);
        return 0;

    case BLE_GAP_EVENT_CONN_UPDATE:
        if (event->conn_update.status == 0) {
            struct ble_gap_conn_desc desc;
            if (ble_gap_conn_find(event->conn_update.conn_handle, &desc) == 0) {
                ESP_LOGI(TAG, "connection updated: itvl=%u x1.25ms latency=%u timeout=%u x10ms",
                         (unsigned)desc.conn_itvl, (unsigned)desc.conn_latency,
                         (unsigned)desc.supervision_timeout);
            }
        } else {
            ESP_LOGW(TAG, "connection update failed status=%d", event->conn_update.status);
        }
        return 0;

    case BLE_GAP_EVENT_PHY_UPDATE_COMPLETE: {
        uint8_t tx = event->phy_updated.tx_phy;
        uint8_t rx = event->phy_updated.rx_phy;
        s_state.phy_done = true;
        if (event->phy_updated.status == 0) {
            s_state.tx_phy = tx;
            s_state.rx_phy = rx;
            ESP_LOGI(TAG, "PHY updated: TX=%s RX=%s (2M preferred, 1M fallback)",
                     phy_name(tx), phy_name(rx));
            if (tx != BLE_GAP_LE_PHY_2M || rx != BLE_GAP_LE_PHY_2M) {
                ESP_LOGW(TAG,
                         "link is not 2M; profile section 2 requires logging the downgrade "
                         "and re-checking the throughput budget");
            }
        } else {
            ESP_LOGW(TAG, "2M PHY request rejected (status=%d); link stays on 1M",
                     event->phy_updated.status);
            uint8_t ltx = 0;
            uint8_t lrx = 0;
            if (phy_of_conn(event->phy_updated.conn_handle, &ltx, &lrx)) {
                s_state.tx_phy = ltx;
                s_state.rx_phy = lrx;
                ESP_LOGW(TAG, "actual PHY: TX=%s RX=%s", phy_name(ltx), phy_name(lrx));
            }
        }
        return 0;
    }

    case BLE_GAP_EVENT_ENC_CHANGE:
        if (event->enc_change.status == 0) {
            ESP_LOGI(TAG, "link encrypted");
        } else {
            ESP_LOGE(TAG, "encryption failed status=%d", event->enc_change.status);
        }
        return 0;

    case BLE_GAP_EVENT_REPEAT_PAIRING: {
        /*
         * Profile section 6: one binding only; a new probe overwrites the old
         * one. Deleting the stale bond and retrying is the only way to let a
         * replaced probe pair without a physical unbind action.
         */
        struct ble_gap_conn_desc desc;
        if (ble_gap_conn_find(event->repeat_pairing.conn_handle, &desc) == 0) {
            int rc = ble_store_util_delete_peer(&desc.peer_id_addr);
            ESP_LOGW(TAG, "repeat pairing: deleted old bond (rc=%d), retrying", rc);
            return BLE_GAP_REPEAT_PAIRING_RETRY;
        }
        ESP_LOGW(TAG, "repeat pairing but the peer address is unavailable; ignoring");
        return BLE_GAP_REPEAT_PAIRING_IGNORE;
    }

    case BLE_GAP_EVENT_NOTIFY_RX: {
        uint16_t attr_handle = event->notify_rx.attr_handle;

        if (attr_handle == s_state.audio_val_handle && s_state.audio_val_handle != 0) {
            uint16_t len = OS_MBUF_PKTLEN(event->notify_rx.om);
            uint16_t copy = len > sizeof(s_notify_buf) ? (uint16_t)sizeof(s_notify_buf) : len;

            if (len > BLE_CENTRAL_MAX_NOTIFICATION_BYTES) {
                s_state.bad_length_notifications++;
                if ((s_state.bad_length_notifications % 100u) == 1u) {
                    ESP_LOGW(TAG,
                             "notification of %u B exceeds MTU-derived max %u B (count=%u); "
                             "clipping and letting the LKA1 CRC reject it",
                             (unsigned)len, (unsigned)BLE_CENTRAL_MAX_NOTIFICATION_BYTES,
                             (unsigned)s_state.bad_length_notifications);
                }
            }
            if (os_mbuf_copydata(event->notify_rx.om, 0, copy, s_notify_buf) != 0) {
                ESP_LOGW(TAG, "os_mbuf_copydata failed (%u B)", (unsigned)copy);
                return 0;
            }

            s_state.notifications++;
            int status = app_pipeline_push_packet(s_notify_buf, copy);
            if (status >= 0) {
                s_state.push_ok++;
            } else {
                s_state.push_rejected++;
                if ((s_state.push_rejected % 50u) == 1u) {
                    ESP_LOGW(TAG, "pipeline rejected packet status=%d (rejected=%u of %u)",
                             status, (unsigned)s_state.push_rejected,
                             (unsigned)s_state.notifications);
                }
            }
        } else if (attr_handle == s_state.status_val_handle &&
                   s_state.status_val_handle != 0) {
            uint8_t status_bytes[4] = {0};
            uint16_t len = OS_MBUF_PKTLEN(event->notify_rx.om);
            if (len > sizeof(status_bytes)) {
                len = sizeof(status_bytes);
            }
            if (os_mbuf_copydata(event->notify_rx.om, 0, len, status_bytes) == 0) {
                /* state, error_code, drop_frames, reserved (profile 3.1) */
                ESP_LOGD(TAG, "probe status: state=%u err=%u drops=%u rsv=%u",
                         status_bytes[0], status_bytes[1], status_bytes[2],
                         status_bytes[3]);
            }
        }
        return 0;
    }

    case BLE_GAP_EVENT_DATA_LEN_CHG:
        /* DLE result: useful evidence for the 2M PHY throughput budget. */
        ESP_LOGI(TAG, "data length changed (DLE negotiated)");
        return 0;

    default:
        return 0;
    }
}

/* ------------------------------------------------------------------------- */
/* Host bring-up                                                             */
/* ------------------------------------------------------------------------- */

static void on_sync(void)
{
    int rc;

    ESP_LOGI(TAG, "NimBLE host synchronised with the controller");

    /* Ensure a usable identity address (public if the eFuse has one). */
    rc = ble_hs_util_ensure_addr(0);
    if (rc != 0) {
        ESP_LOGE(TAG, "ble_hs_util_ensure_addr failed rc=%d", rc);
        return;
    }
    rc = ble_hs_id_infer_auto(0, &s_state.own_addr_type);
    if (rc != 0) {
        ESP_LOGE(TAG, "ble_hs_id_infer_auto failed rc=%d", rc);
        return;
    }

    s_state.synced = true;
    if (s_state.scan_requested) {
        scan_start();
    }
}

/* ble_hs_reset_fn takes the HCI reset reason: typedef void ble_hs_reset_fn(int)
 * in this NimBLE revision (host/ble_hs.h). */
static void on_reset(int reason)
{
    ESP_LOGW(TAG, "NimBLE host reset (reason=%d)", reason);
    s_state.synced = false;
    s_state.connected = false;
    s_state.connecting = false;
    s_state.ready = false;
}

static void host_task(void *param)
{
    (void)param;
    ESP_LOGI(TAG, "NimBLE host task started");
    nimble_port_run();
    nimble_port_freertos_deinit();
}

/* Verify the UUID byte order once, by rendering it back to text. A mistake here
 * would make the firmware scan for, or subscribe to, the wrong attribute. */
static esp_err_t verify_uuid(const ble_uuid_any_t *uuid, const char *expected,
                             const char *what)
{
    char rendered[BLE_UUID_STR_LEN];

    (void)ble_uuid_to_str(&uuid->u, rendered);
    if (strcmp(rendered, expected) != 0) {
        ESP_LOGE(TAG, "%s UUID mismatch: got %s expected %s", what, rendered, expected);
        return ESP_ERR_INVALID_STATE;
    }
    ESP_LOGI(TAG, "%s UUID = %s", what, rendered);
    return ESP_OK;
}

esp_err_t ble_central_init(void)
{
    esp_err_t err;
    int rc;

    memset(&s_state, 0, sizeof(s_state));
    s_state.phase = DISC_IDLE;
    s_state.next_session_id = 0;

    uuid128_from_canonical(&s_state.service, k_uuid_service);
    uuid128_from_canonical(&s_state.audio_data, k_uuid_audio_data);
    uuid128_from_canonical(&s_state.control, k_uuid_control);
    uuid128_from_canonical(&s_state.status, k_uuid_status);

    err = verify_uuid(&s_state.service, UUID_SERVICE_STR, "service");
    if (err != ESP_OK) {
        return err;
    }
    err = verify_uuid(&s_state.audio_data, UUID_AUDIO_STR, "audio data characteristic");
    if (err != ESP_OK) {
        return err;
    }
    err = verify_uuid(&s_state.control, UUID_CONTROL_STR, "control characteristic");
    if (err != ESP_OK) {
        return err;
    }
    err = verify_uuid(&s_state.status, UUID_STATUS_STR, "status characteristic");
    if (err != ESP_OK) {
        return err;
    }

    err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_LOGW(TAG, "NVS needs to be erased (0x%x); erasing", err);
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "nvs_flash_init failed 0x%x", err);
        return err;
    }

    err = nimble_port_init();
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "nimble_port_init failed 0x%x", err);
        return err;
    }

    /* Security: LE Secure Connections + bonding, Just Works (NoInputNoOutput).
     * Profile section 6 accepts the MITM exposure and compensates with a
     * single-bond whitelist and a physical unbind action. */
    ble_hs_cfg.sync_cb = on_sync;
    ble_hs_cfg.reset_cb = on_reset;
    ble_hs_cfg.store_status_cb = ble_store_util_status_rr;
    ble_hs_cfg.sm_io_cap = BLE_SM_IO_CAP_NO_IO;
    ble_hs_cfg.sm_bonding = 1;
    ble_hs_cfg.sm_mitm = 0;
    ble_hs_cfg.sm_sc = 1;
    ble_hs_cfg.sm_our_key_dist = BLE_SM_PAIR_KEY_DIST_ENC | BLE_SM_PAIR_KEY_DIST_ID;
    ble_hs_cfg.sm_their_key_dist = BLE_SM_PAIR_KEY_DIST_ENC | BLE_SM_PAIR_KEY_DIST_ID;

    /* Preferred ATT MTU: requested explicitly after every connect (NimBLE does
     * not initiate the exchange by itself). */
    rc = ble_att_set_preferred_mtu(BLE_CENTRAL_ATT_MTU);
    if (rc != 0) {
        ESP_LOGE(TAG, "ble_att_set_preferred_mtu(%u) failed rc=%d",
                 (unsigned)BLE_CENTRAL_ATT_MTU, rc);
        return ESP_ERR_INVALID_STATE;
    }

    /* Bond keys persist in NVS so a reboot does not need re-pairing. */
    ble_store_config_init();

    const esp_timer_create_args_t reconnect_args = {
        .callback = reconnect_timer_cb,
        .arg = NULL,
        .dispatch_method = ESP_TIMER_TASK,
        .name = "ble_reconnect",
    };
    err = esp_timer_create(&reconnect_args, &s_reconnect_timer);
    if (err != ESP_OK) {
        return err;
    }

    const esp_timer_create_args_t mtu_args = {
        .callback = mtu_timer_cb,
        .arg = NULL,
        .dispatch_method = ESP_TIMER_TASK,
        .name = "ble_mtu",
    };
    err = esp_timer_create(&mtu_args, &s_mtu_timer);
    if (err != ESP_OK) {
        return err;
    }

    /* nimble_port_freertos_init() returns void in the NimBLE revision pinned by
     * ESP-IDF v5.5.6 (nimble/porting/npl/freertos/include/nimble/nimble_port_freertos.h);
     * it returns esp_err_t only in newer revisions. Do not "fix" this to an
     * error check without checking that header. */
    nimble_port_freertos_init(host_task);

    ESP_LOGI(TAG, "NimBLE central ready (MTU target %u, 2M PHY preferred, 1 binding)",
             (unsigned)BLE_CENTRAL_ATT_MTU);
    return ESP_OK;
}

esp_err_t ble_central_start_scan(void)
{
    s_state.scan_requested = true;
    if (s_state.synced) {
        scan_start();
    }
    /* Otherwise on_sync() starts the scan as soon as the host is ready. */
    return ESP_OK;
}

void ble_central_get_link_info(ble_central_link_info_t *out)
{
    if (out == NULL) {
        return;
    }
    out->connected = s_state.connected;
    out->conn_handle = s_state.conn_handle;
    out->att_mtu = s_state.att_mtu;
    out->tx_phy = s_state.tx_phy;
    out->rx_phy = s_state.rx_phy;
    out->conn_itvl_1_25ms = 0; /* read live via ble_gap_conn_find when needed */
    out->reconnect_attempts = s_state.reconnect_attempts;
    out->notifications = s_state.notifications;
    out->push_ok = s_state.push_ok;
    out->push_rejected = s_state.push_rejected;
    out->ready = s_state.ready;

    if (s_state.connected) {
        struct ble_gap_conn_desc desc;
        if (ble_gap_conn_find(s_state.conn_handle, &desc) == 0) {
            out->conn_itvl_1_25ms = desc.conn_itvl;
        }
    }
}
