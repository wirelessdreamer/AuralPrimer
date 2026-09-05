from aural_ingest.algorithms.piano_playability import (
    BeatGrid,
    HarmonyContext,
    MotifConfig,
    SalienceContext,
    SalienceWeights,
    beat_grid_from_json,
    find_motifs,
    note_salience,
    PlayabilityConfig,
    chord_pitch_classes,
    enforce_hand_capacity,
    group_by_onset,
    hand_split,
    harmony_context_from_json,
    is_hand_feasible,
    make_playable,
    max_polyphony,
    melody_line,
    normalize_notes,
    note_key,
    prune_overtone_shadows,
    reduce_group_to_playable,
    trim_sustain,
)
from aural_ingest.transcription import MelodicNote


def note(t_on: float, pitch: int, *, dur: float = 0.5, velocity: int = 100) -> MelodicNote:
    return MelodicNote(t_on=t_on, t_off=t_on + dur, pitch=pitch, velocity=velocity, instrument="keys")


def chord(t: float, pitches, *, dur: float = 0.5) -> list[MelodicNote]:
    return [note(t, p, dur=dur) for p in pitches]


# ---------------------------------------------------------------------------
# hand model
# ---------------------------------------------------------------------------


def test_hand_split_accepts_a_two_hand_voicing():
    # C3 E3 G3 in the left hand, C5 E5 G5 in the right.
    left, right = hand_split([48, 52, 55, 72, 76, 79])
    assert left == [48, 52, 55]
    assert right == [72, 76, 79]


def test_hand_split_rejects_a_span_no_hand_can_reach():
    # Six notes over three octaves: every break leaves one hand either over a
    # ninth wide or over four fingers deep.
    assert hand_split([48, 60, 61, 62, 63, 84]) is None
    assert hand_split([40, 56, 72, 88, 100, 104, 106, 107]) is None


def test_hand_split_rejects_more_fingers_than_exist():
    config = PlayabilityConfig(max_notes_per_hand=4)
    assert hand_split(list(range(60, 69)), config=config) is None


def test_single_octave_cluster_is_feasible():
    assert is_hand_feasible([60, 62, 64, 65])
    # Five adjacent notes still fit -- one hand takes four, the other takes one.
    assert is_hand_feasible([60, 62, 64, 65, 67])
    # Nine do not: that is more fingers than two hands have.
    assert not is_hand_feasible(list(range(60, 69)))


def test_hand_split_breaks_at_the_widest_gap():
    left, right = hand_split([36, 40, 79, 84])
    assert left == [36, 40]
    assert right == [79, 84]


# ---------------------------------------------------------------------------
# grouping / normalising
# ---------------------------------------------------------------------------


def test_group_by_onset_chains_within_the_window():
    notes = [note(0.0, 60), note(0.02, 64), note(0.04, 67), note(0.9, 72)]
    groups = group_by_onset(notes, window_sec=0.05)
    assert [len(g) for g in groups] == [3, 1]


def test_normalize_trims_same_pitch_overlap():
    notes = [note(0.0, 60, dur=1.0), note(0.4, 60, dur=1.0)]
    out = normalize_notes(notes)
    assert len(out) == 2
    first = min(out, key=lambda n: n.t_on)
    assert first.t_off == 0.4  # released before the key is struck again


def test_normalize_drops_zero_length_and_clamps_pitch():
    notes = [MelodicNote(t_on=0.0, t_off=0.0, pitch=60, velocity=100),
             note(0.0, 130), note(0.0, -4)]
    out = normalize_notes(notes)
    assert sorted(n.pitch for n in out) == [21, 108]


# ---------------------------------------------------------------------------
# melody line
# ---------------------------------------------------------------------------


def test_melody_line_follows_the_supported_voice_not_the_octave_shadow():
    # A stepwise tune C5 D5 E5, each shadowed an octave above. The shadow is
    # the highest note, so only the audio can tell them apart.
    notes: list[MelodicNote] = []
    for i, pitch in enumerate((72, 74, 76)):
        notes.extend(chord(i * 0.5, [48, pitch, pitch + 12]))
    evidence = {note_key(n): (0.0 if n.pitch >= 84 else 1.0) for n in notes}
    line = melody_line(notes, evidence=evidence)
    assert {k[1] for k in line} == {72, 74, 76}


