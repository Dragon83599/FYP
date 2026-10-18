/*
 * Portable streaming heart-rate detector - see hr_detector.h for the design
 * decisions (D8-D11), the ported Python reference (tools/ai/hs_s1s2.py) and
 * the evidence levels.
 */

#include "hr_detector.h"

#include <math.h>
#include <string.h>

/*
 * hs_features.design_bandpass_fir(4000, 20, 85, 129): Hamming-windowed sinc,
 * normalised to unit gain at sqrt(20 * 85) Hz. Symmetric, so the centred
 * convolution used below is zero phase, exactly like numpy.convolve(mode
 * = "same") on a symmetric kernel. Values are the float64 reference rounded to
 * binary32; test_hr_detector.c re-derives them from the formula.
 */
const float hr_detector_bandpass_taps[HR_DETECTOR_FILT_TAPS] = {
    -6.62849924e-05f, -3.4955563e-05f, -9.61898083e-06f, 9.45572899e-06f,
    2.14138309e-05f, 2.47446741e-05f, 1.72414327e-05f, -4.00558615e-06f,
    -4.25795334e-05f, -0.000102670186f, -0.000188969669f, -0.000306532069f,
    -0.000460599609f, -0.000656399414f, -0.000898916087f, -0.00119264647f,
    -0.00154134386f, -0.00194775985f, -0.00241339219f, -0.00293824773f,
    -0.0035206292f, -0.00415695441f, -0.00484161597f, -0.00556688875f,
    -0.00632289121f, -0.00709760542f, -0.00787695925f, -0.00864497233f,
    -0.00938396578f, -0.0100748337f, -0.0106973731f, -0.0112306657f,
    -0.011653506f, -0.0119448648f, -0.0120843803f, -0.0120528622f,
    -0.0118328008f, -0.0114088642f, -0.0107683735f, -0.00990174165f,
    -0.00880286438f, -0.00746945099f, -0.00590328499f, -0.00411040547f,
    -0.00210120204f, 0.000109582082f, 0.00250294186f, 0.005055798f,
    0.00774127884f, 0.0105290928f, 0.0133859848f, 0.0162762696f,
    0.0191624296f, 0.0220057675f, 0.0247670979f, 0.0274074648f,
    0.0298888685f, 0.0321749855f, 0.0342318663f, 0.0360285949f,
    0.0375378941f, 0.0387366645f, 0.0396064426f, 0.0401337697f,
    0.0403104609f, 0.0401337697f, 0.0396064426f, 0.0387366645f,
    0.0375378941f, 0.0360285949f, 0.0342318663f, 0.0321749855f,
    0.0298888685f, 0.0274074648f, 0.0247670979f, 0.0220057675f,
    0.0191624296f, 0.0162762696f, 0.0133859848f, 0.0105290928f,
    0.00774127884f, 0.005055798f, 0.00250294186f, 0.000109582082f,
    -0.00210120204f, -0.00411040547f, -0.00590328499f, -0.00746945099f,
    -0.00880286438f, -0.00990174165f, -0.0107683735f, -0.0114088642f,
    -0.0118328008f, -0.0120528622f, -0.0120843803f, -0.0119448648f,
    -0.011653506f, -0.0112306657f, -0.0106973731f, -0.0100748337f,
    -0.00938396578f, -0.00864497233f, -0.00787695925f, -0.00709760542f,
    -0.00632289121f, -0.00556688875f, -0.00484161597f, -0.00415695441f,
    -0.0035206292f, -0.00293824773f, -0.00241339219f, -0.00194775985f,
    -0.00154134386f, -0.00119264647f, -0.000898916087f, -0.000656399414f,
    -0.000460599609f, -0.000306532069f, -0.000188969669f, -0.000102670186f,
    -4.25795334e-05f, -4.00558615e-06f, 1.72414327e-05f, 2.47446741e-05f,
    2.14138309e-05f, 9.45572899e-06f, -9.61898083e-06f, -3.4955563e-05f,
    -6.62849924e-05f
};

/* int16 full scale -> [-1, 1), matching hs_features.read_wav_mono. */
#define HR_INV_FULL_SCALE (1.0f / 32768.0f)

/* A window whose variance is below this carries no heart sound. */
#define HR_MIN_SIGNAL_STD 1e-6

/*
 * Peak candidates per window. Candidates are local maxima over a
 * +/- (refractory_samples / 2) window, so with the validated refractory range
 * (>= 70 ms, i.e. >= 140 samples half-window) they are at least 141 samples
 * apart: at most 57 per 8000-sample window.
 */
