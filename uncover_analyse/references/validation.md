# 可行性与验证记录

## 可行性判断

第一版可行。交付为一个 `coverage-point-analyzer` skill，按用户要求放在 `uncover_analyse/`，没有独立推理 agent、调度框架或服务。辅助子进程仅隔离 NPI/编译器崩溃与日志。

可直接复用的基础包括事实库 2.0 的原生 trail/状态/模型/源码映射，以及现有报告的 `numbered_checklist_v1` 编号和页范围。包内 `_vendor/uncover_extract/{common,facts,output}.py` 来自本仓库 0.3.0 实现；保留原事实身份、状态解码、自然排序和报告显示规则，运行无需开发仓库导入路径。更新原报告格式或事实版本时需要同步这些模块并重跑兼容验证。

主要风险不是编号解析，而是历史设计对应与工具覆盖语义。NPI coverage 与 language 设计模型是两个独立接口；打开 VDB 不表示已加载设计。实际验证发现 Branch term 的取值使用字符串接口，而 Condition term 可能只通过整数接口提供；均不能用 bin 名猜测。原生项不可解码时保留边界。

本机原 `results/coverage-instances` 约有 698 万个未覆盖点，仍是无规范 UC 编号的旧报告。新脚本会拒绝猜测编号。开发验证另在 `/tmp` 从原始 VDB 生成小范围带编号结果，没有重写原报告、事实库或 RTL。

候选 `simv.daidir` 可以通过 NPI 加载，且能查询 `tb_top.u_DongJiangTop.xbar` 的端口/驱动。实际 VDB 的 `io_hnx_tx_req_bits_RSVDC` 有 3 个插桩位；候选 KDB 声明宽度为 5，本地 RTL 为 `[4:0]`。查询将其列为 `instrumentation_vs_design_width` 对应冲突，而不是确认版本差异或功能根因；也可能涉及部分插桩，需原构建配置解释。

## 已验证能力

环境：Python 3.10、pyslang 11.0.0、Synopsys VCS/Verdi NPI U-2023.03。原生工具需要许可证服务器访问；沙箱中许可证不可用时，查询会记录失败及日志，不伪装成无覆盖数据。

| 场景 | 结果 |
| --- | --- |
| skill-creator quick_validate | 通过，名称/frontmatter/脚手架检查有效 |
| 16 个离线功能测试 | 通过，覆盖下述身份、输出与 AST 不变量 |
| 非规范编号、不存在编号、旧报告、报告/事实不一致 | 明确拒绝，不创建虚构点目录 |
| 同一行多个 statement bin | 保留不同原生 locator 和稳定目标身份，不按行合并 |
| 多份结果包含同 UC 编号 | 不同项目根输出独立；同一项目根的同名目录拒绝跨结果混入，即使原子身份相同也核对所属结果 |
| 项目根输出与旧目录迁移 | 从结果子目录调用仍输出到 Git 根；发现旧位置分析时提示迁移，保留历史数据 |
| 重复运行、身份冲突、报告发布 | 同目标历史运行归档；不同身份拒绝混入；错误证据引用和未核验根因确认被拒绝 |
| 简短报告与仅更新格式 | 直接显示信号位/方向和关键发现；完整 locator、限制及失败记录保留在 JSON/证据中；重新展示不修改分析数据或证据，不允许目标身份错误或结论等级越过证据 |
| 安装目录迁移 | 从无关 cwd 执行复制后的脚本可解析原生事实，不依赖仓库导入路径 |
| 拼接、静态切片、符号扩展和截断 | 对照展开 AST 和具体源位映射验证 |
| 时钟、复位、使能和 casez | 保留事件、前序分支、非阻塞及通配匹配语义 |
| generate 和不同参数实例 | 精确实例选择、参数值及宽度验证 |
| 节点预算、源码改变 | 返回图边界；源码指纹漂移可见 |
| 真实受控 VCS 两测试合并库，四类指标 | 原生状态和完整 locator 一致；Branch/Condition 原生 terms 核对；常量 sel 端口绑定与静态模型一致 |
| 真实工程 em 的 Line/Branch/Condition、xbar 的 Toggle | 从规范 UC 定位，NPI/KDB 查询成功；reqLocal 的分支/条件项值可解码，RSVDC 对应冲突可见 |

这些测试验证事实查询和可复核产物流程。当前 Codex 还按真实查询证据发布代表点报告进行对照；未声称已完成另一独立干净 Codex 会话的自动选 skill 行为测试，也未将人工发现的局部条件升级为历史根因确认。

## 复现

离线检查不需要专有工具：

```bash
python3 -m unittest discover -s uncover_analyse/tests -v
python3 <skill-creator>/scripts/quick_validate.py uncover_analyse
```

受控真实 VDB 集成检查在开发仓库中运行，选择新的临时目录：

```bash
python3 scripts/build_fixture.py --out /tmp/uc-fixture
python3 uncover_extract.py --vdb /tmp/uc-fixture/model.vdb --all-tests \
  --rtl-root examples/fixture --out /tmp/uc-fixture-result
python3 uncover_analyse/tests/check_native.py --result /tmp/uc-fixture-result
```

`check_native.py` 仅适用上述受控 fixture，查询 `fixture_tb.right` 的四类点并断言两个测试集合及常量绑定。它不会自动编写原因结论。KDB 对照需提供实际匹配 dbdir，通过正常 `design_query.py` 命令执行。

## 当前分析边界

- 不证明全设计时序可达，不自动求解反馈、任意多驱动或复杂存储器语义；不包含波形、逐回归重放或环境运行统计。
- KDB 返回的部分对象与 coverage bin 只能形成源码位置候选，同一行多点仍需明确的原生映射证据。
- pyslang 每次独立查询重建展开模型，当前版本复用已有查询证据和共享表达式节点，没有持久化编译器 AST 或跨点全设计缓存。不会以可能过期的缓存代替一致性核对。
- 根因和下一步由当前 Codex 基于证据组织；发布脚本检查身份、格式和结论门槛，不替代语义判断。
- 将本目录按 `coverage-point-analyzer` 名称复制到 Codex 可发现的 skills 目录即可部署；本次仅交付指定目录，未安装到用户全局目录。