def test_melody_line_picks_one_note_per_group():
    notes = chord(0.0, [60, 64, 67]) + chord(0.5, [62, 65, 69])
    line = melody_line(notes)
    assert len(line) == 2


# ---------------------------------------------------------------------------
# overtone-shadow cull
# ---------------------------------------------------------------------------


def test_shadow_cull_is_a_no_op_without_audio_evidence():
    notes = chord(0.0, [48, 60, 67, 72, 79])
    out, removed = prune_overtone_shadows(notes, evidence=None)
    assert removed == 0
    assert len(out) == len(notes)


def test_shadow_cull_drops_an_unsupported_harmonic_but_keeps_the_bass():
    notes = chord(0.0, [48, 52, 55, 60, 67])
    evidence = {note_key(n): 1.0 for n in notes}
    evidence[note_key(notes[-1])] = 0.0  # the twelfth above the bass has no energy
    config = PlayabilityConfig(shadow_only_when_crowded=False)
    out, removed = prune_overtone_shadows(notes, evidence=evidence, config=config)
    assert removed == 1
    assert sorted(n.pitch for n in out) == [48, 52, 55, 60]


def test_shadow_cull_spares_the_melody_line():
    notes = chord(0.0, [48, 55, 60, 64, 67])
    evidence = {note_key(n): 0.9 for n in notes}
    evidence[note_key(notes[-1])] = 0.0
    melody = {note_key(notes[-1])}
    config = PlayabilityConfig(shadow_only_when_crowded=False)
    out, removed = prune_overtone_shadows(
        notes, evidence=evidence, melody=melody, config=config
    )
    assert removed == 0


def test_shadow_cull_distrusts_a_group_where_the_fit_explains_nothing():
    # Every note reads as unsupported -> the audio is misaligned, not the notes.
    notes = chord(0.0, [48, 55, 60, 64, 67])
    evidence = {note_key(n): 0.0 for n in notes}
    config = PlayabilityConfig(shadow_only_when_crowded=False)
    out, removed = prune_overtone_shadows(notes, evidence=evidence, config=config)
    assert removed == 0
    assert len(out) == 5


def test_shadow_cull_leaves_uncrowded_groups_alone_by_default():
    notes = chord(0.0, [48, 60])
    evidence = {note_key(n): 0.0 for n in notes}
    _, removed = prune_overtone_shadows(notes, evidence=evidence)
    assert removed == 0


# ---------------------------------------------------------------------------
# sustain trim
# ---------------------------------------------------------------------------


def test_trim_sustain_shortens_tails_instead_of_deleting():
    config = PlayabilityConfig(max_sustained=3)
    notes = [note(i * 0.1, 60 + i, dur=5.0) for i in range(6)]
    out, trimmed = trim_sustain(notes, config=config)
    assert len(out) == len(notes)          # nothing deleted
    assert trimmed > 0
    assert max_polyphony(out) <= 3


def test_trim_sustain_is_a_no_op_under_the_cap():
    notes = [note(0.0, 60), note(0.1, 64)]
    out, trimmed = trim_sustain(notes)
    assert trimmed == 0
    assert [n.t_off for n in out] == [n.t_off for n in notes]


# ---------------------------------------------------------------------------
# capacity reduction
# ---------------------------------------------------------------------------


def test_reduce_group_leaves_a_playable_chord_untouched():
    group = chord(0.0, [48, 55, 64, 67])
    kept = reduce_group_to_playable(group, evidence=None, harmony=HarmonyContext())
    assert len(kept) == 4


def test_reduce_group_keeps_melody_and_bass_when_it_must_cut():
    group = chord(0.0, [36, 43, 48, 55, 60, 64, 67, 72, 79, 84])
    context = SalienceContext(melody=frozenset({note_key(group[-1])}))
    kept = reduce_group_to_playable(group, context=context)
    pitches = [n.pitch for n in kept]
    assert is_hand_feasible(pitches)
    assert 36 in pitches       # bass anchor
    assert 84 in pitches       # melody line


def test_reduce_group_prefers_new_pitch_classes_over_doublings():
    # A C major triad voiced across three octaves, cut to what two hands hold.
    # With the doubling penalty all three chord tones survive; without it the
    # budget goes to a second C and the third is lost.
    group = chord(0.0, [48, 55, 60, 64, 67, 72, 76, 79, 84])
    config = PlayabilityConfig(max_notes_per_hand=2, max_hand_span=14)
    kept = reduce_group_to_playable(group, config=config)
    assert sorted({n.pitch % 12 for n in kept}) == [0, 4, 7]

    flat = SalienceWeights(doubling_penalty=0.0, new_pitch_class=0.0)
    without = reduce_group_to_playable(group, weights=flat, config=config)
    assert sorted({n.pitch % 12 for n in without}) == [0, 7]


