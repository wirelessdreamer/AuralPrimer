// Copyright 2026 Nathanael Anderson. Licensed under the Apache License 2.0.
//
// Session client: TCP for the chart and clock exchange, UDP for position and
// held notes. Mirrors the Rust host's session.rs; see
// docs/mr-link-protocol.md §2–§3.

using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using Debug = UnityEngine.Debug;

namespace AuralPrimer.Link
{
    /// <summary>
    /// Connects to a discovered host and keeps <see cref="Clock"/> and
    /// <see cref="HeldNotes"/> current.
    ///
    /// Sockets run on background threads; everything the rest of the app touches
    /// is either a concurrent collection or read under a lock, because Unity's
    /// main thread will be reading these every frame while packets keep landing.
    /// </summary>
    public sealed class MrSessionClient : IDisposable
    {
        const int PingIntervalMs = 500;

        readonly string _clientName;
        readonly Stopwatch _clock = Stopwatch.StartNew();
        readonly object _notesLock = new();
        readonly List<(byte pitch, byte velocity)> _heldNotes = new();
        readonly ConcurrentQueue<string> _charts = new();

        TcpClient _tcp;
        NetworkStream _stream;
        UdpClient _udp;
        Thread _tcpThread;
        Thread _udpThread;
        volatile bool _running;

        public MrSessionClient(string clientName)
        {
            _clientName = string.IsNullOrWhiteSpace(clientName) ? "Quest" : clientName;
        }

        public HostClock Clock { get; } = new();
        // Deliberately not TcpClient.Connected: that reports the state of the
        // last I/O, and a read timeout — which this loop takes once a second by
        // design, because the host has nothing to say on TCP while a song is
        // idle — flips it to false on a perfectly healthy socket. Reading it
        // here made the link drop and rediscover every two seconds forever.
        // _running is the honest signal: the loop clears it when the link dies.
        public bool IsConnected => _running && _tcp != null;
        public string HostName { get; private set; } = "";

        /// <summary>Local monotonic microseconds — the clock all client-side
        /// timestamps are expressed on.</summary>
        public ulong NowUs => (ulong)(_clock.Elapsed.Ticks / (TimeSpan.TicksPerMillisecond / 1000));

        /// <summary>Copy the current held-note set into <paramref name="into"/>.</summary>
        public void CopyHeldNotes(List<(byte pitch, byte velocity)> into)
        {
            into.Clear();
            lock (_notesLock)
            {
                into.AddRange(_heldNotes);
            }
        }

        /// <summary>Dequeue a chart delivered by the host, if any.</summary>
        public bool TryDequeueChart(out string chartJson) => _charts.TryDequeue(out chartJson);

        public void Connect(HostEndpoint host)
        {
            Disconnect();
            _running = true;

            try
            {
                _tcp = new TcpClient();
                _tcp.Connect(host.Address, host.SessionPort);
                _tcp.NoDelay = true;
                _stream = _tcp.GetStream();
                _stream.ReadTimeout = 1000;

                // Bind our own receive port before saying hello, and tell the
                // host where to stream. The host used to nominate the port and
                // expect us to bind the same number, which couples two machines
                // to one arbitrary value: if anything here already holds it the
                // streams simply never arrive. It also makes running the client
                // on the host itself impossible, and that is the ordinary case
                // when testing in the Editor. Port 0 lets the OS pick a free one.
                _udp = new UdpClient(new IPEndPoint(IPAddress.Any, 0));
                _udp.Client.ReceiveTimeout = 500;
                var localUdpPort = ((IPEndPoint)_udp.Client.LocalEndPoint).Port;

                var hello = Encoding.UTF8.GetBytes(
                    $"{{\"client\":\"{_clientName}\",\"protocol\":{MrProtocol.ProtocolVersion},"
                  + $"\"udpPort\":{localUdpPort}}}");
                Send(MrProtocol.FrameHello, hello);

                _tcpThread = new Thread(TcpLoop) { IsBackground = true, Name = "mr-session-tcp" };
                _tcpThread.Start();

                Debug.Log($"[mr-link] session open to {host}");
            }
            catch (Exception e)
            {
                Debug.LogError($"[mr-link] session connect failed: {e.Message}");
                Disconnect();
            }
        }

