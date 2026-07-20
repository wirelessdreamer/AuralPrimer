use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::io::{BufRead, BufReader};
use std::process::{Command, Stdio};
use std::sync::mpsc;
use std::thread;
use std::time::Duration;
use tauri::{AppHandle, Emitter};
use tauri_plugin_shell::process::CommandEvent;
use tauri_plugin_shell::ShellExt;

const INGEST_SIDECAR_NAME: &str = "aural_ingest";

/// Default max wall-clock runtime for a single ingest import before the driver
/// kills the child. Imports are normally minutes; the GPU path is fast and even
/// the CPU fallback should finish well under this. The deadline exists to catch
/// a wedged / runaway process (a prior bug let one CPU-bound import hang ~3
/// hours). Override with `AURALPRIMER_INGEST_TIMEOUT_SEC`.
const DEFAULT_INGEST_TIMEOUT_SEC: u64 = 30 * 60;

/// Environment variable that overrides [`DEFAULT_INGEST_TIMEOUT_SEC`]. Set to
/// `0` to disable the deadline entirely (treated as "no limit").
const INGEST_TIMEOUT_ENV: &str = "AURALPRIMER_INGEST_TIMEOUT_SEC";

/// Resolve the configured max-runtime deadline.
///
/// Returns `None` when the deadline is disabled (env var set to `0`), otherwise
/// `Some(duration)`. Invalid / empty overrides fall back to the default. Kept
/// pure (takes the env value as an argument) so it is unit-testable without a
/// real environment or child process.
fn resolve_max_runtime(env_override: Option<&str>) -> Option<Duration> {
    let secs = match env_override.map(str::trim) {
        Some(raw) if !raw.is_empty() => raw.parse::<u64>().unwrap_or(DEFAULT_INGEST_TIMEOUT_SEC),
        _ => DEFAULT_INGEST_TIMEOUT_SEC,
    };
    if secs == 0 {
        None
    } else {
        Some(Duration::from_secs(secs))
    }
}

/// Read the configured deadline from the process environment.
fn configured_max_runtime() -> Option<Duration> {
    resolve_max_runtime(std::env::var(INGEST_TIMEOUT_ENV).ok().as_deref())
}

/// Clear, actionable error surfaced when an import blows past the deadline.
fn timeout_error_message(limit: Duration) -> String {
    let minutes = limit.as_secs() / 60;
    format!(
        "ingest import exceeded {minutes} min runtime limit and was terminated -- \
         likely a CPU fallback or a wedged process. Increase or disable the limit \
         via {INGEST_TIMEOUT_ENV} (seconds; 0 disables) if this import legitimately \
         needs longer."
    )
}

#[derive(Debug, Serialize, Deserialize, Clone, Copy, PartialEq, Eq, Default)]
#[serde(rename_all = "kebab-case")]
pub enum IngestSubcommand {
    #[default]
    Import,
    ImportDir,
}

impl IngestSubcommand {
    pub fn as_cli_arg(self) -> &'static str {
        match self {
            IngestSubcommand::Import => "import",
            IngestSubcommand::ImportDir => "import-dir",
        }
    }
}

#[derive(Debug, Serialize, Deserialize, Clone, Default)]
pub struct IngestImportRequest {
    pub source_path: String,

    #[serde(default)]
    pub out_auralsong_path: Option<String>,

    #[serde(default)]
    pub subcommand: Option<IngestSubcommand>,

    #[serde(default)]
    pub profile: Option<String>,

    #[serde(default)]
    pub config: Option<String>,

    #[serde(default)]
    pub title: Option<String>,

    #[serde(default)]
    pub artist: Option<String>,

    #[serde(default)]
    pub duration_sec: Option<f64>,

    #[serde(default)]
    pub drum_filter: Option<String>,

    #[serde(default)]
    pub melodic_method: Option<String>,

    #[serde(default)]
    pub shifts: Option<i32>,

    #[serde(default)]
    pub multi_filter: Option<bool>,

