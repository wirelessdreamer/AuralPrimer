// Copyright 2026 Nathanael Anderson. Licensed under the Apache License 2.0.
//
// Finds the AuralPrimer host on the LAN: listens for its multicast beacon, then
// completes the unicast handshake. Mirrors the Rust host's discovery.rs; see
// docs/mr-link-protocol.md §1.

using System;
using System.Collections.Concurrent;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using UnityEngine;

namespace AuralPrimer.Link
{
    /// <summary>Discovered host, ready to connect to.</summary>
    public readonly struct HostEndpoint
    {
        public readonly string Address;
        public readonly int SessionPort;
        public readonly string HostName;

        public HostEndpoint(string address, int sessionPort, string hostName)
        {
            Address = address;
            SessionPort = sessionPort;
            HostName = hostName;
        }

        public override string ToString() => $"{HostName} ({Address}:{SessionPort})";
    }

    /// <summary>
    /// Multicast discovery. Runs its sockets on background threads and hands
    /// results back through a queue that <see cref="Poll"/> drains on the main
    /// thread — Unity APIs are not thread-safe, and a callback fired from a
    /// socket thread is a crash waiting for a busy frame.
    /// </summary>
    public sealed class MrDiscoveryClient : IDisposable
    {
        readonly ConcurrentQueue<HostEndpoint> _found = new();
        readonly string _clientName;

        UdpClient _listener;
        UdpClient _unicast;
        Thread _listenThread;
        volatile bool _running;
        volatile bool _handshakeInFlight;

#if UNITY_ANDROID && !UNITY_EDITOR
        AndroidJavaObject _multicastLock;
#endif

        public MrDiscoveryClient(string clientName)
        {
            _clientName = string.IsNullOrWhiteSpace(clientName) ? "Quest" : clientName;
        }

        public bool IsRunning => _running;

        public void Start()
        {
            if (_running) return;
            _running = true;

            AcquireMulticastLock();

            try
            {
                _listener = new UdpClient();
                _listener.Client.SetSocketOption(SocketOptionLevel.Socket,
                                                 SocketOptionName.ReuseAddress, true);
                _listener.Client.Bind(new IPEndPoint(IPAddress.Any, MrProtocol.MulticastPort));
                _listener.JoinMulticastGroup(IPAddress.Parse(MrProtocol.MulticastGroup));
                _listener.Client.ReceiveTimeout = 500;

                // The CONNECT/ACK exchange uses its own ephemeral socket, NOT the
                // one bound to the group port. With two sockets sharing 47761 on
                // one machine the unicast ack is delivered to whichever is more
                // specifically bound — the host's own — and discovery hangs with
                // no error anywhere. That is exactly the shape of running this in
                // the Editor on the host PC.
                _unicast = new UdpClient(new IPEndPoint(IPAddress.Any, 0));
                _unicast.Client.ReceiveTimeout = 500;

                _listenThread = new Thread(ListenLoop) { IsBackground = true, Name = "mr-discovery" };
                _listenThread.Start();

                Debug.Log($"[mr-link] discovery listening on {MrProtocol.MulticastGroup}:{MrProtocol.MulticastPort}");
            }
            catch (Exception e)
            {
                Debug.LogError($"[mr-link] discovery failed to start: {e.Message}");
                Stop();
            }
        }

        public void Stop()
        {
            _running = false;
            try { _listener?.Close(); } catch { /* closing a closed socket is fine */ }
            try { _unicast?.Close(); } catch { }
            _listener = null;
            _unicast = null;
            ReleaseMulticastLock();
        }

        /// <summary>Drain discovered hosts. Call from Update().</summary>
        public bool Poll(out HostEndpoint host) => _found.TryDequeue(out host);

        void ListenLoop()
        {
            var any = new IPEndPoint(IPAddress.Any, 0);

            while (_running)
            {
                try
                {
                    var data = _listener.Receive(ref any);
                    var text = Encoding.UTF8.GetString(data);

                    if (!MrProtocol.TryParseBeacon(text, out var beacon)) continue;
                    if (_handshakeInFlight) continue;

                    _handshakeInFlight = true;
                    // Ask on the beacon's source address rather than the address
                    // it advertised: if the host is multi-homed those can differ,
                    // and the one that actually reached us is the one that works.
                    if (TryHandshake(any.Address, out var sessionPort))
                    {
                        _found.Enqueue(new HostEndpoint(any.Address.ToString(), sessionPort, beacon.HostName));
                        _running = false; // one host is enough; the session takes over
                    }
                    _handshakeInFlight = false;
                }
                catch (SocketException)
                {
                    // Receive timeout: normal, and how the loop notices Stop().
                }
                catch (ObjectDisposedException)
                {
                    return; // socket closed under us during Stop()
                }
                catch (Exception e)
                {
                    Debug.LogWarning($"[mr-link] discovery listen error: {e.Message}");
                }
            }
        }

        bool TryHandshake(IPAddress hostAddress, out int sessionPort)
        {
            sessionPort = 0;
            try
            {
                var request = MrProtocol.EncodeConnect(_clientName);
                var target = new IPEndPoint(hostAddress, MrProtocol.MulticastPort);
                _unicast.Send(request, request.Length, target);

                // A couple of attempts: UDP may drop, and a beacon arrives every
                // second anyway so an outright failure costs little.
                var deadline = DateTime.UtcNow.AddSeconds(2);
                var from = new IPEndPoint(IPAddress.Any, 0);
                while (DateTime.UtcNow < deadline)
                {
                    try
                    {
                        var reply = _unicast.Receive(ref from);
                        if (MrProtocol.TryParseAck(Encoding.UTF8.GetString(reply), out sessionPort))
                        {
                            return true;
                        }
                    }
                    catch (SocketException) { /* timeout; keep waiting until the deadline */ }
                }
            }
            catch (Exception e)
            {
                Debug.LogWarning($"[mr-link] handshake failed: {e.Message}");
            }
            return false;
        }

        void AcquireMulticastLock()
        {
#if UNITY_ANDROID && !UNITY_EDITOR
            // Without this Android silently discards multicast, and the symptom
            // is indistinguishable from a host that is not running. It is the
            // single most common reason LAN discovery "doesn't work" on Quest.
            try
            {
                using var player = new AndroidJavaClass("com.unity3d.player.UnityPlayer");
                using var activity = player.GetStatic<AndroidJavaObject>("currentActivity");
                using var wifi = activity.Call<AndroidJavaObject>("getSystemService", "wifi");
                _multicastLock = wifi.Call<AndroidJavaObject>("createMulticastLock", "auralprimer-mr");
                _multicastLock.Call("setReferenceCounted", false);
                _multicastLock.Call("acquire");
                Debug.Log("[mr-link] multicast lock acquired");
            }
            catch (Exception e)
            {
                Debug.LogError($"[mr-link] could not acquire multicast lock: {e.Message}");
            }
#endif
        }

        void ReleaseMulticastLock()
        {
#if UNITY_ANDROID && !UNITY_EDITOR
            try
            {
                if (_multicastLock != null)
                {
                    _multicastLock.Call("release");
                    _multicastLock.Dispose();
                    _multicastLock = null;
                }
            }
            catch (Exception e)
            {
                Debug.LogWarning($"[mr-link] releasing multicast lock: {e.Message}");
            }
#endif
        }

        public void Dispose() => Stop();
    }
}
