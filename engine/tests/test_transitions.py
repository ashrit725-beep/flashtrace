"""Black-box unit tests for engine/transitions.py (SPEC T2, T3, 5.4, 5.5, ADR-004, ADR-005, ADR-018).

Signals are hand-made per-cell time series.  Each expected (frame, sign) list
was traced by hand through the run-tracking semantics in SPEC 5.4: a run is a
monotone stretch between adjacent extrema, plateaus and reversals no larger
than the reversal tolerance never end a run, and a transition is timestamped
at the first frame where the run reached its extremum (ADR-018).
"""
from __future__ import annotations

import inspect

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from engine.constants import D65_UV, RUN_REVERSAL_TOLERANCE
from engine.transitions import (
    FlashPairer,
    Rule,
    RunTracker,
    Transition,
    detect_transitions_1d,
    flash_frames_1d,
    general_rule,
    red_rule_legacy,
    red_rule_wcag22,
)

_PROPERTY = settings(deadline=None, derandomize=True, max_examples=40, database=None)

# Chromaticities used by the red-rule tests (ADR-011): sRGB red (0.45080, 0.52289 from the SPEC 5.3
# matrix, written to 4 decimals as in test_color.py) and the D65 white point the engine computes.
_RED_UV = (0.4508, 0.5229)
_D65_UV = D65_UV
_TOL_L = RUN_REVERSAL_TOLERANCE["L"]


