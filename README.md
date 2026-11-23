# uncover-extract

从 Synopsys VDB 读取 Line、Toggle、Branch、Condition 原生覆盖事实，按相同 RTL 模型比较各实例的覆盖点，生成模块汇总和差异报告。每个覆盖点共享完整描述，逐实例保存原始状态，完整空洞可从事实库离线导出。

主程序只有一条执行流程：模型分组 → 每实例顺序扫描 → 紧凑事实保存 → 覆盖点分类 → 分组报告。工作区默认位于系统临时目录。运行需要 Linux、Python 3.10+、Verdi Python NPI 和可用许可证；已验证工具版本为 Verdi/VCS/URG U-2023.03，无 Python 第三方依赖。

## 直接使用

```bash
# 查看参数，无需 NPI 或许可证。
python3 uncover_extract.py --help

# 当前工程的四指标完整分析。
python3 uncover_extract.py \
  --vdb merged0608.vdb --rtl-root rtl \
  --metrics line tgl branch cond \
  --out results/coverage

# 只分析 Toggle，省略 --metrics 则自动探测全部支持的指标。
python3 uncover_extract.py \
  --vdb merged0608.vdb --rtl-root rtl \
  --metrics tgl --out results/toggle
```

入口支持绝对路径，可从任意目录运行。输出目录必须与输入 VDB、RTL 根目录分离；默认拒绝覆盖已有结果，更新本工具的已有结果时添加 `--overwrite`。原生读取在独立进程和 VDB 副本中运行，成功后核对原始 VDB 全部文件的 SHA-256，再发布完整结果。

当前工程约有 695 万个未覆盖 Toggle 方向目标。原生排除点不进入分析、报告及发布的事实库；其余目标保存完整紧凑事实。默认报告展示差异、共同特殊状态及不可比目标，全体已覆盖和共同未覆盖只汇总。每个信号位、方向和 bin 均独立保留，不按信号名或源码行合并。

## 测试、实例范围与源码

```bash
# 多测试 VDB 明确选择测试；也可用 --all-tests 累计全部测试。
python3 uncover_extract.py \
  --vdb /data/run.vdb --tests regression_a regression_b \
  --rtl-root /work/project/rtl \
  --path-map /old/build/rtl /work/project/rtl \
  --instance-root tb_top.dut \
  --out /work/results/coverage

# 应用已有外部排除，并用 URG 核对全设计精确计数。
python3 uncover_extract.py \
  --vdb /data/run.vdb --all-tests \
  --exclude-file /work/existing.el \
  --validate-urg --urg-bin /path/to/urg \
  --out /work/results/checked
```

单测试 VDB 自动选择唯一测试；多个测试由 NPI 原生累计。未明确选择、测试不存在或实例根不存在时会报错。`--instance-root` 限定报告目标的范围，同模型对照实例可以在范围外；范围外实例独有目标不会扩大报告范围。URG 目前仅支持全设计核对，不能与 `--instance-root` 同用。

`--rtl-root`、`--path-map`、`--instance-root`、`--exclude-file` 均可重复提供。源码映射优先使用显式前缀映射，其次在指定 RTL 根内按最长路径后缀、唯一文件名解析；指定目录中缺失或存在歧义时不回退旧构建路径。未指定 RTL 根或匹配的前缀映射时，才直接使用原路径。报告显示解析后的源码文件路径，链接使用相对于报告的文件路径，行号单列。缺少 RTL 或源码不影响原生覆盖事实保留；当前文件哈希不证明它与编译 VDB 的源码完全一致。

## 输出与离线导出

| 文件 | 内容 |
| --- | --- |
| `report.md` | 存在未覆盖点的模块实例清单、分类依据、“实例 / 覆盖情况”表格及源码文件链接，行号单列 |
| `module_summary.json` | 模型组、实例/源码目录、覆盖分类计数 |
| `differences.csv`、`differences.jsonl.gz` | 差异、共同特殊状态及不可比覆盖点，引用状态分组 |
| `difference_patterns.jsonl.gz` | 分组的实例状态分布和不可比原因 |
| `coverage_facts.sqlite` | 共享完整原生定位、逐实例 covered/coverable/raw_status |
| `analysis_manifest.json` | 输入指纹、测试、范围、原生计数、性能及核验状态 |
| `execution.log` | 原生工具日志和扫描、分类、输出进度 |

实例比较同时核对定义/指标模型身份与实际完整目标描述；身份缺失时按实例隔离。结构不匹配或缺失记录为不可比。原始状态位完整保留，展示状态相同但原始标志不同不会自动算作差异。单实例组会明确标记。

下面的导出读取已保留的事实库，无需 NPI、许可证或重新扫描 VDB：

