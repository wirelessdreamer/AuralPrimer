// Copyright 2026 Nathanael Anderson. Licensed under the Apache License 2.0.
//
// What to do with a tracked pose that has gone missing.

using System;

namespace AuralPrimer.Calibration
{
    /// <summary>
    /// Hold the last good pose, then fade, rather than snapping.
    /// </summary>
    /// <remarks>
    /// A controller strapped to a guitar is never held, and its LEDs face the
    /// headset only while the player looks roughly at the neck: it will drop
    /// out whenever they turn their head. Hiding the highway the instant that
    /// happens would flicker it on every glance away; freezing it in place
    /// forever would leave notes hanging where the neck used to be once the
    /// guitar moves. A short hold covers the glance, and a fade says honestly
    /// that the picture is going stale.
    /// </remarks>
    public static class TrackingHold
    {
        /// <summary>
        /// Opacity for a pose last seen <paramref name="lostSeconds"/> ago: 1
        /// for <paramref name="holdSeconds"/>, then down to 0 over
        /// <paramref name="fadeSeconds"/>.
        /// </summary>
        public static float Visibility(float lostSeconds, float holdSeconds, float fadeSeconds)
        {
            if (lostSeconds <= holdSeconds) return 1f;
            if (fadeSeconds <= 0f) return 0f;
            return Math.Clamp(1f - (lostSeconds - holdSeconds) / fadeSeconds, 0f, 1f);
        }
    }
}
