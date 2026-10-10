// Copyright 2026 Nathanael Anderson. Licensed under the Apache License 2.0.
//
// The guitar (and bass) path: which controller is on the guitar, a one-time
// alignment check, and the standing menu while playing.
//
// The printed mount fixes where the controller sits, so the neck's offset from
// it starts as the CAD's guess (MountOffset.Cad). The check then measures it:
// three touches -- the nut beside the lowest string, the nut beside the
// highest, and the 12th fret -- with the free controller's tip and trigger, or
// a pinch. The result is stored against the controller's grip pose, so it
// survives the player moving, and saved on the profile beside the keyboard
// and the kit (docs/guitar-nashville-fretboard-plan-2026-09-30.md).
//
// Same rules as the keyboard (docs/unity-mr-client-plan-2026-08-18.md §4): the
// menu floats up in the air where hands are raised and well tracked, and
// play needs no gesture at all. The check is the one time a hand goes to the
// strings on purpose, and even there the free controller's trigger does the
// job without a gesture.

using UnityEngine;
using NVector3 = System.Numerics.Vector3;

namespace AuralPrimer.Calibration
{
    public sealed partial class SetupWizard
    {
        [Header("Guitar")]
        [Tooltip("Built at runtime when unset, like the drum overlay.")]
        [SerializeField] GuitarAnchor guitarAnchor;
        [Tooltip("Built at runtime when unset, under the guitar anchor.")]
        [SerializeField] GuitarViews guitarViews;

        /// <summary>Fine-nudge steps: a millimetre, and a degree of roll.</summary>
        const float GuitarNudgeMetres = 0.001f;
        const float GuitarRollStepDegrees = 1f;
        const float GuitarTiltStepDegrees = 5f;

        /// <summary>How long the controller on the guitar may go unseen before the panel says what to change.</summary>
        const float GuitarUnseenHelpSeconds = 4f;

        NVector3 _nutLowest;
        NVector3 _nutHighest;
        bool _haveNutHighest;
        FretboardSolver.Result _check;
        bool _haveCheck;
        string _guitarCaptureProblem;
        float _guitarModeSince;
        bool _guitarHelpShown;
        string _shownGuitarProblem;

        bool IsGuitar => _profile is { instrument: Instrument.Guitar };

        static bool IsGuitarCapture(Step step) =>
            step is Step.GuitarNutLowest or Step.GuitarNutHighest or Step.GuitarTwelfth;

        static bool IsGuitarSetup(Step step) => step == Step.GuitarHand || IsGuitarCapture(step);

        /// <summary>A clean keyboard calibration that keeps the guitar measured on the same profile.</summary>
        CalibrationProfile NewKeyboardProfile() => new()
        {
            profileName = profileName,
            fretboard = _profile?.fretboard ?? new FretboardProfile(),
        };

        /// <summary>
        /// Build the neck's anchor and highway if the scene does not carry them.
        /// </summary>
        /// <remarks>
        /// Created rather than authored, for the drum overlay's reason: a
        /// serialised reference is one more thing a scene merge can drop, and
        /// the failure would be silent -- a calibrated guitar with nothing on it.
        /// </remarks>
        void EnsureGuitar()
        {
            if (guitarAnchor == null) guitarAnchor = new GameObject("Guitar Neck").AddComponent<GuitarAnchor>();
            if (guitarViews == null)
            {
                var lane = new GameObject("Guitar Views");
                lane.transform.SetParent(guitarAnchor.transform, false);
                guitarViews = lane.AddComponent<GuitarViews>();
            }
            guitarViews.Bind(link, guitarAnchor);
            guitarAnchor.TipCaptured -= OnGuitarTouch;
            guitarAnchor.TipCaptured += OnGuitarTouch;
        }

