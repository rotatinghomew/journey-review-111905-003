"""模糊身份线索的相容关联枚举。

规则（DATA.md / identity.csv）：
- 一条回执至多归至一个草稿/分派；
- TEMP1 与 TEMP2 已由交接账确认属于两位不同居民，不能落到同一分派；
- confirmed / ruled_out 以最新证据替代旧 possible（supersedes），可知时点为 received；
- 枚举只用于稳健性包络，不替居民做身份认定。
"""
from __future__ import annotations

from dataclasses import dataclass

from .data import Dataset


@dataclass(frozen=True)
class LinkageScenario:
    """一组相容关联：event_id -> assignment_id（仅列模糊事件）。"""

    mapping: dict[str, str]
    description: str


def _effective_evidence(ds: Dataset) -> dict[str, list[tuple[str, str]]]:
    """去掉被取代的 possible 行，返回 event -> [(assignment, state)]。"""
    superseded = {row.supersedes for row in ds.identity if row.supersedes}
    by_event: dict[str, list[tuple[str, str]]] = {}
    for row in ds.identity:
        if row.id in superseded:
            continue
        by_event.setdefault(row.event, []).append((row.assignment, row.state))
    return by_event


def compatible_scenarios(ds: Dataset) -> list[LinkageScenario]:
    """枚举全部相容关联（confirmed 固定、possible 组合、互斥约束生效）。"""
    evidence = _effective_evidence(ds)

    fixed: dict[str, str] = {}
    unresolved: dict[str, list[str]] = {}
    for event, candidates in evidence.items():
        confirmed = [a for a, state in candidates if state == "confirmed"]
        if confirmed:
            # 同一事件至多一个 confirmed（其余为已替代的 possible 或 ruled_out）
            fixed[event] = confirmed[0]
            continue
        # ruled_out 已从候选中剔除；剩余 possible 才是可枚举空间
        possibles = [a for a, state in candidates if state == "possible"]
        unresolved[event] = possibles

    scenarios: list[LinkageScenario] = []

    def recurse(events: list[str], chosen: dict[str, str]) -> None:
        if not events:
            mapping = dict(fixed)
            mapping.update(chosen)
            desc = ", ".join(f"{e}->{a}" for e, a in sorted(mapping.items()))
            scenarios.append(LinkageScenario(mapping=mapping, description=desc))
            return
        event, *rest = events
        used = set(chosen.values()) | set(fixed.values())
        for assignment in unresolved[event]:
            if assignment in used:  # 不同居民不能并到同一分派
                continue
            chosen[event] = assignment
            recurse(rest, chosen)
            del chosen[event]

    recurse(sorted(unresolved), {})
    return scenarios


def unresolved_events(ds: Dataset) -> dict[str, dict[str, object]]:
    """供报告引用的证据缺口：仍停留在 possible 的事件及其候选。"""
    evidence = _effective_evidence(ds)
    out: dict[str, dict[str, object]] = {}
    for event, candidates in sorted(evidence.items()):
        possible = [a for a, state in candidates if state == "possible"]
        confirmed = [a for a, state in candidates if state == "confirmed"]
        if possible:
            out[event] = {
                "candidates": possible,
                "confirmed": confirmed,
                "result": next(j.result for j in ds.journeys if j.id == event),
                "step": next(j.step for j in ds.journeys if j.id == event),
                "received": next(j.received for j in ds.journeys if j.id == event),
            }
    return out
