# 自助办理改版观察

本项目用于整理社区服务运营数据。`fixtures/` 保存本地合成源记录，各表字段和业务时序约定见 `DATA.md`。没有线上服务或真实个人信息。

## 验证资料结构

使用 Python 3.11：

```sh
python3 -m unittest discover -s tests -v
```

## 生成站点组合审议报告

分析代码在 `src/rollout_review/`，口径见 `ANALYSIS.md`：

```sh
python3 -I run_review.py --fixtures fixtures --out reports/review.md
```

报告只使用本地记录，覆盖：模糊身份关联的相容情景、48 小时窗成熟度、ITT 区组
评价与全情景包络、实际曝光漏斗（非因果）、逐站授权范围、续办承诺排班试算、
切换单状态与决定溯源。
