#include "host_pipeline.h"

#include <string.h>

/*
 * Q15 low-pass coefficients for the streaming 2:1 decimator.
 *
 * Generated offline from the repository reference
 * tools/audio-sim/lib/dsp.mjs:designLowpass({sampleRate: 8000,
 * cutoffHz: 1700, taps: 63}) - a Blackman-windowed sinc normalised to unity
 * DC gain - then rounded to Q15. The centre tap was corrected by +3 so that
 * the integer coefficient sum is exactly 32768, which makes the DC gain
 * exactly 1.0 and keeps the output deterministic across toolchains.
 *
 * The table is symmetric; only the integer arithmetic below is executed at
 * runtime (no floating point, no runtime coefficient design).
 */
static const int16_t host_pipeline_decimator_coefficients[
    HOST_PIPELINE_DECIMATOR_TAPS] = {
    0, 0, 1, -1, -6, -2, 14, 13, -21, -40,
    14, 79, 24, -116, -107, 121, 235, -49, -380, -143,
    478, 479, -428, -943, 95, 1473, 718, -1972, -2545, 2328,
    10100, 13930, 10100,
    2328, -2545, -1972, 718, 1473, 95, -943, -428, 479, 478,
    -143, -380, -49, 235, 121, -107, -116, 24, 79, 14,
    -40, -21, 13, 14, -2, -6, -1, 1, 0, 0
};

static int16_t scale_q15(int64_t accumulator)
{
    int64_t magnitude;
    int64_t rounded;

    if (accumulator == 0) {
        return 0;
    }

    /*
     * Round half away from zero, then saturate. This is deterministic and
     * within 1 LSB of the Node Math.round() tie behaviour.
     */
    magnitude = (accumulator < 0) ? -accumulator : accumulator;
    rounded = (magnitude + 16384) >> 15;
    if (accumulator < 0) {
        rounded = -rounded;
    }
    if (rounded > 32767) {
        return 32767;
    }
    if (rounded < -32768) {
        return -32768;
    }
    return (int16_t)rounded;
}

void host_pipeline_decimator_init(host_pipeline_decimator_t *decimator)
{
    if (decimator == NULL) {
        return;
    }
    memset(decimator, 0, sizeof(*decimator));
    decimator->phase = 0u;
}

size_t host_pipeline_decimator_process(
    host_pipeline_decimator_t *decimator,
    const int16_t *input,
    size_t input_samples,
    int16_t *output)
{
    size_t produced = 0u;
    size_t i;

    if (decimator == NULL || output == NULL ||
        (input == NULL && input_samples != 0u)) {
        return 0u;
    }

    for (i = 0u; i < input_samples; ++i) {
        size_t tap;
        int64_t accumulator = 0;

        /*
         * history[0] is the newest sample, history[t] is x[n-t], matching
         * StreamingDecimator8kTo4k in tools/audio-sim/lib/dsp.mjs. Shifting
         * 126 bytes per sample costs about 1 MB/s at 8 kHz, which the host
         * can absorb and which keeps the correspondence with the reference
         * trivially auditable.
         */
        memmove(
            &decimator->history[1],
            &decimator->history[0],
            (HOST_PIPELINE_DECIMATOR_TAPS - 1u) * sizeof(decimator->history[0]));
        decimator->history[0] = input[i];

        if (decimator->phase == 0u) {
            for (tap = 0u; tap < HOST_PIPELINE_DECIMATOR_TAPS; ++tap) {
                accumulator +=
                    (int64_t)decimator->history[tap] *
                    (int64_t)host_pipeline_decimator_coefficients[tap];
            }
            output[produced] = scale_q15(accumulator);
            ++produced;
        }

        decimator->phase = (uint8_t)(decimator->phase ^ 1u);
    }

    return produced;
}

static int session_is_recent(
    const host_pipeline_t *pipeline,
    uint16_t session_id)
{
    uint8_t i;

    for (i = 0u; i < pipeline->recent_session_count; ++i) {
        if (pipeline->recent_sessions[i] == session_id) {
            return 1;
        }
    }
    return 0;
}

