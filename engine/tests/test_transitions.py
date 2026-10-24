"""Black-box unit tests for engine/transitions.py (SPEC T2, T3, 5.4, 5.5, ADR-004, ADR-005).

Signals are hand-made per-cell time series.  Each expected (frame, sign) list
was traced by hand through the run-tracking semantics in SPEC 5.4: a run is a
monotone stretch between adjacent extrema, plateaus and sub-tolerance wobbles
never end a run, and a transition is timestamped at the frame where the run
reached its extremum.
"""
from __future__ import annotations

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from engine.constants import RUN_REVERSAL_TOLERANCE
from engine.transitions import (
    FlashPairer,
    RunTracker,
    Transition,
    detect_transitions_1d,
    flash_frames_1d,
    general_rule,
    red_rule_legacy,
    red_rule_wcag22,
)

_PROPERTY = settings(deadline=None, derandomize=True, max_examples=40, database=None)

# Chromaticities used by the red-rule tests (ADR-011): sRGB red and the D65 white point.
_RED_UV = (0.4507, 0.5229)
_D65_UV = (0.1978, 0.4683)


def _t(result: list) -> list[tuple[int, int]]:
    """Normalise a (frame, sign) list to plain Python ints for exact comparison."""
    return [(int(frame), int(sign)) for frame, sign in result]


def _f(result: list) -> list[int]:
    """Normalise a frame list to plain Python ints."""
    return [int(frame) for frame in result]


def _uv_aux(u: list[float], v: list[float]) -> dict[str, np.ndarray]:
    """Auxiliary arrays for the WCAG 2.2 red rule."""
    return {"u": np.asarray(u, dtype=np.float64), "v": np.asarray(v, dtype=np.float64)}


def _run_tracker(signals: np.ndarray, rule=general_rule, tolerance: float = 0.005,
                 aux: dict[str, np.ndarray] | None = None) -> list[list[tuple[int, int]]]:
    """Drive RunTracker over signals[T, N] and return per-cell (frame, sign) lists."""
    n_frames, n_cells = signals.shape
    tracker = RunTracker(n_cells, rule, tolerance)
    per_cell: list[list[tuple[int, int]]] = [[] for _ in range(n_cells)]
    emitted: list[Transition] = []
    for t in range(n_frames):
        aux_t = {} if aux is None else {k: v[t] for k, v in aux.items()}
        emitted.extend(tracker.update(t, signals[t], aux_t))
    emitted.extend(tracker.finish(n_frames - 1))
    for tr in emitted:
        for cell in np.asarray(tr.cells).tolist():
            per_cell[int(cell)].append((int(tr.frame), int(tr.sign)))
    return [sorted(cell_list) for cell_list in per_cell]


# ---------------------------------------------------------------------------
# General rule through the 1-D helper: thresholds (SPEC T2)
# ---------------------------------------------------------------------------


def test_delta_of_exactly_0_10_is_a_transition() -> None:
    """SPEC T2: '10% or more' is inclusive, so a 0.10 step up then down gives two transitions."""
    assert _t(detect_transitions_1d(np.array([0.0, 0.10, 0.0]))) == [(1, 1), (2, -1)]


def test_delta_of_0_0999_is_not_a_transition() -> None:
    """SPEC T2: 0.0999 is below '10% or more', so nothing is emitted."""
    assert _t(detect_transitions_1d(np.array([0.0, 0.0999, 0.0]))) == []


def test_both_states_at_or_above_0_80_give_nothing() -> None:
    """SPEC T2: 'the darker image is below 0.80' is exclusive; 0.85 <-> 1.0 (delta 0.15) does not count."""
    assert _t(detect_transitions_1d(np.array([0.85, 1.0, 0.85]))) == []
    assert _t(detect_transitions_1d(np.array([0.85, 0.95, 0.85]))) == []


def test_dark_state_of_0_79_counts() -> None:
    """SPEC T2: 0.79 is below 0.80, so 0.79 <-> 0.95 (delta 0.16) gives two transitions."""
    assert _t(detect_transitions_1d(np.array([0.79, 0.95, 0.79]))) == [(1, 1), (2, -1)]


def test_all_sub_threshold_wiggles_give_nothing() -> None:
    """SPEC 5.4: runs that do not meet the rule produce no transition at all."""
    assert _t(detect_transitions_1d(np.array([0.0, 0.05, 0.0, 0.05, 0.0]))) == []


