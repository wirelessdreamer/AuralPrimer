// Copyright 2026 Nathanael Anderson. Licensed under the Apache License 2.0.
//
// First-run wizard: connect to the desktop app, then calibrate the keyboard.
//
// Every step is verifiable by MIDI rather than by eye. That is the whole design:
// the app knows which pitch arrived, so it can prove the overlay lines up
// instead of asking the player whether it looks right. In particular the
// off-by-one-key error — the classic failure, and subtly wrong for a whole
// session — becomes obvious the moment a key is played.

using System;
using System.Collections.Generic;
using System.IO;
using AuralPrimer.Link;
using AuralPrimer.Recording;
using AuralPrimer.UI;
using UnityEngine;

namespace AuralPrimer.Calibration
{
    public sealed partial class SetupWizard : MonoBehaviour
    {
        public enum Step
        {
            Connecting,
            LowestKey,
            HighestKey,
            MarkLeftEdge,
            MarkRightEdge,
            Verify,
            /// <summary>The standing menu, docked beside the instrument.</summary>
            Menu,
            FineTune,
            /// <summary>Pick a recording to watch.</summary>
            Recordings,
            /// <summary>Browse and search the host's song library.</summary>
            Songs,
            /// <summary>Pick a value for one of the song filters.</summary>
            SongFilter,
            /// <summary>Transport for the recording being watched.</summary>
            Playback,
        }

        [SerializeField] MrLinkBehaviour link;
        [SerializeField] WizardPanel panel;
        [SerializeField] HandGestures hands;
        [SerializeField] KeyboardOverlay overlay;
        [SerializeField] NoteHighway highway;
        [SerializeField] KeyboardAnchor keyboardAnchor;
        [SerializeField] PerformanceCapture capture;
        [SerializeField] PerformancePlayback playback;

        [Header("Pinch debug markers")]
        [Tooltip("A cube is left at each pinch so the captured point can be "
               + "compared against the real key it was meant to mark. The whole "
               + "class of bug this session has been the app placing things "
               + "somewhere other than where the hand was, which is invisible "
               + "until something is drawn at the captured point itself.")]
        [SerializeField] bool showPinchMarkers = true;

        [Header("Docked menu")]
        [Tooltip("Gap between the high end of the keyboard and the MIDDLE of the "
               + "menu. Must exceed the panel's half-width, or the menu reaches "
               + "back over the top octave and sits in front of its notes — but "
               + "kept small enough to reach out and touch without leaning.")]
        [SerializeField] float dockMarginMetres = 0.13f;

        [Tooltip("How far above the key bed the menu floats. Low enough that the "
               + "hand travels from keys to menu without leaving the desk.")]
        [SerializeField] float dockHeightMetres = 0.16f;

        [Tooltip("How far the menu is pulled back toward the player from the "
               + "keyboard's plane. A 61-key bed is a metre wide, so its high end "
               + "is half a metre out before anything is added — without this the "
               + "menu sits at the far end of a lean rather than a reach, and a "
               + "fingertip can never touch it.")]
        [SerializeField] float dockPullTowardPlayerMetres = 0.18f;

        [Tooltip("Off, the menu swivels round to face your head. On, it squares "
               + "up to the instrument instead, so it never tracks where you are "
               + "sitting. Drag the bars to angle it however you like from there.")]
        [SerializeField] bool dockAngledAway = true;
        [SerializeField] Material leftPinchMaterial;
        [SerializeField] Material rightPinchMaterial;
        [SerializeField] float pinchMarkerSizeMetres = 0.03f;
        [SerializeField] string profileName = "My keyboard";

        readonly List<(byte pitch, byte velocity)> _previousNotes = new();

        CalibrationProfile _profile;
        Step _step = Step.Connecting;
        int _lowestPitch;
        int _highestPitch;
        float _widthErrorRatio;
        int _verifiedKeys;
        int _lastVerifyPitch = -1;
        Vector3 _worldLeftEdge;
        bool _busy;
        readonly List<GameObject> _pinchMarkers = new();

        public Step Current => _step;
        public CalibrationProfile Profile => _profile;

        void Awake()
        {
            _profile = CalibrationProfile.Load(profileName);
        }

        void OnEnable()
        {
            if (hands != null)
            {
                hands.PinchStarted += OnPinch;
                hands.MenuSummoned += OnMenuSummoned;
            }

            HookLibrary();
        }

        void OnDisable()
        {
            if (hands != null)
            {
                hands.PinchStarted -= OnPinch;
                hands.MenuSummoned -= OnMenuSummoned;
            }

            UnhookLibrary();
            // A microphone left open survives this component being disabled and
            // holds the device against the next thing that wants it.
            StopListening(send: false);
        }