#define HR_MAX_CANDIDATES 64

/* Public estimator input cap (see hr_heart_rate_from_events). */
#define HR_MAX_EVENT_INPUT 512u

static const hr_detector_config_t hr_default_config = {
    2.5f,   /* threshold_ratio        */
    0.10f,  /* min_threshold_fraction */
    100.0f, /* refractory_ms          */
    80.0f,  /* prominence_window_ms   */
    0.60f,  /* prominence_fraction    */
    0.55f,  /* merge_fraction         */
    120.0f, /* merge_min_gap_ms       */
    HR_NOISE_FLOOR_WINDOW, /* noise_floor_mode; see D10 + the report */
    HR_P99_WINDOW,         /* p99_mode;        see D10 + the report */
    {0u, 0u}
};

const hr_detector_config_t *hr_detector_default_config(void)
{
    return &hr_default_config;
}

/* ------------------------------------------------------------------------- */
/* Small numeric helpers                                                      */
/* ------------------------------------------------------------------------- */

static void hr_sort_double(double *values, size_t count)
{
    size_t i;

    for (i = 1u; i < count; ++i) {
        double value = values[i];
        size_t j = i;

        while (j > 0u && values[j - 1u] > value) {
            values[j] = values[j - 1u];
            --j;
        }
        values[j] = value;
    }
}

/* numpy.median() semantics: average of the two middle values for even counts. */
static double hr_median_sorted(const double *sorted, size_t count)
{
    if (count == 0u) {
        return NAN;
    }
    if ((count % 2u) == 1u) {
        return sorted[count / 2u];
    }
    return 0.5 * (sorted[count / 2u - 1u] + sorted[count / 2u]);
}

/* numpy.percentile(values, q) on already sorted values, linear interpolation. */
static double hr_percentile_sorted(const double *sorted, size_t count, double q)
{
    double position;
    double fraction;
    size_t low;

    if (count == 0u) {
        return NAN;
    }
    if (count == 1u) {
        return sorted[0];
    }
    position = q * (double)(count - 1u);
    if (position <= 0.0) {
        return sorted[0];
    }
    if (position >= (double)(count - 1u)) {
        return sorted[count - 1u];
    }
    low = (size_t)position;
    fraction = position - (double)low;
    return sorted[low] + fraction * (sorted[low + 1u] - sorted[low]);
}

/*
 * hs_s1s2._two_gap_centres: robust two-cluster split (25/75 percentiles, then
 * five median-split iterations). `gaps` is sorted in place. Because the array
 * stays sorted, the short cluster is the prefix below the split point and the
 * long cluster the suffix, so their medians are direct index reads.
 */
static void hr_two_gap_centres(
    double *gaps,
    size_t count,
    double *short_centre,
    double *long_centre)
{
    double low;
    double high;
    int iteration;

    *short_centre = NAN;
    *long_centre = NAN;
    if (count == 0u || count > HR_MAX_CANDIDATES) {
        return;
    }
    hr_sort_double(gaps, count);
    low = hr_percentile_sorted(gaps, count, 0.25);
    high = hr_percentile_sorted(gaps, count, 0.75);

    for (iteration = 0; iteration < 5; ++iteration) {
        double middle;
        size_t split = 0u;

        if (high <= low) {
            break;
        }
        middle = 0.5 * (low + high);
        while (split < count && gaps[split] < middle) {
            ++split;
        }
        if (split == 0u || split == count) {
            break;
        }
        low = hr_median_sorted(gaps, split);
        high = hr_median_sorted(gaps + split, count - split);
    }

    *short_centre = low;
    *long_centre = high;
}

/* ------------------------------------------------------------------------- */
/* Quantiles of the envelope (D10)                                            */
/* ------------------------------------------------------------------------- */

/*
 * Count values into `bins` uniform bins over [lo, hi]; values outside the
 * range land in the edge bins. count must be <= 65535.
 */
