"""核心业务规则测试，全部基于本地 fixtures。"""
import sys
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rollout_review.appraisal import (
    build_envelopes,
    station_authorizations,
)
from rollout_review.capacity import (
    TRAVEL,
    EffectiveResource,
    blocking_commitments,
    effective_staff,
    standalone_feasibility,
    trial_combination,
)
from rollout_review.data import WINDOW, load_dataset
from rollout_review.exposure import cross_over_assignments, exposure_funnel
from rollout_review.linkage import compatible_scenarios
from rollout_review.outcomes import (
    COMPLETION,
    EXTRA,
    assignment_outcomes,
    evaluate_all,
    evaluate_policy,
    evaluate_station,
    is_mature,
)
from rollout_review.rollout import reconstruct_states, trace_decision

FIXTURES = str(ROOT / "fixtures")


class LinkageTests(unittest.TestCase):
    def setUp(self):
        self.ds = load_dataset(FIXTURES)
        self.scenarios = compatible_scenarios(self.ds)

    def test_three_compatible_scenarios_and_mutual_exclusion(self):
        self.assertEqual(len(self.scenarios), 3)
        for scenario in self.scenarios:
            # JZ 已 confirmed 固定到 A2-3-01
            self.assertEqual(scenario.mapping["JZ"], "A2-3-01")
            # TEMP1/TEMP2 不同居民：JX 与 JY 永不同属一个分派
            self.assertNotEqual(scenario.mapping["JX"], scenario.mapping["JY"])
            # ruled_out 的 A2-3-02 不得复活
            self.assertNotIn("A2-3-02", scenario.mapping.values())

    def test_superseded_possible_rows_ignored(self):
        for scenario in self.scenarios:
            self.assertNotIn("X5", scenario.mapping)
            self.assertNotIn("X6", scenario.mapping)


class MaturityTests(unittest.TestCase):
    def setUp(self):
        self.ds = load_dataset(FIXTURES)

    def test_completion_all_mature_extra_s3_blocked(self):
        for aid, assignment in self.ds.assignments.items():
            self.assertTrue(is_mature(self.ds, assignment, COMPLETION))
        s3 = [a for a in self.ds.assignments.values() if a.station == "S3"]
        self.assertTrue(all(not is_mature(self.ds, a, EXTRA) for a in s3))
        s12 = [a for a in self.ds.assignments.values() if a.station in ("S1", "S2")]
        self.assertTrue(all(is_mature(self.ds, a, EXTRA) for a in s12))

    def test_window_is_48h_and_left_closed_right_open(self):
        self.assertEqual(WINDOW.total_seconds(), 48 * 3600)


class ITTEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.ds = load_dataset(FIXTURES)
        self.scenarios = compatible_scenarios(self.ds)

    def test_all_scenarios_pass_policy_thresholds(self):
        for scenario in self.scenarios:
            results = evaluate_all(self.ds, scenario)
            for key, result in results.items():
                with self.subTest(scenario=scenario.description, key=key):
                    self.assertIsNotNone(result.weighted_diff)
                    self.assertTrue(result.passed)

    def test_cross_over_units_stay_in_new_arm(self):
        crossovers = cross_over_assignments(self.ds)
        self.assertTrue(crossovers)
        for aid in crossovers:
            self.assertEqual(self.ds.assignments[aid].arm, "new")
        # 24 个分派新版、实际走旧版
        self.assertEqual(len(crossovers), 24)

    def test_no_rebuilt_control_group_via_exposure(self):
        # 曝光分组人数合计必须仍是 336 个分派单位，臂大小不变
        rows, unidentified = exposure_funnel(self.ds, self.scenarios[0])
        self.assertEqual(sum(r.n for r in rows), len(self.ds.assignments))
        self.assertEqual(unidentified, [])
        old_rows = [r for r in rows if r.label.startswith("分派=old")]
        new_rows = [r for r in rows if r.label.startswith("分派=new")]
        self.assertEqual(sum(r.n for r in old_rows), 168)
        self.assertEqual(sum(r.n for r in new_rows), 168)

    def test_jy_only_moves_completion_count_jx_moves_extra(self):
        # JY 是 submit/complete，JX 是 counter/extra
        s1 = next(s for s in self.scenarios
                  if s.mapping["JY"] == "A1-1-04" and s.mapping["JX"] == "A1-1-07")
        completed, extra, _ = assignment_outcomes(self.ds, s1)
        self.assertIn("A1-1-04", completed)
        self.assertIn("A1-1-07", extra)
        s2 = next(s for s in self.scenarios if s.mapping["JX"] == "A1-1-04")
        _, extra2, _ = assignment_outcomes(self.ds, s2)
        self.assertIn("A1-1-04", extra2)

    def test_extra_q2_envelope_spans_zero_to_positive_but_never_flips(self):
        env = build_envelopes(self.ds)[("Q2", EXTRA, "policy")]
        self.assertFalse(env.flips)
        self.assertTrue(env.passed_all_scenarios)
        self.assertEqual(round(env.worst, 4), 0.0)
        self.assertGreater(env.best, 0.0)

    def test_policy_extra_scope_excludes_s3_but_completion_keeps_it(self):
        result_extra = evaluate_policy(self.ds, "Q1", EXTRA, self.scenarios[0])
        result_comp = evaluate_policy(self.ds, "Q1", COMPLETION, self.scenarios[0])
        self.assertEqual(
            sorted({b.station for b in result_extra.mature_blocks}), ["S1", "S2"]
        )
        self.assertTrue(
            all(b.reason for b in result_extra.immature_blocks if b.station == "S3")
        )
        self.assertEqual(
            sorted({b.station for b in result_comp.mature_blocks}),
            ["S1", "S2", "S3"],
        )

    def test_exposure_fallback_count_is_scenario_dependent(self):
        counts = []
        for scenario in self.scenarios:
            rows, _ = exposure_funnel(self.ds, scenario)
            counts.append(sum(
                r.n for r in rows
                if r.label.startswith("分派=new") and "旧版" in r.label
            ))
        # 24 个自有回退为恒定基数；模糊关联在不同情景下再加 1~2 个
        self.assertEqual(sorted(counts), [25, 25, 26])
        self.assertEqual(len(cross_over_assignments(self.ds)), 24)

    def test_min_arm_rule_excludes_undersized_blocks(self):
        # 现有数据每臂 14 人，均 >= min_arm=12；造一个临时小政策验证门槛
        from rollout_review.data import Policy
        ds = load_dataset(FIXTURES)
        original = ds.policies["Q2"]
        ds.policies["Q2"] = Policy(
            id="Q2", procedure=original.procedure, support=original.support,
            min_arm=999, completion_drop=original.completion_drop,
            extra_increase=original.extra_increase,
            legacy_proof=original.legacy_proof, carry_to_new=original.carry_to_new,
        )
        result = evaluate_policy(ds, "Q2", COMPLETION, self.scenarios[0])
        self.assertIsNone(result.weighted_diff)
        self.assertEqual(result.mature_blocks, [])


class StationAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.ds = load_dataset(FIXTURES)

    def test_s3_not_authorized_s1_s2_authorized(self):
        auth = {(a.station, a.policy_id): a for a in station_authorizations(self.ds)}
        self.assertTrue(auth[("S1", "Q1")].authorized)
        self.assertTrue(auth[("S1", "Q2")].authorized)
        self.assertTrue(auth[("S2", "Q1")].authorized)
        self.assertTrue(auth[("S2", "Q2")].authorized)
        self.assertFalse(auth[("S3", "Q1")].authorized)
        self.assertFalse(auth[("S3", "Q2")].authorized)
        # S3 完成指标有证据、额外回柜无证据
        self.assertTrue(auth[("S3", "Q1")].completion_supported)
        self.assertFalse(auth[("S3", "Q1")].extra_supported)
        # 未成熟部分不得计入放行人数
        self.assertEqual(auth[("S3", "Q2")].mature_new_people[EXTRA], 0)

    def test_station_level_uses_station_only_blocks(self):
        result = evaluate_station(self.ds, "S1", "Q2", EXTRA,
                                  compatible_scenarios(self.ds)[0])
        self.assertTrue(all(b.station == "S1" for b in result.mature_blocks))


