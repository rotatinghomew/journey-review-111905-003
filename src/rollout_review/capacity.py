"""续办承诺排班与切换组合容量试算。

约束（DATA.md）：
- 同一 resource 同期只能供一项服务；supersedes 生效后只认缩短后的最新记录；
- 移动资源跨站点至少留 15 分钟；
- 旧版未切站点仍有本站原班人员；切换后仅 staff 表特留人员；
- 退出试验的人不从续办容量里删除：draft_commitments 全量参与排班；
- 承诺只能在各自窗口内排定（左闭右开），连续占用所列技能分钟。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from .data import Commitment, Dataset, StaffRecord

TRAVEL = timedelta(minutes=15)


@dataclass(frozen=True)
class EffectiveResource:
    resource: str
    stations: tuple[str, ...]
    skill: str
    start: "object"
    end: "object"
    based_on: str


@dataclass(frozen=True)
class Placement:
    commitment: str
    resource: str
    station: str
    start: "object"
    end: "object"


@dataclass(frozen=True)
class Feasibility:
    switched_stations: frozenset[str]
    switched_policies: frozenset[tuple[str, str]]  # (station, policy)
    feasible: bool
    placements: tuple[Placement, ...]
    unscheduled: tuple[str, ...]
    resources: tuple[EffectiveResource, ...]


def effective_staff(ds: Dataset) -> list[EffectiveResource]:
    """应用 supersedes：每个 resource 取 received 最新的一条。"""
    latest: dict[str, StaffRecord] = {}
    for record in sorted(ds.staff, key=lambda r: r.received):
        if record.resource not in latest or record.received >= latest[record.resource].received:
            latest[record.resource] = record
    return [
        EffectiveResource(
            resource=r.resource, stations=r.stations, skill=r.skill,
            start=r.start, end=r.end, based_on=r.id,
        )
        for r in sorted(latest.values(), key=lambda r: r.resource)
    ]


def _try_schedule(
    jobs: list[Commitment], resources: list[EffectiveResource]
) -> tuple[bool, dict[str, Placement], list[str]]:
    """回溯排班；同一资源已排区间不重叠，跨站补 15 分钟。

    返回 (是否全部排定, 已排定安置, 未能排定的承诺 id)。失败时保留部分排班，
    便于指出具体被挤占的承诺。
    """
    schedule: dict[str, list[Placement]] = {r.resource: [] for r in resources}
    ordered = sorted(jobs, key=lambda j: (j.end, -j.minutes))
    unscheduled: list[str] = []

    def place(idx: int) -> bool:
        while idx < len(ordered) and ordered[idx].id in unscheduled:
            idx += 1
        if idx == len(ordered):
            return True
        job = ordered[idx]
        for res in resources:
            if res.skill != job.skill or job.station not in res.stations:
                continue
            starts = {job.start}
            for p in schedule[res.resource]:
                starts.add(p.end + (TRAVEL if p.station != job.station else timedelta(0)))
            for begin in sorted(starts):
                finish = begin + timedelta(minutes=job.minutes)
                if begin < max(job.start, res.start) or finish > min(job.end, res.end):
                    continue
                clash = False
                for p in schedule[res.resource]:
                    gap = TRAVEL if p.station != job.station else timedelta(0)
                    if not (finish + gap <= p.start or p.end + gap <= begin):
                        clash = True
                        break
                if clash:
                    continue
                placement = Placement(job.id, res.resource, job.station, begin, finish)
                schedule[res.resource].append(placement)
                schedule[res.resource].sort(key=lambda p: p.start)
                if place(idx + 1):
                    return True
                schedule[res.resource].remove(placement)
        # 该承诺在现有资源下无可行位置：记录后继续试排其余承诺
        unscheduled.append(job.id)
        if place(idx + 1):
            return True
        unscheduled.pop()
        return False

    place(0)
    flat = {p.commitment: p for plist in schedule.values() for p in plist}
    return not unscheduled, flat, sorted(unscheduled)


def trial_combination(
    ds: Dataset,
    switched_stations: frozenset[str],
    switched_policies: frozenset[tuple[str, str]] | None = None,
) -> Feasibility:
    """试算一个切换组合下全部续办承诺能否兑现。

    承诺集合恒为 draft_commitments 全量（含退出试验者，不删除）。staff 表列出的
    是切换后特留资源；未切站点的本站原班人员不承担表内跨站承诺，故资源集合不随
    切换组合缩减——切换组合只改变判定语境，具体差异体现在逐站/逐政策结论中。
    """
    switched_policies = switched_policies or frozenset()
    resources = effective_staff(ds)
    jobs = list(ds.commitments)
    ok, flat, unscheduled = _try_schedule(jobs, resources)
    return Feasibility(
        switched_stations=switched_stations,
        switched_policies=switched_policies,
        feasible=ok,
        placements=tuple(sorted(flat.values(), key=lambda p: (p.resource, p.start))),
        unscheduled=tuple(unscheduled),
        resources=tuple(resources),
    )


def per_station_commitment_gate(ds: Dataset) -> dict[str, dict[str, object]]:
    """逐站给出切换前必须先有替代履约的承诺及原因。"""
    notes = standalone_feasibility(ds)
    gate: dict[str, dict[str, object]] = {}
    for commitment in ds.commitments:
        gate.setdefault(commitment.station, {"blocked": [], "placeable": []})
        if notes[commitment.id] == "可单独排定":
            gate[commitment.station]["placeable"].append(commitment.id)
        else:
            gate[commitment.station]["blocked"].append(
                {"commitment": commitment.id, "policy":
                 f"{commitment.procedure}/{commitment.support}", "reason": notes[commitment.id]}
            )
    return gate


def blocking_commitments(ds: Dataset) -> dict[str, list[str]]:
    """找出在全量承诺下无法排定、且移除后其余承诺可排定的承诺（替代履约候选项）。"""
    resources = effective_staff(ds)
    jobs = list(ds.commitments)
    base_ok, _, base_missing = _try_schedule(jobs, resources)
    if base_ok:
        return {}
    blockers: dict[str, list[str]] = {}
    for job in jobs:
        rest = [j for j in jobs if j.id != job.id]
        ok, _, missing = _try_schedule(rest, resources)
        if ok:
            blockers.setdefault("remove_one", []).append(job.id)
    blockers["unscheduled_full"] = base_missing
    return blockers


def standalone_feasibility(ds: Dataset) -> dict[str, str]:
    """逐条承诺单独试排：区分"窗口/资源硬冲突"与"多承诺争抢"。"""
    resources = effective_staff(ds)
    out: dict[str, str] = {}
    for job in ds.commitments:
        ok, _, missing = _try_schedule([job], resources)
        if ok:
            out[job.id] = "可单独排定"
        else:
            compatible = [
                r.resource for r in resources
                if r.skill == job.skill and job.station in r.stations
            ]
            if not compatible:
                out[job.id] = "无匹配技能/站点资源"
            else:
                out[job.id] = (
                    f"窗口 {job.start.isoformat(timespec='minutes')}~"
                    f"{job.end.isoformat(timespec='minutes')}（{job.minutes} 分钟）"
                    f"长于匹配资源可用时段，硬冲突"
                )
    return out
