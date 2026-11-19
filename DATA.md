# 资料约定

原始资料均为本地合成。CSV 使用 UTF-8，首行是字段名。

`episodes` 是各渠道内的流水根，`events` 标注当时实际页面和走到的步骤；issued 代表受理回执签发、blocked 代表记下的校验障碍，handed 是转接口记录。`links` 可以来自签发的交接码、工作人员更正或未证实的线索。approved 建立同一办理关系，rejected 表示证实不能关联，candidate 为待核；`replaces` 标识新证据代替关系；不能把不同事项合并。一个根流水可接续多个步骤，但独立的受理回执只能用于一个办理。记录都用日期加当地小时，低于日级精度的事实不补造小时。
`windows` 明确观察日与可用时段。`releases` 按窗口/日期记计划版本，为比较设计的参考，实际页面以事件为准。`orders` 为已提出或已采用的修订范围，不等同所有窗口同意更新。`limits` 的 min_per_path 是目标与对比都需要的完整路径数，min_change 是障碍率业务上有意义的绝对变化，maturity_days 从发起日算。没有可比较范围就不出升级结论。
`revision_relationships` 中 exclusive 双向互斥，requires 表示 from 不能在缺少 to 的有效改动时采用。`reviews` 是业务要求核对的同批范围，applied 指到该审查日已采用的改动，不是新批次要重新选择的项。
