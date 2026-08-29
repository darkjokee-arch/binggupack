"""Read-only cognitive adapters built on top of BingguPack's canonical core.

These helpers reconstruct, challenge, verify, close, and prioritize work.  They
never grant memory authority: SAVE, approval, commit, and ledger mutation stay
in the existing BingguPack paths.
"""

from .catchup import build_catchup, collect_catchup, render_catchup
from .patterns import (
    attach_factcheck,
    fact_check_candidate,
    propose_sip_candidates,
    reconstruct_intent,
    select_load_bearing_objection,
    select_next_best_action,
)

__all__ = [
    "attach_factcheck",
    "build_catchup",
    "collect_catchup",
    "fact_check_candidate",
    "propose_sip_candidates",
    "reconstruct_intent",
    "render_catchup",
    "select_load_bearing_objection",
    "select_next_best_action",
]