        /// <summary>Set up, or go back to, a guitar -- or a bass.</summary>
        public void StartGuitar(bool bass)
        {
            _profile ??= new CalibrationProfile { profileName = profileName };
            // A different kind of neck starts from that kind's defaults; the
            // same kind keeps what was measured.
            if (_profile.fretboard == null || _profile.fretboard.isBass != bass)
            {
                _profile.fretboard = bass ? FretboardProfile.Bass() : FretboardProfile.Guitar();
            }
            _profile.instrument = Instrument.Guitar;
            EnterGuitarMode();

            if (_profile.IsFretboardChecked)
            {
                // Remember the choice, so the next launch comes back to the guitar.
                _profile.Save();
                EnterStep(Step.Menu);
            }
            else
            {
                EnterStep(Step.GuitarHand);
            }
        }

        void EnterGuitarMode()
        {
            EnsureGuitar();
            SetKeyboardVisualsActive(false);
            guitarAnchor.gameObject.SetActive(true);
            guitarAnchor.Apply(_profile.fretboard);
            guitarViews.Apply(_profile.fretboard);
            ApplyGuitarDisplay();
            // The host sends this part's chart, frets and all (protocol §7b).
            link?.SetInstrument(_profile.fretboard.Role);
            _guitarModeSince = Time.unscaledTime;
            _guitarHelpShown = false;
            // No instrument to dock beside: the menu floats where it is put.
            panel?.PlaceInFrontOfUser();
        }

        /// <summary>Back to keys or drums: the neck goes, the keyboard comes back.</summary>
        void LeaveGuitarMode()
        {
            if (guitarAnchor != null) guitarAnchor.gameObject.SetActive(false);
            SetKeyboardVisualsActive(true);
            link?.SetInstrument("keys");
        }

        /// <summary>
        /// Show or hide everything that belongs to the keyboard.
        /// </summary>
        /// <remarks>
        /// The keys lane draws whatever CHART arrives, and in guitar mode that
        /// is the guitar part -- which would rain down on a keyboard nobody is
        /// playing.
        /// </remarks>
        void SetKeyboardVisualsActive(bool active)
        {
            if (overlay != null) overlay.gameObject.SetActive(active);
            if (highway != null) highway.gameObject.SetActive(active);
            if (drumOverlay != null) drumOverlay.gameObject.SetActive(active);
            if (active) return;
            HideLaneHandle();
            _menuHinge?.Hide();
        }

        void ChooseKeyboard()
        {
            LeaveGuitarMode();
            _profile ??= NewKeyboardProfile();
            _profile.instrument = Instrument.Keyboard;
            if (_profile.IsCalibrated || _profile.IsFretboardChecked) _profile.Save();
            if (_profile.IsAnchored) RestoreAsync();
            else EnterStep(Step.LowestKey);
        }

        void ChooseDrums()
        {
            LeaveGuitarMode();
            StartKitCalibration();
        }

        void ChooseGuitarHand(bool left)
        {
            _profile.fretboard.controllerOnLeft = left;
            guitarAnchor.Apply(_profile.fretboard);
            _guitarModeSince = Time.unscaledTime;
            StartGuitarCheck();
        }

        /// <summary>Begin the three touches, from the offset already in force.</summary>
        void StartGuitarCheck()
        {
            // Back to the saved offset (or the CAD's): a half-finished nudge
            // from before is not what the check should start from.
            guitarAnchor.Apply(_profile.fretboard);
            _haveCheck = false;
            _haveNutHighest = false;
            _guitarCaptureProblem = null;
            ClearPinchMarkers();
            EnterStep(Step.GuitarNutLowest);
        }