def test_constant_signal_gives_nothing() -> None:
    """SPEC 5.4: a flat signal has no extrema, hence no runs and no transitions."""
    assert _t(detect_transitions_1d(np.full(5, 0.5))) == []


def test_two_frame_step_is_one_rise_at_frame_one() -> None:
    """ADR-004: the shortest possible run (two frames) is evaluated at its end frame."""
    assert _t(detect_transitions_1d(np.array([0.0, 1.0]))) == [(1, 1)]


# ---------------------------------------------------------------------------
# Monotone runs, plateaus, wobbles (SPEC 5.4, ADR-004)
# ---------------------------------------------------------------------------


def _ramp_plateau_ramp() -> np.ndarray:
    """10 frames 0 -> 0.5, 10 flat frames at 0.5, 10 frames 0.5 -> 0 (30 frames)."""
    return np.concatenate([np.linspace(0.0, 0.5, 10), np.full(10, 0.5), np.linspace(0.5, 0.0, 10)])


def test_ramp_is_timestamped_at_its_end() -> None:
    """ADR-004: 'a ramp counts once when it ends' - rise at frame 9, fall at frame 29."""
    assert _t(detect_transitions_1d(_ramp_plateau_ramp())) == [(9, 1), (29, -1)]


def test_plateau_inside_a_ramp_does_not_split_it() -> None:
    """SPEC 5.4: 'plateaus do not end a run' - one rise ending at frame 7, one fall at 8."""
    signal = np.array([0.0, 0.1, 0.2, 0.2, 0.2, 0.3, 0.4, 0.5, 0.0])
    assert _t(detect_transitions_1d(signal)) == [(7, 1), (8, -1)]


def test_sub_tolerance_wobble_does_not_split_a_ramp() -> None:
    """SPEC 5.4 / RUN_REVERSAL_TOLERANCE: a 0.002 dip (< 0.005) is a plateau, so the ramp is one rise."""
    signal = np.array([0.0, 0.05, 0.10, 0.15, 0.20, 0.198, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50])
    assert _t(detect_transitions_1d(signal)) == [(11, 1)]


def test_wobble_above_tolerance_splits_a_ramp_into_two_rises() -> None:
    """SPEC 5.4: a 0.05 dip (> 0.005) ends the run: rise 0->0.20 at frame 4, the dip itself is below
    0.10 (no transition but it resets the start), then rise 0.15->0.50 at frame 11: two rises, no flash."""
    signal = np.array([0.0, 0.05, 0.10, 0.15, 0.20, 0.15, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50])
    assert _t(detect_transitions_1d(signal)) == [(4, 1), (11, 1)]
    assert _f(flash_frames_1d(signal)) == []


def test_tolerance_parameter_decides_whether_a_wobble_splits() -> None:
    """RUN_REVERSAL_TOLERANCE: the same 0.008 dip splits at tolerance 0.005 and is ignored at 0.01."""
    signal = np.array([0.0, 0.2, 0.192, 0.5, 0.0])
    assert _t(detect_transitions_1d(signal, tolerance=0.005)) == [(1, 1), (3, 1), (4, -1)]
    assert _t(detect_transitions_1d(signal, tolerance=0.01)) == [(3, 1), (4, -1)]


def test_open_run_at_the_end_is_evaluated_by_finish() -> None:
    """ADR-004: 'a run still open at the last frame ends at the last frame' - [0, 0, 1] is a rise at 2."""
    assert _t(detect_transitions_1d(np.array([0.0, 0.0, 1.0]))) == [(2, 1)]


def test_default_tolerance_matches_the_constant() -> None:
    """RUN_REVERSAL_TOLERANCE['L'] is 0.005: a 0.004 dip is ignored by the helper's default."""
    assert RUN_REVERSAL_TOLERANCE["L"] == 0.005
    signal = np.array([0.0, 0.3, 0.296, 0.6, 0.0])
    assert _t(detect_transitions_1d(signal)) == [(3, 1), (4, -1)]


# ---------------------------------------------------------------------------
# Flash pairing through the 1-D helper (SPEC 5.5, ADR-005)
# ---------------------------------------------------------------------------


def test_square_wave_with_four_transitions_flashes_at_second_and_fourth() -> None:
    """ADR-005: pairs are (1st+2nd, 3rd+4th); each flash is timed at its second transition."""
    signal = np.array([0.0, 1.0, 0.0, 1.0, 0.0])
    assert _t(detect_transitions_1d(signal)) == [(1, 1), (2, -1), (3, 1), (4, -1)]
    assert _f(flash_frames_1d(signal)) == [2, 4]