        public void Disconnect()
        {
            _running = false;
            try { _stream?.Close(); } catch { }
            try { _tcp?.Close(); } catch { }
            try { _udp?.Close(); } catch { }
            _stream = null;
            _tcp = null;
            _udp = null;
            Clock.ClearPosition();
            lock (_notesLock) _heldNotes.Clear();
        }

        void Send(byte frameType, byte[] payload)
        {
            // Copy the reference: Disconnect can null it between the check and
            // the write, which is where "Object reference not set" came from
            // while the link was reconnecting every two seconds.
            var stream = _stream;
            if (stream == null) return;

            var frame = MrProtocol.EncodeFrame(frameType, payload);
            stream.Write(frame, 0, frame.Length);
            stream.Flush();
        }

        /// <summary>Ask the host to change transport state. The host stays the
        /// authority; this is a request, not a command.</summary>
        public void SendTransport(string action, double? tSec = null)
        {
            if (!IsConnected) return;
            try
            {
                var json = tSec.HasValue
                    ? $"{{\"action\":\"{action}\",\"tSec\":{tSec.Value.ToString(System.Globalization.CultureInfo.InvariantCulture)}}}"
                    : $"{{\"action\":\"{action}\"}}";
                Send(MrProtocol.FrameTransport, Encoding.UTF8.GetBytes(json));
            }
            catch (Exception e)
            {
                Debug.LogWarning($"[mr-link] transport send failed: {e.Message}");
            }
        }

        void TcpLoop()
        {
            var header = new byte[5];
            var nextPing = DateTime.UtcNow;

            while (_running)
            {
                try
                {
                    if (DateTime.UtcNow >= nextPing)
                    {
                        Send(MrProtocol.FramePing, MrProtocol.WriteUInt64LE(NowUs));
                        nextPing = DateTime.UtcNow.AddMilliseconds(PingIntervalMs);
                    }

                    var headerRead = ReadExact(header, 5);
                    if (headerRead == ReadOutcome.TimedOut) continue;
                    if (headerRead == ReadOutcome.Closed) break;
                    if (!MrProtocol.TryDecodeFrameHeader(header, out var length, out var frameType))
                    {
                        // An implausible prefix means the stream is desynchronised;
                        // there is no safe way to resynchronise, so drop the link
                        // and rediscover rather than reading garbage forever.
                        Debug.LogError("[mr-link] implausible frame length; dropping session");
                        break;
                    }

                    var payload = length > 0 ? new byte[length] : Array.Empty<byte>();
                    if (length > 0 && ReadExact(payload, length) != ReadOutcome.Complete) break;

                    HandleFrame(frameType, payload);
                }
                catch (IOException)
                {
                    // Read timeout is how this loop stays responsive to Disconnect.
                }
                catch (ObjectDisposedException)
                {
                    return;
                }
                catch (Exception e)
                {
                    Debug.LogWarning($"[mr-link] session error: {e.Message}");
                    break;
                }
            }

            _running = false;
        }

        enum ReadOutcome
        {
            /// <summary>The requested bytes are in the buffer.</summary>
            Complete,
            /// <summary>Nothing arrived in time. Normal, and how this loop stays
            /// responsive to Disconnect while the host has nothing to send.</summary>
            TimedOut,
            /// <summary>The peer closed. Retrying reads zero bytes forever.</summary>
            Closed,
        }

        /// <summary>Read exactly <paramref name="count"/> bytes. A short read is
        /// normal on TCP; treating one as a whole frame desynchronises the
        /// stream permanently.</summary>
        ReadOutcome ReadExact(byte[] buffer, int count)
        {
            var stream = _stream;
            if (stream == null) return ReadOutcome.Closed;

            var read = 0;
            while (read < count)
            {
                int n;
                try
                {
                    n = stream.Read(buffer, read, count - read);
                }
                catch (IOException)
                {
                    return ReadOutcome.TimedOut;
                }
                catch (ObjectDisposedException)
                {
                    return ReadOutcome.Closed;
                }
                // Zero from a blocking read is the peer having gone away, not a
                // timeout. Treated as retryable it spins at full speed forever.
                if (n <= 0) return ReadOutcome.Closed;
                read += n;
            }
            return ReadOutcome.Complete;
        }