        /// <summary>
        /// A touch, from the free controller's trigger or a pinch, during the check.
        /// </summary>
        void OnGuitarTouch(Vector3 world)
        {
            if (!IsGuitarCapture(_step) || guitarAnchor == null) return;

            if (!guitarAnchor.TryWorldToGrip(world, out var inGrip))
            {
                _guitarCaptureProblem =
                    "The controller on the guitar isn't tracked right now, so that touch "
                  + "can't be placed. Keep its face toward the headset and touch again.";
                RenderStep();
                return;
            }

            _guitarCaptureProblem = null;
            DropPinchMarker(world, _step == Step.GuitarNutHighest ? rightPinchMaterial : leftPinchMaterial,
                            _step.ToString());

            switch (_step)
            {
                case Step.GuitarNutLowest:
                    _nutLowest = inGrip;
                    EnterStep(Step.GuitarNutHighest);
                    break;

                case Step.GuitarNutHighest:
                    _nutHighest = inGrip;
                    _haveNutHighest = true;
                    EnterStep(Step.GuitarTwelfth);
                    break;

                case Step.GuitarTwelfth:
                    SolveGuitarCheck(inGrip);
                    EnterStep(Step.GuitarCheck);
                    break;
            }
        }

        /// <summary>Skip the second nut touch and keep the mount's roll.</summary>
        void KeepMountRoll()
        {
            _haveNutHighest = false;
            EnterStep(Step.GuitarTwelfth);
        }

        void SolveGuitarCheck(NVector3 twelfth)
        {
            var neck = _profile.fretboard.Geometry;
            _check = _haveNutHighest
                ? FretboardSolver.SolveThree(neck, _nutLowest, _nutHighest, twelfth)
                // Roll from whatever is in force: the CAD's, or a previous check's.
                : FretboardSolver.SolveTwo(neck, guitarAnchor.Offset,
                                           new NVector3(0f, 0f, neck.StringZ(0, 0f)), _nutLowest,
                                           new NVector3(neck.TwelfthX, 0f, 0f), twelfth);
            _haveCheck = true;

            // Shown at once, before saving: the player judges it against the
            // real frets, which is what the nudges are for.
            guitarAnchor.SetOffset(_check.Offset);
            Debug.Log($"[wizard] guitar check: nut-12th {_check.MeasuredMetres * 1000f:F1} mm "
                    + $"(expected {_check.ExpectedMetres * 1000f:F1}, {_check.ErrorRatio:P1} off), "
                    + $"roll {(_check.RollMeasured ? "measured" : "from the mount")}");
        }

        void NudgeGuitar(Vector3 neckLocal, float rollDegrees)
        {
            guitarAnchor.SetOffset(FretboardSolver.Nudge(
                guitarAnchor.Offset, GuitarAnchor.ToNumerics(neckLocal), rollDegrees));
            RenderStep();
        }

        void SaveGuitarCheck()
        {
            var offset = guitarAnchor.Offset;
            var neck = _profile.fretboard;
            neck.neckOriginInGrip = GuitarAnchor.ToUnity(offset.Position);
            neck.neckRotationInGrip = GuitarAnchor.ToUnity(offset.Rotation);
            neck.isChecked = true;
            _profile.instrument = Instrument.Guitar;
            _profile.Save();
            guitarAnchor.Apply(neck);
            ClearPinchMarkers();
            EnterStep(Step.Menu);
        }

        void TiltGuitarLane(float degrees)
        {
            var neck = _profile.fretboard;
            neck.laneTiltDegrees = Mathf.Clamp(neck.laneTiltDegrees + degrees, 0f, FretboardGeometry.MaxTiltDegrees);
            _profile.Save();
            RenderStep();
        }

        /// <summary>
        /// Per frame: re-render the panel when something it describes changes.
        /// </summary>
        /// <remarks>
        /// The menu says why nothing is drawn, and the setup says when the
        /// controller cannot be seen -- both change after the step was entered:
        /// a chart arrives, the controller drops out.
        /// </remarks>
        void PumpGuitar()
        {
            if (!IsGuitar || guitarAnchor == null) return;

            var since = guitarAnchor.HasPose ? guitarAnchor.SecondsLost : Time.unscaledTime - _guitarModeSince;
            var unseen = !guitarAnchor.IsTracked && since > GuitarUnseenHelpSeconds;
            var problem = guitarViews != null ? guitarViews.Problem ?? guitarViews.Notice : null;

            var changed = unseen != _guitarHelpShown;
            var describesProblem = _step == Step.Menu || _step == Step.GuitarPickView;
            if (describesProblem && problem != _shownGuitarProblem) changed = true;
            _guitarHelpShown = unseen;
            _shownGuitarProblem = problem;

            if (changed && (describesProblem || IsGuitarSetup(_step))) RenderStep();
        }

