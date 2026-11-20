"""逐站切换单状态重建与决定溯源。

rollout.csv 中 proposal 不等于启用；enabled / paused / resumed 按 received 时序、
沿 prior 链在同一 (station, policy) 维度上推进。未确认部分仍按旧受理。
下一轮意见不能覆盖已签发的适用范围。
"""
from __future__ import annotations

from dataclasses import dataclass

from .data import Dataset, RolloutRecord


@dataclass(frozen=True)
class ScopeState:
    station: str
    policy: str
    state: str                 # enabled / paused / legacy（仅 proposal 或无记录）
    last_record: RolloutRecord | None
    chain: tuple[RolloutRecord, ...]
    proposals_only: bool


def reconstruct_states(ds: Dataset) -> dict[tuple[str, str], ScopeState]:
    chains: dict[tuple[str, str], list[RolloutRecord]] = {}
    for record in ds.rollout:
        chains.setdefault((record.station, record.policy), []).append(record)

    states: dict[tuple[str, str], ScopeState] = {}
    for key, records in chains.items():
        ordered = sorted(records, key=lambda r: r.received)
        current = "legacy"
        last: RolloutRecord | None = None
        decided = False
        for record in ordered:
            if record.kind == "proposal":
                continue
            decided = True
            last = record
            if record.kind == "enabled":
                current = "enabled"
            elif record.kind == "paused":
                current = "paused"
            elif record.kind == "resumed":
                current = "enabled"
        states[key] = ScopeState(
            station=key[0], policy=key[1], state=current, last_record=last,
            chain=tuple(ordered), proposals_only=not decided,
        )
    return states


def trace_decision(
    ds: Dataset, station: str, policy: str
) -> dict[str, object]:
    """从一条上线决定回溯：证据时点、回执链、在途承诺与适用范围。"""
    states = reconstruct_states(ds)
    state = states.get((station, policy))
    related_commitments = [
        c for c in ds.commitments if c.station == station
        and (c.procedure, c.support)
        == (ds.policies[policy].procedure, ds.policies[policy].support)
    ]
    return {
        "station": station,
        "policy": policy,
        "state": state.state if state else "legacy",
        "chain": [
            {"id": r.id, "kind": r.kind, "received": r.received.isoformat(),
             "prior": r.prior, "proposed_start": r.proposed_start.isoformat()}
            for r in (state.chain if state else [])
        ],
        "in_flight_commitments": [
            {"id": c.id, "window": f"{c.start.isoformat()}~{c.end.isoformat()}",
             "minutes": c.minutes, "skill": c.skill, "proof": c.legacy_proof}
            for c in related_commitments
        ],
        "carry_to_new": ds.policies[policy].carry_to_new,
        "legacy_proof_required": ds.policies[policy].legacy_proof,
    }
