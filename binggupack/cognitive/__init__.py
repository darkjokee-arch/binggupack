"""Read-only cognitive adapters built on top of BingguPack's canonical core.

These helpers reconstruct, challenge, verify, close, and prioritize work.  They
never grant memory authority: SAVE, approval, commit, and ledger mutation stay
in the existing BingguPack paths.
"""

from .catchup import build_catchup, collect_catchup, render_catchup
from .behavioral import run_reference_behavioral_eval
from .mandela import audit_benchmark, evaluate_behavioral_runs
from .patterns import (
    fact_check_candidate,
    propose_sip_candidates,
    reconstruct_intent,
    select_load_bearing_objection,
    select_next_best_action,
)
from .workloop import run_cognitive_workloop

__all__ = [
    "audit_benchmark",
    "build_catchup",
    "collect_catchup",
    "evaluate_behavioral_runs",
    "fact_check_candidate",
    "propose_sip_candidates",
    "reconstruct_intent",
    "render_catchup",
    "run_reference_behavioral_eval",
    "run_cognitive_workloop",
    "select_load_bearing_objection",
    "select_next_best_action",
]
