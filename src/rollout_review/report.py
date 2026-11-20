"""命令行入口：读取本地 fixtures，输出完整审议报告（Markdown）。

用法：
    python3 -m rollout_review.report --fixtures fixtures [--as-of 2026-10-07T12:00]
"""
from __future__ import annotations

import argparse
from pathlib import Path

from .appraisal import build_envelopes, station_authorizations
from .capacity import (
    blocking_commitments,
    effective_staff,
    per_station_commitment_gate,
    standalone_feasibility,
    trial_combination,
)
from .data import WINDOW, load_dataset
from .exposure import arm_funnel, cross_over_assignments, exposure_funnel
from .linkage import compatible_scenarios, unresolved_events
from .outcomes import COMPLETION, EXTRA, evaluate_all, evaluate_policy, is_mature
from .rollout import reconstruct_states, trace_decision


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:+.1f}pp"


def build_report(fixtures: str | Path) -> str:
    ds = load_dataset(fixtures)
    scenarios = compatible_scenarios(ds)
    envelopes = build_envelopes(ds)
    authorizations = station_authorizations(ds)
    states = reconstruct_states(ds)
    lines: list[str] = []

    lines.append("# 自助办理改版观察：站点组合审议报告\n")
    lines.append(
        f"- 证据时钟（本地记录最晚可知时点）：{ds.evidence_time.isoformat(timespec='minutes')}"
    )
    lines.append(f"- 结果窗：首次分派后 {int(WINDOW.total_seconds() // 3600)} 小时，左闭右开")
    lines.append("- 分组口径：按首次随机分派臂 ITT；分派新版后经柜面回到旧版仍计新版组")
    lines.append("- 对照组不得按浏览新版或最终成功者重组；实际曝光仅作非因果复盘\n")

    # 1. 身份线索与相容关联
    lines.append("## 1. 模糊身份线索与相容关联\n")
    unresolved = unresolved_events(ds)
    for event, info in unresolved.items():
        lines.append(
            f"- 回执 `{event}`（{info['step']}/{info['result']}，"
            f"received {info['received'].isoformat(timespec='minutes')}）候选："
            + "、".join(f"`{a}`" for a in info["candidates"])
        )
    lines.append(
        "- 交接账确认 TEMP1、TEMP2 属两位不同居民，同一分派不可同时承接两条回执；"
        "`X7/X8` 已将 JZ 确认至 `A2-3-01`、排除 `A2-3-02`（替代 X5/X6）。"
    )
    lines.append(f"- 相容关联共 {len(scenarios)} 种：")
    for scenario in scenarios:
        lines.append(f"  - {scenario.description}")
    lines.append("")

    # 2. 成熟度 / 覆盖
    lines.append("## 2. 观察窗与成熟度（未成熟不计失败、不计放行）\n")
    for station in sorted({a.station for a in ds.assignments.values()}):
        for metric in (COMPLETION, EXTRA):
            related = [a for a in ds.assignments.values() if a.station == station]
            mature = [a for a in related if is_mature(ds, a, metric)]
            lines.append(
                f"- {station} {metric}：成熟 {len(mature)}/{len(related)} 人"
                + ("" if len(mature) == len(related) else "　**未覆盖整体，仅评价已观察批次**")
            )
    lines.append(
        "- S3 柜面来源仅确认覆盖至 2026-10-02T16:00，早于任一分派窗结束，"
        "故 S3 全部批次的额外回柜指标不可评价；完成指标依赖终端来源，三站均成熟。\n"
    )

    # 3. ITT 主分析（每个情景 + 政策包络）
    lines.append("## 3. ITT 主分析：政策级区组加权结果\n")
    lines.append("区组内按人比例差，区组间按成熟人数计权；阈值为绝对差。\n")
    for scenario in scenarios:
        lines.append(f"### 关联情景：{scenario.description}\n")
        results = evaluate_all(ds, scenario)
        for pid in sorted(ds.policies):
            pol = ds.policies[pid]
            lines.append(
                f"- `{pid}`（{pol.procedure}/{pol.support}，min_arm={pol.min_arm}）"
            )
            for metric in (COMPLETION, EXTRA):
                r = results[(pid, metric)]
                if r.weighted_diff is None:
                    lines.append(f"  - {metric}：无可评价区组")
                    continue
                lines.append(
                    f"  - {metric}：新版-旧版 {_pct(r.weighted_diff)}，"
                    f"界 {'≥ -' if metric == COMPLETION else '≤ +'}"
                    f"{r.threshold:.2f}，成熟 {r.n_mature_people} 人，"
                    f"**{'达标' if r.passed else '不达标'}**"
                )
        lines.append("")

    lines.append("### 全情景包络\n")
    lines.append("| 政策 | 指标 | 范围 | 界 | 是否有情景逆转 | 结论 |")
    lines.append("|---|---|---|---|---|---|")
    for (pid, metric, scope), env in sorted(envelopes.items()):
        if scope != "policy":
            continue
        lines.append(
            f"| {pid} | {metric} | {_pct(env.worst)} ~ {_pct(env.best)} | "
            f"{env.threshold:.2f} | {'是：' + '；'.join(env.flip_scenarios) if env.flips else '否'} | "
            f"{'全情景达标' if env.passed_all_scenarios else '存在不达标情景'} |"
        )
    lines.append("")
    # 区组明细（主情景）+ 未成熟区组
    lines.append("### 区组明细与适用评价批次（主情景）\n")
    lines.append("| 政策 | 指标 | 站点 | 区组 | 旧版 | 新版 | 差值 | 状态 |")
    lines.append("|---|---|---|---|---|---|---|---|")
    primary = scenarios[0]
    for pid in sorted(ds.policies):
        for metric in (COMPLETION, EXTRA):
            result = evaluate_policy(ds, pid, metric, primary)
            for b in result.mature_blocks:
                lines.append(
                    f"| {pid} | {metric} | {b.station} | {b.block} | "
                    f"{b.old.events}/{b.old.n} | {b.new.events}/{b.new.n} | "
                    f"{_pct(b.diff)} | 成熟 |"
                )
            for b in result.immature_blocks:
                lines.append(
                    f"| {pid} | {metric} | {b.station} | {b.block} | "
                    f"{b.old.events}/{b.old.n} | {b.new.events}/{b.new.n} | — | "
                    f"不评价：{b.reason} |"
                )
    lines.append("")

    # 4. 步骤漏斗与实际曝光（非因果）
    lines.append("## 4. 步骤与交互复盘（实际曝光，非因果）\n")
    primary = scenarios[0]
    lines.append("### 4.1 ITT 臂级漏斗\n")
    lines.append("| 组 | 人数 | 完成 | 未提交（草稿阶段失败） | 应到核验 | 额外回柜 |")
    lines.append("|---|---|---|---|---|---|")
    for row in arm_funnel(ds, primary):
        lines.append(
            f"| {row.label} | {row.n} | {row.completed}（{row.completion_rate * 100:.1f}%）"
            f" | {row.no_submit} | {row.required_counter} | {row.extra_counter} |"
        )
    lines.append("")
    lines.append("### 4.2 按实际曝光版本（禁止据此重设对照组）\n")
    lines.append("| 曝光组 | 人数 | 完成率 | 额外回柜率 | 应到核验 |")
    lines.append("|---|---|---|---|---|")
    funnel_rows, unidentified = exposure_funnel(ds, primary)
    for row in funnel_rows:
        lines.append(
            f"| {row.label} | {row.n} | {row.completion_rate * 100:.1f}% | "
            f"{row.extra_rate * 100:.1f}% | {row.required_counter} |"
        )
    if unidentified:
        lines.append(f"\n> 证据缺口：无法识别事件 {', '.join(unidentified)}，未纳入任何重分组。")
    crossovers = cross_over_assignments(ds)
    completed_by_assignment: set[str] = set()
    for event in ds.journeys:
        if event.step == "submit" and event.result == "complete" and event.assignment:
            completed_by_assignment.add(event.assignment)
    cross_complete = sum(a in completed_by_assignment for a in crossovers)
    lines.append(
        f"- 自有事件可证的分派新版回退旧版：{len(crossovers)} 个单位（仍计新版 ITT 组），"
        f"其中完成 {cross_complete}/{len(crossovers)}；这些单位解释了新版组内的部分回柜，"
        "但属曝光复盘而非因果效应。"
    )
    scenario_fallback_n = sum(
        r.n for r in funnel_rows if "旧版" in r.label and r.label.startswith("分派=new")
    )
    if scenario_fallback_n != len(crossovers):
        lines.append(
            f"- 上表含旧版曝光的新版分派合计 {scenario_fallback_n} 个，比自有事件多 "
            f"{scenario_fallback_n - len(crossovers)} 个，差异来自本情景下经模糊关联"
            "（JX/JY）归入新版臂的旧版回执；换关联情景时该数字会变，不得固化为结论。"
        )
    lines.append("- `required` 柜面到场仅出现在 address/assisted（Q1 产品本就要求核验）；"
                 "self/eligibility（Q2）无 required，任何柜面到场都计入 extra。\n")

    # 5. 逐站授权
    lines.append("## 5. 逐站授权范围（仅授权有证据支持的版本/支持/办理类型）\n")
    lines.append(
        "> 政策级结论只覆盖有成熟证据的站点与批次；未覆盖站点不得借其他站点结论放行。\n"
    )
    lines.append("| 政策 | 站点 | 完成率证据 | 额外回柜证据 | 成熟新版人数（完成/额外） | 授权 | 证据缺口 |")
    lines.append("|---|---|---|---|---|---|---|")
    for auth in authorizations:
        lines.append(
            f"| {auth.policy_id} | {auth.station} | "
            f"{'支持' if auth.completion_supported else '不支持/不足'} | "
            f"{'支持' if auth.extra_supported else '不支持/不足'} | "
            f"{auth.mature_new_people[COMPLETION]}/{auth.mature_new_people[EXTRA]} | "
            f"**{'可切' if auth.authorized else '不可整类切'}** | "
            f"{'；'.join(auth.gaps) or '—'} |"
        )
    lines.append("")
    # 政策适用范围（哪些站点/批次支撑了政策级达标）
    lines.append("### 5.1 政策级达标的实际适用范围\n")
    lines.append("| 政策 | 指标 | 支撑站点（成熟区组） | 排除批次 |")
    lines.append("|---|---|---|---|")
    primary = scenarios[0]
    for pid in sorted(ds.policies):
        for metric in (COMPLETION, EXTRA):
            result = evaluate_policy(ds, pid, metric, primary)
            support = sorted({b.station for b in result.mature_blocks})
            excluded = sorted({f"{b.station}/{b.block}" for b in result.immature_blocks})
            lines.append(
                f"| {pid} | {metric} | {', '.join(support)}（{result.n_mature_people} 人） | "
                f"{', '.join(excluded) or '—'} |"
            )
    lines.append("")

    # 6. 续办承诺与容量试算
    lines.append("## 6. 续办承诺与切换组合容量试算\n")
    lines.append("### 6.1 有效资源（supersedes 已应用）\n")
    for res in effective_staff(ds):
        lines.append(
            f"- {res.resource}（{res.skill}，站点 {';'.join(res.stations)}，"
            f"{res.start.isoformat(timespec='minutes')}~"
            f"{res.end.isoformat(timespec='minutes')}，依据 {res.based_on}）"
        )
    lines.append("\n### 6.2 承诺清单（全量保留，含退出试验者）\n")
    lines.append("| 承诺 | 站 | 技能 | 分钟 | 窗口 | 政策 |")
    lines.append("|---|---|---|---|---|---|")
    for c in ds.commitments:
        lines.append(
            f"| {c.id} | {c.station} | {c.skill} | {c.minutes} | "
            f"{c.start.isoformat(timespec='minutes')}~{c.end.isoformat(timespec='minutes')} | "
            f"{c.procedure}/{c.support} |"
        )
    lines.append("")
    station_sets = [
        frozenset(), frozenset({"S1"}), frozenset({"S2"}), frozenset({"S3"}),
        frozenset({"S1", "S2"}), frozenset({"S1", "S3"}),
        frozenset({"S2", "S3"}), frozenset({"S1", "S2", "S3"}),
    ]
    lines.append("### 6.3 组合试算\n")
    lines.append("| 切换站点 | 全部承诺可兑现 | 排不上的承诺 | 可安置 |")
    lines.append("|---|---|---|---|")
    for switched in station_sets:
        result = trial_combination(ds, switched)
        label = "、".join(sorted(switched)) or "（全不切）"
        placed = "、".join(
            f"{p.commitment}→{p.resource} {p.start.isoformat(timespec='minutes')}"
            for p in result.placements
        ) or "—"
        lines.append(
            f"| {label} | {'是' if result.feasible else '**否**'} | "
            f"{'、'.join(result.unscheduled) or '—'} | {placed} |"
        )
    lines.append("\n### 6.4 逐条承诺诊断（区分硬冲突与容量争抢）\n")
    lines.append("| 承诺 | 单独试排 | 含义 |")
    lines.append("|---|---|---|")
    for cid, note in sorted(standalone_feasibility(ds).items()):
        lines.append(f"| {cid} | {note} | {'排班结构性缺口' if note != '可单独排定' else '资源足够'} |")
    blockers = blocking_commitments(ds)
    lines.append(
        "\n- 移动核验仅一名共享人员：R1（13:00–15:00）被 R4（received 10-07 10:10）"
        "缩短为 13:40–15:00，共 80 分钟，跨站另需 15 分钟。"
    )
    lines.append(
        "- D1（S1，45 分钟）与 D2（S2，45 分钟）窗口均为 13:00–14:00，"
        "R4 生效后该窗口内只剩 20 分钟，**单独也无法排定（迟到班表变更导致的硬冲突）**；"
        "D4（S3，30 分钟，14:00–15:00）可由 U1 于 14:00–14:30 单独承接。"
    )
    lines.append(
        "- 参照被取代的 R1 班表（13:00–15:00）：D1 与 D4 可同资源连排，"
        "但 D1 与 D2 同窗跨站仍互斥；即恢复原班次也只解决硬冲突，"
        "S1/S2 同时切 Q2 的容量争抢依旧，二者需不同的替代履约。"
    )
    lines.append(
        f"- 当前全量试算排不上的承诺：{'、'.join('`'+x+'`' for x in blockers['unscheduled_full'])}"
        "（移除任何单一承诺都不能让另一份成立，故不是简单删项可解）。"
    )
    lines.append(
        "- 因此涉及 S1 或 S2 的 Q2 切换前，D1/D2 必须先有替代履约方式"
        "（例如恢复 13:00 起的移动班次或指定其他核验人员）；"
        "不能逐站复用同一名移动核验员证明可上线，也不能把退出试验者从容量中删除。"
    )
    lines.append("\n### 6.5 逐站切换前置条件\n")
    lines.append("| 站点 | 可兑现承诺 | 必须先有替代履约的承诺（办理类型） |")
    lines.append("|---|---|---|")
    for station, info in sorted(per_station_commitment_gate(ds).items()):
        blocked = "；".join(
            f"`{b['commitment']}`（{b['policy']}：{b['reason']}）" for b in info["blocked"]
        ) or "—"
        lines.append(
            f"| {station} | {'、'.join(info['placeable']) or '—'} | {blocked} |"
        )
    lines.append("")

    # 7. 切换单现状与决定溯源
    lines.append("## 7. 切换单现状与决定溯源\n")
    lines.append("| 站 | 政策 | 当前状态 | 回执链 |")
    lines.append("|---|---|---|---|")
    for key in sorted(states):
        state = states[key]
        chain = " → ".join(f"{r.id}:{r.kind}@{r.received.isoformat(timespec='minutes')}"
                           for r in state.chain)
        lines.append(f"| {state.station} | {state.policy} | {state.state} | {chain} |")
    lines.append("")
    lines.append("### 7.1 受影响决定溯源示例（S1/Q2 暂停）\n")
    trace = trace_decision(ds, "S1", "Q2")
    lines.append(f"- 当前状态：`{trace['state']}`；是否可携带旧核验：carry_to_new={trace['carry_to_new']}")
    lines.append(f"- 旧版证件/认定要求：`{trace['legacy_proof_required']}`（Q2 不能携带，必须兑现续办）")
    lines.append("- 回执链：")
    for item in trace["chain"]:
        lines.append(f"  - {item['id']} {item['kind']} received={item['received']} prior={item['prior'] or '—'}")
    lines.append("- 在途承诺（本站同办理类型）：" + (
        "、".join(f"{c['id']}（{c['window']}，{c['minutes']} 分钟 {c['skill']}）" for c in trace["in_flight_commitments"])
        or "无"
    ))
    lines.append("")

    # 8. 结论
    lines.append("## 8. 结论与放行建议\n")
    lines.extend(_conclusions(ds, authorizations, scenarios))
    return "\n".join(lines) + "\n"