        // ---- Views ----------------------------------------------------------------

        /// <summary>The five views, in the order the picker lists them, with what each is.</summary>
        static readonly (GuitarView View, string Name, string Detail)[] ViewChoices =
        {
            (GuitarView.Highway, "Neck highway", "notes fly in and land on the frets"),
            (GuitarView.Coach, "Neck coach", "dots on the strings; next notes count down"),
            (GuitarView.Nashville, "Nashville neck", "numbers by pentatonic position"),
            (GuitarView.Lanes, "String lanes", "a floating board of falling lanes"),
            (GuitarView.Tab, "Tab scroll", "a floating tab, high e on top"),
        };

        /// <summary>Hand the views what the menu chose.</summary>
        void ApplyGuitarDisplay()
        {
            if (guitarViews == null || _profile == null) return;
            guitarViews.SetDisplay(_profile.ViewForPart, _profile.fretLabels, _profile.chordLabels, _profile.countFrom);
        }

        void PickGuitarView(GuitarView view)
        {
            _profile.ViewForPart = view;
            _profile.Save();
            ApplyGuitarDisplay();
            RenderStep();
        }

        void CycleFretLabels()
        {
            _profile.fretLabels = _profile.fretLabels == FretLabelMode.Frets ? FretLabelMode.Numbers : FretLabelMode.Frets;
            _profile.Save();
            ApplyGuitarDisplay();
            RenderStep();
        }

        void CycleChordLabels()
        {
            _profile.chordLabels = _profile.chordLabels switch
            {
                ChordLabelMode.Numbers => ChordLabelMode.Tones,
                ChordLabelMode.Tones => ChordLabelMode.Both,
                _ => ChordLabelMode.Numbers,
            };
            _profile.Save();
            ApplyGuitarDisplay();
            RenderStep();
        }

        void CycleCountFrom()
        {
            _profile.countFrom = _profile.countFrom == CountFrom.Song ? CountFrom.Relative : CountFrom.Song;
            _profile.Save();
            ApplyGuitarDisplay();
            RenderStep();
        }

        static string FretLabelsText(FretLabelMode mode) => mode == FretLabelMode.Numbers ? "Labels: Numbers" : "Labels: Frets";

        static string ChordLabelsText(ChordLabelMode mode) => mode switch
        {
            ChordLabelMode.Tones => "Chords: Tones",
            ChordLabelMode.Both => "Chords: Both",
            _ => "Chords: Numbers",
        };

        static string CountFromText(CountFrom count) => count == CountFrom.Relative ? "Count: Relative" : "Count: Song key";

        static string ViewName(GuitarView view)
        {
            foreach (var choice in ViewChoices)
            {
                if (choice.View == view) return choice.Name;
            }
            return view.ToString();
        }

        /// <summary>
        /// The view picker: a list to choose from, up in the air on the menu, and
        /// the labelling choices beneath it. Poked or ray-pointed like every
        /// other button -- nothing here asks for a gesture near the strings.
        /// </summary>
        void RenderGuitarViews()
        {
            panel?.SetTitle("Views");
            var current = _profile.ViewForPart;
            var rows = new (string, string, System.Action)[ViewChoices.Length];
            for (var i = 0; i < ViewChoices.Length; i++)
            {
                var choice = ViewChoices[i];
                rows[i] = ((choice.View == current ? "● " : "") + choice.Name, choice.Detail, () => PickGuitarView(choice.View));
            }
            // The list stands in for the body, so a missing key is said on the
            // neck itself and in the guitar menu, not here.
            panel?.SetList(rows);
            panel?.SetButtons(
                (FretLabelsText(_profile.fretLabels), CycleFretLabels),
                (ChordLabelsText(_profile.chordLabels), CycleChordLabels),
                (CountFromText(_profile.countFrom), CycleCountFrom),
                ("Back", () => EnterStep(Step.Menu)));
        }