static void hr_histogram(
    const float *values,
    size_t count,
    float lo,
    float hi,
    uint16_t *bins)
{
    float width = hi - lo;
    float scale;
    size_t i;

    memset(bins, 0, HR_DETECTOR_HIST_BINS * sizeof(uint16_t));
    if (!(width > 0.0f)) {
        bins[0] = (uint16_t)count;
        return;
    }
    scale = (float)HR_DETECTOR_HIST_BINS / width;
    for (i = 0u; i < count; ++i) {
        float position = (values[i] - lo) * scale;
        unsigned bin;

        if (!(position > 0.0f)) {
            bin = 0u;
        } else if (position >= (float)HR_DETECTOR_HIST_BINS) {
            bin = HR_DETECTOR_HIST_BINS - 1u;
        } else {
            bin = (unsigned)position;
        }
        bins[bin] = (uint16_t)(bins[bin] + 1u);
    }
}

/* Index of the bin that contains the target rank (0-based, `target` in
 * [0, count - 1]). */
static unsigned hr_histogram_bin(const uint16_t *bins, double target)
{
    double cumulative = 0.0;
    unsigned bin;

    for (bin = 0u; bin < HR_DETECTOR_HIST_BINS; ++bin) {
        cumulative += (double)bins[bin];
        if (cumulative > target) {
            return bin;
        }
    }
    return HR_DETECTOR_HIST_BINS - 1u;
}

/*
 * Quantile of the window envelope. Two-level histogram: a coarse pass over
 * [0, max] locates the containing bin, a fine pass inside that bin refines it,
 * so the error is at most max / (2 * BINS^2) = max / 32768. Deterministic, no
 * log/exp, fixed 256 B of scratch. This replaces the 4 s block median of
 * hs_s1s2._block_median; see D10 and the measured deviation in the report.
 */
static float hr_envelope_quantile(
    const float *values,
    size_t count,
    float maximum,
    double quantile)
{
    uint16_t bins[HR_DETECTOR_HIST_BINS];
    double target;
    float coarse_width;
    float lo;
    float hi;
    unsigned bin;

    if (count == 0u || !(maximum > 0.0f)) {
        return 0.0f;
    }
    target = quantile * (double)(count - 1u);

    hr_histogram(values, count, 0.0f, maximum, bins);
    bin = hr_histogram_bin(bins, target);
    coarse_width = maximum / (float)HR_DETECTOR_HIST_BINS;
    lo = (float)bin * coarse_width;
    hi = lo + coarse_width;

    /* Values outside [lo, hi] are clamped into the edge bins, so the rank is
     * preserved and the crossing can be found again in the fine histogram. */
    hr_histogram(values, count, lo, hi, bins);
    bin = hr_histogram_bin(bins, target);

    return lo + ((float)bin + 0.5f)
                * (coarse_width / (float)HR_DETECTOR_HIST_BINS);
}

/* ------------------------------------------------------------------------- */
/* Band-pass and envelope                                                     */
/* ------------------------------------------------------------------------- */

/*
 * Centred symmetric convolution with the band-pass taps over the edge-padded
 * window: out[i] = sum_{d=-64..64} taps[d+64] * x(clamp(i + d - pad)).
 * This is the forward half of hs_features.filtfilt.
 */
static void hr_forward_filter(
    const int16_t *samples,
    size_t n,
    size_t pad,
    double mean,
    float *out,
    size_t out_count)
{
    const float mean_f = (float)mean;
    size_t i;

    for (i = 0u; i < out_count; ++i) {
        float acc = 0.0f;

        if (i >= pad + HR_DETECTOR_FILT_HALF && i + HR_DETECTOR_FILT_HALF < pad + n) {
            const int16_t *src = samples + (i - pad - HR_DETECTOR_FILT_HALF);
            size_t k;

            for (k = 0u; k < HR_DETECTOR_FILT_TAPS; ++k) {
                float centred = (float)src[k] * HR_INV_FULL_SCALE - mean_f;
                acc += hr_detector_bandpass_taps[k] * centred;
            }
        } else {
            int d;

            for (d = -(int)HR_DETECTOR_FILT_HALF;
                 d <= (int)HR_DETECTOR_FILT_HALF;
                 ++d) {
                long k = (long)i + (long)d - (long)pad;
                float centred;

                if (k < 0) {
                    k = 0;
                } else if (k >= (long)n) {
                    k = (long)n - 1;
                }
                centred = (float)samples[k] * HR_INV_FULL_SCALE - mean_f;
                acc += hr_detector_bandpass_taps[(size_t)(d + (int)HR_DETECTOR_FILT_HALF)]
                       * centred;
            }
        }
        out[i] = acc;
    }
}

/* The backward half of filtfilt: the same centred symmetric kernel applied to
 * the forward result (valid because the taps are symmetric). */
