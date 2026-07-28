#!/bin/bash -l
# scripts/vsc_run_array.slurm
# ==============================
# Trains + evaluates one (mode, seed) replicate as one SLURM array task.
# 4 modes x 5 seeds = 20 tasks, submitted as a single array so SLURM
# schedules/accounts for them together. Each task gets its own node
# allocation, so all 20 can run in parallel if the partition has capacity.
#
#   sbatch --array=0-19 scripts/vsc_run_array.slurm
#
# Replaces the 2026-07-18 vsc_train_mode.slurm / vsc_evaluate_mode.slurm /
# vsc_submit_all.sh (gpu_p100 on genius, no seed control, no
# checkpoint/resume, materializing model.evaluate) -- rewritten
# 2026-07-27 after confirming via `sacctmgr` that this account's actual
# association is mindwell, not genius, and after validating
# checkpoint/resume + streaming eval + seed control on amazone first.
#
# CPU-only deliberately: the model is ~100K params, bound by data loading
# and Python overhead rather than FLOPs (confirmed via the amazone thread
# sweep: 24-32 threads plateaus performance, 48 collapses it 16x from
# hyperthread oversubscription) -- a GPU is not expected to help here, and
# CPU allocation is far less contended than gpu_b200.
#
# Prereqs (run once, ON VSC -- more reliable than vsc_push_code.sh's
# rsync-from-desktop given repeated firewall/connection friction seen
# 2026-07-27; a plain git clone from an already-open VSC terminal session
# has none of that):
#   git clone https://github.com/Nico-Sar/wire-adversary-correlator.git \
#       $VSC_SCRATCH/wire-adversary-correlator
#   cd $VSC_SCRATCH/wire-adversary-correlator && bash scripts/vsc_submit_array.sh
#
#   Datasets: staged read-only at $DATA_ROOT (see below), NOT duplicated
#   into scratch. If not already there (confirmed present 2026-07-27 at
#   $VSC_DATA/wire-adversary-correlator/wire-adversary-correlator-data):
#     cd $VSC_DATA/wire-adversary-correlator
#     git clone --filter=blob:none --sparse \
#         https://github.com/Nico-Sar/wire-adversary-correlator-data.git \
#         wire-adversary-correlator-data
#     cd wire-adversary-correlator-data
#     git sparse-checkout set --no-cone \
#         "/datasets/vpn/vpn_merged.npz" "/datasets/tor/tor_merged.npz" \
#         "/datasets/nym2/nym2_merged.npz" "/datasets/nym5/nym5_merged.npz"
#   (NOT a plain `git clone` -- the data repo's full history is ~68GB of
#   raw pcaps and will blow a $VSC_DATA quota; the sparse clone above pulls
#   only the ~200MB of merged datasets actually needed.)
#
#SBATCH --job-name=wac_array
#SBATCH --partition=batch_graniterapids
#SBATCH --time=08:00:00
#SBATCH --cpus-per-task=32
#SBATCH --mem=32G
#SBATCH --output=logs/%x_%A_%a.out
#SBATCH --error=logs/%x_%A_%a.err
#SBATCH --signal=B:TERM@180

set -uo pipefail

# Self-requeue on approaching the walltime limit. --signal=B:TERM@180 above
# asks SLURM to send TERM 180s before the 8h limit instead of only at hard
# kill time -- enough for the current epoch to finish and a resume
# checkpoint to be written (each epoch is on the order of a minute or two
# based on the amazone pilot, so 180s is comfortable margin). The trap
# resubmits the SAME array task index, which will pick up from
# {mode}_resume.pt automatically (this is the same checkpoint/resume
# mechanism already validated with a real kill/resume test on amazone).
# Worst case from the pilot (nym2, the slowest mode there) was ~5h
# train+eval on older/slower hardware than Granite Rapids, so hitting this
# at all is not expected under normal conditions -- this is a safety net,
# not the primary plan.
_requeue() {
    echo "[$(date)] Caught TERM (approaching walltime) -- requeuing array task $SLURM_ARRAY_TASK_ID"
    scontrol requeue "${SLURM_ARRAY_JOB_ID}_${SLURM_ARRAY_TASK_ID}"
    exit 0
}
trap _requeue TERM

MODES=(vpn tor nym2 nym5)
SEEDS=(1 2 3 4 5)
N_SEEDS=${#SEEDS[@]}

TASK_ID="${SLURM_ARRAY_TASK_ID:?run via sbatch --array=0-19, not directly}"
MODE_IDX=$(( TASK_ID / N_SEEDS ))
SEED_IDX=$(( TASK_ID % N_SEEDS ))
MODE="${MODES[$MODE_IDX]}"
SEED="${SEEDS[$SEED_IDX]}"

echo "[$(date)] array task $TASK_ID -> mode=$MODE seed=$SEED, on $(hostname), $(nproc) CPUs visible"

module purge
module load Python/3.12.3-GCCcore-13.3.0

BUNDLE_DIR="$VSC_SCRATCH/wire-adversary-correlator"
cd "$BUNDLE_DIR" || { echo "[ERROR] $BUNDLE_DIR not found -- run vsc_push_code.sh first" >&2; exit 1; }
mkdir -p logs

# Datasets are staged read-only under $VSC_DATA's sparse clone (see this
# script's header) -- referenced directly, not duplicated into scratch.
# Cross-filesystem reads are fine for a one-time dataset load at the start
# of each task; only checkpoints/results need scratch's higher quota and
# better concurrent-write behavior (Lustre vs NFS-style).
DATA_ROOT="$VSC_DATA/wire-adversary-correlator/wire-adversary-correlator-data"
DATASET="$DATA_ROOT/datasets/${MODE}/${MODE}_merged.npz"
[[ -f "$DATASET" ]] || { echo "[ERROR] $DATASET not found -- stage datasets first" >&2; exit 1; }

OUT_DIR="results/${MODE}_seed${SEED}"
mkdir -p "$OUT_DIR"

# Threads: pin to the allocated CPU count via an EXPLICIT torch.set_num_threads()
# call inside the Python process (--threads below), not just OMP_NUM_THREADS/
# MKL_NUM_THREADS env vars -- this matches what was actually kill/resume-tested
# on amazone (torch.set_num_threads(24) called directly). cpus-per-task=32
# above was chosen conservatively (amazone's own 24-32 plateau region, on
# older/slower hardware than Granite Rapids) and not re-benchmarked here per
# the "skip probes" decision -- if this partition's threading behavior turns
# out to differ meaningfully, the per-epoch wall time in the log below will
# show it.
THREADS="$SLURM_CPUS_PER_TASK"

echo "[$(date)] Training $MODE seed=$SEED (threads=$THREADS) ..."
python -m model.train \
    --dataset "$DATASET" \
    --mode "$MODE" \
    --seed "$SEED" \
    --threads "$THREADS" \
    --output "$OUT_DIR"
train_rc=$?
echo "TRAIN_EXIT=$train_rc"
[[ $train_rc -eq 0 ]] || exit $train_rc

echo "[$(date)] Evaluating $MODE seed=$SEED (threads=$THREADS) ..."
python -m model.evaluate \
    --model "$OUT_DIR/${MODE}_best.pt" \
    --dataset "$DATASET" \
    --mode "$MODE" \
    --threads "$THREADS" \
    --output "$OUT_DIR"
eval_rc=$?
echo "EVAL_EXIT=$eval_rc"
exit $eval_rc