        void Update()
        {
            UpdateStatusLine();

            // Both poll, because neither underlying API reports completion any
            // other way: TouchScreenKeyboard has no callback on any platform,
            // and a recording that runs to the cap has nothing to raise one.
            PumpKeyboard();
            PumpListening();
            PumpKeyboardLayout();

            // A rising edge on a pitch is "the player just pressed a key". The
            // host sends the full held set, so this is a set difference rather
            // than an event stream.
            var pressed = NewlyPressedPitch();

            switch (_step)
            {
                case Step.Connecting:
                    if (link != null && link.IsConnected)
                    {
                        // Skip calibration entirely if this keyboard is already
                        // known — but only once the runtime has re-localised its
                        // anchor and told us where it actually is. Restoring is
                        // then knowing, not guessing.
                        if (_busy) break;

                        if (_profile is { IsCalibrated: true } && _profile.version != CalibrationProfile.CurrentVersion)
                        {
                            Debug.Log($"[wizard] calibration v{_profile.version} predates the current "
                                    + $"scheme (v{CalibrationProfile.CurrentVersion}); re-calibrating");
                            _profile = new CalibrationProfile { profileName = profileName };
                            ClearPinchMarkers();
                            EnterStep(Step.LowestKey);
                        }
                        else if (_profile is { IsAnchored: true }) RestoreAsync();
                        else
                        {
                            _profile = new CalibrationProfile { profileName = profileName };
                            EnterStep(Step.LowestKey);
                        }
                    }
                    break;

                case Step.LowestKey:
                    if (pressed >= 0)
                    {
                        _lowestPitch = pressed;
                        EnterStep(Step.HighestKey);
                    }
                    break;

                case Step.HighestKey:
                    if (pressed >= 0 && pressed != _lowestPitch)
                    {
                        _highestPitch = Mathf.Max(pressed, _lowestPitch + 1);
                        _profile.lowestPitch = Mathf.Min(_lowestPitch, _highestPitch);
                        _profile.highestPitch = Mathf.Max(_lowestPitch, _highestPitch);
                        EnterStep(Step.MarkLeftEdge);
                    }
                    break;

                case Step.Verify:
                    if (pressed >= 0) RegisterVerification(pressed);
                    break;
            }
        }

        void OnPinch(Vector3 position)
        {
            switch (_step)
            {
                case Step.MarkLeftEdge:
                    // Held in world space until there is an anchor to rebase onto.
                    _worldLeftEdge = position;
                    DropPinchMarker(position, leftPinchMaterial, "Pinch L");
                    EnterStep(Step.MarkRightEdge);
                    break;

                case Step.MarkRightEdge:
                    if (_busy) break;
                    DropPinchMarker(position, rightPinchMaterial, "Pinch R");
                    AnchorAndApplyAsync(_worldLeftEdge, position);
                    break;

                // Step.Menu deliberately does not act on a pinch. Dismissing
                // used to be pinch-driven, but a pinch aimed at a button is a
                // click, so dismissing and pressing were the same gesture: the
                // panel moved out from under the press. The menu now stays.
            }
        }

        void OnMenuSummoned()
        {
            // Summon repositions rather than merely toggling: a panel behind you
            // is the same as no panel. Once the keyboard is known the menu has a
            // home, so send it back there rather than into the user's face —
            // otherwise the gesture that recovers a lost menu is also the gesture
            // that knocks it out of the place it belongs.
            if (_profile is { IsCalibrated: true }) DockBesideKeyboard();
            else panel?.PlaceInFrontOfUser();
        }

        void EnterStep(Step step)
        {
            // Grabbable edges left lying around outside fine tuning are two
            // invisible-purpose objects floating over the keyboard that steal
            // any pinch aimed near them.
            // Point at things that sit on the keyboard. Calibration is the one
            // time the ray must survive being aimed at the keys, because that is
            // where everything it needs to grab lives.
            AuralPrimer.UI.KeyboardProximity.SuppressOverKeys = step != Step.FineTune;

            // Same reason, for the eyes rather than the ray: fine tuning is read
            // by comparing each drawn key against the real one beneath it, so the
            // markers go back to full strength for it and drop to a hint again
            // afterwards.
            if (overlay != null) overlay.Placing = step == Step.FineTune;

            if (step != Step.FineTune) HideEdgeHandles();

            // The picker list belongs to the song steps. Left up, it would draw
            // a list of songs under whatever title came next.
            if (step != Step.Songs && step != Step.SongFilter) panel?.SetList();

            // Leaving playback tears the take down: recorded hands left floating
            // over the keyboard would be indistinguishable from live tracking,
            // and the overlay would keep lighting keys nobody is pressing.
            if (step != Step.Playback && playback != null && playback.HasRecording)
            {
                playback.Unload();
                if (overlay != null) overlay.PlaybackNotes = null;
            }
            if (step != Step.Playback && step != Step.Recordings) panel?.SetScrub(null);

            _step = step;
            _verifiedKeys = 0;
            // Ask on the way in rather than caching: a song imported on the
            // desktop while the headset sat on the menu should be there.
            if (step == Step.Songs) RequestSongs();
            _lastVerifyPitch = -1;
            // Show it, do not move it. Re-placing on every step change meant the
            // panel jumped to wherever the user was looking each time the wizard
            // advanced, which reads exactly like a panel pinned to the head.
            // Only an explicit summon repositions it.
            panel?.Show();
            RenderStep();
        }

