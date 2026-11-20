"""实际曝光复盘：步骤漏斗与交互影响（非因果）。

实际曝光只能用来复盘步骤与交互，不能当作因果结论；因果口径以分派臂 ITT 为准。
无法识别的事件单列证据缺口，不进入任何比率分母的"重分组"。
"""
from __future__ import annotations

from dataclasses import dataclass

from .data import Dataset
from .linkage import LinkageScenario


@dataclass(frozen=True)
class FunnelRow:
    label: str
    n: int
    completed: int
    no_submit: int
    required_counter: int
    extra_counter: int

    @property
    def completion_rate(self) -> float:
        return self.completed / self.n if self.n else float("nan")

    @property
    def extra_rate(self) -> float:
        return self.extra_counter / self.n if self.n else float("nan")


def exposure_funnel(
    ds: Dataset, scenario: LinkageScenario
) -> tuple[list[FunnelRow], list[str]]:
    """按 (分派臂, 实际曝光版本集合) 出漏斗；返回未识别事件 id 列表。"""
    completed, extra, required = _collect(ds, scenario)
    exposed: dict[str, set[str]] = {}
    identified: set[str] = set()
    for event in ds.journeys:
        target = event.assignment or scenario.mapping.get(event.id)
        if not target:
            continue
        identified.add(event.id)
        exposed.setdefault(target, set()).add(event.version)

    groups: dict[tuple[str, str], list[str]] = {}
    for aid, assignment in ds.assignments.items():
        versions = exposed.get(aid, set())
        if not versions:
            exposure_label = "无事件"
        elif assignment.arm == "new" and "old" in versions and versions == {"old"}:
            # 分派新版却全程走旧版受理（自有事件可证）
            exposure_label = "新版分派全程旧版"
        elif assignment.arm == "new" and "old" in versions:
            exposure_label = "新版分派新旧版混合"
        else:
            exposure_label = "/".join(sorted(versions))  # 版本集合
        # sorted(versions) 已是 list[str]；join 的元素是版本名而非字符
        groups.setdefault((assignment.arm, exposure_label), []).append(aid)
    rows = [
        FunnelRow(
            label=f"分派={arm} 实际曝光={vers}",
            n=len(ids),
            completed=sum(a in completed for a in ids),
            no_submit=sum(a not in completed for a in ids),
            required_counter=sum(a in required for a in ids),
            extra_counter=sum(a in extra for a in ids),
        )
        for (arm, vers), ids in sorted(groups.items())
    ]
    unidentified = sorted(
        {e.id for e in ds.journeys if not e.assignment and e.id not in identified}
    )
    return rows, unidentified


def arm_funnel(ds: Dataset, scenario: LinkageScenario) -> list[FunnelRow]:
    completed, extra, required = _collect(ds, scenario)
    rows: list[FunnelRow] = []
    for arm in ("old", "new"):
        ids = [a.id for a in ds.assignments.values() if a.arm == arm]
        rows.append(
            FunnelRow(
                label=f"ITT 分派={arm}",
                n=len(ids),
                completed=sum(a in completed for a in ids),
                no_submit=sum(a not in completed for a in ids),
                required_counter=sum(a in required for a in ids),
                extra_counter=sum(a in extra for a in ids),
            )
        )
    return rows


def cross_over_assignments(ds: Dataset) -> list[str]:
    """分派新版却全程/部分走旧版的试验单位（仍归新版 ITT 组）。"""
    out: list[str] = []
    by_assignment: dict[str, set[str]] = {}
    for event in ds.journeys:
        if event.assignment:
            by_assignment.setdefault(event.assignment, set()).add(event.version)
    for aid, versions in sorted(by_assignment.items()):
        arm = ds.assignments[aid].arm
        if arm == "new" and "old" in versions:
            out.append(aid)
    return out


def _collect(ds: Dataset, scenario: LinkageScenario):
    completed: set[str] = set()
    extra: set[str] = set()
    required: set[str] = set()
    for event in ds.journeys:
        target = event.assignment or scenario.mapping.get(event.id)
        if not target:
            continue
        if event.step == "submit" and event.result == "complete":
            completed.add(target)
        elif event.step == "counter" and event.result == "extra":
            extra.add(target)
        elif event.step == "counter" and event.result == "required":
            required.add(target)
    return completed, extra, required
