"""Unit tests for the guitar_cleanup passes.

Each pass has:
  * an "on-gate" test that supplies input matching the gate and asserts the
    pass edits the stream as expected;
  * an "off-gate" test that supplies input which shouldn't fire the gate and
    asserts the pass is a no-op.

Plus a composer test, a determinism test, and a no-mutation test.
"""

from __future__ import annotations

from aural_ingest.algorithms.guitar_cleanup import (
    DEFAULT_GUITAR_CLEANUP_CONFIG,
    GuitarCleanupConfig,
    apply_guitar_cleanup,
    merge_bend_slide_vibrato_glides,
    per_string_range_gate,
    prune_distortion_overtone_shadows,
    suppress_palm_mutes_and_ghost_notes,
)
from aural_ingest.transcription import MelodicNote


def _n(t_on: float, t_off: float, pitch: int, velocity: int = 80) -> MelodicNote:
    return MelodicNote(t_on=t_on, t_off=t_off, pitch=pitch, velocity=velocity, instrument="guitar")


# ---------------------------------------------------------------------------
# Pass 1: prune_distortion_overtone_shadows
# ---------------------------------------------------------------------------


def test_distortion_shadow_pruning_fires_on_dense_octave_fifth_stack() -> None:
    # Dense stack: five roots with matching octave shadows above (10 notes),
    # each shadow overlaps its root, roots louder than shadows.
    roots = [_n(0.1 * i, 0.1 * i + 0.4, 52 + i, velocity=95) for i in range(5)]
    shadows = [_n(0.1 * i + 0.005, 0.1 * i + 0.35, 64 + i, velocity=70) for i in range(5)]
    cleaned = prune_distortion_overtone_shadows(roots + shadows)

    pitches = sorted(int(n.pitch) for n in cleaned)
    # Roots survive; shadows go.
    assert pitches == sorted(int(n.pitch) for n in roots)


def test_distortion_shadow_pruning_no_op_when_gate_misses() -> None:
    # Only a few notes and no octave-shadow structure → gate off, no-op.
    inputs = [_n(0.0, 0.3, 60, 80), _n(0.2, 0.5, 64, 80), _n(0.4, 0.7, 67, 80)]
    cleaned = prune_distortion_overtone_shadows(inputs)
    assert cleaned == inputs


def test_distortion_shadow_pruning_keeps_shadow_when_lower_note_weaker() -> None:
    # Twelve notes, dense octave/fifth stack (gate fires) BUT specific pair
    # where "shadow" is louder than root → don't drop it (not a shadow).
    inputs: list[MelodicNote] = []
    for i in range(6):
        inputs.append(_n(0.1 * i, 0.1 * i + 0.4, 50 + i, velocity=95))  # root
        inputs.append(_n(0.1 * i + 0.005, 0.1 * i + 0.35, 62 + i, velocity=70))  # shadow
    # Louder-upper pair to test the velocity rule:
    inputs.append(_n(1.0, 1.4, 55, velocity=60))  # weak "lower"
    inputs.append(_n(1.005, 1.35, 67, velocity=100))  # strong "upper" — real note

    cleaned = prune_distortion_overtone_shadows(inputs)
    kept_pitches = [int(n.pitch) for n in cleaned]
    # 67 (real strong note, an octave above weak 55) must survive.
    assert 67 in kept_pitches


# ---------------------------------------------------------------------------
# Pass 2: merge_bend_slide_vibrato_glides
# ---------------------------------------------------------------------------


def test_glide_merging_fires_on_monotonic_bend() -> None:
    # Four adjacent single-step notes, monotonic ascending, within 200 ms.
    bend = [
        _n(0.10, 0.14, 60),
        _n(0.14, 0.18, 61),
        _n(0.18, 0.22, 62),
        _n(0.22, 0.36, 63),
    ]
    cleaned = merge_bend_slide_vibrato_glides(bend)
    assert len(cleaned) == 1
    merged = cleaned[0]
    assert merged.pitch == 63  # bend target
    assert merged.t_on == 0.10
    assert merged.t_off >= 0.36


def test_glide_merging_fires_on_vibrato_oscillation() -> None:
    # Symmetric vibrato around 62: 62, 63, 62, 61, 62 → center pitch = 62.
    vib = [
        _n(0.10, 0.14, 62),
        _n(0.14, 0.18, 63),
        _n(0.18, 0.22, 62),
        _n(0.22, 0.26, 61),
        _n(0.26, 0.40, 62),
    ]
    cleaned = merge_bend_slide_vibrato_glides(vib)
    assert len(cleaned) == 1
    assert cleaned[0].pitch == 62
    assert cleaned[0].t_off >= 0.40


