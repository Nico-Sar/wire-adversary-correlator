# model/

Dual-CNN correlator (ShYSh architecture) applied to TCP-layer KDE shapes.

```
cnn.py      DualCNNCorrelator: two independent CNNs + FC sigmoid head
dataset.py  PyTorch Dataset: positive/negative Quartet pairs
train.py    Training loop — primary metric: PR-AUC
evaluate.py Evaluation: PR-AUC, ROC, PR curve, confusion matrix
```
