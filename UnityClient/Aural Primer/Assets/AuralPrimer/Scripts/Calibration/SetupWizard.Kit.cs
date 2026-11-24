// Copyright 2026 Nathanael Anderson. Licensed under the Apache License 2.0.
//
// The drum side of the wizard: a kit placed piece by piece, or a pad unit
// placed from three corners, either way tied to a spatial anchor of its own so
// it comes back in the same place next session.
//
// Three states, and every piece position is read in the space its state says:
//   placing  -- pinches arrive in world space, so pieces are held in world
//               space and drawn from the world root. Nothing is saved: the
//               profile on disk keeps the previous kit until the new one is
//               anchored, so Cancel can put it back.
//   live     -- pieces are relative to the kit anchor, which is loaded, and are
//               drawn on it. An empty anchor id means the anchor could not be
//               saved; the pieces are then world-space for this session only.
//   absent   -- the anchor did not re-localise in this room. The pieces are
//               kept, in case the player goes back to the room they belong in,
//               but nothing is drawn.

using System;
using System.Collections.Generic;
using System.Threading.Tasks;
using UnityEngine;

namespace AuralPrimer.Calibration
{
    public sealed partial class SetupWizard
    {
        /// <summary>
        /// The pads of a Roland SPD-S, far row first, each row left to right as
        /// the player sees it: the three edge triggers, then the back and front
        /// rows of main pads.
        /// </summary>
        /// <remarks>
        /// A standard kit folded onto the unit: cymbals on the edge triggers,
        /// snare in the middle with a tom and the closed hi-hat either side of
        /// it, and the kick in front between the low toms, where both hands
        /// reach it.
        /// </remarks>
        static readonly (string Id, string Label)[] PadOrder =
        {
            ("crash", "crash"),
            ("ride", "ride"),
            ("hihat_open", "hi-hat (open)"),
            ("tom_high", "high tom"),
            ("snare", "snare"),
            ("hihat_closed", "hi-hat (closed)"),
            ("tom_mid", "mid tom"),
            ("kick", "kick drum"),
            ("tom_low", "floor tom"),
        };

        /// <summary>Where each of those pads is, in the player's words.</summary>
        static readonly string[] PadPositions =
        {
            "left edge trigger", "middle edge trigger", "right edge trigger",
            "back-left pad", "back-middle pad", "back-right pad",
            "front-left pad", "front-middle pad", "front-right pad",
        };

        static readonly string[] PadCornerNames =
        {
            "far-left corner of the left edge trigger",
            "far-right corner of the right edge trigger",
            "near-left corner of the front-left pad",
        };

        /// <summary>
        /// How long after asking for a pad its strike is ignored.
        /// </summary>
        /// <remarks>
        /// A pad unit bleeds a strike into its neighbours, and a kit pad can
        /// double-trigger. Without a pause the bleed from one pad, arriving a
        /// few milliseconds after it, would be learned as the next pad's note.
        /// </remarks>
        const float LearnSettleSeconds = 0.25f;

        /// <summary>Placing a pad unit's grid rather than a kit piece by piece.</summary>
        bool _padGrid;
        readonly Vector3[] _padCorners = new Vector3[3];
        int _cornerIndex;
        PadGrid.Problem _padProblem;
        /// <summary>The grid's pieces, placed but waiting for their notes.</summary>
        List<KitPiece> _gridPieces;
        /// <summary>The piece a just-struck note already belongs to, when it was not this one.</summary>
        string _duplicateOf;
        /// <summary>When the wizard started listening for the current pad.</summary>
        float _learnSince;
        /// <summary>Pieces whose note was learned in this pass, as opposed to kept from before.</summary>
        readonly HashSet<string> _learnedThisPass = new();

