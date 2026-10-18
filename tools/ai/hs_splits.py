#!/usr/bin/env python3
"""Frozen, patient-level stratified fold assignment for heart-sound experiments.

Why this module exists
----------------------
``cross_validate.py`` used to re-derive the patient folds from ``--seed`` on
every run.  That is only reproducible while the code, the manifest row order and
the set of collected windows all stay identical, so two experiments run on
different dates could silently use different folds and the "same 5-fold
protocol" claim in a report was not verifiable.  This module makes the fold
assignment *data* instead of code: one CSV per dataset pins every subject to a
fold, and :func:`splits_fingerprint` gives a stable sha256 over that mapping so a
report can cite the exact split it used.

File format (``splits.csv``)
----------------------------
UTF-8, ``\\n`` line endings.  Optional provenance lines start with ``#``, then
the header and one row per patient::

    # dataset: synthetic-pcg
    # n_folds: 5
    # seed: 20261008
    # seed_protocol: 2026-10-08 / ADR-007
    subject_id,fold,label
    90000,0,1
    90001,3,0

The data columns are exactly ``subject_id,fold,label``.  ``fold`` is a 0-based
integer in ``[0, n_folds)``; ``label`` is the patient-level murmur label
(``1=Present``, ``0=Absent``) copied from the manifest so a reader can verify
stratification without the manifest.  The ``#`` preamble is metadata only:
:func:`splits_fingerprint` hashes the ``subject_id -> fold`` mapping alone, so
adding or editing metadata never changes the fingerprint.

Compatibility with the seed-derived protocol
--------------------------------------------
:func:`patient_folds` applies exactly the rule of
``cross_validate.stratified_folds``: sort the subjects of each label, shuffle
with ``random.Random(seed)``, then assign ``position % n_folds``.  The default
seed is therefore not an arbitrary date: ``DEFAULT_SEED = 20261008`` is the seed
of the 2026-10-08 CirCor patient-level 5-fold run recorded in ADR-007, so a
frozen file created with the defaults re-creates the exact folds behind the
published numbers (logreg 0.775/0.789, CNN 0.811/0.834 patient-level AUROC)
instead of silently starting a new protocol.  ``DEFAULT_SEED`` must stay equal
to ``cross_validate.py --seed`` and ``train_mfcc.py --seed``; the equivalence is
tested both at unit level and through ``freeze_splits.py`` in
``tests/python/test_hs_splits.py``.

Balance properties
------------------
Per label, the round-robin rule gives every fold ``floor(n_label / n_folds)`` or
``ceil(n_label / n_folds)`` patients, so each fold's per-class count is always
within 1 of the ideal ``n_label / n_folds``.  The fold *total* is not rotated
between classes (both classes start at fold 0), so for binary labels fold totals
can differ by up to 2 patients: with the CirCor cohort (874 labelled patients,
5 folds, roughly 175 test patients per fold) that worst case is about 1 %.  The
exact reproduction of the historical protocol was judged more valuable than
removing that 1 % (see ``docs/engineering-decision-register.md`` ADR-007).
Rotating each class by the size of the classes before it would balance the
totals to within 1 patient, but it would no longer reproduce any seed-derived
fold set.

All functions accept a mapping ``{subject_id: fold}``; an iterable of
``(subject_id, fold)`` pairs is also accepted where a malformed file could
contain duplicates, so duplicate rows can be detected instead of silently
collapsed by ``dict``.
"""

from __future__ import annotations

import csv
import hashlib
import operator
import random
from collections.abc import Mapping
from pathlib import Path

#: Default number of folds of the project-wide patient-level protocol.
DEFAULT_N_FOLDS = 5

#: Seed of the project-wide patient-level protocol.  It is deliberately equal to
#: the seed of the 2026-10-08 CirCor run recorded in ADR-007 and to the
#: ``cross_validate.py`` / ``train_mfcc.py`` ``--seed`` default, so freezing with
#: the defaults reproduces the published folds instead of starting a new one.
DEFAULT_SEED = 20261008

#: Human-readable provenance of :data:`DEFAULT_SEED`, written to reports.
SEED_PROTOCOL = "2026-10-08 / ADR-007"

#: Data columns of ``splits.csv``, in file order.
SPLIT_FIELDS = ("subject_id", "fold", "label")

_METADATA_PREFIX = "#"


