import platform
import shutil
import sys
import torch
import pathlib
import traceback
import numpy as np

from torch import nn, Tensor
from functools import partial


def _ensure_libriichi_extension() -> None:
    base_dir = pathlib.Path(__file__).resolve().parent
    py_tag = f"{sys.version_info.major}.{sys.version_info.minor}"
    machine = platform.machine().lower()
    if sys.platform == "darwin":
        target = "aarch64-apple-darwin" if machine in {"arm64", "aarch64"} else "x86_64-apple-darwin"
        extension_name = "libriichi.so"
    elif sys.platform.startswith("linux"):
        target = "x86_64-unknown-linux-gnu"
        extension_name = "libriichi.so"
    elif sys.platform.startswith("win"):
        target = "x86_64-pc-windows-msvc"
        extension_name = "libriichi.pyd"
    else:
        raise RuntimeError(f"Unsupported platform for libriichi: {sys.platform} {machine}")

    source = base_dir / "libriichi" / f"libriichi-{py_tag}-{target}.{extension_name.rsplit('.', 1)[1]}"
    destination = base_dir / extension_name
    if not source.exists():
        raise FileNotFoundError(f"Missing libriichi binary for {py_tag}/{target}: {source}")
    if not destination.exists() or source.read_bytes() != destination.read_bytes():
        shutil.copyfile(source, destination)


_ensure_libriichi_extension()

from .libriichi.mjai import Bot
from .libriichi.consts import obs_shape, ACTION_SPACE
from .logger import logger

