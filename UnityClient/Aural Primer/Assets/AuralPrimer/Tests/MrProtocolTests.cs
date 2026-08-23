// Copyright 2026 Nathanael Anderson. Licensed under the Apache License 2.0.
//
// Wire-format tests for the MR link client.
//
// The byte fixtures below are asserted verbatim by the Rust host's
// protocol.rs tests as well. Pinning the same literals on both sides is what
// makes "the implementations agree" a fact rather than an intention: if either
// encoder drifts, one of the two suites fails immediately, instead of the
// headset quietly rendering nonsense.

using System.Collections.Generic;
using AuralPrimer.Link;
using NUnit.Framework;

namespace AuralPrimer.Link.Tests
{
    public class MrProtocolTests
    {
        [Test]
        public void ParsesTheHostBeacon()
        {
            Assert.IsTrue(MrProtocol.TryParseBeacon(
                "AURALPRIMER|1|BEACON|10.0.0.5|47762|STUDIO-PC", out var beacon));
            Assert.AreEqual("10.0.0.5", beacon.HostIp);
            Assert.AreEqual(47762, beacon.SessionPort);
            Assert.AreEqual("STUDIO-PC", beacon.HostName);
        }

        [Test]
        public void IgnoresForeignTrafficOnTheGroup()
        {
            // AugmentedDefense uses the same mechanism on a different socket. If
            // it ever shared ours, it must not be mistaken for a host.
            Assert.IsFalse(MrProtocol.TryParseBeacon("GameServer|192.168.1.5|47777", out _));
            Assert.IsFalse(MrProtocol.TryParseBeacon("ConnectRequest", out _));
            Assert.IsFalse(MrProtocol.TryParseBeacon("", out _));
            Assert.IsFalse(MrProtocol.TryParseBeacon(null, out _));
        }

        [Test]
        public void IgnoresADifferentProtocolVersion()
        {
            Assert.IsFalse(MrProtocol.TryParseBeacon("AURALPRIMER|2|BEACON|10.0.0.5|47762|PC", out _));
        }

        [Test]
        public void IgnoresTruncatedBeacons()
        {
            Assert.IsFalse(MrProtocol.TryParseBeacon("AURALPRIMER|1|BEACON|10.0.0.5", out _));
            Assert.IsFalse(MrProtocol.TryParseBeacon("AURALPRIMER|1", out _));
        }

        [Test]
        public void EmitsTheConnectTheHostExpects()
        {
            Assert.AreEqual("AURALPRIMER|1|CONNECT|Quest 3",
                System.Text.Encoding.UTF8.GetString(MrProtocol.EncodeConnect("Quest 3")));
        }

        [Test]
        public void FrameLengthIsPayloadOnlyLittleEndian()
        {
            // Getting either wrong desynchronises the stream permanently rather
            // than failing loudly.
            var framed = MrProtocol.EncodeFrame(MrProtocol.FrameHello, new byte[258]);
            Assert.AreEqual(0x02, framed[0]);
            Assert.AreEqual(0x01, framed[1]);
            Assert.AreEqual(MrProtocol.FrameHello, framed[4]);
            Assert.AreEqual(263, framed.Length);

            Assert.IsTrue(MrProtocol.TryDecodeFrameHeader(framed, out var len, out var type));
            Assert.AreEqual(258, len);
            Assert.AreEqual(MrProtocol.FrameHello, type);
        }

        [Test]
        public void RefusesAnImplausibleLengthPrefix()
        {
            var header = new byte[5];
            System.BitConverter.GetBytes((uint)(MrProtocol.MaxPayload + 1)).CopyTo(header, 0);
            Assert.IsFalse(MrProtocol.TryDecodeFrameHeader(header, out _, out _));
        }

        [Test]
        public void DecodesAPositionProducedByTheRustHost()
        {
            var bytes = new byte[]
            {
                0x40,
                0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x29, 0x40, // 12.5
                0xB1, 0x68, 0xDE, 0x3A, 0x00, 0x00, 0x00, 0x00, // 987654321
                0x01,
            };
            Assert.IsTrue(MrProtocol.TryDecodePosition(bytes, bytes.Length, out var pos));
            Assert.AreEqual(12.5, pos.SongTimeSec, 1e-9);
            Assert.AreEqual(987654321UL, pos.HostClockUs);
            Assert.IsTrue(pos.Playing);
        }

        [Test]
        public void DecodesAChordProducedByTheRustHost()
        {
            var bytes = new byte[]
            {
                0x41,
                0x2A, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, // 42
                0x03,
                60, 100, 64, 90, 67, 80,
            };
            var held = new List<(byte, byte)>();
            Assert.IsTrue(MrProtocol.TryDecodeNotes(bytes, bytes.Length, out var clock, held));
            Assert.AreEqual(42UL, clock);
            Assert.AreEqual(3, held.Count);
            Assert.AreEqual(((byte)60, (byte)100), held[0]);
            Assert.AreEqual(((byte)67, (byte)80), held[2]);
        }

        [Test]
        public void DecodesTheEmptyHeldNoteSet()
        {
            // Empty is how "all keys released" is communicated; mistaking it for
            // a malformed packet would leave the last chord lit forever.
            var bytes = new byte[] { 0x41, 0, 0, 0, 0, 0, 0, 0, 0, 0x00 };
            var held = new List<(byte, byte)> { (60, 100) };
            Assert.IsTrue(MrProtocol.TryDecodeNotes(bytes, bytes.Length, out _, held));
            Assert.AreEqual(0, held.Count);
        }

        [Test]
        public void RejectsATruncatedChordRatherThanLightingWrongKeys()
        {
            var bytes = new byte[] { 0x41, 0x2A, 0, 0, 0, 0, 0, 0, 0, 0x03, 60, 100, 64, 90, 67 };
            Assert.IsFalse(MrProtocol.TryDecodeNotes(bytes, bytes.Length, out _, new List<(byte, byte)>()));
        }

        [Test]
        public void RejectsACountThatDisagreesWithThePayload()
        {
            var bytes = new byte[] { 0x41, 0x2A, 0, 0, 0, 0, 0, 0, 0, 0x09, 60, 100 };
            Assert.IsFalse(MrProtocol.TryDecodeNotes(bytes, bytes.Length, out _, new List<(byte, byte)>()));
        }
    }
}