        void RenderStep()
        {
            // Per-step, and cleared by default: a button carried over from the
            // previous step still points at the previous step's action.
            panel?.SetButtons();

            switch (_step)
            {
                case Step.Connecting:
                    // Name the thing that is missing. "Connecting" reads as
                    // "something is happening, wait" — but nothing is happening
                    // and nothing will until the desktop app is running, which
                    // the headset cannot do anything about on its own.
                    panel?.SetTitle("Waiting for desktop");
                    panel?.SetBody(
                        "<b>The AuralPrimer desktop app isn't running</b>, or this headset "
                        + "can't see it yet.\n\n"
                        + "Start AuralPrimer on your PC and make sure both are on the same "
                        + "Wi-Fi. This will connect on its own once it finds it.");
                    break;

                case Step.LowestKey:
                    panel?.SetTitle("Which keyboard?");
                    panel?.SetBody(
                        "<b>Play the LOWEST key</b> on your keyboard.\n\n"
                        + "This works out the size and range of your instrument, so you "
                        + "don't have to tell it.");
                    break;

                case Step.HighestKey:
                    panel?.SetTitle("Which keyboard?");
                    panel?.SetBody(
                        $"Lowest: <b>{NoteName(_lowestPitch)}</b>\n\n"
                        + "Now <b>play the HIGHEST key</b>.");
                    break;

                case Step.MarkLeftEdge:
                    panel?.SetTitle("Where is it?");
                    panel?.SetBody(
                        $"<b>{KeyCountLabel()}</b>, {NoteName(_profile.lowestPitch)} to "
                        + $"{NoteName(_profile.highestPitch)}.\n\n"
                        + "<b>Pinch at the far LEFT edge</b> of the lowest white key.");
                    break;

                case Step.MarkRightEdge:
                    panel?.SetTitle("Where is it?");
                    panel?.SetBody("Now <b>pinch at the far RIGHT edge</b> of the highest white key.");
                    break;

                case Step.Verify:
                    panel?.SetTitle("Check the alignment");
                    panel?.SetBody(WidthWarning()
                        + "Play any <b>three keys</b>. Each one should light up directly "
                        + "over the key you pressed.\n\n"
                        + "If the highlight is on the wrong key, pinch to start over.");
                    break;

                case Step.Menu:
                    panel?.SetTitle("AuralPrimer");
                    panel?.SetBody(
                        $"<b>{_profile.profileName}</b> — {KeyCountLabel()}, "
                        + $"{NoteName(_profile.lowestPitch)} to {NoteName(_profile.highestPitch)}.\n\n"
                        + (_profile.ignoreOutOfRangeNotes
                            ? "Notes outside this range are not shown."
                            : "Notes outside this range are folded in by octaves.")
                        + $"\n\nGrab the ball on the menu's outer edge to swing it — it is "
                        + $"hinged to the {(_profile.menuOnHighEnd ? "high" : "low")} corner "
                        + "of the keyboard.");
                    // Song selection and transport belong here too; the row takes
                    // them without further plumbing.
                    panel?.SetButtons(
                        ("Configure", () => EnterStep(Step.FineTune)),
                        (capture != null && capture.IsRecording ? "Stop recording" : "Record",
                         ToggleRecording),
                        ("Songs", () => EnterStep(Step.Songs)),
                        ("Watch", () => EnterStep(Step.Recordings)),
                        ("Flip side", FlipMenuSide),
                        (_profile.ignoreOutOfRangeNotes ? "Fold notes in" : "Drop off-range",
                         ToggleOutOfRange),
                        (HandVisualLabel(_profile.handVisual), CycleHandVisual));
                    break;

                case Step.FineTune:
                    panel?.SetTitle("Fine tune");
                    ShowEdgeHandles();
                    break;

                case Step.Recordings:
                    panel?.SetTitle("Recordings");
                    RenderRecordings();
                    break;

                case Step.Songs:
                    panel?.SetTitle("Songs");
                    RenderSongs();
                    break;

                case Step.SongFilter:
                    panel?.SetTitle(_filteringGenre ? "Genre" : "Artist");
                    RenderSongFilter();
                    break;

                case Step.Playback:
                    panel?.SetTitle("Watching");
                    RenderPlayback();
                    break;
            }
        }

        // --- Fine tune ------------------------------------------------------
        //
        // Two pinches fix the span from two hand-tracked points, and hand
        // tracking is good to roughly a centimetre — enough that the drawn keys
        // drift off the real ones toward the ends of the instrument. Re-pinching
        // from scratch just re-rolls the same error, so the edges become
        // grabbable instead: take hold of one and slide it onto the real key
        // edge, with the whole overlay following the hand as it moves.

        EdgeHandles _handles;

        void ShowEdgeHandles()
        {
            // Redraw the keys before the handles go up. Lining an edge marker up
            // against a real key is the whole job, and doing it with nothing
            // drawn is aiming at a target you cannot see.
            RefreshVisuals();

            if (_handles == null)
            {
                _handles = gameObject.AddComponent<EdgeHandles>();
                _handles.Moved += OnEdgeMoved;
                _handles.Released += OnEdgeReleased;
            }

            // The overlay's own transform, not the anchor's. Whatever frame the
            // keys are drawn in is the frame the handles must live in, or a drag
            // moves the edge in one space while the keys redraw in another and
            // the overlay slides away from the hand.
            _handles.Show(_profile,
                          overlay != null ? overlay.transform
                                          : (keyboardAnchor != null ? keyboardAnchor.Space : null),
                          leftPinchMaterial, rightPinchMaterial);
            RenderFineTune();
        }

        void HideEdgeHandles() => _handles?.Hide();

