use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::fs;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct InstalledModelPack {
    pub id: String,
    pub version: String,
    pub root_dir: String,
    pub manifest_path: String,
    pub ok: bool,
    pub error: Option<String>,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct InstallModelPackZipRequest {
    pub zip_bytes: Vec<u8>,
    pub expected_zip_sha256: Option<String>,
}

fn models_root_dir(app_data_dir: &Path) -> PathBuf {
    // Storage rule from spec: assets/models/<model-id>/<version>/...
    app_data_dir.join("assets").join("models")
}

fn list_dirs(p: &Path) -> Vec<PathBuf> {
    let mut out = vec![];
    let Ok(rd) = fs::read_dir(p) else {
        return out;
    };
    for e in rd.flatten() {
        let path = e.path();
        if path.is_dir() {
            out.push(path);
        }
    }
    out
}

pub fn list_installed_modelpacks(app_data_dir: &Path) -> Vec<InstalledModelPack> {
    let root = models_root_dir(app_data_dir);

    let mut out = vec![];
    for id_dir in list_dirs(&root) {
        let id = id_dir.file_name().and_then(|s| s.to_str()).unwrap_or("").to_string();
        if id.is_empty() {
            continue;
        }

        for ver_dir in list_dirs(&id_dir) {
            let version = ver_dir
                .file_name()
                .and_then(|s| s.to_str())
                .unwrap_or("")
                .to_string();
            if version.is_empty() {
                continue;
            }

            let manifest_path = ver_dir.join("modelpack.json");
            let ok = manifest_path.is_file();
            let err = if ok { None } else { Some("missing modelpack.json".to_string()) };

            out.push(InstalledModelPack {
                id: id.clone(),
                version,
                root_dir: ver_dir.to_string_lossy().to_string(),
                manifest_path: manifest_path.to_string_lossy().to_string(),
                ok,
                error: err,
            });
        }
    }

    // deterministic ordering
    out.sort_by(|a, b| (a.id.clone(), a.version.clone()).cmp(&(b.id.clone(), b.version.clone())));
    out
}

fn sha256_hex(bytes: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(bytes);
    let digest = hasher.finalize();
    hex::encode(digest)
}

/// Extracts a modelpack zip that has `modelpack.json` at the archive root.
///
/// Expected layout inside zip:
/// - modelpack.json
/// - files/**
fn extract_modelpack_zip(bytes: &[u8], dest_root: &Path) -> Result<(), String> {
    let reader = std::io::Cursor::new(bytes);
    let mut archive = zip::ZipArchive::new(reader).map_err(|e| format!("zip open: {e}"))?;

    // Read and parse manifest to get id/version.
    // Scope the ZipFile borrow so we can iterate `archive` later.
    let mf_raw = {
        let mut mf = archive
            .by_name("modelpack.json")
            .map_err(|e| format!("zip missing modelpack.json: {e}"))?;
        let mut raw = String::new();
        mf.read_to_string(&mut raw)
            .map_err(|e| format!("read modelpack.json: {e}"))?;
        raw
    };

    let v: serde_json::Value = serde_json::from_str(&mf_raw).map_err(|e| format!("invalid modelpack.json: {e}"))?;

    let id = v
        .get("id")
        .and_then(|x| x.as_str())
        .ok_or_else(|| "modelpack.json missing id".to_string())?
        .to_string();
    let version = v
        .get("version")
        .and_then(|x| x.as_str())
        .ok_or_else(|| "modelpack.json missing version".to_string())?
        .to_string();

    if id.contains("/") || version.contains("/") {
        return Err("invalid id/version".to_string());
    }

    let dest = dest_root.join(&id).join(&version);

    // Never overwrite an existing version.
    if dest.exists() {
        return Err(format!("model pack already installed: {id}/{version}"));
    }

    fs::create_dir_all(&dest).map_err(|e| format!("mkdir {}: {e}", dest.display()))?;

    // Write manifest first.
    fs::write(dest.join("modelpack.json"), mf_raw).map_err(|e| format!("write modelpack.json: {e}"))?;

    // Extract files/* and preserve relative layout.
    let n = archive.len();
    for i in 0..n {
        let mut file = archive.by_index(i).map_err(|e| format!("zip index {i}: {e}"))?;
        let name = file.name().to_string();

        if name == "modelpack.json" {
            continue;
        }

        // Only allow files under files/.
        if !name.starts_with("files/") {
            continue;
        }

        // Path traversal prevention.
        let rel = Path::new(&name);
        if rel.components().any(|c| matches!(c, std::path::Component::ParentDir)) {
            return Err("zip path traversal detected".to_string());
        }

        let out_path = dest.join(rel);
        if let Some(parent) = out_path.parent() {
            fs::create_dir_all(parent).map_err(|e| format!("mkdir {}: {e}", parent.display()))?;
        }

        if file.is_dir() {
            fs::create_dir_all(&out_path).map_err(|e| format!("mkdir {}: {e}", out_path.display()))?;
        } else {
            let mut out_f = fs::File::create(&out_path).map_err(|e| format!("create {}: {e}", out_path.display()))?;
            std::io::copy(&mut file, &mut out_f).map_err(|e| format!("write {}: {e}", out_path.display()))?;
            out_f.flush().ok();
        }
    }

    Ok(())
}

pub fn install_modelpack_zip_bytes(
    app_data_dir: &Path,
    req: InstallModelPackZipRequest,
) -> Result<(), String> {
    if let Some(expected) = &req.expected_zip_sha256 {
        let actual = sha256_hex(&req.zip_bytes);
        if actual.to_lowercase() != expected.to_lowercase() {
            return Err(format!("sha256 mismatch (expected {expected}, got {actual})"));
        }
    }

    let root = models_root_dir(app_data_dir);
    fs::create_dir_all(&root).map_err(|e| format!("mkdir models root: {e}"))?;

    extract_modelpack_zip(&req.zip_bytes, &root)
}

pub fn install_modelpack_from_path(app_data_dir: &Path, path: &str) -> Result<(), String> {
    let p = PathBuf::from(path);
    let bytes = fs::read(&p).map_err(|e| format!("read {}: {e}", p.display()))?;

    let root = models_root_dir(app_data_dir);
    fs::create_dir_all(&root).map_err(|e| format!("mkdir models root: {e}"))?;

    // No hash enforced for local import.
    extract_modelpack_zip(&bytes, &root)
}