        void HandleFrame(byte frameType, byte[] payload)
        {
            switch (frameType)
            {
                case MrProtocol.FrameWelcome:
                    HandleWelcome(Encoding.UTF8.GetString(payload));
                    break;

                case MrProtocol.FrameChart:
                    _charts.Enqueue(Encoding.UTF8.GetString(payload));
                    break;

                case MrProtocol.FramePong:
                    if (payload.Length == 16)
                    {
                        Clock.AddClockSample(
                            MrProtocol.ReadUInt64LE(payload, 0),
                            MrProtocol.ReadUInt64LE(payload, 8),
                            NowUs);
                    }
                    break;

                case MrProtocol.FrameSongChanged:
                    // The chart follows; nothing to do but wait for it.
                    break;
            }
        }

        void HandleWelcome(string json)
        {
            // Deliberately a minimal scan rather than a JSON dependency: this is
            // four known fields on a hot path, and adding a parser to the client
            // for it would be more surface than it is worth.
            HostName = ExtractString(json, "host") ?? "host";
            var udpPort = (int)(ExtractNumber(json, "udpPort") ?? 0);
            Clock.AudioOffsetSec = ExtractNumber(json, "audioOffsetSec") ?? 0.0;

            if (_udp == null)
            {
                Debug.LogError("[mr-link] no stream socket; the session cannot receive");
                return;
            }

            // udpPort in WELCOME is the host's own send port. It is informational
            // now that the client nominates where streams land, and is logged
            // only because it is the first thing worth knowing when they don't.
            _udpThread = new Thread(UdpLoop) { IsBackground = true, Name = "mr-session-udp" };
            _udpThread.Start();

            var localPort = ((IPEndPoint)_udp.Client.LocalEndPoint).Port;
            Debug.Log($"[mr-link] streams inbound on udp/{localPort} (host sends from udp/{udpPort}), "
                    + $"host audio offset {Clock.AudioOffsetSec:F3}s");
        }

        void UdpLoop()
        {
            var any = new IPEndPoint(IPAddress.Any, 0);
            var scratch = new List<(byte pitch, byte velocity)>();

            while (_running)
            {
                try
                {
                    var data = _udp.Receive(ref any);

                    if (MrProtocol.TryDecodePosition(data, data.Length, out var pos))
                    {
                        Clock.AddPositionSample(pos.SongTimeSec, pos.HostClockUs, pos.Playing);
                    }
                    else if (MrProtocol.TryDecodeNotes(data, data.Length, out _, scratch))
                    {
                        lock (_notesLock)
                        {
                            _heldNotes.Clear();
                            _heldNotes.AddRange(scratch);
                        }
                    }
                }
                catch (SocketException) { /* receive timeout: normal */ }
                catch (ObjectDisposedException) { return; }
                catch (Exception e)
                {
                    Debug.LogWarning($"[mr-link] stream error: {e.Message}");
                }
            }
        }

        static string ExtractString(string json, string key)
        {
            var marker = $"\"{key}\":\"";
            var start = json.IndexOf(marker, StringComparison.Ordinal);
            if (start < 0) return null;
            start += marker.Length;
            var end = json.IndexOf('"', start);
            return end < 0 ? null : json.Substring(start, end - start);
        }

        static double? ExtractNumber(string json, string key)
        {
            var marker = $"\"{key}\":";
            var start = json.IndexOf(marker, StringComparison.Ordinal);
            if (start < 0) return null;
            start += marker.Length;
            var end = start;
            while (end < json.Length && (char.IsDigit(json[end]) || json[end] is '.' or '-' or '+' or 'e' or 'E'))
            {
                end++;
            }
            return double.TryParse(json.Substring(start, end - start),
                                   System.Globalization.NumberStyles.Float,
                                   System.Globalization.CultureInfo.InvariantCulture,
                                   out var value)
                ? value
                : null;
        }

        public void Dispose() => Disconnect();
    }
}
