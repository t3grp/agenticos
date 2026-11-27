"""Metadata-only preflight: pure evaluation plus reviewable read-only SQL text.

PROTOTYPE ONLY. No database connector, no process, no file access. The caller supplies
metadata numbers; nothing here can approve a live capture.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, localcontext

from .core import ProbeError, strict_int

MIB = 2**20
GIB = 2**30
EXAMPLE_RELAY_CAP = 256 * MIB

STORAGE_PER_GB_MONTH = Decimal("0.02")
EGRESS_PER_GB = Decimal("0.12")
EGRESS_COPIES = 2
HOLD_DAYS = 7
BILLING_MONTH_DAYS = 28
REQUEST_BLOCK = 512 * 1024
REQUEST_FIXED = 80
REQUEST_RATE = Decimal("0.00000145")
METADATA_RESERVE = Decimal("0.01")
KEY_RESERVE = Decimal("0.02")
DECIMAL_GB = Decimal(10**9)

PROOF_TMPFS_MAX = GIB
PROOF_CONTAINER_MEM = 2 * GIB
PROOF_EXTRA = 256 * MIB
HOST_BASE = 2 * GIB
HOST_MARGIN = GIB

# Reviewable text only. Metadata sizes and relation kinds; no names, no row text.
READ_ONLY_SQL = """\
BEGIN READ ONLY;
SET LOCAL statement_timeout = '5s';
SET LOCAL lock_timeout = '2s';
SELECT pg_database_size(current_database()) AS physical_db_bytes;
SELECT c.relkind, pg_relation_size(c.oid) AS relation_bytes
  FROM pg_class AS c
  JOIN pg_namespace AS n ON n.oid = c.relnamespace
 WHERE c.relkind IN ('r', 'i', 't', 'm')
   AND n.nspname NOT IN ('pg_catalog', 'information_schema')
 ORDER BY relation_bytes DESC
 LIMIT 10;
ROLLBACK;
"""

_INT_FIELDS = (
    "physical_db_bytes",
    "largest_relation_bytes",
    "host_mem_available_bytes",
    "production_peak_bytes",
    "relay_memory_cap_bytes",
    "proof_capacity_bytes",
)
_FIELDS = frozenset(_INT_FIELDS) | {"cost_budget_usd", "logical_bound_bytes"}


def cost_for(nbytes: int) -> Decimal:
    """Conservative total cost for one capture of nbytes. Decimal only; no free bandwidth."""
    n = strict_int(nbytes)
    with localcontext() as ctx:
        ctx.prec = 60
        gb = Decimal(n) / DECIMAL_GB
        storage = gb * STORAGE_PER_GB_MONTH * HOLD_DAYS / BILLING_MONTH_DAYS
        egress = gb * EGRESS_PER_GB * EGRESS_COPIES
        requests = Decimal(-(-n // REQUEST_BLOCK) + REQUEST_FIXED) * REQUEST_RATE
        return storage + egress + requests + METADATA_RESERVE + KEY_RESERVE


def max_bytes_for_budget(budget: Decimal) -> int:
    """Largest byte count whose cost_for() fits the budget. cost_for is monotonic."""
    if not isinstance(budget, Decimal) or not budget.is_finite() or budget < 0:
        raise ProbeError("E_BAD_INT")
    if cost_for(0) > budget:
        return 0
    lo, hi = 0, 1 << 44
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if cost_for(mid) <= budget:
            lo = mid
        else:
            hi = mid - 1
    return lo


def proof_pgdata_floor(physical_db_bytes: int) -> int:
    """ceil(1.3 * physical size) + 256 MiB. Physical size is not a dump-size bound."""
    return -(-13 * strict_int(physical_db_bytes) // 10) + PROOF_EXTRA


def host_memory_needed(production_peak_bytes: int) -> int:
    return HOST_BASE + strict_int(production_peak_bytes) + HOST_MARGIN


@dataclass(frozen=True)
class Preflight:
    decision: str  # "refuse" | "cannot_approve" | "candidate"
    reasons: tuple[str, ...]
    cap_bytes: int
    proof_floor_bytes: int
    next_step: str
    live_permitted: bool = False  # always False: approval is a separate, human step


def _refuse(*reasons: str) -> Preflight:
    return Preflight("refuse", reasons, 0, 0, "fix_metadata")


def evaluate(meta: Mapping[str, object]) -> Preflight:
    """Evaluate metadata. Missing, unknown or malformed fields refuse."""
    if not isinstance(meta, Mapping) or set(meta) - _FIELDS:
        return _refuse("unknown_field")
    if any(f not in meta for f in (*_INT_FIELDS, "cost_budget_usd", "logical_bound_bytes")):
        return _refuse("missing_field")
    try:
        ints = {f: strict_int(meta[f]) for f in _INT_FIELDS}
        bound = meta["logical_bound_bytes"]
        logical = None if bound is None else strict_int(bound)
        cost_cap = max_bytes_for_budget(meta["cost_budget_usd"])  # type: ignore[arg-type]
    except ProbeError:
        return _refuse("malformed_field")
    if ints["largest_relation_bytes"] > ints["physical_db_bytes"]:
        return _refuse("inconsistent_metadata")

    cap = min(cost_cap, ints["relay_memory_cap_bytes"], ints["proof_capacity_bytes"])
    floor = proof_pgdata_floor(ints["physical_db_bytes"])
    reasons: list[str] = []
    if floor > PROOF_TMPFS_MAX:
        reasons.append("proof_floor_exceeds_tmpfs")
    if ints["host_mem_available_bytes"] < host_memory_needed(ints["production_peak_bytes"]):
        reasons.append("host_memory_insufficient")
    if cap <= 0:
        reasons.append("no_capacity")
    if reasons:
        return Preflight("refuse", tuple(reasons), cap, floor, "none")
    if logical is None:
        return Preflight(
            "cannot_approve",
            ("logical_bound_unknown",),
            cap,
            floor,
            "separately_authorized_count_discard_pass_service_up",
        )
    if logical > cap:
        return Preflight("refuse", ("logical_bound_exceeds_cap",), cap, floor, "none")
    return Preflight("candidate", (), cap, floor, "human_approval_required")
