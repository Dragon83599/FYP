/*
 * Tests for the portable heart-rate detector (shared/src/hr_detector.c).
 *
 * Two modes, one binary:
 *
 *   test_hr_detector
 *       Synthetic unit suite (also the ctest entry): filter coefficients and
 *       response, envelope/threshold behaviour, peak picking on synthetic PCG,
 *       heart rate, the validity gate, determinism, and the overlapping-window
 *       de-duplication that the 2 s / 1 s firmware stepping requires.
 *
 *   test_hr_detector --fixture <csv> [--limit N] [--sweep] [--timing]
 *       Real-data agreement run. Reads the CSV written by
 *       tools/ai/dump_hr_fixture.py, streams every recording through the C
 *       detector with the firmware's 2 s / 1 s windows, applies the same
 *       annotation mask as tools/ai/evaluate_heart_rate.py, and reports
 *       C-vs-Python and C-vs-reference heart-rate agreement.
 *
 * Plain C, no framework: failures are counted and main() returns non-zero.
 * The test may use malloc for WAV/CSV buffers; the library itself never does.
 */

#include "hr_detector.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define TEST_PI 3.14159265358979323846

#define TEST_FIXTURE_CAPACITY 4096u
#define TEST_MAX_INTERVALS 512u

static int failures = 0;

#define CHECK(condition)                                                       \
    do {                                                                       \
        if (!(condition)) {                                                    \
            ++failures;                                                        \
            fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #condition); \
        }                                                                      \
    } while (0)

/* ------------------------------------------------------------------------- */
/* Small helpers                                                              */
/* ------------------------------------------------------------------------- */

static double test_median_sorted(const double *values, size_t count)
{
    if (count == 0u) {
        return NAN;
    }
    if ((count % 2u) == 1u) {
        return values[count / 2u];
    }
    return 0.5 * (values[count / 2u - 1u] + values[count / 2u]);
}

/* Deterministic noise (no rand(), so the suite is reproducible everywhere). */
static double test_uniform(uint32_t *state)
{
    *state = *state * 1664525u + 1013904223u;
    return (double)(*state >> 8) / 8388608.0 - 1.0;
}

static void test_add_burst(
    int16_t *signal,
    size_t count,
    double sample_rate,
    double centre_s,
    double duration_s,
    double frequency_hz,
    double amplitude)
{
    size_t half = (size_t)(0.5 * duration_s * sample_rate);
    long centre = (long)(centre_s * sample_rate);
    long start = centre - (long)half;
    long end = centre + (long)half;
    long i;

    for (i = start; i <= end; ++i) {
        double offset;
        double window;
        double value;

        if (i < 0 || i >= (long)count) {
            continue;
        }
        offset = (double)(i - start) / (double)(end - start);
        window = 0.5 - 0.5 * cos(2.0 * TEST_PI * offset);
        value = amplitude * window
                * sin(2.0 * TEST_PI * frequency_hz * (double)i / sample_rate);
        value += (double)signal[i];
        if (value > 32767.0) {
            value = 32767.0;
        }
        if (value < -32768.0) {
            value = -32768.0;
        }
        signal[i] = (int16_t)lround(value);
    }
}

/*
 * Synthetic PCG: one S1 (41 Hz) and one S2 (62 Hz) burst per cardiac cycle, plus
 * deterministic broadband noise and a DC offset, so the whole chain (mean
 * removal, band-pass, envelope, threshold, peak picking) is exercised.
 */
static void test_synth_pcg(
    int16_t *signal,
    size_t count,
    double sample_rate,
    double bpm,
    double s1_s2_s,
    double amplitude,
    double noise)
{
    double cycle = 60.0 / bpm;
    double duration = (double)count / sample_rate;
    double time = 0.30;
    uint32_t state = 20261018u;
    size_t i;

    memset(signal, 0, count * sizeof(int16_t));
    for (i = 0u; i < count; ++i) {
        signal[i] = (int16_t)lround(test_uniform(&state) * noise);
    }
    while (time + s1_s2_s < duration) {
        test_add_burst(signal, count, sample_rate, time, 0.075, 41.0, amplitude);
        test_add_burst(signal, count, sample_rate, time + s1_s2_s, 0.055, 62.0,
                       amplitude * 0.62);
        time += cycle;
    }
    for (i = 0u; i < count; ++i) {
        long value = (long)signal[i] + 400;
        if (value > 32767) {
            value = 32767;
        }
        if (value < -32768) {
            value = -32768;
        }
        signal[i] = (int16_t)value;
    }
}

/* Collect the events currently in the detector's history. */
static void test_collect(
    const hr_detector_t *detector,
    double *events,
    size_t capacity,
    size_t *count,
    double *last_time)
{
    double buffer[HR_DETECTOR_EVENT_HISTORY];
    size_t available = hr_detector_get_events(detector, buffer, HR_DETECTOR_EVENT_HISTORY);
    size_t i;

    for (i = 0u; i < available; ++i) {
        if (buffer[i] > *last_time + 1e-12) {
            if (*count < capacity) {
                events[*count] = buffer[i];
                ++(*count);
            }
            *last_time = buffer[i];
        }
    }
}

/*
 * Stream one recording through the detector the way the firmware does: 2 s
 * windows with an explicit hop, plus a final window aligned to the end so the
 * tail is not lost. n < 2 s pushes a single short window.
 */
static void test_stream_recording(
    const int16_t *signal,
    size_t n,
    size_t hop,
    const hr_detector_config_t *config,
    double *events,
    size_t events_capacity,
    size_t *event_count,
    hr_detector_estimate_t *estimate,
    double *push_seconds)
{
    static float work[HR_DETECTOR_WORK_FLOATS];
    hr_detector_t detector;
    size_t start = 0u;
    size_t last_start = 0u;
    double last_time = -1.0;

    *event_count = 0u;
    memset(estimate, 0, sizeof(*estimate));
    if (push_seconds != NULL) {
        *push_seconds = 0.0;
    }
    if (hr_detector_init(&detector, work, HR_DETECTOR_WORK_FLOATS, config)
        != HR_DETECTOR_STATUS_OK) {
        CHECK(0);
        return;
    }
    if (n < HR_DETECTOR_WINDOW_SAMPLES) {
        clock_t begin = clock();
        hr_detector_push(&detector, signal, n);
        if (push_seconds != NULL) {
            *push_seconds += (double)(clock() - begin) / (double)CLOCKS_PER_SEC;
        }
        test_collect(&detector, events, events_capacity, event_count, &last_time);
    } else {
        while (start + HR_DETECTOR_WINDOW_SAMPLES <= n) {
            clock_t begin = clock();
            hr_detector_push_hop(
                &detector, signal + start, HR_DETECTOR_WINDOW_SAMPLES, hop);
            if (push_seconds != NULL) {
                *push_seconds += (double)(clock() - begin) / (double)CLOCKS_PER_SEC;
            }
            test_collect(&detector, events, events_capacity, event_count, &last_time);
            last_start = start;
            start += hop;
        }
        if (last_start + HR_DETECTOR_WINDOW_SAMPLES < n) {
            size_t tail = n - HR_DETECTOR_WINDOW_SAMPLES;
            clock_t begin = clock();
            hr_detector_push_hop(
                &detector,
                signal + tail,
                HR_DETECTOR_WINDOW_SAMPLES,
                tail - last_start);
            if (push_seconds != NULL) {
                *push_seconds += (double)(clock() - begin) / (double)CLOCKS_PER_SEC;
            }
            test_collect(&detector, events, events_capacity, event_count, &last_time);
        }
    }
    hr_detector_estimate(&detector, estimate);
}

/* ------------------------------------------------------------------------- */
/* Band-pass design and response                                              */
/* ------------------------------------------------------------------------- */

