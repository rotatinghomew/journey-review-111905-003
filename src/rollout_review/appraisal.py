"""跨情景稳健性包络与授权范围判定。

批准要求：所有相容关联下两个指标均达界（policy.csv）。
结论只授权有证据支持的 版本/支持方式/办理类型；未成熟批次不计入放行人数。
"""
from __future__ import annotations

from dataclasses import dataclass

from .data import Dataset
from .linkage import LinkageScenario, compatible_scenarios
from .outcomes import (
    COMPLETION,
    EXTRA,
    evaluate_all,
    evaluate_station,
    is_mature,
)


@dataclass(frozen=True)
class MetricEnvelope:
    policy_id: str
    metric: str
    scope: tuple[str, ...]           # ("policy",) 或 ("station", Sx)
    diffs: tuple[float, ...]
    threshold: float
    passed_all_scenarios: bool
    flips: bool                       # 不同关联是否逆转达标判断
    flip_scenarios: tuple[str, ...]
    best: float
    worst: float


@dataclass(frozen=True)
class Authorization:
    policy_id: str
    station: str
    completion_supported: bool
    extra_supported: bool
    authorized: bool                  # 两个指标在本站均有成熟证据且全情景达标
    gaps: tuple[str, ...]
    mature_new_people: dict[str, int]


def build_envelopes(ds: Dataset) -> dict[tuple, MetricEnvelope]:
    scenarios = compatible_scenarios(ds)
    envelopes: dict[tuple, MetricEnvelope] = {}

    def absorb(key: tuple, scope: tuple, metric: str, policy_id: str,
               entries: list[tuple[float, bool, str]]) -> None:
        diffs = tuple(d for d, _, _ in entries)
        passes = [p for _, p, _ in entries]
        flips = not all(passes) and any(passes)
        threshold = ds.policies[policy_id].completion_drop if metric == COMPLETION \
            else ds.policies[policy_id].extra_increase
        envelopes[key] = MetricEnvelope(
            policy_id=policy_id, metric=metric, scope=scope, diffs=diffs,
            threshold=threshold, passed_all_scenarios=all(passes), flips=flips,
            flip_scenarios=tuple(desc for _, p, desc in entries if not p),
            best=max(diffs), worst=min(diffs),
        )

    policy_entries: dict[tuple, list[tuple[float, bool, str]]] = {}
    station_entries: dict[tuple, list[tuple[float, bool, str]]] = {}
    stations = sorted({a.station for a in ds.assignments.values()})
    for scenario in scenarios:
        for (policy_id, metric), result in evaluate_all(ds, scenario).items():
            if result.weighted_diff is not None:
                policy_entries.setdefault((policy_id, metric), []).append(
                    (result.weighted_diff, result.passed, scenario.description)
                )
        for station in stations:
            for policy_id in ds.policies:
                for metric in (COMPLETION, EXTRA):
                    result = evaluate_station(ds, station, policy_id, metric, scenario)
                    if result.weighted_diff is not None:
                        station_entries.setdefault(
                            (policy_id, metric, station), []
                        ).append((result.weighted_diff, result.passed, scenario.description))

    for (policy_id, metric), entries in policy_entries.items():
        absorb((policy_id, metric, "policy"), ("policy",), metric, policy_id, entries)
    for (policy_id, metric, station), entries in station_entries.items():
        absorb((policy_id, metric, "station"), ("station", station), metric,
               policy_id, entries)
    return envelopes


def station_authorizations(ds: Dataset) -> list[Authorization]:
    envelopes = build_envelopes(ds)
    out: list[Authorization] = []
    stations = sorted({a.station for a in ds.assignments.values()})
    for policy_id, policy in sorted(ds.policies.items()):
        for station in stations:
            gaps: list[str] = []
            mature_new: dict[str, int] = {}
            metric_ok: dict[str, bool] = {}
            for metric in (COMPLETION, EXTRA):
                related = [
                    a for a in ds.assignments.values()
                    if a.station == station and a.procedure == policy.procedure
                    and a.support == policy.support
                ]
                immature = [a for a in related if not is_mature(ds, a, metric)]
                mature_new[metric] = sum(
                    1 for a in related if is_mature(ds, a, metric) and a.arm == "new"
                )
                if immature:
                    gaps.append(
                        f"{metric}: {len(immature)} 人观察窗未获来源覆盖确认，"
                        f"不计入放行（成熟 {len(related) - len(immature)}/"
                        f"{len(related)} 人）"
                    )
                    metric_ok[metric] = False
                    continue
                env = envelopes.get((policy_id, metric, "station"))
                metric_ok[metric] = bool(env and env.passed_all_scenarios)
            out.append(
                Authorization(
                    policy_id=policy_id, station=station,
                    completion_supported=metric_ok[COMPLETION],
                    extra_supported=metric_ok[EXTRA],
                    authorized=all(metric_ok.values()),
                    gaps=tuple(gaps),
                    mature_new_people=dict(mature_new),
                )
            )
    return out


def all_scenarios(ds: Dataset) -> list[LinkageScenario]:
    return compatible_scenarios(ds)