        /// <summary>Pieces are being placed, in world space.</summary>
        bool _placingKit;
        /// <summary>The profile's pieces are valid in this room this session.</summary>
        bool _kitLive;
        /// <summary>The saved kit was not found in this room at startup.</summary>
        bool _kitMissing;
        /// <summary>The anchor a placement in progress will replace, erased once it is.</summary>
        string _replacedKitAnchorId;
        /// <summary>The id of the kit anchor currently loaded, if any.</summary>
        string _liveKitAnchorId;
        /// <summary>The kit a placement in progress replaces was live in world space.</summary>
        bool _replacedKitWasLiveWorld;
        /// <summary>The host was last sent a kit with pads in it.</summary>
        bool _kitLayoutSent;

        KeyboardAnchor _kitAnchor;

        bool IsDrums => _profile is { instrument: Instrument.Drums };

        (string Id, string Label)[] CurrentKitOrder => _padGrid ? PadOrder : KitOrder;

        static bool IsKitPlacement(Step step) =>
            step is Step.PlacePieceCentre or Step.PlacePieceRim or Step.LearnPieceNote or Step.PlacePadCorner;

        /// <summary>
        /// The kit's own spatial anchor.
        /// </summary>
        /// <remarks>
        /// A second anchor beside the keyboard's, built at runtime like the drum
        /// overlay. One anchor holds one place, and re-marking the keyboard
        /// replaces its anchor, which would carry the drums off with it.
        /// </remarks>
        KeyboardAnchor KitAnchor
        {
            get
            {
                if (_kitAnchor == null)
                {
                    var host = new GameObject("KitAnchor");
                    host.transform.SetParent(transform, worldPositionStays: false);
                    _kitAnchor = host.AddComponent<KeyboardAnchor>();
                }
                return _kitAnchor;
            }
        }

        /// <summary>The space the profile's pieces are in, or null for world space.</summary>
        Transform KitSpace =>
            _placingKit || _kitAnchor == null || string.IsNullOrEmpty(_profile?.kitAnchorId)
                ? null
                : _kitAnchor.Space;

        /// <summary>
        /// Seat the drum overlay where the pieces are, or take the kit off it.
        /// </summary>
        /// <remarks>
        /// A kit whose anchor is not loaded is not drawn at all. Its pieces are
        /// relative to an anchor that is not here, and drawn anyway they would
        /// put cues in mid-air near the world origin.
        /// </remarks>
        void ShowKit()
        {
            EnsureDrumOverlay();
            if (drumOverlay == null) return;
            var drawn = _placingKit || _kitLive;
            Reseat(drumOverlay.transform, drawn ? KitSpace : null);
            drumOverlay.SetProfile(drawn ? _profile : null);
        }

        void ChooseDrums()
        {
            if (_busy) return;
            LeaveGuitarMode();
            HideUnplacedKeyboard();
            ResumeOrPlaceKit(padGrid: false);
        }

        void ChooseDrumPad()
        {
            if (_busy) return;
            LeaveGuitarMode();
            HideUnplacedKeyboard();
            ResumeOrPlaceKit(padGrid: true);
        }

        /// <summary>
        /// Hide a keyboard whose anchor is not loaded.
        /// </summary>
        /// <remarks>
        /// Its edges are relative to that anchor, so drawn without it the keys
        /// and lane land in the wrong place -- in the way of the drums, which is
        /// what the player is here for.
        /// </remarks>
        void HideUnplacedKeyboard()
        {
            if (_profile is not { IsCalibrated: true }) return;
            if (keyboardAnchor != null && keyboardAnchor.IsAnchored) return;
            if (overlay != null) overlay.gameObject.SetActive(false);
            if (highway != null) highway.gameObject.SetActive(false);
        }

        /// <summary>
        /// Take the keyboard's visuals off its anchor before the anchor is replaced.
        /// </summary>
        /// <remarks>
        /// Loading or creating an anchor destroys the previous anchor's
        /// GameObject, and the overlay and lane seated on it would go with it.
        /// RefreshVisuals seats them on whatever anchor comes back.
        /// </remarks>
        void UnseatKeyboardVisuals()
        {
            if (overlay != null) Reseat(overlay.transform, null);
            if (highway != null) Reseat(highway.transform, null);
        }