static double test_sinc(double value)
{
    if (fabs(value) < 1e-12) {
        return 1.0;
    }
    return sin(TEST_PI * value) / (TEST_PI * value);
}

/* hs_features.design_bandpass_fir, re-derived in double precision. */
static void test_design_taps(double *taps, int num_taps, double sample_rate,
                             double low_hz, double high_hz)
{
    int n;
    double centre = (double)(num_taps - 1) / 2.0;
    double response_re = 0.0;
    double response_im = 0.0;
    double magnitude;
    double centre_hz = sqrt(low_hz * high_hz);

    for (n = 0; n < num_taps; ++n) {
        double normalised_low = low_hz / sample_rate;
        double normalised_high = high_hz / sample_rate;
        double offset = (double)n - centre;

        taps[n] = 2.0 * normalised_high * test_sinc(2.0 * normalised_high * offset)
                  - 2.0 * normalised_low * test_sinc(2.0 * normalised_low * offset);
        taps[n] *= 0.54 - 0.46 * cos(2.0 * TEST_PI * (double)n / (double)(num_taps - 1));
    }
    for (n = 0; n < num_taps; ++n) {
        double phase = -2.0 * TEST_PI * centre_hz * (double)n / sample_rate;

        response_re += taps[n] * cos(phase);
        response_im += taps[n] * sin(phase);
    }
    magnitude = sqrt(response_re * response_re + response_im * response_im);
    if (magnitude < 1e-8) {
        magnitude = 1e-8;
    }
    for (n = 0; n < num_taps; ++n) {
        taps[n] /= magnitude;
    }
}

static double test_response_at(const float *taps, size_t count, double hz,
                               double sample_rate)
{
    double re = 0.0;
    double im = 0.0;
    size_t n;

    for (n = 0u; n < count; ++n) {
        double phase = -2.0 * TEST_PI * hz * (double)n / sample_rate;

        re += (double)taps[n] * cos(phase);
        im += (double)taps[n] * sin(phase);
    }
    return sqrt(re * re + im * im);
}

static void test_bandpass_table(void)
{
    double reference[HR_DETECTOR_FILT_TAPS];
    double worst = 0.0;
    size_t i;

    test_design_taps(reference, (int)HR_DETECTOR_FILT_TAPS, 4000.0, 20.0, 85.0);
    for (i = 0u; i < HR_DETECTOR_FILT_TAPS; ++i) {
        double difference =
            fabs(reference[i] - (double)hr_detector_bandpass_taps[i]);
        if (difference > worst) {
            worst = difference;
        }
        /* The table is symmetric (linear phase). */
        CHECK(fabs((double)hr_detector_bandpass_taps[i]
                   - (double)hr_detector_bandpass_taps[HR_DETECTOR_FILT_TAPS - 1u - i])
              < 1e-9);
    }
    printf("bandpass table: max |c - python design| = %.3g\n", worst);
    CHECK(worst < 1e-6);

    {
        double pass = test_response_at(
            hr_detector_bandpass_taps, HR_DETECTOR_FILT_TAPS, 41.23, 4000.0);
        double low = test_response_at(
            hr_detector_bandpass_taps, HR_DETECTOR_FILT_TAPS, 20.0, 4000.0);
        double high = test_response_at(
            hr_detector_bandpass_taps, HR_DETECTOR_FILT_TAPS, 85.0, 4000.0);
        double stop120 = test_response_at(
            hr_detector_bandpass_taps, HR_DETECTOR_FILT_TAPS, 120.0, 4000.0);
        double stop300 = test_response_at(
            hr_detector_bandpass_taps, HR_DETECTOR_FILT_TAPS, 300.0, 4000.0);

        printf("bandpass response: |H| 20Hz=%.3f 41.2Hz=%.4f 85Hz=%.3f "
               "120Hz=%.3f 300Hz=%.5f\n",
               low, pass, high, stop120, stop300);
        CHECK(pass > 0.95 && pass < 1.05);
        CHECK(low > 0.55);
        CHECK(high > 0.55);
        CHECK(stop120 < 0.10);
        CHECK(stop300 < 0.01);
    }
}

/* ------------------------------------------------------------------------- */
/* Unit tests                                                                 */
/* ------------------------------------------------------------------------- */

static void test_heart_rate_estimator(void)
{
    double cycle = 0.0;

    {
        /* 0.35 + 0.65 s pairs => one cycle of 1.0 s. */
        const double times[5] = {0.0, 0.35, 1.0, 1.35, 2.0};
        double bpm = hr_heart_rate_from_events(times, 5u, &cycle);

        CHECK(fabs(bpm - 60.0) < 1e-9);
        CHECK(fabs(cycle - 1.0) < 1e-9);
    }
    {
        /* Odd trailing gap is dropped, exactly like hs_s1s2.estimate_heart_rate. */
        const double times[6] = {0.0, 0.3, 1.0, 1.3, 2.0, 2.9};
        double bpm = hr_heart_rate_from_events(times, 6u, &cycle);

        CHECK(fabs(bpm - 60.0) < 1e-9);
    }
    {
        const double times[2] = {0.0, 1.0};

        CHECK(isnan(hr_heart_rate_from_events(times, 2u, &cycle)));
        CHECK(hr_heart_rate_from_events(times, 0u, &cycle) != 0.0
              && isnan(hr_heart_rate_from_events(times, 0u, &cycle)));
    }
    {
        /* 72 bpm: 0.30 + 0.5333 s pairs. */
        const double times[4] = {0.0, 0.30, 0.8333333333, 1.1333333333};
        double bpm = hr_heart_rate_from_events(times, 4u, &cycle);

        CHECK(fabs(bpm - 72.0) < 1e-6);
    }
}

static void test_synthetic_heart_rate(void)
{
    static int16_t signal[10u * HR_DETECTOR_SAMPLE_RATE_HZ];
    static double events[TEST_FIXTURE_CAPACITY];
    hr_detector_estimate_t estimate;
    size_t count = 0u;
    size_t samples = (size_t)(10u * HR_DETECTOR_SAMPLE_RATE_HZ);

    test_synth_pcg(signal, samples, 4000.0, 72.0, 0.30, 6000.0, 120.0);
    test_stream_recording(
        signal, samples, HR_DETECTOR_SAMPLE_RATE_HZ / 2u, NULL, events,
        TEST_FIXTURE_CAPACITY, &count, &estimate, NULL);

    printf("synthetic 72 bpm: %u events, bpm=%.2f valid=%u, duplicates=%u\n",
           (unsigned)count, (double)estimate.bpm, (unsigned)estimate.valid,
           (unsigned)estimate.duplicates);
    /* 10 s at 72 bpm = 12 cycles = 24 events; allow the first/last cycle loss. */
    CHECK(count >= 20u && count <= 26u);
    CHECK(fabs((double)estimate.bpm - 72.0) <= 2.0);
    CHECK(estimate.valid == 1u);
    CHECK(estimate.median_cycle_s > 0.80f && estimate.median_cycle_s < 0.86f);
}

