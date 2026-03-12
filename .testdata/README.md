# .testdata/

Small committed sample for pipeline smoke-testing.

Once you have your first real captures, add one flow pair here:

```
.testdata/
└── sample_nym_2024/
    ├── ingress.pcap          ← ~10s of ingress router capture
    ├── egress.pcap           ← ~10s of egress router capture
    └── labels.jsonl          ← single label record for this pair
```

Then anyone who clones the repo can immediately validate the full
preprocessing pipeline without needing Hetzner VMs:

```bash
python preprocessing/dataset_builder.py \
    --labels   .testdata/sample_nym_2024/labels.jsonl \
    --ingress_dir .testdata/sample_nym_2024 \
    --egress_dir  .testdata/sample_nym_2024 \
    --output   /tmp/smoke_test.npz

python analysis/visualize_shapes.py \
    --ingress .testdata/sample_nym_2024/ingress.pcap \
    --egress  .testdata/sample_nym_2024/egress.pcap \
    --mode nym
```

Keep files small — a few seconds of headers-only pcap is enough.
Snapshot length is 96 bytes so even 1000 packets ≈ 100KB.
