---
title: "PX4 仿真飞行报告"
lang: zh-CN
---

# 任务概况

飞行编号：{{flightId}}。目标：{{vehicleId}}。本文记录一次仿真，不代表真机验证。

# 航线与基础审查

{{planSummary}}

**基础审查未验证障碍物与地形，不代表已完成全面安全验证。**

# 审批与执行记录

| 时间（UTC） | 事件 | 回执或内容 |
|---|---|---|
{{eventRows}}

# 飞行结果

{{outcome}}

# 数据指标

统计来源：{{metricSource}}；仅反映本次执行期间实际采集的数据。

| 指标 | 结果 |
|---|---|
{{metricRows}}

# 异常与数据缺失

{{missing}}

# 原始资料与复核

{{artifacts}}

原始日志、Markdown 与 DOCX 应一起归档。报告格式版本：px4-flight-report-v1。缺失的数据没有推测补写。