static void test_overlapping_windows_dedup(void)
{
    static int16_t signal[10u * HR_DETECTOR_SAMPLE_RATE_HZ];
    static double events_overlap[TEST_FIXTURE_CAPACITY];
    static double events_plain[TEST_FIXTURE_CAPACITY];
    hr_detector_estimate_t overlap_estimate;
    hr_detector_estimate_t plain_estimate;
    size_t overlap_count = 0u;
    size_t plain_count = 0u;
    size_t samples = (size_t)(10u * HR_DETECTOR_SAMPLE_RATE_HZ);
    size_t i;
    double smallest_gap = 1e9;

    test_synth_pcg(signal, samples, 4000.0, 72.0, 0.30, 6000.0, 120.0);
    test_stream_recording(
        signal, samples, HR_DETECTOR_SAMPLE_RATE_HZ / 2u, NULL, events_overlap,
        TEST_FIXTURE_CAPACITY, &overlap_count, &overlap_estimate, NULL);
    test_stream_recording(
        signal, samples, HR_DETECTOR_WINDOW_SAMPLES, NULL, events_plain,
        TEST_FIXTURE_CAPACITY, &plain_count, &plain_estimate, NULL);

    for (i = 1u; i < overlap_count; ++i) {
        double gap = events_overlap[i] - events_overlap[i - 1u];

        if (gap < smallest_gap) {
            smallest_gap = gap;
        }
        CHECK(events_overlap[i] > events_overlap[i - 1u]);
    }

    printf("overlap dedup: hop=1s -> %u events (bpm %.2f, %u of %u window "
           "peaks suppressed as duplicates, smallest gap %.3fs), hop=2s -> "
           "%u events (bpm %.2f)\n",
           (unsigned)overlap_count, (double)overlap_estimate.bpm,
           (unsigned)overlap_estimate.duplicates,
           (unsigned)overlap_estimate.peaks_total, smallest_gap,
           (unsigned)plain_count, (double)plain_estimate.bpm);

    /* Overlap must not double the rate: the rate is the same as the
     * non-overlapping stream, within the one-cycle quantisation of the tail. */
    CHECK(fabs((double)overlap_estimate.bpm - (double)plain_estimate.bpm) <= 2.0);
    CHECK(overlap_count <= plain_count + 4u);
    CHECK(overlap_count + 4u >= plain_count);
    /* Every event is separated by at least the merge floor. */
    CHECK(smallest_gap >= 0.12 - 1e-6);
    /* The guard actually fired: the 1 s overlap re-detected events. Without
     * it the event list would hold peaks_total entries instead of
     * overlap_count, every event would appear twice (gap ~0 s), and the pair
     * sums would collapse to one gap, i.e. the rate would roughly double. */
    CHECK(overlap_estimate.duplicates > 0u);
    CHECK(overlap_estimate.peaks_total
          == overlap_estimate.events_total + overlap_estimate.duplicates);
    CHECK(overlap_estimate.peaks_total > overlap_count + 10u);
    CHECK(overlap_estimate.valid == 1u && plain_estimate.valid == 1u);
}

static void test_same_window_twice(void)
{
    static int16_t signal[4u * HR_DETECTOR_SAMPLE_RATE_HZ];
    static float work[HR_DETECTOR_WORK_FLOATS];
    hr_detector_t detector;
    hr_detector_estimate_t estimate;
    size_t samples = (size_t)(4u * HR_DETECTOR_SAMPLE_RATE_HZ);
    int first;
    int second;

    test_synth_pcg(signal, samples, 4000.0, 72.0, 0.30, 6000.0, 120.0);
    CHECK(hr_detector_init(&detector, work, HR_DETECTOR_WORK_FLOATS, NULL)
          == HR_DETECTOR_STATUS_OK);
    first = hr_detector_push_hop(&detector, signal, HR_DETECTOR_WINDOW_SAMPLES, 0u);
    second = hr_detector_push_hop(&detector, signal, HR_DETECTOR_WINDOW_SAMPLES, 0u);
    hr_detector_estimate(&detector, &estimate);
    printf("same window twice: first=%d events, second=%d new events, "
           "%u duplicates\n",
           first, second, (unsigned)estimate.duplicates);
    CHECK(first > 0);
    CHECK(second == 0);
    CHECK(estimate.duplicates > 0u);
    CHECK(estimate.events_total == (uint32_t)first);
}

static void test_validity_gate(void)
{
    static int16_t signal[12u * HR_DETECTOR_SAMPLE_RATE_HZ];
    static double events[TEST_FIXTURE_CAPACITY];
    hr_detector_estimate_t estimate;
    size_t count = 0u;
    size_t samples = (size_t)(12u * HR_DETECTOR_SAMPLE_RATE_HZ);
    double time;

    /* 230 bpm (one burst every 130 ms) is above the 30-220 display gate. */
    memset(signal, 0, samples * sizeof(int16_t));
    for (time = 0.4; time < 11.0; time += 0.130) {
        test_add_burst(signal, samples, 4000.0, time, 0.060, 45.0, 9000.0);
    }
    test_stream_recording(signal, samples, HR_DETECTOR_SAMPLE_RATE_HZ / 2u, NULL,
                          events, TEST_FIXTURE_CAPACITY, &count, &estimate, NULL);
    printf("230 bpm case: %u events, bpm=%.1f valid=%u\n", (unsigned)count,
           (double)estimate.bpm, (unsigned)estimate.valid);
    CHECK(estimate.bpm > 220.0f);
    CHECK(estimate.valid == 0u);

    /* 24 bpm is below the gate. */
    test_synth_pcg(signal, samples, 4000.0, 24.0, 0.40, 6000.0, 120.0);
    test_stream_recording(signal, samples, HR_DETECTOR_SAMPLE_RATE_HZ / 2u, NULL,
                          events, TEST_FIXTURE_CAPACITY, &count, &estimate, NULL);
    printf("24 bpm case: %u events, bpm=%.1f valid=%u\n", (unsigned)count,
           (double)estimate.bpm, (unsigned)estimate.valid);
    CHECK(estimate.bpm < 30.0f);
    CHECK(estimate.valid == 0u);

    /* Too few events is invalid even when the rate is in range. */
    memset(signal, 0, samples * sizeof(int16_t));
    test_add_burst(signal, samples, 4000.0, 0.50, 0.070, 45.0, 9000.0);
    test_add_burst(signal, samples, 4000.0, 0.85, 0.060, 62.0, 7000.0);
    test_stream_recording(signal, samples, HR_DETECTOR_SAMPLE_RATE_HZ / 2u, NULL,
                          events, TEST_FIXTURE_CAPACITY, &count, &estimate, NULL);
    printf("two events case: %u events, bpm=%.1f valid=%u\n", (unsigned)count,
           (double)estimate.bpm, (unsigned)estimate.valid);
    CHECK(count <= 2u);
    CHECK(estimate.valid == 0u);
}

static void test_silence_and_constant(void)
{
    static int16_t signal[HR_DETECTOR_WINDOW_SAMPLES];
    static double events[64];
    hr_detector_estimate_t estimate;
    size_t count = 0u;
    size_t i;

    memset(signal, 0, sizeof(signal));
    test_stream_recording(signal, HR_DETECTOR_WINDOW_SAMPLES,
                          HR_DETECTOR_SAMPLE_RATE_HZ / 2u, NULL, events, 64u,
                          &count, &estimate, NULL);
    CHECK(count == 0u);
    CHECK(estimate.valid == 0u);

    for (i = 0u; i < HR_DETECTOR_WINDOW_SAMPLES; ++i) {
        signal[i] = 1234;
    }
    test_stream_recording(signal, HR_DETECTOR_WINDOW_SAMPLES,
                          HR_DETECTOR_SAMPLE_RATE_HZ / 2u, NULL, events, 64u,
                          &count, &estimate, NULL);
    CHECK(count == 0u);
    CHECK(estimate.valid == 0u);
}

static void test_event_history_cap(void)
{
    static int16_t signal[40u * HR_DETECTOR_SAMPLE_RATE_HZ];
    static double events[TEST_FIXTURE_CAPACITY];
    hr_detector_estimate_t estimate;
    size_t count = 0u;
    size_t samples = (size_t)(40u * HR_DETECTOR_SAMPLE_RATE_HZ);

    test_synth_pcg(signal, samples, 4000.0, 72.0, 0.30, 6000.0, 120.0);
    test_stream_recording(signal, samples, HR_DETECTOR_SAMPLE_RATE_HZ / 2u, NULL,
                          events, TEST_FIXTURE_CAPACITY, &count, &estimate, NULL);
    printf("long recording: %u events accepted, history=%u, bpm=%.2f\n",
           (unsigned)count, (unsigned)estimate.n_events, (double)estimate.bpm);
    CHECK(count > 40u);
    CHECK(estimate.n_events == HR_DETECTOR_EVENT_HISTORY);
    CHECK(estimate.events_total > HR_DETECTOR_EVENT_HISTORY);
    CHECK(fabs((double)estimate.bpm - 72.0) <= 2.0);
}

