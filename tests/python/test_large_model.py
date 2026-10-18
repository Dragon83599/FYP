"""Tests for the capacity experiment (``tools/ai/large_model_experiment.py``).

That module answers one question: *would a Raspberry Pi's extra compute buy
patient-level accuracy on CirCor, or is the data the bottleneck?*  The answer is
only worth anything if three properties hold, and these tests pin them:

1. **The table describes the models that are trained.**  ``small`` must
   reproduce the published baseline exactly (41,105 parameters, 5,722,944
   MAC/window), the capacity arms must stay inside the size bands the report
   claims, and the analytic parameter/MAC formulas must agree with
   ``hs_cnn.count_parameters`` / ``hs_cnn.measure_macs`` on the real torch
   modules.  The torch half is skipped when PyTorch is absent, because CI
   installs NumPy only.
2. **The decision rule is fixed and one-sided.**  It was pre-registered before
   the large arms ran, so it must be a pure function of the two arms' pooled
   AUROC and CIs: a larger arm can only justify a stronger host by beating the
   control's pooled CI half-width, and a smoke run over fewer than five frozen
   folds can never trigger it.
3. **The module stays torch-free at import time**, so the whole Python suite
   (and this file's formula tests) run in CI without PyTorch.

Everything here is measured against the frozen protocol constants
(``data/processed/circor/splits.csv``, 874 patients, 11,728 annotated windows);
the tests themselves never read the dataset, so they stay fast and hermetic.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
AI_DIR = REPO_ROOT / "tools" / "ai"
PYTHON = sys.executable

sys.path.insert(0, str(AI_DIR))

import large_model_experiment as lme  # noqa: E402  (path setup first)

SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")

#: The published baseline (data/reports/circor-cv-cnn-annotated/report.json).
BASELINE_PARAMETERS = 41105
BASELINE_MACS = 5722944


def torch_available() -> bool:
    try:
        import torch  # noqa: F401
    except Exception:  # noqa: BLE001 - only the torch half is skipped
        return False
    return True


TORCH_AVAILABLE = torch_available()


def fake_arm(
    model: str,
    pooled_auroc: float,
    ci_low: float,
    ci_high: float,
    folds: int = 5,
    parameters: int = 400000,
    macs: int = 40000000,
    fold_std: float = 0.05,
) -> dict:
    """The subset of an arm report the decision rule reads."""
    return {
        "model": model,
        "folds": folds,
        "aggregate": {"patient_auroc": {"mean": pooled_auroc, "std": fold_std}},
        "pooled_patient_at_0.5": {
            "auroc": pooled_auroc,
            "auroc_ci95": {"low": ci_low, "high": ci_high},
        },
        "architecture": {"parameters": parameters, "macs_per_window": macs},
    }


class TestArchitectureTable(unittest.TestCase):
    def test_names_are_the_ones_cross_validate_accepts(self) -> None:
        self.assertEqual(lme.CONV_ARCHITECTURES, ("small", "resnet", "tcn"))
        for name in lme.CONV_ARCHITECTURES:
            self.assertEqual(lme.ARCHITECTURE_SPECS[name].name, name)
        self.assertIn("cnn", lme.SMALL_ALIASES)
        self.assertIn("small", lme.SMALL_ALIASES)

    def test_small_spec_is_the_published_baseline(self) -> None:
        """41,105 params / 5.72 M MAC is the statement every arm is compared to."""
        self.assertEqual(lme.architecture_parameters("small"), BASELINE_PARAMETERS)
        self.assertEqual(lme.architecture_macs("small"), BASELINE_MACS)

    def test_capacity_arms_are_actually_larger(self) -> None:
        small = lme.architecture_parameters("small")
        small_macs = lme.architecture_macs("small")
        for name in ("resnet", "tcn"):
            parameters = lme.architecture_parameters(name)
            macs = lme.architecture_macs(name)
            self.assertGreater(parameters, 5 * small, name)
            self.assertGreater(macs, 3 * small_macs, name)

    def test_capacity_arms_stay_inside_the_reported_size_bands(self) -> None:
        """The report claims ~200-500k (ResNet) and ~150-400k (TCN) parameters.

        If a spec is resized, this test must be updated together with the
        report's claim - that is the point of pinning the band, not the exact
        number.
        """
        self.assertGreaterEqual(lme.architecture_parameters("resnet"), 200_000)
        self.assertLessEqual(lme.architecture_parameters("resnet"), 500_000)
        self.assertGreaterEqual(lme.architecture_parameters("tcn"), 150_000)
        self.assertLessEqual(lme.architecture_parameters("tcn"), 400_000)

    def test_conv_out_length_matches_torch_same_padding(self) -> None:
        self.assertEqual(lme.conv_out_length(8000, 15, 4), 2000)
        self.assertEqual(lme.conv_out_length(1000, 11, 2), 500)
        self.assertEqual(lme.conv_out_length(250, 7, 2), 125)
        self.assertEqual(lme.conv_out_length(62, 3, 1), 62)
        self.assertEqual(lme.conv_out_length(63, 3, 2), 32)

    def test_formula_on_a_hand_computed_spec(self) -> None:
        """A tiny spec whose parameter and MAC count are computed by hand.

        stem: conv(1->4, k=3) + BN(4)          -> 1*4*3 + 2*4      = 20
        stage: conv(4->8, k=3) + BN(8)         -> 4*8*3 + 2*8      = 112
        head: linear(8, 1)                     -> 8 + 1            = 9
        total 141; MAC at length 16: 16*4*3 + 16*8*4*3 + 8         = 1736
        """
        spec = lme.ConvNetSpec(
            name="tiny",
            description="hand-computed fixture",
            stem_channels=4,
            stem_kernel=3,
            stem_stride=1,
            stem_pool=1,
            stages=(lme.ConvStageSpec(blocks=1, in_channels=4, channels=8, kernel=3),),
        )
        self.assertEqual(lme.parameters_of(spec), 141)
        self.assertEqual(lme.macs_of(spec, input_length=16), 1736)

    def test_residual_formula_on_a_hand_computed_spec(self) -> None:
        """A strided residual block has a projection shortcut and its own BNs.

        conv1 4*8*3 + BN 16 + conv2 8*8*3 + BN 16 + shortcut 8*4*1 + BN 16 = 368
        MAC at length 16: out 8 -> 8*8*4*3 + 8*8*8*3 + 8*8*4 = 2560
        """
        spec = lme.ConvNetSpec(
            name="tiny-residual",
            description="hand-computed fixture",
            stem_channels=4,
            stem_kernel=3,
            stem_stride=1,
            stem_pool=1,
            stages=(
                lme.ConvStageSpec(
                    blocks=1,
                    in_channels=4,
                    channels=8,
                    kernel=3,
                    stride=2,
                    residual=True,
                ),
            ),
        )
        # stem 20 + block 368 + head 9
        self.assertEqual(lme.parameters_of(spec), 397)
        # stem 192 + block 2560 + head 8
        self.assertEqual(lme.macs_of(spec, input_length=16), 2760)

    def test_budget_table_reports_ratios_against_the_baseline(self) -> None:
        table = {row["name"]: row for row in lme.budget_table()}
        self.assertEqual(set(table), set(lme.CONV_ARCHITECTURES))
        self.assertAlmostEqual(table["small"]["parameter_ratio_to_small"], 1.0)
        self.assertAlmostEqual(table["small"]["macs_ratio_to_small"], 1.0)
        for name in ("resnet", "tcn"):
            expected = table[name]["parameters"] / BASELINE_PARAMETERS
            self.assertAlmostEqual(table[name]["parameter_ratio_to_small"], expected)
            self.assertEqual(table[name]["int8_weight_bytes_estimate"], table[name]["parameters"])
            self.assertEqual(table[name]["fp32_weight_bytes"], 4 * table[name]["parameters"])

    def test_every_architecture_is_integer_counted(self) -> None:
        small_macs = lme.architecture_macs("small")
        for name in lme.CONV_ARCHITECTURES:
            self.assertIsInstance(lme.architecture_parameters(name), int)
            self.assertIsInstance(lme.architecture_macs(name), int)
            if name != "small":
                self.assertGreater(lme.architecture_macs(name), small_macs)


class TestTorchFreeImport(unittest.TestCase):
    def test_importing_the_experiment_does_not_import_torch(self) -> None:
        """CI installs NumPy only, so the architecture table must not need torch."""
        code = (
            "import sys; sys.path.insert(0, %r);"
            "import large_model_experiment as m;"
            "print('torch' in sys.modules);"
            "print(m.architecture_parameters('small'))" % str(AI_DIR)
        )
        result = subprocess.run(
            [PYTHON, "-c", code],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.split()
        self.assertEqual(lines[0], "False")
        self.assertEqual(lines[1], str(BASELINE_PARAMETERS))

    def test_unknown_architecture_is_rejected(self) -> None:
        with self.assertRaises(KeyError):
            lme.architecture_parameters("transformer")
        with self.assertRaises(KeyError):
            lme.architecture_macs("transformer")


@unittest.skipUnless(TORCH_AVAILABLE, "PyTorch is not installed (CI installs NumPy only)")
class TestTorchModels(unittest.TestCase):
    """The formulas must describe the modules that are actually trained."""

    @classmethod
    def setUpClass(cls) -> None:
        import torch

        import hs_cnn

        cls.torch = torch
        cls.hs_cnn = hs_cnn
        torch.set_num_threads(1)

    def test_formulas_match_the_real_modules(self) -> None:
        for name in lme.CONV_ARCHITECTURES:
            with self.subTest(architecture=name):
                model = self.hs_cnn.build_model(name)
                self.assertEqual(
                    self.hs_cnn.count_parameters(model),
                    lme.architecture_parameters(name),
                )
                self.assertEqual(
                    self.hs_cnn.measure_macs(model),
                    lme.architecture_macs(name),
                )

    def test_small_spec_rebuilds_the_small_cnn(self) -> None:
        """``ConvNet1D(SMALL_SPEC)`` is the same architecture as ``Small1DCNN``."""
        rebuilt = self.hs_cnn.ConvNet1D(lme.ARCHITECTURE_SPECS["small"])
        baseline = self.hs_cnn.Small1DCNN()
        self.assertEqual(
            self.hs_cnn.count_parameters(rebuilt),
            self.hs_cnn.count_parameters(baseline),
        )
        self.assertEqual(self.hs_cnn.measure_macs(rebuilt), self.hs_cnn.measure_macs(baseline))

    def test_build_model_small_is_the_baseline_class(self) -> None:
        """The control arm must be the published model, not a rebuild of it."""
        for alias in ("small", "cnn", "SMALL"):
            self.assertIsInstance(self.hs_cnn.build_model(alias), self.hs_cnn.Small1DCNN)
        self.assertIsInstance(self.hs_cnn.build_model("resnet"), self.hs_cnn.ConvNet1D)
        self.assertIsInstance(self.hs_cnn.build_model("tcn"), self.hs_cnn.ConvNet1D)

    def test_forward_accepts_2d_and_3d_input(self) -> None:
        for name in lme.CONV_ARCHITECTURES:
            with self.subTest(architecture=name):
                model = self.hs_cnn.build_model(name)
                model.eval()
                with self.torch.no_grad():
                    flat = model(self.torch.zeros(2, 8000))
                    channel_first = model(self.torch.zeros(2, 1, 8000))
                self.assertEqual(tuple(flat.shape), (2,))
                self.assertEqual(tuple(channel_first.shape), (2,))

    def test_residual_block_shortcut_only_when_needed(self) -> None:
        identity = self.hs_cnn.ResidualBlock1D(8, 8, kernel=3)
        projected = self.hs_cnn.ResidualBlock1D(8, 16, kernel=3, stride=2)
        self.assertIsInstance(identity.shortcut, self.torch.nn.Identity)
        parameter_count = sum(p.numel() for p in projected.parameters())
        # conv1 16*8*3 + BN 32 + conv2 16*16*3 + BN 32 + shortcut 16*8 + BN 32
        self.assertEqual(parameter_count, 384 + 32 + 768 + 32 + 128 + 32)

    def test_unknown_architecture_message_lists_the_valid_names(self) -> None:
        with self.assertRaises(ValueError):
            self.hs_cnn.build_model("gru")


class TestReportRebuild(unittest.TestCase):
    """``--report-only`` must re-render a report without changing a number."""

    def test_load_saved_arm_round_trips_the_pooled_probabilities(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            arm_dir = Path(directory) / "resnet"
            arm_dir.mkdir()
            (arm_dir / "report.json").write_text(
                json.dumps({"model": "resnet", "folds": 5}), encoding="utf-8"
            )
            (arm_dir / "patient-probabilities.csv").write_text(
                "fold,subject_id,label,probability\n"
                "0,1001,0,0.125\n"
                "0,1002,1,0.875\n",
                encoding="utf-8",
            )
            loaded = lme.load_saved_arm(Path(directory), "resnet")
        self.assertEqual(loaded["report"]["model"], "resnet")
        self.assertEqual(loaded["pooled"]["subject"], ["1001", "1002"])
        self.assertEqual(loaded["pooled"]["truth"], [0, 1])
        self.assertEqual(loaded["pooled"]["fold"], [0, 0])
        self.assertEqual(loaded["pooled"]["probability"], [0.125, 0.875])

    def test_protocol_record_pins_the_hyperparameters_and_split(self) -> None:
        args = lme.parse_args(["--epochs", "30", "--seed", "20261008"])
        protocol = lme.protocol_record(
            args,
            splits_file="data/processed/circor/splits.csv",
            fingerprint="1f212dea53b9184c91fe8184bf1e5e3e799083065cdc69f436b23b6fa5509a47",
            n_folds=5,
            windows_total=11728,
            subjects_total=874,
            threads="4",
            torch_threads=4,
        )
        self.assertEqual(protocol["hyperparameters"]["epochs_max"], 30)
        self.assertEqual(protocol["hyperparameters"]["seed"], 20261008)
        self.assertEqual(protocol["windows_total"], 11728)
        self.assertEqual(protocol["subjects_total"], 874)
        self.assertEqual(protocol["n_folds"], 5)
        self.assertEqual(protocol["threads"], "4")


class TestDecisionRule(unittest.TestCase):
    def test_rule_text_is_pinned_and_pre_registered(self) -> None:
        """The rule is one-sided and mentions both outcomes it can produce."""
        self.assertIn("half-width", lme.DECISION_RULE)
        self.assertIn("within patient-resampling noise", lme.DECISION_RULE)
        self.assertIn("no architecture is", lme.DECISION_RULE.replace("selected,", "selected,"))

    def test_no_trigger_when_the_gain_is_within_noise(self) -> None:
        control = fake_arm("small", 0.814, 0.770, 0.854)  # half-width 0.042
        resnet = fake_arm("resnet", 0.821, 0.780, 0.860)
        tcn = fake_arm("tcn", 0.806, 0.760, 0.848)
        decision = lme.decide_compute_ceiling(control, [resnet, tcn])
        self.assertTrue(decision["evaluated"])
        self.assertFalse(decision["triggered"])
        self.assertIsNone(decision["required_model"])
        self.assertAlmostEqual(decision["baseline_half_width"], 0.042)
        best = decision["best_arm"]
        self.assertEqual(best["model"], "resnet")  # best arm is the one compared
        self.assertFalse(best["exceeds_half_width"])
        self.assertLess(best["delta_vs_baseline"], decision["baseline_half_width"])

    def test_trigger_when_the_gain_exceeds_the_half_width(self) -> None:
        control = fake_arm("small", 0.814, 0.770, 0.854)  # half-width 0.042
        resnet = fake_arm("resnet", 0.889, 0.850, 0.925, parameters=463089, macs=32324448)
        tcn = fake_arm("tcn", 0.820, 0.780, 0.860)
        decision = lme.decide_compute_ceiling(control, [resnet, tcn])
        self.assertTrue(decision["triggered"])
        self.assertEqual(decision["required_model"]["model"], "resnet")
        self.assertEqual(decision["required_model"]["parameters"], 463089)
        self.assertGreater(decision["best_arm"]["delta_over_baseline_half_width"], 1.0)

    def test_the_rule_compares_the_best_arm(self) -> None:
        control = fake_arm("small", 0.814, 0.770, 0.854)
        resnet = fake_arm("resnet", 0.830, 0.790, 0.868)
        tcn = fake_arm("tcn", 0.870, 0.830, 0.905)
        decision = lme.decide_compute_ceiling(control, [resnet, tcn])
        self.assertEqual(decision["best_arm"]["model"], "tcn")
        self.assertTrue(decision["triggered"])

    def test_a_partial_smoke_run_can_never_trigger(self) -> None:
        control = fake_arm("small", 0.814, 0.770, 0.854, folds=1)
        resnet = fake_arm("resnet", 0.950, 0.900, 0.990, folds=1)
        decision = lme.decide_compute_ceiling(control, [resnet])
        self.assertFalse(decision["evaluated"])
        self.assertFalse(decision["triggered"])
        self.assertIn("folds", decision["reason"])

    def test_a_partial_arm_blocks_the_rule(self) -> None:
        control = fake_arm("small", 0.814, 0.770, 0.854)
        resnet = fake_arm("resnet", 0.950, 0.900, 0.990, folds=2)
        decision = lme.decide_compute_ceiling(control, [resnet])
        self.assertFalse(decision["evaluated"])
        self.assertIn("resnet", decision["reason"])

    def test_the_verdict_quotes_the_numbers(self) -> None:
        control = fake_arm("small", 0.814, 0.770, 0.854)
        resnet = fake_arm("resnet", 0.820, 0.779, 0.859)
        decision = lme.decide_compute_ceiling(control, [resnet])
        verdict = lme.verdict_text(decision)
        self.assertIn("within noise", verdict)
        self.assertIn("0.820", verdict)
        self.assertIn("0.814", verdict)
        self.assertIn("Raspberry Pi", verdict)

    def test_a_triggered_verdict_names_the_required_model(self) -> None:
        control = fake_arm("small", 0.814, 0.770, 0.854)
        resnet = fake_arm("resnet", 0.890, 0.851, 0.926, parameters=463089, macs=32324448)
        decision = lme.decide_compute_ceiling(control, [resnet])
        verdict = lme.verdict_text(decision)
        self.assertIn("compute ceiling is real", verdict)
        self.assertIn("463,089", verdict)


class TestProtocolConstants(unittest.TestCase):
    """The report cites these; pinning them catches an accidental protocol change."""

    def test_window_length_is_two_seconds_at_four_kilohertz(self) -> None:
        self.assertEqual(lme.WINDOW_SAMPLES, 8000)

    def test_small_budget_matches_the_published_report(self) -> None:
        report_path = REPO_ROOT / "data" / "reports" / "circor-cv-cnn-annotated" / "report.json"
        if not report_path.exists():  # pragma: no cover - report is in the repo
            self.skipTest(f"{report_path} not found")
        report = json.loads(report_path.read_text(encoding="utf-8"))
        # The published report does not carry params/MACs (train_cnn writes
        # those), so pin the accuracy claim the arms are compared against and the
        # protocol counts the experiment must reproduce.
        self.assertEqual(report["windows_total"], 11728)
        self.assertEqual(report["subjects_total"], 874)
        self.assertAlmostEqual(report["pooled_patient_at_0.5"]["auroc"], 0.814, places=3)
        self.assertAlmostEqual(report["aggregate"]["patient_auroc"]["mean"], 0.848, places=3)

    def test_frozen_split_fingerprint_is_the_one_in_the_report(self) -> None:
        splits_path = REPO_ROOT / "data" / "processed" / "circor" / "splits.csv"
        if not splits_path.exists():  # pragma: no cover - data is in the repo
            self.skipTest(f"{splits_path} not found")
        header = splits_path.read_text(encoding="utf-8").splitlines()
        fingerprints = [
            line.split("fingerprint:", 1)[1].strip()
            for line in header
            if line.startswith("#") and "fingerprint:" in line
        ]
        self.assertEqual(len(fingerprints), 1)
        self.assertRegex(fingerprints[0], SHA256_HEX)


if __name__ == "__main__":
    unittest.main()
