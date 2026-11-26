# uncover-extract

从 Synopsys VDB 提取逐实例覆盖事实，生成 Line、Toggle、Branch、Condition 四类未覆盖点报告，再通过 Codex skill 分析指定点的覆盖条件、设计依赖和原因边界。

| 组件 | 入口 | 用途 |
| --- | --- | --- |
| 覆盖提取工具 | [uncover_extract.py](uncover_extract.py) | 读取原生覆盖状态，生成统计、事实库和带 `UC-xxxxxx` 编号的完整报告 |
| 单点分析 skill | [uncover_analyse/SKILL.md](uncover_analyse/SKILL.md) | 由当前 Codex 按 `coverage-point-analyzer` 指引分析一条编号记录，输出结论与证据 |

## 使用 uncover_analyse：直接向 Codex 发请求

`uncover_analyse/` 是一份供 Codex 读取的分析指引。你告诉 Codex **报告放在哪里、分析哪个 UC 编号**，Codex 负责调用查询脚本、检查设计逻辑和覆盖证据，并写出分析报告。

### 已有带 UC 编号的报告

报告目录中应有 `report.md` 和 `coverage_facts.sqlite`。打开 `report.md`，从实例和指标入口进入明细页，复制一条记录的完整编号，然后把下面这段文字发到 Codex 对话中：

```text
请使用当前仓库的 uncover_analyse/SKILL.md，
分析 results/coverage-instances 中的未覆盖项 UC-000123。
```

将目录和 `UC-000123` 替换为实际值。结果目录和 skill 已在会话中明确后，可以直接说“继续分析 UC-000124”。

### 旧报告还没有 UC 编号

先让 Codex 给旧报告补编号。下面这段请求还指定了补编号后要分析的第一条记录，可直接作为首次使用示例：

```text
请将 results/coverage-instances 中的已有覆盖事实
重新生成带 UC 编号的报告，保存到 results/coverage-numbered。
然后使用 uncover_analyse/SKILL.md，
分析新报告中的未覆盖项 UC-000001。
```