        /// <summary>Clear the panel while an await runs, so no stale button can act.</summary>
        void ShowBusy(string message)
        {
            panel?.SetButtons();
            panel?.SetBody(message);
        }

        /// <summary>
        /// Go back to the kit already set up, or place one.
        /// </summary>
        /// <remarks>
        /// Switching instruments and back must not cost a recalibration, any
        /// more than it does for the keyboard or the guitar. Placing again is
        /// what Redo kit is for.
        /// </remarks>
        async void ResumeOrPlaceKit(bool padGrid)
        {
            if (_busy) return;
            if (_profile is { IsKitCalibrated: true } && _profile.kitIsPadGrid == padGrid)
            {
                _busy = true;
                ShowBusy("Looking for your kit...");
                bool found;
                try
                {
                    found = await RestoreKitAnchorAsync();
                }
                finally
                {
                    _busy = false;
                }
                if (found)
                {
                    _profile.instrument = Instrument.Drums;
                    _profile.Save();
                    ShowKit();
                    SendKitLayout();
                    EnterStep(Step.Menu);
                    return;
                }
            }
            if (padGrid) StartPadCalibration();
            else StartKitCalibration();
        }

        /// <summary>Begin placing a pad unit, from its first corner.</summary>
        public void StartPadCalibration()
        {
            if (_busy) return;
            PrepareKitForPlacing(keepPieces: false);
            _padGrid = true;
            _cornerIndex = 0;
            _padProblem = PadGrid.Problem.None;
            _gridPieces = null;
            EnterStep(Step.PlacePadCorner);
        }

        void RedoKit()
        {
            if (_profile is { kitIsPadGrid: true }) StartPadCalibration();
            else StartKitCalibration();
        }

        /// <summary>
        /// Bring the kit into world space, ready to place.
        /// </summary>
        /// <remarks>
        /// Pinches arrive in world space and the new anchor does not exist until
        /// the last one is in, so everything is held in world space until then.
        /// Pieces kept from before -- a redo that skips a piece keeps the old
        /// one -- are brought out of the old anchor first, or they would be read
        /// as world coordinates and land somewhere else entirely. The old anchor
        /// itself stays until the new one replaces it, so Cancel loses nothing.
        /// </remarks>
        void PrepareKitForPlacing(bool keepPieces)
        {
            _profile ??= NewKeyboardProfile();
            _profile.instrument = Instrument.Drums;

            if (keepPieces && (_kitLive || _placingKit))
            {
                var space = KitSpace;
                foreach (var piece in _profile.kitPieces) piece?.Rebase(space, null);
            }
            else
            {
                _profile.kitPieces.Clear();
            }

            if (!_placingKit)
            {
                _replacedKitAnchorId = _profile.kitAnchorId;
                // A kit whose anchor could not be saved is world-space and good
                // only for this session; Cancel has to know it was live, since
                // reloading it from disk cannot tell.
                _replacedKitWasLiveWorld = _kitLive && string.IsNullOrEmpty(_profile.kitAnchorId);
            }
            _profile.kitAnchorId = null;
            _placingKit = true;
            _kitLive = false;
            _kitMissing = false;

            _padGrid = false;
            _pendingPiece = null;
            _kitIndex = 0;
            _learnedThisPass.Clear();
            ClearPinchMarkers();

            // Drawn while placing, so a pad whose note has just been learned
            // flashes when it is hit.
            ShowKit();
        }