static float hr_backward_filter(const float *forward, size_t count, size_t index)
{
    float acc = 0.0f;
    size_t k;

    if (index >= HR_DETECTOR_FILT_HALF && index + HR_DETECTOR_FILT_HALF < count) {
        const float *src = forward + (index - HR_DETECTOR_FILT_HALF);

        for (k = 0u; k < HR_DETECTOR_FILT_TAPS; ++k) {
            acc += hr_detector_bandpass_taps[k] * src[k];
        }
    } else {
        int d;

        for (d = -(int)HR_DETECTOR_FILT_HALF;
             d <= (int)HR_DETECTOR_FILT_HALF;
             ++d) {
            long j = (long)index + (long)d;

            if (j < 0) {
                j = 0;
            } else if (j >= (long)count) {
                j = (long)count - 1;
            }
            acc += hr_detector_bandpass_taps[(size_t)(d + (int)HR_DETECTOR_FILT_HALF)]
                   * forward[j];
        }
    }
    return acc;
}

/*
 * hs_s1s2._moving_rms with mode="same" and zero padding: the energy at index i
 * is the 152-sample average of squares centred on i (fewer samples near the
 * edges, still divided by 152), so envelope[i] = sqrt(max(energy, 0)).
 *
 * `forward` holds the forward-filtered window (count = n + 2 * pad floats) and
 * `envelope` is written at forward + pad (in place). The in-place overlap is
 * safe: the envelope lags the forward pass by half_left = 75 samples, while the
 * backward kernel only reaches back half = 64 samples, so a written slot is
 * never read again.
 */
static void hr_envelope(
    const float *forward,
    size_t count,
    size_t n,
    size_t pad,
    float *envelope)
{
    float ring[HR_DETECTOR_ENVELOPE_SAMPLES];
    const float inverse_window = 1.0f / (float)HR_DETECTOR_ENVELOPE_SAMPLES;
    const size_t half_left = (HR_DETECTOR_ENVELOPE_SAMPLES - 1u) / 2u;  /* 75 */
    const size_t half_right = HR_DETECTOR_ENVELOPE_SAMPLES / 2u;        /* 76 */
    float sum_sq = 0.0f;
    size_t ring_pos = 0u;
    size_t emitted = 0u;
    size_t i;
    long tail_start;

    for (i = 0u; i < n; ++i) {
        float value = hr_backward_filter(forward, count, pad + i);
        float square = value * value;

        sum_sq += square;
        if (i >= HR_DETECTOR_ENVELOPE_SAMPLES) {
            sum_sq -= ring[ring_pos];
        }
        ring[ring_pos] = square;
        ring_pos = (ring_pos + 1u) % HR_DETECTOR_ENVELOPE_SAMPLES;

        if (i >= half_left) {
            float energy = sum_sq * inverse_window;
            size_t index = i - half_left;

            envelope[index] = sqrtf(energy > 0.0f ? energy : 0.0f);
            emitted = index + 1u;
        }
    }

    /*
     * Tail: for the last half_left indices the upper end of the energy window is
     * clipped, so sum_sq only loses samples from the left. sum_sq already covers
     * [max(0, n - 152), n - 1]; each step subtracts the square that leaves the
     * window (index i - half_right - 1), which is still in the ring because the
     * ring holds the newest 152 squares.
     */
    tail_start = (long)n - (long)half_left;
    if (tail_start < (long)emitted) {
        tail_start = (long)emitted;
    }
    if (tail_start < 0) {
        tail_start = 0;
    }
    for (i = (size_t)tail_start; i < n; ++i) {
        long dropped = (long)i - (long)half_right - 1L;
        float energy;

        if (dropped >= 0) {
            sum_sq -= ring[(size_t)dropped % HR_DETECTOR_ENVELOPE_SAMPLES];
        }
        energy = sum_sq * inverse_window;
        envelope[i] = sqrtf(energy > 0.0f ? energy : 0.0f);
    }
}

/* ------------------------------------------------------------------------- */
/* Peak picking and merging                                                   */
/* ------------------------------------------------------------------------- */

static float hr_prominence(const float *envelope, size_t n, size_t index, size_t half)
{
    float minimum = envelope[index];
    size_t low = (index > half) ? index - half : 0u;
    size_t high = index + half + 1u;
    size_t i;

    if (high > n) {
        high = n;
    }
    for (i = low; i < high; ++i) {
        if (envelope[i] < minimum) {
            minimum = envelope[i];
        }
    }
    return envelope[index] - minimum;
}

