// Copyright 2026 Nathanael Anderson. Licensed under the Apache License 2.0.
//
// The session's read path, against a host that goes quiet mid-frame.
//
// This is the failure that cost the most to find, because it was invisible from
// both ends: the host is healthy, the client reports a session opening, and a
// second later the link is gone with nothing logged. It only happens once a
// song is loaded — the CHART is tens of kilobytes and spans many TCP segments,
// where every other frame in the protocol is small enough to arrive whole.

using System;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using NUnit.Framework;

namespace AuralPrimer.Link.Tests
{
    public sealed class MrSessionClientTests
    {
        /// <summary>
        /// A host that sends a large CHART with a deliberate silence in the
        /// middle of the payload, longer than the client's stream read timeout.
        /// </summary>
        sealed class StallingHost : IDisposable
        {
            readonly TcpListener _listener;
            readonly Thread _thread;
            volatile bool _running = true;

            public int Port { get; }

            public StallingHost(int chartBytes, int stallMs, int firstChunk = 1000)
            {
                _listener = new TcpListener(IPAddress.Loopback, 0);
                _listener.Start();
                Port = ((IPEndPoint)_listener.LocalEndpoint).Port;

                _thread = new Thread(() => Serve(chartBytes, stallMs, firstChunk))
                {
                    IsBackground = true,
                    Name = "stalling-host",
                };
                _thread.Start();
            }

            void Serve(int chartBytes, int stallMs, int firstChunk)
            {
                try
                {
                    using var client = _listener.AcceptTcpClient();
                    using var stream = client.GetStream();

                    // Consume the HELLO frame so the client's write completes.
                    var header = new byte[5];
                    ReadFully(stream, header, 5);
                    var helloLen = BitConverter.ToInt32(header, 0);
                    if (helloLen > 0) ReadFully(stream, new byte[helloLen], helloLen);

                    var welcome = Encoding.UTF8.GetBytes(
                        "{\"host\":\"StallHost\",\"protocol\":1,\"udpPort\":55999,\"audioOffsetSec\":0.0}");
                    Write(stream, 0x02, welcome);

                    // CHART: header plus a first chunk, a long silence, then the
                    // rest. Nothing else may be written in between — interleaving
                    // another frame inside a payload desynchronises the stream and
                    // looks exactly like a client bug. The real host cannot do it,
                    // because it writes the whole CHART before its request loop.
                    var chart = new byte[chartBytes];
                    for (var i = 0; i < chart.Length; i++) chart[i] = (byte)' ';
                    Encoding.ASCII.GetBytes("{\"notes\":[]}").CopyTo(chart, 0);

                    var frameHeader = new byte[5];
                    BitConverter.GetBytes(chartBytes).CopyTo(frameHeader, 0);
                    frameHeader[4] = 0x10; // CHART
                    stream.Write(frameHeader, 0, 5);
                    stream.Write(chart, 0, firstChunk);
                    stream.Flush();

                    Thread.Sleep(stallMs);

                    stream.Write(chart, firstChunk, chartBytes - firstChunk);
                    stream.Flush();

                    // Stay connected, ignoring pings. The client must not need a
                    // reply to consider the link alive.
                    while (_running) Thread.Sleep(50);
                }
                catch (Exception)
                {
                    // Torn down by Dispose, or the client went away first.
                }
            }

            static void ReadFully(Stream stream, byte[] buffer, int count)
            {
                var read = 0;
                while (read < count)
                {
                    var n = stream.Read(buffer, read, count - read);
                    if (n <= 0) throw new IOException("peer closed during handshake");
                    read += n;
                }
            }

            static void Write(Stream stream, byte type, byte[] payload)
            {
                var frame = new byte[5 + payload.Length];
                BitConverter.GetBytes(payload.Length).CopyTo(frame, 0);
                frame[4] = type;
                payload.CopyTo(frame, 5);
                stream.Write(frame, 0, frame.Length);
                stream.Flush();
            }

            public void Dispose()
            {
                _running = false;
                try { _listener.Stop(); } catch { /* already stopped */ }
            }
        }

        [Test]
        public void A_large_frame_survives_a_silence_longer_than_the_read_timeout()
        {
            const int chartBytes = 88415; // the size actually observed in the wild
            const int stallMs = 2500;     // comfortably past the 1s stream timeout

            using var host = new StallingHost(chartBytes, stallMs);
            var client = new MrSessionClient("StallTest");

            try
            {
                client.Connect(new HostEndpoint("127.0.0.1", host.Port, "StallHost"));

                var clock = Stopwatch.StartNew();
                string chart = null;
                while (clock.ElapsedMilliseconds < 8000)
                {
                    Assert.That(client.IsConnected, Is.True,
                        $"session dropped after {clock.ElapsedMilliseconds} ms; a quiet "
                      + "moment mid-payload is not a dead link");

                    if (client.TryDequeueChart(out chart)) break;
                    Thread.Sleep(50);
                }

                Assert.That(chart, Is.Not.Null, "the chart never arrived");
                Assert.That(chart.Length, Is.EqualTo(chartBytes),
                    "the chart arrived truncated, which would desynchronise every frame after it");
                Assert.That(client.IsConnected, Is.True, "the session did not survive the chart");
            }
            finally
            {
                client.Dispose();
            }
        }
    }
}
