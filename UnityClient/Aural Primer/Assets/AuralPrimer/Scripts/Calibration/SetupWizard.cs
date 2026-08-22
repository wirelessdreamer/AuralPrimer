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
        [SerializeField] KeyboardAnchor keyboardAnchor;

        [Header("Pinch debug markers")]
        [Tooltip("A cube is left at each pinch so the captured point can be "
               + "compared against the real key it was meant to mark. The whole "
               + "class of bug this session has been the app placing things "
               + "somewhere other than where the hand was, which is invisible "
               + "until something is drawn at the captured point itself.")]
        [SerializeField] bool showPinchMarkers = true;
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

                case Step.Done:
                    // Not hidden: parked above the instrument. See ParkAboveKeyboard.
                    ParkAboveKeyboard();
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
                    EnterStep(Step.Done);
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
        void ParkAboveKeyboard()
        {
            if (panel == null || _profile is not { IsCalibrated: true }) return;

            var space = keyboardAnchor != null ? keyboardAnchor.Space : null;
            var centreLocal = Vector3.Lerp(_profile.leftEdge, _profile.rightEdge, 0.5f);
            var upLocal = _profile.up.sqrMagnitude > 1e-6f ? _profile.up.normalized : Vector3.up;

            // Clear of the top of the note lane, so it never sits over the music.
            var laneHeight = _profile.laneHeightMetres * Mathf.Max(0.01f, _profile.spacingMultiplier);
            var aboveLocal = centreLocal + upLocal * (laneHeight + 0.18f);

            var position = space != null ? space.TransformPoint(aboveLocal) : aboveLocal;

            // Face the player rather than the instrument's own axis: this is
            // something to read, and it is directly above what it describes.
            var camera = Camera.main;
            var toPlayer = camera != null ? camera.transform.position - position : Vector3.back;
            toPlayer.y = 0f;
            if (toPlayer.sqrMagnitude < 1e-4f) toPlayer = Vector3.back;

            panel.PlaceAt(position, Quaternion.LookRotation(-toPlayer.normalized, Vector3.up));
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

            // Both renderers work in the anchor's space, so parent them to it.
            // Re-localisation then moves the keys and the lane together, with no
            // per-frame bookkeeping here.
            var space = keyboardAnchor != null ? keyboardAnchor.Space : null;
            if (space != null)
            {
                if (overlay != null) overlay.transform.SetParent(space, false);
                if (highway != null) highway.transform.SetParent(space, false);
            }

            if (overlay != null) overlay.Apply(_profile);
            // The lane hangs off the same calibration: without it there is no
            // keyboard for the notes to line up above.
            if (highway != null) highway.Apply(_profile);

            ParkAboveKeyboard();
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
