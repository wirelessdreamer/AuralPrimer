// Copyright 2026 Nathanael Anderson. Licensed under the Apache License 2.0.
//
// Browsing the host's song library from the headset.
//
// The library lives on the desktop, so every question here is a round trip: the
// headset asks, the host filters and answers, and nothing is cached beyond the
// page currently on screen. That is deliberate. Filtering locally would mean a
// second copy of the matching rules — free to disagree with the desktop's about
// what "matches" means — and holding the whole library in the headset to do it
// buys nothing, because the only thing that can actually load a song is the
// host anyway.
//
// See `docs/mr-link-protocol.md` §6 for the wire contract.

using System;
using System.Collections.Generic;
using AuralPrimer.Link;
using AuralPrimer.UI;
using UnityEngine;

namespace AuralPrimer.Calibration
{
    public sealed partial class SetupWizard
    {
        /// <summary>The page currently on screen, or null before the first reply.</summary>
        LibraryPage _library;

        string _songSearch = "";
        string _songArtist;
        string _songGenre;
        int _songsPage;

        /// <summary>Which facet the filter step is picking a value for.</summary>
        bool _filteringGenre;

        /// <summary>What the last voice search did, shown under the title.</summary>
        string _voiceStatus = "";

        TouchScreenKeyboard _keyboard;
        AudioClip _recording;
        string _microphone;
        float _recordingStartedAt;

        /// <summary>
        /// Stop listening after this long even if the user never says stop.
        /// </summary>
        /// <remarks>
        /// The protocol caps a query at ten seconds. Cutting it here rather than
        /// letting the clip run means the headset never builds a payload the host
        /// is entitled to reject.
        /// </remarks>
        const float ListenSeconds = VoiceWav.MaxSeconds;

        bool _wasLinked;

        /// <summary>
        /// Tell the host which notes this instrument can actually play.
        /// </summary>
        /// <remarks>
        /// Without this the two ends disagree about what is playable and the
        /// song stops dead: the headset draws only what fits and drops the rest,
        /// while the host's wait mode holds the song open for every note in the
        /// chart -- including ones it was never shown and the keyboard cannot
        /// produce. Invisible and unplayable at once, with no way forward.
        ///
        /// Sent on connect and on every recalibration, because re-marking the
        /// ends of the keyboard changes the answer.
        /// </remarks>
        void PublishKeyboardLayout()
        {
            if (link == null || !link.IsConnected) return;
            if (_profile is not { IsCalibrated: true }) return;

            link.SendKeyboardLayout(
                _profile.lowestPitch,
                _profile.highestPitch,
                _profile.ignoreOutOfRangeNotes);
        }

        /// <summary>Resend the layout when a link comes up.</summary>
        /// <remarks>
        /// A rising edge rather than every frame: this is standing state the
        /// host keeps, and a reconnect is the only thing that loses it.
        /// </remarks>
        void PumpKeyboardLayout()
        {
            var linked = link != null && link.IsConnected;
            if (linked && !_wasLinked) PublishKeyboardLayout();
            _wasLinked = linked;
        }

        void HookLibrary()
        {
            if (link == null) return;
            link.LibraryReceived += OnLibraryReceived;
            link.VoiceHeard += OnVoiceHeard;
        }

        void UnhookLibrary()
        {
            if (link == null) return;
            link.LibraryReceived -= OnLibraryReceived;
            link.VoiceHeard -= OnVoiceHeard;
        }

        void OnLibraryReceived(LibraryPage page)
        {
            _library = page;
            // The host clamps the page to what actually exists, so take its word
            // for which page we are on rather than keeping our own guess: after
            // a search narrows the results, ours is usually past the end.
            _songsPage = page.page;
            if (_step == Step.Songs || _step == Step.SongFilter) RenderStep();
        }

        void RequestSongs()
        {
            if (link == null || !link.CanBrowseLibrary) return;

            link.RequestLibrary(new LibraryQuery
            {
                Search = _songSearch,
                Artist = _songArtist,
                Genre = _songGenre,
                Page = _songsPage,
                PageSize = WizardPanel.ListRowsPerPage,
            });
        }

        // --- The song list ---------------------------------------------------