static void remember_superseded_session(
    host_pipeline_t *pipeline,
    uint16_t session_id)
{
    uint8_t i;

    if (pipeline->recent_session_count < HOST_PIPELINE_SESSION_HISTORY) {
        pipeline->recent_sessions[pipeline->recent_session_count] = session_id;
        ++pipeline->recent_session_count;
        return;
    }

    for (i = 1u; i < HOST_PIPELINE_SESSION_HISTORY; ++i) {
        pipeline->recent_sessions[i - 1u] = pipeline->recent_sessions[i];
    }
    pipeline->recent_sessions[HOST_PIPELINE_SESSION_HISTORY - 1u] = session_id;
}

/*
 * Empty the reassembler, decimator and ring. Session history and counters are
 * intentionally left alone: they describe the host's view across sessions.
 */
static void reset_transport_state(host_pipeline_t *pipeline)
{
    link_a_reassembler_init(&pipeline->reassembler);
    host_pipeline_decimator_init(&pipeline->decimator);
    pipeline->ring_count = 0u;
    pipeline->ring_write_frame = 0u;
    pipeline->frame_open = 0u;
    pipeline->open_frame_sequence = 0u;
}

static void close_open_frame_as_dropped(host_pipeline_t *pipeline)
{
    if (pipeline->frame_open != 0u) {
        ++pipeline->stats.frames_dropped;
        pipeline->frame_open = 0u;
    }
}

static void ring_push_frame(
    host_pipeline_t *pipeline,
    const int16_t *frame)
{
    size_t offset =
        pipeline->ring_write_frame * HOST_PIPELINE_DECIMATED_FRAME_SAMPLES;

    memcpy(
        &pipeline->ring[offset],
        frame,
        HOST_PIPELINE_DECIMATED_FRAME_SAMPLES * sizeof(int16_t));

    pipeline->ring_write_frame =
        (pipeline->ring_write_frame + 1u) % pipeline->ring_capacity_frames;
    if (pipeline->ring_count < pipeline->ring_capacity_frames) {
        ++pipeline->ring_count;
    } else {
        /* Oldest frame was overwritten. */
        ++pipeline->stats.overruns;
    }
}

static int handle_completed_frame(
    host_pipeline_t *pipeline,
    const uint8_t *frame_data,
    size_t frame_length)
{
    audio_frame_t frame;
    int16_t decimated[HOST_PIPELINE_DECIMATED_FRAME_SAMPLES];
    size_t produced;
    int status;

    if (frame_data == NULL || frame_length != AUDIO_WIRE_FRAME_BYTES) {
        ++pipeline->stats.format_errors;
        return HOST_PIPELINE_STATUS_BAD_AUDIO_FORMAT;
    }

    status = audio_protocol_decode_frame(
        frame_data,
        frame_length,
        &frame,
        NULL,
        NULL);
    if (status == AUDIO_STATUS_BAD_CRC) {
        ++pipeline->stats.crc_errors;
        return HOST_PIPELINE_STATUS_BAD_AUDIO_CRC;
    }
    if (status != AUDIO_STATUS_OK) {
        ++pipeline->stats.format_errors;
        return HOST_PIPELINE_STATUS_BAD_AUDIO_FORMAT;
    }

    produced = host_pipeline_decimator_process(
        &pipeline->decimator,
        frame.samples,
        AUDIO_FRAME_SAMPLES,
        decimated);
    if (produced != HOST_PIPELINE_DECIMATED_FRAME_SAMPLES) {
        /*
         * Defensive only: 160 samples at phase 0 always yield 80 outputs. A
         * short result would silently corrupt the AI window length.
         */
        ++pipeline->stats.format_errors;
        return HOST_PIPELINE_STATUS_BAD_AUDIO_FORMAT;
    }

    ring_push_frame(pipeline, decimated);
    pipeline->stats.last_sequence = frame.sequence;
    ++pipeline->stats.frames_ok;

    return HOST_PIPELINE_STATUS_OK;
}