class ChannelAttention(nn.Module):
    def __init__(self, channels, ratio=16, actv_builder=nn.ReLU, bias=True):
        super().__init__()
        self.shared_mlp = nn.Sequential(
            nn.Linear(channels, channels // ratio, bias=bias),
            actv_builder(),
            nn.Linear(channels // ratio, channels, bias=bias),
        )
        if bias:
            for mod in self.modules():
                if isinstance(mod, nn.Linear):
                    nn.init.constant_(mod.bias, 0)

    def forward(self, x: Tensor):
        avg_out = self.shared_mlp(x.mean(-1))
        max_out = self.shared_mlp(x.amax(-1))
        weight = (avg_out + max_out).sigmoid()
        x = weight.unsqueeze(-1) * x
        return x

class ResBlock(nn.Module):
    def __init__(
        self,
        channels,
        *,
        norm_builder = nn.Identity,
        actv_builder = nn.ReLU,
        pre_actv = False,
    ):
        super().__init__()
        self.pre_actv = pre_actv

        if pre_actv:
            self.res_unit = nn.Sequential(
                norm_builder(),
                actv_builder(),
                nn.Conv1d(channels, channels, kernel_size=3, padding=1, bias=False),
                norm_builder(),
                actv_builder(),
                nn.Conv1d(channels, channels, kernel_size=3, padding=1, bias=False),
            )
        else:
            self.res_unit = nn.Sequential(
                nn.Conv1d(channels, channels, kernel_size=3, padding=1, bias=False),
                norm_builder(),
                actv_builder(),
                nn.Conv1d(channels, channels, kernel_size=3, padding=1, bias=False),
                norm_builder(),
            )
            self.actv = actv_builder()
        self.ca = ChannelAttention(channels, actv_builder=actv_builder, bias=True)

    def forward(self, x):
        out = self.res_unit(x)
        out = self.ca(out)
        out = out + x
        if not self.pre_actv:
            out = self.actv(out)
        return out

class ResNet(nn.Module):
    def __init__(
        self,
        in_channels,
        conv_channels,
        num_blocks,
        *,
        norm_builder = nn.Identity,
        actv_builder = nn.ReLU,
        pre_actv = False,
    ):
        super().__init__()

        blocks = []
        for _ in range(num_blocks):
            blocks.append(ResBlock(
                conv_channels,
                norm_builder = norm_builder,
                actv_builder = actv_builder,
                pre_actv = pre_actv,
            ))

        layers = [nn.Conv1d(in_channels, conv_channels, kernel_size=3, padding=1, bias=False)]
        if pre_actv:
            layers += [*blocks, norm_builder(), actv_builder()]
        else:
            layers += [norm_builder(), actv_builder(), *blocks]
        layers += [
            nn.Conv1d(conv_channels, 32, kernel_size=3, padding=1),
            actv_builder(),
            nn.Flatten(),
            nn.Linear(32 * 34, 1024),
        ]
        self.net = nn.Sequential(*layers)

    def forward(self, x):
        return self.net(x)

class Brain(nn.Module):
    def __init__(self, *, conv_channels, num_blocks, version=1):
        super().__init__()
        self.version = version

        in_channels = obs_shape(version)[0]

        norm_builder = partial(nn.BatchNorm1d, conv_channels, momentum=0.01)
        actv_builder = partial(nn.Mish, inplace=True)
        pre_actv = True

        match version:
            case 1:
                actv_builder = partial(nn.ReLU, inplace=True)
                pre_actv = False
                self.latent_net = nn.Sequential(
                    nn.Linear(1024, 512),
                    nn.ReLU(inplace=True),
                )
                self.mu_head = nn.Linear(512, 512)
                self.logsig_head = nn.Linear(512, 512)
            case 2:
                pass
            case 3 | 4:
                norm_builder = partial(nn.BatchNorm1d, conv_channels, momentum=0.01, eps=1e-3)
            case _:
                raise ValueError(f'Unexpected version {self.version}')

        self.encoder = ResNet(
            in_channels = in_channels,
            conv_channels = conv_channels,
            num_blocks = num_blocks,
            norm_builder = norm_builder,
            actv_builder = actv_builder,
            pre_actv = pre_actv,
        )
        self.actv = actv_builder()

        # always use EMA or CMA when True
        self._freeze_bn = False

    def forward(self, obs: Tensor):
        phi = self.encoder(obs)

        match self.version:
            case 1:
                latent_out = self.latent_net(phi)
                mu = self.mu_head(latent_out)
                logsig = self.logsig_head(latent_out)
                return mu, logsig
            case 2 | 3 | 4:
                return self.actv(phi)
            case _:
                raise ValueError(f'Unexpected version {self.version}')

    def train(self, mode=True):
        super().train(mode)
        if self._freeze_bn:
            for mod in self.modules():
                if isinstance(mod, nn.BatchNorm1d):
                    mod.eval()
                    # I don't think this benefits
                    # module.requires_grad_(False)
        return self

    def reset_running_stats(self):
        for mod in self.modules():
            if isinstance(mod, nn.BatchNorm1d):
                mod.reset_running_stats()

    def freeze_bn(self, value: bool):
        self._freeze_bn = value
        return self.train(self.training)

class DQN(nn.Module):
    def __init__(self, *, version=1):
        super().__init__()
        self.version = version
        match version:
            case 1:
                self.v_head = nn.Linear(512, 1)
                self.a_head = nn.Linear(512, ACTION_SPACE)
            case 2 | 3:
                hidden_size = 512 if version == 2 else 256
                self.v_head = nn.Sequential(
                    nn.Linear(1024, hidden_size),
                    nn.Mish(inplace=True),
                    nn.Linear(hidden_size, 1),
                )
                self.a_head = nn.Sequential(
                    nn.Linear(1024, hidden_size),
                    nn.Mish(inplace=True),
                    nn.Linear(hidden_size, ACTION_SPACE),
                )
            case 4:
                self.net = nn.Linear(1024, 1 + ACTION_SPACE)
                nn.init.constant_(self.net.bias, 0)

    def forward(self, phi, mask):
        if self.version == 4:
            v, a = self.net(phi).split((1, ACTION_SPACE), dim=-1)
        else:
            v = self.v_head(phi)
            a = self.a_head(phi)
        a_sum = a.masked_fill(~mask, 0.).sum(-1, keepdim=True)
        mask_sum = mask.sum(-1, keepdim=True)
        a_mean = a_sum / mask_sum
        q = (v + a - a_mean).masked_fill(~mask, -torch.inf)
        return q


class MortalEngine:
    def __init__(
        self,
        brain,
        dqn,
        version,
        device = None,
        name = 'NoName',
    ):
        self.engine_type = 'mortal'
        self.device = device or torch.device('cpu')
        assert isinstance(self.device, torch.device)
        self.brain = brain.to(self.device).eval()
        self.dqn = dqn.to(self.device).eval()
        self.is_oracle = False
        self.version = version
        self.enable_quick_eval = False
        self.enable_rule_based_agari_guard = True
        self.name = name

    def react_batch(self, obs, masks, invisible_obs):
        try:
            with torch.inference_mode():
                return self._react_batch(obs, masks, invisible_obs)
        except Exception as ex:
            raise Exception(f'{ex}\n{traceback.format_exc()}')

    def _react_batch(self, obs, masks, invisible_obs):
        obs = torch.as_tensor(np.stack(obs, axis=0), device=self.device)
        masks = torch.as_tensor(np.stack(masks, axis=0), device=self.device)
        batch_size = obs.shape[0]

        match self.version:
            case 1:
                mu, _logsig = self.brain(obs)
                q_out = self.dqn(mu, masks)
            case 2 | 3 | 4:
                phi = self.brain(obs)
                q_out = self.dqn(phi, masks)

        is_greedy = torch.ones(batch_size, dtype=torch.bool, device=self.device)
        actions = q_out.argmax(-1)

        return actions.tolist(), q_out.tolist(), masks.tolist(), is_greedy.tolist()

def load_model(seat: int) -> Bot:
    # check if GPU is available
    if torch.cuda.is_available():
        device = torch.device('cuda')
    else:
        device = torch.device('cpu')

    # Load model path from settings (configurable), fallback to mortal.pth
    from settings.settings import settings as app_settings
    control_state_file = getattr(app_settings, 'model_path', '../../mortal.pth')
    control_state_file = pathlib.Path(control_state_file)

    # Support absolute path or relative to project root
    if not control_state_file.is_absolute():
        control_state_file = pathlib.Path.cwd() / control_state_file
    state = torch.load(control_state_file, map_location=device)

    mortal = Brain(version=state['config']['control']['version'], conv_channels=state['config']['resnet']['conv_channels'], num_blocks=state['config']['resnet']['num_blocks']).eval()
    dqn = DQN(version=state['config']['control']['version']).eval()
    mortal.load_state_dict(state['mortal'])
    dqn.load_state_dict(state['current_dqn'])

    engine = MortalEngine(
        mortal,
        dqn,
        version = state['config']['control']['version'],
        device = device,
        name = 'mortal',
    )

    bot = Bot(engine, seat)
    return bot