        /// <summary>The overlay follows the hand, so the fit is judged live.</summary>
        void OnEdgeMoved()
        {
            // Deliberately not DockBesideKeyboard: the menu is anchored off the
            // high end, so re-docking it here would drag the panel — and the
            // button being pressed — along with the handle.
            RefreshVisuals();
            RenderFineTune();
        }

        /// <summary>Only commit on release: not sixty times a second mid-drag.</summary>
        void OnEdgeReleased()
        {
            // Refuse to save a placement that is not a keyboard, and put the
            // handle back. A handle can end up metres away in one grab, and
            // saving that overwrites the only good calibration the player has —
            // which is exactly how an edge got stored 79 cm above the other one.
            if (!_profile.IsPlausible)
            {
                Debug.LogWarning($"[wizard] rejected an edge {_profile.TiltDegrees:F0}° off level");

                var restored = CalibrationProfile.Load(_profile.profileName);
                if (restored != null)
                {
                    _profile.leftEdge = restored.leftEdge;
                    _profile.rightEdge = restored.rightEdge;
                }

                _handles?.Resync();
                RefreshVisuals();
                RenderFineTune();
                return;
            }

            _profile.Save();
            Debug.Log($"[wizard] edge release accepted: tilt={_profile.TiltDegrees:F1}° "
                    + $"span={_profile.WidthMetres:F3}m left={_profile.leftEdge:F3} "
                    + $"right={_profile.rightEdge:F3}");
            RenderFineTune();
        }

        LaneHandle _laneHandle;

        /// <summary>
        /// The lane's drag bar. Always up once there is a lane to drag.
        /// </summary>
        /// <remarks>
        /// Not a page to navigate to. The bar belongs on top of the scrolling
        /// display, where the thing it adjusts is — putting it behind a submenu
        /// meant leaving the view you are trying to judge in order to change it.
        /// </remarks>
        void ShowLaneHandle()
        {
            if (_profile is not { IsCalibrated: true }) { HideLaneHandle(); return; }

            if (_laneHandle == null)
            {
                _laneHandle = gameObject.AddComponent<LaneHandle>();
                _laneHandle.Moved += OnLaneMoved;
                _laneHandle.Released += OnLaneReleased;
            }

            _laneHandle.Show(_profile,
                             overlay != null ? overlay.transform
                                             : (keyboardAnchor != null ? keyboardAnchor.Space : null),
                             leftPinchMaterial);
        }

        void HideLaneHandle() => _laneHandle?.Hide();

        void OnLaneMoved()
        {
            // The lane redraws under the hand, so the rake is judged against the
            // notes themselves rather than against a number.
            RefreshVisuals();
        }

        void OnLaneReleased()
        {
            _profile.Save();
            Debug.Log($"[wizard] lane set: tilt={_profile.laneTiltDegrees:F0}° "
                    + $"height={_profile.laneHeightMetres:F2}m");
        }

        /// <summary>Degrees of key-plane roll per press.</summary>
        const float CantStepDegrees = 1f;
        /// <summary>Metres of lift per press — about an eighth of an inch.</summary>
        const float LiftStepMetres = 0.01f;

        void AdjustCant(float degrees)
        {
            if (_profile == null) return;
            _profile.keyCantDegrees = Mathf.Clamp(
                _profile.keyCantDegrees + degrees,
                -CalibrationProfile.MaxCantDegrees,
                CalibrationProfile.MaxCantDegrees);
            _profile.Save();
            RefreshVisuals();
            RenderFineTune();
        }

        void AdjustLift(float metres)
        {
            if (_profile == null) return;
            _profile.laneLiftMetres = Mathf.Clamp(_profile.laneLiftMetres + metres, 0f, 0.20f);
            _profile.Save();
            RefreshVisuals();
            RenderFineTune();
        }

        void RenderFineTune()
        {
            var layout = _profile.BuildLayout();
            panel?.SetBody(
                $"Span <b>{_profile.WidthMetres:F3} m</b>. A {KeyCountLabel()} is normally "
                + $"about {layout.ExpectedWidthMetres:F2} m.\n\n"
                + "Pinch a marker and slide it onto the outer edge of your "
                + $"<b>{NoteName(_profile.lowestPitch)}</b> or "
                + $"<b>{NoteName(_profile.highestPitch)}</b> key. The keys follow as you move."
                + $"\n\nKey cant <b>{_profile.keyCantDegrees:+0;-0;0}°</b>, "
                + $"play-line gap <b>{_profile.laneLiftMetres * 100f:F0} cm</b>."
                + (_profile.TiltDegrees > CalibrationProfile.MaxTiltDegrees
                    ? $"\n\n<color=#FFB020>That is {_profile.TiltDegrees:F0}° off level — "
                      + "a keyboard is flat, so one marker is off the instrument. "
                      + "Put it back on the key before letting go.</color>"
                    : ""));

            panel?.SetButtons(
                ("Cant −", () => AdjustCant(-CantStepDegrees)),
                ("Cant +", () => AdjustCant(CantStepDegrees)),
                ("Lower", () => AdjustLift(-LiftStepMetres)),
                ("Raise", () => AdjustLift(LiftStepMetres)),
                ("Back", () => EnterStep(Step.Menu)));
        }

        // --- Watching a recording -------------------------------------------