        /// <summary>The status line while the guitar is out of view, or null.</summary>
        string GuitarStatus() =>
            IsGuitar && guitarAnchor != null && guitarAnchor.HasPose && !guitarAnchor.IsTracked
                ? "guitar not tracked — look toward the neck"
                : null;

        string GuitarTrackingHelp()
        {
            if (!_guitarHelpShown) return "";
            var side = _profile.fretboard.controllerOnLeft ? "left" : "right";
            var refused = guitarAnchor != null && guitarAnchor.SimultaneousModeAccepted == false;
            return $"\n\n<color=#FFB020><b>Can't see the {side} controller on the guitar.</b> Check it is on, "
                 + "seated in the mount, and its face can see the headset. "
                 + (refused
                     ? "This headset would not track hands and controllers together, so Horizon OS "
                     : "If it disappears whenever you let go of it, Horizon OS ")
                 + "may be swapping the set-down controller for your hands: in the headset's "
                 + "Movement tracking settings, turn off switching automatically between hands "
                 + "and controllers.</color>";
        }

        /// <summary>Panel copy and buttons for the guitar steps. False for any other step.</summary>
        bool RenderGuitarStep()
        {
            var what = _profile?.fretboard is { isBass: true } ? "bass" : "guitar";
            var problem = string.IsNullOrEmpty(_guitarCaptureProblem)
                ? ""
                : $"\n\n<color=#FFB020>{_guitarCaptureProblem}</color>";
            const string howToTouch =
                "with the tip of your other controller and pull its trigger, or pinch there";

            switch (_step)
            {
                case Step.GuitarHand:
                    panel?.SetTitle($"Set up the {what}");
                    panel?.SetBody(
                        $"Which controller is in the {what}'s mount?\n\n"
                        + "Its face needs to see the headset while you play, so keep it turned "
                        + "toward you." + GuitarTrackingHelp());
                    panel?.SetButtons(
                        ("Left", () => ChooseGuitarHand(true)),
                        ("Right", () => ChooseGuitarHand(false)),
                        ("Back", () => EnterStep(Step.PickInstrument)));
                    return true;

                case Step.GuitarNutLowest:
                    panel?.SetTitle("Line up the neck (1 of 3)");
                    panel?.SetBody(
                        $"Touch the <b>nut</b>, on the <b>lowest string</b>, {howToTouch}.\n\n"
                        + "<i>Slowly, hand up and clear of the other strings.</i>"
                        + problem + GuitarTrackingHelp());
                    panel?.SetButtons(("Back", () => EnterStep(IsGuitarReady ? Step.Menu : Step.GuitarHand)));
                    return true;

                case Step.GuitarNutHighest:
                    panel?.SetTitle("Line up the neck (2 of 3)");
                    panel?.SetBody(
                        $"Now the <b>nut</b> on the <b>highest string</b>. This sets how far the neck "
                        + "is rolled.\n\n<i>Or keep the roll the mount was designed for.</i>"
                        + problem + GuitarTrackingHelp());
                    panel?.SetButtons(("Keep mount roll", KeepMountRoll), ("Restart", StartGuitarCheck));
                    return true;

                case Step.GuitarTwelfth:
                    panel?.SetTitle("Line up the neck (3 of 3)");
                    panel?.SetBody(
                        $"Touch the <b>12th fret wire</b>, in the middle of the neck, {howToTouch}."
                        + problem + GuitarTrackingHelp());
                    panel?.SetButtons(("Restart", StartGuitarCheck));
                    return true;

                case Step.GuitarCheck:
                    RenderGuitarCheck();
                    return true;

                case Step.GuitarPickView:
                    RenderGuitarViews();
                    return true;
            }
            return false;
        }

