"""
model/cnn.py
============
Dual-CNN correlator following the ShYSh architecture.

Two independent CNNs process the two correlation channels:
  CNN_up:   correlates (ingress_up,   egress_up)
  CNN_down: correlates (ingress_down, egress_down)

Their outputs are concatenated and fed into a fully-connected stage
with a single sigmoid neuron to produce a window-wise correlation score.
Final flow-level score = mean of window-wise scores.

Reference: ShYSh paper, Section III-A-2 "Shape Comparison"
"""

import torch
import torch.nn as nn


class SingleChannelCNN(nn.Module):
    """
    Processes one correlation channel: a pair of (ingress, egress) shape windows.
    Shared architecture between CNN_up and CNN_down, but independent weights.
    """

    def __init__(self, window_len: int = 30,
                 conv1_filters: int = 32, conv1_kernel: int = 8, conv1_stride: int = 4,
                 conv2_filters: int = 64, conv2_kernel: int = 8, conv2_stride: int = 4,
                 fc_hidden: int = 128):
        super().__init__()
        raise NotImplementedError

    def forward(self, x_ingress: torch.Tensor,
                x_egress: torch.Tensor) -> torch.Tensor:
        """
        Args:
            x_ingress: (batch, window_len)
            x_egress:  (batch, window_len)
        Returns:
            features:  (batch, fc_hidden)
        """
        raise NotImplementedError


class DualCNNCorrelator(nn.Module):
    """
    Full dual-CNN correlator.
    Input: Quartet windows (ingress_up, ingress_down, egress_up, egress_down)
    Output: scalar correlation score in [0, 1] per window pair
    """

    def __init__(self, **cnn_kwargs):
        super().__init__()
        self.cnn_up   = SingleChannelCNN(**cnn_kwargs)
        self.cnn_down = SingleChannelCNN(**cnn_kwargs)
        raise NotImplementedError  # classifier head

    def forward(self,
                ingress_up:   torch.Tensor,
                ingress_down: torch.Tensor,
                egress_up:    torch.Tensor,
                egress_down:  torch.Tensor) -> torch.Tensor:
        """
        Args: all tensors of shape (batch, window_len)
        Returns: (batch, 1) sigmoid scores
        """
        raise NotImplementedError

    def flow_score(self,
                   ingress_up:   torch.Tensor,
                   ingress_down: torch.Tensor,
                   egress_up:    torch.Tensor,
                   egress_down:  torch.Tensor) -> float:
        """
        Aggregates window-wise scores into a single flow-level score
        by taking the mean across all windows. Used at inference time.
        """
        raise NotImplementedError