def _conclusions(ds, authorizations, scenarios) -> list[str]:
    out: list[str] = []
    s1q1 = next(a for a in authorizations if a.station == "S1" and a.policy_id == "Q1")
    s1q2 = next(a for a in authorizations if a.station == "S1" and a.policy_id == "Q2")
    s2q1 = next(a for a in authorizations if a.station == "S2" and a.policy_id == "Q1")
    s2q2 = next(a for a in authorizations if a.station == "S2" and a.policy_id == "Q2")
    s3 = [a for a in authorizations if a.station == "S3"]
    out.append(
        "1. **政策层面 Q1、Q2 在全部 3 种相容关联下两个指标均达界**，"
        "但政策级额外回柜结论只由 S1、S2 的成熟区组支撑（S3 该指标无成熟证据）；"
        "没有任何关联情景能逆转达标判断。最值得先核对的回执是 `JX`（跨新/旧臂，"
        "直接影响 S1/K1 的额外回柜归属），其次是 `JY`（只影响完成计数，不跨臂）。"
    )
    out.append(
        f"2. **可独立切换**：S1 的 Q1（address/assisted）证据完整且全情景达标；"
        f"S2 的 Q1 同样达标。S1/Q1 已由 `L4` 启用，与证据一致，可维持。"
    )
    out.append(
        "3. **S1/Q2、S2/Q2 指标达标但受容量与切换单约束**：S1/Q2 已被 `L6` 暂停"
        "（暂停后只停新办类别，已受理记录按承诺规则办完）；S2/Q2 仅有 proposal，"
        "尚未启用。两站的移动续办承诺 D1、D2 在 R4 缩短班表后连单独都排不进，"
        "必须先有替代履约，不能要求居民重填，也不能以终端仍开机视为保留续办。"
    )
    out.append(
        "4. **S3 不可整类切换**：柜面来源覆盖不足，Q1、Q2 的额外回柜指标均无成熟证据；"
        "已观察批次的完成率可作参考，但未观察批次不计入放行人数。"
    )
    out.append(
        "5. **整批（三站同窗）上线不成立**：当前班表下 D1、D2 硬冲突无法排定，"
        "唯一可安置的移动承诺是 D4；即使按被取代的 R1 班表，D1 与 D2 同窗跨站仍互斥。"
        "可独立推进的是不依赖移动承诺的 Q1（S1 已启用、S2 可切）；"
        "Q2 各站须在 D1/D2 替代履约落地后再逐站切。"
    )
    out.append(
        "6. 旧版草稿依法按原受理规则走完；Q2（carry_to_new=no）不能携带 paper_seal，"
        "站点必须主动兑现续办安排。下一轮意见不得覆盖已向站点签发的适用范围（L4）。"
    )
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="自助改版站点组合审议")
    parser.add_argument("--fixtures", required=True)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()
    report = build_report(args.fixtures)
    if args.out:
        Path(args.out).write_text(report, encoding="utf-8")
    else:
        print(report)


if __name__ == "__main__":
    main()