def test_eight_transitions_give_four_flashes() -> None:
    """SPEC 5.5: 'a 4 Hz square wave therefore yields 8 transitions and 4 flashes'."""
    signal = np.array([0.0, 1.0] * 4 + [0.0])
    assert len(detect_transitions_1d(signal)) == 8
    assert _f(flash_frames_1d(signal)) == [2, 4, 6, 8]


def test_three_transitions_give_one_flash() -> None:
    """ADR-005: non-overlapping pairing - the unpaired third transition is not a flash."""
    signal = np.array([0.0, 1.0, 0.0, 1.0])
    assert len(detect_transitions_1d(signal)) == 3
    assert _f(flash_frames_1d(signal)) == [2]


def test_rise_tiny_fall_rise_fall_is_exactly_one_flash() -> None:
    """ADR-005: two same-sign rises (the 0.05 fall between them is no transition) pair with the
    final fall only once - one flash at frame 4."""
    signal = np.array([0.0, 0.5, 0.45, 0.9, 0.0])
    assert _t(detect_transitions_1d(signal)) == [(1, 1), (3, 1), (4, -1)]
    assert _f(flash_frames_1d(signal)) == [4]


def test_single_transition_is_not_a_flash() -> None:
    """SPEC 3.2: a flash is 'a pair of opposing changes'; one step up alone is not a flash."""
    assert _f(flash_frames_1d(np.array([0.0, 1.0, 1.0, 1.0]))) == []


# ---------------------------------------------------------------------------
# Rule functions called directly (vectorised over cells)
# ---------------------------------------------------------------------------


def test_general_rule_boundaries_vectorised() -> None:
    """SPEC T2: |b-a| >= 0.10 (inclusive) and min(a, b) < 0.80 (exclusive), evaluated per cell."""
    # Cell 4 is a FALL of exactly 0.10.  The pair must differ by exactly 0.10 in float64:
    # 0.2 - 0.1 == 0.1 holds, whereas 0.5 - 0.4 == 0.09999999999999998 and would not be a
    # transition (the engine applies the threshold exactly as written, with no fudge factor).
    a = np.array([0.0, 0.0, 0.85, 0.79, 0.2])
    b = np.array([0.10, 0.0999, 1.0, 0.95, 0.1])
    out = general_rule(a, b, {}, {})
    assert out.dtype == np.bool_
    np.testing.assert_array_equal(out, [True, False, False, True, True])
    # The rule is symmetric: swapping start and end states changes nothing.
    np.testing.assert_array_equal(general_rule(b, a, {}, {}), out)


def test_red_rule_wcag22_boundaries_vectorised() -> None:
    """SPEC T3: max(ratio) >= 0.8 (inclusive) and uv distance > 0.2 (exclusive), per cell."""
    ratio_a = np.array([1.0, 1.0, 0.7, 0.8, 0.79, 1.0, 0.333])
    ratio_b = np.array([0.333, 0.333, 0.3, 0.3, 0.3, 0.5, 1.0])
    aux_a = _uv_aux(
        u=[_RED_UV[0], _RED_UV[0], _RED_UV[0], 0.0, 0.0, 0.0, _D65_UV[0]],
        v=[_RED_UV[1], _RED_UV[1], _RED_UV[1], 0.4, 0.4, 0.4, _D65_UV[1]],
    )
    aux_b = _uv_aux(
        u=[_D65_UV[0], 0.3507, _D65_UV[0], 0.3, 0.3, 0.2, _RED_UV[0]],
        v=[_D65_UV[1], _RED_UV[1], _D65_UV[1], 0.4, 0.4, 0.4, _RED_UV[1]],
    )
    out = red_rule_wcag22(ratio_a, ratio_b, aux_a, aux_b)
    assert out.dtype == np.bool_
    np.testing.assert_array_equal(
        out,
        [
            True,   # red <-> grey: saturated, distance 0.259 > 0.2
            False,  # saturated but the uv shift is only 0.1
            False,  # distance 0.3 but neither state reaches 0.8
            True,   # ratio exactly 0.8 is saturated ('greater than or equal to')
            False,  # ratio 0.79 is not
            False,  # distance exactly 0.2 is not 'more than 0.2'
            True,   # saturated state at the END of the run also counts ('to or from')
        ],
    )