int host_pipeline_init(
    host_pipeline_t *pipeline,
    int16_t *ring_storage,
    size_t ring_capacity_frames)
{
    if (pipeline == NULL || ring_storage == NULL) {
        return HOST_PIPELINE_STATUS_INVALID_ARGUMENT;
    }
    if (ring_capacity_frames == 0u) {
        ring_capacity_frames = HOST_PIPELINE_DEFAULT_RING_FRAMES;
    }
    if (ring_capacity_frames >
        (SIZE_MAX / (HOST_PIPELINE_DECIMATED_FRAME_SAMPLES * sizeof(int16_t)))) {
        return HOST_PIPELINE_STATUS_INVALID_ARGUMENT;
    }

    memset(pipeline, 0, sizeof(*pipeline));
    pipeline->ring = ring_storage;
    pipeline->ring_capacity_frames = ring_capacity_frames;
    memset(
        ring_storage,
        0,
        ring_capacity_frames *
            HOST_PIPELINE_DECIMATED_FRAME_SAMPLES * sizeof(int16_t));

    link_a_reassembler_init(&pipeline->reassembler);
    host_pipeline_decimator_init(&pipeline->decimator);

    return HOST_PIPELINE_STATUS_OK;
}

int host_pipeline_push(
    host_pipeline_t *pipeline,
    const uint8_t *packet_data,
    size_t packet_length)
{
    link_a_packet_t packet;
    const uint8_t *completed_frame = NULL;
    size_t completed_length = 0u;
    int status;
    int restarted = 0;

    if (pipeline == NULL || packet_data == NULL || packet_length == 0u ||
        pipeline->ring == NULL || pipeline->ring_capacity_frames == 0u) {
        return HOST_PIPELINE_STATUS_INVALID_ARGUMENT;
    }

    ++pipeline->stats.packets_in;

    /*
     * Decode once for the session ID and packet type; link_a_reassembler_push
     * decodes the same packet again. The duplicate decode costs one CRC pass
     * over at most 244 bytes at 50 packets/s and keeps the reassembler API
     * untouched (D5 in the header).
     */
    status = link_a_decode_packet(packet_data, packet_length, &packet, NULL);
    if (status != LINK_A_STATUS_OK) {
        ++pipeline->stats.packet_errors;
        return HOST_PIPELINE_STATUS_BAD_PACKET;
    }
    if (packet.type != LINK_A_PACKET_DATA) {
        return HOST_PIPELINE_STATUS_IGNORED;
    }

    if (pipeline->session_active == 0u) {
        pipeline->session_active = 1u;
        pipeline->current_session_id = packet.session_id;
        pipeline->stats.current_session_id = packet.session_id;
    } else if (packet.session_id != pipeline->current_session_id) {
        if (session_is_recent(pipeline, packet.session_id) != 0) {
            ++pipeline->stats.stale_packets;
            return HOST_PIPELINE_STATUS_STALE_SESSION;
        }

        close_open_frame_as_dropped(pipeline);
        remember_superseded_session(
            pipeline,
            pipeline->current_session_id);
        pipeline->current_session_id = packet.session_id;
        pipeline->stats.current_session_id = packet.session_id;
        reset_transport_state(pipeline);
        ++pipeline->stats.session_restarts;
        restarted = 1;
    }

    /*
     * A newer frame sequence means the previous partial logical frame was
     * abandoned (missing fragment or radio loss). Older sequences are left to
     * the reassembler; counting them as drops would misreport reordering.
     */
    if (pipeline->frame_open != 0u &&
        (int32_t)(packet.frame_sequence - pipeline->open_frame_sequence) > 0) {
        close_open_frame_as_dropped(pipeline);
    }

    status = link_a_reassembler_push(
        &pipeline->reassembler,
        packet_data,
        packet_length,
        &completed_frame,
        &completed_length);

    switch (status) {
    case LINK_A_STATUS_COMPLETE:
        pipeline->frame_open = 0u;
        status = handle_completed_frame(
            pipeline,
            completed_frame,
            completed_length);
        if (status != HOST_PIPELINE_STATUS_OK) {
            return status;
        }
        if (restarted != 0) {
            return HOST_PIPELINE_STATUS_COMPLETE_NEW_SESSION;
        }
        return HOST_PIPELINE_STATUS_COMPLETE;

    case LINK_A_STATUS_IN_PROGRESS:
        pipeline->frame_open = 1u;
        pipeline->open_frame_sequence = packet.frame_sequence;
        if (restarted != 0) {
            return HOST_PIPELINE_STATUS_SESSION_RESTARTED;
        }
        return HOST_PIPELINE_STATUS_INCOMPLETE;

    case LINK_A_STATUS_DUPLICATE:
        return HOST_PIPELINE_STATUS_DUPLICATE;

    case LINK_A_STATUS_OK:
        /* Defensive: only non-data packets take this path. */
        return HOST_PIPELINE_STATUS_IGNORED;

    default:
        ++pipeline->stats.packet_errors;
        return HOST_PIPELINE_STATUS_BAD_PACKET;
    }
}

