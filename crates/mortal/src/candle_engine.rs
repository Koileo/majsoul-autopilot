use crate::config::{load_model_config, ModelConfig};
use crate::native::{EngineDecision, NativeEngine, Observation};
use anyhow::{anyhow, ensure, Result};
use candle_core::{DType, Device, Tensor};
use candle_nn::{
    batch_norm, conv1d, conv1d_no_bias, linear,
    ops::{mish, sigmoid},
    BatchNorm, BatchNormConfig, Conv1d, Conv1dConfig, Linear, Module, ModuleT, VarBuilder,
};
use std::path::Path;

pub struct CandleMortalEngine {
    config: ModelConfig,
    net: MortalNet,
    device: Device,
}

impl CandleMortalEngine {
    pub fn load(export_dir: impl AsRef<Path>) -> Result<Self> {
        let export_dir = export_dir.as_ref();
        let config_path = export_dir.join("model_config.json");
        let config = load_model_config(&config_path)?;
        ensure!(config.version == 4, "only Mortal v4 is supported");
        let device = Device::Cpu;
        let model_path = export_dir.join("model.safetensors");
        let vb =
            unsafe { VarBuilder::from_mmaped_safetensors(&[model_path], DType::F32, &device)? };
        let net = MortalNet::load(&config, vb)?;
        Ok(Self {
            config,
            net,
            device,
        })
    }
}

impl NativeEngine for CandleMortalEngine {
    fn version(&self) -> u32 {
        self.config.version
    }

    fn react_batch(&mut self, observations: &[Observation]) -> Result<Vec<EngineDecision>> {
        ensure!(!observations.is_empty(), "empty Mortal batch");
        let channels = observations[0].channels;
        let width = observations[0].width;
        let mut values = Vec::with_capacity(observations.len() * channels * width);
        let masks = observations
            .iter()
            .map(|obs| {
                ensure!(
                    obs.channels == channels && obs.width == width,
                    "mixed obs shapes"
                );
                ensure!(obs.mask.len() == 46, "mask length must be 46");
                values.extend_from_slice(&obs.values);
                Ok(obs.mask.clone())
            })
            .collect::<Result<Vec<_>>>()?;

        let obs = Tensor::from_vec(values, (observations.len(), channels, width), &self.device)?;
        let logits = self.net.forward(&obs)?.to_vec2::<f32>()?;
        logits_to_decisions(logits, masks)
    }
}

fn logits_to_decisions(
    logits: Vec<Vec<f32>>,
    masks: Vec<Vec<bool>>,
) -> Result<Vec<EngineDecision>> {
    logits
        .into_iter()
        .zip(masks)
        .map(|(row, mask)| {
            ensure!(row.len() == 47, "DQN output row must be 47");
            ensure!(mask.len() == 46, "mask length must be 46");
            let v = row[0];
            let a = &row[1..];
            let legal_count = mask.iter().filter(|&&flag| flag).count();
            ensure!(legal_count > 0, "Mortal mask has no legal actions");
            let legal_sum = a
                .iter()
                .zip(&mask)
                .filter_map(|(value, legal)| legal.then_some(*value))
                .sum::<f32>();
            let legal_mean = legal_sum / legal_count as f32;
            let q_values = a
                .iter()
                .zip(&mask)
                .map(|(value, legal)| {
                    if *legal {
                        v + *value - legal_mean
                    } else {
                        f32::NEG_INFINITY
                    }
                })
                .collect::<Vec<_>>();
            let action = q_values
                .iter()
                .enumerate()
                .max_by(|(_, left), (_, right)| left.total_cmp(right))
                .map(|(idx, _)| idx)
                .ok_or_else(|| anyhow!("empty q values"))?;
            Ok(EngineDecision {
                action,
                q_values,
                mask,
                is_greedy: true,
            })
        })
        .collect()
}

struct MortalNet {
    brain: Brain,
    dqn: Linear,
}

impl MortalNet {
    fn load(config: &ModelConfig, vb: VarBuilder<'_>) -> Result<Self> {
        Ok(Self {
            brain: Brain::load(config, vb.pp("brain"))?,
            dqn: linear(1024, 47, vb.pp("dqn.net"))?,
        })
    }

    fn forward(&self, x: &Tensor) -> candle_core::Result<Tensor> {
        let phi = self.brain.forward(x)?;
        self.dqn.forward(&phi)
    }
}

struct Brain {
    encoder: ResNet,
}

impl Brain {
    fn load(config: &ModelConfig, vb: VarBuilder<'_>) -> Result<Self> {
        Ok(Self {
            encoder: ResNet::load(config, vb.pp("encoder.net"))?,
        })
    }

    fn forward(&self, x: &Tensor) -> candle_core::Result<Tensor> {
        mish(&self.encoder.forward(x)?)
    }
}

struct ResNet {
    input: Conv1d,
    blocks: Vec<ResBlock>,
    tail_norm: BatchNorm,
    tail_conv: Conv1d,
    tail_linear: Linear,
}