def test_red_rule_legacy_boundaries_vectorised() -> None:
    """ADR-001 legacy: max(ratio) >= 0.8 and |RV_b - RV_a| > 20 (exclusive), per cell."""
    rv_a = np.array([320.0, 320.0, 320.0, 320.0, 280.0])
    rv_b = np.array([160.0, 300.0, 299.5, 160.0, 320.0])
    aux_a = {"ratio": np.array([1.0, 1.0, 1.0, 0.79, 0.5])}
    aux_b = {"ratio": np.array([1.0, 1.0, 1.0, 0.79, 1.0])}
    out = red_rule_legacy(rv_a, rv_b, aux_a, aux_b)
    assert out.dtype == np.bool_
    np.testing.assert_array_equal(
        out,
        [
            True,   # delta 160 > 20, both states saturated
            False,  # delta exactly 20 is not 'greater than 20'
            True,   # delta 20.5 is
            False,  # delta 160 but neither state reaches ratio 0.8
            True,   # delta 40 and the END state is saturated ('to or from')
        ],
    )


# ---------------------------------------------------------------------------
# Red rules through the 1-D helper (SPEC T3, ADR-001)
# ---------------------------------------------------------------------------


def test_red_wcag22_red_grey_toggle_gives_transitions_and_a_flash() -> None:
    """SPEC T3: ratio 1.0 <-> 0.333 with a 0.259 uv distance is a red transition each way."""
    ratio = np.array([1.0, 0.333, 1.0])
    aux = _uv_aux(u=[_RED_UV[0], _D65_UV[0], _RED_UV[0]], v=[_RED_UV[1], _D65_UV[1], _RED_UV[1]])
    found = detect_transitions_1d(
        ratio, rule=red_rule_wcag22, tolerance=RUN_REVERSAL_TOLERANCE["ratio"], aux=aux
    )
    assert _t(found) == [(1, -1), (2, 1)]
    assert _f(flash_frames_1d(ratio, rule=red_rule_wcag22,
                              tolerance=RUN_REVERSAL_TOLERANCE["ratio"], aux=aux)) == [2]


def test_red_wcag22_small_uv_shift_gives_nothing() -> None:
    """SPEC T3: the same ratio swing with only a 0.1 uv distance is not 'more than 0.2'."""
    ratio = np.array([1.0, 0.333, 1.0])
    aux = _uv_aux(u=[_RED_UV[0], 0.3507, _RED_UV[0]], v=[_RED_UV[1], _RED_UV[1], _RED_UV[1]])
    assert _t(detect_transitions_1d(ratio, rule=red_rule_wcag22, aux=aux)) == []


def test_red_wcag22_without_a_saturated_state_gives_nothing() -> None:
    """SPEC T3: ratio 0.7 <-> 0.3 never reaches 0.8, so a 0.3 uv distance does not count."""
    ratio = np.array([0.7, 0.3, 0.7])
    aux = _uv_aux(u=[0.0, 0.3, 0.0], v=[0.4, 0.4, 0.4])
    assert _t(detect_transitions_1d(ratio, rule=red_rule_wcag22, aux=aux)) == []


def test_red_legacy_320_to_160_gives_transitions() -> None:
    """ADR-001 legacy: RV 320 <-> 160 (delta 160 > 20) with ratio 1.0 is a red transition each way."""
    rv = np.array([320.0, 160.0, 320.0])
    aux = {"ratio": np.ones(3)}
    found = detect_transitions_1d(
        rv, rule=red_rule_legacy, tolerance=RUN_REVERSAL_TOLERANCE["rv"], aux=aux
    )
    assert _t(found) == [(1, -1), (2, 1)]


def test_red_legacy_delta_of_exactly_20_gives_nothing() -> None:
    """ADR-001 legacy: 'change > 20' is strict, so RV 320 <-> 300 is not a transition."""
    rv = np.array([320.0, 300.0, 320.0])
    aux = {"ratio": np.ones(3)}
    found = detect_transitions_1d(
        rv, rule=red_rule_legacy, tolerance=RUN_REVERSAL_TOLERANCE["rv"], aux=aux
    )
    assert _t(found) == []


# ---------------------------------------------------------------------------
# RunTracker vectorised over N cells (SPEC 5.4)
# ---------------------------------------------------------------------------