        void RenderSongs()
        {
            if (link == null || !link.CanBrowseLibrary)
            {
                // Said so in WELCOME. Better to explain than to offer a menu
                // that can only ever spin.
                panel?.SetList();
                panel?.SetBody(link != null && link.IsConnected
                    ? "This host is too old to browse its library from here.\n\n"
                      + "Update the desktop app and reconnect."
                    : "Not connected to a host, so there is no library to browse.");
                panel?.SetButtons(("Back", () => EnterStep(Step.Menu)));
                return;
            }

            if (_library == null)
            {
                panel?.SetList();
                panel?.SetBody("Asking the host for its library…");
                panel?.SetButtons(("Back", () => EnterStep(Step.Menu)));
                return;
            }

            panel?.SetStatus(SongsStatus(), new Color(0.208f, 0.941f, 1f));

            if (_library.items.Length == 0)
            {
                panel?.SetList();
                panel?.SetBody(AnyFilterActive
                    ? "Nothing matches that.\n\nClear the filters to see everything."
                    : "The host's library is empty.\n\nImport a song on the desktop "
                      + "and it will appear here.");
            }
            else
            {
                var rows = new List<(string, string, Action)>(_library.items.Length);
                foreach (var song in _library.items)
                {
                    var chosen = song;
                    // Artist on the right, because it is what tells two similar
                    // titles apart; length only when the title leaves room for
                    // nothing else to say.
                    var detail = !string.IsNullOrWhiteSpace(chosen.artist)
                        ? chosen.artist
                        : chosen.Length;
                    rows.Add((chosen.title, detail, () => ChooseSong(chosen)));
                }
                panel?.SetList(rows.ToArray());
            }

            panel?.SetButtons(SongButtons());
        }

        (string, Action)[] SongButtons()
        {
            var buttons = new List<(string, Action)>(5)
            {
                (string.IsNullOrEmpty(_songSearch) ? "Search" : "Search*", OpenSearchKeyboard),
            };

            if (link != null && link.CanSearchByVoice)
            {
                buttons.Add((_listening ? "Listening…" : "Speak", ToggleListening));
            }

            // Only offer a facet the library actually has values for. A chip
            // that opens an empty list is worse than no chip.
            if (_library != null && _library.artists.Length > 0)
            {
                buttons.Add((_songArtist ?? "Artist", () => OpenFilter(false)));
            }
            if (_library != null && _library.genres.Length > 0)
            {
                buttons.Add((_songGenre ?? "Genre", () => OpenFilter(true)));
            }

            // Paging shares the row with everything else, so it only appears
            // when there is somewhere to go. A dead "More" is worse than none.
            if (_library != null && _library.PageCount > 1)
            {
                buttons.Add(("More", NextSongPage));
            }

            buttons.Add((AnyFilterActive ? "Clear" : "Back", BackFromSongs));
            return buttons.ToArray();
        }

        bool AnyFilterActive =>
            !string.IsNullOrEmpty(_songSearch)
            || !string.IsNullOrEmpty(_songArtist)
            || !string.IsNullOrEmpty(_songGenre);

        string SongsStatus()
        {
            if (!string.IsNullOrEmpty(_voiceStatus)) return _voiceStatus;
            if (_library == null) return "";

            var where = new List<string>(3);
            if (!string.IsNullOrEmpty(_songSearch)) where.Add($"“{_songSearch}”");
            if (!string.IsNullOrEmpty(_songArtist)) where.Add(_songArtist);
            if (!string.IsNullOrEmpty(_songGenre)) where.Add(_songGenre);

            var scope = where.Count > 0 ? string.Join(" · ", where) : "all songs";
            var pages = _library.PageCount > 1
                ? $" · page {_library.page + 1} of {_library.PageCount}"
                : "";
            return $"{_library.total} · {scope}{pages}";
        }

        void NextSongPage()
        {
            if (_library == null) return;
            // Wraps, like the recordings list: with one button for movement,
            // running off the end and stopping leaves no way back to the start.
            _songsPage = (_library.page + 1) % Mathf.Max(1, _library.PageCount);
            RequestSongs();
        }

        void BackFromSongs()
        {
            if (AnyFilterActive)
            {
                // One button, two jobs: while anything is narrowed, the way out
                // the user wants is almost always "show me everything again".
                ClearSongFilters();
                return;
            }
            EnterStep(Step.Menu);
        }

        void ClearSongFilters()
        {
            _songSearch = "";
            _songArtist = null;
            _songGenre = null;
            _songsPage = 0;
            _voiceStatus = "";
            RequestSongs();
        }

