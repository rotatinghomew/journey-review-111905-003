# 审议方法与口径说明（ANALYSIS）

本目录代码把 `fixtures/` 的本地合成记录转成可复核的站点组合审议结论，
入口为 `run_review.py`，报告产物为 `reports/review.md`。全程只读本地 CSV，
不访问外部服务，不使用真实个人信息。

## 运行

```sh
python3 -I run_review.py --fixtures fixtures --out reports/review.md
python3 -m unittest discover -s tests -v
```

`-I` 用于隔离环境，避免工作目录中的同名模块被解释器加载。

## 模块与口径

| 模块 | 职责 |
|---|---|
| `data.py` | 记录加载、证据时钟（最晚 `received`）、48 小时结果窗 |
| `linkage.py` | 模糊回执的相容关联枚举；confirmed/ruled_out 替代 possible；TEMP1/TEMP2 互斥 |
| `outcomes.py` | ITT 结局、覆盖成熟度、区组/政策加权评价、逐站评价 |
| `exposure.py` | 实际曝光漏斗与跨版本回退复盘（非因果，禁止据此重组对照组） |
| `appraisal.py` | 全情景稳健性包络、逐站授权与证据缺口 |
| `capacity.py` | supersedes 班表、共享资源回溯排班、15 分钟跨站间隔、替代履约诊断 |
| `rollout.py` | 切换单状态机（proposal/enabled/paused/resumed）与决定溯源 |
| `report.py` | Markdown 报告装配 |

### 分组与窗口

- 试验单位是 `assignment.id`，分组只认首次随机分派臂。分派新版却经柜面回到旧版
  的 24 个单位仍在新版 ITT 组；不按浏览新版或最终成功者另组对照。
- 完成与额外回柜窗均为首次分派后 48 小时。整窗结束且 `coverage.through`
  覆盖到窗末才成熟；未成熟单位既不算失败也不计入放行人数。
- `required` 是产品本就要求的柜面核验（仅 Q1 出现），`extra` 才是真正额外回柜，
  两者分别统计。

### 关联枚举

- `JZ` 由 `X7` confirmed 至 `A2-3-01`，`X8` 排除 `A2-3-02`；被 supersedes 的
  `X5/X6` 不再使用。
- `JX`、`JY` 各有两个 possible 候选，且 TEMP1/TEMP2 属不同居民，不能并到同一分派，
  共得到 3 种相容关联。批准要求 3 种关联下两个指标都达界。
- 无法识别的事件不强行归因，在报告中标为证据缺口；实际曝光分析不作因果结论。

### 评价公式

- 区组内：新版率 − 旧版率（按人比例差）。
- 区组间：按两臂成熟人数合计计权。
- 界（绝对差）：完成率最多降 `completion_drop=0.05`；额外回柜率最多增
  `extra_increase=0.10`。每臂成熟样本需 ≥ `min_arm=12`。
- 评价边界只用于本审议，不是总体置信区间；政策结论不跨办理类别借用。

### 容量与切换

- `staff.csv` 的 supersedes 生效后，移动资源 U1 只剩 13:40–15:00（R4）。
- 续办承诺全量参与排班（含退出试验者）；同一资源同期单服务，跨站留 15 分钟。
- 切换单以 `received` 时序沿 `prior` 链推进；proposal 不是启用，未确认部分按旧受理；
  暂停只停受影响新办类别，已受理记录按承诺规则办完；后续意见不覆盖已签发范围。

## 决定溯源

`rollout.trace_decision(ds, station, policy)` 可从任一站点/政策决定查到：
回执链与时点、当前可接办理范围、在途续办承诺及所需证件/认定（`legacy_proof`、
`carry_to_new`），数据全部来自本地记录。
