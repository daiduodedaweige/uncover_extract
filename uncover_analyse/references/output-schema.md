# 证据与报告格式

唯一定位后目录固定为 `<项目根目录>/result_analyse_<规范UC编号>/`，标准四文件在该目录根部。根目录按照 `SKILL.md` 的规则确定，并由 `resolve.project_root` 返回。不得为无效/不存在编号创建目录，不自动改变原报告勾选。重复运行核对所属结果路径、database_id、test_scope_ref、实例、指标、完整 locator 和稳定目标 ID，匹配才允许归档。不同结果的相同编号在同一项目根下发生目录冲突时明确报错，不混用证据。

- `manifest.json`：运行状态、run_id、UC、所属结果、项目根、输出目录、原报告记录、覆盖/测试/目标身份、后端、版本、配置、预算和全部产物清单。
- `evidence.jsonl`：每个查询一条，包含 `evidence_id`、时间、operation、status、identity、data。失败、不支持和超时也记录。证据 ID 在每次运行内从 E0001 开始。
- `analysis.json`：完整目标身份、状态、条件、关键发现、结论、全部未解决项、停止原因、适用范围及证据引用。供复核和程序读取。
- `analysis.md`：面向用户的简短报告，直接展示目标、覆盖条件、关键发现、原因判断、下一步，以及影响判断的关键缺口。全文原生 JSON、长哈希、查询状态表和运行信息由链接访问。
- `logs/`：确有后端查询时才创建；请求、响应、编译/许可证日志均在其中。旧运行保存在 `runs/<run_id>/`。

## 写作与展示

主报告通常一屏到两屏即可读完；复杂目标需要更多说明时据实展开。不要通过截断表达式或隐藏重要冲突来缩短报告。

| 内容 | 草稿字段 | 展示方式 |
| --- | --- | --- |
| 覆盖目标和局部必要条件 | `conditions` | 短句列明具体信号/语句及成立条件；`kind` 保留在 JSON，正文不显示 exact/necessary/candidate 英文标签 |
| 已观察到的关键事实 | `findings` | 少量发现及关键证据引用，例如端口直连、初始化值、相关方向覆盖状态 |
| 原因与确认程度 | `conclusion` | 通常一两句，区分候选解释与已证实原因，不再重复全部条件和限制 |
| 可执行的下一步 | `next_steps` | 按优先级列出具体检查或补测，不加入无关的操作警告 |
| 影响判断的关键缺口 | `key_limitations` | 报告末尾简短说明，例如历史版本对应和动态写入待确认 |
| 完整边界与过程信息 | `unresolved`、`stop_reason`、身份、预算、flags | 全部保留在结构化结果、证据及 manifest 中 |

设计对应未确认或冲突时，生成器始终保留简短提示；失败状态也会明确说明。`key_limitations` 应概括真正阻止结论确认的因素，不能为了简洁省略原生 bin 未解码、图截断影响或其他关键语义缺口。确认原因时，证明范围和适用假设仍出现在正文中。

`findings` 和 `key_limitations` 为向后兼容的可选字段，新分析应填写。旧结果没有 `findings` 时省略该小节，没有 `key_limitations` 时展示原 `unresolved`，不由脚本猜测哪些未解决项不重要。脚本不会自动总结或截断自然语言；精简由当前 Codex 基于证据撰写。

当前 Codex 在本点目录编写 `draft.json`，例如：

```json
{
  "uc_id": "UC-000123",
  "status": "partial",
  "design_match": "unconfirmed",
  "conditions": [
    {"kind": "necessary", "text": "目标输入位从 0 变为 1，且该变化被覆盖工具计分。", "evidence_ids": ["E0001", "E0002"]}
  ],
  "findings": [
    {"text": "当前 testbench 将该输入初始化为 0，未发现后续显式驱动。", "evidence_ids": ["E0003"]}
  ],
  "conclusion": {
    "level": "候选原因",
    "text": "环境可能没有提供所需翻转，目前属于候选原因。",
    "evidence_ids": ["E0001", "E0003"]
  },
  "unresolved": ["历史编译一致性", "计分窗口与实际输入变化"],
  "key_limitations": ["尚缺历史运行中的动态写入与计分窗口证据。"],
  "stop_reason": "到达外部输入，且缺少历史驱动证据。",
  "next_steps": ["核对历史构建记录和对应版本的字段驱动绑定。"],
  "assumptions": ["局部设计模型与目标编译相同，尚待核实。"]
}
```

示例中的编号和 E 编号必须替换为实际查询值，不复制为事实。`kind` 只有 `exact`、`necessary`、`candidate`。结论等级只有“原因已确认”“已定位缺失条件”“候选原因”“无法可靠分析”。`status` 为 `complete`（本次分析按有依据的边界结束）、`partial`（预算/缺证据导致未完成）、`failed`（已定位但查询失败）。complete 不等于根因确认。

`原因已确认` 额外要求 `proof_scope` 和 `assumptions`，以及已核验设计证据。`design_match` 来自查询记录，不允许在草稿手动提升。关键冲突时只能候选或无法可靠分析。报告校验能防止身份和证据引用错误，不能替代 Codex 对必要性、同时发生、控制流和时序可达性的判断。

```bash
python3 <skill>/scripts/analysis_report.py --result <结果> --uc UC-000123 \
  --draft <项目根目录>/result_analyse_UC-000123/draft.json
```

最终回复给出该点目录和 analysis.md 的链接，概述确认程度、关键未解决条件及下一步。发布失败时修正草稿后重试，保留已有证据。若无法继续分析，也按上述格式发布 failed/partial，避免只留下日志。

## 只更新已有报告的展示格式

已有分析结束后，可读取其 `analysis.json` 重新生成简短 Markdown，无需 `begin`、NPI、许可证或重新分析：

```bash
python3 <skill>/scripts/analysis_report.py --result <结果> --uc UC-000123 --render-only
```

命令核对目标身份与证据引用，只更新 `analysis.md` 和 manifest 中的展示版本，不修改结构化结论、原报告勾选或查询证据。若旧数据中的文字本身冗长，应由 Codex 核对原证据后精简条件/发现/判断，并填写 `key_limitations`；保留完整 `unresolved`、`stop_reason` 和此前版本，不能以更新展示为由提升结论等级。

旧版分析目录迁移到项目根后，使用 `--render-only` 更新正文中的相对链接。历史证据和日志里记录的绝对路径保留原样，在 manifest 中记录迁移前后位置，方便复核历史查询，不将历史路径改写成新的执行记录。