static void test_determinism(void)
{
    static int16_t signal[8u * HR_DETECTOR_SAMPLE_RATE_HZ];
    static double first_events[TEST_FIXTURE_CAPACITY];
    static double second_events[TEST_FIXTURE_CAPACITY];
    hr_detector_estimate_t first_estimate;
    hr_detector_estimate_t second_estimate;
    size_t first_count = 0u;
    size_t second_count = 0u;
    size_t i;
    size_t samples = (size_t)(8u * HR_DETECTOR_SAMPLE_RATE_HZ);

    test_synth_pcg(signal, samples, 4000.0, 96.0, 0.26, 5000.0, 400.0);
    test_stream_recording(signal, samples, HR_DETECTOR_SAMPLE_RATE_HZ / 2u, NULL,
                          first_events, TEST_FIXTURE_CAPACITY, &first_count,
                          &first_estimate, NULL);
    test_stream_recording(signal, samples, HR_DETECTOR_SAMPLE_RATE_HZ / 2u, NULL,
                          second_events, TEST_FIXTURE_CAPACITY, &second_count,
                          &second_estimate, NULL);

    CHECK(first_count == second_count);
    CHECK(first_estimate.bpm == second_estimate.bpm);
    for (i = 0u; i < first_count && i < second_count; ++i) {
        CHECK(first_events[i] == second_events[i]);
    }
}

static void test_reset_and_arguments(void)
{
    static int16_t signal[4u * HR_DETECTOR_SAMPLE_RATE_HZ];
    static float work[HR_DETECTOR_WORK_FLOATS];
    static float small_work[16];
    hr_detector_t detector;
    hr_detector_estimate_t estimate;
    hr_detector_config_t config;
    size_t samples = (size_t)(4u * HR_DETECTOR_SAMPLE_RATE_HZ);

    CHECK(hr_detector_init(NULL, work, HR_DETECTOR_WORK_FLOATS, NULL)
          == HR_DETECTOR_STATUS_INVALID_ARGUMENT);
    CHECK(hr_detector_init(&detector, NULL, HR_DETECTOR_WORK_FLOATS, NULL)
          == HR_DETECTOR_STATUS_INVALID_ARGUMENT);
    CHECK(hr_detector_init(&detector, small_work, 16u, NULL)
          == HR_DETECTOR_STATUS_INVALID_ARGUMENT);

    config = *hr_detector_default_config();
    config.refractory_ms = 1.0f;
    CHECK(hr_detector_init(&detector, work, HR_DETECTOR_WORK_FLOATS, &config)
          == HR_DETECTOR_STATUS_INVALID_ARGUMENT);
    config = *hr_detector_default_config();
    config.noise_floor_mode = 7u;
    CHECK(hr_detector_init(&detector, work, HR_DETECTOR_WORK_FLOATS, &config)
          == HR_DETECTOR_STATUS_INVALID_ARGUMENT);
    CHECK(hr_detector_default_config()->threshold_ratio == 2.5f);

    CHECK(hr_detector_init(&detector, work, HR_DETECTOR_WORK_FLOATS, NULL)
          == HR_DETECTOR_STATUS_OK);
    test_synth_pcg(signal, samples, 4000.0, 72.0, 0.30, 6000.0, 120.0);
    CHECK(hr_detector_push_hop(&detector, NULL, 100u, 50u)
          == HR_DETECTOR_STATUS_INVALID_ARGUMENT);
    CHECK(hr_detector_push_hop(&detector, signal, 0u, 0u)
          == HR_DETECTOR_STATUS_INVALID_ARGUMENT);
    CHECK(hr_detector_push_hop(&detector, signal, HR_DETECTOR_WINDOW_SAMPLES + 1u, 0u)
          == HR_DETECTOR_STATUS_INVALID_ARGUMENT);
    CHECK(hr_detector_push(&detector, signal, HR_DETECTOR_WINDOW_SAMPLES) > 0);
    CHECK(hr_detector_estimate(NULL, &estimate) == HR_DETECTOR_STATUS_INVALID_ARGUMENT);
    CHECK(hr_detector_estimate(&detector, NULL) == HR_DETECTOR_STATUS_INVALID_ARGUMENT);
    CHECK(hr_detector_estimate(&detector, &estimate) == HR_DETECTOR_STATUS_OK);
    CHECK(estimate.events_total > 0u);

    hr_detector_reset(&detector);
    CHECK(hr_detector_estimate(&detector, &estimate) == HR_DETECTOR_STATUS_OK);
    CHECK(estimate.n_events == 0u);
    CHECK(estimate.events_total == 0u);
    CHECK(estimate.valid == 0u);
    CHECK(isnan(estimate.bpm));
    {
        int have = 1;

        CHECK(hr_detector_last_event_time(&detector, &have) == 0.0);
        CHECK(have == 0);
    }
    /* After reset the detector behaves like a fresh one. */
    CHECK(hr_detector_push(&detector, signal, HR_DETECTOR_WINDOW_SAMPLES) > 0);
}

/*
 * Short pushes exercise the filtfilt edge-padding and envelope-ring paths that
 * a full 2 s window never reaches (n < 2 * pad, n < 152, n < 76). They must not
 * read or write out of bounds and must not report a heart rate.
 */
static void test_short_windows(void)
{
    static int16_t signal[2048];
    static float work[HR_DETECTOR_WORK_FLOATS];
    static const size_t lengths[9] = {1u, 2u, 51u, 100u, 151u, 152u, 387u,
                                      388u, 2048u};
    hr_detector_t detector;
    hr_detector_estimate_t estimate;
    size_t i;
    size_t k;

    for (i = 0u; i < 2048u; ++i) {
        signal[i] = (int16_t)lround(6000.0
                                    * sin(2.0 * TEST_PI * 41.0 * (double)i / 4000.0));
    }
    for (k = 0u; k < sizeof(lengths) / sizeof(lengths[0]); ++k) {
        CHECK(hr_detector_init(&detector, work, HR_DETECTOR_WORK_FLOATS, NULL)
              == HR_DETECTOR_STATUS_OK);
        CHECK(hr_detector_push(&detector, signal, lengths[k]) >= 0);
        CHECK(hr_detector_estimate(&detector, &estimate) == HR_DETECTOR_STATUS_OK);
        CHECK(estimate.valid == 0u);
    }
    printf("short pushes: %u lengths from 1 to 2048 samples handled\n",
           (unsigned)(sizeof(lengths) / sizeof(lengths[0])));
}

static void test_noise_floor_modes(void)
{
    static int16_t signal[10u * HR_DETECTOR_SAMPLE_RATE_HZ];
    static double events[TEST_FIXTURE_CAPACITY];
    hr_detector_estimate_t estimate;
    size_t samples = (size_t)(10u * HR_DETECTOR_SAMPLE_RATE_HZ);
    int mode;

    test_synth_pcg(signal, samples, 4000.0, 72.0, 0.30, 6000.0, 120.0);
    for (mode = 0; mode < 4; ++mode) {
        hr_detector_config_t config = *hr_detector_default_config();
        size_t count = 0u;

        config.noise_floor_mode = (uint8_t)(mode & 1);
        config.p99_mode = (uint8_t)((mode >> 1) & 1);
        test_stream_recording(signal, samples, HR_DETECTOR_SAMPLE_RATE_HZ / 2u,
                              &config, events, TEST_FIXTURE_CAPACITY, &count,
                              &estimate, NULL);
        printf("mode floor=%u p99=%u: %u events, bpm=%.2f valid=%u\n",
               (unsigned)config.noise_floor_mode, (unsigned)config.p99_mode,
               (unsigned)count, (double)estimate.bpm, (unsigned)estimate.valid);
        CHECK(estimate.valid == 1u);
        CHECK(fabs((double)estimate.bpm - 72.0) <= 2.0);
    }
}

