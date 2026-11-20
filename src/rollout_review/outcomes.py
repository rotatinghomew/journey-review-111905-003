"""ITT 结局构造、成熟度判定与区组/政策评价。

关键口径：
- 分组只认首次随机分派臂（assignment.arm）。分派新版、实际走旧版（经柜面回到旧版）
  仍计入新版分派组，用于检验整体改版承诺；不得按浏览过新版或最终成功者重组对照组。
- 完成 / 额外回柜窗均为首次分派后 48 小时；整窗结束且来源确认覆盖（coverage.through）
  才视为成熟，未成熟单位不计入任何比率（也不计入放行人数）。
- 模糊回执按给定关联情景落到分派；required（产品本就要求的柜面核验）与 extra
  （真正额外的回柜）分开统计。
"""
from __future__ import annotations

from dataclasses import dataclass

from .data import WINDOW, Assignment, Dataset
from .linkage import LinkageScenario

COMPLETION = "completion"
EXTRA = "extra"


@dataclass(frozen=True)
class ArmStat:
    events: int
    n: int

    @property
    def rate(self) -> float | None:
        return self.events / self.n if self.n else None


@dataclass(frozen=True)
class BlockResult:
    station: str
    block: str
    support: str
    procedure: str
    old: ArmStat
    new: ArmStat
    evaluable: bool
    reason: str = ""

    @property
    def diff(self) -> float | None:
        if self.old.rate is None or self.new.rate is None:
            return None
        return self.new.rate - self.old.rate


@dataclass(frozen=True)
class PolicyResult:
    policy_id: str
    metric: str
    weighted_diff: float | None
    threshold: float
    passed: bool
    mature_blocks: list[BlockResult]
    immature_blocks: list[BlockResult]
    min_arm: int
    n_mature_people: int


def coverage_map(ds: Dataset) -> dict[tuple[str, str], "object"]:
    return {(c.station, c.source): c.through for c in ds.coverage}


def is_mature(ds: Dataset, assignment: Assignment, metric: str) -> bool:
    end = assignment.assigned_at + WINDOW
    cov = coverage_map(ds)
    terminal_ok = cov[(assignment.station, "terminal")] >= end
    if metric == COMPLETION:
        return terminal_ok
    return terminal_ok and cov[(assignment.station, "counter")] >= end


def assignment_outcomes(
    ds: Dataset, scenario: LinkageScenario
) -> tuple[set[str], set[str], set[str]]:
    """返回 (完成集合, 额外回柜集合, 应到核验集合)，已并入模糊关联。"""
    completed: set[str] = set()
    extra: set[str] = set()
    required: set[str] = set()
    for event in ds.journeys:
        target = event.assignment or scenario.mapping.get(event.id)
        if not target:
            continue  # 在本情景下仍无法识别的事件：保留为证据缺口，不强行归因
        if event.step == "submit" and event.result == "complete":
            completed.add(target)
        elif event.step == "counter" and event.result == "extra":
            extra.add(target)
        elif event.step == "counter" and event.result == "required":
            required.add(target)
    return completed, extra, required


def _block_stats(
    ds: Dataset, metric: str, completed: set[str], extra: set[str]
) -> list[BlockResult]:
    rows: dict[tuple, dict[str, ArmStat]] = {}
    for assignment in ds.assignments.values():
        key = (assignment.station, assignment.block, assignment.support, assignment.procedure)
        rows.setdefault(key, {"old": ArmStat(0, 0), "new": ArmStat(0, 0)})
        if not is_mature(ds, assignment, metric):
            continue
        stat = rows[key][assignment.arm]
        hit = assignment.id in completed if metric == COMPLETION else assignment.id in extra
        rows[key][assignment.arm] = ArmStat(stat.events + int(hit), stat.n + 1)
    out: list[BlockResult] = []
    for (station, block, support, procedure), arms in sorted(rows.items()):
        out.append(
            BlockResult(station, block, support, procedure, arms["old"], arms["new"],
                        evaluable=True)
        )
    return out