    #[serde(default)]
    pub ingest_binary_path: Option<String>,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct IngestImportResult {
    pub ok: bool,
    pub exit_code: i32,
    pub command: Vec<String>,
    pub stdout: String,
    pub stderr: String,
    /// AuralSong-relative paths of any source MIDI files copied into
    /// `features/midi/` after the sidecar finished. Populated by
    /// `ingest_import` (Tauri command in lib.rs) when the source is a
    /// folder that contains user-supplied gameplay MIDI -- chiefly Suno
    /// exports -- so the Refine workspace can render them as a guide
    /// layer. Empty for single-file imports and folders without MIDI.
    #[serde(default)]
    pub preserved_reference_midis: Vec<String>,
}

#[derive(Debug, Serialize, Deserialize, Clone, Default)]
pub struct IngestRuntimeCheckRequest {
    #[serde(default)]
    pub ingest_binary_path: Option<String>,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct IngestRuntimeCheckResult {
    pub ok: bool,
    pub exit_code: i32,
    pub command: Vec<String>,
    pub stdout: String,
    pub stderr: String,
    pub payload: Option<serde_json::Value>,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct IngestProgressEvent {
    pub stream: String,
    pub line: String,
    pub parsed: Option<serde_json::Value>,
}

fn non_empty_opt(s: &Option<String>) -> Option<String> {
    s.as_ref()
        .map(|x| x.trim().to_string())
        .filter(|x| !x.is_empty())
}

fn emit_progress(app: Option<&AppHandle>, ev: IngestProgressEvent) {
    if let Some(app) = app {
        let _ = app.emit("ingest_import_progress", ev);
    }
}

fn explicit_binary(req: &IngestImportRequest) -> Option<String> {
    non_empty_opt(&req.ingest_binary_path)
}

fn runtime_fallback_sidecar_binary() -> Option<String> {
    let exe_dir = std::env::current_exe().ok()?.parent()?.to_path_buf();
    let sidecar_leaf = if cfg!(target_os = "windows") {
        "aural_ingest.exe"
    } else {
        "aural_ingest"
    };

    for candidate in [
        exe_dir.join(sidecar_leaf),
        exe_dir.join("sidecar").join(sidecar_leaf),
    ] {
        if candidate.is_file() {
            return Some(candidate.to_string_lossy().into_owned());
        }
    }

    None
}

fn run_runtime_fallback_with_progress(
    args: &[String],
    app: Option<&AppHandle>,
    reason: &str,
) -> Option<Result<IngestImportResult, String>> {
    let binary = runtime_fallback_sidecar_binary()?;
    Some(
        run_explicit_binary_with_progress(&binary, args, app).map_err(|fallback_error| {
            format!("{reason}; fallback binary {binary} also failed: {fallback_error}")
        }),
    )
}

fn run_runtime_fallback_capture(
    args: &[String],
    reason: &str,
) -> Option<Result<IngestRuntimeCheckResult, String>> {
    let binary = runtime_fallback_sidecar_binary()?;
    Some(
        run_explicit_binary_capture(&binary, args).map_err(|fallback_error| {
            format!("{reason}; fallback binary {binary} also failed: {fallback_error}")
        }),
    )
}

pub fn build_ingest_args(req: &IngestImportRequest) -> Result<Vec<String>, String> {
    let source_path = req.source_path.trim();
    if source_path.is_empty() {
        return Err("missing source_path".to_string());
    }

    let out_auralsong_path = non_empty_opt(&req.out_auralsong_path)
        .ok_or_else(|| "missing out_auralsong_path".to_string())?;

    let subcommand = req.subcommand.unwrap_or_default().as_cli_arg().to_string();
    let profile = req
        .profile
        .as_ref()
        .map(|x| x.trim())
        .filter(|x| !x.is_empty())
        .unwrap_or("full")
        .to_string();

    let mut args: Vec<String> = vec![
        subcommand,
        source_path.to_string(),
        "--out".to_string(),
        out_auralsong_path,
        "--profile".to_string(),
        profile,
    ];

    if let Some(config) = non_empty_opt(&req.config) {
        args.push("--config".to_string());
        args.push(config);
    }
    if let Some(title) = non_empty_opt(&req.title) {
        args.push("--title".to_string());
        args.push(title);
    }
    if let Some(artist) = non_empty_opt(&req.artist) {
        args.push("--artist".to_string());
        args.push(artist);
    }
    if let Some(duration_sec) = req.duration_sec {
        args.push("--duration-sec".to_string());
        args.push(duration_sec.to_string());
    }
    if let Some(drum_filter) = non_empty_opt(&req.drum_filter) {
        args.push("--drum-filter".to_string());
        args.push(drum_filter);
    }
    if let Some(melodic_method) = non_empty_opt(&req.melodic_method) {
        args.push("--melodic-method".to_string());
        args.push(melodic_method);
    }
    if let Some(shifts) = req.shifts {
        args.push("--shifts".to_string());
        args.push(shifts.to_string());
    }
    if req.multi_filter.unwrap_or(false) {
        args.push("--multi-filter".to_string());
    }

    Ok(args)
}

#[derive(Clone, Copy)]
enum StreamKind {
    Stdout,
    Stderr,
}

fn run_explicit_binary_with_progress(
    binary: &str,
    args: &[String],
    app: Option<&AppHandle>,
) -> Result<IngestImportResult, String> {
    let mut child = Command::new(binary)
        .args(args)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("failed to start {binary}: {e}"))?;

    let (tx, rx) = mpsc::channel::<(StreamKind, String)>();

    if let Some(stdout) = child.stdout.take() {
        let tx_out = tx.clone();
        thread::spawn(move || {
            let reader = BufReader::new(stdout);
            for line in reader.lines().map_while(Result::ok) {
                let _ = tx_out.send((StreamKind::Stdout, line));
            }
        });
    }
    if let Some(stderr) = child.stderr.take() {
        let tx_err = tx.clone();
        thread::spawn(move || {
            let reader = BufReader::new(stderr);
            for line in reader.lines().map_while(Result::ok) {
                let _ = tx_err.send((StreamKind::Stderr, line));
            }
        });
    }
    drop(tx);

    let mut stdout_lines: Vec<String> = vec![];
    let mut stderr_lines: Vec<String> = vec![];

    // Bound the read loop with the configured max-runtime deadline so a wedged
    // child cannot hang the import forever. `recv_timeout` returns once the
    // remaining budget elapses; on that path we kill the child and surface a
    // clear error rather than blocking on `child.wait()`.
    let max_runtime = configured_max_runtime();
    let deadline = max_runtime.map(|d| std::time::Instant::now() + d);

    loop {
        let recv_result = match deadline {
            Some(deadline) => {
                let remaining = deadline.saturating_duration_since(std::time::Instant::now());
                rx.recv_timeout(remaining)
                    .map_err(|e| matches!(e, mpsc::RecvTimeoutError::Timeout))
            }
            None => rx.recv().map_err(|_| false),
        };

        match recv_result {
            Ok((kind, line)) => match kind {
                StreamKind::Stdout => {
                    let parsed = serde_json::from_str::<serde_json::Value>(&line).ok();
                    emit_progress(
                        app,
                        IngestProgressEvent {
                            stream: "stdout".to_string(),
                            line: line.clone(),
                            parsed,
                        },
                    );
                    stdout_lines.push(line);
                }
                StreamKind::Stderr => {
                    emit_progress(
                        app,
                        IngestProgressEvent {
                            stream: "stderr".to_string(),
                            line: line.clone(),
                            parsed: None,
                        },
                    );
                    stderr_lines.push(line);
                }
            },
            // `Err(true)` == deadline hit; `Err(false)` == channel closed (both
            // reader threads finished, i.e. the child's pipes are at EOF).
            Err(true) => {
                let _ = child.kill();
                let _ = child.wait();
                return Err(timeout_error_message(
                    max_runtime.unwrap_or(Duration::from_secs(DEFAULT_INGEST_TIMEOUT_SEC)),
                ));
            }
            Err(false) => break,
        }
    }

    let status = child
        .wait()
        .map_err(|e| format!("failed waiting for {binary}: {e}"))?;
    let exit_code = status.code().unwrap_or(-1);

    Ok(IngestImportResult {
        ok: status.success(),
        exit_code,
        command: {
            let mut command = vec![binary.to_string()];
            command.extend(args.to_vec());
            command
        },
        stdout: stdout_lines.join("\n"),
        stderr: stderr_lines.join("\n"),
        // Populated downstream by the Tauri command after the sidecar
        // succeeds and the source folder has been scanned for MIDI.
        preserved_reference_midis: Vec::new(),
    })
}

fn run_tauri_sidecar_with_progress(
    app: &AppHandle,
    args: &[String],
) -> Result<IngestImportResult, String> {
    let command = match app.shell().sidecar(INGEST_SIDECAR_NAME) {
        Ok(command) => command.args(args.to_vec()),
        Err(error) => {
            let reason = format!("failed to resolve Tauri sidecar {INGEST_SIDECAR_NAME}: {error}");
            if let Some(result) = run_runtime_fallback_with_progress(args, Some(app), &reason) {
                return result;
            }
            return Err(reason);
        }
    };

    let (mut rx, child) = match command.spawn() {
        Ok(child) => child,
        Err(error) => {
            let reason = format!("failed to spawn Tauri sidecar {INGEST_SIDECAR_NAME}: {error}");
            if let Some(result) = run_runtime_fallback_with_progress(args, Some(app), &reason) {
                return result;
            }
            return Err(reason);
        }
    };

    let mut stdout_lines: Vec<String> = vec![];
    let mut stderr_lines: Vec<String> = vec![];
    let mut exit_code = -1;

    // Bound the event loop with the configured max-runtime deadline. Without
    // this, the loop blocks on `rx.recv()` until the child self-terminates --
    // a wedged / CPU-bound import then hangs unbounded. tauri's async runtime
    // is a tokio runtime with the time driver enabled, so `tokio::time::timeout`
    // works inside `block_on`. `Some(child)` is taken (consumed by `kill`) on
    // the timeout path.
    let max_runtime = configured_max_runtime();
    let mut child = Some(child);

    let timed_out = tauri::async_runtime::block_on(async {
        loop {
            let event = match max_runtime {
                Some(limit) => match tokio::time::timeout(limit, rx.recv()).await {
                    Ok(event) => event,
                    Err(_) => return true, // deadline elapsed
                },
                None => rx.recv().await,
            };

            let Some(event) = event else {
                return false; // channel closed: child finished on its own
            };

            match event {
                CommandEvent::Stdout(bytes) => {
                    let line = String::from_utf8_lossy(&bytes)
                        .trim_end_matches(&['\r', '\n'][..])
                        .to_string();
                    let parsed = serde_json::from_str::<serde_json::Value>(&line).ok();
                    emit_progress(
                        Some(app),
                        IngestProgressEvent {
                            stream: "stdout".to_string(),
                            line: line.clone(),
                            parsed,
                        },
                    );
                    stdout_lines.push(line);
                }
                CommandEvent::Stderr(bytes) => {
                    let line = String::from_utf8_lossy(&bytes)
                        .trim_end_matches(&['\r', '\n'][..])
                        .to_string();
                    emit_progress(
                        Some(app),
                        IngestProgressEvent {
                            stream: "stderr".to_string(),
                            line: line.clone(),
                            parsed: None,
                        },
                    );
                    stderr_lines.push(line);
                }
                CommandEvent::Error(error) => stderr_lines.push(error),
                CommandEvent::Terminated(payload) => {
                    exit_code = payload.code.unwrap_or(-1);
                }
                _ => {}
            }
        }
    });

    if timed_out {
        if let Some(child) = child.take() {
            let _ = child.kill();
        }
        return Err(timeout_error_message(
            max_runtime.unwrap_or(Duration::from_secs(DEFAULT_INGEST_TIMEOUT_SEC)),
        ));
    }
    // Normal completion: the channel closed, so the child has exited. Drop the
    // handle without killing.
    drop(child);

    Ok(IngestImportResult {
        ok: exit_code == 0,
        exit_code,
        command: {
            let mut command = vec![INGEST_SIDECAR_NAME.to_string()];
            command.extend(args.to_vec());
            command
        },
        stdout: stdout_lines.join("\n"),
        stderr: stderr_lines.join("\n"),
        // Populated downstream by the Tauri command after the sidecar
        // succeeds and the source folder has been scanned for MIDI.
        preserved_reference_midis: Vec::new(),
    })
}

fn run_explicit_binary_capture(
    binary: &str,
    args: &[String],
) -> Result<IngestRuntimeCheckResult, String> {
    let output = Command::new(binary)
        .args(args)
        .output()
        .map_err(|e| format!("failed to start {binary}: {e}"))?;

    let exit_code = output.status.code().unwrap_or(-1);
    let stdout = String::from_utf8_lossy(&output.stdout).trim().to_string();
    let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
    let payload = serde_json::from_str::<serde_json::Value>(&stdout).ok();

    Ok(IngestRuntimeCheckResult {
        ok: output.status.success(),
        exit_code,
        command: {
            let mut command = vec![binary.to_string()];
            command.extend(args.to_vec());
            command
        },
        stdout,
        stderr,
        payload,
    })
}

fn run_tauri_sidecar_capture(
    app: &AppHandle,
    args: &[String],
) -> Result<IngestRuntimeCheckResult, String> {
    run_tauri_sidecar_capture_env(app, args, Vec::new())
}

/// As [`run_tauri_sidecar_capture`], but with extra environment variables set
/// on the child. Used for credential-bearing runs (e.g. a HuggingFace token for
/// a gated weights download) so the secret is passed via the environment rather
/// than a command line. Note the dev-only explicit-binary fallback does not
/// receive `env`.
fn run_tauri_sidecar_capture_env(
    app: &AppHandle,
    args: &[String],
    env: Vec<(String, String)>,
) -> Result<IngestRuntimeCheckResult, String> {
    let command = match app.shell().sidecar(INGEST_SIDECAR_NAME) {
        Ok(command) => {
            let command = command.args(args.to_vec());
            if env.is_empty() {
                command
            } else {
                command.envs(env.into_iter().collect::<HashMap<String, String>>())
            }
        }
        Err(error) => {
            let reason = format!("failed to resolve Tauri sidecar {INGEST_SIDECAR_NAME}: {error}");
            if let Some(result) = run_runtime_fallback_capture(args, &reason) {
                return result;
            }
            return Err(reason);
        }
    };

    let output = match tauri::async_runtime::block_on(async move { command.output().await }) {
        Ok(output) => output,
        Err(error) => {
            let reason = format!("failed to execute Tauri sidecar {INGEST_SIDECAR_NAME}: {error}");
            if let Some(result) = run_runtime_fallback_capture(args, &reason) {
                return result;
            }
            return Err(reason);
        }
    };

    let exit_code = output.status.code().unwrap_or(-1);
    let stdout = String::from_utf8_lossy(&output.stdout).trim().to_string();
    let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
    let payload = serde_json::from_str::<serde_json::Value>(&stdout).ok();

    Ok(IngestRuntimeCheckResult {
        ok: output.status.success(),
        exit_code,
        command: {
            let mut command = vec![INGEST_SIDECAR_NAME.to_string()];
            command.extend(args.to_vec());
            command
        },
        stdout,
        stderr,
        payload,
    })
}

pub fn run_ingest_import(req: IngestImportRequest) -> Result<IngestImportResult, String> {
    run_ingest_import_with_progress(req, None)
}

pub fn run_ingest_import_with_progress(
    req: IngestImportRequest,
    app: Option<&AppHandle>,
) -> Result<IngestImportResult, String> {
    let args = build_ingest_args(&req)?;
    if let Some(binary) = explicit_binary(&req) {
        return run_explicit_binary_with_progress(&binary, &args, app);
    }

    let app = app.ok_or_else(|| {
        format!(
            "Tauri AppHandle required for sidecar execution; use ingest_binary_path to run {} explicitly in tests or tooling",
            INGEST_SIDECAR_NAME
        )
    })?;
    run_tauri_sidecar_with_progress(app, &args)
}

pub fn run_ingest_runtime_check(
    req: IngestRuntimeCheckRequest,
    app: Option<&AppHandle>,
) -> Result<IngestRuntimeCheckResult, String> {
    let explicit_binary = req
        .ingest_binary_path
        .as_ref()
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty());
    let args = vec!["runtime-check".to_string()];