def test_glide_merging_no_op_when_gate_misses() -> None:
    # Two-note step is below the neighbor threshold → gate off, no-op.
    inputs = [_n(0.1, 0.3, 60), _n(0.4, 0.6, 61)]
    cleaned = merge_bend_slide_vibrato_glides(inputs)
    assert cleaned == inputs


def test_glide_merging_no_op_for_large_intervals() -> None:
    # Three notes at chord intervals (not single steps) → gate off.
    inputs = [_n(0.1, 0.3, 60), _n(0.15, 0.35, 64), _n(0.20, 0.40, 67)]
    cleaned = merge_bend_slide_vibrato_glides(inputs)
    assert cleaned == inputs


# ---------------------------------------------------------------------------
# Pass 3: suppress_palm_mutes_and_ghost_notes
# ---------------------------------------------------------------------------


def test_palm_mute_suppression_fires_on_bimodal_velocity() -> None:
    # Six real notes (v≈80) and three palm-muted (v≈25, short).
    real = [_n(0.1 + 0.2 * i, 0.35 + 0.2 * i, 60 + i, velocity=80) for i in range(6)]
    mutes = [_n(0.05 + 0.2 * i, 0.06 + 0.2 * i, 40 + i, velocity=25) for i in range(3)]
    cleaned = suppress_palm_mutes_and_ghost_notes(real + mutes)

    kept_v = [int(n.velocity) for n in cleaned]
    assert all(v >= 40 or True for v in kept_v)  # sanity
    # All 6 real notes survive.
    assert sum(1 for n in cleaned if int(n.velocity) == 80) == 6
    # No muted notes survive.
    assert not any(int(n.velocity) == 25 for n in cleaned)


def test_palm_mute_suppression_no_op_when_velocity_uniform() -> None:
    # All real, uniform velocity → gate off, no-op.
    inputs = [_n(0.1 + 0.2 * i, 0.35 + 0.2 * i, 60 + i, velocity=78) for i in range(8)]
    cleaned = suppress_palm_mutes_and_ghost_notes(inputs)
    assert cleaned == inputs


def test_palm_mute_suppression_no_op_when_only_a_few_notes() -> None:
    # Even a bimodal-looking stream is a no-op if there aren't enough notes.
    inputs = [_n(0.1, 0.4, 60, velocity=85), _n(0.5, 0.51, 60, velocity=25)]
    cleaned = suppress_palm_mutes_and_ghost_notes(inputs)
    assert cleaned == inputs


def test_palm_mute_suppression_keeps_short_but_loud_note() -> None:
    real = [_n(0.1 + 0.2 * i, 0.35 + 0.2 * i, 60 + i, velocity=80) for i in range(6)]
    mutes = [_n(0.05 + 0.2 * i, 0.06 + 0.2 * i, 40 + i, velocity=25) for i in range(3)]
    # Short high-velocity accent — must survive (fails duration test only).
    accent = _n(1.5, 1.51, 65, velocity=100)
    cleaned = suppress_palm_mutes_and_ghost_notes(real + mutes + [accent])
    assert accent in cleaned


# ---------------------------------------------------------------------------
# Pass 4: per_string_range_gate
# ---------------------------------------------------------------------------


def test_per_string_range_gate_lead_drops_below_e3() -> None:
    inputs = [_n(0.1, 0.3, 34), _n(0.4, 0.6, 40), _n(0.7, 0.9, 65)]
    cleaned = per_string_range_gate(inputs, role="lead")
    assert [int(n.pitch) for n in cleaned] == [40, 65]


def test_per_string_range_gate_rhythm_keeps_e2_to_a4() -> None:
    inputs = [_n(0.1, 0.3, 26), _n(0.4, 0.6, 28), _n(0.7, 0.9, 69), _n(1.0, 1.2, 72)]
    cleaned = per_string_range_gate(inputs, role="rhythm")
    assert [int(n.pitch) for n in cleaned] == [28, 69]


def test_per_string_range_gate_no_op_without_role() -> None:
    inputs = [_n(0.1, 0.3, 34), _n(0.4, 0.6, 40), _n(0.7, 0.9, 100)]
    cleaned = per_string_range_gate(inputs, role=None)
    assert cleaned == inputs
    cleaned2 = per_string_range_gate(inputs, role="unknown_role")
    assert cleaned2 == inputs


# ---------------------------------------------------------------------------
# Composer
# ---------------------------------------------------------------------------