def test_enforce_hand_capacity_makes_every_group_feasible():
    notes = chord(0.0, list(range(36, 96, 6))) + chord(1.0, [60, 64, 67])
    out, removed = enforce_hand_capacity(notes, evidence=None, harmony=HarmonyContext())
    assert removed > 0
    for group in group_by_onset(out):
        assert is_hand_feasible([n.pitch for n in group])


# ---------------------------------------------------------------------------
# harmony context
# ---------------------------------------------------------------------------


def test_chord_pitch_classes_reads_harmony_json_qualities():
    assert chord_pitch_classes("F#", "min7") == frozenset({6, 9, 1, 4})
    assert chord_pitch_classes("E", "sus4") == frozenset({4, 9, 11})
    assert chord_pitch_classes("nonsense", "maj") == frozenset()


def test_harmony_context_looks_up_by_time():
    context = harmony_context_from_json({
        "key": "A", "mode": "major",
        "events": [{"t": 0.0, "duration": 2.0, "root": "A", "quality": "maj"},
                   {"t": 2.0, "duration": 2.0, "root": "E", "quality": "maj"}],
    })
    assert context.chord_at(1.0) == frozenset({9, 1, 4})
    assert context.chord_at(3.0) == frozenset({4, 8, 11})
    assert context.chord_at(99.0) == frozenset()
    assert 9 in context.key_classes


# ---------------------------------------------------------------------------
# composer
# ---------------------------------------------------------------------------


def test_make_playable_reports_and_fixes_an_unplayable_chart():
    notes = []
    for i in range(4):
        notes.extend(chord(i * 0.5, [30, 42, 54, 60, 66, 72, 78, 84, 90, 96], dur=3.0))
    out, report = make_playable(notes)
    assert report["before"]["unplayable_groups"] > 0
    assert report["after"]["unplayable_groups"] == 0
    assert report["removed_total"] == len(notes) - len(out)
    for group in group_by_onset(out):
        assert is_hand_feasible([n.pitch for n in group])


def test_make_playable_never_empties_an_attack():
    notes = []
    for i in range(6):
        notes.extend(chord(i * 0.25, [24, 36, 48, 60, 72, 84, 96, 108], dur=2.0))
    before = {round(n.t_on, 4) for n in notes}
    out, _ = make_playable(notes)
    assert {round(n.t_on, 4) for n in out} == before


def test_make_playable_leaves_playable_music_alone():
    notes = chord(0.0, [48, 55, 64, 67]) + chord(1.0, [50, 57, 65, 69])
    out, report = make_playable(notes)
    assert report["removed_total"] == 0
    assert len(out) == len(notes)


# ---------------------------------------------------------------------------
# metrical position
# ---------------------------------------------------------------------------


def test_beat_grid_marks_downbeats_from_the_timeline():
    grid = beat_grid_from_json({"beats": [
        {"time": 0.0, "measure": 1}, {"time": 0.5, "measure": 1},
        {"time": 1.0, "measure": 1}, {"time": 1.5, "measure": 1},
        {"time": 2.0, "measure": 2},
    ]})
    assert grid.is_downbeat(2.02)
    assert not grid.is_downbeat(1.0)
    assert grid.is_on_beat(1.0)
    assert not grid.is_on_beat(1.25)


def test_beat_grid_without_a_timeline_is_inert():
    grid = BeatGrid()
    assert not grid.is_downbeat(0.0)
    assert not grid.is_on_beat(0.0)


# ---------------------------------------------------------------------------
# motifs
# ---------------------------------------------------------------------------


def _alternating_fourth(start: float, base: int, step: float = 0.25) -> list[MelodicNote]:
    """The figure the detector finds in What A God: an alternating fourth."""
    return [note(start + i * step, base + (5 if i % 2 else 0), dur=step * 0.9)
            for i in range(6)]


