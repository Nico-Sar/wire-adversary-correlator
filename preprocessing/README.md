# preprocessing/

PCAP → model-ready tensor pipeline.

```
pcap_parser.py      raw pcap → packet list [{ts, size, direction}]
       ↓
kde.py              timestamps → Gaussian KDE density wave
       ↓
windower.py         density wave → overlapping windows  +  pcap time carving
       ↓
quartet_builder.py  assembles (ingress_up, ingress_down, egress_up, egress_down)
       ↓
dataset_builder.py  metadata.jsonl + pcaps → .npz dataset
```
