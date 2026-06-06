use anyhow::{anyhow, Context, Result};
use std::{
    fs,
    path::{Path, PathBuf},
};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ModelImportResult {
    pub model_path: PathBuf,
    pub model_name: String,
}

pub fn import_model_file(source: &Path, output_dir: &Path) -> Result<ModelImportResult> {
    if !source.exists() {
        return Err(anyhow!("model source not found: {}", source.display()));
    }
    if source.is_dir() {
        return Err(anyhow!(
            "model directory import is not supported; choose a .safetensors file"
        ));
    }
    let model_name = sanitize_model_name(
        source
            .file_stem()
            .or_else(|| source.file_name())
            .and_then(|name| name.to_str())
            .unwrap_or("model"),
    );
    let result = match source
        .extension()
        .and_then(|extension| extension.to_str())
        .map(|extension| extension.to_ascii_lowercase())
        .as_deref()
    {
        Some("safetensors") => {
            copy_safetensors_model(source, output_dir)?;
            Ok(ModelImportResult {
                model_path: output_dir.to_path_buf(),
                model_name,
            })
        }
        Some(extension) => Err(anyhow!(
            "unsupported model file .{extension}; only .safetensors model files are supported"
        )),
        None => Err(anyhow!(
            "unsupported model source {}; only .safetensors model files are supported",
            source.display()
        )),
    };

    if result.is_err() {
        let _ = fs::remove_dir_all(output_dir);
    }
    result
}

pub fn ensure_exported_model_dir(path: &Path) -> Result<()> {
    let weights = path.join("model.safetensors");
    let config = path.join("model_config.json");
    if !weights.is_file() || !config.is_file() {
        return Err(anyhow!(
            "model directory must contain model.safetensors and model_config.json: {}",
            path.display()
        ));
    }
    Ok(())
}

pub fn copy_safetensors_model(source: &Path, output_dir: &Path) -> Result<()> {
    let config = source.with_file_name("model_config.json");
    if !config.is_file() {
        return Err(anyhow!(
            "model_config.json not found next to {}; choose the model.safetensors file from an exported model",
            source.display()
        ));
    }
    fs::create_dir_all(output_dir)
        .with_context(|| format!("create imported model directory {}", output_dir.display()))?;
    fs::copy(source, output_dir.join("model.safetensors"))
        .with_context(|| format!("copy {}", source.display()))?;
    fs::copy(&config, output_dir.join("model_config.json"))
        .with_context(|| format!("copy {}", config.display()))?;
    Ok(())
}

pub fn sanitize_model_name(raw: &str) -> String {
    let mut slug = String::with_capacity(raw.len());
    for ch in raw.chars() {
        if ch.is_ascii_alphanumeric() || ch == '-' || ch == '_' {
            slug.push(ch);
        } else {
            slug.push('-');
        }
    }
    let slug = slug.trim_matches('-');
    if slug.is_empty() {
        "model".to_string()
    } else {
        slug.to_string()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn temp_dir(name: &str) -> PathBuf {
        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let path = std::env::temp_dir().join(format!("majsoul-mortal-import-{name}-{stamp}"));
        fs::create_dir_all(&path).unwrap();
        path
    }

    #[test]
    fn safetensors_file_uses_sibling_config() {
        let root = temp_dir("safetensors");
        let source = root.join("mortal.safetensors");
        let output = root.join("out");
        fs::write(&source, b"weights").unwrap();
        fs::write(root.join("model_config.json"), b"{\"version\":4}").unwrap();

        let result = import_model_file(&source, &output).unwrap();

        assert_eq!(result.model_name, "mortal");
        assert_eq!(
            fs::read(output.join("model.safetensors")).unwrap(),
            b"weights"
        );
        assert_eq!(
            fs::read(output.join("model_config.json")).unwrap(),
            b"{\"version\":4}"
        );
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn directory_import_is_rejected() {
        let root = temp_dir("dir");
        let source = root.join("source-model");
        fs::create_dir_all(&source).unwrap();
        fs::write(source.join("model.safetensors"), b"weights").unwrap();
        fs::write(source.join("model_config.json"), b"{}").unwrap();

        let error = import_model_file(&source, &root.join("out"))
            .unwrap_err()
            .to_string();

        assert!(error.contains("model directory import is not supported"));
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn unsupported_model_file_is_rejected() {
        let root = temp_dir("bad-extension");
        let source = root.join("mortal.txt");
        fs::write(&source, b"nope").unwrap();

        let error = import_model_file(&source, &root.join("out"))
            .unwrap_err()
            .to_string();

        assert!(error.contains("unsupported model file .txt"));
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn pth_model_file_is_rejected_without_conversion() {
        let root = temp_dir("pth");
        let source = root.join("mortal.pth");
        fs::write(&source, b"checkpoint").unwrap();

        let error = import_model_file(&source, &root.join("out"))
            .unwrap_err()
            .to_string();

        assert!(error.contains("only .safetensors"));
        assert!(!root.join("out").exists());
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn model_name_is_sanitized() {
        assert_eq!(sanitize_model_name("mortal model"), "mortal-model");
        assert_eq!(sanitize_model_name("!!!"), "model");
    }
}