        bool IsGuitarReady => _profile is { IsFretboardChecked: true };

        void RenderGuitarCheck()
        {
            panel?.SetTitle("Line up the neck");
            var body = "";
            if (_haveCheck)
            {
                var measured = _check.MeasuredMetres * 1000f;
                var expected = _check.ExpectedMetres * 1000f;
                body = _check.Plausible
                    ? $"Nut to 12th fret: <b>{measured:F0} mm</b> (this neck: {expected:F0} mm)."
                    // The keyboard's span check, for a neck: well outside what
                    // hand tracking misses by, a touch landed somewhere else.
                    : $"<color=#FFB020><b>That looks off.</b> Nut to 12th fret measured {measured:F0} mm; "
                      + $"this neck's is {expected:F0} mm, {_check.ErrorRatio:P0} out. A touch probably "
                      + "missed -- press Retry.</color>";
                if (!_check.RollMeasured) body += "\nRoll kept from the mount's design.";
                body += "\n\n";
            }
            body += "Compare the drawn frets and strings with the real ones, nudge until they "
                  + "sit on them, then Save.";
            panel?.SetBody(body);

            panel?.SetButtons(
                ("Retry", StartGuitarCheck),
                ("To nut", () => NudgeGuitar(new Vector3(-GuitarNudgeMetres, 0f, 0f), 0f)),
                ("To body", () => NudgeGuitar(new Vector3(GuitarNudgeMetres, 0f, 0f), 0f)),
                ("Low side", () => NudgeGuitar(new Vector3(0f, 0f, -GuitarNudgeMetres), 0f)),
                ("High side", () => NudgeGuitar(new Vector3(0f, 0f, GuitarNudgeMetres), 0f)),
                ("Roll −", () => NudgeGuitar(Vector3.zero, -GuitarRollStepDegrees)),
                ("Roll +", () => NudgeGuitar(Vector3.zero, GuitarRollStepDegrees)),
                ("Save", SaveGuitarCheck));
        }

        void RenderGuitarMenu()
        {
            var neck = _profile.fretboard;
            panel?.SetTitle("AuralPrimer");
            var problem = guitarViews != null ? guitarViews.Problem ?? guitarViews.Notice : null;
            panel?.SetBody(
                $"<b>{neck.name}</b> — {neck.stringCount} strings, {neck.fretCount} frets, "
                + $"{neck.scaleLengthMetres * 1000f:F0} mm scale, {(neck.controllerOnLeft ? "left" : "right")} "
                + "controller.\n\n"
                + $"Showing <b>{ViewName(_profile.ViewForPart)}</b>, "
                + (_profile.fretLabels == FretLabelMode.Numbers ? "with Nashville numbers" : "with frets")
                + ". Press <b>Views</b> to change it.\n\n"
                + (problem != null ? $"<color=#FFB020>{problem}</color>\n\n" : "")
                + $"The note lane leans {neck.laneTiltDegrees:F0}° toward you."
                + GuitarTrackingHelp());
            panel?.SetButtons(
                ("Songs", () => EnterStep(Step.Songs)),
                ("Views", () => EnterStep(Step.GuitarPickView)),
                ("Re-check", StartGuitarCheck),
                ("Nudge", () =>
                {
                    _haveCheck = false;
                    EnterStep(Step.GuitarCheck);
                }),
                ("Tilt −", () => TiltGuitarLane(-GuitarTiltStepDegrees)),
                ("Tilt +", () => TiltGuitarLane(GuitarTiltStepDegrees)),
                ("Instrument", () => EnterStep(Step.PickInstrument)));
        }
    }
}
