// Copyright 2026 Nathanael Anderson. Licensed under the Apache License 2.0.
//
// First-run wizard: connect to the desktop app, then calibrate the keyboard.
//
// Every step is verifiable by MIDI rather than by eye. That is the whole design:
// the app knows which pitch arrived, so it can prove the overlay lines up
// instead of asking the player whether it looks right. In particular the
// off-by-one-key error — the classic failure, and subtly wrong for a whole
// session — becomes obvious the moment a key is played.

using System.Collections.Generic;
using AuralPrimer.Link;
using AuralPrimer.UI;
using UnityEngine;

namespace AuralPrimer.Calibration
{
    public sealed class SetupWizard : MonoBehaviour
    {
        public enum Step
        {
            Connecting,
            LowestKey,
            HighestKey,
            MarkLeftEdge,
            MarkRightEdge,
            Verify,
            Done,
        }

        [SerializeField] MrLinkBehaviour link;
        [SerializeField] WizardPanel panel;
        [SerializeField] HandGestures hands;
        [SerializeField] KeyboardOverlay overlay;
        [SerializeField] NoteHighway highway;
        [SerializeField] string profileName = "My keyboard";

        readonly List<(byte pitch, byte velocity)> _previousNotes = new();

        CalibrationProfile _profile;
        Step _step = Step.Connecting;
        int _lowestPitch;
        int _highestPitch;
        float _widthErrorRatio;
        int _verifiedKeys;
        int _lastVerifyPitch = -1;

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
        }

        void OnDisable()
        {
            if (hands != null)
            {
                hands.PinchStarted -= OnPinch;
                hands.MenuSummoned -= OnMenuSummoned;
            }
        }

        void Update()
        {
            UpdateStatusLine();

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
                        // known: re-calibrating every session would be absurd.
                        // But a saved profile holds world coordinates, and the
                        // headset re-localises between sessions — so yesterday's
                        // numbers can put the keyboard across the room or under
                        // the floor. Rather than draw the overlay somewhere the
                        // user will never find it, treat an implausible profile
                        // as no profile and ask for the two edges again.
                        if (_profile is { IsCalibrated: true } && !ProfileLooksStale(_profile))
                        {
                            ApplyProfile();
                            EnterStep(Step.Done);
                        }
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
                    _profile.leftEdge = position;
                    EnterStep(Step.MarkRightEdge);
                    break;

                case Step.MarkRightEdge:
                    _profile.rightEdge = position;
                    _profile.up = Vector3.up;

                    // Cross-check the span against the instrument's real size
                    // before trusting it. A measurement well off expectation is
                    // a mis-tap, and silently building a skewed lane from it
                    // would be wrong for the whole session.
                    var layout = _profile.BuildLayout();
                    _widthErrorRatio = (float)layout.WidthErrorRatio(_profile.WidthMetres);
                    ApplyProfile();
                    EnterStep(Step.Verify);
                    break;

                case Step.Done:
                    panel?.SetVisible(false);
                    break;
            }
        }

        void OnMenuSummoned()
        {
            // Summon repositions rather than merely toggling: a panel behind you
            // is the same as no panel.
            panel?.PlaceInFrontOfUser();
        }

        void EnterStep(Step step)
        {
            _step = step;
            _verifiedKeys = 0;
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
            switch (_step)
            {
                case Step.Connecting:
                    panel?.SetTitle("Connecting");
                    panel?.SetBody(
                        "Looking for AuralPrimer on your network.\n\n"
                        + "Make sure the desktop app is running and this headset is on the "
                        + "same Wi-Fi.");
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

                case Step.Done:
                    panel?.SetTitle("Ready");
                    panel?.SetBody(
                        $"<b>{_profile.profileName}</b> is calibrated.\n\n"
                        + "Pinch to dismiss. Hold a palm toward your face to bring this back.");
                    break;
            }
        }

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
                EnterStep(Step.Done);
            }
        }

        /// <summary>Is this saved placement too far from the user to be real?</summary>
        static bool ProfileLooksStale(CalibrationProfile profile)
        {
            var camera = Camera.main;
            if (camera == null) return false;

            var centre = (profile.leftEdge + profile.rightEdge) * 0.5f;
            var toKeyboard = centre - camera.transform.position;

            // A keyboard you are playing is within arm's reach and roughly at
            // desk height relative to the head. Anything else is a coordinate
            // from a session that no longer exists.
            var tooFar = toKeyboard.magnitude > 2.5f;
            var tooHigh = toKeyboard.y > 0.5f;
            var tooLow = toKeyboard.y < -1.5f;

            if (tooFar || tooHigh || tooLow)
            {
                Debug.Log($"[wizard] saved keyboard is {toKeyboard.magnitude:F2} m away "
                        + $"({toKeyboard.y:F2} m vertically) — re-calibrating");
                return true;
            }
            return false;
        }

        void ApplyProfile()
        {
            if (_profile != null)
            {
                Debug.Log($"[wizard] applying profile: calibrated={_profile.IsCalibrated} "
                        + $"pitches={_profile.lowestPitch}..{_profile.highestPitch} "
                        + $"width={_profile.WidthMetres:F3}m "
                        + $"left={_profile.leftEdge} right={_profile.rightEdge}");
            }

            if (overlay != null) overlay.Apply(_profile);
            // The lane hangs off the same calibration: without it there is no
            // keyboard for the notes to line up above.
            if (highway != null) highway.Apply(_profile);
        }

        void UpdateStatusLine()
        {
            if (panel == null) return;

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
                panel.SetStatus("searching for the desktop app…", new Color(0.98f, 0.75f, 0.15f));
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