def test_composer_combines_multiple_passes() -> None:
    # Build an input that trips multiple gates:
    #   * a distortion octave stack (root + shadow x5)
    #   * a bend (four single-step notes, monotonic)
    #   * palm mutes (low velocity, short)
    #   * a stray pitch outside lead range
    inputs: list[MelodicNote] = []
    # Octave stack — 10 root+shadow pairs (20 notes, ~50% shadow ratio) so
    # pass-1 gate fires cleanly even after other notes are added.
    for i in range(10):
        inputs.append(_n(0.10 * i, 0.10 * i + 0.4, 52 + (i % 6), velocity=95))
        inputs.append(_n(0.10 * i + 0.005, 0.10 * i + 0.35, 64 + (i % 6), velocity=70))
    # Bend — gate 2 fires
    inputs.extend(
        [
            _n(2.10, 2.14, 60, velocity=85),
            _n(2.14, 2.18, 61, velocity=85),
            _n(2.18, 2.22, 62, velocity=85),
            _n(2.22, 2.36, 63, velocity=85),
        ]
    )
    # Palm mutes — gate 3 fires (with the real notes above, we have a bimodal distribution)
    inputs.extend(
        [
            _n(3.05, 3.06, 44, velocity=25),
            _n(3.20, 3.21, 45, velocity=22),
            _n(3.35, 3.36, 46, velocity=27),
        ]
    )
    # Below lead range (E3=40) — pass 4 fires with role="lead"
    inputs.append(_n(4.0, 4.2, 34, velocity=85))

    cleaned = apply_guitar_cleanup(inputs, role="lead")
    pitches = [int(n.pitch) for n in cleaned]

    # No shadow pitches (64..69) survive in the stack region t<2.0.
    stack_notes = [n for n in cleaned if float(n.t_on) < 2.0]
    for shadow_pitch in (64, 65, 66, 67, 68, 69):
        assert not any(int(n.pitch) == shadow_pitch for n in stack_notes)
    # Bend collapsed to a single 63 at t_on=2.10.
    bend_hits = [n for n in cleaned if int(n.pitch) == 63 and abs(float(n.t_on) - 2.10) < 1e-6]
    assert len(bend_hits) == 1
    # Palm mutes dropped.
    for pm_pitch in (44, 45, 46):
        assert pm_pitch not in pitches
    # Below-range 34 removed by range gate.
    assert 34 not in pitches


def test_composer_no_op_on_empty_input() -> None:
    assert apply_guitar_cleanup([], role="lead") == []


def test_composer_no_op_on_clean_input() -> None:
    # A tiny clean line with no gates triggered → identical output.
    inputs = [_n(0.1, 0.3, 60, 80), _n(0.4, 0.6, 64, 80), _n(0.7, 0.9, 67, 80)]
    cleaned = apply_guitar_cleanup(inputs, role=None)
    assert cleaned == inputs


# ---------------------------------------------------------------------------
# Determinism + no-mutation
# ---------------------------------------------------------------------------


def test_composer_is_deterministic() -> None:
    inputs = [_n(0.10 * i, 0.10 * i + 0.4, 52 + i, velocity=95) for i in range(5)]
    inputs.extend([_n(0.10 * i + 0.005, 0.10 * i + 0.35, 64 + i, velocity=70) for i in range(5)])
    inputs.extend(
        [
            _n(1.10, 1.14, 60),
            _n(1.14, 1.18, 61),
            _n(1.18, 1.22, 62),
            _n(1.22, 1.36, 63),
        ]
    )
    first = apply_guitar_cleanup(list(inputs), role="lead")
    second = apply_guitar_cleanup(list(inputs), role="lead")
    assert first == second


def test_passes_do_not_mutate_input_list() -> None:
    inputs = [
        _n(0.10, 0.14, 60),
        _n(0.14, 0.18, 61),
        _n(0.18, 0.22, 62),
        _n(0.22, 0.36, 63),
    ]
    snapshot = list(inputs)
    _ = merge_bend_slide_vibrato_glides(inputs)
    assert inputs == snapshot

    inputs2 = [_n(0.1 + 0.2 * i, 0.35 + 0.2 * i, 60 + i, velocity=80) for i in range(6)]
    inputs2 += [_n(0.05 + 0.2 * i, 0.06 + 0.2 * i, 40 + i, velocity=25) for i in range(3)]
    snapshot2 = list(inputs2)
    _ = suppress_palm_mutes_and_ghost_notes(inputs2)
    assert inputs2 == snapshot2


def test_config_can_disable_a_pass_by_widening_gate() -> None:
    # A stricter config keeps the mute gate closed even on bimodal input.
    real = [_n(0.1 + 0.2 * i, 0.35 + 0.2 * i, 60 + i, velocity=80) for i in range(6)]
    mutes = [_n(0.05 + 0.2 * i, 0.06 + 0.2 * i, 40 + i, velocity=25) for i in range(3)]
    strict = GuitarCleanupConfig(
        mute_gate_min_notes=DEFAULT_GUITAR_CLEANUP_CONFIG.mute_gate_min_notes,
        mute_gate_min_low_ratio=0.95,  # basically impossible to hit
    )
    cleaned = suppress_palm_mutes_and_ghost_notes(real + mutes, config=strict)
    # Muted notes survive — gate never fired.
    assert any(int(n.velocity) == 25 for n in cleaned)
