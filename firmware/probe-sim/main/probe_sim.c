/*
 * ESP32-S3 probe simulator.
 *
 * Stands in for the teammate's probe until the real hardware exists: it
 * advertises the Link A service, streams a stored recording as AUD1 frames
 * inside LKA1 packets at 50 frames per second, and answers the Control
 * characteristic. The phone cannot tell it from a probe except by the data,
 * which is the point -- the whole point-to-phone path can be debugged before
 * anyone solders a microphone.
 *
 * It is deliberately *not* an audio device: no ADC, no I2S, no microphone. The
 * source is one 8 kHz/16-bit mono recording embedded in flash and looped, so two
 * runs produce the same bytes and the phone's result can be compared with the
 * same file fed through the page's file picker.
 *
 * See docs/link-a-gatt-profile.md for the contract and firmware/probe-sim/README.md
 * for how to flash and observe it.
 */

#include <string.h>

#include "esp_log.h"
#include "esp_timer.h"
#include "nvs_flash.h"

#include "host/ble_hs.h"
#include "host/util/util.h"
#include "nimble/nimble_port.h"
#include "nimble/nimble_port_freertos.h"
#include "services/gap/ble_svc_gap.h"
#include "services/gatt/ble_svc_gatt.h"

#include "lka1_encode.h"

static const char *TAG = "probe-sim";

/* Embedded recording: the same clip the phone's self-check uses. */
extern const uint8_t sample_wav_start[] asm("_binary_sample_wav_start");
extern const uint8_t sample_wav_end[] asm("_binary_sample_wav_end");

/* ---- Link A UUIDs, little-endian byte order as NimBLE expects -------------- */

static const ble_uuid128_t service_uuid = BLE_UUID128_INIT(
    0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x80,
    0x00, 0x40, 0x00, 0x00, 0x31, 0x41, 0x4b, 0x4c);

static const ble_uuid128_t audio_uuid = BLE_UUID128_INIT(
    0x02, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x80,
    0x00, 0x40, 0x00, 0x00, 0x31, 0x41, 0x4b, 0x4c);

static const ble_uuid128_t control_uuid = BLE_UUID128_INIT(
    0x03, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x80,
    0x00, 0x40, 0x00, 0x00, 0x31, 0x41, 0x4b, 0x4c);

static const ble_uuid128_t status_uuid = BLE_UUID128_INIT(
    0x04, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x80,
    0x00, 0x40, 0x00, 0x00, 0x31, 0x41, 0x4b, 0x4c);

/* ---- state ---------------------------------------------------------------- */

static uint8_t own_addr_type;
static uint16_t conn_handle = BLE_HS_CONN_HANDLE_NONE;
static uint16_t audio_handle;      /* value handle of the Audio Data characteristic */
static uint16_t status_handle;     /* value handle of the Status characteristic */

static volatile bool streaming;
static uint16_t session_id = 1;
static uint32_t frame_sequence;
static uint32_t frames_sent;
static uint32_t packets_sent;
static uint32_t dropped_frames;

static const int16_t *pcm;         /* start of the PCM inside the embedded wav */
static size_t pcm_samples;
static size_t pcm_cursor;

static esp_timer_handle_t frame_timer;

/** Wav header scan: find the "data" chunk and return the PCM after it. */
static bool wav_locate_pcm(const uint8_t *wav, size_t length,
                           const int16_t **out_pcm, size_t *out_samples)
{
    if (length < 44 || memcmp(wav, "RIFF", 4) != 0 || memcmp(wav + 8, "WAVE", 4) != 0) {
        return false;
    }
    size_t offset = 12;
    while (offset + 8 <= length) {
        const char *id = (const char *)(wav + offset);
        uint32_t size = (uint32_t)wav[offset + 4] | ((uint32_t)wav[offset + 5] << 8) |
                        ((uint32_t)wav[offset + 6] << 16) | ((uint32_t)wav[offset + 7] << 24);
        if (memcmp(id, "data", 4) == 0) {
            if (offset + 8 + size > length) {
                return false;
            }
            *out_pcm = (const int16_t *)(wav + offset + 8);
            *out_samples = size / 2;
            return size >= AUD1_PAYLOAD_BYTES;
        }
        offset += 8 + size + (size & 1);
    }
    return false;
}

/** Send one notification, copying the payload (NimBLE owns the copy). */
static int notify(uint16_t attr_handle, const uint8_t *data, size_t length)
{
    if (conn_handle == BLE_HS_CONN_HANDLE_NONE) {
        return BLE_HS_ENOTCONN;
    }
    struct os_mbuf *om = ble_hs_mbuf_from_flat(data, (uint16_t)length);
    if (om == NULL) {
        return BLE_HS_ENOMEM;
    }
    return ble_gatts_notify_custom(conn_handle, attr_handle, om);
}

static void send_status(void)
{
    uint8_t status[4] = {
        streaming ? 1 : 0,          /* state: IDLE / STREAMING */
        0,                          /* error: OK */
        (uint8_t)(dropped_frames & 0xff),
        0,                          /* reserved */
    };
    if (notify(status_handle, status, sizeof(status)) == 0) {
        ESP_LOGD(TAG, "status sent: state=%u drop=%u", status[0], status[2]);
    }
}

