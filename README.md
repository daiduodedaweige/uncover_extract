# uncover-extract

逐实例读取 Synopsys VDB 的原生覆盖事实，生成 Line、Toggle、Branch、Condition 四指标统计和完整未覆盖点报告。每个实例独立检查，不执行实例间差异分析。

需要 Python 3.10+、Verdi NPI 和可用许可证；原生验证环境为 U-2023.03。读取在 VDB 副本及独立进程中完成，核对输入指纹后发布结果。

## 运行

```bash
python3 uncover_extract.py \
  --vdb merged0608.vdb --rtl-root rtl \
  --metrics line tgl branch cond \
  --out results/coverage-instances

# 省略 --metrics 自动探测；可重复指定实例根，包含该实例及后代。
python3 uncover_extract.py \
  --vdb merged0608.vdb --rtl-root rtl \
  --instance-root tb_top.u_DongJiangTop.hnx.slice \
  --out results/slice
```

单测试 VDB 自动采用唯一测试；多测试通过 `--tests mode0 mode1` 或 `--all-tests` 选择，由 NPI 原生累计。未知测试或实例根会报错。

`--instance-root` 只读取范围内实例的原子状态。父实例只统计自身覆盖点，子实例单独列出。`--metrics` 支持 `line tgl branch cond`，也接受 `toggle`、`condition`。

`--rtl-root`、`--path-map OLD NEW`、`--instance-root`、`--exclude-file` 可以重复。源码映射先匹配显式前缀，再按 RTL 根中最长路径后缀或唯一文件名解析；指定目录中缺失或歧义时不会回退旧构建路径。缺少源码不影响保留覆盖事实。

`--batch-size` 默认 256，范围 1–500；`--work-dir` 默认为系统临时目录；`--timeout` 默认 3600 秒。已有输出需要 `--overwrite`，且只能替换本工具生成的结果；失败不会替换旧结果。

## 阅读报告

先打开 `report.md`，从四指标总览进入实例小节，再按指标检查源码覆盖点。

| 文件 | 内容 |
| --- | --- |
| `report.md` | 全部所选实例的四指标统计与逐实例分析入口 |
| `reports/instances/` | 按真实实例层级组织的实例目录和指标分页 |
| `instance_summary.json` | 实例、模块、指标可用状态、原生总计、原子状态数量、全部页面、计数及每页起止编号 |
| `coverage_facts.sqlite` | 完整原生定位和逐实例原始状态 |
| `analysis_manifest.json` | 输入指纹、测试、范围、采集统计、报告与性能 |
| `execution.log` | 原生采集和报告生成进度 |

只有存在常规未覆盖点的实例生成自身的 `index.md` 和指标明细页。已覆盖父实例可能作为子实例的路径目录存在，但没有父实例自己的明细。没有未覆盖点或没有可分析点的实例仍保留在主报告中。

四指标分别统计常规未覆盖原子点。`0` 表示该实例该指标已分析且没有常规未覆盖点；`—` 表示未选择、未采集或没有可分析原子点，原因见实例小节。特殊状态不当作未覆盖，也不能据未覆盖数为零认定全部覆盖。原生排除点不参与分析。

明细按源码文件、数值行号和信号位/方向排序，每页最多 2000 个点、约 1 MiB，单条超长记录允许独立成页。所有未覆盖点都会写出，分页不截断。每条前面都有“已分析”勾选框，随后展示编号、源码文件、行号和信号/覆盖点/方向；Line 的源码信息只展示文件和行号。源码只链接文件，行号独立显示。

所有常规未覆盖点共用一套编号，从 `UC-000001` 连续递增，跨页、跨指标、跨实例不重置。编号顺序为实例自然排序 → Line、Toggle、Branch、Condition → 源码文件、数值行号、信号位及方向。编号至少六位，超过六位时自动扩展。同一事实库和相同分析范围下，调整分页大小、字节上限、离线重建或勾选均不改变编号；更换 VDB 或改变覆盖点集合后允许重新编号，编号只在当前报告内唯一。

```markdown
- [ ] 编号：UC-000001；源码文件：foo.sv；行号：38
- [x] 编号：UC-000002；源码文件：foo.sv；行号：42；信号 / 覆盖点 / 方向：data[0] / 0->1
```

实例目录和明细页显示每页编号范围；`instance_summary.json` 通过 `detail_format: numbered_checklist_v1` 标识该格式，每页的 `first_point_no` 和 `last_point_no` 保存整数起止编号。

勾选框使用 Markdown 任务列表：`[ ]` 表示待分析，`[x]` 表示已分析。在支持任务列表编辑的阅读器中勾选，或直接修改 Markdown 标记并保存，即可记录进度。勾选只记录人工分析进度，覆盖点仍保留在未覆盖清单中。新生成或离线重建的报告默认全部未勾选。

## 离线报告及导出

无需加载 NPI，也无需许可证，可从已有结果重建完整报告到新目录。支持旧版 1.0 事实库，只读访问来源；新副本升级到事实库 2.0。

```bash
python3 -m uncover_extract.rebuild_report \
  --result results/coverage --out results/coverage-instances-offline

# 按需要调整分页大小和字节上限。
python3 -m uncover_extract.rebuild_report \
  --result results/coverage --out results/custom-pages \
  --page-size 1000 --page-bytes 1048576

# 默认导出全部所选实例的常规未覆盖点，流式生成 JSONL 或 gzip JSONL。
python3 -m uncover_extract.export_facts \
  --result results/coverage-instances --out results/all-holes.jsonl.gz
```

导出保留实例、指标、目标身份、完整原生定位、源码映射、`covered/coverable/raw_status`、独立标志和状态，并增加整数 `point_no`，与报告中的 `UC-xxxxxx` 编号对应；原有 `target_id` 保持不变。`--kind holes` 可以显式指定。导出文件不得覆盖。

0.3.0 移除了差异分析，默认不再生成 `differences.*`、`difference_patterns.jsonl.gz` 或 `module_summary.json`。`--kind differences`、`--kind common-uncovered` 会说明迁移方式。旧的模式/比较参数不再支持；历史结果保留原样。

## 核验

全设计可使用 `--validate-urg --urg-bin /path/to/urg`，独立核对同测试、同排除条件下的原生计分总数。当前 URG 核验不支持 `--instance-root`。

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
python3 scripts/build_fixture.py --out /tmp/coverage-fixture
python3 scripts/run_scenarios.py --vdb /tmp/coverage-fixture/model.vdb --out validation/instance-scenarios
python3 scripts/check_missing_identity.py --vdb /tmp/coverage-fixture/model.vdb --out validation/instance-no-identity
python3 scripts/check_instances.py --result results/coverage-instances
```

`check_instances.py` 将所有分页逐条与事实库对照，检查数量、顺序、遗漏、重复、编号连续性及对应关系、每页编号范围、源码及导航链接；兼容含目标编号的旧表格、无编号表格、无编号任务列表和当前带编号任务列表，已有报告可直接校验，无需重建。支持未勾选和已勾选的明细。可用 `--reference OLD_RESULT` 核对每个实例的全部原始状态。需要给旧报告增加编号时，使用离线重建命令输出到新目录。

[输出格式](docs/输出格式.md) · [实施计划](plan.md) · [本次验证记录](docs/重构验证.md)
