// Copyright 2026 Nathanael Anderson. Licensed under the Apache License 2.0.
//
// Simultaneous hands and controllers, which only Meta's Core SDK can switch.
//
// Isolated behind META_XR_CORE, like the recorder's body/face/eye channels, so
// the client still compiles without the Meta package -- it then simply relies
// on whatever the runtime does by default, and the setup tells the player what
// to change if the controller on the guitar keeps disappearing.

using UnityEngine;

namespace AuralPrimer.Calibration
{
    public sealed partial class GuitarAnchor
    {
        /// <summary>
        /// Did the runtime accept simultaneous hands and controllers? Null until asked.
        /// </summary>
        /// <remarks>
        /// False on a runtime without XR_META_simultaneous_hands_and_controllers
        /// or without the Meta SDK; the setup reads this to decide whether to
        /// warn that Horizon OS may swap the set-down controller for hands.
        /// </remarks>
        public bool? SimultaneousModeAccepted { get; private set; }

#if META_XR_CORE
        /// <summary>When to ask again after a refusal (unscaled seconds).</summary>
        float _nextSimultaneousAsk;

        partial void SetSimultaneousHandsAndControllers(bool enabled)
        {
            // The player's hands are on the strings while the controller is
            // strapped to the head: the runtime has to deliver both at once.
            // This is Meta's documented use for the mode, not a workaround.
            var accepted = OVRPlugin.SetSimultaneousHandsAndControllersEnabled(enabled);
            var was = SimultaneousModeAccepted;
            if (enabled) SimultaneousModeAccepted = accepted;
            // Logged on a change only: a refusal is retried every second.
            if (!enabled || was != SimultaneousModeAccepted)
            {
                Debug.Log($"[guitar] simultaneous hands and controllers {(enabled ? "on" : "off")}: "
                        + (accepted ? "accepted" : "refused (retrying)"));
            }
        }

        partial void KeepSimultaneousHandsAndControllers()
        {
            if (SimultaneousModeAccepted == true || Time.unscaledTime < _nextSimultaneousAsk) return;
            _nextSimultaneousAsk = Time.unscaledTime + 1f;
            SetSimultaneousHandsAndControllers(true);
        }
#endif
    }
}