/* In-place forward compaction, identical to hs_s1s2._merge_close_peaks. */
static void hr_merge_close_peaks(
    size_t *peaks,
    size_t *count,
    const float *envelope,
    size_t min_gap_samples)
{
    int round;

    if (*count < 2u || min_gap_samples == 0u) {
        return;
    }
    for (round = 0; round < 3; ++round) {
        size_t kept_count = 0u;
        size_t read;
        int changed = 0;

        for (read = 0u; read < *count; ++read) {
            size_t peak = peaks[read];

            if (kept_count > 0u && peak - peaks[kept_count - 1u] < min_gap_samples) {
                changed = 1;
                if (envelope[peak] > envelope[peaks[kept_count - 1u]]) {
                    peaks[kept_count - 1u] = peak;
                }
            } else {
                peaks[kept_count++] = peak;
            }
        }
        *count = kept_count;
        if (!changed) {
            break;
        }
    }
}

/* ------------------------------------------------------------------------- */
/* Init / reset                                                               */
/* ------------------------------------------------------------------------- */

static int hr_config_is_valid(const hr_detector_config_t *config)
{
    if (!(config->threshold_ratio > 0.0f)) {
        return 0;
    }
    if (!(config->min_threshold_fraction >= 0.0f)) {
        return 0;
    }
    /* Upper bounds keep the candidate count inside HR_MAX_CANDIDATES. */
    if (!(config->refractory_ms >= 70.0f) || !(config->refractory_ms <= 400.0f)) {
        return 0;
    }
    if (!(config->prominence_window_ms >= 10.0f)
        || !(config->prominence_window_ms <= 500.0f)) {
        return 0;
    }
    if (!(config->prominence_fraction >= 0.0f)
        || !(config->prominence_fraction <= 1.0f)) {
        return 0;
    }
    if (!(config->merge_fraction >= 0.0f) || !(config->merge_min_gap_ms >= 0.0f)) {
        return 0;
    }
    if (config->noise_floor_mode > HR_NOISE_FLOOR_BLEND) {
        return 0;
    }
    if (config->p99_mode > HR_P99_RUNNING_MAX) {
        return 0;
    }
    return 1;
}

int hr_detector_init(
    hr_detector_t *detector,
    float *work,
    size_t work_floats,
    const hr_detector_config_t *config)
{
    if (detector == NULL || work == NULL || work_floats < HR_DETECTOR_WORK_FLOATS) {
        return HR_DETECTOR_STATUS_INVALID_ARGUMENT;
    }
    if (config != NULL && !hr_config_is_valid(config)) {
        return HR_DETECTOR_STATUS_INVALID_ARGUMENT;
    }
    memset(detector, 0, sizeof(*detector));
    detector->work = work;
    detector->work_floats = work_floats;
    detector->config = (config != NULL) ? *config : hr_default_config;
    return HR_DETECTOR_STATUS_OK;
}

void hr_detector_reset(hr_detector_t *detector)
{
    float *work;

    if (detector == NULL) {
        return;
    }
    work = detector->work;
    memset(detector->event_times, 0, sizeof(detector->event_times));
    detector->event_head = 0u;
    detector->event_count = 0u;
    detector->event_total = 0u;
    detector->last_event_time = 0.0;
    detector->have_last_event = 0u;
    detector->have_prev_window = 0u;
    detector->prev_window_median = 0.0f;
    detector->running_p99 = 0.0f;
    detector->next_window_start = 0u;
    detector->windows = 0u;
    detector->peaks_total = 0u;
    detector->duplicates = 0u;
    detector->work = work;
}

/* ------------------------------------------------------------------------- */
/* Push                                                                       */
/* ------------------------------------------------------------------------- */

static void hr_append_event(hr_detector_t *detector, double time)
{
    uint16_t slot;

    if (detector->event_count == HR_DETECTOR_EVENT_HISTORY) {
        detector->event_head =
            (uint16_t)((detector->event_head + 1u) % HR_DETECTOR_EVENT_HISTORY);
        --detector->event_count;
    }
    slot = (uint16_t)((detector->event_head + detector->event_count)
                      % HR_DETECTOR_EVENT_HISTORY);
    detector->event_times[slot] = time;
    ++detector->event_count;
    ++detector->event_total;
}

/* Is `index` the maximum of the envelope over +/- half, with the leftmost
 * element of a plateau winning? O(1) for the common non-peak case. */