这里的“重新生成报告”也叫**离线重建**：用已经保存的覆盖事实换一份报告，给记录补上编号，不重跑仿真。补编号后的覆盖报告保存在 `results/coverage-numbered`，单点分析输出到项目根目录。需要自行执行时，可使用后文的[补编号命令](#给旧报告补编号或调整分页)。

### 可提供的设计资料

首次分析可一并提供候选 KDB 的 `simv.daidir` 路径、源码目录和可选验证环境源码。Codex 会检查这些资料是否与覆盖结果对应；缺少资料或存在冲突时，报告会写明能够分析到哪一步。

当 skill 已安装到 Codex 的 skills 目录时，也可以显式调用 `$coverage-point-analyzer`。直接指定本仓库的 `uncover_analyse/SKILL.md` 即可使用上述请求。完整分析方法见 [SKILL.md](uncover_analyse/SKILL.md)。

### 分析完成后看哪里

该点的全部分析产物保存在**项目根目录**下，与 `README.md` 同级：

```text
result_analyse_UC-000123/
├── analysis.md       # 主要阅读入口：条件、结论、未解决问题与下一步
├── analysis.json     # 结构化目标、条件、结论和证据引用
├── evidence.jsonl    # 查询结果、失败记录和能力边界
├── manifest.json     # 本点身份、运行状态、版本、后端和产物清单
├── logs/             # 按需保存后端日志、请求和响应
└── runs/             # 重复分析时保留同一目标的历史运行
```

结论区分“原因已确认”“已定位缺失条件”“候选原因”和“无法可靠分析”。报告说明证据能够支持到哪里，以及下一步最值得补充的检查或激励。

脚本优先从覆盖结果向上定位 Git 仓库根；结果在仓库外时使用当前工作目录所属的 Git 根，没有 Git 根则使用当前工作目录。同一根目录中，不同覆盖结果的相同 UC 编号会使用同名输出目录；如已有分析属于另一份结果，脚本会说明冲突，避免混用。

主要阅读 `analysis.md`：正文只列目标、覆盖条件、关键发现、原因判断和下一步，影响判断的未确认项用短句说明。完整原生定位、查询记录和运行细节通过文末链接查看。**单点分析不需要先做数据导出**。

## 从 VDB 生成报告（还没有报告时使用）

如果已经有上述报告目录，可以直接使用 `uncover_analyse`。只有 VDB、尚无报告时，先运行：

```bash
python3 uncover_extract.py \
  --vdb merged0608.vdb \
  --rtl-root rtl \
  --metrics line tgl branch cond \
  --out results/coverage-instances
```

以下终端命令均在仓库根目录执行。单测试 VDB 自动采用唯一测试；多测试 VDB 必须用 `--tests mode0 mode1` 或 `--all-tests` 明确范围，状态由 NPI 原生累计。

提取在 VDB 副本和独立进程中完成，核对原始输入指纹后发布结果。每个实例只统计自身覆盖点，子实例单独列出。

## 环境要求

| 操作 | 依赖 |
| --- | --- |
| 读取 VDB、查询原生 bin 或使用 KDB | Python 3.10+、Verdi Python NPI、可用许可证；原生验证环境为 U-2023.03 |
| 阅读已有事实、重新生成报告或导出 | Python 3.10+，无需加载 NPI 或取得许可证 |
| 由 Codex 执行单点分析 | 本仓库的 `uncover_analyse/` skill 包，以及所选查询后端的依赖 |
| 显式使用 pyslang 重建设计 | `pyslang>=11,<12`，以及完整 filelist、宏、include、库、顶层和参数配置 |
| 生成受控仿真测试库 | VCS 与可用许可证，仅开发核验需要 |

提取工具可通过 `--verdi-home /path/to/verdi` 指定 Verdi 安装目录。单点分析优先使用匹配历史编译的 KDB，pyslang 作为显式选择的备用后端。

## 提取范围与常用参数

只分析指定实例及其后代：

```bash
python3 uncover_extract.py \
  --vdb merged0608.vdb --rtl-root rtl \
  --instance-root tb_top.u_DongJiangTop.hnx.slice \
  --metrics line tgl branch cond \
  --out results/slice
```

| 参数 | 说明 |
| --- | --- |
| `--metrics line tgl branch cond` | 省略时自动探测；也接受 `toggle`、`condition` |
| `--tests NAME ...` / `--all-tests` | 选择测试集合，两者互斥；未知测试会报错 |
| `--instance-root PATH` | 可重复，只读取选中实例及后代的原子状态；未知实例会报错 |
| `--rtl-root DIR` | 可重复，用于定位本地源码 |
| `--path-map OLD NEW` | 可重复，将历史构建源码前缀映射到本地目录 |
| `--exclude-file FILE` | 可重复，在提取前加载已有排除文件 |
| `--batch-size N` | 原生事实批大小，默认 256，范围 1–500 |
| `--work-dir DIR` | VDB 副本和原生读取的临时工作目录，默认为系统临时目录 |
| `--timeout SECONDS` | 原生提取超时，默认 3600 秒 |
| `--overwrite` | 整体替换已有的本工具结果目录；失败时保留旧结果 |

源码映射先使用显式前缀，再按 RTL 根中的最长路径后缀或唯一文件名解析。指定目录中缺失或歧义时，不回退到旧构建路径。缺少源码仍会保留原生覆盖事实。

重新提取建议使用新输出目录；`--overwrite` 会替换整个旧覆盖结果目录，包括原报告的人工勾选。项目根目录中的单点分析通过 manifest 记录所属结果和原子目标，后续使用时会重新核对。

## 报告结构与编号规则

提取阶段生成以下文件：

| 路径 | 内容 |
| --- | --- |
| `report.md` | 所选实例的四指标统计和明细入口 |
| `reports/instances/` | 按真实实例层级组织的目录及指标分页 |
| `instance_summary.json` | 模块、实例、指标可用状态、原生总计、原子状态数量及每页起止编号 |
| `coverage_facts.sqlite` | 完整原生定位、源码映射和逐实例原始状态 |
| `analysis_manifest.json` | 本次提取的 VDB 指纹、测试集合、配置、统计和性能 |
| `execution.log` | 原生采集和报告生成进度 |

`analysis_manifest.json` 描述整份覆盖结果，`result_analyse_UC-xxxxxx/manifest.json` 描述单个覆盖点的分析运行。

### 统计与明细

四指标分别统计常规未覆盖原子点。`0` 表示该实例该指标已分析且没有常规未覆盖点；`—` 表示未选择、未采集或没有可分析原子点，原因见实例小节。特殊状态单独保留，未覆盖数为零不能据此认定全部覆盖。原生排除点不参与报告统计。

只有存在常规未覆盖点的实例生成自身 `index.md` 和指标明细页。其他实例仍保留在主报告中，父实例也可能仅作为子实例的路径目录存在。

明细按源码文件、数值行号和信号位/方向排序。每页默认最多 2000 个点、约 1 MiB，单条超长记录允许独立成页；分页会保留全部未覆盖点。每条记录展示编号、源码文件和行号，非 Line 指标另显示信号/覆盖点/方向。源码链接指向文件，行号独立显示。

```markdown
- [ ] 编号：UC-000001；源码文件：foo.sv；行号：38
- [x] 编号：UC-000002；源码文件：foo.sv；行号：42；信号 / 覆盖点 / 方向：data[0] / 0 -> 1
```

### UC 编号与分析进度

所有常规未覆盖点共用一套编号，从 `UC-000001` 连续递增，跨页、跨指标、跨实例不重置。数字至少六位，超过六位自动扩展。编号顺序为：实例自然排序 → Line、Toggle、Branch、Condition → 源码文件、数值行号、信号位及方向。

同一事实库和相同分析范围下，调整分页大小、字节上限、离线重建或勾选不会改变编号。更换 VDB、测试集合或覆盖点集合后，应重新核对编号。UC 编号只在所属结果中唯一，数据库内部 target ID 和原生 locator 另行保留。

`instance_summary.json` 用 `detail_format: numbered_checklist_v1` 标识带编号格式，每页的 `first_point_no`、`last_point_no` 保存整数起止编号。skill 据此定位分页并核对事实库，不为分析一个编号导出全设计状态。编号须按报告原样填写，保留大写 `UC`、连字符和补零数字。

勾选框中 `[ ]` 表示待分析，`[x]` 表示已分析，可在支持任务列表编辑的阅读器中勾选，或直接修改 Markdown 并保存。勾选只记录人工进度，记录仍属于未覆盖点。skill 的分析结果独立输出，不自动勾选原报告。新生成或离线重建的报告默认全部未勾选。

## 单点分析的能力与边界

skill 统一处理四类指标，由当前 Codex 选择分析方向、查询依赖并组织结论：

| 指标 | 覆盖目标 |
| --- | --- |
| Toggle | 具体信号位发生指定方向的可计分翻转，保留前态与后态要求 |
| Line | 完整原生 locator 对应的 statement bin 被执行 |
| Branch | 原生分支结果被选择，保留外层条件和前序分支优先级 |
| Condition | 在监测求值上下文中满足原生 condition bin 的项值要求 |

Branch、Condition 使用原生 term 顺序和值解释目标，不根据 bin 名称猜测完整语义。同一物理行上的多个覆盖点、同一模块的多个实例分别核对。

KDB 与 pyslang 都需要检查实例、参数、信号范围、源码和编译来源。同名对象、相同路径或当前源码编译成功，均不足以证明与历史覆盖库匹配；未确认时保留候选结论，有关键冲突时停止历史根因归因。

合并覆盖只能说明选定测试集合的计分状态，不能恢复同周期组合、事件先后或每次运行的取值历史。验证环境源码可用于分析驱动能力，实际执行情况仍需运行证据。复杂状态、黑盒、多驱动、动态索引和预算耗尽等情况会明确保留未解决边界。

默认通过原生覆盖事实、静态设计和可选环境源码分析，波形或重跑仿真作为后续验证建议。skill 不自动修改 RTL、激励、排除配置或原报告勾选状态。

详细资料：[指标语义](uncover_analyse/references/metrics.md) · [设计后端与查询命令](uncover_analyse/references/design-backends.md) · [分析输出格式](uncover_analyse/references/output-schema.md) · [可行性与验证记录](uncover_analyse/references/validation.md)。

## 给旧报告补编号或调整分页

使用已有的 `coverage_facts.sqlite` 重新生成可阅读的 Markdown 报告，称为“离线重建”。它适用于旧报告缺少 UC 编号，或需要调整每页条数的情况，不重新读取原始 VDB，也不重跑仿真。

给旧报告补编号：

```bash
python3 -m uncover_extract.rebuild_report \
  --result results/coverage-instances \
  --out results/coverage-numbered
```

也可以在生成报告时调整分页：

```bash
python3 -m uncover_extract.rebuild_report \
  --result results/coverage-instances \
  --out results/custom-pages \
  --page-size 1000 --page-bytes 1048576
```

重建会复制覆盖事实并重新生成报告，不复制原报告的人工勾选和已有单点分析目录。原目录仍保留这些内容。

该操作只读访问来源，目标目录必须不存在，且不能与来源目录重叠。支持旧版 1.0 事实库，生成的副本升级为 2.0。

## 导出覆盖点供其他程序读取（可选）

“数据导出”是把事实库里的未覆盖点转换成 JSONL 文本，供你自己编写的统计程序或其他工具读取。每行是一条覆盖点记录，`.gz` 表示压缩保存。**使用 `uncover_analyse` 时可以跳过这一节**，skill 会直接查询事实库。

需要导出时运行：

```bash
python3 -m uncover_extract.export_facts \
  --result results/coverage-instances \
  --out results/all-holes.jsonl.gz
```

输出支持 JSONL 和 gzip JSONL，文件必须不存在。每条包含实例、指标、目标身份、完整原生定位、源码映射、`covered/coverable/raw_status`、独立标志和状态；整数 `point_no` 对应报告中的 UC 编号，原有 `target_id` 保持不变。可显式指定 `--kind holes`。

## 核验与开发测试

离线功能测试：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s uncover_analyse/tests -v
```

完整报告可逐页与事实库核对：

```bash
python3 scripts/check_instances.py --result results/coverage-instances
```

该检查涵盖数量、顺序、遗漏、重复、编号连续性及对应关系、分页范围、源码和导航链接，支持已勾选记录及旧表格/无编号报告。可加 `--reference OLD_RESULT` 核对逐实例全部原始状态。旧报告可直接校验；进入单点 skill 前仍需具备规范 UC 编号。

有 VCS 和 NPI 许可证时，可生成受控两测试 VDB 并核验原生查询：

```bash
python3 scripts/build_fixture.py --out /tmp/coverage-fixture
python3 uncover_extract.py \
  --vdb /tmp/coverage-fixture/model.vdb --all-tests \
  --rtl-root examples/fixture --out /tmp/coverage-fixture-result
python3 uncover_analyse/tests/check_native.py --result /tmp/coverage-fixture-result
python3 scripts/run_scenarios.py \
  --vdb /tmp/coverage-fixture/model.vdb --out validation/instance-scenarios
python3 scripts/check_missing_identity.py \
  --vdb /tmp/coverage-fixture/model.vdb --out validation/instance-no-identity
```

`check_native.py` 用于上述受控 fixture，检查四类原生点、合并测试身份及 pyslang 静态连接，需要同时具备 pyslang 依赖。生成 fixture 和场景报告时，输出目录应选用尚未存在的路径。

全设计提取可加 `--validate-urg --urg-bin /path/to/urg`，独立核对相同测试、相同排除条件下的原生计分总数；当前 URG 核验不支持 `--instance-root`。

## 旧版功能迁移

0.3.0 起聚焦逐实例事实和未覆盖点报告，移除了实例间差异分析。`differences.*`、`difference_patterns.jsonl.gz`、`module_summary.json` 不再生成；旧的模式/比较参数，以及导出的 `--kind differences`、`--kind common-uncovered` 不再支持。历史结果可保留，新增分析使用当前报告和 `uncover_analyse/`。

单点分析的设计目标与范围见[实施计划](plan.md)。