static void test_unit_suite(void)
{
    printf("sizeof(hr_detector_t)=%u bytes, work buffer=%u floats (%u bytes), "
           "event history=%u\n",
           (unsigned)sizeof(hr_detector_t), (unsigned)HR_DETECTOR_WORK_FLOATS,
           (unsigned)(HR_DETECTOR_WORK_FLOATS * sizeof(float)),
           (unsigned)HR_DETECTOR_EVENT_HISTORY);
    CHECK(sizeof(hr_detector_t) <= 512u);
    test_bandpass_table();
    test_heart_rate_estimator();
    test_synthetic_heart_rate();
    test_overlapping_windows_dedup();
    test_same_window_twice();
    test_validity_gate();
    test_silence_and_constant();
    test_event_history_cap();
    test_determinism();
    test_reset_and_arguments();
    test_short_windows();
    test_noise_floor_modes();
}

/* ------------------------------------------------------------------------- */
/* Fixture mode: C vs Python vs CirCor reference on real recordings           */
/* ------------------------------------------------------------------------- */

typedef struct {
    double start;
    double end;
} test_interval_t;

static int test_read_wav(
    const char *path,
    int16_t **out_samples,
    size_t *out_count,
    int *out_rate)
{
    FILE *handle = fopen(path, "rb");
    unsigned char header[12];
    unsigned char chunk[8];
    int channels = 0;
    int bits = 0;
    int rate = 0;
    long data_offset = -1;
    long data_size = 0;
    int16_t *samples = NULL;
    size_t frames;
    size_t i;

    *out_samples = NULL;
    *out_count = 0u;
    *out_rate = 0;
    if (handle == NULL) {
        return 0;
    }
    if (fread(header, 1u, 12u, handle) != 12u || memcmp(header, "RIFF", 4u) != 0
        || memcmp(header + 8, "WAVE", 4u) != 0) {
        fclose(handle);
        return 0;
    }
    while (fread(chunk, 1u, 8u, handle) == 8u) {
        long size = (long)chunk[4] | ((long)chunk[5] << 8)
                    | ((long)chunk[6] << 16) | ((long)chunk[7] << 24);
        long position = ftell(handle);

        if (memcmp(chunk, "fmt ", 4u) == 0) {
            unsigned char format[16];

            if (size < 16 || fread(format, 1u, 16u, handle) != 16u) {
                break;
            }
            if ((format[0] | (format[1] << 8)) != 1) {
                break; /* not PCM */
            }
            channels = format[2] | (format[3] << 8);
            rate = (int)((unsigned)format[4] | ((unsigned)format[5] << 8)
                         | ((unsigned)format[6] << 16)
                         | ((unsigned)format[7] << 24));
            bits = format[14] | (format[15] << 8);
        } else if (memcmp(chunk, "data", 4u) == 0) {
            data_offset = position;
            data_size = size;
        }
        if (fseek(handle, position + size + (size & 1), SEEK_SET) != 0) {
            break;
        }
        if (data_offset >= 0 && channels > 0) {
            break;
        }
    }
    if (data_offset < 0 || channels <= 0 || bits != 16 || rate <= 0) {
        fclose(handle);
        return 0;
    }
    if (fseek(handle, data_offset, SEEK_SET) != 0) {
        fclose(handle);
        return 0;
    }
    frames = (size_t)(data_size / (2L * (long)channels));
    if (frames == 0u) {
        fclose(handle);
        return 0;
    }
    samples = (int16_t *)malloc(frames * sizeof(int16_t));
    if (samples == NULL) {
        fclose(handle);
        return 0;
    }
    {
        int16_t *raw = (int16_t *)malloc((size_t)data_size);

        if (raw == NULL) {
            free(samples);
            fclose(handle);
            return 0;
        }
        if (fread(raw, 2u, (size_t)(data_size / 2L), handle)
            != (size_t)(data_size / 2L)) {
            free(raw);
            free(samples);
            fclose(handle);
            return 0;
        }
        for (i = 0u; i < frames; ++i) {
            if (channels == 1) {
                samples[i] = raw[i];
            } else {
                long sum = 0;
                int c;

                for (c = 0; c < channels; ++c) {
                    sum += raw[i * (size_t)channels + (size_t)c];
                }
                samples[i] = (int16_t)(sum / (long)channels);
            }
        }
        free(raw);
    }
    fclose(handle);
    *out_samples = samples;
    *out_count = frames;
    *out_rate = rate;
    return 1;
}

/* Merge the annotated intervals exactly like hs_segments.annotated_intervals
 * (labels 1-4, clipped to the recording, overlapping intervals merged). */
static size_t test_load_intervals(
    const char *path,
    double duration_s,
    test_interval_t *out,
    size_t capacity)
{
    FILE *handle = fopen(path, "r");
    char line[256];
    size_t count = 0u;
    size_t i;

    if (handle == NULL) {
        return 0u;
    }
    while (fgets(line, (int)sizeof(line), handle) != NULL) {
        double start;
        double end;
        double label;

        if (sscanf(line, "%lf %lf %lf", &start, &end, &label) != 3) {
            continue;
        }
        if ((int)label < 1 || (int)label > 4) {
            continue;
        }
        if (start < 0.0) {
            start = 0.0;
        }
        if (end > duration_s) {
            end = duration_s;
        }
        if (!(end > start) || count >= capacity) {
            continue;
        }
        out[count].start = start;
        out[count].end = end;
        ++count;
    }
    fclose(handle);

    for (i = 1u; i < count; ++i) {
        test_interval_t value = out[i];
        size_t j = i;

        while (j > 0u && out[j - 1u].start > value.start) {
            out[j] = out[j - 1u];
            --j;
        }
        out[j] = value;
    }
    {
        size_t merged = 0u;

        for (i = 0u; i < count; ++i) {
            if (merged > 0u && out[i].start <= out[merged - 1u].end) {
                if (out[i].end > out[merged - 1u].end) {
                    out[merged - 1u].end = out[i].end;
                }
            } else {
                out[merged++] = out[i];
            }
        }
        count = merged;
    }
    return count;
}

static size_t test_mask_events(
    double *times,
    size_t count,
    const test_interval_t *intervals,
    size_t interval_count)
{
    size_t kept = 0u;
    size_t i;

    for (i = 0u; i < count; ++i) {
        size_t k;
        int inside = 0;

        for (k = 0u; k < interval_count; ++k) {
            if (times[i] >= intervals[k].start && times[i] <= intervals[k].end) {
                inside = 1;
                break;
            }
        }
        if (inside) {
            times[kept++] = times[i];
        }
    }
    return kept;
}

static int test_compare_double(const void *left, const void *right)
{
    double a = *(const double *)left;
    double b = *(const double *)right;

    return (a < b) ? -1 : ((a > b) ? 1 : 0);
}