static int hr_is_local_maximum(
    const float *envelope,
    size_t n,
    size_t index,
    size_t half)
{
    float value = envelope[index];
    size_t low;
    size_t high;
    size_t i;

    if (index > 0u && envelope[index - 1u] >= value) {
        return 0;
    }
    if (index + 1u < n && envelope[index + 1u] > value) {
        return 0;
    }
    low = (index > half) ? index - half : 0u;
    high = (index + half + 1u < n) ? index + half + 1u : n;
    for (i = low; i < high; ++i) {
        if (envelope[i] > value) {
            return 0;
        }
    }
    return 1;
}

int hr_detector_push_hop(
    hr_detector_t *detector,
    const int16_t *samples,
    size_t n,
    size_t hop)
{
    const hr_detector_config_t *config;
    float *work;
    float *envelope;
    size_t pad;
    size_t count;
    size_t i;
    double sum = 0.0;
    double sum_sq = 0.0;
    double mean;
    double variance;
    float maximum;
    float raw_median;
    float noise_floor;
    float p99;
    float threshold;
    size_t candidates[HR_MAX_CANDIDATES];
    size_t candidate_count = 0u;
    size_t accepted[HR_MAX_CANDIDATES];
    size_t accepted_count = 0u;
    size_t candidate_half;
    size_t refractory_samples;
    size_t prominence_half;
    size_t first_new;
    uint64_t window_start;
    double guard_s;
    int new_events = 0;

    if (detector == NULL || samples == NULL || detector->work == NULL) {
        return HR_DETECTOR_STATUS_INVALID_ARGUMENT;
    }
    if (n == 0u || n > HR_DETECTOR_WINDOW_SAMPLES) {
        return HR_DETECTOR_STATUS_INVALID_ARGUMENT;
    }

    config = &detector->config;
    work = detector->work;
    window_start = detector->next_window_start;
    detector->next_window_start = window_start + (uint64_t)hop;
    ++detector->windows;

    pad = 3u * (size_t)HR_DETECTOR_FILT_TAPS;
    if (pad > n - 1u) {
        pad = n - 1u;
    }
    count = n + 2u * pad;

    for (i = 0u; i < n; ++i) {
        double value = (double)samples[i] * (double)HR_INV_FULL_SCALE;

        sum += value;
        sum_sq += value * value;
    }
    mean = sum / (double)n;
    variance = sum_sq / (double)n - mean * mean;
    if (!(variance > HR_MIN_SIGNAL_STD * HR_MIN_SIGNAL_STD)) {
        return 0;
    }

    hr_forward_filter(samples, n, pad, mean, work, count);
    envelope = work + pad;
    hr_envelope(work, count, n, pad, envelope);

    maximum = 0.0f;
    for (i = 0u; i < n; ++i) {
        if (envelope[i] > maximum) {
            maximum = envelope[i];
        }
    }
    if (!(maximum > 0.0f)) {
        return 0;
    }

    raw_median = hr_envelope_quantile(envelope, n, maximum, 0.5);
    p99 = hr_envelope_quantile(envelope, n, maximum, 0.99);

    /* D10: the 4 s block median of hs_s1s2 cannot be reproduced inside a 2 s
     * window. BLEND mode averages the two newest overlapping windows, i.e. a
     * 3 s span, as the closest causal approximation. */
    noise_floor = raw_median;
    if (config->noise_floor_mode == HR_NOISE_FLOOR_BLEND
        && detector->have_prev_window) {
        noise_floor = 0.5f * (raw_median + detector->prev_window_median);
    }
    detector->prev_window_median = raw_median;
    detector->have_prev_window = 1u;

    if (config->p99_mode == HR_P99_RUNNING_MAX) {
        if (p99 > detector->running_p99) {
            detector->running_p99 = p99;
        }
        p99 = detector->running_p99;
    }

    threshold = config->threshold_ratio * noise_floor;
    {
        float floor_value = config->min_threshold_fraction * p99;

        if (floor_value > threshold) {
            threshold = floor_value;
        }
    }

    refractory_samples = (size_t)(config->refractory_ms
                                  * (float)HR_DETECTOR_SAMPLE_RATE_HZ / 1000.0f
                                  + 0.5f);
    candidate_half = refractory_samples / 2u;
    prominence_half = (size_t)(config->prominence_window_ms
                               * (float)HR_DETECTOR_SAMPLE_RATE_HZ / 1000.0f + 0.5f);
    if (prominence_half < 1u) {
        prominence_half = 1u;
    }

    /* Candidate peaks: local maxima above the threshold. hs_s1s2 iterates over
     * every sample above threshold; the steepest-first pass below only ever
     * accepts the maximum of an unblocked region, and that is a local maximum,
     * so the candidate list is equivalent apart from window boundaries. */
    for (i = 0u; i < n; ++i) {
        if (!(envelope[i] > threshold)) {
            continue;
        }
        if (!hr_is_local_maximum(envelope, n, i, candidate_half)) {
            continue;
        }
        if (candidate_count < HR_MAX_CANDIDATES) {
            candidates[candidate_count++] = i;
        }
    }

    /* Steepest-first non-maximum suppression with the refractory period, then
     * the prominence test (hs_s1s2._select_peaks). Sorting by envelope value is
     * a stable insertion sort on <= 64 entries. */
    for (i = 1u; i < candidate_count; ++i) {
        size_t index = candidates[i];
        float amp = envelope[index];
        size_t j = i;

        while (j > 0u && envelope[candidates[j - 1u]] < amp) {
            candidates[j] = candidates[j - 1u];
            --j;
        }
        candidates[j] = index;
    }
    for (i = 0u; i < candidate_count; ++i) {
        size_t index = candidates[i];
        size_t a;
        int blocked = 0;

        for (a = 0u; a < accepted_count; ++a) {
            size_t other = accepted[a];
            size_t distance = (index > other) ? (index - other) : (other - index);

            if (distance < refractory_samples) {
                blocked = 1;
                break;
            }
        }
        if (blocked) {
            continue;
        }
        if (hr_prominence(envelope, n, index, prominence_half)
            < config->prominence_fraction * envelope[index]) {
            continue;
        }
        accepted[accepted_count++] = index;
    }

    /* Ascending order, as in _select_peaks. */
    for (i = 1u; i < accepted_count; ++i) {
        size_t value = accepted[i];
        size_t j = i;

        while (j > 0u && accepted[j - 1u] > value) {
            accepted[j] = accepted[j - 1u];
            --j;
        }
        accepted[j] = value;
    }

    /* Merge spurious peaks inside one heart sound. */
    if ((config->merge_fraction > 0.0f || config->merge_min_gap_ms > 0.0f)
        && accepted_count >= 3u) {
        double gaps[HR_MAX_CANDIDATES];
        double short_centre;
        double long_centre;
        size_t gap_count = accepted_count - 1u;

        for (i = 0u; i < gap_count; ++i) {
            gaps[i] = (double)(accepted[i + 1u] - accepted[i])
                      / (double)HR_DETECTOR_SAMPLE_RATE_HZ;
        }
        hr_two_gap_centres(gaps, gap_count, &short_centre, &long_centre);
        (void)long_centre;
        if (isfinite(short_centre) && short_centre > 0.0) {
            double merge_gap_s = (double)config->merge_min_gap_ms / 1000.0;

            if ((double)config->merge_fraction * short_centre > merge_gap_s) {
                merge_gap_s = (double)config->merge_fraction * short_centre;
            }
            hr_merge_close_peaks(
                accepted,
                &accepted_count,
                envelope,
                (size_t)(merge_gap_s * (double)HR_DETECTOR_SAMPLE_RATE_HZ));
        }
    }

    /*
     * D11 de-duplication. (a) Only the samples the previous window did not
     * cover may nominate an event; the first push has no previous window and
     * nominates everything. (b) An accepted event must be later than the last
     * accepted event plus the merge floor.
     */
    first_new = 0u;
    if (detector->windows > 1u && hop < n) {
        first_new = n - hop;
    }
    guard_s = (double)config->merge_min_gap_ms / 1000.0;

    detector->peaks_total += (uint32_t)accepted_count;
    for (i = 0u; i < accepted_count; ++i) {
        size_t index = accepted[i];
        double time;

        if (index < first_new) {
            ++detector->duplicates;
            continue;
        }
        time = (double)(window_start + (uint64_t)index)
               / (double)HR_DETECTOR_SAMPLE_RATE_HZ;
        if (detector->have_last_event
            && !(time > detector->last_event_time + guard_s)) {
            ++detector->duplicates;
            continue;
        }
        hr_append_event(detector, time);
        detector->last_event_time = time;
        detector->have_last_event = 1u;
        ++new_events;
    }
    return new_events;
}

