use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use std::{fs, path::Path};

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ModelConfig {
    pub version: u32,
    pub conv_channels: usize,
    pub num_blocks: usize,
}

pub fn load_model_config(path: &Path) -> Result<ModelConfig> {
    let raw = fs::read_to_string(path)
        .with_context(|| format!("read model config from {}", path.display()))?;
    serde_json::from_str(&raw)
        .with_context(|| format!("parse model config from {}", path.display()))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn model_config_parses_exported_json_shape() {
        let config: ModelConfig =
            serde_json::from_str(r#"{"version":4,"conv_channels":512,"num_blocks":8}"#).unwrap();
        assert_eq!(
            config,
            ModelConfig {
                version: 4,
                conv_channels: 512,
                num_blocks: 8
            }
        );
    }
}