impl ResNet {
    fn load(config: &ModelConfig, vb: VarBuilder<'_>) -> Result<Self> {
        let conv_cfg = Conv1dConfig {
            padding: 1,
            ..Default::default()
        };
        let input = conv1d_no_bias(1012, config.conv_channels, 3, conv_cfg, vb.pp("0"))?;
        let blocks = (0..config.num_blocks)
            .map(|idx| ResBlock::load(config.conv_channels, vb.pp((idx + 1).to_string())))
            .collect::<Result<Vec<_>>>()?;
        let tail_norm = batch_norm(
            config.conv_channels,
            bn_config(),
            vb.pp((config.num_blocks + 1).to_string()),
        )?;
        let tail_conv = conv1d(
            config.conv_channels,
            32,
            3,
            conv_cfg,
            vb.pp((config.num_blocks + 3).to_string()),
        )?;
        let tail_linear = linear(32 * 34, 1024, vb.pp((config.num_blocks + 6).to_string()))?;
        Ok(Self {
            input,
            blocks,
            tail_norm,
            tail_conv,
            tail_linear,
        })
    }

    fn forward(&self, x: &Tensor) -> candle_core::Result<Tensor> {
        let mut out = self.input.forward(x)?;
        for block in &self.blocks {
            out = block.forward(&out)?;
        }
        out = self.tail_norm.forward_t(&out, false)?;
        out = mish(&out)?;
        out = self.tail_conv.forward(&out)?;
        out = mish(&out)?;
        out = out.flatten_from(1)?;
        self.tail_linear.forward(&out)
    }
}

struct ResBlock {
    bn1: BatchNorm,
    conv1: Conv1d,
    bn2: BatchNorm,
    conv2: Conv1d,
    ca: ChannelAttention,
}

impl ResBlock {
    fn load(channels: usize, vb: VarBuilder<'_>) -> Result<Self> {
        let conv_cfg = Conv1dConfig {
            padding: 1,
            ..Default::default()
        };
        Ok(Self {
            bn1: batch_norm(channels, bn_config(), vb.pp("res_unit.0"))?,
            conv1: conv1d_no_bias(channels, channels, 3, conv_cfg, vb.pp("res_unit.2"))?,
            bn2: batch_norm(channels, bn_config(), vb.pp("res_unit.3"))?,
            conv2: conv1d_no_bias(channels, channels, 3, conv_cfg, vb.pp("res_unit.5"))?,
            ca: ChannelAttention::load(channels, vb.pp("ca.shared_mlp"))?,
        })
    }

    fn forward(&self, x: &Tensor) -> candle_core::Result<Tensor> {
        let mut out = self.bn1.forward_t(x, false)?;
        out = mish(&out)?;
        out = self.conv1.forward(&out)?;
        out = self.bn2.forward_t(&out, false)?;
        out = mish(&out)?;
        out = self.conv2.forward(&out)?;
        out = self.ca.forward(&out)?;
        out + x
    }
}

struct ChannelAttention {
    fc1: Linear,
    fc2: Linear,
}

impl ChannelAttention {
    fn load(channels: usize, vb: VarBuilder<'_>) -> Result<Self> {
        Ok(Self {
            fc1: linear(channels, channels / 16, vb.pp("0"))?,
            fc2: linear(channels / 16, channels, vb.pp("2"))?,
        })
    }

    fn forward(&self, x: &Tensor) -> candle_core::Result<Tensor> {
        let avg = self.shared_mlp(&x.mean(2)?)?;
        let max = self.shared_mlp(&x.max(2)?)?;
        let weight = sigmoid(&(avg + max)?)?.unsqueeze(2)?;
        x.broadcast_mul(&weight)
    }

    fn shared_mlp(&self, x: &Tensor) -> candle_core::Result<Tensor> {
        let x = self.fc1.forward(x)?;
        let x = mish(&x)?;
        self.fc2.forward(&x)
    }
}

fn bn_config() -> BatchNormConfig {
    BatchNormConfig {
        eps: 1e-3,
        momentum: 0.01,
        ..Default::default()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn logits_to_decisions_applies_dueling_mask_formula() {
        let logits = vec![{
            let mut row = vec![0.0; 47];
            row[0] = 2.0;
            row[1 + 1] = 1.0;
            row[1 + 3] = 5.0;
            row
        }];
        let mut mask = vec![false; 46];
        mask[1] = true;
        mask[3] = true;
        let decisions = logits_to_decisions(logits, vec![mask.clone()]).unwrap();
        assert_eq!(decisions[0].action, 3);
        assert_eq!(decisions[0].mask, mask);
        assert!(decisions[0].q_values[0].is_infinite());
        assert!((decisions[0].q_values[1] - 0.0).abs() < 1e-6);
        assert!((decisions[0].q_values[3] - 4.0).abs() < 1e-6);
    }
}