int hr_detector_push(hr_detector_t *detector, const int16_t *samples, size_t n)
{
    return hr_detector_push_hop(detector, samples, n, n / 2u);
}

/* ------------------------------------------------------------------------- */
/* Heart rate                                                                 */
/* ------------------------------------------------------------------------- */

/*
 * hs_s1s2.estimate_heart_rate: 60 / median of consecutive gap-pair sums.
 * Pairs are formed from the oldest gap, so pairs = g[0]+g[1], g[2]+g[3], ...
 * and an odd trailing gap is dropped.
 */
static double hr_estimate_from_times(
    const double *times,
    size_t count,
    double *pair_scratch,
    size_t pair_capacity,
    double *cycle_out)
{
    size_t gap_count;
    size_t pair_count;
    size_t i;
    double rr;

    if (cycle_out != NULL) {
        *cycle_out = NAN;
    }
    if (times == NULL || count < 3u || pair_capacity == 0u) {
        return NAN;
    }
    gap_count = count - 1u;
    pair_count = gap_count / 2u;
    if (pair_count > pair_capacity) {
        pair_count = pair_capacity;
    }
    if (pair_count == 0u) {
        return NAN;
    }
    for (i = 0u; i < pair_count; ++i) {
        pair_scratch[i] = times[2u * i + 2u] - times[2u * i];
    }
    hr_sort_double(pair_scratch, pair_count);
    rr = hr_median_sorted(pair_scratch, pair_count);
    if (!(rr > 0.0) || !isfinite(rr)) {
        return NAN;
    }
    if (cycle_out != NULL) {
        *cycle_out = rr;
    }
    return 60.0 / rr;
}