        /// <summary>
        /// Abandon a placement and put back whatever kit there was.
        /// </summary>
        /// <remarks>
        /// Nothing is saved while placing, so the profile on disk is still the
        /// previous one, and its anchor was never erased.
        /// </remarks>
        async void CancelKitPlacing()
        {
            if (_busy) return;
            _busy = true;
            ShowBusy("Putting the kit back...");
            try
            {
                _placingKit = false;
                _replacedKitAnchorId = null;
                _pendingPiece = null;
                ClearPinchMarkers();

                _profile = CalibrationProfile.Load(profileName)
                        ?? new CalibrationProfile { profileName = profileName, fretboard = _profile.fretboard };
                _kitLive = _replacedKitWasLiveWorld && string.IsNullOrEmpty(_profile.kitAnchorId);
                if (_profile.IsKitCalibrated) await RestoreKitAnchorAsync();
                else _kitLive = false;
                RefreshVisuals();
                // Back on the keyboard's hinge only for a keyboard that is
                // really there; otherwise the panel stays where it is.
                if (!IsDrums && !IsGuitar && keyboardAnchor != null && keyboardAnchor.IsAnchored)
                {
                    DockBesideKeyboard();
                }
            }
            finally
            {
                _busy = false;
            }

            // Back where the player came from: the guitar, which choosing a kit
            // had switched off, or the kit they were redoing.
            if (IsGuitar && _profile.IsFretboardChecked) StartGuitar(_profile.fretboard.isBass);
            else if (IsDrums && _kitLive) EnterStep(Step.Menu);
            else EnterStep(Step.PickInstrument);
        }

        /// <summary>A pinch while placing a pad unit's corners.</summary>
        void OnPadCornerPinch(Vector3 position)
        {
            _padCorners[_cornerIndex] = position;
            DropPinchMarker(position, _cornerIndex == 0 ? leftPinchMaterial : rightPinchMaterial,
                            $"Corner {_cornerIndex + 1}");
            _cornerIndex++;
            if (_cornerIndex < _padCorners.Length)
            {
                _padProblem = PadGrid.Problem.None;
                EnterStep(Step.PlacePadCorner);
                return;
            }

            var head = Camera.main;
            var towardPlayer = head != null ? head.transform.position - _padCorners[0] : Vector3.up;
            _padProblem = PadGrid.Check(_padCorners[0], _padCorners[1], _padCorners[2], towardPlayer);
            if (_padProblem != PadGrid.Problem.None)
            {
                Debug.LogWarning($"[wizard] pad corners rejected: {_padProblem}");
                _cornerIndex = 0;
                ClearPinchMarkers();
                EnterStep(Step.PlacePadCorner);
                return;
            }

            var ids = new string[PadOrder.Length];
            for (var i = 0; i < ids.Length; i++) ids[i] = PadOrder[i].Id;
            _gridPieces = PadGrid.Build(_padCorners[0], _padCorners[1], _padCorners[2], ids, PadGrid.SpdsRows);
            _kitIndex = 0;
            _pendingPiece = _gridPieces[0];
            EnterStep(Step.LearnPieceNote);
        }

        /// <summary>
        /// The kit was struck.
        /// </summary>
        /// <remarks>
        /// Notes are learned from the strike event, not from the held-note set
        /// the keyboard steps read. A drum hit is a note-on with an immediate
        /// note-off, which the held set polled per frame usually never sees, and
        /// a gamepad kit's strikes are never in it at all.
        /// </remarks>
        void OnDrumStrike(byte note, byte velocity, ulong hostClockUs)
        {
            if (_busy || _step != Step.LearnPieceNote || _pendingPiece == null) return;
            if (Time.unscaledTime - _learnSince < LearnSettleSeconds) return;

            var owner = _profile.PieceForNote(note);
            if (owner != null && owner.id != _pendingPiece.id)
            {
                // One note, one pad: a second pad answering with a note already
                // learned would be found as the first one on every strike.
                // A piece kept from an earlier pass whose turn is still to come
                // gives its note up: that is how two swapped pads get fixed, and
                // it learns again when it is asked for. One already passed over
                // this time keeps it, or a stray hit on it would quietly delete it.
                if (_learnedThisPass.Contains(owner.id) || !TurnIsStillToCome(owner.id))
                {
                    _duplicateOf = LabelFor(owner.id);
                    RenderStep();
                    return;
                }
                owner.midiNote = -1;
            }

            _pendingPiece.midiNote = note;
            // Replace rather than append: re-running the wizard over a kit that
            // is already placed should move the pad, not leave the old position
            // behind to be found by the next lookup.
            _profile.kitPieces.RemoveAll(x => x != null && x.id == _pendingPiece.id);
            _profile.kitPieces.Add(_pendingPiece);
            _learnedThisPass.Add(_pendingPiece.id);
            _pendingPiece = null;
            SendKitLayout();
            NextKitPiece();
        }

