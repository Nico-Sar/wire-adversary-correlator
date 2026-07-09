#!/usr/bin/env python3
"""
scripts/campaign_heartbeat.py
==============================
Dead-man's-switch for the unattended campaign on leroy. Run every 30 min via
cron (see crontab entry below). Detects six failure modes, each tagged
CRITICAL or non-critical:
  1. run_campaign.sh process died (and campaign hasn't reported completion) — CRITICAL
  2. no file activity in the current round dir for STALE_LOG_HOURS         — CRITICAL
  3. run_campaign.sh alive but zero collector.coordinator processes active — CRITICAL
  4. ingress/egress router unreachable via SSH                            — CRITICAL
  5. a mode's success count frozen for STALL_CHECKS_FOR_ALERT checks      — non-critical
  6. router rx_dropped/rx_missed counters increased since last check      — non-critical

Two email channels (this replaced a per-check-failure email design after it
produced one email per cron tick whenever the set of active issues changed —
confirmed live: an 8-consecutive-check "frozen" alert fired repeatedly as
different mode combinations tripped it):
  - CRITICAL issues email immediately (every run they're detected — at most
    one per DIGEST_INTERVAL_HOURS repeat if the exact same signature persists
    unchanged, so a multi-day outage doesn't page every 30 min forever).
  - Everything else (non-critical issues, or a clean run) is folded into one
    HOURLY DIGEST email — sent once per DIGEST_INTERVAL_HOURS regardless of
    state, so there's always a steady "still alive" pulse without per-check
    noise.
Every run still appends one line to campaign_heartbeat.log and, if anything
is wrong, a timestamped file under alerts/.

Uses only the stdlib + `ssh`/`mail` CLIs — no venv/paramiko required, so cron
can invoke it with plain system python3.

Crontab (survives reboot — crond is a system service, not tied to login):
  */30 * * * * cd /volume1/scratch/r1086364/wire-adversary-correlator && \
    /usr/bin/python3 scripts/campaign_heartbeat.py >> campaign_heartbeat_cron.log 2>&1
"""

import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN_ROOT = REPO_ROOT / "data" / "campaign"
STATE_FILE = REPO_ROOT / ".campaign_heartbeat_state.json"
LOG_FILE = REPO_ROOT / "campaign_heartbeat.log"
ALERT_DIR = REPO_ROOT / "alerts"

_git_email = subprocess.run(
    ["git", "-C", str(REPO_ROOT), "config", "user.email"],
    capture_output=True, text=True,
).stdout.strip()
EMAIL_TO = _git_email or "nicolas.escolapios@gmail.com"

STALE_LOG_HOURS = 2          # no file activity this long => campaign presumed dead
STALL_CHECKS_FOR_ALERT = 3   # consecutive unchanged-count checks (~1.5h @ 30min cadence)
CRITICAL_REMIND_HOURS = 1    # re-email cadence for an UNCHANGED critical signature
DIGEST_INTERVAL_HOURS = 1    # routine "still alive" report cadence, sent regardless of state

# Dedicated, passphrase-free key — NOT ~/.ssh/nico-thesis. That key requires a
# passphrase normally supplied by an interactive ssh-agent session; cron has
# no access to that agent (no SSH_AUTH_SOCK), so every router check failed
# with "Permission denied (publickey,password)" and false-alerted on every
# cron-triggered run (confirmed live: 3 spurious alert emails before this was
# caught). This key is authorized only on the ingress/egress routers this
# script actually touches, for read-only commands (echo/ip -s link show).
SSH_KEY = os.path.expanduser("~/.ssh/heartbeat_monitor")
SSH_OPTS = ["-i", SSH_KEY, "-o", "StrictHostKeyChecking=no",
            "-o", "ConnectTimeout=8", "-o", "BatchMode=yes"]
INGRESS_IP = "204.168.184.30"
EGRESS_IP = "204.168.189.97"


def now_utc():
    return datetime.now(timezone.utc)


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {"mode_counts": {}, "mode_stall": {}, "router_drops": {},
            "alerts_active": {}, "last_critical_sent": {}, "last_digest_sent": None}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2))