double hr_heart_rate_from_events(
    const double *event_times,
    size_t count,
    double *cycle_out)
{
    double pairs[HR_MAX_EVENT_INPUT / 2u];

    if (count > HR_MAX_EVENT_INPUT) {
        count = HR_MAX_EVENT_INPUT;
    }
    return hr_estimate_from_times(
        event_times,
        count,
        pairs,
        HR_MAX_EVENT_INPUT / 2u,
        cycle_out);
}

int hr_detector_estimate(const hr_detector_t *detector, hr_detector_estimate_t *out)
{
    double times[HR_DETECTOR_EVENT_HISTORY];
    double pairs[HR_DETECTOR_EVENT_HISTORY / 2u];
    double cycle = NAN;
    double bpm;
    size_t i;

    if (detector == NULL || out == NULL) {
        return HR_DETECTOR_STATUS_INVALID_ARGUMENT;
    }
    memset(out, 0, sizeof(*out));
    out->n_events = detector->event_count;
    out->events_total = detector->event_total;
    out->windows = detector->windows;
    out->duplicates = detector->duplicates;
    out->peaks_total = detector->peaks_total;
    out->bpm = (float)NAN;
    out->median_cycle_s = (float)NAN;
    out->valid = 0u;

    for (i = 0u; i < detector->event_count; ++i) {
        times[i] = detector->event_times[(detector->event_head + i)
                                         % HR_DETECTOR_EVENT_HISTORY];
    }
    bpm = hr_estimate_from_times(
        times,
        detector->event_count,
        pairs,
        HR_DETECTOR_EVENT_HISTORY / 2u,
        &cycle);
    if (!isfinite(bpm) || !isfinite(cycle)) {
        return HR_DETECTOR_STATUS_OK;
    }
    out->bpm = (float)bpm;
    out->median_cycle_s = (float)cycle;
    out->valid = (uint8_t)(
        detector->event_count >= HR_DETECTOR_MIN_EVENTS
        && bpm >= (double)HR_DETECTOR_BPM_MIN
        && bpm <= (double)HR_DETECTOR_BPM_MAX);
    return HR_DETECTOR_STATUS_OK;
}

size_t hr_detector_get_events(
    const hr_detector_t *detector,
    double *out,
    size_t capacity)
{
    size_t i;

    if (detector == NULL || out == NULL) {
        return 0u;
    }
    for (i = 0u; i < detector->event_count && i < capacity; ++i) {
        out[i] = detector->event_times[(detector->event_head + i)
                                       % HR_DETECTOR_EVENT_HISTORY];
    }
    return i;
}

double hr_detector_last_event_time(const hr_detector_t *detector, int *have)
{
    if (have != NULL) {
        *have = 0;
    }
    if (detector == NULL) {
        return 0.0;
    }
    if (have != NULL) {
        *have = (int)detector->have_last_event;
    }
    return detector->last_event_time;
}
