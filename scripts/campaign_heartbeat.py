#!/usr/bin/env python3
"""
scripts/campaign_heartbeat.py
==============================
Dead-man's-switch for the unattended campaign on leroy. Run every 30 min via
cron (see crontab entry below). Detects six failure modes, each tagged
CRITICAL or non-critical:
  1. an orchestrator that's alive for a root but launching no coordinators — CRITICAL
  2. no file activity in an active root's current round dir for STALE_LOG_HOURS — CRITICAL
  3. a root's orchestrator is dead but that root hasn't reported completion   — CRITICAL
  4. ingress/egress router unreachable via SSH                               — CRITICAL
  5. a mode's success count frozen for STALL_CHECKS_FOR_ALERT checks         — non-critical
  6. router rx_dropped/rx_missed counters increased since last check         — non-critical

Multi-root (2026-07-12, patches/10_nym5_instance_separation_design.md): the
campaign split into independent instances, each with its own root
(data/campaign_fast, data/campaign_nym5) plus the original data/campaign,
which is now permanently idle for new rounds but still legitimately
finishing its round_03 transitional tail via a client working directly
against it (no run_campaign.sh orchestrator attached to it anymore -- that
was launched by a direct run_stage.sh invocation, not the campaign loop).
CAMPAIGN_ROOTS below lists every root to watch; override via the
CAMPAIGN_ROOTS env var (colon-separated paths) if the set changes again.

Root-level ACTIVE vs IDLE, and why it matters: a root with NO orchestrator
process and NO coordinator process currently referencing it is IDLE, not
FROZEN -- of course no new files are appearing, nothing is trying to write
any. Only a root where something (an orchestrator that should be
launching clients, or a coordinator that should be producing new visits)
IS supposed to be running right now, but isn't producing anything, is a
real stall. This is the same principle the existing per-mode stall check
(#5, "not mode_coordinator_running() => stall=0, not a failure") already
uses -- generalized here to the root level, which is what actually lets
data/campaign go idle-but-fine after round_03 without paging anyone.

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
_default_roots = ["data/campaign", "data/campaign_fast", "data/campaign_nym5"]
_roots_env = os.environ.get("CAMPAIGN_ROOTS")
CAMPAIGN_ROOTS = [REPO_ROOT / p for p in
                  (_roots_env.split(":") if _roots_env else _default_roots)]
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


def _pgrep_af(pattern):
    """Returns a list of full command-line strings (one per matching process)."""
    r = subprocess.run(["pgrep", "-af", pattern], capture_output=True, text=True)
    return r.stdout.splitlines() if r.returncode == 0 else []


def root_match_str(root: Path) -> str:
    """The path string as it actually appears in a process command line.

    CAMPAIGN_ROOTS holds absolute paths (needed for reliable filesystem
    access regardless of cron's CWD), but every script in this repo is
    always invoked with paths relative to the repo root (e.g.
    "data/campaign_fast", never the full /volume1/.../data/campaign_fast).
    Matching the absolute Path's str() against process command lines
    therefore never matches anything -- confirmed live (2026-07-12): every
    root reported "idle" and fired a false "campaign silently died"
    CRITICAL, because none of the absolute-path strings matched the
    relative-path arguments actually on the command line. This resolves
    the root back to repo-root-relative for matching purposes only;
    filesystem calls elsewhere keep using the absolute Path directly.
    """
    return os.path.relpath(root, REPO_ROOT)


def orchestrator_lines_for_root(root: Path):
    """run_campaign.sh invocations whose CAMPAIGN_ROOT argument is this root.

    Exact whitespace-token match, NOT substring — "data/campaign" is a
    literal substring of "data/campaign_fast" and "data/campaign_nym5", so
    naive substring matching would misattribute every fast/nym5-instance
    process to the old (now-idle-for-new-rounds) root. Args are
    whitespace-separated, so comparing against ln.split() rather than `in ln`
    closes it.
    """
    root_str = root_match_str(root)
    return [ln for ln in _pgrep_af("run_campaign.sh") if root_str in ln.split()]


def coordinator_lines_for_root(root: Path):
    """collector.coordinator invocations whose --output argument is this root.

    Same prefix-collision risk as above, fixed the same way: extract the
    token immediately after --output and require an exact match or a
    "<root>/round_NN" prefix, not a bare substring check.
    """
    root_str = root_match_str(root)
    matches = []
    for ln in _pgrep_af("collector.coordinator"):
        tokens = ln.split()
        for i, tok in enumerate(tokens):
            if tok == "--output" and i + 1 < len(tokens):
                out_path = tokens[i + 1]
                if out_path == root_str or out_path.startswith(root_str + "/"):
                    matches.append(ln)
                break
    return matches


def mode_coordinator_running(root: Path, mode):
    """True if a collector.coordinator process for this mode is active for this root."""
    for ln in coordinator_lines_for_root(root):
        if f"--mode {mode} " in ln:
            return True
    return False


def latest_round_dir(root: Path):
    if not root.exists():
        return None
    rounds = sorted(p for p in root.glob("round_*") if p.is_dir())
    return rounds[-1] if rounds else None


def root_campaign_complete(root: Path):
    """True if any tee'd campaign log for this root shows the final banner.

    Same "data/campaign" is a substring of "data/campaign_fast" risk as the
    process-matching functions above -- checked token-safe here too (split
    on whitespace, since the log's ROUND line prints the root path as its
    own token, e.g. "vpn=data/campaign_fast/_url_slices/...").
    """
    root_str = root_match_str(root)
    candidates = list(REPO_ROOT.glob("campaign_round*.log")) + list(Path("/tmp").glob("campaign_round*.log"))
    for f in candidates:
        try:
            tail = f.read_text()[-4000:]
            if "Campaign complete: all" not in tail:
                continue
            if any(root_str == tok or tok.startswith(root_str + "/")
                   for tok in tail.replace("=", " ").split()):
                return True
        except Exception:
            continue
    return False


def check_root(root: Path, state: dict):
    """Runs checks 1-3 and 5 for one root. Returns (critical, noncritical, mode_counts, mode_stall)."""
    critical = []
    noncritical = []
    label = root.name

    orch_lines = orchestrator_lines_for_root(root)
    coord_lines = coordinator_lines_for_root(root)
    orch_alive = bool(orch_lines)
    coord_alive = bool(coord_lines)
    complete = root_campaign_complete(root)
    round_dir = latest_round_dir(root)

    # A root is ACTIVE if either its own orchestrator or any coordinator
    # process currently references it. IDLE (neither) is not inherently
    # a problem -- see module docstring. Only evaluate staleness/frozen
    # checks when the root is expected to be producing something.
    active = orch_alive or coord_alive

    # 1/3. Orchestrator alive but launched nothing, or died mid-campaign — CRITICAL
    if orch_alive and not coord_alive and not complete:
        critical.append(f"[{label}] run_campaign.sh is running for this root but zero "
                         f"collector.coordinator processes are active")
    # (An orchestrator dying entirely, with the root neither complete nor
    # producing coordinators, is covered by check 2's staleness test below
    # once file activity goes quiet -- no separate "process died" check is
    # meaningful per-root now that a root's activity can also come from a
    # one-off run_stage.sh invocation with no orchestrator at all, as
    # data/campaign's round_03 tail currently does.)

    # 2. File freshness — CRITICAL, but only when this root is ACTIVE.
    if active and round_dir is not None and not complete:
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
                critical.append(f"[{label}] active (process running) but no file activity in "
                                 f"{round_dir.name} for {age_h:.1f}h (> {STALE_LOG_HOURS}h threshold)")
        else:
            critical.append(f"[{label}] {round_dir.name} exists but is empty — nothing collected yet")

    # 5. Per-mode success count frozen — non-critical (digest only). Only
    # meaningful while a coordinator for that (root, mode) is running --
    # a mode that finished its round and has no process is done, not stalled.
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

    prev_counts = state.get("mode_counts", {}).get(label, {})
    prev_stall = state.get("mode_stall", {}).get(label, {})
    new_stall = {}
    if not complete:
        for mode, count in mode_counts_now.items():
            if not mode_coordinator_running(root, mode):
                new_stall[mode] = 0
                continue
            prev = prev_counts.get(mode)
            streak = prev_stall.get(mode, 0) + 1 if (prev is not None and count == prev) else 0
            new_stall[mode] = streak
            if streak >= STALL_CHECKS_FOR_ALERT:
                noncritical.append(f"[{label}] {mode}: success count frozen at {count} for {streak} "
                                    f"consecutive checks (~{streak * 0.5:.1f}h) while its "
                                    f"coordinator process is still running")

    status = "complete" if complete else ("active" if active else "idle")
    return {
        "label": label, "critical": critical, "noncritical": noncritical,
        "mode_counts": mode_counts_now, "mode_stall": new_stall,
        "round_label": round_dir.name if round_dir else "(no round dir)",
        "status": status,
    }


def main():
    critical = []      # immediate-email issues
    noncritical = []   # hourly-digest-only issues
    info = []
    state = load_state()
    ts = now_utc().isoformat()

    per_root_results = [check_root(root, state) for root in CAMPAIGN_ROOTS if root.exists()]
    for res in per_root_results:
        critical.extend(res["critical"])
        noncritical.extend(res["noncritical"])

    all_active_or_complete = any(r["status"] in ("active", "complete") for r in per_root_results)
    if not per_root_results:
        critical.append("no configured campaign root exists on disk at all — check CAMPAIGN_ROOTS")
    elif not all_active_or_complete:
        # Every configured root is idle simultaneously -- unlike a single
        # idle root (which is fine, see docstring), NOTHING running anywhere
        # is the real "campaign silently died" signal that check 1 used to
        # catch via a single run_campaign.sh pgrep.
        critical.append("no active or complete campaign root found — every root is idle "
                         "(possible silent death of the whole campaign, not just one instance)")

    # 4. Routers reachable — CRITICAL (unchanged, infra is shared across all roots)
    ing_ok, _, ing_err = ssh_client(INGRESS_IP, "echo ok")
    if not ing_ok:
        critical.append(f"ingress router ({INGRESS_IP}) unreachable: {ing_err[:200]}")
    egr_ok, _, egr_err = ssh_client(EGRESS_IP, "echo ok")
    if not egr_ok:
        critical.append(f"egress router ({EGRESS_IP}) unreachable: {egr_err[:200]}")

    # 6. Router drops — delta since last check — non-critical (digest only, unchanged)
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

    state["mode_counts"] = {r["label"]: r["mode_counts"] for r in per_root_results}
    state["mode_stall"] = {r["label"]: r["mode_stall"] for r in per_root_results}
    state["router_drops"] = new_drop_state

    summary_str = "; ".join(
        f"{r['label']}[{r['status']}]: {r['round_label']}, " +
        (", ".join(f"{m}={c}" for m, c in sorted(r["mode_counts"].items())) or "no data")
        for r in per_root_results
    ) or "no roots found"
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

    all_complete = per_root_results and all(r["status"] == "complete" for r in per_root_results)
    if all_complete:
        line = f"[{ts}] heartbeat OK — ALL roots COMPLETE — {summary_str}"
        print(line)
        with LOG_FILE.open("a") as fh:
            fh.write(line + "\n")
        state["alerts_active"] = {}
        save_state(state)
        return

    # ── Log line + alert file (every run, independent of emailing) ─────────
    if all_issues:
        body_lines = [f"[{ts}] {summary_str} — {len(critical)} critical, {len(noncritical)} other"]
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
        line = f"[{ts}] heartbeat OK — {summary_str}"
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
            subject = f"[wire-adversary-correlator] CRITICAL — {len(critical)} issue(s)"
            body = "\n".join([f"[{ts}] {summary_str}"] + [f"  - {a}" for a in critical])
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
        subject = f"[wire-adversary-correlator] Hourly report — {status_word}"
        digest_lines = [f"[{ts}] {summary_str}"]
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