        /// <summary>Recordings shown per page — the button row holds five.</summary>
        const int RecordingsPerPage = 4;

        string[] _recordings;
        int _recordingsPage;

        void RenderRecordings()
        {
            _recordings = PerformanceReader.List();

            if (_recordings.Length == 0)
            {
                panel?.SetBody("Nothing recorded yet.\n\n"
                             + "Press <b>Record</b> on the menu, play, then stop. "
                             + "Takes are saved on the headset and appear here.");
                panel?.SetButtons(("Back", () => EnterStep(Step.Menu)));
                return;
            }

            var pages = (_recordings.Length + RecordingsPerPage - 1) / RecordingsPerPage;
            _recordingsPage = Mathf.Clamp(_recordingsPage, 0, pages - 1);
            var start = _recordingsPage * RecordingsPerPage;

            panel?.SetBody($"<b>{_recordings.Length}</b> take"
                         + (_recordings.Length == 1 ? "" : "s")
                         + $", newest first.{(pages > 1 ? $" Page {_recordingsPage + 1} of {pages}." : "")}");

            var buttons = new List<(string, Action)>();
            for (var i = start; i < Mathf.Min(start + RecordingsPerPage, _recordings.Length); i++)
            {
                var path = _recordings[i];
                buttons.Add((Path.GetFileNameWithoutExtension(path), () => StartWatching(path)));
            }

            // One button changes meaning with the page count: with a single page
            // there is nothing to turn to, and a dead "More" is worse than none.
            if (pages > 1)
            {
                buttons.Add(("More", () =>
                {
                    _recordingsPage = (_recordingsPage + 1) % pages;
                    RenderRecordings();
                }));
            }
            else
            {
                buttons.Add(("Back", () => EnterStep(Step.Menu)));
            }

            panel?.SetButtons(buttons.ToArray());
        }

        void StartWatching(string path)
        {
            if (playback == null)
            {
                Debug.LogWarning("[wizard] no PerformancePlayback assigned");
                return;
            }

            if (!playback.Load(path))
            {
                panel?.SetBody($"<color=#FFB020>Could not open "
                             + $"<b>{Path.GetFileNameWithoutExtension(path)}</b>. "
                             + "It may be from an older build.</color>");
                return;
            }

            // The recorded notes drive the same key highlighting the live
            // performance used, so a replay lights exactly what was played.
            if (overlay != null) overlay.PlaybackNotes = playback.HeldNotes;
            EnterStep(Step.Playback);
        }

        void RenderPlayback()
        {
            if (playback == null || !playback.HasRecording)
            {
                EnterStep(Step.Recordings);
                return;
            }

            panel?.SetBody($"<b>{playback.LoadedName}</b>\n\n"
                         + "The recorded hands play over your keyboard. "
                         + "Drag the bar to scrub.");

            panel?.SetScrub(Scrub, Normalised(playback.Time));

            panel?.SetButtons(
                (playback.IsPlaying ? "Pause" : "Play", TogglePlayback),
                ("-10s", () => { playback.Nudge(-10f); SyncScrub(); }),
                ("+10s", () => { playback.Nudge(10f); SyncScrub(); }),
                ("Back", () => EnterStep(Step.Recordings)));
        }

        void TogglePlayback()
        {
            playback.TogglePlay();
            RenderPlayback();
        }

        void Scrub(float normalised)
        {
            if (playback == null || !playback.HasRecording) return;
            playback.Seek(normalised * playback.Duration);
        }

        float Normalised(float seconds)
        {
            var duration = playback != null ? playback.Duration : 0f;
            return duration > 0f ? seconds / duration : 0f;
        }

        /// <summary>Move the handle to match a seek the buttons made.</summary>
        void SyncScrub() => panel?.SetScrubPosition(Normalised(playback.Time));

        string WidthWarning()
        {
            // 15% is well outside the variation between real instruments, so
            // beyond it the marks are far more likely to be wrong than the piano.
            if (_widthErrorRatio <= 0.15f) return "";

            var layout = _profile.BuildLayout();
            return $"<color=#FFB020><b>That looks off.</b> A {KeyCountLabel()} should be about "
                 + $"{layout.ExpectedWidthMetres:F2} m wide; you marked {_profile.WidthMetres:F2} m. "
                 + "Check the highlights below — if they are wrong, pinch to redo.</color>\n\n";
        }

        void RegisterVerification(int pitch)
        {
            if (pitch == _lastVerifyPitch) return;
            _lastVerifyPitch = pitch;
            _verifiedKeys++;

            var layout = _profile.BuildLayout();
            var known = layout.Contains(pitch);

            panel?.SetBody(
                (known
                    ? $"Lit: <b>{NoteName(pitch)}</b> — is the highlight on that key?\n\n"
                    : $"<color=#FFB020><b>{NoteName(pitch)}</b> is outside the range you set "
                      + "({NoteName(_profile.lowestPitch)}–{NoteName(_profile.highestPitch)}). "
                      + "Pinch to start over.</color>\n\n")
                + $"{_verifiedKeys} of 3 checked.");

            if (_verifiedKeys >= 3 && known)
            {
                _profile.Save();
                EnterStep(Step.Menu);
            }
        }

