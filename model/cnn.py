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
 
Window encoding
---------------
Each SingleChannelCNN receives an (ingress, egress) window pair.
The two windows are stacked as 2 input channels → Conv1d(in_channels=2).
This lets the network learn cross-channel interaction patterns
(e.g. correlated bursts) directly in the first layer.
 
Architecture per CNN
--------------------
  Conv1d(2 → C1, kernel=K1, stride=S1, padding=K1//2)   ReLU
  Conv1d(C1 → C2, kernel=K2, stride=S2, padding=K2//2)  ReLU
  AdaptiveAvgPool1d(4)    → fixed size regardless of window length
  Flatten → Linear(C2*4, fc_hidden)                       ReLU
 
Full model
----------
  [cnn_up(i_up, e_up), cnn_down(i_down, e_down)]  each → (batch, fc_hidden)
  Concatenate                                             → (batch, 2*fc_hidden)
  Linear(2*fc_hidden, 1)      → (batch, 1) raw logit
  [sigmoid applied by BCEWithLogitsLoss during training,
   or explicitly via torch.sigmoid() at inference]
"""
 
import torch
import torch.nn as nn
 
from config.hyperparams import MODEL
 
 
class SingleChannelCNN(nn.Module):
    """
    Processes one correlation channel: a pair of (ingress, egress) shape windows.
    Shared architecture between CNN_up and CNN_down, but independent weights.
    """
 
    def __init__(self,
                 window_len:    int = 30,                       # kept for API compat
                 conv1_filters: int = MODEL["conv1_filters"],
                 conv1_kernel:  int = MODEL["conv1_kernel"],
                 conv1_stride:  int = MODEL["conv1_stride"],
                 conv2_filters: int = MODEL["conv2_filters"],
                 conv2_kernel:  int = MODEL["conv2_kernel"],
                 conv2_stride:  int = MODEL["conv2_stride"],
                 fc_hidden:     int = MODEL["fc_hidden"]):
        super().__init__()
 
        p1       = conv1_kernel // 2
        p2       = conv2_kernel // 2
        pool_out = 4   # AdaptiveAvgPool1d target — gives fixed flatten size
 
        self.conv = nn.Sequential(
            nn.Conv1d(2, conv1_filters, conv1_kernel, stride=conv1_stride, padding=p1),
            nn.ReLU(),
            nn.Conv1d(conv1_filters, conv2_filters, conv2_kernel, stride=conv2_stride, padding=p2),
            nn.ReLU(),
            nn.AdaptiveAvgPool1d(pool_out),
        )
        self.fc = nn.Sequential(
            nn.Linear(conv2_filters * pool_out, fc_hidden),
            nn.ReLU(),
        )
 
    def forward(self,
                x_ingress: torch.Tensor,
                x_egress:  torch.Tensor) -> torch.Tensor:
        """
        Args:
            x_ingress: (batch, window_len)
            x_egress:  (batch, window_len)
        Returns:
            features:  (batch, fc_hidden)
        """
        x = torch.stack([x_ingress, x_egress], dim=1)  # (batch, 2, window_len)
        x = self.conv(x)            # (batch, conv2_filters, pool_out)
        x = x.flatten(start_dim=1) # (batch, conv2_filters * pool_out)
        return self.fc(x)           # (batch, fc_hidden)
 
 
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
 
        fc_hidden = cnn_kwargs.get("fc_hidden", MODEL["fc_hidden"])
        # No Sigmoid here — BCEWithLogitsLoss applies it internally during training.
        # Apply torch.sigmoid() explicitly at inference time (see flow_score).
        self.classifier = nn.Sequential(
            nn.Linear(2 * fc_hidden, 1),
        )
 
    def forward(self,
                ingress_up:   torch.Tensor,
                ingress_down: torch.Tensor,
                egress_up:    torch.Tensor,
                egress_down:  torch.Tensor) -> torch.Tensor:
        """
        Args: all tensors of shape (batch, window_len)
        Returns: (batch, 1) sigmoid scores
        """
        feat_up   = self.cnn_up(ingress_up, egress_up)       # (batch, fc_hidden)
        feat_down = self.cnn_down(ingress_down, egress_down)  # (batch, fc_hidden)
        combined  = torch.cat([feat_up, feat_down], dim=1)    # (batch, 2*fc_hidden)
        return self.classifier(combined)                       # (batch, 1)
 
    def flow_score(self,
                   ingress_up:   torch.Tensor,
                   ingress_down: torch.Tensor,
                   egress_up:    torch.Tensor,
                   egress_down:  torch.Tensor) -> float:
        """
        Aggregates window-wise scores into a single flow-level score
        by taking the mean across all windows. Used at inference time.
 
        Args: all tensors of shape (n_windows, window_len)
        Returns: scalar float in [0, 1]
        """
        self.eval()
        with torch.no_grad():
            logits = self.forward(ingress_up, ingress_down, egress_up, egress_down)
        return float(torch.sigmoid(logits).mean().item())