def _t(result: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Normalise a (frame, sign) list to plain Python ints for exact comparison."""
    return [(int(frame), int(sign)) for frame, sign in result]


def _f(result: list[int]) -> list[int]:
    """Normalise a frame list to plain Python ints."""
    return [int(frame) for frame in result]


def _uv_aux(u: list[float], v: list[float]) -> dict[str, np.ndarray]:
    """Auxiliary arrays for the WCAG 2.2 red rule."""
    return {"u": np.asarray(u, dtype=np.float64), "v": np.asarray(v, dtype=np.float64)}


def _run_tracker(signals: np.ndarray, rule: Rule = general_rule, tolerance: float = _TOL_L,
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
    assert _t(detect_transitions_1d(np.array([0.0, 0.10, 0.0, 0.0]))) == [(1, 1), (2, -1)]


def test_delta_of_0_0999_is_not_a_transition() -> None:
    """SPEC T2: 0.0999 is below '10% or more', so nothing is emitted."""
    assert _t(detect_transitions_1d(np.array([0.0, 0.0999, 0.0]))) == []


def test_both_states_at_or_above_0_80_give_nothing() -> None:
    """SPEC T2: 'the darker image is below 0.80' is exclusive; 0.85 <-> 1.0 (delta 0.15) does not count."""
    assert _t(detect_transitions_1d(np.array([0.85, 1.0, 0.85]))) == []
    assert _t(detect_transitions_1d(np.array([0.85, 0.95, 0.85]))) == []


def test_dark_state_of_0_79_counts() -> None:
    """SPEC T2: 0.79 is below 0.80, so 0.79 <-> 0.95 (delta 0.16) gives two transitions."""
    assert _t(detect_transitions_1d(np.array([0.79, 0.95, 0.79, 0.79]))) == [(1, 1), (2, -1)]


def test_all_sub_threshold_wiggles_give_nothing() -> None:
    """SPEC 5.4: runs that do not meet the rule produce no transition at all."""
    assert _t(detect_transitions_1d(np.array([0.0, 0.05, 0.0, 0.05, 0.0]))) == []


def test_constant_signal_gives_nothing() -> None:
    """SPEC 5.4: a flat signal has no extrema, hence no runs and no transitions."""
    assert _t(detect_transitions_1d(np.full(5, 0.5))) == []


def test_held_step_is_one_rise_at_frame_one() -> None:
    """ADR-004 as amended: the shortest run that counts is a step the next frame holds; it is
    stamped at the step.  The same step shown by the last frame alone has not been seen to arrive."""
    assert _t(detect_transitions_1d(np.array([0.0, 1.0, 1.0]))) == [(1, 1)]
    assert _t(detect_transitions_1d(np.array([0.0, 1.0]))) == []


# ---------------------------------------------------------------------------
# Monotone runs, plateaus, wobbles (SPEC 5.4, ADR-004, ADR-018)
# ---------------------------------------------------------------------------


def test_plateau_is_stamped_at_its_first_frame() -> None:
    """ADR-018 (1): a transition is stamped at the FIRST frame showing the new state, not the last plateau frame."""
    signal = np.array([0.0, 1.0, 1.0, 1.0, 0.0, 0.0, 0.0, 1.0, 1.0])
    assert _t(detect_transitions_1d(signal)) == [(1, 1), (4, -1), (7, 1)]


def _ramp_plateau_ramp() -> np.ndarray:
    """10 frames 0 -> 0.5, 10 flat frames at 0.5, 10 frames 0.5 -> 0, then 0 held once (31 frames)."""
    return np.concatenate([np.linspace(0.0, 0.5, 10), np.full(10, 0.5), np.linspace(0.5, 0.0, 10), [0.0]])


def test_ramp_is_timestamped_at_its_end() -> None:
    """ADR-004: 'a ramp counts once when it ends' - rise at frame 9, fall at frame 29."""
    assert _t(detect_transitions_1d(_ramp_plateau_ramp())) == [(9, 1), (29, -1)]


def test_plateau_inside_a_ramp_does_not_split_it() -> None:
    """SPEC 5.4: 'plateaus do not end a run' - one rise ending at frame 7, one fall at 8."""
    signal = np.array([0.0, 0.1, 0.2, 0.2, 0.2, 0.3, 0.4, 0.5, 0.0, 0.0])
    assert _t(detect_transitions_1d(signal)) == [(7, 1), (8, -1)]


def test_sub_tolerance_wobble_does_not_split_a_ramp() -> None:
    """SPEC 5.4 / ADR-018 (2): a 0.002 dip (< 0.005) is a plateau, so the ramp is one rise."""
    signal = np.array([0.0, 0.05, 0.10, 0.15, 0.20, 0.198, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.50])
    assert _t(detect_transitions_1d(signal)) == [(11, 1)]


def test_wobble_above_tolerance_splits_a_ramp_into_two_rises() -> None:
    """SPEC 5.4: a 0.05 dip (> 0.005) ends the run: rise 0->0.20 at frame 4, the dip itself is below
    0.10 (no transition but it resets the start), then rise 0.15->0.50 at frame 11: two rises, no flash."""
    signal = np.array([0.0, 0.05, 0.10, 0.15, 0.20, 0.15, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.50])
    assert _t(detect_transitions_1d(signal)) == [(4, 1), (11, 1)]
    assert _f(flash_frames_1d(signal)) == []


def test_tolerance_parameter_decides_whether_a_wobble_splits() -> None:
    """ADR-018 (2): the same 0.008 dip splits at tolerance 0.005 and is ignored at 0.01."""
    signal = np.array([0.0, 0.2, 0.192, 0.5, 0.0, 0.0])
    assert _t(detect_transitions_1d(signal, tolerance=0.005)) == [(1, 1), (3, 1), (4, -1)]
    assert _t(detect_transitions_1d(signal, tolerance=0.01)) == [(3, 1), (4, -1)]


def test_open_run_that_has_settled_is_evaluated_by_finish() -> None:
    """ADR-004 as amended: a run still open at the end whose signal has stood still is closed by
    finish() at its stamp - [0, 0, 1, 1] is a rise at 2, and so is [0, 0, 1, 0.998] (a turn back
    within the tolerance also means the signal has arrived)."""
    assert _t(detect_transitions_1d(np.array([0.0, 0.0, 1.0, 1.0]))) == [(2, 1)]
    assert _t(detect_transitions_1d(np.array([0.0, 0.0, 1.0, 0.998]))) == [(2, 1)]


def test_open_run_still_moving_at_the_end_is_dropped() -> None:
    """ADR-004 as amended: a run still moving at the last frame has not reached its extremum inside
    the capture, so finish() drops it: a step shown only by the last frame, a fade cut off on its
    way down, and a settled run that takes a further big step on the last frame."""
    assert _t(detect_transitions_1d(np.array([0.0, 0.0, 1.0]))) == []
    assert _t(detect_transitions_1d(np.array([0.0, 1.0, 1.0, 0.7, 0.4, 0.1]))) == [(1, 1)]
    assert _t(detect_transitions_1d(np.array([0.0, 1.0, 1.0, 0.5, 0.5, 0.2]))) == [(1, 1)]
    # The same fade allowed to arrive: the fall is stamped at its first frame at the bottom.
    assert _t(detect_transitions_1d(np.array([0.0, 1.0, 1.0, 0.7, 0.4, 0.1, 0.1]))) == [(1, 1), (5, -1)]


def test_default_tolerance_matches_the_constant() -> None:
    """ADR-018: RUN_REVERSAL_TOLERANCE['L'] is 0.005 and the 1-D helpers take their default from it."""
    assert RUN_REVERSAL_TOLERANCE == {"L": 0.005, "ratio": 0.005, "rv": 1.0}
    for helper in (detect_transitions_1d, flash_frames_1d):
        assert inspect.signature(helper).parameters["tolerance"].default == RUN_REVERSAL_TOLERANCE["L"]
    signal = np.array([0.0, 0.3, 0.296, 0.6, 0.0, 0.0])
    assert _t(detect_transitions_1d(signal)) == [(3, 1), (4, -1)]


# ---------------------------------------------------------------------------
# Where the stamp sits: noise on a plateau versus a smooth approach (SPEC 5.4, ADR-018)
# ---------------------------------------------------------------------------


def test_sub_tolerance_noise_on_a_plateau_does_not_move_the_stamp() -> None:
    """ADR-018 (1): the change finished at frame 8; a 0.003 bump at frame 11 (< 0.005) is noise on the
    plateau, so the rise stays stamped at 8 (the literal 'first frame of the extremum' gave 11)."""
    signal = np.array([0.2] * 8 + [0.5, 0.5, 0.5, 0.503, 0.5, 0.5, 0.5] + [0.2] * 8)
    assert _t(detect_transitions_1d(signal)) == [(8, 1), (15, -1)]


def test_sub_tolerance_noise_on_a_falling_plateau_does_not_move_the_stamp() -> None:
    """The mirror image: a 0.003 dip at frame 5 on the dark plateau leaves the fall stamped at 3."""
    signal = np.array([0.8, 0.8, 0.8, 0.1, 0.1, 0.097, 0.1, 0.1, 0.8, 0.8])
    assert _t(detect_transitions_1d(signal)) == [(3, -1), (8, 1)]


def test_smooth_approach_is_followed_to_its_extremum() -> None:
    """ADR-018 (1): a fade into black keeps falling by less than 0.005 per frame near the end
    (0.004 -> 0.001 -> 0.0); the signal has not settled, so the stamp follows it to frame 8, the
    first frame at 0.0.  Ignoring every sub-tolerance gain would stop at frame 6, earlier for a
    dimmer cell than for a brighter one, and split one flash over several frames."""
    signal = np.array([0.3, 0.3, 0.2, 0.1, 0.03, 0.01, 0.004, 0.001, 0.0, 0.0, 0.3, 0.3])
    assert _t(detect_transitions_1d(signal)) == [(8, -1), (10, 1)]


def test_gain_on_the_first_plateau_frame_still_counts_as_arriving() -> None:
    """ADR-018 (1): until the signal stands still or turns back it is still arriving, so a 0.003
    gain on the frame right after the edge moves the stamp from 3 to 4 (a one-frame shift, which
    the one-frame event tolerance of SPEC 5.6 absorbs)."""
    signal = np.array([0.2, 0.2, 0.2, 0.5, 0.503, 0.5, 0.5, 0.2, 0.2])
    assert _t(detect_transitions_1d(signal)) == [(4, 1), (7, -1)]


def test_step_beyond_the_tolerance_after_settling_moves_the_stamp_again() -> None:
    """ADR-018 (1): after standing at 0.5 the signal climbs 0.02 (> 0.005) and then 0.01 more; that
    is a real change, so the stamp follows it to frame 4."""
    signal = np.array([0.0, 0.5, 0.5, 0.52, 0.53, 0.0, 0.0])
    assert _t(detect_transitions_1d(signal)) == [(4, 1), (5, -1)]


def test_gain_of_exactly_the_tolerance_after_settling_does_not_move_the_stamp() -> None:
    """SPEC 5.4: a change 'no larger than' the tolerance is noise - a settled plateau gaining exactly
    0.005 keeps its stamp at frame 2, while a gain 1e-9 larger moves it to frame 4."""
    exact = np.array([0.2, 0.2, 0.5, 0.5, 0.5 + _TOL_L, 0.2, 0.2])
    larger = np.array([0.2, 0.2, 0.5, 0.5, 0.5 + _TOL_L + 1e-9, 0.2, 0.2])
    assert _t(detect_transitions_1d(exact)) == [(2, 1), (5, -1)]
    assert _t(detect_transitions_1d(larger)) == [(4, 1), (5, -1)]


def test_reversal_of_exactly_the_tolerance_does_not_end_a_run() -> None:
    """SPEC 5.4: a reversal 'no larger than' the tolerance does not end a run.  0.0625 is exact in
    binary; a dip of exactly 0.005 is ignored, so the two 0.06 rises merge into one rise >= 0.10 at
    frame 3.  With the dip 1e-9 deeper the run ends there and neither 0.06 rise is a transition."""
    peak = 0.0625
    dip = peak - _TOL_L  # the same float the tracker computes as ext - tol
    assert _t(detect_transitions_1d(np.array([0.0, peak, dip, dip + 0.06, dip + 0.06]))) == [(3, 1)]
    deeper = dip - 1e-9
    assert _t(detect_transitions_1d(np.array([0.0, peak, deeper, deeper + 0.06, deeper + 0.06]))) == []


def test_a_change_of_exactly_the_tolerance_does_not_start_a_run() -> None:
    """SPEC 5.4: from rest, a move of exactly 0.005 is still a plateau, so the later fall is measured
    from 0.3 (0.098, no transition).  A move 1e-9 larger starts a rise; the fall is then measured
    from 0.305 (0.103 >= 0.10) and is a transition."""
    start, low = 0.3, 0.3 - 0.098
    assert _t(detect_transitions_1d(np.array([start, start + _TOL_L, low, low]))) == []
    assert _t(detect_transitions_1d(np.array([start, start + _TOL_L + 1e-9, low, low]))) == [(2, -1)]


# ---------------------------------------------------------------------------
# Flash pairing through the 1-D helper (SPEC 5.5, ADR-005)
# ---------------------------------------------------------------------------


def test_square_wave_with_four_transitions_flashes_at_second_and_fourth() -> None:
    """ADR-005: pairs are (1st+2nd, 3rd+4th); each flash is timed at its second transition."""
    signal = np.array([0.0, 1.0, 0.0, 1.0, 0.0, 0.0])
    assert _t(detect_transitions_1d(signal)) == [(1, 1), (2, -1), (3, 1), (4, -1)]
    assert _f(flash_frames_1d(signal)) == [2, 4]


def test_eight_transitions_give_four_flashes() -> None:
    """SPEC 5.5: 'a 4 Hz square wave therefore yields 8 transitions and 4 flashes'."""
    signal = np.array([0.0, 1.0] * 4 + [0.0, 0.0])
    assert len(detect_transitions_1d(signal)) == 8
    assert _f(flash_frames_1d(signal)) == [2, 4, 6, 8]


def test_three_transitions_give_one_flash() -> None:
    """ADR-005: non-overlapping pairing - the unpaired third transition is not a flash."""
    signal = np.array([0.0, 1.0, 0.0, 1.0, 1.0])
    assert len(detect_transitions_1d(signal)) == 3
    assert _f(flash_frames_1d(signal)) == [2]


def test_rise_tiny_fall_rise_fall_is_exactly_one_flash() -> None:
    """ADR-005 / ADR-018 (3): two same-sign rises (the 0.05 fall between them is no transition); the
    newer rise replaces the pending one and pairs with the final fall - one flash at frame 4."""
    signal = np.array([0.0, 0.5, 0.45, 0.9, 0.0, 0.0])
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
    ratio = np.array([1.0, 0.333, 1.0, 1.0])
    aux = _uv_aux(u=[_RED_UV[0], _D65_UV[0], _RED_UV[0], _RED_UV[0]],
                  v=[_RED_UV[1], _D65_UV[1], _RED_UV[1], _RED_UV[1]])
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
    rv = np.array([320.0, 160.0, 320.0, 320.0])
    aux = {"ratio": np.ones(4)}
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
    square = np.array([0.0, 1.0] * 6 + [1.0])
    ramp = np.array([0.0, 0.1, 0.2, 0.2, 0.2, 0.3, 0.4, 0.5, 0.5, 0.3, 0.1, 0.0, 0.0])
    step = np.array([0.5] * 6 + [0.9] * 3 + [0.5] * 4)
    signals = np.stack([square, ramp, step], axis=1)  # [T=13, N=3]
    per_cell = _run_tracker(signals)
    for cell in range(3):
        assert per_cell[cell] == sorted(_t(detect_transitions_1d(signals[:, cell])))
    # Sanity: the square wave cell really has 11 transitions, the ramp cell 2.
    assert len(per_cell[0]) == 11
    assert len(per_cell[1]) == 2


def test_run_tracker_groups_cells_sharing_frame_and_sign() -> None:
    """Transition.cells: cells that end the same kind of run at the same frame share one record."""
    square = np.array([0.0, 1.0, 0.0, 1.0, 0.0, 0.0])
    signals = np.stack([square, square], axis=1)
    tracker = RunTracker(2, general_rule, _TOL_L)
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
    tracker = RunTracker(1, general_rule, _TOL_L)
    emitted_at: dict[int, list[tuple[int, int]]] = {}
    for t in range(signal.shape[0]):
        out = tracker.update(t, signal[t : t + 1], {})
        if out:
            emitted_at[t] = [(int(tr.frame), int(tr.sign)) for tr in out]
    final = tracker.finish(signal.shape[0] - 1)
    assert emitted_at == {21: [(9, 1)]}
    assert [(int(tr.frame), int(tr.sign)) for tr in final] == [(29, -1)]


def test_run_tracker_finish_returns_only_open_runs_that_have_arrived() -> None:
    """ADR-004 as amended: finish() emits the open run whose signal has settled (cell 0 steps at
    frame 2 and holds); nothing for a flat cell (1) or for a step shown by the last frame alone (2)."""
    signals = np.array([[0.0, 0.5, 0.0], [0.0, 0.5, 0.0], [1.0, 0.5, 0.0], [1.0, 0.5, 1.0]])
    tracker = RunTracker(3, general_rule, _TOL_L)
    during = []
    for t in range(4):
        during.extend(tracker.update(t, signals[t], {}))
    assert during == []
    final = tracker.finish(3)
    assert len(final) == 1
    assert int(final[0].frame) == 2
    assert int(final[0].sign) == 1
    assert np.asarray(final[0].cells).tolist() == [0]


def test_run_tracker_finish_with_nothing_open_returns_empty() -> None:
    """ADR-004: a signal that already completed all its runs has nothing left for finish()."""
    tracker = RunTracker(1, general_rule, _TOL_L)
    for t in range(4):
        tracker.update(t, np.full(1, 0.3), {})
    assert list(tracker.finish(3)) == []


def test_run_tracker_red_rule_uses_aux_per_cell() -> None:
    """SPEC T3 vectorised: only the cell toggling red <-> grey yields red transitions."""
    ratio = np.array([[1.0, 0.333], [0.333, 0.333], [1.0, 0.333], [0.333, 0.333], [0.333, 0.333]])  # [T=5, N=2]
    u = np.array([[_RED_UV[0], _D65_UV[0]], [_D65_UV[0]] * 2, [_RED_UV[0], _D65_UV[0]], [_D65_UV[0]] * 2,
                  [_D65_UV[0]] * 2])
    v = np.array([[_RED_UV[1], _D65_UV[1]], [_D65_UV[1]] * 2, [_RED_UV[1], _D65_UV[1]], [_D65_UV[1]] * 2,
                  [_D65_UV[1]] * 2])
    per_cell = _run_tracker(ratio, rule=red_rule_wcag22,
                            tolerance=RUN_REVERSAL_TOLERANCE["ratio"], aux={"u": u, "v": v})
    assert per_cell[0] == [(1, -1), (2, 1), (3, -1)]
    assert per_cell[1] == []


def _most_in_any_second(frames: list[int], fps: int = 60) -> int:
    """The largest number of the given frames inside any fps consecutive frames."""
    return max((sum(1 for g in frames if f <= g < f + fps) for f in frames), default=0)


@pytest.mark.parametrize("hz", [2.8, 2.9, 3.0])
def test_smooth_pulse_cut_off_by_the_end_adds_no_early_flash(hz: float) -> None:
    """ADR-004 as amended / SC 2.3.1: a pulse at or below 3 Hz has at most 3 flashes in any one second,
    whatever phase the capture ends in.  A cosine fade cut off by the last frame has not reached
    its valley; closing it there stamped a flash early and gave 4 in the last second."""
    for n_frames in (590, 600):
        t = (np.arange(n_frames) + 0.25) / 60  # ADR-029 sample instants
        for k in range(12):
            signal = 0.5 - 0.5 * np.cos(2 * np.pi * (hz * t + k / 12))
            assert _most_in_any_second(_f(flash_frames_1d(signal))) <= 3, (n_frames, k)


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


# ---------------------------------------------------------------------------
# Pixel-level rule and pairing (SPEC 5.4, 5.5, ADR-002 as amended)
# ---------------------------------------------------------------------------


def _grey_measure(pixels: np.ndarray) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """Test measure: a pixel's signal is its red byte / 255 (enough to drive general_rule)."""
    return np.asarray(pixels, dtype=np.float64)[..., 0] / 255.0, {}


def _cell_frames(levels: list[list[int]]) -> list[np.ndarray]:
    """One cell of 4 pixels per frame: levels[t][j] is pixel j's byte (all three channels)."""
    return [np.repeat(np.array(row, dtype=np.uint8)[None, :, None], 3, axis=2) for row in levels]


def _track_pixels(levels: list[list[int]]) -> list[Transition]:
    """Drive a one-cell RunTracker with the test measure; the cell signal is the pixel mean."""
    tracker = RunTracker(1, general_rule, _TOL_L, measure=_grey_measure)
    out: list[Transition] = []
    for t, px in enumerate(_cell_frames(levels)):
        out.extend(tracker.update(t, np.array([px[0, :, 0].mean() / 255.0]), {}, px))
    out.extend(tracker.finish(len(levels) - 1))
    return out


def test_pixel_rule_reports_which_pixels_made_the_transition() -> None:
    """ADR-002 as amended: two of four pixels go black -> white -> black.  The cell mean moves by 0.5,
    and each transition names exactly the two pixels that changed."""
    found = _track_pixels([[0, 0, 0, 0], [255, 255, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0]])
    assert [(tr.frame, tr.sign) for tr in found] == [(1, 1), (2, -1)]
    for tr in found:
        assert tr.pixels is not None and tr.pixels.tolist() == [[True, True, False, False]]


def test_pixel_rule_counts_a_low_contrast_pixel_the_cell_mean_would_miss() -> None:
    """ADR-002 as amended: one pixel of four changes by 0.2 (>= 0.10); the cell mean changes by only 0.05
    (< 0.10).  The cell-mean rule saw no transition; the pixel rule sees that one pixel's."""
    level = round(0.2 * 255)
    found = _track_pixels([[0, 0, 0, 0], [level, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0]])
    assert [(tr.frame, tr.sign) for tr in found] == [(1, 1), (2, -1)]
    assert found[0].pixels.tolist() == [[True, False, False, False]]


def test_pixel_rule_drops_a_cell_whose_mean_passes_but_no_pixel_does() -> None:
    """ADR-002 as amended: one pixel stays black while three go 0.82 <-> 1.0.  The cell mean goes
    0.615 <-> 0.75 (Delta 0.135, darker < 0.80: a transition by the mean), but no pixel has a darker
    state below 0.80, so nothing on the screen is a general flash."""
    lo, hi = round(0.82 * 255), 255
    found = _track_pixels([[0, lo, lo, lo], [0, hi, hi, hi], [0, lo, lo, lo], [0, lo, lo, lo]])
    assert found == []


def test_run_tracker_with_a_measure_needs_pixels_and_without_one_refuses_them() -> None:
    """Contract: pixels are required exactly when a measure is given, shaped (N, P, 3) uint8."""
    with_measure = RunTracker(1, general_rule, _TOL_L, measure=_grey_measure)
    with pytest.raises(ValueError):
        with_measure.update(0, np.zeros(1), {})
    with pytest.raises(ValueError):
        with_measure.update(0, np.zeros(1), {}, np.zeros((1, 4, 3), dtype=np.float64))
    without = RunTracker(1, general_rule, _TOL_L)
    with pytest.raises(ValueError):
        without.update(0, np.zeros(1), {}, np.zeros((1, 4, 3), dtype=np.uint8))


def test_flash_pairer_pairs_each_pixel_on_its_own() -> None:
    """SPEC 5.5 per pixel: pixels 0-1 rise, then pixels 1-2 fall.  Only pixel 1 completes a flash;
    pixel 2's fall waits, and pixel 0's rise is still pending for a later fall."""
    pairer = FlashPairer(1, pixels_per_cell=3)
    cells, counts = pairer.complete(Transition(1, 1, np.array([0]), np.array([[True, True, False]])))
    assert cells.tolist() == [] and counts.tolist() == []
    cells, counts = pairer.complete(Transition(2, -1, np.array([0]), np.array([[False, True, True]])))
    assert cells.tolist() == [0] and counts.tolist() == [1]
    assert pairer.pending_sign.tolist() == [[1, 0, -1]]
    cells, counts = pairer.complete(Transition(3, -1, np.array([0]), None))  # no pixel data: every pixel
    assert cells.tolist() == [0] and counts.tolist() == [1]                  # pixel 0 completes
    assert pairer.pending_sign.tolist() == [[0, -1, -1]]


def test_flash_pairer_rejects_pixels_of_the_wrong_shape() -> None:
    """Contract: Transition.pixels must be [len(cells), pixels_per_cell]."""
    pairer = FlashPairer(2, pixels_per_cell=4)
    with pytest.raises(ValueError):
        pairer.complete(Transition(0, 1, np.array([0, 1]), np.ones((2, 3), dtype=bool)))
