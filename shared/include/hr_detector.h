#ifndef HR_DETECTOR_H
#define HR_DETECTOR_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/*
 * Streaming heart-rate detector for the ESP32-S3 firmware.
 *
 * This is the portable C port of the *heart-rate* half of the Python DSP
 * detector in tools/ai/hs_s1s2.py (ADR-008's fallback output). Heart rate only
 * needs stable event times, never the S1/S2 labels, so the two-state dynamic
 * program, the zero-crossing cue and the amplitude cue are deliberately NOT
 * ported. What is ported, step for step, is:
 *
 *   1. 20-85 Hz windowed-sinc FIR band-pass (129 taps) applied forward and
 *      backward (zero phase), i.e. hs_features.filtfilt(design_bandpass_fir).
 *   2. 38 ms moving-RMS envelope (a moving average of squares,
 *      hs_s1s2._moving_rms).
 *   3. Adaptive threshold = max(threshold_ratio * noise floor,
 *      min_threshold_fraction * p99(envelope)).
 *   4. Peak picking by steepest-first non-maximum suppression with a 100 ms
 *      refractory period plus the 60 % prominence test (hs_s1s2._select_peaks).
 *   5. Merge of peaks closer than max(merge_min_gap_s, merge_fraction *
 *      short_gap) down to the stronger one (hs_s1s2._merge_close_peaks).
 *   6. Heart rate = 60 / median of consecutive gap-pair sums
 *      (hs_s1s2.estimate_heart_rate), over an event-time history.
 *
 * ---------------------------------------------------------------------------
 * Decisions and their evidence (per docs/engineering-decision-guide.md)
 * ---------------------------------------------------------------------------
 * Evidence levels: E1 = primary (standard/datasheet/official reference code
 * in this repo), E2 = independent, E3 = vendor claim, E4 = measured on this PC.
 *
 * D8. Arithmetic: IEEE-754 binary32 float (chosen) vs Q15 fixed point.
 *     - The band-pass and the moving RMS are 2.3 M MAC per 2 s window. The S3
 *       has a single-precision FPU, so float32 runs at ~1 FMA/cycle without
 *       soft-float; Q15 would need 64-bit accumulators and hand-tuned scaling
 *       for the same dynamic range (envelope p99/median spans >20 dB inside
 *       one recording, E4 measurement on CirCor). Python computes in float64
 *       with float32 audio (hs_features), so float32 is also the closer port.
 *     - Determinism: the library is built as ISO C99 (CMAKE_C_EXTENSIONS OFF),
 *       where GCC/Clang do not contract a*b+c into FMA, and no fast-math flag
 *       is used, so binary32 results are reproducible bit for bit across
 *       x86-64 SSE and the S3 FPU. All filter coefficients are a compile-time
 *       table (hr_detector_bandpass_taps) rather than computed from libm at
 *       init, so sin()/cos() version differences cannot change the filter.
 *     - This deliberately deviates from ADR-014's "no runtime floating point",
 *       which was scoped to the *data path* (decimator). The heart-rate
 *       detector is a different unit and the S3 FPU makes float the cheaper
 *       choice; if a future board has no FPU the fallback is Q15 with the
 *       envelope normalised per window (documented in D10 below).
 *
 * D9. Work buffer: caller-owned float[8774] (chosen) vs an internal static
 *     buffer vs malloc vs a fully streaming envelope.
 *     - filtfilt needs the whole forward-filtered window before the backward
 *       pass (8774 floats = 35,096 B for a 2 s window), and the steepest-first
 *       peak rule plus the 80 ms prominence window need the envelope of the
 *       whole window. A fully streaming variant (causal FIR, 2.5 kB envelope
 *       ring, threshold from the previous window) is possible and is the
 *       fallback if PSRAM is unavailable, but it cannot reproduce
 *       filtfilt/block statistics, so it could not be validated against the
 *       Python reference the way this port is.
 *     - IRAM: the struct below is ~0.4 kB of .bss (DRAM, not IRAM) and the
 *       coefficient table is `const` (flash/rodata). Caller-owned storage
 *       follows the host_pipeline D3 precedent and can live in PSRAM (the S3
 *       on the current board has 8 MB). No malloc, no VLA, no static
 *       mutable state: two instances are independent.
 *     - Cost: 35,096 B per instance is the price of the batch window; the
 *       firmware already materialises an 8000-sample (16,000 B) AI window, so
 *       the extra requirement is 19 kB of PSRAM-class storage.
 *
 * D10. Adaptive noise floor: per-window quantiles (chosen) vs a 4 s block
 *     median. Python's median is taken over 4 s blocks of the envelope of the
 *     whole recording, which needs 4 s of envelope. This port cannot keep 4 s
 *     of envelope inside a 2 s window, so it computes the quantiles of the
 *     current window and offers two documented modes:
 *       HR_NOISE_FLOOR_WINDOW  - median of the window (2 s memory).
 *       HR_NOISE_FLOOR_BLEND   - 0.5 * (median of this window + median of the
 *                                previous window). Two overlapping 2 s windows
 *                                span 3 s, so this is the closest causal
 *                                approximation to the 4 s block that a 2 s
 *                                window can build.
 *     The p99 floor term has the same problem (Python uses the p99 of the
 *     whole recording), so HR_P99_WINDOW / HR_P99_RUNNING_MAX are selectable;
 *     RUNNING_MAX keeps max(p99) over all windows seen, i.e. a causal
 *     approximation of the recording-wide p99.
 *
 *     MEASURED (E4, CirCor, C vs Python heart rate,
 *     tests/test_hr_detector.c --fixture --sweep). The two p99=window rows are
 *     the full corpus (3152 recordings); the running-max rows were swept on a
 *     300-recording subset (296 usable) and are marked as such:
 *       mode                              MAE  within+-2  within+-5  ref MAE
 *       window(2s)+p99 window  (3152)     2.82    79.2%     89.5%     6.14
 *       blend(3s) +p99 window  (3152)     2.77    78.8%     89.4%     6.37
 *       window(2s)+p99 run-max (296)      4.31    75.0%     85.8%     8.72
 *       blend(3s) +p99 run-max (296)      3.78    77.7%     88.5%     9.23
 *       Python vs the same reference (3152): MAE 6.76, 66.6% within +-2 bpm,
 *       so both C modes are *closer to the reference* than Python itself.
 *     WINDOW is the default: on the full corpus BLEND does not improve
 *     agreement (78.8 % vs 79.2 %), costs 0.23 bpm of reference MAE, and needs
 *     cross-window state; the 300-recording pre-run had shown BLEND ahead
 *     (81.9 % vs 79.3 %), i.e. that difference is subset noise. RUNNING_MAX is
 *     rejected outright (worse on both metrics). The 95 % agreement target is
 *     NOT met - see D13 for the diagnosis and the measurements that explain
 *     where the residual difference comes from. A third candidate (a streaming
 *     4 s floor that reproduces Python's statistic more closely) was added and
 *     measured later: see D14 - it made both metrics worse and its code was
 *     removed, so there are exactly two modes.
 *
 * D11. Overlapping windows: the firmware steps a 2 s window every 1 s, so each
 *     sample is analysed twice. Two rules de-duplicate events:
 *       (a) a peak is nominated only if it lies in the part of the window that
 *           the previous push did not cover: index >= n - hop. With the
 *           firmware's 2 s/1 s pattern that is the newest second; with hop = 0
 *           (the same window pushed twice) nothing new is nominated.
 *       (b) an accepted event must be strictly later than the last accepted
 *           event plus the merge floor (merge_min_gap_ms, 120 ms). The floor is
 *           the same physiological floor hs_s1s2 uses ("two heart sounds cannot
 *           be closer than ~100 ms"), so it cannot swallow a real neighbour.
 *     Without (a)+(b) every event is counted twice and the heart rate doubles;
 *     test_hr_detector.c::test_overlapping_windows asserts the event count and
 *     the duplicate counter that prove the suppression fires.
 *
 * D12. Firmware integration (measured on the current firmware skeleton).
 *     - ai_display_task (firmware/main/app_main.c) already reads the newest
 *       8000 samples every APP_AI_WINDOW_STEP_MS = 1000 ms, which is exactly
 *       this API's 2 s window / 1 s hop, so hr_detector_push() is the right
 *       call; if the step ever changes, use hr_detector_push_hop() with the
 *       real hop in samples.
 *     - Storage: `static hr_detector_t` (360 B) + `static float
 *       work[HR_DETECTOR_WORK_FLOATS]` (35,096 B) in .bss/DIRAM. Both are
 *       DRAM, not IRAM, and `nm` shows no new IRAM demand: the code and the
 *       coefficient table are flash/rodata, so the full IRAM segment (ADR-016)
 *       is untouched. The S3 DIRAM was 39.4 % used, so 35 kB fits, but it is
 *       the dominant RAM cost of the feature - see D9 for the streaming
 *       alternative if the display/AI work later needs that RAM back.
 *     - Stack: measured with -fstack-usage (gcc -O2, x86-64):
 *       hr_detector_push_hop 1760 B, hr_envelope_quantile 288 B,
 *       hr_detector_estimate 432 B, hr_heart_rate_from_events 2080 B (host
 *       helper; the firmware never calls it). The existing AI task stack is
 *       APP_AI_TASK_STACK_BYTES = 4096, so it must be raised (8192) or the HR
 *       work moved to its own task before enabling this on the board.
 *     - Reset on a new probe session: hr_detector_reset(). The absolute-time
 *       cursor advances by hop per push, so a session restart (or a long run of
 *       skipped pushes) must not carry the old cursor into the new stream.
 *     - Display: hr_detector_estimate()->valid ? "HR ~%d" : "HR --", with the
 *       "estimated, not a diagnosis" wording of docs/mvp-output-contract.md.
 *     - Runtime: 1.48 ms per 2 s window on this PC (E4, Release, one core).
 *       That is NOT the S3 number: the window is 2.2 M MAC, i.e. >=9 ms at
 *       240 MHz even at 1 FMA/cycle, so budget ~10-20 ms per push (estimate)
 *       and confirm with an on-board measurement. The 1 s step has the room.
 *
 * D13. Agreement with the Python reference - measured, and NOT met (E4).
 *     3152 CirCor recordings, C vs Python heart rate (same masked estimator):
 *       MAE 2.82 bpm, median 0.47, p95 16.0, 79.2 % within +-2 bpm.
 *     The 95 % target is missed. Where the difference comes from:
 *       - Event level: C finds 97.6 % of Python's masked events and 96.4 % of
 *         its own events are Python events (50 ms tolerance); for the 12,995
 *         matched events of the 300-recording pre-run the median time
 *         difference is 0.05 ms (p95 7.8 ms). The DSP chain, the peak rule,
 *         the merge and the estimator are therefore ported correctly.
 *       - 31.4 % of recordings have an identical masked event set, and on
 *         those the heart rate agrees within +-2 bpm in 98.6 % of cases. The
 *         residual comes from a handful of extra/missing events per
 *         recording, which is the D10 threshold deviation working as
 *         documented.
 *       - The estimator itself (median of consecutive gap-pair sums) is
 *         brittle when few events fall inside the annotated interval:
 *         agreement is 52 % for <10 events, 64 % for 10-19, 81 % for 20-39 and
 *         90 % for >=40 (CirCor annotates only representative sections).
 *       - Against the CirCor annotation reference the C port is *better* than
 *         Python on the same 3152 rows (MAE 6.14 vs 6.76, p95 39.5 vs 44.1),
 *         i.e. the disagreement is not a regression: Python loses events where
 *         its 4 s/whole-recording threshold starves a quiet but annotated
 *         section, and the 2 s local threshold does not.
 *       - The obvious repair - a streaming 4 s floor that matches Python's
 *         statistic more closely - was implemented and measured on the full
 *         corpus in D14 and made both metrics worse (agreement -0.9 to
 *         -2.0 points, reference MAE +0.7 to +1.1 bpm). The residual is
 *         therefore NOT the 2 s vs 4 s floor span; D14 lists what is left.
 *     Two product-relevant measurements on the same corpus:
 *       - the same estimator over only the newest 32 events (the size of the
 *         history here) but still annotation-masked: 67.9 % within +-2 bpm of
 *         Python. The bounded history alone costs ~11 points.
 *       - what the device can actually do (no annotation mask, newest 32
 *         events): MAE 8.06-8.14 bpm vs the reference and 41 % within +-2 bpm
 *         of Python. The annotation mask is worth more than the history depth,
 *         so the firmware number must not be quoted from the 1.19 bpm median
 *         of the offline report.
 *
 * D14. Candidate D - streaming 4 s noise floor: IMPLEMENTED, MEASURED ON THE
 *     FULL CORPUS, REJECTED, CODE REMOVED.
 *     ADR-014 left the 4 s block median as the improvement path, and D10
 *     approximated it with the median of one 2 s window. Candidate D was built
 *     as ADR-014 proposed: keep a reduced-rate history of the envelope (one
 *     sample per 4000 / rate input samples, i.e. 100 floats at 4 s / 25 Hz and
 *     200 at 4 s / 50 Hz), append only the part of each window the previous
 *     push did not cover (the same `first_new` rule D11 uses for events, so the
 *     2 s / 1 s stepping cannot count a sample twice), and take the median of
 *     the newest noise_history_ms * rate / 1000 entries with the same two-level
 *     histogram as D10. It was selectable through the config (history length
 *     and sampling rate) and cost 800 B of .bss (sizeof(hr_detector_t) 360 ->
 *     1,184 B on x86-64); the work buffer and the init signature were
 *     unchanged. It is gone now - see the decision. The table below is the
 *     record, so nobody needs to rebuild it.
 *
 *     MEASURED (E4, CirCor, full fixture = 3152 recordings, 3145-3150 usable;
 *     control = the pre-D14 default; `agreement` = C vs Python within +-2 bpm,
 *     `ref` = C vs the CirCor annotation, `dev32` = device context (newest 32
 *     events, no annotation mask, vs ref)):
 *       mode              py MAE  agreement  within+-5  ref MAE  dev32
 *       window(2s) ctrl     2.82     79.2%     89.5%      6.14    8.14
 *       stream(3s@25Hz)     2.79     77.7%     89.4%      6.93    8.39
 *       stream(3s@50Hz)     2.67     78.3%     89.4%      6.83    8.28
 *       stream(4s@25Hz)     3.06     77.2%     88.4%      7.17    8.19
 *       stream(4s@50Hz)     2.85     78.2%     88.8%      7.21    8.40
 *     DECISION: rejected, and the code was removed instead of kept as an
 *     opt-in mode - a mode that measured worse is dead code, and the firmware
 *     DIRAM margin (docs/host-resource-budget.md) cannot pay 800 B for it. The
 *     2 s window mode was the default before the experiment and stayed the
 *     default throughout; no measurement below was produced with a worse
 *     default in place.
 *       - It does not close the gap. Agreement falls 0.9-2.0 points (the
 *         pre-registered target was >= 90 %) and the C-vs-reference MAE
 *         regresses 0.69-1.07 bpm against a budget of +0.30 (i.e. <= 6.44).
 *         Both acceptance criteria fail.
 *       - At a fixed envelope rate the degradation is monotone in the history
 *         length (25 Hz: 2 s 79.2 % / 6.14 -> 3 s 77.7 % / 6.93 -> 4 s
 *         77.2 % / 7.17; 50 Hz: 3 s 78.3 % / 6.83 -> 4 s 78.2 % / 7.21), so the
 *         longer memory is the cause, not one unlucky setting.
 *       - The direction matches D13: Python's 4 s/whole-recording threshold
 *         starves quiet but annotated sections, which is why the 2 s window
 *         mode is *closer to the annotation* than Python. Reproducing the 4 s
 *         floor more faithfully imports that error.
 *       - SUBSET-NOISE TRAP - do not fall for it again. A 300-recording
 *         pre-run showed the opposite sign (+2.0 points for 4 s @ 25 Hz:
 *         81.3 % vs 79.3 %; ref MAE 7.80 vs 6.53), and a 200-recording run
 *         reproduced the same illusion (79-82.5 % vs 77.0 %; ref MAE 7.26-8.13
 *         vs 6.25). The full corpus reversed both. The BLEND pre-run in D10
 *         was the same mistake. Never size a threshold statistic on a subset
 *         of CirCor.
 *       - Therefore the residual C-vs-Python disagreement is NOT the 2 s vs 4 s
 *         floor span. What remains unmatched is the recording-wide p99 (still
 *         per-window here) and the non-causal alignment of Python's block
 *         median (a sample at the start of a block sees up to 4 s into the
 *         future), which no causal streaming implementation can reproduce.
 *         Those two are the things a future attempt should target.
 *     Note for docs/engineering-decision-register.md ADR-014: its candidate-D
 *     row ("4 s block median + whole-recording p99, not implemented") is
 *     superseded by this section; docs/ is outside this change's scope.
 *
 * Public API (see the declarations below):
 *   hr_detector_init      - bind caller storage + optional config
 *   hr_detector_push      - one 2 s window (hop = n/2, the firmware pattern)
 *   hr_detector_push_hop  - one window with an explicit hop (n - hop new)
 *   hr_detector_estimate  - bpm + validity flag + counters
 *   hr_detector_reset     - drop events and quantile history, keep config
 *
 * Sample rate: fixed 4 kHz (HOST_PIPELINE_AI_WINDOW_SAMPLES / ADR-002). The
 * detector does not resample; feed it host_pipeline decimator output.
 *
 * Not thread-safe; the caller must serialise push/estimate/reset.
 */

/* Fixed 4 kHz baseline (ADR-002). */
#define HR_DETECTOR_SAMPLE_RATE_HZ 4000u

/* 2 s analysis window: 8000 samples at 4 kHz. */
#define HR_DETECTOR_WINDOW_MS 2000u
#define HR_DETECTOR_WINDOW_SAMPLES \
    ((HR_DETECTOR_SAMPLE_RATE_HZ * HR_DETECTOR_WINDOW_MS) / 1000u)

/* 129-tap windowed-sinc band-pass, 20-85 Hz (hs_s1s2.DetectorConfig). */
#define HR_DETECTOR_FILT_TAPS 129u
#define HR_DETECTOR_FILT_HALF (HR_DETECTOR_FILT_TAPS / 2u) /* 64 */

/* filtfilt edge padding: min(3 * taps, n - 1) at the 2 s window. */
#define HR_DETECTOR_FILT_PAD 387u

/*
 * Caller-owned work storage, in floats. The forward-filtered window is
 * n + 2 * pad floats for n = 8000; the envelope is then built in place.
 */
#define HR_DETECTOR_WORK_FLOATS \
    (HR_DETECTOR_WINDOW_SAMPLES + 2u * HR_DETECTOR_FILT_PAD)

/* 38 ms envelope at 4 kHz. */
#define HR_DETECTOR_ENVELOPE_MS 38u
#define HR_DETECTOR_ENVELOPE_SAMPLES \
    ((HR_DETECTOR_SAMPLE_RATE_HZ * HR_DETECTOR_ENVELOPE_MS) / 1000u) /* 152 */

/* Event history. 32 events = 16 cardiac cycles at 60 bpm. */
#define HR_DETECTOR_EVENT_HISTORY 32u

/* Validity gate used by the product display ("--" otherwise). */
#define HR_DETECTOR_MIN_EVENTS 4u
#define HR_DETECTOR_BPM_MIN 30.0f
#define HR_DETECTOR_BPM_MAX 220.0f

/* Quantile estimator resolution: one coarse + one fine histogram pass. */
#define HR_DETECTOR_HIST_BINS 128u

/* Status values: negative is an error, non-negative is "no fault". */
#define HR_DETECTOR_STATUS_OK 0
#define HR_DETECTOR_STATUS_INVALID_ARGUMENT (-1)

/*
 * Noise-floor / p99 modes (documented in D10). HR_NOISE_FLOOR_WINDOW and
 * HR_P99_WINDOW are the defaults; D10 lists the measured agreement of all four
 * combinations on 3152 CirCor recordings. Switch to BLEND only with a new
 * measurement - it did not improve agreement on the full corpus.
 */
#define HR_NOISE_FLOOR_WINDOW 0u
#define HR_NOISE_FLOOR_BLEND 1u
#define HR_P99_WINDOW 0u
#define HR_P99_RUNNING_MAX 1u

typedef struct {
    /* hs_s1s2.DetectorConfig defaults. */
    float threshold_ratio;        /* 2.5   */
    float min_threshold_fraction; /* 0.10  */
    float refractory_ms;          /* 100.0 */
    float prominence_window_ms;   /* 80.0  */
    float prominence_fraction;    /* 0.60  */
    float merge_fraction;         /* 0.55  */
    float merge_min_gap_ms;       /* 120.0 */
    /* D10 deviations; see HR_NOISE_FLOOR_* / HR_P99_*. */
    uint8_t noise_floor_mode;
    uint8_t p99_mode;
    uint8_t reserved[2];
} hr_detector_config_t;

typedef struct {
    /* Result of hr_detector_estimate(). */
    float bpm;             /* set to NAN when valid == 0                   */
    float median_cycle_s;  /* median consecutive gap-pair sum (1 beat)     */
    uint8_t valid;         /* 1 iff n_events >= 4 and 30 <= bpm <= 220     */
    uint16_t n_events;     /* events currently in the history (<= 32)      */
    uint32_t events_total; /* events accepted since init/reset()           */
    uint32_t windows;      /* pushes since init/reset()                    */
    uint32_t duplicates;   /* peaks suppressed by the overlap rules (D11)  */
    uint32_t peaks_total;  /* peaks accepted inside windows (pre-dedup)    */
} hr_detector_estimate_t;

/*
 * Detector state. The fields are public only because the caller allocates the
 * struct (same convention as host_pipeline_t). Treat them as private.
 */
typedef struct {
    hr_detector_config_t config;
    float *work;                           /* caller-owned, WORK_FLOATS   */
    size_t work_floats;
    float prev_window_median;              /* D10 BLEND mode              */
    float running_p99;                     /* D10 RUNNING_MAX mode        */
    uint8_t have_prev_window;
    uint8_t have_last_event;
    uint8_t reserved0[6];
    /* Event times in absolute seconds, ring, oldest at event_head. */
    double event_times[HR_DETECTOR_EVENT_HISTORY];
    uint16_t event_head;
    uint16_t event_count;
    uint32_t event_total;
    double last_event_time;
    uint64_t next_window_start;            /* absolute sample index       */
    uint32_t windows;
    uint32_t peaks_total;
    uint32_t duplicates;
} hr_detector_t;

/*
 * Band-pass coefficients: hs_features.design_bandpass_fir(4000, 20, 85, 129),
 * i.e. 129-tap Hamming-windowed sinc, normalised to unit gain at
 * sqrt(20 * 85) = 41.23 Hz. Symmetric, so the centred convolution is zero
 * phase. Regenerate with:
 *   python -c "import sys; sys.path.insert(0,'tools/ai'); \
 *     import hs_features; print(list(hs_features.design_bandpass_fir(4000,20,85,129)))"
 * test_hr_detector.c re-derives the table from that formula and compares.
 */
extern const float hr_detector_bandpass_taps[HR_DETECTOR_FILT_TAPS];

/* Default configuration (never NULL). */
const hr_detector_config_t *hr_detector_default_config(void);

/*
 * Initialise a caller-owned detector.
 *
 * work must point to at least work_floats >= HR_DETECTOR_WORK_FLOATS floats;
 * the storage is not zeroed and its contents are scratch. config may be NULL
 * (defaults). Returns HR_DETECTOR_STATUS_OK, or
 * HR_DETECTOR_STATUS_INVALID_ARGUMENT for a NULL detector/work, a short work
 * buffer, or an out-of-range config value.
 */
int hr_detector_init(
    hr_detector_t *detector,
    float *work,
    size_t work_floats,
    const hr_detector_config_t *config);

/*
 * Push one analysis window of n samples at 4 kHz, where consecutive windows
 * advance by n / 2 samples (2 s window, 1 s step - the firmware pattern).
 *
 * n must be in [1, HR_DETECTOR_WINDOW_SAMPLES]; the caller keeps ownership of
 * samples and may reuse it on return. Returns the number of *new* events
 * accepted by this push (>= 0), or HR_DETECTOR_STATUS_INVALID_ARGUMENT.
 */
int hr_detector_push(hr_detector_t *detector, const int16_t *samples, size_t n);

/*
 * Same as hr_detector_push() with an explicit hop: the previous window ended
 * hop samples before this one, so only indices >= n - hop can nominate a new
 * event (D11). hop = n means "no overlap", hop = 0 means "same window again"
 * (nothing new). n must be in [1, HR_DETECTOR_WINDOW_SAMPLES] and
 * hop in [0, n + ...); a hop larger than n simply means the stream had a gap.
 */
int hr_detector_push_hop(
    hr_detector_t *detector,
    const int16_t *samples,
    size_t n,
    size_t hop);

/*
 * Heart rate from the stored event history. out is always written (bpm = NAN,
 * valid = 0 when the history cannot support a rate). Returns
 * HR_DETECTOR_STATUS_OK or HR_DETECTOR_STATUS_INVALID_ARGUMENT.
 */
int hr_detector_estimate(
    const hr_detector_t *detector,
    hr_detector_estimate_t *out);

/*
 * Pure estimator: 60 / median of consecutive gap-pair sums over `count` event
 * times in seconds. Requires count >= 3; returns NAN otherwise. `cycle_out`
 * (optional) receives the median pair sum in seconds. This is exactly
 * hs_s1s2.estimate_heart_rate, exposed so tests and the host can apply it to
 * an event list that is longer than the 32-event history.
 */
double hr_heart_rate_from_events(
    const double *event_times,
    size_t count,
    double *cycle_out);

/*
 * Copy up to `capacity` event times (absolute seconds, oldest first). Returns
 * the number of times written. NULL detector or out writes nothing and
 * returns 0.
 */
size_t hr_detector_get_events(
    const hr_detector_t *detector,
    double *out,
    size_t capacity);

/* Newest accepted event time in seconds; 0 with *have = 0 when none yet. */
double hr_detector_last_event_time(
    const hr_detector_t *detector,
    int *have);

/*
 * Drop the event history and the adaptive-floor history. Config and the work
 * buffer binding are kept, so the next push starts a new recording: the window
 * cursor restarts at sample 0 and the first push nominates every index.
 */
void hr_detector_reset(hr_detector_t *detector);

#ifdef __cplusplus
}
#endif

#endif
