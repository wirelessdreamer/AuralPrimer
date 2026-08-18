// Copyright 2026 Nathanael Anderson. Licensed under the Apache License 2.0.
//
// The clock maths that removes the need for any headset-latency calibration.
// If these are wrong the notes drift against the audio, which is the single
// most damaging failure this client can have — so they are tested rather than
// reasoned about.

using AuralPrimer.Link;
using NUnit.Framework;

namespace AuralPrimer.Link.Tests
{
    public class HostClockTests
    {
        [Test]
        public void ReportsNoPositionBeforeAnyPacket()
        {
            var clock = new HostClock();
            Assert.IsFalse(clock.HasPosition);
            Assert.AreEqual(0.0, clock.SongTimeForDisplay(1_000), 1e-9);
        }

        [Test]
        public void LowestRttSampleWinsOverAStalledOne()
        {
            // The host runs 1,000,000us ahead. Three exchanges, one of them
            // stalled: on Wi-Fi the fast samples are the honest ones, because a
            // stalled packet inflates RTT asymmetrically and skews its own
            // midpoint estimate. Averaging would let that skew in and keep it.
            var clock = new HostClock();
            clock.AddClockSample(1_000, 1_001_100, 1_200);   // rtt 200
            clock.AddClockSample(2_000, 2_051_000, 12_000);  // rtt 10000, skewed
            clock.AddClockSample(3_000, 3_001_150, 3_300);   // rtt 300

            Assert.IsTrue(clock.HasOffset);
            Assert.AreEqual(200.0, clock.BestRttUs, 1e-9);
        }

        [Test]
        public void ProjectsSongTimeToWhenThePhotonsLand()
        {
            var clock = new HostClock();
            clock.AddClockSample(1_000, 1_001_100, 1_200); // offset ~= 1,000,000us
            clock.AddPositionSample(songTimeSec: 10.0, hostClockUs: 5_000_000, playing: true);

            // local 4,100,000 + offset 1,000,000 = host 5,100,000, i.e. 0.1s on.
            Assert.AreEqual(10.1, clock.SongTimeForDisplay(4_100_000), 1e-6);
        }

        [Test]
        public void AddsTheHostsMeasuredAudioOffset()
        {
            var clock = new HostClock { AudioOffsetSec = 0.042 };
            clock.AddClockSample(1_000, 1_001_100, 1_200);
            clock.AddPositionSample(10.0, 5_000_000, playing: true);

            Assert.AreEqual(10.142, clock.SongTimeForDisplay(4_100_000), 1e-6);
        }

        [Test]
        public void HoldsPositionWhilePausedInsteadOfExtrapolating()
        {
            // Running the clock on through a pause would drift the lane away
            // from the audio and then resynchronise as a visible jump.
            var clock = new HostClock();
            clock.AddClockSample(1_000, 1_001_100, 1_200);
            clock.AddPositionSample(20.0, 6_000_000, playing: false);

            Assert.AreEqual(20.0, clock.SongTimeForDisplay(99_999_999), 1e-6);
        }

        [Test]
        public void IgnoresAnOutOfOrderOlderSample()
        {
            // Datagrams can overtake each other; an older one must not undo a
            // newer one and yank the playhead backwards.
            var clock = new HostClock();
            clock.AddClockSample(1_000, 1_001_100, 1_200);
            clock.AddPositionSample(20.0, 6_000_000, playing: false);
            clock.AddPositionSample(5.0, 1_000, playing: false);

            Assert.AreEqual(20.0, clock.SongTimeForDisplay(99_999_999), 1e-6);
        }

        [Test]
        public void DiscardsAReplyThatArrivedBeforeItWasSent()
        {
            // Not a time machine — a clock that stepped mid-flight. Taking it
            // would poison the offset for as long as it stayed in the window.
            var clock = new HostClock();
            clock.AddClockSample(1_000, 1_001_100, 1_200);
            var good = clock.BestRttUs;

            clock.AddClockSample(clientSendUs: 500, hostReplyUs: 0, clientRecvUs: 100);

            Assert.AreEqual(good, clock.BestRttUs, 1e-9);
        }

        [Test]
        public void ClearingPositionKeepsTheMeasuredOffset()
        {
            // A dropped link should not throw away a good clock measurement:
            // reconnecting re-runs discovery, not the whole clock discipline.
            var clock = new HostClock();
            clock.AddClockSample(1_000, 1_001_100, 1_200);
            clock.AddPositionSample(10.0, 5_000_000, playing: true);

            clock.ClearPosition();

            Assert.IsFalse(clock.HasPosition);
            Assert.IsTrue(clock.HasOffset);
        }
    }
}
