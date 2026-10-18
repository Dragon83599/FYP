#include "app_pipeline.h"

#include <stdbool.h>
#include <string.h>

#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

static const char *TAG = "app_pipeline";

/*
 * One pipeline instance for the whole firmware. The probe link is a single
 * bonded peer (docs/link-a-gatt-profile.md section 6: 1 binding only), so a
 * second pipeline would have no owner.
 */
static host_pipeline_t s_pipeline;
static int16_t s_ring[APP_PIPELINE_RING_FRAMES *
                       HOST_PIPELINE_DECIMATED_FRAME_SAMPLES];

/*
 * Static mutex: no heap allocation at init, and FreeRTOS does not have to
 * guess a queue length. The storage must outlive the handle, hence file scope.
 */
static StaticSemaphore_t s_lock_storage;
static SemaphoreHandle_t s_lock;
static uint32_t s_lock_misses;

/* The AI window must fit in the ring or the consumer could never read one. */
_Static_assert(APP_PIPELINE_RING_FRAMES >= HOST_PIPELINE_AI_WINDOW_FRAMES,
               "ring must hold at least one 2 s AI window (100 frames)");
_Static_assert(APP_PIPELINE_WINDOW_SAMPLES == 8000u,
               "AI window must stay 8000 samples at 4 kHz (ADR-007)");
_Static_assert(APP_PIPELINE_RING_BYTES == 25600u,
               "ring size changed; update the RAM note in app_pipeline.h");

static bool pipeline_lock(TickType_t ticks)
{
    if (s_lock == NULL) {
        return false;
    }
    if (xSemaphoreTake(s_lock, ticks) != pdTRUE) {
        /*
         * Single-writer counter; a lost increment during a timeout storm is
         * acceptable for a diagnostic. It is only read by the logger task.
         */
        s_lock_misses++;
        return false;
    }
    return true;
}

static void pipeline_unlock(void)
{
    (void)xSemaphoreGive(s_lock);
}

esp_err_t app_pipeline_init(void)
{
    if (s_lock != NULL) {
        return ESP_ERR_INVALID_STATE;
    }

    s_lock = xSemaphoreCreateMutexStatic(&s_lock_storage);
    if (s_lock == NULL) {
        return ESP_ERR_NO_MEM;
    }

    if (host_pipeline_init(&s_pipeline, s_ring, APP_PIPELINE_RING_FRAMES) !=
        HOST_PIPELINE_STATUS_OK) {
        vSemaphoreDelete(s_lock);
        s_lock = NULL;
        return ESP_ERR_INVALID_STATE;
    }

    ESP_LOGI(TAG,
             "ring: %u frames (%.1f s @ %u Hz), %u B static; AI window: %u samples (%.1f s)",
             (unsigned)APP_PIPELINE_RING_FRAMES,
             (double)APP_PIPELINE_RING_FRAMES *
                 (double)HOST_PIPELINE_DECIMATED_FRAME_SAMPLES /
                 (double)AUDIO_AI_SAMPLE_RATE_HZ,
             (unsigned)AUDIO_AI_SAMPLE_RATE_HZ,
             (unsigned)APP_PIPELINE_RING_BYTES,
             (unsigned)APP_PIPELINE_WINDOW_SAMPLES,
             (double)APP_PIPELINE_WINDOW_SAMPLES /
                 (double)AUDIO_AI_SAMPLE_RATE_HZ);
    return ESP_OK;
}

int app_pipeline_push_packet(const uint8_t *packet_data, size_t packet_length)
{
    if (s_lock == NULL || packet_data == NULL || packet_length == 0u) {
        return HOST_PIPELINE_STATUS_INVALID_ARGUMENT;
    }

    /*
     * Never portMAX_DELAY: this runs in the NimBLE host task, and a wedged
     * consumer must not be able to stall the Bluetooth stack (which would drop
     * the connection). A missed lock is counted and the packet is dropped, so
     * the failure is visible instead of silent.
     */
    if (!pipeline_lock(pdMS_TO_TICKS(APP_PIPELINE_LOCK_TIMEOUT_MS))) {
        ESP_LOGW(TAG, "push: lock timeout, packet dropped (misses=%u)",
                 (unsigned)s_lock_misses);
        return HOST_PIPELINE_STATUS_INVALID_ARGUMENT;
    }

    int status = host_pipeline_push(&s_pipeline, packet_data, packet_length);

    pipeline_unlock();
    return status;
}

size_t app_pipeline_read_window(int16_t *output, size_t sample_count)
{
    if (s_lock == NULL || output == NULL || sample_count == 0u ||
        (sample_count % HOST_PIPELINE_DECIMATED_FRAME_SAMPLES) != 0u) {
        return 0u;
    }

    if (!pipeline_lock(pdMS_TO_TICKS(APP_PIPELINE_LOCK_TIMEOUT_MS))) {
        ESP_LOGW(TAG, "read: lock timeout, window skipped (misses=%u)",
                 (unsigned)s_lock_misses);
        return 0u;
    }

    size_t copied =
        host_pipeline_read_newest_samples(&s_pipeline, output, sample_count);

    pipeline_unlock();
    return copied;
}

size_t app_pipeline_frames_available(void)
{
    if (!pipeline_lock(pdMS_TO_TICKS(APP_PIPELINE_LOCK_TIMEOUT_MS))) {
        return 0u;
    }
    size_t frames = host_pipeline_frames_available(&s_pipeline);
    pipeline_unlock();
    return frames;
}

size_t app_pipeline_frames_capacity(void)
{
    return APP_PIPELINE_RING_FRAMES;
}

void app_pipeline_get_stats(host_pipeline_stats_t *out)
{
    if (out == NULL) {
        return;
    }
    memset(out, 0, sizeof(*out));
    if (!pipeline_lock(pdMS_TO_TICKS(APP_PIPELINE_LOCK_TIMEOUT_MS))) {
        return;
    }
    const host_pipeline_stats_t *stats = host_pipeline_get_stats(&s_pipeline);
    if (stats != NULL) {
        *out = *stats;
    }
    pipeline_unlock();
}

uint32_t app_pipeline_lock_misses(void)
{
    return s_lock_misses;
}