static void test_summary(const char *label, const double *errors, size_t count,
                         int with_within)
{
    double *values = (double *)malloc(count * sizeof(double));
    double sum = 0.0;
    size_t i;
    size_t within2 = 0u;
    size_t within5 = 0u;

    if (values == NULL || count == 0u) {
        free(values);
        printf("%-14s no data\n", label);
        return;
    }
    for (i = 0u; i < count; ++i) {
        values[i] = fabs(errors[i]);
        sum += values[i];
        if (values[i] <= 2.0) {
            ++within2;
        }
        if (values[i] <= 5.0) {
            ++within5;
        }
    }
    qsort(values, count, sizeof(double), test_compare_double);
    printf("%-14s n=%-5u MAE=%.2f  median=%.2f  p95=%.2f",
           label, (unsigned)count, sum / (double)count,
           test_median_sorted(values, count),
           values[(size_t)(0.95 * (double)(count - 1u))]);
    if (with_within) {
        printf("  within2=%.1f%%  within5=%.1f%%",
               100.0 * (double)within2 / (double)count,
               100.0 * (double)within5 / (double)count);
    }
    printf("\n");
    free(values);
}

/* Greedy 1:1 match of two ascending event lists within `tolerance` seconds. */
static size_t test_match_events(
    const double *left,
    size_t left_count,
    const double *right,
    size_t right_count,
    double tolerance)
{
    size_t i = 0u;
    size_t j = 0u;
    size_t hits = 0u;

    while (i < left_count && j < right_count) {
        double difference = left[i] - right[j];

        if (difference > tolerance) {
            ++j;
        } else if (difference < -tolerance) {
            ++i;
        } else {
            ++hits;
            ++i;
            ++j;
        }
    }
    return hits;
}

/*
 * One pass over the fixture CSV with one configuration.
 *
 * The mask is the same one evaluate_heart_rate.py applies: keep only the
 * detected events inside the annotated intervals (labels 1-4), then take the
 * heart rate from the remaining sequence. C heart rate is
 * hr_detector_estimate()'s formula (hr_heart_rate_from_events) over all kept
 * events of the recording, which is the same estimator Python uses on the whole
 * recording; the 32-event history variant is printed separately.
 */