def patient_folds(
    subject_labels: dict[str, int],
    n_folds: int = DEFAULT_N_FOLDS,
    seed: int = DEFAULT_SEED,
) -> dict[str, int]:
    """Assign every patient to exactly one stratified fold.

    Parameters
    ----------
    subject_labels:
        Patient-level labels ``{subject_id: label}``.  Every subject must appear
        once; the label is the patient-level target (normally ``0``/``1``).
    n_folds:
        Number of folds, ``>= 2``.
    seed:
        Seed of the deterministic shuffling; equal inputs always produce the
        same assignment.

    Returns
    -------
    dict[str, int]
        ``{subject_id: fold}`` with fold indices ``0 .. n_folds - 1``.  For the
        same ``(subject_labels, n_folds, seed)`` the mapping is identical to
        ``cross_validate.stratified_folds`` (see the module docstring).

    Raises
    ------
    ValueError
        If the cohort is empty, ``n_folds < 2`` or a label is not an integer.
    """
    n_folds = _as_int(n_folds)
    if n_folds < 2:
        raise ValueError(f"n_folds must be >= 2, got {n_folds}")
    labels = _as_label_mapping(subject_labels)
    if not labels:
        raise ValueError("subject_labels is empty; there is nothing to stratify")

    by_label: dict[int, list[str]] = {}
    for subject, label in labels.items():
        by_label.setdefault(label, []).append(subject)

    rng = random.Random(seed)
    folds: dict[str, int] = {}
    # Sorted label order keeps the RNG sequence independent of dict insertion
    # order and matches cross_validate.stratified_folds for labels (0, 1).
    for label in sorted(by_label):
        subjects = sorted(by_label[label])
        rng.shuffle(subjects)
        for position, subject in enumerate(subjects):
            folds[subject] = position % n_folds
    return folds


def seed_protocol(seed: int = DEFAULT_SEED) -> str:
    """Return the provenance label of ``seed`` for summaries and reports.

    ``DEFAULT_SEED`` reproduces the 2026-10-08 ADR-007 fold assignment and keeps
    that label; any other seed is explicitly marked as a new, non-ADR-007
    protocol so a frozen file can never be mistaken for the published one.
    """
    seed = _as_int(seed)
    if seed == DEFAULT_SEED:
        return SEED_PROTOCOL
    return f"custom seed {seed} (not ADR-007)"


def fold_sets(folds: dict[str, int], n_folds: int) -> list[set[str]]:
    """Invert ``{subject: fold}`` into ``[{subjects of fold 0}, ...]``."""
    n_folds = _as_int(n_folds)
    if n_folds < 1:
        raise ValueError(f"n_folds must be >= 1, got {n_folds}")
    groups: list[set[str]] = [set() for _ in range(n_folds)]
    for subject, fold in _normalise_folds(folds).items():
        if fold < 0 or fold >= n_folds:
            raise ValueError(
                f"unknown fold index {fold} for subject {subject!r}; "
                f"expected 0 <= fold < {n_folds}"
            )
        groups[fold].add(subject)
    return groups


def fold_label_counts(
    folds: dict[str, int], subject_labels: dict[str, int], n_folds: int
) -> dict[int, dict[int, int]]:
    """Count patients per fold and per label.

    Returns ``{fold: {label: count}}`` for every fold in ``0 .. n_folds - 1``
    (a fold without patients yields an empty dict).
    """
    n_folds = _as_int(n_folds)
    if n_folds < 1:
        raise ValueError(f"n_folds must be >= 1, got {n_folds}")
    labels = _as_label_mapping(subject_labels)
    normalised = _normalise_folds(folds)
    counts: dict[int, dict[int, int]] = {fold: {} for fold in range(n_folds)}
    for subject, fold in normalised.items():
        if subject not in labels:
            raise ValueError(f"subject {subject!r} has a fold but no label")
        if fold < 0 or fold >= n_folds:
            raise ValueError(
                f"unknown fold index {fold} for subject {subject!r}; "
                f"expected 0 <= fold < {n_folds}"
            )
        per_label = counts[fold]
        per_label[labels[subject]] = per_label.get(labels[subject], 0) + 1
    return counts


