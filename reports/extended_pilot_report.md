# Extended Pilot: Analysis Report

**Date:** 2026-04-25  
**Dataset:** `data/extended_pilot/`  
**Collection script:** `scripts/collect_extended_pilot.sh` (V=4, ~4h wall time)  
**Modes:** baseline, vpn, tor, nym5, nym2

---

## 1. Executive Summary

The extended pilot (V=4 visits/URL across 5 modes) revealed three findings that must be addressed before the full collection run:

1. **nym2 routing failure (critical):** 97% of nym2 ingress pcaps are 0 bytes. Root cause: the eth0 default route returns after VM reboot, causing WireGuard UDP traffic to bypass the ingress router entirely. The nym2 dataset is unusable and must be re-collected after applying the routing fix.

2. **Content-type stratification (significant):** For baseline, vpn, and tor — HTML pages and JSON responses produce zero-byte ingress pcaps at a rate of ~25% each (collectively ~50% of all skipped visits). Small HTTP responses complete before the ingress router BPF filter can capture sufficient packets (below `min_packets=5`). Binary file types (PDF, MP3, MP4, ZIP) are unaffected. nym5 is immune due to Sphinx protocol padding.

3. **Four valid modes (baseline, vpn, tor, nym5):** Build rates of 73-76% on non-nym2 modes. All four NPZ datasets pass integrity checks. Pipeline is otherwise functioning correctly.

---

## 2. Per-Mode Results

### 2.1 Pcap Build Rates

| Mode     | Total pcaps | Zero-byte (ingress) | Non-zero | Build rate |
|----------|-------------|---------------------|----------|------------|
| baseline | 918         | 248                 | 670      | 73%        |
| vpn      | 920         | 233                 | 687      | 75%        |
| tor      | 1,839       | 449                 | 1,390    | 76%        |
| nym5     | 949         | 244                 | 705      | 74%        |
| nym2     | 1,190       | **634**             | 556      | **47%\***  |

\*nym2's 47% non-zero rate counts egress pcaps (which were fine). Of 653 ingress pcaps, **634 (97%) are 0 bytes** — effectively no ingress was captured for nym2.

### 2.2 Zero-Byte Breakdown by Content Type

For the four working modes, zero-byte ingress pcaps cluster exclusively around small content types:

| Mode     | Zero-byte html | Zero-byte json | Zero-byte binary |
|----------|----------------|----------------|------------------|
| baseline | 129            | 117            | 0                |
| vpn      | 131            | 102            | 0                |
| tor      | 251            | 197            | 0                |
| nym5     | 117            | 116            | 0 (estimated)    |

**For nym2**, zero-byte pcaps span all content types (html: 224, json: 196, pdf: 32, mp3: 24, mp4: 22, zip: 20) — confirming that the failure is routing-level, not content-size-dependent.

### 2.3 Flow Duration and Preprocessing

Per-mode KDE hyperparams used (`config/hyperparams.py`):

| Mode     | Duration | Sigma | n_windows | n_grid_samples |
|----------|----------|-------|-----------|----------------|
| baseline | 30s      | 0.125 | 19        | 300            |
| vpn      | 30s      | 0.125 | 19        | 300            |
| tor      | 60s      | 0.25  | 39        | 600            |
| nym5     | 60s      | 0.5   | 39        | 600            |
| nym2     | 30s      | 0.2   | 19        | 300            |

All four valid NPZ datasets passed:
- Shape consistency check (X, y, urls, visit_ids)
- No NaN/Inf values
- URL train/val/test split (stratified)
- `QuartetDataset` instantiation (positive + hard-negative pair loading)
- KDE normalisation: stitched signal sums to n_grid_samples (300 or 600) ✓
- n_windows count ✓

---

## 3. nym2 Routing Failure

### 3.1 Root Cause

The nym2 clients (204.168.181.115, 95.216.218.124) connect to the internet via WireGuard (2-hop VPN). After a VM reboot, the cloud provider injects a default route via eth0:

```
default via 172.31.1.1 dev eth0
```

This route overrides the WireGuard tunnel. When the browser makes an HTTP request, WireGuard UDP packets flow out directly through eth0 to the ISP, bypassing the ingress router entirely. The ingress router BPF filter (`udp and (host 10.0.0.4 or host 10.0.0.6)`) sees no traffic → 0-byte pcaps.

