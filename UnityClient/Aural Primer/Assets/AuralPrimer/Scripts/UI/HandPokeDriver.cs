// Copyright 2026 Nathanael Anderson. Licensed under the Apache License 2.0.
//
// Puts a poke interactor on the index fingertip.
//
// Touching a button is the primary way to use the menu; the ray is the backup
// for when it is out of reach. That ordering matters for where the menu lives
// and how it behaves — a panel you have to aim a laser at wants to be far away
// and large, a panel you touch wants to be within arm's reach and solid — and
// getting it the wrong way round is what makes hand-tracked UI feel remote.
//
// Reads XR Hands joints directly, like HandRayDriver, rather than depending on
// an interaction profile resolving. Same reason: an unresolved binding is not
// an error, it is silence, and silence here means a fingertip parked at the
// rig origin poking whatever happens to be there.

using Unity.XR.CoreUtils;
using UnityEngine;
using UnityEngine.XR.Hands;
using UnityEngine.XR.Interaction.Toolkit.Interactors;
using UnityEngine.XR.Management;

namespace AuralPrimer.UI
{
    [AddComponentMenu("AuralPrimer/Hand Poke Driver")]
    [DefaultExecutionOrder(-100)] // pose before the interactor tests against it
    public sealed class HandPokeDriver : MonoBehaviour
    {
        [Tooltip("Which hand's fingertip this follows.")]
        [SerializeField] bool leftHand;

        XRHandSubsystem _hands;
        XROrigin _origin;
        XRPokeInteractor _interactor;

        /// <summary>Which hand this follows. Set when built at edit time.</summary>
        public bool LeftHand { get => leftHand; set => leftHand = value; }

        Transform TrackingSpace
        {
            get
            {
                var camera = _origin != null && _origin.Camera != null
                    ? _origin.Camera.transform
                    : null;
                if (camera == null && Camera.main != null) camera = Camera.main.transform;
                return camera != null ? camera.parent : null;
            }
        }

        float _nextReport;

        void Awake()
        {
            _origin = FindFirstObjectByType<XROrigin>();
            _interactor = GetComponent<XRPokeInteractor>();

            if (_interactor != null)
            {
                // Explicit rather than inherited. The defaults are tuned for a
                // controller poking a rigid panel; a tracked fingertip jitters,
                // and a hover radius measured in millimetres never lands.
                _interactor.enableUIInteraction = true;
                // Buttons, not objects.
                //
                // With this false the fingertip selects ANY interactable it
                // touches — the lane bar, the calibration handles — and a
                // poke-select ends on finger withdrawal rather than on
                // unpinching. So the bar could be grabbed and not let go, while
                // the ray reported selecting=False the whole time because the
                // ray was never the thing holding it.
                //
                // True restricts poke to interactables carrying an XRPokeFilter,
                // which none of ours do. UI presses go through the graphic
                // raycaster and are unaffected.
                _interactor.requirePokeFilter = true;
                _interactor.pokeDepth = 0.12f;
                _interactor.pokeWidth = 0.012f;
                _interactor.pokeSelectWidth = 0.020f;
                _interactor.pokeHoverRadius = 0.020f;
                _interactor.clickUIOnDown = true;
            }

            SetActive(false);
        }

        /// <summary>
        /// Once a second, say where the fingertip is and how far it is from the
        /// menu — the two numbers that decide whether a poke can ever land.
        /// </summary>
        void Report()
        {
            if (Time.unscaledTime < _nextReport) return;
            _nextReport = Time.unscaledTime + 1f;

            var panel = FindFirstObjectByType<WizardPanel>();
            var distance = panel != null
                ? Vector3.Distance(transform.position, panel.transform.position)
                : -1f;

            Debug.Log($"[poke] {(leftHand ? "left" : "right")} "
                    + $"enabled={(_interactor != null && _interactor.enabled)} "
                    + $"tip={transform.position:F3} "
                    + $"toMenu={distance:F3}m "
                    + $"(a poke needs this under ~{(_interactor != null ? _interactor.pokeDepth : 0f):F2})");
        }

        void Update()
        {
            if (_hands == null || !_hands.running)
            {
                var loader = XRGeneralSettings.Instance?.Manager?.activeLoader;
                _hands = loader?.GetLoadedSubsystem<XRHandSubsystem>();
                if (_hands == null) { SetActive(false); return; }
            }

            var hand = leftHand ? _hands.leftHand : _hands.rightHand;
            if (!hand.isTracked)
            {
                SetActive(false);
                return;
            }

            if (!hand.GetJoint(XRHandJointID.IndexTip).TryGetPose(out var tip))
            {
                SetActive(false);
                return;
            }

            // Joint poses are relative to the XR Origin; this transform is in
            // world space. They coincide only while the origin sits at identity,
            // so mixing them works right up until the player is recentered.
            var space = TrackingSpace;
            transform.SetPositionAndRotation(
                space != null ? space.TransformPoint(tip.position) : tip.position,
                space != null ? space.rotation * tip.rotation : tip.rotation);

            SetActive(true);
            Report();
        }

        void SetActive(bool active)
        {
            if (_interactor != null && _interactor.enabled != active) _interactor.enabled = active;
        }
    }
}