        /// <summary>
        /// Anchor the calibration just measured, then apply it.
        /// </summary>
        async void AnchorAndApplyAsync(Vector3 worldLeft, Vector3 worldRight)
        {
            _busy = true;
            try
            {
                var up = Vector3.up;
                var right = worldRight - worldLeft;
                var forward = right.sqrMagnitude > 1e-6f
                    ? Vector3.Cross(right.normalized, up)
                    : Vector3.forward;

                // Anchor at the left edge, oriented along the instrument, so the
                // saved pose means something on its own rather than being an
                // arbitrary point with numbers hanging off it.
                var pose = new Pose(worldLeft, Quaternion.LookRotation(forward, up));

                string anchorId = null;
                if (keyboardAnchor != null) anchorId = await keyboardAnchor.CreateAndSaveAsync(pose);

                _profile.anchorId = anchorId;
                _profile.RebaseOnto(keyboardAnchor != null ? keyboardAnchor.Space : null,
                                    worldLeft, worldRight, up);

                // Cross-check the span against the instrument's real size before
                // trusting it. A measurement well off expectation is a mis-tap,
                // and silently building a skewed lane from it would be wrong for
                // the whole session.
                var layout = _profile.BuildLayout();
                _widthErrorRatio = (float)layout.WidthErrorRatio(_profile.WidthMetres);

                ApplyProfile();
                EnterStep(Step.Verify);
            }
            catch (Exception e)
            {
                Debug.LogError($"[wizard] anchoring failed: {e.Message}");
                EnterStep(Step.MarkLeftEdge);
            }
            finally
            {
                _busy = false;
            }
        }

        /// <summary>
        /// Re-localise a saved anchor and reuse its calibration, or ask for the
        /// edges again if the room no longer matches.
        /// </summary>
        async void RestoreAsync()
        {
            _busy = true;
            try
            {
                var restored = keyboardAnchor != null
                            && await keyboardAnchor.TryLoadAsync(_profile.anchorId);

                if (restored)
                {
                    ApplyProfile();
                    EnterStep(Step.Menu);
                }
                else
                {
                    // Not a failure worth alarming anyone about: a room can
                    // change, and re-marking two edges is quick.
                    _profile = new CalibrationProfile { profileName = profileName };
                    EnterStep(Step.LowestKey);
                }
            }
            catch (Exception e)
            {
                Debug.LogError($"[wizard] restoring the anchor failed: {e.Message}");
                _profile = new CalibrationProfile { profileName = profileName };
                EnterStep(Step.LowestKey);
            }
            finally
            {
                _busy = false;
            }
        }

        /// <summary>
        /// Leave a cube where a pinch was captured.
        /// </summary>
        /// <remarks>
        /// World space and unparented on purpose: this must show the raw
        /// captured point, not the point after it has been rebased onto an
        /// anchor. If the cube is not on the key the user pinched, the capture
        /// is wrong; if it is on the key but the overlay is not, the placement
        /// downstream is wrong. Nothing else distinguishes those two.
        /// </remarks>
        void DropPinchMarker(Vector3 world, Material material, string name)
        {
            Debug.Log($"[wizard] {name} captured at {world} "
                    + $"(head at {(Camera.main != null ? Camera.main.transform.position.ToString() : "?")})");

            if (!showPinchMarkers) return;

            var marker = GameObject.CreatePrimitive(PrimitiveType.Cube);
            marker.name = name;
            Destroy(marker.GetComponent<Collider>());
            marker.transform.SetPositionAndRotation(world, Quaternion.identity);
            marker.transform.localScale = Vector3.one * pinchMarkerSizeMetres;
            if (material != null && marker.TryGetComponent<Renderer>(out var r)) r.sharedMaterial = material;
            _pinchMarkers.Add(marker);
        }

        /// <summary>Clear markers from a previous calibration attempt.</summary>
        void ClearPinchMarkers()
        {
            foreach (var m in _pinchMarkers)
            {
                if (m != null) Destroy(m);
            }
            _pinchMarkers.Clear();
        }

        /// <summary>
        /// Put the panel above the calibrated keyboard, facing the player.
        /// </summary>
        /// <summary>
        /// Dock the menu in the air just off the high end of the keyboard.
        /// </summary>
        /// <remarks>
        /// Beside the instrument rather than over it: above the keys it would
        /// sit in front of the falling notes, which is the one place a menu must
        /// never be. Docking to the keyboard's own axes rather than to the room
        /// means it keeps its place relative to the instrument when the anchor
        /// re-localises, instead of drifting off on its own.
        /// </remarks>
        MenuHinge _menuHinge;

        /// <summary>
        /// Hang the menu off the corner of the instrument and swing it to taste.
        /// </summary>
        /// <remarks>
        /// Pinned rather than placed. A free panel has three positions and three
        /// rotations to get wrong, and this client has got each of them wrong in
        /// turn — aimed at the head, squared to the room, squared to the
        /// instrument, too far to reach. Hinged to the keyboard's end corner
        /// there is one number left, how far open it is, and it cannot wander
        /// away from the thing it belongs to.
        /// </remarks>
        void DockBesideKeyboard()
        {
            if (panel == null || _profile is not { IsCalibrated: true }) return;

            if (_menuHinge == null)
            {
                _menuHinge = gameObject.AddComponent<MenuHinge>();
                _menuHinge.Moved += OnMenuSwung;
                _menuHinge.Released += OnMenuSwingReleased;
            }

            var space = keyboardAnchor != null ? keyboardAnchor.Space : null;
            var hingeSpace = overlay != null ? overlay.transform : space;
            _menuHinge.Show(_profile, hingeSpace, rightPinchMaterial, panel.WidthMetres);

            PlaceMenuOnHinge(hingeSpace);
        }