/** Send one AUD1 frame as two LKA1 packets (222 + 130 bytes of PCM). */
static void send_frame(const int16_t *samples)
{
    uint8_t frame[AUD1_FRAME_BYTES];
    if (aud1_encode(frame, sizeof(frame), samples, frame_sequence,
                    (uint32_t)(esp_timer_get_time() / 1000)) != AUD1_FRAME_BYTES) {
        dropped_frames++;
        return;
    }

    uint8_t packet[LKA1_MAX_PACKET_BYTES];
    size_t first = 222;
    size_t remaining = AUD1_FRAME_BYTES - first;

    size_t length = lka1_encode_packet(packet, sizeof(packet), LKA1_TYPE_DATA,
                                       LKA1_FLAG_START, session_id, frame_sequence,
                                       0, 2, 0, frame, (uint16_t)first);
    if (length == 0 || notify(audio_handle, packet, length) != 0) {
        dropped_frames++;
        return;
    }
    packets_sent++;

    length = lka1_encode_packet(packet, sizeof(packet), LKA1_TYPE_DATA,
                                LKA1_FLAG_END, session_id, frame_sequence,
                                1, 2, (uint16_t)first, frame + first, (uint16_t)remaining);
    if (length == 0 || notify(audio_handle, packet, length) != 0) {
        dropped_frames++;
        return;
    }
    packets_sent++;

    frame_sequence++;
    frames_sent++;
}

/** 20 ms tick: 160 samples per frame = 8 kHz. */
static void frame_timer_cb(void *arg)
{
    (void)arg;
    if (!streaming || conn_handle == BLE_HS_CONN_HANDLE_NONE) {
        return;
    }
    if (pcm_cursor + AUD1_FRAME_SAMPLES > pcm_samples) {
        pcm_cursor = 0;                     /* loop the recording */
    }
    send_frame(pcm + pcm_cursor);
    pcm_cursor += AUD1_FRAME_SAMPLES;

    if ((frames_sent % 50) == 0) {          /* once per second */
        ESP_LOGI(TAG, "frames=%lu packets=%lu dropped=%lu",
                 (unsigned long)frames_sent, (unsigned long)packets_sent,
                 (unsigned long)dropped_frames);
        send_status();
    }
}

/* ---- Control characteristic ---------------------------------------------- */

static int control_access(uint16_t conn_handle_in, uint16_t attr_handle,
                          struct ble_gatt_access_ctxt *ctxt, void *arg)
{
    (void)conn_handle_in;
    (void)attr_handle;
    (void)arg;
    if (ctxt->op != BLE_GATT_ACCESS_OP_WRITE_CHR) {
        return BLE_ATT_ERR_UNLIKELY;
    }
    uint8_t value[8];
    uint16_t length = 0;
    if (ble_hs_mbuf_to_flat(ctxt->om, value, sizeof(value), &length) != 0 ||
        length < LKA1_COMMAND_PAYLOAD_BYTES) {
        return BLE_ATT_ERR_INVALID_ATTR_VALUE_LEN;
    }
    const uint8_t command = value[0];
    const uint32_t token = (uint32_t)value[4] | ((uint32_t)value[5] << 8) |
                           ((uint32_t)value[6] << 16) | ((uint32_t)value[7] << 24);
    switch (command) {
    case LKA1_CMD_START:
        session_id = (uint16_t)(session_id + 1);
        frame_sequence = 0;
        frames_sent = 0;
        packets_sent = 0;
        dropped_frames = 0;
        pcm_cursor = 0;
        streaming = true;
        ESP_LOGI(TAG, "START session=%u token=%lu", session_id, (unsigned long)token);
        break;
    case LKA1_CMD_STOP:
        streaming = false;
        ESP_LOGI(TAG, "STOP session=%u", session_id);
        break;
    case LKA1_CMD_GET_STATUS:
        break;
    default:
        ESP_LOGW(TAG, "unknown command %u", command);
        break;
    }
    send_status();
    return 0;
}

/* ---- GATT table ----------------------------------------------------------- */

static const struct ble_gatt_svc_def gatt_services[] = {
    {
        .type = BLE_GATT_SVC_TYPE_PRIMARY,
        .uuid = &service_uuid.u,
        .characteristics = (struct ble_gatt_chr_def[]){
            {
                .uuid = &audio_uuid.u,
                .access_cb = NULL,
                .flags = BLE_GATT_CHR_F_NOTIFY,
                .val_handle = &audio_handle,
            },
            {
                .uuid = &control_uuid.u,
                .access_cb = control_access,
                .flags = BLE_GATT_CHR_F_WRITE,
            },
            {
                .uuid = &status_uuid.u,
                .access_cb = NULL,
                .flags = BLE_GATT_CHR_F_NOTIFY | BLE_GATT_CHR_F_READ,
                .val_handle = &status_handle,
            },
            {0},
        },
    },
    {0},
};

