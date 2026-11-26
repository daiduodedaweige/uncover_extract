---
name: coverage-point-analyzer
description: 分析现有 uncover-extract 报告中由规范 UC-000123 编号指定的单条 Toggle、Line、Branch 或 Condition 未覆盖点，查询原生事实、局部设计依赖及原因边界并生成可复核报告。适用于单点原因分析，不用于全设计统计或批量修改激励。
---

# 单条未覆盖点分析

由当前 Codex 执行分析与推理，包内脚本只做确定性查询和产物管理。Python 3.10+ 即可读取事实；KDB/NPI 需 Verdi 与许可证；显式备用后端需 `pyslang>=11,<12`。无需导入开发仓库。相对脚本路径均相对于本 `SKILL.md` 所在目录，执行时使用其绝对路径。

## 入口与定位

用户只通过报告原样的 `UC-[0-9]{6,}` 指定一条记录（如 `UC-000123`）。不接受小写、缺连字符、内部 target ID、信号名或自由描述替代编号。结果目录取当前会话已经明确的那一份；否则寻找 `instance_summary.json`，有多个候选或范围尚未明确时请用户明确。不要自行选择最近结果。

```bash
python3 <skill>/scripts/coverage_query.py resolve --result <结果目录或报告> --uc UC-000123
```

脚本检查页范围、该页记录和实例事实。错误编号、旧版无编号结果或报告不一致时解释问题并停止，不创建分析目录。无编号结果需先使用原工具 `python3 -m uncover_extract.rebuild_report --help` 确定重建参数，保留原结果，不猜测编号。UC 编号只在一份结果中有效。

唯一定位成功后初始化工作目录：

```bash
python3 <skill>/scripts/coverage_query.py begin --result <结果> --uc UC-000123
```

所有持久化输出（包括配置副本、草稿、导出片段和日志）放在 **项目根目录**的 `result_analyse_UC-000123/`。根目录优先取覆盖结果所在的 Git 仓库根；结果位于仓库外时取当前工作目录所属的 Git 根，仍无 Git 根时取当前工作目录。`resolve` 返回 `project_root`，后续查询保持这个输出位置。

脚本自动记录证据。已有同结果、同目标的结束运行会归档到 `runs/<run_id>/`；进行中运行默认继续使用，只有明确重新开始时使用 `begin --new-run`。同一项目根下不同结果的相同 UC 编号会占用同名目录；发现 manifest 的所属结果或目标身份不同则停止并说明冲突，不覆盖或混入。旧版位于覆盖结果目录内的分析应先迁移，再继续查询。保留原报告勾选状态、事实库、RTL、激励与排除配置。

## 分析流程

1. 从 `resolve` 结果读取实例、完整原生 locator、独立状态标志、数据库及测试集合身份。编号与内部 ID 分开处理。
2. 读取 [metrics.md](references/metrics.md) 的当前指标部分，再运行 `goal`。Branch/Condition 必须取得原生 term 顺序和值，不能按 bin 显示名称猜测解码；Toggle/Line 也可用 `native` 核对实际模型和配置。
3. 按 [design-backends.md](references/design-backends.md) 显式选择后端，优先匹配 KDB。执行 `check` 并检查对应状态及冲突；同名、路径相同、当前源码指纹相同和编译成功均不等于历史编译匹配。KDB 失败不得静默改用 pyslang；切换后端需保留本运行并开始新运行。
4. 使用 `inspect`/`trace` 及有限源码上下文解释局部表达式、外围控制、事件、赋值顺序和保持条件。每次展开对应一个具体问题，如“这个使能由谁产生”。利用共享表达式节点和已有查询证据，避免重复展开完整公式。
5. 选择对候选解释最有区分力的对象，通过 `related` 查询精确信号或源码行。源码行相同不保证是同一原生 statement/branch/condition，不能消除同一行多点的歧义。
6. 如提供匹配验证环境源码，可继续查顶层绑定、driver 赋值、默认值与约束。静态代码证明驱动能力，不证明历史测试执行过该赋值。
7. 整理条件、关键发现、原因判断和下一步，保留支持与反驳证据。按 [output-schema.md](references/output-schema.md) 编写草稿，并用 `analysis_report.py` 校验发布四个标准文件。`analysis.md` 用简短中文帮助用户决定下一步；完整定位、查询轨迹和限制保存在 JSON/日志中。最终回复链接分析目录与 `analysis.md`。