    if let Some(binary) = explicit_binary {
        return run_explicit_binary_capture(&binary, &args);
    }

    let app = app.ok_or_else(|| {
        format!(
            "Tauri AppHandle required for sidecar execution; use ingest_binary_path to run {} explicitly in tests or tooling",
            INGEST_SIDECAR_NAME
        )
    })?;
    run_tauri_sidecar_capture(app, &args)
}

pub fn run_ingest_model_setup(
    app: Option<&AppHandle>,
) -> Result<IngestRuntimeCheckResult, String> {
    let args = vec!["model-setup".to_string()];
    let app = app.ok_or_else(|| {
        format!(
            "Tauri AppHandle required for sidecar execution to run {} model-setup",
            INGEST_SIDECAR_NAME
        )
    })?;
    run_tauri_sidecar_capture(app, &args)
}

/// Download MuScriptor's gated weights into the user's HuggingFace cache.
///
/// The MIT-licensed engine ships inside the sidecar; only the CC-BY-NC-4.0
/// weights are fetched here, and only after the user has accepted the license.
/// The token is passed through the child's environment (never argv) and is not
/// persisted anywhere.
pub fn run_ingest_muscriptor_download(
    hf_token: Option<String>,
    check_only: bool,
    app: Option<&AppHandle>,
) -> Result<IngestRuntimeCheckResult, String> {
    let mut args = vec!["muscriptor-download".to_string()];
    if check_only {
        // Metadata-only probe: answers "am I signed in / has the license been
        // granted" without starting a multi-GB download.
        args.push("--check-only".to_string());
    }
    let app = app.ok_or_else(|| {
        format!(
            "Tauri AppHandle required for sidecar execution to run {} muscriptor-download",
            INGEST_SIDECAR_NAME
        )
    })?;

    let token = hf_token
        .map(|t| t.trim().to_string())
        .filter(|t| !t.is_empty());
    let env = match token {
        // Both names are honoured by huggingface_hub; set both so the download
        // works regardless of which the installed version reads.
        Some(token) => vec![
            ("HF_TOKEN".to_string(), token.clone()),
            ("HUGGING_FACE_HUB_TOKEN".to_string(), token),
        ],
        None => Vec::new(),
    };
    if check_only {
        return run_tauri_sidecar_capture_env(app, &args, env);
    }
    // A multi-GB transfer behind a single blocking call is indistinguishable
    // from a hang, so the download streams its progress lines instead.
    run_tauri_sidecar_streaming_env(app, &args, env, MODEL_DOWNLOAD_PROGRESS_EVENT)
}

pub const MODEL_DOWNLOAD_PROGRESS_EVENT: &str = "model_download_progress";

/// Run the sidecar, forwarding each stdout line to the frontend as it arrives,
/// and return the last JSON object it printed as the result payload.
///
/// The capture variants wait for exit before anyone learns anything; that is
/// fine for a 40s status probe and useless for a 5.5 GB download.
fn run_tauri_sidecar_streaming_env(
    app: &AppHandle,
    args: &[String],
    env: Vec<(String, String)>,
    event_name: &str,
) -> Result<IngestRuntimeCheckResult, String> {
    let command = match app.shell().sidecar(INGEST_SIDECAR_NAME) {
        Ok(command) => {
            let command = command.args(args.to_vec());
            if env.is_empty() {
                command
            } else {
                command.envs(env.into_iter().collect::<HashMap<String, String>>())
            }
        }
        Err(error) => {
            return Err(format!(
                "failed to resolve Tauri sidecar {INGEST_SIDECAR_NAME}: {error}"
            ))
        }
    };

    let (mut rx, child) = command
        .spawn()
        .map_err(|e| format!("failed to spawn Tauri sidecar {INGEST_SIDECAR_NAME}: {e}"))?;

    let mut stdout_lines: Vec<String> = vec![];
    let mut stderr_lines: Vec<String> = vec![];
    let mut exit_code = -1;
    let event_name = event_name.to_string();

    tauri::async_runtime::block_on(async {
        while let Some(event) = rx.recv().await {
            match event {
                CommandEvent::Stdout(bytes) => {
                    let line = String::from_utf8_lossy(&bytes)
                        .trim_end_matches(&['\r', '\n'][..])
                        .to_string();
                    if line.is_empty() {
                        continue;
                    }
                    if let Ok(parsed) = serde_json::from_str::<serde_json::Value>(&line) {
                        let _ = app.emit(event_name.as_str(), parsed);
                    }
                    stdout_lines.push(line);
                }
                CommandEvent::Stderr(bytes) => {
                    stderr_lines.push(
                        String::from_utf8_lossy(&bytes)
                            .trim_end_matches(&['\r', '\n'][..])
                            .to_string(),
                    );
                }
                CommandEvent::Error(error) => stderr_lines.push(error),
                CommandEvent::Terminated(payload) => exit_code = payload.code.unwrap_or(-1),
                _ => {}
            }
        }
    });
    drop(child);

    // The result is the last JSON object carrying `ok`; progress lines precede it.
    let payload = stdout_lines
        .iter()
        .rev()
        .find_map(|line| serde_json::from_str::<serde_json::Value>(line).ok())
        .filter(|value| value.get("ok").is_some());

    Ok(IngestRuntimeCheckResult {
        ok: exit_code == 0,
        exit_code,
        command: {
            let mut command = vec![INGEST_SIDECAR_NAME.to_string()];
            command.extend(args.to_vec());
            command
        },
        stdout: stdout_lines.join("\n"),
        stderr: stderr_lines.join("\n"),
        payload,
    })
}

#[derive(Debug, Serialize, Deserialize, Clone, Default)]
pub struct RefineCandidatesRequest {
    /// Path to the AuralSong directory (must end with `.auralsong` and be a
    /// directory, not a zip — same constraint as `write_auralsong_features_json`).
    pub container_path: String,
    /// One or more instruments to precompute. Empty defaults to `["keys"]`.
    #[serde(default)]
    pub instruments: Vec<String>,
}

/// Run the sidecar's `refine-candidates` subcommand against an existing
/// AuralSong. Writes `features/refine_candidates.<instrument>.json` per
/// instrument; the Studio's Refine workspace then renders them.
///
/// Used by the post-import "Run candidate precompute" CTA so the user
/// doesn't have to drop to a terminal between import and refine.
pub fn run_ingest_refine_candidates(
    req: RefineCandidatesRequest,
    app: Option<&AppHandle>,
) -> Result<IngestRuntimeCheckResult, String> {
    let container = req.container_path.trim();
    if container.is_empty() {
        return Err("missing container_path".to_string());
    }
    let instruments: Vec<String> = if req.instruments.is_empty() {
        vec!["keys".to_string()]
    } else {
        req.instruments
            .iter()
            .map(|s| s.trim().to_string())
            .filter(|s| !s.is_empty())
            .collect()
    };
    if instruments.is_empty() {
        return Err("no instruments after filtering".to_string());
    }

    let mut args: Vec<String> = vec!["refine-candidates".to_string(), container.to_string()];
    for inst in &instruments {
        args.push("--instrument".to_string());
        args.push(inst.clone());
    }

    let app = app.ok_or_else(|| {
        "Tauri AppHandle required for sidecar execution; ingest_refine_candidates can't run headless".to_string()
    })?;
    run_tauri_sidecar_capture(app, &args)
}

#[derive(Debug, Serialize, Deserialize, Clone, Default)]
pub struct SpectrogramRequest {
    /// Path to the AuralSong directory (must end with `.auralsong` and be a
    /// directory, not a zip — same constraint as `RefineCandidatesRequest`).
    pub container_path: String,
    /// One or more melodic instruments to build. Empty defaults to all
    /// melodic stems present in the pack.
    #[serde(default)]
    pub instruments: Vec<String>,
}

/// Run the sidecar's `spectrogram` subcommand against an existing AuralSong.
/// Writes `features/spectrogram/<role>/` (tiles + spectrogram.json) per
/// melodic stem; the Studio's Refine (cleanup) workspace then renders them.
///
/// Used by the "Build spectrogram" CTA so a song can be prepped for cleanup
/// on-demand without a full re-import.
pub fn run_ingest_spectrogram(
    req: SpectrogramRequest,
    app: Option<&AppHandle>,
) -> Result<IngestRuntimeCheckResult, String> {
    let container = req.container_path.trim();
    if container.is_empty() {
        return Err("missing container_path".to_string());
    }
    let instruments: Vec<String> = req
        .instruments
        .iter()
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .collect();

    let mut args: Vec<String> = vec!["spectrogram".to_string(), container.to_string()];
    for inst in &instruments {
        args.push("--instrument".to_string());
        args.push(inst.clone());
    }

    let app = app.ok_or_else(|| {
        "Tauri AppHandle required for sidecar execution; ingest_spectrogram can't run headless"
            .to_string()
    })?;
    run_tauri_sidecar_capture(app, &args)
}

#[derive(Debug, Serialize, Deserialize, Clone, Default)]
pub struct AlignDrumOnsetsRequest {
    /// Path to the pack directory (contains `drum_tab.json` + `audio/stems/drums.wav`).
    pub container_path: String,
}

/// Run the sidecar's `align-drum-onsets` subcommand: snap the pack's drum-tab
/// hit times onto the drums-stem audio transients, in place. Backs the drum
/// cleanup editor's "Snap to onsets" button.
pub fn run_ingest_align_drum_onsets(
    req: AlignDrumOnsetsRequest,
    app: Option<&AppHandle>,
) -> Result<IngestRuntimeCheckResult, String> {
    let container = req.container_path.trim();
    if container.is_empty() {
        return Err("missing container_path".to_string());
    }
    let args: Vec<String> = vec!["align-drum-onsets".to_string(), container.to_string()];
    let app = app.ok_or_else(|| {
        "Tauri AppHandle required for sidecar execution; ingest_align_drum_onsets can't run headless"
            .to_string()
    })?;
    run_tauri_sidecar_capture(app, &args)
}

/// Run the sidecar's `refresh-meter` subcommand: re-track beats/downbeats/meter
/// (Beat This!) on an existing pack and rewrite `song_timeline.json` in place
/// (auto-backup to `.bak`). Backs the Cleanup/Edit "Refresh meter" button.
/// Reuses the container-path request shape.
pub fn run_ingest_refresh_meter(
    req: AlignDrumOnsetsRequest,
    app: Option<&AppHandle>,
) -> Result<IngestRuntimeCheckResult, String> {
    let container = req.container_path.trim();
    if container.is_empty() {
        return Err("missing container_path".to_string());
    }
    let args: Vec<String> = vec!["refresh-meter".to_string(), container.to_string()];
    let app = app.ok_or_else(|| {
        "Tauri AppHandle required for sidecar execution; ingest_refresh_meter can't run headless"
            .to_string()
    })?;
    run_tauri_sidecar_capture(app, &args)
}

/// Run the sidecar's `prep-arrangements` subcommand: read the manifest's
/// `arrangements[].file` wire JSONs, synthesize `aural/notes.mid` (the melodic
/// gameplay charts source, one instrument per role) and `song_timeline.json`
/// (beats/measures/sections) in place, and patch the manifest keys. Backs the
/// Studio Cleanup/Edit "Prep notes" button; needed before a freshly-imported
/// sloppak (which ships arrangement JSONs but no notes.mid) shows a melodic
/// highway in the game. Reuses the container-path request shape.
pub fn run_ingest_prep_arrangements(
    req: AlignDrumOnsetsRequest,
    app: Option<&AppHandle>,
) -> Result<IngestRuntimeCheckResult, String> {
    let container = req.container_path.trim();
    if container.is_empty() {
        return Err("missing container_path".to_string());
    }
    let args: Vec<String> = vec!["prep-arrangements".to_string(), container.to_string()];
    let app = app.ok_or_else(|| {
        "Tauri AppHandle required for sidecar execution; ingest_prep_arrangements can't run headless"
            .to_string()
    })?;
    run_tauri_sidecar_capture(app, &args)
}

#[cfg(test)]
mod tests {
    use super::{
        build_ingest_args, explicit_binary, resolve_max_runtime, run_ingest_import,
        timeout_error_message, IngestImportRequest, IngestSubcommand, DEFAULT_INGEST_TIMEOUT_SEC,
    };
    use serde_json::Value;
    use std::env;
    use std::path::Path;
    use std::time::Duration;