/* ---- GAP ------------------------------------------------------------------ */

static void advertise(void)
{
    struct ble_hs_adv_fields fields = {0};
    fields.flags = BLE_HS_ADV_F_DISC_GEN | BLE_HS_ADV_F_BREDR_UNSUP;
    fields.uuids128 = (ble_uuid128_t *)&service_uuid;
    fields.num_uuids128 = 1;
    fields.uuids128_is_complete = 1;

    int rc = ble_gap_adv_set_fields(&fields);
    if (rc != 0) {
        ESP_LOGE(TAG, "adv fields: %d", rc);
        return;
    }

    struct ble_gap_adv_params params = {0};
    params.conn_mode = BLE_GAP_CONN_MODE_UND;
    params.disc_mode = BLE_GAP_DISC_MODE_GEN;
    params.itvl_min = BLE_GAP_ADV_ITVL_MS(100);
    params.itvl_max = BLE_GAP_ADV_ITVL_MS(100);

    rc = ble_gap_adv_start(own_addr_type, NULL, BLE_HS_FOREVER, &params, NULL, NULL);
    if (rc != 0) {
        ESP_LOGE(TAG, "adv start: %d", rc);
    } else {
        ESP_LOGI(TAG, "advertising, waiting for the phone");
    }
}

static int gap_event(struct ble_gap_event *event, void *arg)
{
    (void)arg;
    switch (event->type) {
    case BLE_GAP_EVENT_CONNECT:
        if (event->connect.status != 0) {
            ESP_LOGW(TAG, "connect failed: %d", event->connect.status);
            advertise();
            return 0;
        }
        conn_handle = event->connect.conn_handle;
        ESP_LOGI(TAG, "connected, handle=%d", conn_handle);
        /* 2M PHY and the connection interval the profile asks for. */
        (void)ble_gap_set_prefered_le_phy(conn_handle, BLE_GAP_LE_PHY_2M_MASK,
                                          BLE_GAP_LE_PHY_2M_MASK, 0);
        {
            struct ble_gap_upd_params upd = {
                .itvl_min = 12,             /* 15 ms */
                .itvl_max = 24,             /* 30 ms */
                .latency = 0,
                .supervision_timeout = 400, /* 4 s in 10 ms units */
            };
            int rc = ble_gap_update_params(conn_handle, &upd);
            ESP_LOGI(TAG, "connection params: %d", rc);
        }
        streaming = false;                  /* START is required */
        return 0;

    case BLE_GAP_EVENT_DISCONNECT:
        ESP_LOGW(TAG, "disconnected, reason=%d", event->disconnect.reason);
        conn_handle = BLE_HS_CONN_HANDLE_NONE;
        streaming = false;
        advertise();
        return 0;

    case BLE_GAP_EVENT_MTU:
        ESP_LOGI(TAG, "mtu=%u (channels %u)", event->mtu.value, event->mtu.channel_id);
        return 0;

    case BLE_GAP_EVENT_ADV_COMPLETE:
        advertise();
        return 0;

    default:
        return 0;
    }
}

static void on_sync(void)
{
    int rc = ble_hs_util_ensure_addr(0);
    if (rc != 0) {
        ESP_LOGE(TAG, "ensure_addr: %d", rc);
        return;
    }
    rc = ble_hs_id_infer_auto(0, &own_addr_type);
    if (rc != 0) {
        ESP_LOGE(TAG, "id_infer_auto: %d", rc);
        return;
    }
    advertise();
}

static void on_reset(int reason)
{
    ESP_LOGW(TAG, "host reset, reason=%d", reason);
}

static void host_task(void *param)
{
    (void)param;
    nimble_port_run();
    nimble_port_freertos_deinit();
}

void app_main(void)
{
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    ESP_ERROR_CHECK(err);

    const size_t wav_length = (size_t)(sample_wav_end - sample_wav_start);
    if (!wav_locate_pcm(sample_wav_start, wav_length, &pcm, &pcm_samples)) {
        ESP_LOGE(TAG, "embedded wav has no usable PCM data");
        return;
    }
    ESP_LOGI(TAG, "embedded audio: %u samples (%.2f s at 8 kHz)",
             (unsigned)pcm_samples, (double)pcm_samples / 8000.0);

    ESP_ERROR_CHECK(nimble_port_init());
    ble_hs_cfg.sync_cb = on_sync;
    ble_hs_cfg.reset_cb = on_reset;
    ble_att_set_preferred_mtu(247);

    ble_svc_gap_init();
    ble_svc_gatt_init();
    ESP_ERROR_CHECK(ble_gatts_count_cfg(gatt_services));
    ESP_ERROR_CHECK(ble_gatts_add_svcs(gatt_services));

    const esp_timer_create_args_t timer_args = {
        .callback = frame_timer_cb,
        .name = "frame",
    };
    ESP_ERROR_CHECK(esp_timer_create(&timer_args, &frame_timer));
    ESP_ERROR_CHECK(esp_timer_start_periodic(frame_timer, 20000));   /* 20 ms */

    nimble_port_freertos_init(host_task);
}
