"""本地合成记录加载与证据时钟。

全部判断只基于 fixtures 中的本地 CSV，不访问任何外部服务。
时间均为同一地区本地时间；时段左闭右开。
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

WINDOW = timedelta(hours=48)


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value)


@dataclass(frozen=True)
class Assignment:
    id: str
    station: str
    block: str
    arm: str          # new / old：首次随机分派臂，ITT 分析的分组依据
    support: str
    procedure: str
    assigned_at: datetime


@dataclass(frozen=True)
class JourneyEvent:
    id: str
    assignment: str
    draft: str
    step: str         # draft / submit / counter
    version: str      # new / old：实际曝光版本，不等于分派臂
    occurred: datetime
    received: datetime
    result: str       # started / complete / required / extra


@dataclass(frozen=True)
class IdentityLink:
    id: str
    event: str
    assignment: str
    state: str        # possible / confirmed / ruled_out
    received: datetime
    supersedes: str


@dataclass(frozen=True)
class Coverage:
    id: str
    station: str
    source: str       # terminal / counter
    through: datetime
    received: datetime


@dataclass(frozen=True)
class Policy:
    id: str
    procedure: str
    support: str
    min_arm: int
    completion_drop: float
    extra_increase: float
    legacy_proof: str
    carry_to_new: bool


@dataclass(frozen=True)
class Commitment:
    id: str
    station: str
    procedure: str
    support: str
    legacy_proof: str
    start: datetime
    end: datetime
    minutes: int
    skill: str


@dataclass(frozen=True)
class StaffRecord:
    id: str
    resource: str
    stations: tuple[str, ...]
    skill: str
    start: datetime
    end: datetime
    received: datetime
    supersedes: str


@dataclass(frozen=True)
class RolloutRecord:
    id: str
    station: str
    policy: str
    proposed_start: datetime
    kind: str         # proposal / enabled / paused / resumed
    received: datetime
    prior: str


@dataclass
class Dataset:
    assignments: dict[str, Assignment]
    journeys: list[JourneyEvent]
    identity: list[IdentityLink]
    coverage: list[Coverage]
    policies: dict[str, Policy]
    commitments: list[Commitment]
    staff: list[StaffRecord]
    rollout: list[RolloutRecord]
    fixtures_dir: Path
    evidence_time: datetime = field(init=False)

    def __post_init__(self) -> None:
        # 证据时钟：本地记录中最晚的可知时点（received）。评估时点取此后任意时刻等价。
        stamps: list[datetime] = []
        stamps += [e.received for e in self.journeys]
        stamps += [c.received for c in self.coverage]
        stamps += [s.received for s in self.staff]
        stamps += [r.received for r in self.rollout]
        stamps += [i.received for i in self.identity]
        self.evidence_time = max(stamps)


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def load_dataset(fixtures_dir: str | Path) -> Dataset:
    base = Path(fixtures_dir)
    assignments = {
        row["id"]: Assignment(
            id=row["id"], station=row["station"], block=row["block"], arm=row["arm"],
            support=row["support"], procedure=row["procedure"],
            assigned_at=parse_ts(row["assigned_at"]),
        )
        for row in _read(base / "assignment.csv")
    }
    journeys = [
        JourneyEvent(
            id=row["id"], assignment=row["assignment"], draft=row["draft"],
            step=row["step"], version=row["version"],
            occurred=parse_ts(row["occurred"]), received=parse_ts(row["received"]),
            result=row["result"],
        )
        for row in _read(base / "journeys.csv")
    ]
    identity = [
        IdentityLink(
            id=row["id"], event=row["event"], assignment=row["assignment"],
            state=row["state"], received=parse_ts(row["received"]),
            supersedes=row["supersedes"],
        )
        for row in _read(base / "identity.csv")
    ]
    coverage = [
        Coverage(
            id=row["id"], station=row["station"], source=row["source"],
            through=parse_ts(row["through"]), received=parse_ts(row["received"]),
        )
        for row in _read(base / "coverage.csv")
    ]
    policies = {
        row["id"]: Policy(
            id=row["id"], procedure=row["procedure"], support=row["support"],
            min_arm=int(row["min_arm"]),
            completion_drop=float(row["completion_drop"]),
            extra_increase=float(row["extra_increase"]),
            legacy_proof=row["legacy_proof"],
            carry_to_new=row["carry_to_new"] == "yes",
        )
        for row in _read(base / "policy.csv")
    }
    commitments = [
        Commitment(
            id=row["id"], station=row["station"], procedure=row["procedure"],
            support=row["support"], legacy_proof=row["legacy_proof"],
            start=parse_ts(row["start"]), end=parse_ts(row["end"]),
            minutes=int(row["minutes"]), skill=row["skill"],
        )
        for row in _read(base / "draft_commitments.csv")
    ]
    staff = [
        StaffRecord(
            id=row["id"], resource=row["resource"],
            stations=tuple(row["stations"].split(";")), skill=row["skill"],
            start=parse_ts(row["start"]), end=parse_ts(row["end"]),
            received=parse_ts(row["received"]), supersedes=row["supersedes"],
        )
        for row in _read(base / "staff.csv")
    ]
    rollout = [
        RolloutRecord(
            id=row["id"], station=row["station"], policy=row["policy"],
            proposed_start=parse_ts(row["proposed_start"]), kind=row["kind"],
            received=parse_ts(row["received"]), prior=row["prior"],
        )
        for row in _read(base / "rollout.csv")
    ]
    return Dataset(
        assignments=assignments, journeys=journeys, identity=identity,
        coverage=coverage, policies=policies, commitments=commitments,
        staff=staff, rollout=rollout, fixtures_dir=base,
    )
