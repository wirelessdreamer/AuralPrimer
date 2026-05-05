use serde::{Deserialize, Serialize};
use std::io::{BufRead, BufReader};
use std::process::{Command, Stdio};
use std::sync::mpsc;
use std::thread;
use tauri::{AppHandle, Emitter};
use tauri_plugin_shell::process::CommandEvent;
use tauri_plugin_shell::ShellExt;

const INGEST_SIDECAR_NAME: &str = "aural_ingest";

#[derive(Debug, Serialize, Deserialize, Clone, Copy, PartialEq, Eq, Default)]
#[serde(rename_all = "kebab-case")]
pub enum IngestSubcommand {
    #[default]
    Import,
    ImportDir,
    ImportDtx,
}

impl IngestSubcommand {
    pub fn as_cli_arg(self) -> &'static str {
        match self {
            IngestSubcommand::Import => "import",
            IngestSubcommand::ImportDir => "import-dir",
            IngestSubcommand::ImportDtx => "import-dtx",
        }
    }
}

#[derive(Debug, Serialize, Deserialize, Clone, Default)]
pub struct IngestImportRequest {
    pub source_path: String,

    #[serde(default)]
    pub out_songpack_path: Option<String>,

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
    Some(run_explicit_binary_with_progress(&binary, args, app).map_err(|fallback_error| {
        format!("{reason}; fallback binary {binary} also failed: {fallback_error}")
    }))
}

fn run_runtime_fallback_capture(
    args: &[String],
    reason: &str,
) -> Option<Result<IngestRuntimeCheckResult, String>> {
    let binary = runtime_fallback_sidecar_binary()?;
    Some(run_explicit_binary_capture(&binary, args).map_err(|fallback_error| {
        format!("{reason}; fallback binary {binary} also failed: {fallback_error}")
    }))
}

pub fn build_ingest_args(req: &IngestImportRequest) -> Result<Vec<String>, String> {
    let source_path = req.source_path.trim();
    if source_path.is_empty() {
        return Err("missing source_path".to_string());
    }

    let out_songpack_path = non_empty_opt(&req.out_songpack_path)
        .ok_or_else(|| "missing out_songpack_path".to_string())?;

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
        out_songpack_path,
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

    for (kind, line) in rx {
        match kind {
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

    let (mut rx, _child) = match command.spawn() {
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

    tauri::async_runtime::block_on(async {
        while let Some(event) = rx.recv().await {
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
    })
}

fn run_explicit_binary_capture(binary: &str, args: &[String]) -> Result<IngestRuntimeCheckResult, String> {
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

fn run_tauri_sidecar_capture(app: &AppHandle, args: &[String]) -> Result<IngestRuntimeCheckResult, String> {
    let command = match app.shell().sidecar(INGEST_SIDECAR_NAME) {
        Ok(command) => command.args(args.to_vec()),
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

#[cfg(test)]
mod tests {
    use super::{build_ingest_args, explicit_binary, IngestImportRequest, IngestSubcommand};

    fn req_base() -> IngestImportRequest {
        IngestImportRequest {
            source_path: "C:/input/song.wav".to_string(),
            out_songpack_path: Some("C:/songs/my.songpack".to_string()),
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
        assert!(has_pair(&args, "--out", "C:/songs/my.songpack"));
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
            out_songpack_path: None,
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
}