def evaluate_policy(
    ds: Dataset,
    policy_id: str,
    metric: str,
    scenario: LinkageScenario,
) -> PolicyResult:
    policy = ds.policies[policy_id]
    completed, extra, _ = assignment_outcomes(ds, scenario)
    blocks = _block_stats(ds, metric, completed, extra)
    threshold = policy.completion_drop if metric == COMPLETION else policy.extra_increase
    mature: list[BlockResult] = []
    immature: list[BlockResult] = []
    for block in blocks:
        if block.procedure != policy.procedure or block.support != policy.support:
            continue
        if block.old.n < policy.min_arm or block.new.n < policy.min_arm:
            if block.old.n == 0 and block.new.n == 0:
                reason = "观察窗未获来源覆盖确认，无成熟样本"
            else:
                reason = (
                    f"成熟臂样本 {block.old.n}/{block.new.n} 低于 "
                    f"min_arm={policy.min_arm}"
                )
            immature.append(
                BlockResult(block.station, block.block, block.support, block.procedure,
                            block.old, block.new, evaluable=False, reason=reason)
            )
            continue
        mature.append(block)

    if not mature:
        return PolicyResult(policy_id, metric, None, threshold, False, [], immature,
                            policy.min_arm, 0)

    # 区组内按人比例差；区组间按成熟人数（两臂合计）计权
    total_weight = sum(b.old.n + b.new.n for b in mature)
    weighted = sum(
        (b.diff or 0.0) * (b.old.n + b.new.n) for b in mature
    ) / total_weight
    if metric == COMPLETION:
        # 新版完成率最多降低 completion_drop（绝对差）
        passed = weighted >= -threshold
    else:
        # 额外回柜率最多增加 extra_increase（绝对差）
        passed = weighted <= threshold
    return PolicyResult(policy_id, metric, weighted, threshold, passed, mature, immature,
                        policy.min_arm, total_weight)


def evaluate_station(
    ds: Dataset,
    station: str,
    policy_id: str,
    metric: str,
    scenario: LinkageScenario,
) -> PolicyResult:
    """与 evaluate_policy 同口径，但只纳入指定站点的区组（逐站授权用）。"""
    policy = ds.policies[policy_id]
    completed, extra, _ = assignment_outcomes(ds, scenario)
    blocks = _block_stats(ds, metric, completed, extra)
    threshold = policy.completion_drop if metric == COMPLETION else policy.extra_increase
    mature: list[BlockResult] = []
    immature: list[BlockResult] = []
    for block in blocks:
        if block.station != station:
            continue
        if block.procedure != policy.procedure or block.support != policy.support:
            continue
        if block.old.n < policy.min_arm or block.new.n < policy.min_arm:
            if block.old.n == 0 and block.new.n == 0:
                reason = "观察窗未获来源覆盖确认，无成熟样本"
            else:
                reason = (
                    f"成熟臂样本 {block.old.n}/{block.new.n} 低于 "
                    f"min_arm={policy.min_arm}"
                )
            immature.append(
                BlockResult(block.station, block.block, block.support, block.procedure,
                            block.old, block.new, evaluable=False, reason=reason)
            )
            continue
        mature.append(block)

    if not mature:
        return PolicyResult(policy_id, metric, None, threshold, False, [], immature,
                            policy.min_arm, 0)

    total_weight = sum(b.old.n + b.new.n for b in mature)
    weighted = sum(
        (b.diff or 0.0) * (b.old.n + b.new.n) for b in mature
    ) / total_weight
    if metric == COMPLETION:
        passed = weighted >= -threshold
    else:
        passed = weighted <= threshold
    return PolicyResult(policy_id, metric, weighted, threshold, passed, mature, immature,
                        policy.min_arm, total_weight)


def evaluate_all(ds: Dataset, scenario: LinkageScenario) -> dict[tuple[str, str], PolicyResult]:
    return {
        (pid, metric): evaluate_policy(ds, pid, metric, scenario)
        for pid in ds.policies
        for metric in (COMPLETION, EXTRA)
    }