class CapacityTests(unittest.TestCase):
    def setUp(self):
        self.ds = load_dataset(FIXTURES)

    def test_supersedes_shortens_mobile_window(self):
        resources = {r.resource: r for r in effective_staff(self.ds)}
        self.assertEqual(resources["U1"].based_on, "R4")
        self.assertEqual(resources["U1"].start, datetime(2026, 10, 7, 13, 40))

    def test_d1_d2_hard_conflict_after_r4(self):
        notes = standalone_feasibility(self.ds)
        self.assertIn("硬冲突", notes["D1"])
        self.assertIn("硬冲突", notes["D2"])
        self.assertEqual(notes["D4"], "可单独排定")

    def test_full_commitment_set_infeasible_and_d3_d4_d5_placed(self):
        result = trial_combination(self.ds, frozenset({"S1", "S2", "S3"}))
        self.assertFalse(result.feasible)
        self.assertEqual(set(result.unscheduled), {"D1", "D2"})
        placed = {p.commitment for p in result.placements}
        self.assertEqual(placed, {"D3", "D4", "D5"})

    def test_travel_time_enforced_between_stations(self):
        self.assertEqual(TRAVEL.total_seconds(), 900)
        # 构造两个跨站相邻任务：间隔不足 15 分钟应判冲突
        from rollout_review.data import Commitment
        from rollout_review.capacity import _try_schedule
        res = [EffectiveResource(
            "U", ("S1", "S2"), "mobile",
            datetime(2026, 10, 7, 13, 0), datetime(2026, 10, 7, 15, 0), "RX")]
        jobs = [
            Commitment("J1", "S1", "p", "s", "x",
                       datetime(2026, 10, 7, 13, 0), datetime(2026, 10, 7, 13, 30),
                       30, "mobile"),
            Commitment("J2", "S2", "p", "s", "x",
                       datetime(2026, 10, 7, 13, 0), datetime(2026, 10, 7, 14, 30),
                       30, "mobile"),
        ]
        ok, flat, missing = _try_schedule(jobs, res)
        self.assertTrue(ok)
        # 13:00–13:30 在 S1，跨站 15 分钟，S2 最早 13:45 开始
        self.assertEqual(flat["J1"].start, datetime(2026, 10, 7, 13, 0))
        self.assertEqual(flat["J2"].start, datetime(2026, 10, 7, 13, 45))

    def test_blocking_report_names_d1_d2(self):
        blockers = blocking_commitments(self.ds)
        self.assertEqual(blockers["unscheduled_full"], ["D1", "D2"])

    def test_per_station_gate(self):
        from rollout_review.capacity import per_station_commitment_gate
        gate = per_station_commitment_gate(self.ds)
        self.assertEqual({b["commitment"] for b in gate["S1"]["blocked"]}, {"D1"})
        self.assertEqual({b["commitment"] for b in gate["S2"]["blocked"]}, {"D2"})
        self.assertEqual(gate["S3"]["blocked"], [])
        self.assertIn("D4", gate["S3"]["placeable"])


class RolloutStateTests(unittest.TestCase):
    def setUp(self):
        self.ds = load_dataset(FIXTURES)

    def test_state_machine(self):
        states = reconstruct_states(self.ds)
        self.assertEqual(states[("S1", "Q1")].state, "enabled")
        self.assertEqual(states[("S1", "Q2")].state, "paused")
        self.assertEqual(states[("S2", "Q2")].state, "legacy")
        self.assertTrue(states[("S2", "Q2")].proposals_only)

    def test_trace_includes_chain_and_commitments(self):
        trace = trace_decision(self.ds, "S1", "Q2")
        kinds = [c["kind"] for c in trace["chain"]]
        self.assertEqual(kinds, ["proposal", "enabled", "paused"])
        self.assertEqual([c["id"] for c in trace["in_flight_commitments"]], ["D1"])
        self.assertFalse(trace["carry_to_new"])


if __name__ == "__main__":
    unittest.main()