def validate_splits(
    folds: dict[str, int] | list[tuple[str, int]],
    subject_labels: dict[str, int],
    n_folds: int,
    *,
    strict_extra: bool = False,
) -> None:
    """Check that a fold assignment is a complete, usable protocol.

    Raises ``ValueError`` when

    - a subject appears twice (only reachable when ``folds`` is a pair sequence),
    - a fold index is not an integer or lies outside ``[0, n_folds)``,
    - a subject of ``subject_labels`` has no fold,
    - ``folds`` contains a subject that ``subject_labels`` does not know and
      ``strict_extra`` is true, or
    - the assignment is empty.

    Extra frozen subjects are tolerated by default because a run can legitimately
    filter out every window of a patient (``--window-selection annotated``, a
    silence threshold); those patients simply contribute nothing.  Pass
    ``strict_extra=True`` when the file must match the manifest exactly, as
    ``freeze_splits.py`` does before writing.

    Returns ``None``; the function is idempotent and has no side effects.
    """
    n_folds = _as_int(n_folds)
    if n_folds < 2:
        raise ValueError(f"n_folds must be >= 2, got {n_folds}")
    labels = _as_label_mapping(subject_labels)
    if not labels:
        raise ValueError("subject_labels is empty; there is nothing to validate")
    normalised = _normalise_folds(folds)
    if not normalised:
        raise ValueError("fold assignment is empty")

    for subject, fold in normalised.items():
        if fold < 0 or fold >= n_folds:
            raise ValueError(
                f"unknown fold index {fold} for subject {subject!r}; "
                f"expected 0 <= fold < {n_folds}"
            )

    if strict_extra:
        unknown = sorted(set(normalised) - set(labels))
        if unknown:
            raise ValueError(
                f"{len(unknown)} subject(s) in the fold assignment are missing "
                f"from subject_labels (first: {unknown[:5]})"
            )
    missing = sorted(set(labels) - set(normalised))
    if missing:
        raise ValueError(
            f"{len(missing)} subject(s) have no fold assignment "
            f"(first: {missing[:5]})"
        )