        void PlaceMenuOnHinge(Transform hingeSpace)
        {
            if (panel == null || _menuHinge == null || !_menuHinge.IsShowing) return;

            Vector3 ToWorld(Vector3 local) =>
                hingeSpace != null ? hingeSpace.TransformPoint(local) : local;
            Vector3 Dir(Vector3 local) =>
                hingeSpace != null ? hingeSpace.TransformDirection(local) : local;

            var hinge = ToWorld(_menuHinge.HingePoint());
            var outward = Dir(_menuHinge.Outward()).normalized;
            var up = Dir(_profile.CantedUp).normalized;

            // The panel spans from the pin outward, so its centre is half a width
            // along that direction and its face is perpendicular to it.
            var centre = hinge + outward * (panel.WidthMetres * 0.5f);
            var normal = Vector3.Cross(outward, up).normalized;

            var camera = Camera.main;
            if (camera != null)
            {
                var toPlayer = camera.transform.position - centre;
                if (Vector3.Dot(normal, toPlayer) < 0f) normal = -normal;
            }

            // A world-space canvas is read from its -Z side.
            panel.PlaceAt(centre, Quaternion.LookRotation(-normal, up));
        }

        void OnMenuSwung()
        {
            PlaceMenuOnHinge(overlay != null ? overlay.transform
                                             : (keyboardAnchor != null ? keyboardAnchor.Space : null));
        }

        void OnMenuSwingReleased()
        {
            _profile.Save();
            Debug.Log($"[wizard] menu swung to {_profile.menuYawDegrees:F0}° "
                    + $"on the {(_profile.menuOnHighEnd ? "high" : "low")} end");
        }

        HandVisuals _handVisuals;

        /// <summary>Put the saved hand treatment into effect.</summary>
        void ApplyHandVisuals()
        {
            if (_profile == null) return;
            _handVisuals ??= gameObject.AddComponent<HandVisuals>();
            _handVisuals.Apply(_profile);
        }

        /// <summary>Human-readable name for the current hand treatment.</summary>
        static string HandVisualLabel(CalibrationProfile.HandVisual mode) => mode switch
        {
            CalibrationProfile.HandVisual.Rendered => "Hands: drawn",
            CalibrationProfile.HandVisual.Occluded => "Hands: real",
            _ => "Hands: hidden",
        };

        /// <summary>
        /// Step through the hand treatments.
        /// </summary>
        /// <remarks>
        /// A cycle rather than three buttons: the row is already full, and the
        /// choice is one the player makes once and forgets. Saved immediately,
        /// because the next thing they do is put the headset back on and play.
        /// </remarks>
        void CycleHandVisual()
        {
            if (_profile == null) return;

            _profile.handVisual = _profile.handVisual switch
            {
                CalibrationProfile.HandVisual.Overlay => CalibrationProfile.HandVisual.Rendered,
                CalibrationProfile.HandVisual.Rendered => CalibrationProfile.HandVisual.Occluded,
                _ => CalibrationProfile.HandVisual.Overlay,
            };

            _profile.Save();
            ApplyHandVisuals();
            RenderStep();
            Debug.Log($"[wizard] hand visual -> {_profile.handVisual}");
        }

        void ToggleOutOfRange()
        {
            if (_profile == null) return;
            _profile.ignoreOutOfRangeNotes = !_profile.ignoreOutOfRangeNotes;
            _profile.Save();
            RefreshVisuals();
            RenderStep();
        }

        /// <summary>Move the menu to the other end of the instrument.</summary>
        void FlipMenuSide()
        {
            if (_profile == null) return;
            _profile.menuOnHighEnd = !_profile.menuOnHighEnd;
            // Mirror the swing too, or flipping sides folds the panel back across
            // the keyboard instead of opening away from it.
            _profile.menuYawDegrees = -_profile.menuYawDegrees;
            _profile.Save();

            DockBesideKeyboard();
            _menuHinge?.Place();
            RenderStep();
        }

        static void Reseat(Transform target, Transform space)
        {
            if (target == null) return;
            target.SetParent(space, false);
            target.localPosition = Vector3.zero;
            target.localRotation = Quaternion.identity;
            target.localScale = Vector3.one;
        }