    fn req_base() -> IngestImportRequest {
        IngestImportRequest {
            source_path: "C:/input/song.wav".to_string(),
            out_auralsong_path: Some("C:/songs/my.auralsong".to_string()),
            ..IngestImportRequest::default()
        }
    }

    fn has_pair(args: &[String], key: &str, value: &str) -> bool {
        args.windows(2).any(|w| w[0] == key && w[1] == value)
    }

    #[test]
    fn build_ingest_args_forwards_transcription_flags() {
        let mut req = req_base();
        req.subcommand = Some(IngestSubcommand::Import);
        req.profile = Some("full".to_string());
        req.config = Some("{\"bpm_hint\":120}".to_string());
        req.title = Some("My Song".to_string());
        req.artist = Some("My Artist".to_string());
        req.duration_sec = Some(42.5);
        req.drum_filter = Some("combined_filter".to_string());
        req.melodic_method = Some("basic_pitch".to_string());
        req.shifts = Some(3);
        req.multi_filter = Some(true);

        let args = build_ingest_args(&req).expect("args");
        assert_eq!(args[0], "import");
        assert_eq!(args[1], "C:/input/song.wav");
        assert!(has_pair(&args, "--out", "C:/songs/my.auralsong"));
        assert!(has_pair(&args, "--profile", "full"));
        assert!(has_pair(&args, "--config", "{\"bpm_hint\":120}"));
        assert!(has_pair(&args, "--title", "My Song"));
        assert!(has_pair(&args, "--artist", "My Artist"));
        assert!(has_pair(&args, "--duration-sec", "42.5"));
        assert!(has_pair(&args, "--drum-filter", "combined_filter"));
        assert!(has_pair(&args, "--melodic-method", "basic_pitch"));
        assert!(has_pair(&args, "--shifts", "3"));
        assert!(args.iter().any(|x| x == "--multi-filter"));
    }