def test_find_motifs_picks_up_a_repeated_figure():
    notes: list[MelodicNote] = []
    for i in range(5):
        notes.extend(_alternating_fourth(i * 4.0, 72))
    keys, occurrences = find_motifs(notes)
    assert occurrences
    assert (5, -5, 5, -5, 5) in {o.pattern for o in occurrences}
    assert len(keys) >= 20


def test_find_motifs_ignores_a_figure_that_happens_once():
    notes = _alternating_fourth(0.0, 72) + [note(10.0 + i * 0.3, 60 + i) for i in range(8)]
    keys, occurrences = find_motifs(notes, motif_config=MotifConfig(min_occurrences=4))
    assert not occurrences
    assert not keys


def test_find_motifs_rejects_repeated_note_texture():
    # (0, 0, 7) is accompaniment, not a motif, and it recurs everywhere.
    notes: list[MelodicNote] = []
    for i in range(8):
        base = i * 2.0
        notes.extend([note(base, 60), note(base + 0.25, 60),
                      note(base + 0.5, 60), note(base + 0.75, 67)])
    _, occurrences = find_motifs(notes)
    assert (0, 0, 7) not in {o.pattern for o in occurrences}


def test_locate_patterns_finds_the_figure_under_a_held_chord_tone():
    # The same figure, with a higher note on top of every attack, so a
    # top-voice miner alone would never see it.
    notes: list[MelodicNote] = []
    for i in range(5):
        figure = _alternating_fourth(i * 4.0, 60)
        notes.extend(figure)
        notes.extend(note(n.t_on, 84, dur=0.2) for n in figure)
    keys, occurrences = find_motifs(notes)
    carried = {k[1] for k in keys}
    assert 60 in carried and 65 in carried


# ---------------------------------------------------------------------------
# salience
# ---------------------------------------------------------------------------


def test_salience_ranks_motif_above_everything_else():
    group = chord(0.0, [48, 60, 64, 67])
    context = SalienceContext(motif=frozenset({note_key(group[1])}))
    scores = [note_salience(n, context=context, group=group) for n in group]
    assert scores[1] == max(scores)


def test_salience_rewards_the_primary_source_over_a_supplementary_one():
    group = chord(0.0, [60, 62])
    provenance = {note_key(group[0]): ("primary",), note_key(group[1]): ("wide_mix",)}
    context = SalienceContext(provenance=provenance, primary_source="primary")
    first, second = (note_salience(n, context=context, group=group) for n in group)
    assert first > second


def test_salience_rewards_agreement_between_sources():
    # Compared in isolation: the bass-anchor and top-voice terms depend on
    # position within the group and would otherwise mask the provenance term.
    lonely = note(0.0, 60)
    corroborated = note(4.0, 60)
    provenance = {note_key(lonely): ("wide_mix",),
                  note_key(corroborated): ("wide_mix", "keys_only", "keys_guitar")}
    context = SalienceContext(provenance=provenance, primary_source="primary")
    assert (note_salience(corroborated, context=context, group=[corroborated])
            > note_salience(lonely, context=context, group=[lonely]))


def test_salience_rewards_a_downbeat_over_an_offbeat():
    grid = beat_grid_from_json({"beats": [{"time": 0.0, "measure": 1},
                                          {"time": 0.5, "measure": 1}]})
    on_beat = note(0.0, 60)
    off_beat = note(0.25, 60)
    context = SalienceContext(beats=grid)
    assert (note_salience(on_beat, context=context, group=[on_beat])
            > note_salience(off_beat, context=context, group=[off_beat]))


def test_motif_notes_survive_a_reduction_that_must_cut_hard():
    # An inner note of a ten-note cluster, with only two fingers a hand.
    group = chord(0.0, [30, 36, 42, 48, 54, 61, 66, 72, 78, 84])
    inner = group[5]
    config = PlayabilityConfig(max_notes_per_hand=2, max_hand_span=12)
    without = reduce_group_to_playable(group, config=config)
    assert inner.pitch not in [n.pitch for n in without]
    context = SalienceContext(motif=frozenset({note_key(inner)}))
    with_motif = reduce_group_to_playable(group, context=context, config=config)
    assert inner.pitch in [n.pitch for n in with_motif]


def test_make_playable_reports_the_motif_gate():
    notes: list[MelodicNote] = []
    for i in range(5):
        notes.extend(_alternating_fourth(i * 4.0, 72))
    _, report = make_playable(notes)
    assert report["motif_notes"] > 0
    assert report["motif_intact"] is True