def test_run_tracker_matches_1d_helper_per_cell() -> None:
    """SPEC 5.4: the vectorised tracker gives, per cell, exactly what the 1-D helper gives."""
    square = np.array([0.0, 1.0] * 6)
    ramp = np.array([0.0, 0.1, 0.2, 0.2, 0.2, 0.3, 0.4, 0.5, 0.5, 0.3, 0.1, 0.0])
    step = np.array([0.5] * 6 + [0.9] * 3 + [0.5] * 3)
    signals = np.stack([square, ramp, step], axis=1)  # [T=12, N=3]
    per_cell = _run_tracker(signals)
    for cell in range(3):
        assert per_cell[cell] == sorted(_t(detect_transitions_1d(signals[:, cell])))
    # Sanity: the square wave cell really has 11 transitions, the ramp cell 2.
    assert len(per_cell[0]) == 11
    assert len(per_cell[1]) == 2


def test_run_tracker_groups_cells_sharing_frame_and_sign() -> None:
    """Transition.cells: cells that end the same kind of run at the same frame share one record."""
    square = np.array([0.0, 1.0, 0.0, 1.0, 0.0])
    signals = np.stack([square, square], axis=1)
    tracker = RunTracker(2, general_rule, 0.005)
    emitted: list[Transition] = []
    for t in range(signals.shape[0]):
        emitted.extend(tracker.update(t, signals[t], {}))
    emitted.extend(tracker.finish(signals.shape[0] - 1))
    assert [(int(tr.frame), int(tr.sign)) for tr in emitted] == [(1, 1), (2, -1), (3, 1), (4, -1)]
    for tr in emitted:
        assert sorted(np.asarray(tr.cells).tolist()) == [0, 1]


def test_run_tracker_emits_transitions_timestamped_before_the_current_frame() -> None:
    """ADR-004: a rise whose plateau lasts to frame 20 is emitted at update(21) but timestamped 9."""
    signal = _ramp_plateau_ramp()
    tracker = RunTracker(1, general_rule, 0.005)
    emitted_at: dict[int, list[tuple[int, int]]] = {}
    for t in range(signal.shape[0]):
        out = tracker.update(t, signal[t : t + 1], {})
        if out:
            emitted_at[t] = [(int(tr.frame), int(tr.sign)) for tr in out]
    final = tracker.finish(signal.shape[0] - 1)
    assert emitted_at == {21: [(9, 1)]}
    assert [(int(tr.frame), int(tr.sign)) for tr in final] == [(29, -1)]


def test_run_tracker_finish_returns_only_open_qualifying_runs() -> None:
    """ADR-004: finish() emits the run still open at the last frame; nothing for a flat cell."""
    signals = np.array([[0.0, 0.5], [0.0, 0.5], [1.0, 0.5]])  # cell 0 steps at frame 2, cell 1 flat
    tracker = RunTracker(2, general_rule, 0.005)
    during = []
    for t in range(3):
        during.extend(tracker.update(t, signals[t], {}))
    assert during == []
    final = tracker.finish(2)
    assert len(final) == 1
    assert int(final[0].frame) == 2
    assert int(final[0].sign) == 1
    assert np.asarray(final[0].cells).tolist() == [0]


def test_run_tracker_finish_with_nothing_open_returns_empty() -> None:
    """ADR-004: a signal that already completed all its runs has nothing left for finish()."""
    tracker = RunTracker(1, general_rule, 0.005)
    for t in range(4):
        tracker.update(t, np.full(1, 0.3), {})
    assert list(tracker.finish(3)) == []


def test_run_tracker_red_rule_uses_aux_per_cell() -> None:
    """SPEC T3 vectorised: only the cell toggling red <-> grey yields red transitions."""
    ratio = np.array([[1.0, 0.333], [0.333, 0.333], [1.0, 0.333], [0.333, 0.333]])  # [T=4, N=2]
    u = np.array([[_RED_UV[0], _D65_UV[0]], [_D65_UV[0]] * 2, [_RED_UV[0], _D65_UV[0]], [_D65_UV[0]] * 2])
    v = np.array([[_RED_UV[1], _D65_UV[1]], [_D65_UV[1]] * 2, [_RED_UV[1], _D65_UV[1]], [_D65_UV[1]] * 2])
    per_cell = _run_tracker(ratio, rule=red_rule_wcag22,
                            tolerance=RUN_REVERSAL_TOLERANCE["ratio"], aux={"u": u, "v": v})
    assert per_cell[0] == [(1, -1), (2, 1), (3, -1)]
    assert per_cell[1] == []