        bool TurnIsStillToCome(string id)
        {
            var order = CurrentKitOrder;
            for (var i = _kitIndex + 1; i < order.Length; i++)
            {
                if (order[i].Id == id) return true;
            }
            return false;
        }

        string LabelFor(string id)
        {
            foreach (var entry in CurrentKitOrder)
            {
                if (entry.Id == id) return entry.Label;
            }
            return id;
        }

        /// <summary>
        /// Anchor the placed kit, then show it for checking.
        /// </summary>
        async void FinishKitAsync()
        {
            // A kept piece whose note moved to another pad, and was then skipped.
            _profile.kitPieces.RemoveAll(x => x == null || !x.IsComplete);
            if (!_profile.IsKitCalibrated)
            {
                // Everything skipped: there is no new kit, so keep the old one.
                CancelKitPlacing();
                return;
            }

            _busy = true;
            // The previous step's buttons still point at that step. Pressed
            // while the anchor is saved, Skip would finish the kit a second time.
            panel?.SetButtons();
            panel?.SetBody("Saving the kit...");
            try
            {
                // Committed now, so the old kit's anchor goes. The overlay sits
                // at the world root while placing, so it does not go with it.
                if (!string.IsNullOrEmpty(_replacedKitAnchorId)) await KitAnchor.EraseAsync(_replacedKitAnchorId);
                _replacedKitAnchorId = null;
                _liveKitAnchorId = null;

                // At the middle of the kit, so the saved pose is a place that
                // means something rather than an arbitrary point.
                var centre = Vector3.zero;
                foreach (var piece in _profile.kitPieces) centre += piece.centre;
                centre /= _profile.kitPieces.Count;

                var id = await KitAnchor.CreateAndSaveAsync(new Pose(centre, Quaternion.identity));
                _placingKit = false;
                if (!string.IsNullOrEmpty(id) && KitAnchor.Space != null)
                {
                    _profile.kitAnchorId = id;
                    _liveKitAnchorId = id;
                    foreach (var piece in _profile.kitPieces) piece.Rebase(null, KitSpace);
                }
                else
                {
                    // Left in world space: good for this session, and placed
                    // again next time, since no anchor will bring it back.
                    Debug.LogWarning("[wizard] the kit's anchor was not saved; it will not survive a restart");
                    _profile.kitAnchorId = null;
                }

                _profile.kitIsPadGrid = _padGrid;
                _profile.instrument = Instrument.Drums;
                _kitLive = true;
                _profile.Save();
                RefreshVisuals();
            }
            catch (Exception e)
            {
                Debug.LogError($"[wizard] anchoring the kit failed: {e.Message}");
                _busy = false;
                if (_padGrid) StartPadCalibration();
                else StartKitCalibration();
                return;
            }
            finally
            {
                _busy = false;
            }
            EnterStep(Step.VerifyKit);
        }