def ssh_client(host, cmd, timeout=15):
    try:
        r = subprocess.run(["ssh"] + SSH_OPTS + [f"root@{host}", cmd],
                            capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0, r.stdout.strip(), r.stderr.strip()
    except Exception as e:
        return False, "", str(e)


def mode_coordinator_running(mode):
    """True if a collector.coordinator process for this mode is still active.

    A mode's success count is expected to stop moving once its coordinator
    exits cleanly for the round (e.g. vpn/tor/nym5 finish their gap-fill in
    minutes while nym2 is still catching up for hours) — that is not a
    stall, it's the mode being done until the next round. Only flag a
    frozen count when the process is still running but making no progress.
    """
    r = subprocess.run(["pgrep", "-f", f"collector.coordinator --mode {mode} "],
                        capture_output=True, text=True)
    return r.returncode == 0


def latest_round_dir():
    if not CAMPAIGN_ROOT.exists():
        return None
    rounds = sorted(p for p in CAMPAIGN_ROOT.glob("round_*") if p.is_dir())
    return rounds[-1] if rounds else None


def campaign_complete():
    """True if any tee'd campaign log shows the final 'all rounds passed' banner."""
    candidates = list(REPO_ROOT.glob("campaign_round*.log")) + list(Path("/tmp").glob("campaign_round*.log"))
    for f in candidates:
        try:
            if "Campaign complete: all" in f.read_text()[-2000:]:
                return True
        except Exception:
            continue
    return False


def main():
    critical = []      # immediate-email issues
    noncritical = []   # hourly-digest-only issues
    info = []
    state = load_state()
    ts = now_utc().isoformat()

    # 1. Campaign process alive? — CRITICAL
    proc_check = subprocess.run(["pgrep", "-f", "run_campaign.sh"], capture_output=True, text=True)
    campaign_alive = proc_check.returncode == 0

    round_dir = latest_round_dir()
    complete = campaign_complete()

    if not campaign_alive and not complete:
        critical.append("run_campaign.sh is NOT running and campaign has not reported "
                         "completion — possible silent death")

    # 2. Log/file freshness in the current round dir — CRITICAL
    if round_dir is not None and not complete:
        newest_mtime = 0.0
        for p in round_dir.rglob("*"):
            try:
                m = p.stat().st_mtime
                if m > newest_mtime:
                    newest_mtime = m
            except Exception:
                continue
        if newest_mtime > 0:
            age_h = (time.time() - newest_mtime) / 3600.0
            if age_h > STALE_LOG_HOURS:
                critical.append(f"no file activity in {round_dir.name} for {age_h:.1f}h "
                                 f"(> {STALE_LOG_HOURS}h threshold)")
        else:
            critical.append(f"{round_dir.name} exists but is empty — nothing collected yet")

    # 3. Coordinator processes running while campaign is active — CRITICAL
    coord_check = subprocess.run(["pgrep", "-fc", "collector.coordinator"], capture_output=True, text=True)
    n_coord = int(coord_check.stdout.strip() or 0)
    if campaign_alive and not complete and n_coord == 0:
        critical.append("run_campaign.sh is running but zero collector.coordinator processes are active")

    # 4. Routers reachable — CRITICAL
    ing_ok, _, ing_err = ssh_client(INGRESS_IP, "echo ok")
    if not ing_ok:
        critical.append(f"ingress router ({INGRESS_IP}) unreachable: {ing_err[:200]}")
    egr_ok, _, egr_err = ssh_client(EGRESS_IP, "echo ok")
    if not egr_ok:
        critical.append(f"egress router ({EGRESS_IP}) unreachable: {egr_err[:200]}")

    # 5. Per-mode success count frozen — non-critical (digest only)
    mode_counts_now = {}
    if round_dir is not None:
        for mode in ("vpn", "tor", "nym5", "nym2"):
            f = round_dir / f"{mode}_visits.jsonl"
            if not f.exists():
                continue
            success = 0
            try:
                with f.open() as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                        except Exception:
                            continue
                        if rec.get("visit_status") == "success":
                            success += 1
            except Exception:
                continue
            mode_counts_now[mode] = success

    prev_counts = state.get("mode_counts", {})
    prev_stall = state.get("mode_stall", {})
    new_stall = {}
    if not complete:
        for mode, count in mode_counts_now.items():
            if not mode_coordinator_running(mode):
                # No coordinator process for this mode right now — it already
                # finished its work for this round (or hasn't started the next
                # one yet). A frozen count here is expected, not a failure.
                new_stall[mode] = 0
                continue
            prev = prev_counts.get(mode)
            streak = prev_stall.get(mode, 0) + 1 if (prev is not None and count == prev) else 0
            new_stall[mode] = streak
            if streak >= STALL_CHECKS_FOR_ALERT:
                noncritical.append(f"{mode}: success count frozen at {count} for {streak} "
                                    f"consecutive checks (~{streak * 0.5:.1f}h) while its "
                                    f"coordinator process is still running")

    # 6. Router drops — delta since last check (cumulative counters, so only a
    #    NEW increase since the previous heartbeat is meaningful, not the raw
    #    value) — non-critical (digest only)
    drop_state = state.get("router_drops", {})
    new_drop_state = dict(drop_state)
    for name, ip, iface in (("ingress", INGRESS_IP, "enp7s0"), ("egress", EGRESS_IP, "enp7s0")):
        ok, out, _ = ssh_client(ip, f"ip -s link show {iface} | awk '/RX:/{{getline; print $4, $5}}'")
        if ok and out:
            try:
                dropped, missed = (int(x) for x in out.split())
                prev_d = drop_state.get(f"{name}_dropped")
                prev_m = drop_state.get(f"{name}_missed")
                if prev_d is not None and dropped > prev_d:
                    noncritical.append(f"{name} router: rx_dropped increased by {dropped - prev_d} since last check")
                if prev_m is not None and missed > prev_m:
                    noncritical.append(f"{name} router: rx_missed increased by {missed - prev_m} since last check")
                new_drop_state[f"{name}_dropped"] = dropped
                new_drop_state[f"{name}_missed"] = missed
            except Exception:
                pass

    state["mode_counts"] = mode_counts_now
    state["mode_stall"] = new_stall
    state["router_drops"] = new_drop_state

    round_label = round_dir.name if round_dir else "(no round dir)"
    counts_str = ", ".join(f"{m}={c}" for m, c in sorted(mode_counts_now.items())) or "no data"
    all_issues = critical + noncritical

    def send_mail(subject, body):
        try:
            p = subprocess.run(
                ["mail", "-s", subject, EMAIL_TO],
                input=body, capture_output=True, text=True,
                env={**os.environ, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
                timeout=30,
            )
            if p.returncode == 0:
                info.append(f"email sent to {EMAIL_TO}: {subject}")
                return True
            info.append(f"mail command failed rc={p.returncode}: {p.stderr[:300]}")
        except Exception as e:
            info.append(f"mail send exception: {e}")
        return False

    if complete:
        line = f"[{ts}] heartbeat OK — campaign COMPLETE — {counts_str}"
        print(line)
        with LOG_FILE.open("a") as fh:
            fh.write(line + "\n")
        state["alerts_active"] = {}
        save_state(state)
        return

    # ── Log line + alert file (every run, independent of emailing) ─────────
    if all_issues:
        body_lines = [f"[{ts}] {round_label} — {len(critical)} critical, {len(noncritical)} other"]
        body_lines += [f"  - [CRITICAL] {a}" for a in critical]
        body_lines += [f"  - {a}" for a in noncritical]
        body = "\n".join(body_lines)
        print(body)
        with LOG_FILE.open("a") as fh:
            fh.write(body + "\n")
        ALERT_DIR.mkdir(exist_ok=True)
        alert_file = ALERT_DIR / f"ALERT_{now_utc().strftime('%Y%m%dT%H%M%SZ')}.txt"
        alert_file.write_text(body + "\n")
    else:
        line = f"[{ts}] heartbeat OK — {round_label}, {counts_str}"
        print(line)
        with LOG_FILE.open("a") as fh:
            fh.write(line + "\n")

    # ── Channel 1: CRITICAL — immediate, deduped only if truly unchanged ───
    if critical:
        key = "|".join(sorted(critical))[:500]
        last_critical_sent = state.get("last_critical_sent", {})
        last_sent_ts = last_critical_sent.get("ts")
        last_sent_key = last_critical_sent.get("key")
        should_email = True
        if last_sent_key == key and last_sent_ts:
            hrs = (now_utc() - datetime.fromisoformat(last_sent_ts)).total_seconds() / 3600.0
            should_email = hrs >= CRITICAL_REMIND_HOURS

        if should_email:
            subject = f"[wire-adversary-correlator] CRITICAL — {round_label} — {len(critical)} issue(s)"
            body = "\n".join([f"[{ts}] {round_label}"] + [f"  - {a}" for a in critical])
            if send_mail(subject, body):
                last_critical_sent = {"ts": now_utc().isoformat(), "key": key}
        state["last_critical_sent"] = last_critical_sent

    # ── Channel 2: hourly digest — routine pulse, sent regardless of state ──
    last_digest_sent = state.get("last_digest_sent")
    should_digest = True
    if last_digest_sent:
        hrs = (now_utc() - datetime.fromisoformat(last_digest_sent)).total_seconds() / 3600.0
        should_digest = hrs >= DIGEST_INTERVAL_HOURS

    if should_digest:
        status_word = "OK" if not all_issues else f"{len(all_issues)} issue(s)"
        subject = f"[wire-adversary-correlator] Hourly report — {round_label} — {status_word}"
        digest_lines = [f"[{ts}] {round_label} — {counts_str}"]
        if critical:
            digest_lines += [f"  - [CRITICAL] {a}" for a in critical]
        if noncritical:
            digest_lines += [f"  - {a}" for a in noncritical]
        if not all_issues:
            digest_lines.append("  - all checks clean")
        if send_mail(subject, "\n".join(digest_lines)):
            state["last_digest_sent"] = now_utc().isoformat()

    state["alerts_active"] = {a: True for a in all_issues}
    save_state(state)

    if info:
        joined = "\n".join(info)
        print(joined)
        with LOG_FILE.open("a") as fh:
            fh.write(joined + "\n")


if __name__ == "__main__":
    main()