    #[test]
    fn build_ingest_args_handles_import_dir_defaults() {
        let mut req = req_base();
        req.subcommand = Some(IngestSubcommand::ImportDir);
        req.profile = None;
        req.multi_filter = Some(false);

        let args = build_ingest_args(&req).expect("args");
        assert_eq!(args[0], "import-dir");
        assert!(has_pair(&args, "--profile", "full"));
        assert!(!args.iter().any(|x| x == "--multi-filter"));
    }

    #[test]
    fn build_ingest_args_requires_paths() {
        let req = IngestImportRequest {
            source_path: "   ".to_string(),
            out_auralsong_path: None,
            ..IngestImportRequest::default()
        };
        let err = build_ingest_args(&req).expect_err("expected error");
        assert!(err.contains("missing source_path"));
    }

    #[test]
    fn explicit_binary_honors_override() {
        let req = IngestImportRequest {
            ingest_binary_path: Some("C:/tools/custom_ingest.exe".to_string()),
            ..req_base()
        };
        assert_eq!(
            explicit_binary(&req).as_deref(),
            Some("C:/tools/custom_ingest.exe")
        );
    }

    #[test]
    fn resolve_max_runtime_defaults_when_unset_or_blank() {
        let default = Duration::from_secs(DEFAULT_INGEST_TIMEOUT_SEC);
        assert_eq!(resolve_max_runtime(None), Some(default));
        assert_eq!(resolve_max_runtime(Some("")), Some(default));
        assert_eq!(resolve_max_runtime(Some("   ")), Some(default));
    }