        /// <summary>
        /// Redraw the keys and the lane from the current calibration.
        /// </summary>
        /// <remarks>
        /// Separate from ApplyProfile because the fine-tune handles call this on
        /// every frame of a drag: re-docking the menu at that rate would slide it
        /// out from under the button being pressed.
        /// </remarks>
        void RefreshVisuals()
        {
            if (_profile != null)
            {
                Debug.Log($"[wizard] applying profile: calibrated={_profile.IsCalibrated} "
                        + $"pitches={_profile.lowestPitch}..{_profile.highestPitch} "
                        + $"width={_profile.WidthMetres:F3}m "
                        + $"left={_profile.leftEdge} right={_profile.rightEdge}");
            }

            // Both renderers work in the anchor's space, so parent them to it —
            // and seat them AT it, or "anchor space" and "the space the keys are
            // drawn in" quietly differ by whatever offset the scene was authored
            // with.
            var space = keyboardAnchor != null ? keyboardAnchor.Space : null;
            if (space != null)
            {
                Reseat(overlay != null ? overlay.transform : null, space);
                Reseat(highway != null ? highway.transform : null, space);
            }

            if (overlay != null)
            {
                overlay.Apply(_profile);
                // The overlay lights the keys the lane is about to drop notes
                // onto, so it needs to be told where the lane is. Set here, with
                // the rest of the calibration, rather than serialised into the
                // scene — one less field for a scene edit to lose.
                overlay.Highway = highway;
            }
            // The lane hangs off the same calibration: without it there is no
            // keyboard for the notes to line up above.
            if (highway != null) highway.Apply(_profile);

            // Whatever the player chose about their hands, applied here so it
            // survives a restart without them having to go and set it again.
            ApplyHandVisuals();

            // The host needs to know what this instrument can reach, or it
            // waits for notes that are not on it. Sent here because this is
            // where the range becomes known and where it changes.
            PublishKeyboardLayout();

            // The bar lives with the display it adjusts.
            ShowLaneHandle();
        }

        /// <summary>Redraw everything, and send the menu to its hinge.</summary>
        void ApplyProfile()
        {
            RefreshVisuals();
            DockBesideKeyboard();
        }

        bool _wasPlaying;

        static string Clock(double seconds) =>
            $"{(int)(seconds / 60):00}:{(int)(seconds % 60):00}";

        void ToggleRecording()
        {
            if (capture == null)
            {
                Debug.LogWarning("[wizard] no PerformanceCapture assigned; cannot record");
                return;
            }

            if (capture.IsRecording) capture.Stop();
            else capture.Begin();

            // Relabel the button to match what it will now do.
            RenderStep();
        }

        void UpdateStatusLine()
        {
            if (panel == null) return;

            // While watching, the transport is the status: the link is not what
            // the player is looking at, and the elapsed time has to come from
            // somewhere that runs every frame rather than on button presses.
            if (_step == Step.Playback && playback != null && playback.HasRecording)
            {
                if (playback.IsPlaying)
                {
                    panel.SetScrubPosition(Normalised(playback.Time));

                    // Playback stops itself at the end; relabel Play/Pause once
                    // when it does, rather than re-rendering the row every frame.
                    if (!_wasPlaying) _wasPlaying = true;
                }
                else if (_wasPlaying)
                {
                    _wasPlaying = false;
                    RenderPlayback();
                }

                panel.SetStatus($"{Clock(playback.Time)} / {Clock(playback.Duration)}",
                                new Color(0.55f, 0.8f, 1f));
                return;
            }

            // Recording outranks link state: while a take is running, that is
            // the thing the player needs to see, and a performance silently not
            // being captured is the failure that cannot be undone afterwards.
            if (capture != null && capture.IsRecording)
            {
                panel.SetStatus($"● recording — {Clock(capture.ElapsedSeconds)}",
                                new Color(1f, 0.35f, 0.4f));
                return;
            }

            if (link == null)
            {
                panel.SetStatus("no link component", new Color(1f, 0.4f, 0.45f));
            }
            else if (link.IsConnected)
            {
                panel.SetStatus($"connected to {link.HostName}", new Color(0.4f, 0.95f, 0.55f));
            }
            else if (link.IsReconnecting)
            {
                // Distinct from the firewall case below: we reached this host
                // once, so telling the user to go change firewall rules over a
                // momentary drop would send them after the wrong thing.
                panel.SetStatus($"reconnecting to {link.LastHostName}…",
                                new Color(0.98f, 0.82f, 0.35f));
            }
            else if (link.HostHeardButNotConnected)
            {
                // The beacon reached us, so the desktop is up and on this
                // network; only the reply is missing. On Windows that is the
                // firewall dropping inbound UDP to the app, which is invisible
                // from in here unless the headset says so.
                panel.SetStatus($"found {link.LastBeaconHost} — no reply (allow AuralPrimer through the firewall)",
                                new Color(1f, 0.55f, 0.2f));
            }
            else
            {
                panel.SetStatus("waiting for the desktop app…", new Color(1f, 0.180f, 0.533f));
            }
        }

        /// <summary>Pitch that has just been pressed, or -1. Derived from the
        /// host's full held-note set, which is what the link streams.</summary>
        int NewlyPressedPitch()
        {
            if (link == null) return -1;

            var pressed = -1;
            foreach (var note in link.HeldNotes)
            {
                var wasHeld = false;
                foreach (var previous in _previousNotes)
                {
                    if (previous.pitch == note.pitch) { wasHeld = true; break; }
                }
                if (!wasHeld) pressed = note.pitch;
            }

            _previousNotes.Clear();
            _previousNotes.AddRange(link.HeldNotes);
            return pressed;
        }

        string KeyCountLabel()
        {
            var layout = _profile.BuildLayout();
            return $"{layout.KeyCount}-key";
        }

        static string NoteName(int pitch)
        {
            string[] names = { "C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B" };
            return $"{names[((pitch % 12) + 12) % 12]}{pitch / 12 - 1}";
        }
    }
}
