"""
collector/label_logger.py
=========================
Lightweight process that runs on the CLIENT VM alongside visit_trigger.py.
Records (visit_id, t_start, t_end, url, mode) for every scripted browser visit
so that the preprocessor can carve the correct time windows from the rotating
pcap files after collection.

This is the ground truth source. It runs on the client — NOT on the routers —
so it does not interfere with the passive wire-adversary capture in any way.

Output: JSONL file, one record per visit.
  {"visit_id": "abc123", "url": "example.com", "mode": "nym",
   "t_start": 1710000000.123, "t_end": 1710000018.456}
"""

import json
import time
from pathlib import Path


class LabelLogger:
    """
    Context manager. Usage:
        with LabelLogger("logs/labels.jsonl", visit_id, url, mode) as logger:
            # browser visit happens here
            pass
        # t_start and t_end are recorded automatically on enter/exit
    """

    def __init__(self, log_path: str, visit_id: str, url: str, mode: str):
        self.log_path = Path(log_path)
        self.record = {
            "visit_id": visit_id,
            "url":      url,
            "mode":     mode,
            "t_start":  None,
            "t_end":    None,
            "status":   "started",
        }

    def __enter__(self):
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.record["t_start"] = time.time()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.record["t_end"] = time.time()
        self.record["status"] = "error" if exc_type is not None else "success"
        self._write()
        # Return False so exceptions propagate normally
        return False

    def _write(self):
        """Appends the current record to the JSONL log file."""
        with self.log_path.open("a") as f:
            f.write(json.dumps(self.record) + "\n")