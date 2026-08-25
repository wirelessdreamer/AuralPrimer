// Copyright 2026 Nathanael Anderson. Licensed under the Apache License 2.0.
//
// A local clock disciplined to the host's, and the song position derived from
// it. See docs/mr-link-protocol.md §5.
//
// This is what makes the headset need no latency calibration. Every term is
// measured or predicted:
//
//   clockOffset          measured, from PING/PONG round trips
//   predictedDisplayTime predicted by OpenXR — when THIS frame's photons land
//   audioOffsetSec       already calibrated on the host, sent in WELCOME
//
// No UnityEngine dependency, so the maths can be tested in a plain runner.

using System;
using Debug = UnityEngine.Debug;

namespace AuralPrimer.Link
{
    /// <summary>
    /// Tracks the offset between the local monotonic clock and the host's, and
    /// projects song position forward to when the frame will actually be seen.
    /// </summary>
    public sealed class HostClock
    {
        /// <summary>
        /// How many round trips to consider when picking the offset. Small on
        /// purpose: the point is to keep a recent good sample, not a long
        /// history that lags a genuine clock change.
        /// </summary>
        public const int SampleWindow = 16;

        readonly (double rttUs, double offsetUs)[] _samples = new (double, double)[SampleWindow];
        int _sampleCount;
        int _nextSample;

        double _offsetUs;
        bool _haveOffset;

        double _lastSongTimeSec;
        ulong _lastPositionHostClockUs;
        bool _playing;
        bool _havePosition;

        public double AudioOffsetSec { get; set; }

        public bool HasOffset => _haveOffset;
        public bool HasPosition => _havePosition;
        public bool Playing => _playing;

        /// <summary>Best (lowest-RTT) round-trip seen in the window, microseconds.</summary>
        public double BestRttUs { get; private set; } = double.PositiveInfinity;

        /// <summary>
        /// Record one PING/PONG exchange. All times in microseconds on their own
        /// clocks; the host's need not share an origin with ours.
        /// </summary>
        public void AddClockSample(ulong clientSendUs, ulong hostReplyUs, ulong clientRecvUs)
        {
            // A reply that arrives before it was sent is a bad measurement, not a
            // time machine — most likely a clock that stepped mid-flight.
            if (clientRecvUs < clientSendUs) return;

            double rtt = clientRecvUs - clientSendUs;
            double midpoint = clientSendUs + rtt / 2.0;
            double offset = hostReplyUs - midpoint;

            _samples[_nextSample] = (rtt, offset);
            _nextSample = (_nextSample + 1) % SampleWindow;
            if (_sampleCount < SampleWindow) _sampleCount++;

            // Take the offset from the LOWEST-RTT sample rather than averaging.
            // On Wi-Fi the fast samples are the honest ones: a stalled packet
            // inflates RTT asymmetrically, so its midpoint estimate is skewed,
            // and averaging lets that skew in and keeps it.
            var bestRtt = double.PositiveInfinity;
            var bestOffset = 0.0;
            for (var i = 0; i < _sampleCount; i++)
            {
                if (_samples[i].rttUs < bestRtt)
                {
                    bestRtt = _samples[i].rttUs;
                    bestOffset = _samples[i].offsetUs;
                }
            }

            BestRttUs = bestRtt;
            _offsetUs = bestOffset;
            _haveOffset = true;
        }

        /// <summary>Record a POSITION datagram from the host.</summary>
        public void AddPositionSample(double songTimeSec, ulong hostClockUs, bool playing)
        {
            // Two different things look like "the host clock went backwards",
            // and they need opposite handling.
            //
            // A small step back is a reordered datagram: drop it, or an older
            // sample undoes a newer one.
            //
            // A large one is a different host PROCESS. host_clock_us() is
            // monotonic from process start, so a relaunch resets it to near
            // zero -- and every offset we measured against the old process is
            // now wrong by however long that process had been up. Keeping them
            // renders the song that far AHEAD of the audio, which reads as the
            // sound lagging the notes by seconds.
            if (_havePosition && hostClockUs < _lastPositionHostClockUs)
            {
                var stepBackUs = _lastPositionHostClockUs - hostClockUs;
                if (stepBackUs < RestartStepBackUs) return;

                Debug.Log($"[clock] host clock stepped back {stepBackUs / 1e6:F1}s "
                        + "- treating as a restarted host and re-measuring");
                ForgetOffset();
            }

            _lastSongTimeSec = songTimeSec;
            _lastPositionHostClockUs = hostClockUs;
            _playing = playing;
            _havePosition = true;
        }

        /// <summary>
        /// Song position to render for a frame whose photons land at
        /// <paramref name="predictedDisplayTimeUs"/> on the local clock.
        /// </summary>
        public double SongTimeForDisplay(ulong predictedDisplayTimeUs)
        {
            if (!_havePosition) return 0.0;

            // While paused the position is held, not extrapolated: running the
            // clock on through a pause would drift the lane away from the audio
            // and only resynchronise on the next packet, as a visible jump.
            if (!_playing) return _lastSongTimeSec + AudioOffsetSec;

            double hostClockAtPhotons = predictedDisplayTimeUs + _offsetUs;
            double elapsedUs = hostClockAtPhotons - _lastPositionHostClockUs;
            return _lastSongTimeSec + elapsedUs / 1_000_000.0 + AudioOffsetSec;
        }

        /// <summary>
        /// A backwards step at least this large means a different host process,
        /// not a reordered packet. Well above any plausible reordering and well
        /// below any plausible process uptime.
        /// </summary>
        const double RestartStepBackUs = 1_000_000.0;

        /// <summary>
        /// Forget everything measured about the host, including the offset.
        /// </summary>
        /// <remarks>
        /// Called when the link drops. The offset used to be kept across a
        /// reconnect, on the grounds that re-measuring costs a round trip -- but
        /// a dropped link is exactly when the host may have been relaunched, and
        /// an offset measured against a process that no longer exists is wrong
        /// by that process's uptime. Being seconds out is far worse than
        /// spending one PING to find out again.
        /// </remarks>
        public void Reset()
        {
            ClearPosition();
            ForgetOffset();
        }

        void ForgetOffset()
        {
            _sampleCount = 0;
            _nextSample = 0;
            _haveOffset = false;
            _offsetUs = 0.0;
            BestRttUs = double.PositiveInfinity;
        }

        /// <summary>Forget where the song is, keeping the measured clock offset.</summary>
        public void ClearPosition()
        {
            _havePosition = false;
            _playing = false;
        }
    }
}