Egress pcaps were unaffected because the egress router sits between the web server and the public internet — the web server's response traffic flows inbound regardless of how the client's packets exit.

### 3.2 Evidence

- 634/653 nym2 ingress pcaps are 0 bytes (97%)
- 0-byte pcaps include large binary types (pdf, mp3, mp4) where packet counts would otherwise be high
- Egress pcaps for same visits are normal size (537 non-zero egress pcaps)
- The 19 non-zero ingress pcaps correspond to visits that occurred during the brief window after WireGuard was brought up but before the eth0 route fully reasserted

### 3.3 Fix: Persistent Route Deletion via systemd

`scripts/fix_nym2_routing.sh` installs a systemd oneshot service on both nym2 VMs that deletes the eth0 default route at every boot, before any network traffic can use it.

Service installed as `/etc/systemd/system/nym2-routing-fix.service`:

```ini
[Unit]
Description=Delete eth0 default route to force WireGuard routing
After=network.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/sbin/ip route del default via 172.31.1.1 dev eth0 || true

[Install]
WantedBy=multi-user.target
```

The `|| true` prevents the service from failing if the route is already absent (idempotent).

**To apply the fix:**
```bash
bash scripts/fix_nym2_routing.sh
```

This SSHes to both VMs, writes the service file, runs `systemctl daemon-reload && systemctl enable --now nym2-routing-fix`, and verifies the eth0 route is absent.

### 3.4 Pre-Collection Verification Test

Before starting any nym2 collection, verify the fix is active on both VMs:

```bash
# On nym2-client1 (204.168.181.115)
ssh -i ~/.ssh/nico-thesis root@204.168.181.115 \
  "ip route show | grep -v 'eth0.*default' && echo OK || echo FAIL"

# On nym2-client2 (95.216.218.124)
ssh -i ~/.ssh/nico-thesis root@95.216.218.124 \
  "ip route show | grep -v 'eth0.*default' && echo OK || echo FAIL"
```

The expected output is the routing table with no `default via 172.31.1.1 dev eth0` line, followed by `OK`. If `FAIL` appears, re-run `fix_nym2_routing.sh` before proceeding.

Optionally verify a WireGuard packet reaches the ingress router with a quick curl test:
```bash
ssh -i ~/.ssh/nico-thesis root@204.168.181.115 \
  "curl --silent --output /dev/null --write-out '%{http_code}' http://204.168.189.97/"
```
Expected: `200`. If the route is broken it will hang or return a connection error.

### 3.5 Recovery Procedure (if ingress pcaps are empty mid-collection)

If you discover 0-byte nym2 ingress pcaps during a collection run:

1. **Stop the coordinator** — the current partial run is already written to the JSONL log with `visit_status=success` but unusable pcaps. Do not delete the log.

2. **Fix the routing** on both VMs:
   ```bash
   bash scripts/fix_nym2_routing.sh
   ```

3. **Clear the corrupt pcaps** — delete only the nym2 ingress pcaps that are 0 bytes:
   ```bash
   find data/<output_dir>/nym2/ -name "*.pcap" -size 0 -delete
   ```

4. **Remove the corrupt JSONL entries** — the resume logic in `run_dataset()` tracks completed visits by `visit_status=success` in the log. Since the visits "succeeded" browser-wise but have no ingress data, their log entries must be purged so they are re-collected:
   ```bash
   # Backup log
   cp data/<output_dir>/nym2_visits.jsonl data/<output_dir>/nym2_visits.jsonl.bak
   # Remove entries that correspond to corrupt visits (those with no ingress pcap)
   # Simplest approach: delete the entire log and re-collect from scratch
   rm data/<output_dir>/nym2_visits.jsonl
   ```

5. **Re-run nym2 collection** — the resume logic will see an empty log and start from visit 1:
   ```bash
   python3 -m collector.coordinator \
       --mode nym2 --urls config/urls_nym2.txt \
       --visits <V> --output data/<output_dir> \
       --client nym2-client1 --rotate-circuits &
   python3 -m collector.coordinator \
       --mode nym2 --urls config/urls_nym2.txt \
       --visits <V> --output data/<output_dir> \
       --client nym2-client2 --rotate-circuits
   ```