        void ChooseSong(LibrarySong song)
        {
            if (song == null || link == null) return;

            link.SelectSong(song.songId);
            Debug.Log($"[wizard] asked host to load {song.title} ({song.songId})");

            // Straight back to the menu. There is no acknowledgement to wait for
            // — the chart arriving is what says it worked — and sitting on the
            // list would leave the user staring at a screen that never changes.
            _voiceStatus = "";
            EnterStep(Step.Menu);
        }

        // --- Filters ---------------------------------------------------------

        void OpenFilter(bool genre)
        {
            _filteringGenre = genre;
            EnterStep(Step.SongFilter);
        }

        void RenderSongFilter()
        {
            var values = _filteringGenre ? _library?.genres : _library?.artists;
            if (values == null || values.Length == 0)
            {
                panel?.SetList();
                panel?.SetBody("Nothing to filter by yet.");
                panel?.SetButtons(("Back", () => EnterStep(Step.Songs)));
                return;
            }

            var active = _filteringGenre ? _songGenre : _songArtist;
            var rows = new List<(string, string, Action)>(values.Length + 1)
            {
                // "Any" first and always present, so clearing a filter is one
                // press from the same list that set it.
                ("Any", active == null ? "current" : "", () => ApplyFilter(null)),
            };

            var start = Mathf.Clamp(_filterPage * (WizardPanel.ListRowsPerPage - 1), 0, values.Length);
            var end = Mathf.Min(start + WizardPanel.ListRowsPerPage - 1, values.Length);
            for (var i = start; i < end; i++)
            {
                var value = values[i];
                rows.Add((value, string.Equals(value, active, StringComparison.OrdinalIgnoreCase)
                    ? "current" : "", () => ApplyFilter(value)));
            }

            panel?.SetList(rows.ToArray());

            // One row of the page is spent on "Any", so the rest page in blocks
            // one smaller than the list holds.
            var perPage = Mathf.Max(1, WizardPanel.ListRowsPerPage - 1);
            var pages = Mathf.Max(1, (values.Length + perPage - 1) / perPage);
            panel?.SetStatus($"{values.Length} {(_filteringGenre ? "genres" : "artists")}"
                             + (pages > 1 ? $" · page {_filterPage + 1} of {pages}" : ""),
                             new Color(0.208f, 0.941f, 1f));

            if (pages > 1)
            {
                panel?.SetButtons(
                    ("More", () => { _filterPage = (_filterPage + 1) % pages; RenderStep(); }),
                    ("Back", () => EnterStep(Step.Songs)));
            }
            else
            {
                panel?.SetButtons(("Back", () => EnterStep(Step.Songs)));
            }
        }

        int _filterPage;

        void ApplyFilter(string value)
        {
            if (_filteringGenre) _songGenre = value; else _songArtist = value;
            // Back to the first page: page four of the old result set says
            // nothing about the new one.
            _songsPage = 0;
            _filterPage = 0;
            EnterStep(Step.Songs);
            RequestSongs();
        }

        // --- Typing ----------------------------------------------------------

        void OpenSearchKeyboard()
        {
            // The system keyboard, not one drawn here. Quest's is already the
            // one the user knows, already handles hand tracking and controllers,
            // and already appears where they are looking.
            _keyboard = TouchScreenKeyboard.Open(
                _songSearch ?? "",
                TouchScreenKeyboardType.Search,
                autocorrection: false,
                multiline: false,
                secure: false,
                alert: false,
                textPlaceholder: "Search songs");

            if (_keyboard == null)
            {
                // No system keyboard in the Editor. Voice is the other way in,
                // and saying so beats a button that appears to do nothing.
                _voiceStatus = "No keyboard here — use Speak, or search on the desktop.";
                RenderStep();
            }
        }

        /// <summary>Poll the system keyboard; called from the wizard's Update.</summary>
        /// <remarks>
        /// Polled rather than evented because that is the only interface
        /// <see cref="TouchScreenKeyboard"/> offers — it has no completion
        /// callback on any platform.
        /// </remarks>
        void PumpKeyboard()
        {
            if (_keyboard == null) return;

            switch (_keyboard.status)
            {
                case TouchScreenKeyboard.Status.Done:
                    _songSearch = _keyboard.text ?? "";
                    _keyboard = null;
                    _songsPage = 0;
                    _voiceStatus = "";
                    RequestSongs();
                    break;

                case TouchScreenKeyboard.Status.Canceled:
                case TouchScreenKeyboard.Status.LostFocus:
                    // Leave the query alone: a cancelled keyboard means "never
                    // mind", not "clear what I had".
                    _keyboard = null;
                    break;
            }
        }