    #[test]
    fn resolve_max_runtime_parses_override_seconds() {
        assert_eq!(
            resolve_max_runtime(Some("60")),
            Some(Duration::from_secs(60))
        );
        assert_eq!(
            resolve_max_runtime(Some("  120 ")),
            Some(Duration::from_secs(120))
        );
    }

    #[test]
    fn resolve_max_runtime_zero_disables_the_deadline() {
        assert_eq!(resolve_max_runtime(Some("0")), None);
    }

    #[test]
    fn resolve_max_runtime_invalid_override_falls_back_to_default() {
        let default = Duration::from_secs(DEFAULT_INGEST_TIMEOUT_SEC);
        assert_eq!(resolve_max_runtime(Some("not-a-number")), Some(default));
        assert_eq!(resolve_max_runtime(Some("-5")), Some(default));
    }

    #[test]
    fn timeout_error_message_states_the_limit_and_override_knob() {
        let msg = timeout_error_message(Duration::from_secs(30 * 60));
        assert!(
            msg.contains("30 min"),
            "must state the limit in minutes: {msg}"
        );
        assert!(
            msg.contains("AURALPRIMER_INGEST_TIMEOUT_SEC"),
            "must point at the override env var: {msg}"
        );
        assert!(
            msg.to_lowercase().contains("cpu fallback") || msg.to_lowercase().contains("wedged"),
            "must explain the likely cause: {msg}"
        );
    }