```bash
# 全部所选实例的常规未覆盖点，保留完整身份、源码和原始状态。
python3 -m uncover_tool.export_facts \
  --result results/coverage --kind holes \
  --out results/all-holes.jsonl.gz

# 差异、特殊状态及不可比目标的完整定位和逐实例原始状态。
python3 -m uncover_tool.export_facts \
  --result results/coverage --kind differences \
  --out results/difference-details.jsonl.gz

# 每个模型目标一条共同未覆盖记录。
python3 -m uncover_tool.export_facts \
  --result results/coverage --kind common-uncovered \
  --out results/common-uncovered.jsonl.gz
```

导出支持 `.jsonl` 和 `.jsonl.gz`，拒绝覆盖已有文件。空洞按模型覆盖点/实例顺序导出，不重复附带全组实例对照。原生排除点过滤后不发布，也不产生不可比记录；不可达、非法和不计分状态保存在事实库中，不归为普通空洞。未覆盖不自动代表应当排除或不可达。

## 参数与迁移

常用参数除上述输入选择外还有：

- `--work-dir PATH`：临时工作盘，默认系统临时目录；保存 VDB 副本与状态库。
- `--batch-size N`：有界事实读写批次，默认 256，范围 1–500。
- `--verdi-home PATH`：Verdi 安装目录；默认读取 `VERDI_HOME`，再回退本机 U-2023.03 路径。
- `--timeout N`：原生 worker 超时，默认 3600 秒。
- `--overwrite`：成功后替换本工具的已有结果；失败保留原结果和诊断目录。

0.2.0 将原来可选的高效分析路径设为唯一主流程，manifest 格式版本为 2.0。事实库和分组文件的结构仍为 1.0。

`--mode`、`--compare-peers`、`--limit-per-metric`/`--limit`、`--comparison-cache-entries`、`--profile-comparison`、`--include-other-states`、`--per-metric`、`--export-exclusions`、`--validate-exclusions` 已移除；批次参数改为 `--batch-size`。旧参数会明确报错。已有外部排除继续通过 `--exclude-file` 加载，加载后按原生排除标志过滤目标，其余特殊状态完整保留。

默认不再生成 `uncovered_coverage.json/csv` 或重复的逐主项实例对照。需要全部空洞时使用离线导出。旧的候选排除文件生成和新副本重新加载验证已随旧流程移除。

性能记录在 `performance.timings` 中，分别统计原生扫描/状态保存、分类、事实库发布和报告写出。原生实例状态仍逐条读取，优化减少重复描述、定位、完整结果构造和重复落盘。全设计历史实测与验证边界见 [模块差异分析](docs/模块差异分析.md)。

## 验证与工程结构

```bash
# 无需原生工具的逻辑回归。
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v

# 可重建双测试 fixture，需要 VCS。
python3 scripts/build_fixture.py --out /tmp/uncover-fixture

# 默认流程的原生场景及 URG 核验，需要 NPI/URG 许可证。
python3 scripts/run_scenarios.py \
  --vdb /tmp/uncover-fixture/model.vdb \
  --out validation/scenarios-new

# 核对可选 XML 身份提示缺失时，原始空洞事实仍一致。
python3 scripts/check_missing_identity.py \
  --vdb /tmp/uncover-fixture/model.vdb \
  --out validation/identity-new

# 只读比较两份结果的逐条事实、分组报告与计数。
python3 scripts/check_differences.py \
  --reference /work/reference-result --result results/coverage
```

运行进度显示为 `comparison: scanning`、`comparison: classified` 和 `output:`。失败不会发布半份正式结果或替换旧结果；失败目录保留配置、错误、输入审计和工作区位置。

`uncover_tool/` 保存命令行、原生会话、事实分析、报告、离线导出和 URG 核验；`tests/` 为逻辑回归；`scripts/` 为 fixture 和原生验证；`docs/` 为数据格式与实现说明。可直接运行入口，也可执行 `python3 -m pip install --no-build-isolation --no-deps .` 安装命令行工具。

`validation/` 保存历次真实测量及新验证结果。历史报告、`advice.md`、`plan.md` 中的旧模式和命令仅反映当时实现，当前用法以本文和 `--help` 为准。历史结果不会被本次升级重写。

尚未声明支持 FSM、VHDL、跨编译版本目标匹配或其他厂商覆盖库。原生定位下标仅在记录的模型内有效；本工具不提供 RTL 不可达性证明。

## 清理结果

```bash
./clean.sh --dry-run  # 查看将终止的任务与清理范围。
./clean.sh            # 终止使用 results/ 的相关任务，再清空 results/。
```

脚本按自身位置定位 `results/`，可从任意目录调用。程序、VDB、RTL 和 `validation/` 保留；自定义到其他位置的输出/工作目录不在清理范围内。

当前版本已通过 25 项逻辑测试、11 次原生场景和全设计四指标 URG 核验；7,753,570 条原始状态及全部分组报告与整合前结果逐条一致。默认全设计 worker 本次耗时约 5.77 分钟。详见 [本次验证](docs/模块差异分析.md#主流程整合020)。

详细字段见 [输出格式](docs/输出格式.md)。
