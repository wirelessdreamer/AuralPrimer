// Copyright 2026 Nathanael Anderson. Licensed under the Apache License 2.0.
//
// Scene-facing wrapper: finds the host, keeps the session up, and exposes the
// song position and held notes the renderer needs. Drop one of these in the
// scene and everything else can read from it.

using System.Collections.Generic;
using UnityEngine;

namespace AuralPrimer.Link
{
    [AddComponentMenu("AuralPrimer/MR Link")]
    public sealed class MrLinkBehaviour : MonoBehaviour
    {
        [Tooltip("Name this headset reports to the host during discovery.")]
        [SerializeField] string clientName = "Quest";

        [Tooltip("Start looking for a host as soon as the scene loads.")]
        [SerializeField] bool autoConnect = true;

        [Tooltip("Seconds between reconnection attempts after the link drops.")]
        [SerializeField] float reconnectDelaySeconds = 2f;

        MrDiscoveryClient _discovery;
        MrSessionClient _session;
        float _reconnectAt;

        readonly List<(byte pitch, byte velocity)> _heldNotes = new();

        /// <summary>Held notes as of this frame, ascending by pitch.</summary>
        public IReadOnlyList<(byte pitch, byte velocity)> HeldNotes => _heldNotes;

        /// <summary>Song position to render this frame, already compensated for
        /// clock offset, display latency and the host's audio offset.</summary>
        public double SongTimeSec { get; private set; }

        public bool IsConnected => _session is { IsConnected: true };
        public bool IsPlaying => _session?.Clock.Playing ?? false;
        public string HostName => _session?.HostName ?? "";

        /// <summary>The host is beaconing but we are not connected to it.</summary>
        public bool HostHeardButNotConnected =>
            !IsConnected && _discovery is { BeaconHeard: true };

        /// <summary>Host named by the last beacon heard, or "".</summary>
        public string LastBeaconHost => _discovery?.LastBeaconHost ?? "";

        /// <summary>Raised when the host delivers a chart (protocol §4).</summary>
        public event System.Action<string> ChartReceived;

        void Start()
        {
            _session = new MrSessionClient(clientName);
            if (autoConnect) BeginDiscovery();
        }

        public void BeginDiscovery()
        {
            _discovery?.Dispose();
            _discovery = new MrDiscoveryClient(clientName);
            _discovery.Start();
        }

        void Update()
        {
            // Hand a discovered host to the session. Discovery marshals results
            // through a queue precisely so this happens on the main thread.
            //
            // Poll whether or not discovery is still running. It stops itself
            // the moment a handshake succeeds — so gating the poll on IsRunning
            // discarded the single result the whole search existed to produce,
            // and the block below then started the search over. The host was
            // found every time and connected to never.
            if (_discovery != null && _discovery.Poll(out var host))
            {
                Debug.Log($"[mr-link] found {host}");
                _discovery.Stop();
                _session.Connect(host);
                // Let the session finish coming up before rediscovery is
                // considered, or it restarts on top of a connection in progress.
                _reconnectAt = Time.unscaledTime + reconnectDelaySeconds;
            }

            // Rediscover after a drop. The host beacons continuously, so this
            // needs no action from the user.
            if (_session is { IsConnected: false } && _discovery is not { IsRunning: true })
            {
                if (Time.unscaledTime >= _reconnectAt)
                {
                    _reconnectAt = Time.unscaledTime + reconnectDelaySeconds;
                    BeginDiscovery();
                }
            }

            if (_session != null)
            {
                _session.CopyHeldNotes(_heldNotes);

                while (_session.TryDequeueChart(out var chart))
                {
                    ChartReceived?.Invoke(chart);
                }

                // Render for when this frame's photons actually land, not for
                // now. This is the term that would otherwise have to become a
                // user-facing latency slider.
                SongTimeSec = _session.Clock.SongTimeForDisplay(PredictedDisplayTimeUs());
            }
        }

        /// <summary>
        /// Local monotonic microseconds at which the frame being rendered is
        /// expected to reach the eye.
        /// </summary>
        /// <remarks>
        /// Unity does not surface OpenXR's predicted display time directly in a
        /// portable way. Until it is wired through, this approximates it as
        /// "now plus one frame", which is the right shape and the right order of
        /// magnitude; the remaining error is a constant, not a per-user
        /// quantity, so it still costs the player nothing to configure. Phase 0
        /// spike 3 measures whether the residual is worth chasing.
        /// </remarks>
        ulong PredictedDisplayTimeUs()
        {
            var frameUs = Mathf.Max(Time.unscaledDeltaTime, 1f / 120f) * 1_000_000f;
            return _session.NowUs + (ulong)frameUs;
        }

        /// <summary>Ask the host to change transport state; it remains the authority.</summary>
        public void RequestTransport(string action, double? tSec = null)
            => _session?.SendTransport(action, tSec);

        void OnDestroy()
        {
            _discovery?.Dispose();
            _session?.Dispose();
        }

        void OnApplicationPause(bool paused)
        {
            // Horizon OS suspends aggressively when the headset is removed. Drop
            // the link rather than resuming onto a dead socket and a stale clock.
            if (paused)
            {
                _session?.Disconnect();
                _discovery?.Stop();
            }
            else if (autoConnect)
            {
                // Let Update's reconnect path do the actual work. Starting
                // discovery here as well means two clients and two sessions
                // racing: this fires once before Start on the first frame, and
                // again on every unpause — and on Quest an unpause is just the
                // headset going back on. Clearing the backoff resumes at once
                // without duplicating anything.
                _reconnectAt = 0f;
            }
        }
    }
}