        // --- Speaking --------------------------------------------------------

        bool _listening;

        void ToggleListening()
        {
            if (_listening) StopListening(send: true);
            else StartListening();
        }

        void StartListening()
        {
            if (link == null || !link.CanSearchByVoice) return;

#if UNITY_ANDROID && !UNITY_EDITOR
            if (!UnityEngine.Android.Permission.HasUserAuthorizedPermission(
                    UnityEngine.Android.Permission.Microphone))
            {
                // Asked at the moment it means something, rather than at launch
                // where the user has no idea what it is for. Android answers
                // asynchronously and this press is lost, so say what to do next
                // rather than appearing to record nothing.
                UnityEngine.Android.Permission.RequestUserPermission(
                    UnityEngine.Android.Permission.Microphone);
                _voiceStatus = "Allow microphone access, then press Speak again.";
                RenderStep();
                return;
            }
#endif

            if (Microphone.devices.Length == 0)
            {
                _voiceStatus = "No microphone available.";
                RenderStep();
                return;
            }

            _microphone = Microphone.devices[0];
            // Recorded at whatever rate the device likes and resampled on the
            // way out. Asking the mic for 16 kHz directly is not portable --
            // some devices quietly ignore it and hand back their native rate,
            // which would then be mislabelled in the WAV header.
            _recording = Microphone.Start(_microphone, false, Mathf.CeilToInt(ListenSeconds), 44100);
            if (_recording == null)
            {
                _voiceStatus = "Could not open the microphone.";
                RenderStep();
                return;
            }

            _listening = true;
            _recordingStartedAt = Time.unscaledTime;
            _voiceStatus = "Listening — press again when you are done.";
            RenderStep();
        }

        void StopListening(bool send)
        {
            if (!_listening) return;
            _listening = false;

            var recorded = Microphone.GetPosition(_microphone);
            Microphone.End(_microphone);

            var clip = _recording;
            _recording = null;
            if (!send || clip == null || recorded <= 0)
            {
                _voiceStatus = "";
                RenderStep();
                return;
            }

            // Only the part actually spoken. The clip is allocated for the full
            // ten seconds, and sending the untouched tail would ship seconds of
            // silence for the recogniser to chew on.
            var samples = new float[recorded * clip.channels];
            clip.GetData(samples, 0);

            var wav = VoiceWav.Encode(Mono(samples, clip.channels), clip.frequency);
            if (wav.Length == 0)
            {
                _voiceStatus = "Nothing was recorded.";
                RenderStep();
                return;
            }

            _voiceStatus = "Sending to the host…";
            RenderStep();
            link.SendVoiceQuery(wav);
        }

        /// <summary>Average any extra channels down to one.</summary>
        static float[] Mono(float[] interleaved, int channels)
        {
            if (channels <= 1) return interleaved;

            var frames = interleaved.Length / channels;
            var mono = new float[frames];
            for (var i = 0; i < frames; i++)
            {
                var sum = 0f;
                for (var c = 0; c < channels; c++) sum += interleaved[i * channels + c];
                mono[i] = sum / channels;
            }
            return mono;
        }

        void OnVoiceHeard(VoiceResult heard)
        {
            if (heard == null) return;

            if (!string.IsNullOrEmpty(heard.error))
            {
                // The typed query is left alone deliberately: losing what was
                // already there because the microphone failed would be a second
                // failure on top of the first.
                _voiceStatus = heard.error;
                RenderStep();
                return;
            }

            if (string.IsNullOrWhiteSpace(heard.text))
            {
                _voiceStatus = "Did not catch that.";
                RenderStep();
                return;
            }

            _songSearch = heard.text.Trim();
            _songsPage = 0;
            _voiceStatus = $"Heard “{_songSearch}”";
            RequestSongs();
        }

        /// <summary>Give up on a recording that ran to the cap.</summary>
        void PumpListening()
        {
            if (!_listening) return;
            if (Time.unscaledTime - _recordingStartedAt < ListenSeconds) return;
            StopListening(send: true);
        }
    }
}
