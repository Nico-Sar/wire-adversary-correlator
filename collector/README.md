# collector/

Data collection infrastructure. Runs across three machine roles:

| Machine | Scripts | Role |
|---|---|---|
| Control machine | `coordinator.py` | SSH orchestrator |
| Client VM(s) | `visit_trigger.py`, `label_logger.py` | Browser automation + ground truth |
| Ingress Router VM | `router_setup.sh` | Always-on tshark capture |
| Egress Router VM | `router_setup.sh` | Always-on tshark capture |
