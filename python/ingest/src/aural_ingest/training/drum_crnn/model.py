"""Compact CRNN for drum-onset transcription.

Reimplemented from the public description of the convolutional-recurrent
architecture used for automatic drum transcription by Vogl et al.,
"Towards multi-instrument drum transcription" (Proc. DAFx-18, 2018) and the
related line of ISMIR/DAFx CRNN work (Southall et al., ISMIR 2017). The design
is standard and described in those papers; NO code was copied from the ADTOF
repository (CC BY-NC-SA) or any NC / GPL source.

Shape of the idea (all reimplemented here in plain PyTorch):

  log-mel frames (T x M)
    -> a small stack of 2-D conv blocks (conv + BN + ReLU) with pooling on the
       MEL axis only, so the time axis keeps one output step per input frame;
    -> collapse the remaining (channels x mel) into a per-frame feature vector;
    -> a bi-directional GRU over the frame sequence for temporal context;
    -> a per-class linear layer producing one logit per drum class per frame.

Outputs are RAW LOGITS ``(batch, T, num_classes)``; callers apply sigmoid
(multi-label, one independent probability per class per frame). Training uses
``BCEWithLogitsLoss`` so the sigmoid stays fused with the loss for stability.
"""
from __future__ import annotations

import torch
from torch import nn

from .config import ModelConfig


class _ConvBlock(nn.Module):
    """conv(3x3) -> BatchNorm -> ReLU, then max-pool the mel axis by 2.

    Pooling is ``(1, 2)`` -- time stride 1 -- so the frame count is preserved
    end to end and the recurrent layer sees exactly one step per input frame.
    """

    def __init__(self, in_ch: int, out_ch: int) -> None:
        super().__init__()
        self.conv = nn.Conv2d(in_ch, out_ch, kernel_size=3, padding=1)
        self.bn = nn.BatchNorm2d(out_ch)
        self.act = nn.ReLU(inplace=True)
        self.pool = nn.MaxPool2d(kernel_size=(1, 2))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.pool(self.act(self.bn(self.conv(x))))


class DrumCRNN(nn.Module):
    """Conv feature stack -> BiGRU -> per-class per-frame logits."""

    def __init__(self, cfg: ModelConfig | None = None) -> None:
        super().__init__()
        self.cfg = cfg or ModelConfig()

        blocks: list[nn.Module] = []
        in_ch = 1
        mel = self.cfg.n_mels
        for out_ch in self.cfg.conv_channels:
            blocks.append(_ConvBlock(in_ch, out_ch))
            in_ch = out_ch
            mel = mel // 2  # each block halves the mel axis
        self.conv = nn.Sequential(*blocks)
        self._mel_after_conv = mel
        self._chan_after_conv = in_ch

        gru_in = in_ch * mel
        self.gru = nn.GRU(
            input_size=gru_in,
            hidden_size=self.cfg.gru_hidden,
            num_layers=self.cfg.gru_layers,
            batch_first=True,
            bidirectional=True,
            dropout=self.cfg.dropout if self.cfg.gru_layers > 1 else 0.0,
        )
        self.dropout = nn.Dropout(self.cfg.dropout)
        self.head = nn.Linear(2 * self.cfg.gru_hidden, self.cfg.num_classes)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """``x``: ``(batch, T, n_mels)`` log-mel -> logits ``(batch, T, C)``."""
        # -> (batch, 1, T, n_mels) for the 2-D conv stack.
        h = x.unsqueeze(1)
        h = self.conv(h)  # (batch, C_conv, T, mel')
        b, c, t, m = h.shape
        # (batch, T, C_conv * mel') per-frame feature vectors.
        h = h.permute(0, 2, 1, 3).reshape(b, t, c * m)
        h, _ = self.gru(h)  # (batch, T, 2*gru_hidden)
        h = self.dropout(h)
        return self.head(h)  # (batch, T, num_classes)


def count_parameters(model: nn.Module) -> int:
    """Total number of trainable parameters."""
    return sum(p.numel() for p in model.parameters() if p.requires_grad)