        /// <summary>
        /// Re-localise the kit's anchor. True when the kit can be drawn.
        /// </summary>
        /// <remarks>
        /// A failure keeps the pieces: the room may simply be a different one,
        /// and the kit is still where it was in the room it belongs to.
        /// </remarks>
        async Task<bool> RestoreKitAnchorAsync()
        {
            if (string.IsNullOrEmpty(_profile.kitAnchorId))
            {
                // World-space pieces mean something only in the session that
                // placed them.
                return _kitLive;
            }
            if (_kitAnchor != null && _kitAnchor.Space != null && _liveKitAnchorId == _profile.kitAnchorId)
            {
                return _kitLive = true;
            }

            // The overlay may be seated on an anchor that loading replaces, and
            // would be destroyed with it.
            if (drumOverlay != null) Reseat(drumOverlay.transform, null);
            _kitLive = await KitAnchor.TryLoadAsync(_profile.kitAnchorId);
            _liveKitAnchorId = _kitLive ? _profile.kitAnchorId : null;
            if (!_kitLive) Debug.Log("[wizard] the kit's anchor is not in this room; it is kept but not drawn");
            return _kitLive;
        }

        /// <summary>Bring back a saved kit at startup.</summary>
        async void RestoreKitAsync()
        {
            _busy = true;
            var restored = false;
            try
            {
                restored = await RestoreKitAnchorAsync();

                // A keyboard on the same profile comes back with it. One that
                // cannot is hidden rather than drawn in the wrong place.
                UnseatKeyboardVisuals();
                if (_profile.IsAnchored && keyboardAnchor != null) await keyboardAnchor.TryLoadAsync(_profile.anchorId);
                HideUnplacedKeyboard();
            }
            catch (Exception e)
            {
                Debug.LogError($"[wizard] restoring the kit failed: {e.Message}");
            }
            finally
            {
                _busy = false;
            }

            RefreshVisuals();
            panel?.PlaceInFrontOfUser();
            if (restored)
            {
                EnterStep(Step.Menu);
            }
            else
            {
                // Not straight into placing: the player may be in another room
                // on purpose, with a different instrument in front of them.
                _kitMissing = true;
                EnterStep(Step.PickInstrument);
            }
        }

        string KitMissingText() => _kitMissing && !_kitLive
            ? $"<color=#FFB020>Your {(_profile is { kitIsPadGrid: true } ? "drum pad" : "drum kit")} "
              + "wasn't found in this room. Pick it to place it here.</color>\n\n"
            : "";