@_PROPERTY
@given(
    st.integers(min_value=2, max_value=15).flatmap(
        lambda n: st.lists(
            st.lists(st.integers(min_value=0, max_value=16), min_size=n, max_size=n),
            min_size=3, max_size=3,
        )
    )
)
def test_property_vectorised_tracker_equals_1d_helper(cells: list[list[int]]) -> None:
    """SPEC 5.4: for arbitrary k/16 signals (exact in binary) the N-cell tracker equals the 1-D helper."""
    signals = np.array(cells, dtype=np.float64).T / 16.0  # [T, N=3]
    per_cell = _run_tracker(signals)
    for cell in range(3):
        assert per_cell[cell] == sorted(_t(detect_transitions_1d(signals[:, cell])))


@_PROPERTY
@given(st.lists(st.integers(min_value=0, max_value=16), min_size=2, max_size=20))
def test_property_transition_frames_increase_and_flashes_are_paired(values: list[int]) -> None:
    """SPEC 5.5: transition frames strictly increase, flashes are a subset of them, at most half as many."""
    signal = np.array(values, dtype=np.float64) / 16.0
    transitions = _t(detect_transitions_1d(signal))
    flashes = _f(flash_frames_1d(signal))
    frames = [frame for frame, _ in transitions]
    assert frames == sorted(set(frames))
    assert all(0 <= frame < len(values) for frame in frames)
    assert set(flashes) <= set(frames)
    assert len(flashes) <= len(transitions) // 2
    assert flashes == sorted(flashes)


# ---------------------------------------------------------------------------
# FlashPairer and Transition (ADR-005)
# ---------------------------------------------------------------------------


def test_transition_dataclass_fields() -> None:
    """Contract: Transition carries frame, sign and the integer cell indices."""
    tr = Transition(frame=3, sign=-1, cells=np.array([1, 2]))
    assert tr.frame == 3
    assert tr.sign == -1
    assert np.asarray(tr.cells).tolist() == [1, 2]


def test_flash_pairer_completes_only_on_opposite_sign() -> None:
    """ADR-005: a flash completes when a pending transition meets one of the opposite sign."""
    pairer = FlashPairer(3)
    assert list(pairer.add(Transition(frame=1, sign=1, cells=np.array([0, 1])))) == []
    # Cell 0 completes (pending +1 meets -1); cell 2 had nothing pending so it only stores.
    assert sorted(int(c) for c in pairer.add(Transition(frame=2, sign=-1, cells=np.array([0, 2])))) == [0]
    # Cell 2's pending -1 now meets +1.
    assert sorted(int(c) for c in pairer.add(Transition(frame=3, sign=1, cells=np.array([2])))) == [2]
    # Cell 1 still holds +1; another +1 is not a flash (same sign) and is stored instead.
    assert list(pairer.add(Transition(frame=4, sign=1, cells=np.array([1])))) == []
    assert sorted(int(c) for c in pairer.add(Transition(frame=5, sign=-1, cells=np.array([1])))) == [1]


def test_flash_pairer_clears_pending_after_a_flash() -> None:
    """ADR-005: non-overlapping pairs - after a flash the cell starts from nothing pending."""
    pairer = FlashPairer(1)
    pairer.add(Transition(frame=1, sign=1, cells=np.array([0])))
    assert [int(c) for c in pairer.add(Transition(frame=2, sign=-1, cells=np.array([0])))] == [0]
    # The -1 that completed the flash is consumed; a fresh -1 is stored, not paired with it.
    assert list(pairer.add(Transition(frame=3, sign=-1, cells=np.array([0])))) == []
    assert [int(c) for c in pairer.add(Transition(frame=4, sign=1, cells=np.array([0])))] == [0]


def test_flash_pairer_returns_integer_cell_indices() -> None:
    """Contract: add() returns an integer ndarray of the cells completing a flash now."""
    pairer = FlashPairer(4)
    pairer.add(Transition(frame=0, sign=-1, cells=np.array([3, 1])))
    out = pairer.add(Transition(frame=1, sign=1, cells=np.array([1, 2, 3])))
    out = np.asarray(out)
    assert np.issubdtype(out.dtype, np.integer)
    assert sorted(out.tolist()) == [1, 3]