static int test_fixture_pass(
    const char *csv_path,
    int limit,
    const hr_detector_config_t *config,
    const char *mode_label,
    int debug_worst,
    FILE *dump,
    FILE *python_events)
{
    static double events[TEST_FIXTURE_CAPACITY];
    double *c_vs_python = NULL;
    double *c_vs_reference = NULL;
    double *python_vs_reference = NULL;
    double *history_vs_python = NULL;
    double *history_vs_reference = NULL;
    double *last32_vs_python = NULL;
    double *push_seconds = NULL;
    double *debug_error = NULL;
    char (*debug_path)[256] = NULL;
    double *debug_c = NULL;
    double *debug_python = NULL;
    double *debug_ref = NULL;
    size_t *debug_events = NULL;
    FILE *handle = fopen(csv_path, "r");
    char line[2048];
    size_t capacity = 8192u;
    size_t usable = 0u;
    size_t c_failed = 0u;
    size_t total_rows = 0u;
    size_t gated = 0u;
    size_t windows = 0u;
    double total_push_seconds = 0.0;
    int row = 0;
    /* event-level diagnostics against tools/ai/dump_hr_fixture.py
     * --debug-events */
    static double python_times[2048];
    char python_line[65536];
    size_t total_c_events = 0u;
    size_t total_python_events = 0u;
    size_t matched_events = 0u;
    size_t identical_sets = 0u;
    size_t identical_within2 = 0u;
    size_t bucket_n[4];
    size_t bucket_within2[4];

    if (handle == NULL) {
        fprintf(stderr, "cannot open fixture %s\n", csv_path);
        return 1;
    }
    memset(bucket_n, 0, sizeof(bucket_n));
    memset(bucket_within2, 0, sizeof(bucket_within2));
    (void)python_line;
    c_vs_python = (double *)malloc(capacity * sizeof(double));
    c_vs_reference = (double *)malloc(capacity * sizeof(double));
    python_vs_reference = (double *)malloc(capacity * sizeof(double));
    push_seconds = (double *)malloc(capacity * sizeof(double));
    history_vs_python = (double *)malloc(capacity * sizeof(double));
    history_vs_reference = (double *)malloc(capacity * sizeof(double));
    last32_vs_python = (double *)malloc(capacity * sizeof(double));
    if (debug_worst > 0) {
        debug_error = (double *)calloc(capacity, sizeof(double));
        debug_path = (char (*)[256])calloc(capacity, 256u);
        debug_c = (double *)calloc(capacity, sizeof(double));
        debug_python = (double *)calloc(capacity, sizeof(double));
        debug_ref = (double *)calloc(capacity, sizeof(double));
        debug_events = (size_t *)calloc(capacity, sizeof(size_t));
    }
    if (c_vs_python == NULL || c_vs_reference == NULL
        || python_vs_reference == NULL || push_seconds == NULL
        || history_vs_python == NULL || history_vs_reference == NULL
        || last32_vs_python == NULL
        || (debug_worst > 0
            && (debug_error == NULL || debug_path == NULL || debug_c == NULL
                || debug_python == NULL || debug_ref == NULL
                || debug_events == NULL))) {
        fprintf(stderr, "out of memory\n");
        fclose(handle);
        free(history_vs_python);
    free(history_vs_reference);
    free(last32_vs_python);
        free(c_vs_python);
        free(c_vs_reference);
        free(python_vs_reference);
        free(push_seconds);
        return 1;
    }

    while (fgets(line, (int)sizeof(line), handle) != NULL) {
        char *last;
        char *previous;
        double python_hr;
        double reference_hr;
        size_t length = strlen(line);
        size_t samples = 0u;
        size_t recording_windows;
        int rate = 0;
        int16_t *wav = NULL;
        char tsv_path[2048];
        test_interval_t intervals[TEST_MAX_INTERVALS];
        size_t interval_count;
        hr_detector_estimate_t estimate;
        size_t event_count = 0u;
        size_t kept;
        size_t python_count = 0u;
        double push_measure = 0.0;
        double c_hr;
        double tail_hr;

        while (length > 0u
               && (line[length - 1u] == '\n' || line[length - 1u] == '\r')) {
            line[--length] = '\0';
        }
        if (length == 0u || strncmp(line, "wav_path", 8u) == 0) {
            continue;
        }
        last = strrchr(line, ',');
        if (last == NULL) {
            continue;
        }
        *last = '\0';
        previous = strrchr(line, ',');
        if (previous == NULL) {
            continue;
        }
        *previous = '\0';
        /* after both truncations: previous + 1 is the python column, last + 1
         * is the reference column (the row is path,python_hr,reference_hr). */
        python_hr = atof(previous + 1);
        reference_hr = atof(last + 1);
        if (limit > 0 && row >= limit) {
            break;
        }
        ++row;
        ++total_rows;

        /* The --python-events CSV is written by dump_hr_fixture.py in the same
         * row order, so read it in lockstep for every row (even a row the C
         * detector fails on) to keep the two files aligned. */
        python_count = 0u;
        if (python_events != NULL
            && fgets(python_line, (int)sizeof(python_line), python_events) != NULL) {
            char *comma = strchr(python_line, ',');

            if (comma != NULL) {
                const char *cursor = comma + 1;

                while (*cursor != '\0' && *cursor != '\n') {
                    char *end = NULL;
                    double value = strtod(cursor, &end);

                    if (end == cursor) {
                        break;
                    }
                    if (python_count < 2048u) {
                        python_times[python_count++] = value;
                    }
                    cursor = end;
                }
            }
        }

        if (!test_read_wav(line, &wav, &samples, &rate) || rate != 4000) {
            ++c_failed;
            free(wav);
            continue;
        }
        {
            size_t path_length = strlen(line);

            if (path_length + 5u > sizeof(tsv_path)) {
                ++c_failed;
                free(wav);
                continue;
            }
            memcpy(tsv_path, line, path_length + 1u);
            if (path_length >= 4u
                && strcmp(tsv_path + path_length - 4u, ".wav") == 0) {
                /* the annotation file sits next to the WAV, same stem */
                memcpy(tsv_path + path_length - 4u, ".tsv", 4u);
            } else {
                memcpy(tsv_path + path_length, ".tsv", 5u);
            }
        }
        interval_count = test_load_intervals(
            tsv_path, (double)samples / 4000.0, intervals, TEST_MAX_INTERVALS);

        test_stream_recording(
            wav,
            samples,
            HR_DETECTOR_SAMPLE_RATE_HZ / 2u,
            config,
            events,
            TEST_FIXTURE_CAPACITY,
            &event_count,
            &estimate,
            &push_measure);
        free(wav);

        if (samples < HR_DETECTOR_WINDOW_SAMPLES) {
            recording_windows = 1u;
        } else {
            size_t last_start = ((samples - HR_DETECTOR_WINDOW_SAMPLES)
                                 / (HR_DETECTOR_SAMPLE_RATE_HZ / 2u))
                                * (HR_DETECTOR_SAMPLE_RATE_HZ / 2u);
            recording_windows = last_start / (HR_DETECTOR_SAMPLE_RATE_HZ / 2u) + 1u;
            if (last_start + HR_DETECTOR_WINDOW_SAMPLES < samples) {
                ++recording_windows;
            }
        }

        kept = test_mask_events(events, event_count, intervals, interval_count);
        if (dump != NULL) {
            size_t d;

            fprintf(dump, "%s,%u,%u", line, (unsigned)kept,
                    (unsigned)event_count);
            for (d = 0u; d < kept; ++d) {
                fprintf(dump, ",%.4f", events[d]);
            }
            fprintf(dump, "\n");
        }
        c_hr = hr_heart_rate_from_events(events, kept, NULL);
        if (!isfinite(c_hr)) {
            ++c_failed;
            continue;
        }

        if (python_events != NULL) {
            size_t hits = test_match_events(events, kept, python_times,
                                            python_count, 0.050);
            total_c_events += kept;
            total_python_events += python_count;
            matched_events += hits;
            if (hits == kept && hits == python_count) {
                ++identical_sets;
                if (fabs(c_hr - python_hr) <= 2.0) {
                    ++identical_within2;
                }
            }
            {
                size_t bucket = (python_count < 10u) ? 0u
                                : (python_count < 20u) ? 1u
                                : (python_count < 40u) ? 2u : 3u;

                ++bucket_n[bucket];
                if (fabs(c_hr - python_hr) <= 2.0) {
                    ++bucket_within2[bucket];
                }
            }
        }
        total_push_seconds += push_measure;
        windows += recording_windows;
        if (kept < HR_DETECTOR_MIN_EVENTS || c_hr < 30.0 || c_hr > 220.0) {
            ++gated;
        }
        if (usable < capacity) {
            c_vs_python[usable] = c_hr - python_hr;
            c_vs_reference[usable] = c_hr - reference_hr;
            python_vs_reference[usable] = python_hr - reference_hr;
            /* What the device would show: the 32-event history estimate,
             * unmasked (the firmware has no annotations), over all events. */
            history_vs_python[usable] =
                (isfinite(estimate.bpm) ? (double)estimate.bpm : 1e6) - python_hr;
            history_vs_reference[usable] =
                (isfinite(estimate.bpm) ? (double)estimate.bpm : 1e6) - reference_hr;
            {
                size_t tail = (kept > HR_DETECTOR_EVENT_HISTORY)
                                  ? HR_DETECTOR_EVENT_HISTORY : kept;

                tail_hr = hr_heart_rate_from_events(
                    events + (kept - tail), tail, NULL);
                last32_vs_python[usable] =
                    (isfinite(tail_hr) ? tail_hr : 1e6) - python_hr;
            }
            if (debug_worst > 0) {
                debug_error[usable] = fabs(c_hr - python_hr);
                {
                    size_t copy = strlen(line);

                    if (copy > 255u) {
                        copy = 255u;
                    }
                    memcpy(debug_path[usable], line, copy);
                    debug_path[usable][copy] = '\0';
                }
                debug_c[usable] = c_hr;
                debug_python[usable] = python_hr;
                debug_ref[usable] = reference_hr;
                debug_events[usable] = kept;
            }
            ++usable;
        }
        if (row % 500 == 0) {
            fprintf(stderr, "  ... %d rows\n", row);
        }
    }
    fclose(handle);

    printf("\n=== heart-rate agreement [%s] ===\n", mode_label);
    printf("fixture: %s\n", csv_path);
    printf("recordings: %u compared, %u with no C heart rate\n",
           (unsigned)usable, (unsigned)c_failed);
    if (usable == 0u) {
        fprintf(stderr, "no usable recordings\n");
        free(c_vs_python);
        free(c_vs_reference);
        free(python_vs_reference);
        free(push_seconds);
        return 1;
    }
    printf("product gate (events >= 4 and 30 <= bpm <= 220) would show \"--\" "
           "on %u of %u (%.1f%%)\n",
           (unsigned)gated, (unsigned)usable,
           100.0 * (double)gated / (double)usable);
    test_summary("C vs Python", c_vs_python, usable, 1);
    test_summary("C vs ref", c_vs_reference, usable, 1);
    test_summary("Python vs ref", python_vs_reference, usable, 1);
    test_summary("C 32-ev vs Py", history_vs_python, usable, 1);
    test_summary("C 32-ev vs ref", history_vs_reference, usable, 1);
    test_summary("C last32 mask", last32_vs_python, usable, 1);

    if (debug_worst > 0) {
        int shown;
        size_t i;

        printf("\nworst %d records by |C - Python|:\n", debug_worst);
        for (shown = 0; shown < debug_worst; ++shown) {
            size_t best = usable;
            double best_error = -1.0;

            for (i = 0u; i < usable; ++i) {
                if (debug_error[i] > best_error) {
                    best_error = debug_error[i];
                    best = i;
                }
            }
            if (best == usable) {
                break;
            }
            printf("  %-58s c=%6.1f python=%6.1f ref=%6.1f err=%6.1f "
                   "events=%u\n",
                   debug_path[best], debug_c[best], debug_python[best],
                   debug_ref[best], debug_error[best],
                   (unsigned)debug_events[best]);
            debug_error[best] = -1.0;
        }
    }

    {
        size_t within2 = 0u;
        size_t i;
        double c_mae = 0.0;
        double python_mae = 0.0;
        double python_median;
        double python_p95;
        double *sorted;

        for (i = 0u; i < usable; ++i) {
            if (fabs(c_vs_python[i]) <= 2.0) {
                ++within2;
            }
            c_mae += fabs(c_vs_reference[i]);
            python_mae += fabs(python_vs_reference[i]);
        }
        c_mae /= (double)usable;
        python_mae /= (double)usable;

        sorted = (double *)malloc(usable * sizeof(double));
        if (sorted != NULL) {
            for (i = 0u; i < usable; ++i) {
                sorted[i] = fabs(python_vs_reference[i]);
            }
            qsort(sorted, usable, sizeof(double), test_compare_double);
            python_median = test_median_sorted(sorted, usable);
            python_p95 = sorted[(size_t)(0.95 * (double)(usable - 1u))];
            free(sorted);
        } else {
            python_median = 0.0;
            python_p95 = 0.0;
        }
        (void)python_median;
        (void)python_p95;

        printf("\ncriteria (stated before the run):\n");
        printf("  1. >= 95%% of recordings within +-2 bpm of Python: "
               "%.1f%% (%u/%u, C failures counted as misses) -> %s\n",
               100.0 * (double)within2 / (double)total_rows, (unsigned)within2,
               (unsigned)total_rows,
               (100.0 * (double)within2 / (double)total_rows >= 95.0) ? "PASS"
                                                                      : "FAIL");
        printf("  2. C-vs-reference MAE <= Python-vs-reference MAE + 1.0 bpm "
               "on the same rows: %.2f vs %.2f -> %s\n",
               c_mae, python_mae,
               (c_mae <= python_mae + 1.0) ? "PASS" : "FAIL");
        printf("     (Python reference for the full corpus: MAE 6.79, "
               "median 1.19, p95 44.2 on 3152 recordings)\n");
    }

    if (python_events != NULL && total_c_events > 0u) {
        static const char *bucket_names[4] = {
            "<10 events", "10-19", "20-39", ">=40 events"};
        int b;

        printf("\nevent level (tolerance 50 ms, only with --python-events):\n");
        printf("  python events found by C: %.2f%%   C events that are python "
               "events: %.2f%%\n",
               100.0 * (double)matched_events / (double)total_python_events,
               100.0 * (double)matched_events / (double)total_c_events);
        printf("  recordings with identical masked event sets: %u/%u "
               "(%.1f%%), HR within +-2 bpm on %.1f%% of them\n",
               (unsigned)identical_sets, (unsigned)usable,
               usable ? 100.0 * (double)identical_sets / (double)usable : 0.0,
               identical_sets
                   ? 100.0 * (double)identical_within2 / (double)identical_sets
                   : 0.0);
        for (b = 0; b < 4; ++b) {
            if (bucket_n[b] > 0u) {
                printf("  HR within +-2 bpm, python has %-11s: %5.1f%% (n=%u)\n",
                       bucket_names[b],
                       100.0 * (double)bucket_within2[b] / (double)bucket_n[b],
                       (unsigned)bucket_n[b]);
            }
        }
    }

    if (windows > 0u) {
        printf("\ntiming on this PC (NOT the ESP32-S3 number): %.1f s of "
               "hr_detector_push_hop over %u windows = %.1f us per 2 s window\n",
               total_push_seconds, (unsigned)windows,
               1e6 * total_push_seconds / (double)windows);
    }

    free(c_vs_python);
    free(c_vs_reference);
    free(python_vs_reference);
    free(history_vs_python);
    free(history_vs_reference);
    free(last32_vs_python);
    free(push_seconds);
    free(debug_error);
    free(debug_path);
    free(debug_c);
    free(debug_python);
    free(debug_ref);
    free(debug_events);
    return 0;
}