        /// <summary>The kit steps' panels. True when the step was one of them.</summary>
        bool RenderKitStep()
        {
            switch (_step)
            {
                case Step.PlacePadCorner:
                    panel?.SetTitle($"Place the pad ({_cornerIndex + 1} of 3)");
                    panel?.SetBody(PadProblemText()
                        + (_cornerIndex == 0
                            ? "<b>Put your sticks down first</b> - a hand holding one is hard to track. "
                              + "Set the pad to send on <b>MIDI channel 10</b>.\n\n"
                            : "")
                        + $"<b>Pinch the {PadCornerNames[_cornerIndex]}</b>"
                        + (_cornerIndex == 0 ? " - the thin strip farthest from you." : "."));
                    if (_cornerIndex > 0) panel?.SetButtons(("Restart", StartPadCalibration), ("Cancel", CancelKitPlacing));
                    else panel?.SetButtons(("Cancel", CancelKitPlacing));
                    return true;

                case Step.PlacePieceCentre:
                    panel?.SetTitle($"Place the {KitOrder[_kitIndex].Label}");
                    panel?.SetBody(
                        // Said before the first pinch, not after a bad one: a
                        // fist round a stick is most of a closed hand, and hand
                        // tracking reads it poorly. A pad placed from a misread
                        // pinch is wrong for the whole session.
                        "<b>Put your sticks down first</b> - a hand holding one "
                        + "is hard to track.\n\n"
                        + $"<b>Pinch at the middle</b> of the {KitOrder[_kitIndex].Label}.\n\n"
                        + $"<i>No {KitOrder[_kitIndex].Label}? Press Skip.</i>");
                    panel?.SetButtons(("Skip", SkipKitPiece), ("Cancel", CancelKitPlacing));
                    return true;

                case Step.PlacePieceRim:
                    panel?.SetTitle($"Place the {KitOrder[_kitIndex].Label}");
                    panel?.SetBody(
                        $"Now <b>pinch at the edge</b> of the {KitOrder[_kitIndex].Label}, "
                        + "so its size is known.");
                    panel?.SetButtons(("Redo", () => EnterStep(Step.PlacePieceCentre)), ("Cancel", CancelKitPlacing));
                    return true;

                case Step.LearnPieceNote:
                {
                    var label = CurrentKitOrder[_kitIndex].Label;
                    panel?.SetTitle($"Which note is the {label}?");
                    var duplicate = _duplicateOf != null
                        ? $"<color=#FFB020>That note is already the <b>{_duplicateOf}</b>. "
                          + "Each pad needs a note of its own.</color>\n\n"
                        : "";
                    panel?.SetBody(duplicate + (_padGrid
                        ? $"<b>Hit the {PadPositions[_kitIndex]} once.</b> It will be the {label}.\n\n"
                          + "The pad's own note is asked rather than assumed, so a pad "
                          + "whose notes were changed still lines up."
                        : $"<b>Hit the {label} once.</b>\n\n"
                          + "Kits disagree about note numbers, so this one is "
                          + "asked rather than assumed."));
                    if (_padGrid) panel?.SetButtons(("Skip", SkipKitPiece), ("Cancel", CancelKitPlacing));
                    else panel?.SetButtons(("Skip", SkipKitPiece),
                                           ("Redo", () => EnterStep(Step.PlacePieceCentre)),
                                           ("Cancel", CancelKitPlacing));
                    return true;
                }

                case Step.VerifyKit:
                    panel?.SetTitle("Check the kit");
                    panel?.SetBody(
                        $"<b>{_profile.kitPieces.Count} piece(s)</b> placed.\n\n"
                        + "Play round the kit. Each pad should flash as you hit it.\n\n"
                        + "If a flash lands on the wrong drum, press Redo.");
                    panel?.SetButtons(("Done", () => EnterStep(Step.Menu)), ("Redo", RedoKit));
                    return true;

                case Step.Menu when IsDrums && _kitLive:
                    panel?.SetTitle("AuralPrimer");
                    panel?.SetBody(
                        $"<b>{(_profile.kitIsPadGrid ? "Drum pad" : "Drum kit")}</b>, "
                        + $"{_profile.kitPieces.Count} piece(s) placed.\n\n"
                        + "Pick a song and play along. A ring fills on each pad as its "
                        + "hit comes, and notes fall onto the pad they are for.");
                    panel?.SetButtons(
                        ("Songs", () => EnterStep(Step.Songs)),
                        ("Check kit", () => EnterStep(Step.VerifyKit)),
                        ("Redo kit", RedoKit),
                        ("Instrument", () => EnterStep(Step.PickInstrument)));
                    return true;
            }
            return false;
        }

        string PadProblemText() => _padProblem switch
        {
            PadGrid.Problem.TooSmall =>
                "<color=#FFB020>Those corners are too close together for a pad unit. "
                + "Pinch the outside corners of the corner pads.</color>\n\n",
            PadGrid.Problem.TooLarge =>
                "<color=#FFB020>Those corners are too far apart for a pad unit. "
                + "Pinch on the pads themselves.</color>\n\n",
            PadGrid.Problem.Skewed =>
                "<color=#FFB020>The third corner was not straight toward you from the first. "
                + "Pinch the near-left corner of the front-left pad.</color>\n\n",
            PadGrid.Problem.Mirrored =>
                "<color=#FFB020>Left and right came out swapped. Start from the far-LEFT "
                + "corner, then the far-RIGHT one.</color>\n\n",
            _ => "",
        };
    }
}