常用事实命令：

```bash
python3 <skill>/scripts/coverage_query.py goal --result <结果> --uc UC-000123
python3 <skill>/scripts/coverage_query.py native --result <结果> --uc UC-000123 --seconds 60
python3 <skill>/scripts/coverage_query.py related --result <结果> --uc UC-000123 --instance top.u --metric tgl --signal 'q[0]'
python3 <skill>/scripts/coverage_query.py related --result <结果> --uc UC-000123 --line 42 --limit 40
```

`--instance`、`--signal`、`--line` 是从已定位目标或依赖整理的内部查询参数，不构成用户入口。单次查询有时间、图节点、分支数限制；默认总预算为 24 次查询、10 分钟，用户指定预算优先。总预算由当前 Codex 管理。接近预算时整理部分分析，停止重复无新信息的查询。

## 证据和停止规则

- 保留 covered、coverable、raw_status 和所有独立标志；未采集、排除、不计分、未知与未覆盖分别解释。事实库可能已经移除排除点，无记录不能判为未覆盖。
- 合并覆盖不恢复事件先后、同周期组合或跨测试共同满足条件；多个输入各自覆盖不证明目标表达式共同满足。某上游点未覆盖须证明它对目标的必要性，才能用作解释。
- Toggle 区分位和方向，需要前态与后态；不能从未覆盖推出恒 0。Line 表示语句执行，值不变也可覆盖。握手只在 RTL 或计分配置实际参与时加入。
- 顶层输入：输出所需外部条件；没有环境证据时停在外部驱动未知。寄存器：保留时钟、复位优先级、更新、保持和前态，按问题决定是否继续。
- 常量或矛盾：给出结构依据、适用设计配置和证明范围。局部可满足不证明全局时序可达，有限深度找不到路径不证明不可达。
- 反馈、复杂状态、多驱动、inout、黑盒、存储器、动态索引、缺失源码或未支持结构：指出影响路径，保留未知，不用任意假设补全。
- 设计对应关键冲突：输出覆盖事实和冲突，停止历史根因归因。未确认时只做附适用范围的候选分析。
- 原生 bin 无法可靠解码：保留原始 locator、term 和 bin，停止该部分推导；输出 `无法可靠分析` 或有范围限制的候选结论。
- 时间、节点或总预算耗尽：输出 `partial`、未展开边界和下一项最有价值的验证，不能把预算耗尽当作原因确认。

结论等级只有：**原因已确认 / 已定位缺失条件 / 候选原因 / 无法可靠分析**。原因确认须有设计对应、原生语义和可核验解释，明确适用范围与假设。没有依据时不用置信度数值。失败或能力不足发生在唯一定位之后，也要在对应目录保留证据并发布部分/失败报告。

## 面向用户的报告

正文先直接标明目标，如“`io_ci[0]` 的 0→1 翻转未覆盖”，再回答“覆盖需要什么条件、目前发现什么、原因判断、下一步怎么做”。条件只写目标成立所需的条件，初始化、连接和覆盖状态等观察放到 `findings`，避免把候选原因混入条件后又重复解释。

每条发现、判断关联关键证据。使用 `findings` 写少量关键事实，`conclusion.text` 简述原因与确认程度，`key_limitations` 概括影响结论的缺口；通常各用一至三条即可，复杂情况按实际需要展开，不机械删减。需要确认的设计对应、原生语义、动态驱动或可达性缺口不能因简化而隐去。

长哈希、原生 locator JSON、逐条查询状态、失败重试和运行编号留在结构化结果、证据和 manifest；正文链接这些文件。`unresolved`、`stop_reason` 和独立状态标志照常完整保存。报告格式优化不需要重新分析，已有结果可用 `analysis_report.py --render-only` 根据保存的数据重新生成 Markdown，详见输出指引。

实现边界、验证方法和本机可行性评估见 [validation.md](references/validation.md)。