def splits_fingerprint(folds: dict[str, int] | list[tuple[str, int]]) -> str:
    """Return the sha256 hex digest of the sorted ``subject_id -> fold`` map.

    The payload is ``"<subject_id>\\t<fold>\\n"`` for subjects sorted by id, so
    the digest depends only on the assignment (not on metadata, file order or
    line endings).  Cite this value in every report that used the split.
    """
    normalised = _normalise_folds(folds)
    payload = "".join(
        f"{subject}\t{fold}\n" for subject, fold in sorted(normalised.items())
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def write_splits(
    path,
    folds: dict[str, int] | list[tuple[str, int]],
    subject_labels: dict[str, int],
    extra_metadata: dict | None = None,
) -> Path:
    """Write ``splits.csv`` and return its path.

    ``extra_metadata`` is written as ``# key: value`` provenance lines before the
    header (sorted by key).  Values must be single-line: newlines would corrupt
    the CSV, so they raise ``ValueError`` instead of being silently rewritten.

    A fold assignment is only written when it covers exactly the subjects of
    ``subject_labels`` and its fold indices are contiguous from 0, so a typo
    cannot produce a partially valid frozen protocol.
    """
    path = Path(path)
    labels = _as_label_mapping(subject_labels)
    normalised = _normalise_folds(folds)
    if not normalised:
        raise ValueError("fold assignment is empty; refusing to write an empty split file")
    missing = sorted(set(normalised) - set(labels))
    if missing:
        raise ValueError(
            f"{len(missing)} subject(s) have a fold but no label "
            f"(first: {missing[:5]}); pass the full subject_labels mapping"
        )
    n_folds = max(normalised.values()) + 1
    present = set(normalised.values())
    if present != set(range(n_folds)):
        raise ValueError(
            f"fold indices must be contiguous starting at 0, got {sorted(present)}"
        )
    # coverage is exact by construction, so this only catches duplicate/unknown
    # fold indices via the normalisation above.
    validate_splits(normalised, labels, n_folds)
    metadata = _normalise_metadata(extra_metadata)

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        for key in sorted(metadata):
            handle.write(f"{_METADATA_PREFIX} {key}: {metadata[key]}\n")
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(SPLIT_FIELDS)
        for subject in sorted(normalised):
            writer.writerow([subject, normalised[subject], labels[subject]])
    return path


def load_splits(path) -> dict[str, int]:
    """Read ``splits.csv`` and return ``{subject_id: fold}``.

    ``#`` comment lines and blank lines are ignored, a UTF-8 BOM is tolerated,
    and rows are returned sorted by ``subject_id``.  Duplicate subjects, a
    missing/renamed header, malformed integers and short rows raise
    ``ValueError``; nothing is silently repaired.
    """
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        raise ValueError(f"{path}: splits file not found") from None
    data_lines = [
        line
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith(_METADATA_PREFIX)
    ]
    if not data_lines:
        raise ValueError(f"{path}: empty splits file (no header)")

    rows = list(csv.reader(data_lines))
    header = [field.strip() for field in rows[0]]
    expected = list(SPLIT_FIELDS)
    if header != expected:
        raise ValueError(
            f"{path}: expected header {','.join(expected)}, got {','.join(header)}"
        )

    folds: dict[str, int] = {}
    for row_number, row in enumerate(rows[1:], start=2):
        if len(row) != len(SPLIT_FIELDS):
            raise ValueError(
                f"{path}: row {row_number} has {len(row)} fields, "
                f"expected {len(SPLIT_FIELDS)}"
            )
        subject = row[0].strip()
        if not subject:
            raise ValueError(f"{path}: row {row_number} has an empty subject_id")
        if subject in folds:
            raise ValueError(
                f"{path}: row {row_number} repeats subject {subject!r}"
            )
        folds[subject] = _as_int(row[1], what=f"fold of row {row_number}")
        _as_int(row[2], what=f"label of row {row_number}")
    if not folds:
        raise ValueError(f"{path}: header present but no patient rows")
    return {subject: folds[subject] for subject in sorted(folds)}


def _as_int(value, what: str = "value") -> int:
    """Convert to ``int`` without truncating floats or accepting booleans."""
    if isinstance(value, bool):
        raise ValueError(f"{what} must be an integer, got a bool ({value!r})")
    if isinstance(value, str):
        text = value.strip()
        try:
            return int(text)
        except ValueError:
            raise ValueError(f"{what} must be an integer, got {value!r}") from None
    try:
        return operator.index(value)
    except TypeError:
        raise ValueError(f"{what} must be an integer, got {value!r}") from None


def _as_label_mapping(subject_labels) -> dict[str, int]:
    """Normalise ``{subject: label}`` keys to ``str`` and labels to ``int``."""
    try:
        items = subject_labels.items()
    except AttributeError:
        raise ValueError("subject_labels must be a mapping {subject_id: label}") from None
    labels: dict[str, int] = {}
    for subject, label in items:
        subject = str(subject).strip()
        if not subject:
            raise ValueError("empty subject_id in subject_labels")
        if subject in labels:
            raise ValueError(f"duplicate subject in subject_labels: {subject!r}")
        labels[subject] = _as_int(label, what=f"label of subject {subject!r}")
    return labels


def _normalise_folds(folds) -> dict[str, int]:
    """Return ``{subject: fold}``; reject duplicates when given a pair sequence."""
    if isinstance(folds, Mapping):
        items = list(folds.items())
    else:
        try:
            items = list(folds)
        except TypeError:
            raise ValueError(
                "folds must be a mapping {subject_id: fold} or an iterable of "
                "(subject_id, fold) pairs"
            ) from None
    normalised: dict[str, int] = {}
    for item in items:
        if isinstance(item, (str, bytes)):
            raise ValueError(
                "folds must be a mapping {subject_id: fold} or an iterable of "
                "(subject_id, fold) pairs"
            )
        try:
            subject, fold = item
        except (TypeError, ValueError):
            raise ValueError(
                f"fold entries must be (subject_id, fold) pairs, got {item!r}"
            ) from None
        subject = str(subject).strip()
        if not subject:
            raise ValueError("empty subject_id in fold assignment")
        if subject in normalised:
            raise ValueError(f"duplicate subject in fold assignment: {subject!r}")
        normalised[subject] = _as_int(fold, what=f"fold of subject {subject!r}")
    return normalised


def _normalise_metadata(extra_metadata) -> dict[str, str]:
    """Validate the provenance preamble: string keys/values, no newlines."""
    if extra_metadata is None:
        return {}
    try:
        items = extra_metadata.items()
    except AttributeError:
        raise ValueError("extra_metadata must be a mapping") from None
    metadata: dict[str, str] = {}
    for key, value in items:
        key = str(key).strip()
        value = str(value).strip()
        if not key:
            raise ValueError("extra_metadata contains an empty key")
        for text, what in ((key, "metadata key"), (value, "metadata value")):
            if "\n" in text or "\r" in text:
                raise ValueError(f"{what} {text!r} must be a single line")
        if _METADATA_PREFIX in key:
            raise ValueError(
                f"metadata key {key!r} must not contain {_METADATA_PREFIX!r}"
            )
        metadata[key] = value
    return metadata