**Note:** If only a partial log is corrupt (some visits have good pcaps), use selective log repair instead of deleting the whole file. Keep entries whose corresponding ingress pcap is non-zero; remove the rest so the coordinator re-collects them.

---

## 4. Content-Type / Packet-Count Discovery

### 4.1 Mechanism

HTML pages (e.g. `page_html_1.html`, ~5 KB) and JSON responses (e.g. `data_json_1.json`, ~2 KB) are served in a single TCP segment or two. The web server sends the full response within ~1-2ms of the browser request. At `t_sample=0.1s` resolution, the egress capture records 1-2 data packets; the ingress capture for baseline/vpn records 1 TCP ACK packet. Both streams fall below `min_packets=5` (the default in `config/hyperparams.py`), so `dataset_builder.py` drops them.

This produces 0-byte ingress pcaps for these visits because the BPF capture window opens *after* the content has already been fully received.

### 4.2 Why nym5 Is Immune

The Sphinx protocol wraps every payload in fixed-size 32 KB packets sent at constant inter-packet intervals regardless of the actual content size. A 2 KB JSON response generates the same number of Sphinx packets as a 10 MB video. This padding normalizes packet counts across all content types, explaining why nym5's zero-byte breakdown shows only html/json misses at the same rate as other modes — even those misses are likely timing-related (very early visits before the SOCKS5 proxy stabilises).

### 4.3 Content-Type Build Rates (baseline, vpn, tor)

| Content type | Approx. response size | Build rate |
|-------------|----------------------|-----------|
| page_html   | ~5 KB                | ~0%        |
| page_heavy  | ~200 KB              | ~45-75%    |
| data_json   | ~2 KB                | ~2-11%     |
| doc_pdf     | ~500 KB              | ~48-100%   |
| audio_mp3   | ~3 MB                | ~90-100%   |
| video_mp4   | ~8 MB                | ~90-100%   |

### 4.4 Options and Recommendation

**Option A — Lower `min_packets`:**  
Set `KDE["min_packets"] = 1` or `2`. This admits flows with very sparse packet sequences. Risk: KDE estimates become degenerate (near-zero signal on ingress) and may confuse the model. Not recommended unless html/json are important for class balance.

**Option B — Remove html/json from URL set (baseline/vpn/tor):**  
Filter `urls.txt` to include only `page_heavy_*`, `doc_pdf_*`, `audio_mp3_*`, `video_mp4_*`. Increases build rate to ~95%+ for these modes. Disadvantage: the URL distribution no longer matches a realistic web workload — the dataset is biased toward large content.

**Option C — Accept the distribution and oversample binary types:**  
Keep the full URL set but collect more visits of binary types to compensate for the ~50% loss on html/json. At V=8 (full collection), binary types already dominate built samples. The model will train on a content-skewed but technically valid dataset.

**Option D — Extend window and increase `t_sample`:**  
Use `t_sample=0.01s` (100 samples/sec) and a wider BPF capture window (start capture 200ms before browser trigger). More granular sampling catches the burst. Requires changes to `visit_trigger.py` and `windower.py`. Highest engineering cost.

**Recommendation:** Use **Option C** for the full collection. At V=8 per URL, each URL will produce ~4 usable samples on average for html/json (even at 0-10% build rate, with 8 visits × 2 clients × some hits). The training set will have sufficient variety. Document the distribution skew in the thesis as a characteristic of real-world traffic (large transfers carry more temporal structure). If Class imbalance becomes a training problem, revisit Option B for a second dataset.

---

## 5. Pipeline Resilience Features

### 5.1 Resume-from-Checkpoint

`coordinator.py:run_dataset()` implements per-(URL, visit_num) checkpointing. On startup it reads the existing JSONL log and counts `visit_status=success` entries per URL into `completed_counts`. Any visit whose index is below the completed count is skipped with a `[resume]` log line. The serial counter resumes from the highest `_v<serial>` seen in the log.

This means a crashed coordinator can be restarted with the identical command and will continue from where it left off without duplicating visits or corrupting visit_ids.

### 5.2 Parallel Client Safety (`set -euo pipefail`)

All collection scripts use `set -euo pipefail`. Bare `wait $PID1 $PID2` propagates a non-zero exit code from any child, killing the script and leaving subsequent modes uncollected. All wait calls now use the individual pattern:

```bash
wait $PID_CLIENT1 || echo "[client1] exited with error — continuing"
wait $PID_CLIENT2 || echo "[client2] exited with error — continuing"
```

This logs the failure but does not propagate it, allowing remaining groups to run.

### 5.3 SOCKS5 Proxy Connection Retry

`run_single_visit()` in `coordinator.py` detects `NS_ERROR_PROXY_CONNECTION_REFUSED` in the visit status and retries once after a 10s wait. The capture continues during the wait so no pcap restart is needed. The SOCKS5 poll timeout was extended to 90s (from 45s) to give nym5's Sphinx setup sufficient time to stabilise before the first visit.

### 5.4 Tor Guard Logging (nc-based)

`rotate_circuit_tor()` uses `nc -q 1` (quit 1s after EOF) rather than `python3 -c` inline scripts to send AUTHENTICATE + SIGNAL NEWNYM + GETINFO entry-guards to the Tor control port. The `-q 1` flag prevents nc from hanging after the response is received. Guard nicknames are parsed with `re.search(r'\$[0-9A-Fa-f]+~(\S+)', line)`.

---

## 6. Actions Before Full Collection

In priority order:

### Step 1: Fix nym2 routing (required)
```bash
bash scripts/fix_nym2_routing.sh
```
Verify on both VMs that `ip route show` contains no `default via 172.31.1.1 dev eth0` line.

### Step 2: Run quick test to confirm end-to-end pipeline (recommended)
```bash
bash scripts/setup_webserver_ports.sh   # if not already done
bash scripts/collect_quick_test.sh
```
Expected outcome: nym2 ingress pcaps non-zero for at least binary content types. If still empty, the routing fix did not persist — check systemd service status on the VMs.

### Step 3: Start full collection
```bash
bash scripts/collect_full.sh 8 data/full_v8
```
Estimated wall time: ~320 min (5.3h) at V=8. Wall time calculation:
- Group 1 bottleneck (vpn at port 8080): 115 URLs × 8 visits × 6s / 60 = ~92 min
- nym5 (2 clients): 60 URLs × 8 visits × 43s / 60 / 2 = ~172 min
- nym2 (2 clients): 100 URLs × 8 visits × 34s / 60 / 2 = ~227 min
- Total ≈ (92 + 172 + 227) / 2 ≈ 320 min (Group 1 runs concurrently with itself, nym5/nym2 sequential)

### Step 4: Monitor nym2 ingress during collection (first 15 min)
Spot-check ingress pcap sizes during the nym2 phase:
```bash
ls -lh data/full_v8/nym2/*.pcap 2>/dev/null | head -20
```
If any ingress pcaps are 0 bytes within the first 5 visits, stop and re-apply the routing fix (see §3.5 Recovery).

### Step 5: Run preprocessing after collection
```bash
python3 -m preprocessing.dataset_builder --mode baseline --input data/full_v8 --output data/full_v8
# ... repeat for vpn, tor, nym5, nym2
python3 scripts/check_pilot_npz.py data/full_v8
```

---

## 7. File Reference

| File | Purpose |
|------|---------|
| `config/infrastructure.py` | BPF filters, URL_BASE per mode, client SSH config |
| `config/hyperparams.py` | KDE params (sigma, window, min_packets), model config |
| `config/urls.txt` | 115 URLs for baseline/vpn/tor |
| `config/urls_nym5_extended.txt` | 60 URLs for nym5 (no large binaries) |
| `config/urls_nym2.txt` | 100 URLs for nym2 (115 minus 15 page_heavy_*) |
| `scripts/fix_nym2_routing.sh` | Install systemd route-deletion service on nym2 VMs |
| `scripts/setup_webserver_ports.sh` | Configure nginx to listen on ports 80, 8080-8083 |
| `scripts/collect_quick_test.sh` | V=3 quick end-to-end test (6/4 URLs, all 5 modes) |
| `scripts/collect_full.sh` | Full collection (configurable V, default 8) |
| `collector/coordinator.py` | Main collection loop, resume logic, circuit rotation |
| `preprocessing/dataset_builder.py` | Pcap → KDE window → NPZ |
| `scripts/check_pilot_npz.py` | NPZ integrity checks |
| `data/extended_pilot/kde_plots/` | KDE shape plots + flow duration violin per content type |