static int test_fixture(
    const char *csv_path,
    int limit,
    int sweep,
    int timing,
    const hr_detector_config_t *base,
    int debug_worst,
    const char *dump_events,
    const char *python_events_path)
{
    int mode_count = sweep ? 4 : 1;
    FILE *dump = NULL;
    FILE *python_events = NULL;
    int mode;

    (void)timing;
    if (python_events_path != NULL && python_events_path[0] != '\0') {
        python_events = fopen(python_events_path, "r");
        if (python_events == NULL) {
            fprintf(stderr, "cannot read %s\n", python_events_path);
            if (dump != NULL) {
                fclose(dump);
            }
            return 1;
        }
        /* Skip the header row. */
        {
            char header[512];

            if (fgets(header, (int)sizeof(header), python_events) == NULL) {
                fclose(python_events);
                python_events = NULL;
            }
        }
    }
    if (dump_events != NULL && dump_events[0] != '\0') {
        dump = fopen(dump_events, "w");
        if (dump == NULL) {
            fprintf(stderr, "cannot write %s\n", dump_events);
            return 1;
        }
    }
    for (mode = 0; mode < mode_count; ++mode) {
        hr_detector_config_t config = (base != NULL) ? *base
                                                     : *hr_detector_default_config();
        char label[192];
        int status;

        if (sweep || base == NULL) {
            config.noise_floor_mode = (uint8_t)(mode & 1);
            config.p99_mode = (uint8_t)((mode >> 1) & 1);
        }
        snprintf(label, sizeof(label),
                 "noise_floor=%s p99=%s merge_fraction=%.2f ratio=%.2f prom=%.2f",
                 config.noise_floor_mode == HR_NOISE_FLOOR_WINDOW ? "window(2s)"
                                                                  : "blend(3s)",
                 config.p99_mode == HR_P99_WINDOW ? "window" : "running-max",
                 (double)config.merge_fraction, (double)config.threshold_ratio,
                 (double)config.prominence_fraction);
        if (python_events != NULL) {
            rewind(python_events);
            {
                char header[512];

                if (fgets(header, (int)sizeof(header), python_events) == NULL) {
                    /* empty file: nothing to match */
                }
            }
        }
        status = test_fixture_pass(csv_path, limit, &config, label, debug_worst,
                                   dump, python_events);
        if (status != 0) {
            if (dump != NULL) {
                fclose(dump);
            }
            return status;
        }
    }
    if (dump != NULL) {
        fclose(dump);
    }
    if (python_events != NULL) {
        fclose(python_events);
    }
    return 0;
}

/* ------------------------------------------------------------------------- */

int main(int argc, char **argv)
{
    const char *fixture = NULL;
    hr_detector_config_t config = *hr_detector_default_config();
    int have_config = 0;
    int limit = 0;
    int sweep = 0;
    int timing = 0;
    int debug_worst = 0;
    const char *dump_events = NULL;
    const char *python_events_path = NULL;
    int i;

    for (i = 1; i < argc; ++i) {
        if (strcmp(argv[i], "--fixture") == 0 && i + 1 < argc) {
            fixture = argv[++i];
        } else if (strcmp(argv[i], "--limit") == 0 && i + 1 < argc) {
            limit = atoi(argv[++i]);
        } else if (strcmp(argv[i], "--sweep") == 0) {
            sweep = 1;
        } else if (strcmp(argv[i], "--timing") == 0) {
            timing = 1;
        } else if (strcmp(argv[i], "--python-events") == 0 && i + 1 < argc) {
            python_events_path = argv[++i];
        } else if (strcmp(argv[i], "--dump-events") == 0 && i + 1 < argc) {
            dump_events = argv[++i];
        } else if (strcmp(argv[i], "--debug") == 0 && i + 1 < argc) {
            debug_worst = atoi(argv[++i]);
        } else if (strcmp(argv[i], "--refractory-ms") == 0 && i + 1 < argc) {
            config.refractory_ms = (float)atof(argv[++i]);
            have_config = 1;
        } else if (strcmp(argv[i], "--prominence-fraction") == 0 && i + 1 < argc) {
            config.prominence_fraction = (float)atof(argv[++i]);
            have_config = 1;
        } else if (strcmp(argv[i], "--threshold-ratio") == 0 && i + 1 < argc) {
            config.threshold_ratio = (float)atof(argv[++i]);
            have_config = 1;
        } else if (strcmp(argv[i], "--noise") == 0 && i + 1 < argc) {
            config.noise_floor_mode = (uint8_t)atoi(argv[++i]);
            have_config = 1;
        } else if (strcmp(argv[i], "--p99") == 0 && i + 1 < argc) {
            config.p99_mode = (uint8_t)atoi(argv[++i]);
            have_config = 1;
        } else if (strcmp(argv[i], "--merge-fraction") == 0 && i + 1 < argc) {
            config.merge_fraction = (float)atof(argv[++i]);
            have_config = 1;
        } else if (strcmp(argv[i], "--merge-min-gap-ms") == 0 && i + 1 < argc) {
            config.merge_min_gap_ms = (float)atof(argv[++i]);
            have_config = 1;
        } else {
            fprintf(stderr, "unknown argument: %s\n", argv[i]);
            return 2;
        }
    }

    if (fixture != NULL) {
        return test_fixture(fixture, limit, sweep, timing,
                            have_config ? &config : NULL, debug_worst,
                            dump_events, python_events_path);
    }

    test_unit_suite();

    if (failures != 0) {
        fprintf(stderr, "%d test(s) failed\n", failures);
        return 1;
    }
    printf("hr_detector tests passed\n");
    return 0;
}