const host_pipeline_stats_t *host_pipeline_get_stats(
    const host_pipeline_t *pipeline)
{
    if (pipeline == NULL) {
        return NULL;
    }
    return &pipeline->stats;
}

size_t host_pipeline_frames_available(const host_pipeline_t *pipeline)
{
    if (pipeline == NULL) {
        return 0u;
    }
    return pipeline->ring_count;
}

size_t host_pipeline_frames_capacity(const host_pipeline_t *pipeline)
{
    if (pipeline == NULL) {
        return 0u;
    }
    return pipeline->ring_capacity_frames;
}

size_t host_pipeline_read_newest(
    const host_pipeline_t *pipeline,
    int16_t *output,
    size_t frame_count)
{
    size_t start;
    size_t first;

    if (pipeline == NULL || output == NULL || frame_count == 0u ||
        pipeline->ring == NULL || pipeline->ring_capacity_frames == 0u ||
        frame_count > pipeline->ring_count) {
        return 0u;
    }

    start =
        (pipeline->ring_write_frame + pipeline->ring_capacity_frames -
         frame_count) %
        pipeline->ring_capacity_frames;
    first = pipeline->ring_capacity_frames - start;
    if (first > frame_count) {
        first = frame_count;
    }

    memcpy(
        output,
        &pipeline->ring[start * HOST_PIPELINE_DECIMATED_FRAME_SAMPLES],
        first * HOST_PIPELINE_DECIMATED_FRAME_SAMPLES * sizeof(int16_t));

    if (frame_count > first) {
        memcpy(
            &output[first * HOST_PIPELINE_DECIMATED_FRAME_SAMPLES],
            pipeline->ring,
            (frame_count - first) *
                HOST_PIPELINE_DECIMATED_FRAME_SAMPLES * sizeof(int16_t));
    }

    return frame_count;
}

size_t host_pipeline_read_newest_samples(
    const host_pipeline_t *pipeline,
    int16_t *output,
    size_t sample_count)
{
    size_t frames;

    if (sample_count == 0u ||
        (sample_count % HOST_PIPELINE_DECIMATED_FRAME_SAMPLES) != 0u) {
        return 0u;
    }

    frames = sample_count / HOST_PIPELINE_DECIMATED_FRAME_SAMPLES;
    if (host_pipeline_read_newest(pipeline, output, frames) != frames) {
        return 0u;
    }
    return sample_count;
}

void host_pipeline_clear(host_pipeline_t *pipeline)
{
    if (pipeline == NULL) {
        return;
    }
    pipeline->ring_count = 0u;
    pipeline->ring_write_frame = 0u;
}

void host_pipeline_reset(host_pipeline_t *pipeline)
{
    int16_t *ring;
    size_t ring_capacity_frames;

    if (pipeline == NULL) {
        return;
    }

    ring = pipeline->ring;
    ring_capacity_frames = pipeline->ring_capacity_frames;

    memset(pipeline, 0, sizeof(*pipeline));
    pipeline->ring = ring;
    pipeline->ring_capacity_frames = ring_capacity_frames;

    if (ring != NULL && ring_capacity_frames != 0u) {
        memset(
            ring,
            0,
            ring_capacity_frames *
                HOST_PIPELINE_DECIMATED_FRAME_SAMPLES * sizeof(int16_t));
    }

    link_a_reassembler_init(&pipeline->reassembler);
    host_pipeline_decimator_init(&pipeline->decimator);
}