    #[test]
    #[ignore = "requires local piano audio/config paths and a packaged sidecar binary"]
    fn explicit_binary_piano_import_smoke_from_env() {
        let binary = env::var("AURALPRIMER_STUDIO_INGEST_SMOKE_BINARY")
            .expect("set AURALPRIMER_STUDIO_INGEST_SMOKE_BINARY");
        let source = env::var("AURALPRIMER_STUDIO_INGEST_SMOKE_SOURCE")
            .expect("set AURALPRIMER_STUDIO_INGEST_SMOKE_SOURCE");
        let out = env::var("AURALPRIMER_STUDIO_INGEST_SMOKE_OUT")
            .expect("set AURALPRIMER_STUDIO_INGEST_SMOKE_OUT");
        let config = env::var("AURALPRIMER_STUDIO_INGEST_SMOKE_CONFIG").ok();
        let drum_filter = env::var("AURALPRIMER_STUDIO_INGEST_SMOKE_DRUM_FILTER")
            .unwrap_or_else(|_| "combined_filter".to_string());

        assert!(Path::new(&binary).is_file(), "sidecar missing: {binary}");
        assert!(Path::new(&source).is_file(), "source missing: {source}");
        assert!(
            !Path::new(&out).exists(),
            "smoke output path must not already exist: {out}"
        );
        if let Some(config) = &config {
            assert!(Path::new(config).is_file(), "config missing: {config}");
        }

        let result = run_ingest_import(IngestImportRequest {
            source_path: source,
            out_auralsong_path: Some(out.clone()),
            subcommand: Some(IngestSubcommand::Import),
            profile: Some("full".to_string()),
            config,
            title: Some("Studio Wrapper Piano Smoke".to_string()),
            drum_filter: Some(drum_filter),
            melodic_method: Some("piano_auto".to_string()),
            ingest_binary_path: Some(binary),
            ..IngestImportRequest::default()
        })
        .expect("Studio ingest wrapper should execute explicit sidecar binary");

        assert!(
            result.ok,
            "ingest failed with exit {}:\nstdout:\n{}\nstderr:\n{}",
            result.exit_code, result.stdout, result.stderr
        );
        assert_eq!(result.exit_code, 0);
        assert!(
            result.command.iter().any(|arg| arg == "--melodic-method"),
            "command should forward melodic method: {:?}",
            result.command
        );
        assert!(
            result.command.iter().any(|arg| arg == "--drum-filter"),
            "command should forward GUI drum filter default: {:?}",
            result.command
        );

        let manifest_path = Path::new(&out).join("manifest.json");
        assert!(
            manifest_path.is_file(),
            "manifest missing: {manifest_path:?}"
        );
        let manifest: Value =
            serde_json::from_slice(&std::fs::read(&manifest_path).expect("read smoke manifest"))
                .expect("parse smoke manifest");

        let used = manifest
            .pointer("/pipeline/transcription/instrument_melodic_methods_used/keys")
            .and_then(Value::as_str);
        assert_eq!(used, Some("piano_auto"));

        let stems = manifest
            .pointer("/pipeline/transcription/instrument_stems_transcribed")
            .and_then(Value::as_array)
            .expect("instrument_stems_transcribed");
        assert_eq!(stems.len(), 1, "expected only keys stem: {stems:?}");
        assert_eq!(stems[0].as_str(), Some("keys"));

        let drum_backend = manifest
            .pointer("/pipeline/transcription/drum_engine_backend")
            .and_then(Value::as_str);
        assert_eq!(drum_backend, Some("skipped"));
        let skip_reason = manifest
            .pointer("/pipeline/transcription/drum_engine_meta/skip_reason")
            .and_then(Value::as_str);
        assert_eq!(skip_reason, Some("input_stems_mix_without_drums"));
    }
}
