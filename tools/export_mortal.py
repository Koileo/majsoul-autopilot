#!/usr/bin/env python3
"""Export a Mortal .pth checkpoint to safetensors for the Rust runtime."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import torch
from safetensors.torch import save_file


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("checkpoint", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()

    state = torch.load(args.checkpoint, map_location="cpu")
    config = {
        "version": int(state["config"]["control"]["version"]),
        "conv_channels": int(state["config"]["resnet"]["conv_channels"]),
        "num_blocks": int(state["config"]["resnet"]["num_blocks"]),
        "obs_shape": [1012, 34],
        "action_space": 46,
    }
    if config["version"] != 4:
        raise SystemExit(f"only Mortal v4 export is supported now, got {config['version']}")

    tensors = {}
    for prefix, group_name in [("brain", "mortal"), ("dqn", "current_dqn")]:
        for name, tensor in state[group_name].items():
            if tensor.dtype == torch.int64:
                continue
            tensors[f"{prefix}.{name}"] = tensor.detach().contiguous()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    save_file(tensors, args.output_dir / "model.safetensors")
    (args.output_dir / "model_config.json").write_text(
        json.dumps(config, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
