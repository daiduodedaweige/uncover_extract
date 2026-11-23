# uncover-extract

从 Synopsys VDB 提取实例本层的 Line、Toggle、Branch、Condition 未覆盖原子目标，输出源码位置、完整实例路径、原始状态及兼容实例对照。输入工程、RTL 路径、测试集合和输出目录均通过参数指定。

需要分析“相同 RTL 的实例为什么覆盖不同”时，使用 `--mode differences`：独立扫描模型覆盖点，只为差异、特殊状态和不可比情况生成明细，并保留可离线导出的紧凑原始状态库。

提取在 VDB 副本上运行，完成后核对原始 VDB 全部文件的 SHA-256。普通提取不执行排除导出或排除验证。已验证环境为 Linux、Python 3.10、Verdi/VCS/URG U-2023.03；其他版本需要验证后再声明兼容。

## 1. 直接使用

无需安装 Python 第三方包。需要本机安装的 Verdi Python NPI 及可用许可证；NPI 由 `--verdi-home` 或 `VERDI_HOME` 定位。本机默认路径为 `/nfs/tools/synopsys/verdi/U-2023.03`。

```bash
cd /nfs/home/weijing/workspace/uncover_extract

# 查看参数，不需要启动 NPI 或使用许可证。
python3 uncover_extract.py --help

# 相同 RTL 模块的四指标覆盖差异；不生成数百万条实例空洞主项。
# 工作区默认使用系统临时盘，也可显式设置 --work-dir。
python3 uncover_extract.py \
  --mode differences \
  --vdb merged0608.vdb --rtl-root rtl \
  --out results/module-differences \
  --metrics line tgl branch cond

# 当前工程：每个指标输出前 10 个目标，仍统计所选实例范围的全部目标。
python3 uncover_extract.py \
  --vdb merged0608.vdb \
  --rtl-root rtl \
  --out results/sample \
  --metrics line tgl branch cond \
  --limit-per-metric 10

# 全量提取 Line，不限制输出数量。
python3 uncover_extract.py \
  --vdb merged0608.vdb \
  --rtl-root rtl \
  --out results/line-full \
  --metrics line
```

入口可以使用绝对路径，从任意目录运行。输出目录必须与输入 VDB、RTL 根目录分离；默认拒绝覆盖已有结果，更新同一结果目录时显式添加 `--overwrite`。

**省略 `--limit-per-metric` 就是全量输出。** 当前工程约有 695 万个未覆盖 Toggle 方向目标，全量输出及实例对照会产生大量数据。先按指标、实例范围或限量查看结果更便于核查。限量不减少扫描范围，耗时和完整计数会写入 manifest。

上述限量规则适用于默认的 `--mode holes`。`differences` 始终检查所选范围的全部模型覆盖点，不接受 `--limit-per-metric`；对照实例可以位于 `--instance-root` 范围外。该模式默认在本地临时盘保存中间数据，结果目录仍由 `--out` 指定。

## 2. 换工程、选择测试与映射源码

```bash
# 更换 VDB 和 RTL 即可处理另一工程；不要求复制到工具所在目录。
python3 uncover_extract.py \
  --vdb /data/other_project/merged.vdb \
  --rtl-root /work/other_project/rtl \
  --out /work/results/other-project \
  --instance-root tb_top.dut \
  --metrics line branch cond \
  --limit-per-metric 20

# 多测试数据库必须明确选择测试，或使用 --all-tests 累计全部测试。
python3 uncover_extract.py \
  --vdb /data/run.vdb \
  --tests regression_a regression_b \
  --path-map /old/build/rtl /work/project/rtl \
  --rtl-root /work/project/rtl \
  --out /work/results/selected-tests \
  --limit-per-metric 20
```

单测试数据库自动选择唯一测试。多个测试通过 NPI 原生接口累计；不会拼接内部命中位串，也不会静默选择第一个测试。测试不存在或未明确选择时，错误及可用测试名称会保存在失败目录的 `execution.log` 中。

`--rtl-root`、`--path-map`、`--instance-root`、`--exclude-file` 均可以重复提供。路径映射优先级为：显式前缀映射、仍存在的原始路径、RTL 根目录内最长相对路径后缀、唯一文件名候选。多个候选标记为歧义；明确映射后找不到文件时不会回退到旧路径。

不提供 RTL、源码缺失或映射有歧义时，覆盖目标仍然保留。当前路径及文件哈希只证明找到了当前文件，不证明它与编译 VDB 时的源码和配置完全一致。

## 3. 输出内容

`--mode differences` 输出：

| 文件 | 内容 |
| --- | --- |
| `report.md` | 模块汇总，以及按确切实例状态分布归类的差异和不可比明细 |
| `module_summary.json` | 模型组、实例/源码目录、全体已覆盖、共同未覆盖、差异及特殊状态计数 |
| `differences.csv`、`differences.jsonl.gz` | 每个模型覆盖点一条明细，引用实例状态分组，不按未覆盖实例重复展开 |
| `difference_patterns.jsonl.gz` | 各分组的实例状态分布和不可比原因 |
| `coverage_facts.sqlite` | 共享的完整原生定位，以及逐实例 `covered/coverable/raw_status`，供追溯和离线导出 |
| `analysis_manifest.json`、`execution.log` | 输入指纹、测试、范围、原生统计、验证状态和性能 |

所有实例都已覆盖的点只进入汇总，不逐点报告；共同未覆盖也仅汇总，需要时离线导出。展示状态相同但原始标志不同不会自动计为差异，原始值仍保留。单实例或模型身份不足的组会明确标记；特殊状态和无法对齐的对照不会被当成普通未覆盖。

离线导出无需 NPI 或许可证，不会重新扫描 VDB：

```bash
# 为差异/特殊状态/不可比目标展开完整定位和逐实例原始状态。
python3 -m uncover_tool.export_facts \
  --result results/module-differences --kind differences \
  --out results/difference-details.jsonl.gz

# 还原所选实例的全部常规未覆盖目标及原始目标 ID。
python3 -m uncover_tool.export_facts \
  --result results/module-differences --kind holes \
  --out results/all-holes.jsonl.gz

# 展开所有可比实例都未覆盖的点，每个模型目标一条。
python3 -m uncover_tool.export_facts \
  --result results/module-differences --kind common-uncovered \
  --out results/common-uncovered.jsonl.gz
```

`holes` 离线导出保留目标身份和原始事实，采用模型目标/实例顺序，不承诺与旧主项的源码排序一致，也不附加重复的全组对照。模块差异模式不执行候选排除导出/验证；这些操作继续使用 `--mode holes`。`--validate-urg` 可用于两种模式。

默认的 `--mode holes` 输出：

| 文件                      | 内容                                                                                 |
| --------------------------- | -------------------------------------------------------------------------------------- |
| `report.md`               | 按指标汇总、未覆盖主项和所选目标的可比实例对照                                       |
| `uncovered_coverage.json` | 带 `schema_version` 的对象，`records` 数组保存完整目标身份、原生定位、状态和源码映射 |
| `uncovered_coverage.csv`  | 便于筛选的表格；完整结构以 JSON 为准                                                 |
| `analysis_manifest.json`  | 输入指纹、测试和范围、工具环境、完整计数、输出数量、映射问题、验证状态与性能         |
| `execution.log`           | 原生工具日志与扫描进度                                                               |

可选产物：

- `--per-metric`：额外生成各指标的 JSON/CSV。
- `--include-other-states`：完整遍历原子目标，将排除、不可达、不计分和未知等非正常状态保存到 `other_states.jsonl.gz`；比默认剪枝扫描更慢。
- `--compare-peers none|selected|all`：关闭比较、仅比较输出目标、比较所选范围全部原子目标。默认 `selected`；`all` 额外生成 `same_rtl_comparison.jsonl.gz`，保存差异及不可比记录。
- `--comparison-batch-size N`：每批比较的主项数，默认 `256`；共享目标路径在一批内只展开一次。
- `--comparison-cache-entries N`：内存比较缓存的条目上限，默认 `1024`；其余快照保存在工作区 SQLite，设为 `0` 时仅使用磁盘缓存。
- `--profile-comparison`：额外统计比较期间的原生子句柄调用、返回和释放数量，用于性能分析，默认关闭。

上述 `--compare-peers`、比较缓存和定位诊断参数用于 `holes` 路径。`differences` 独立比较全部模型目标，`--comparison-batch-size` 控制有界状态读写批次，最大 500；不使用旧的结果 LRU 或按目标重新定位路径。

覆盖状态、排除状态和不可达标记分别保存。常规清单只包含可计分且未命中的目标，排除和不可达对象不自动计为未覆盖。Toggle 按信号位及方向，Branch/Condition 按工具建立的 bin，Line 保留语句目标和所属 Block。

实例比较要求原始源码、模块定义/指标模型以及原生目标定位一致；不兼容实例保留原因。原生定位中的遍历下标只在记录的模型中有效，不能作为跨编译版本的稳定 ID。

比较按模型目标复用对照结果，并对同批目标共享路径进行批量读取。缓存只在当前原生会话及当前测试、排除状态下有效；进入排除导出前清空。每条 JSON 主项仍包含完整实例对照，输出大小不会随原生读取次数等比例减少。`--instance-root` 限定主项范围，对照实例仍可位于该范围外。

初扫复用遍历已读取的叶节点状态和源码定位。比较状态使用独立 SQLite 与有界批次，缓存退出直接回收自有数据库文件。`all` 对全部必要实例完成定位核验和状态读取后，仅为差异及不可比目标展开完整结果；公开状态、目标 ID、排序和主项明细保持原有含义。本轮实现及验证见 [Q1–Q4 验证报告](validation/comparison-q1-q4.md)。

扫描之后会显示 `comparison:` 进度，输出阶段显示 `output:` 进度。`analysis_manifest.json` 的 `performance.comparison` 保存索引构建、读取与缓存、结果组装耗时及复用计数，`performance.timings.output_seconds` 单独记录写出耗时。大规模任务可用 `--work-dir /tmp` 将 VDB 副本和 SQLite 放到本地临时盘；请预留副本、缓存和输出所需空间。

优化前后的真实 VDB 对照和最新验证范围见 [继续优化验证](validation/comparison-continuation.md)；上次恢复记录见 [比较性能验证](validation/comparison-performance.md)。`all` 的重复目标先批量去重再暂存，gzip 写出另有独立计时；单目标采用按需定位，多目标共享路径。

## 4. 可选排除与独立核验

```bash
# 导出候选排除文件，并在全新 VDB 副本中加载核对实际影响范围。
python3 uncover_extract.py \
  --vdb merged0608.vdb --rtl-root rtl \
  --out results/line-checked --metrics line \
  --limit-per-metric 10 \
  --export-exclusions --validate-exclusions

# 完整设计范围下，同时用 URG 独立核对每个指标的精确计数。
python3 uncover_extract.py \
  --vdb merged0608.vdb --rtl-root rtl \
  --out results/checked --limit-per-metric 10 \
  --validate-urg \
  --urg-bin /nfs/tools/synopsys/vcs/U-2023.03/bin/urg

# 应用已有外部排除后再提取；VDB 中已有的排除也会保留。
python3 uncover_extract.py \
  --vdb /data/run.vdb --exclude-file /work/existing.el \
  --out /work/results/with-existing-exclusions --metrics line \
  --limit-per-metric 10 --export-exclusions --validate-exclusions
```

`--export-exclusions` 生成 `extracted_coverage.el`。`--validate-exclusions` 可单独使用，只验证临时候选文件；验证结果保存为 `coverage_validation.json`。两者默认关闭。

导出保留原生编号、checksum 和记录，不按行号推算。候选文件取“新增原生记录”，不重复导出原有排除。导出与验证都在隔离副本中进行；不调用 `unload_exclusion()` 清掉原状态。验证核查选中实例及兼容对照实例中的全部 bin，报告明确记录核查范围，不宣称证明全设计每个 bin 都未变化。

Line 的 Block 排除可能影响多条语句。JSON 中记录本次所选候选在该实例/指标下的联合影响定位；`native_records_for_instance` 也是该实例本次候选的联合记录，不应误解为单个语句专属记录。未覆盖清单不自动等同于应当排除的清单。

`--validate-urg` 保存 `urg_validation/` 和精确计数比较结果。当前只支持完整设计汇总，不能与 `--instance-root` 同用。URG 仅在请求该选项时需要。也可以使用 `scripts/check_urg.py` 单独检查已有结果。

## 5. 安装与工程结构

可选安装（直接运行入口无需此步骤）：

```bash
python3 -m pip install --no-build-isolation --no-deps .
uncover-extract --help
```

安装环境需要 setuptools 61 或更新版本。安装只包含本工具代码，Verdi NPI 仍由本机安装提供。

此前构建的 wheel 安装包、测试编译产物和安装验证虚拟环境已清理；可直接运行源码或按上述命令安装，历史验证结果仍保存在 `validation/`。

```text
uncover_extract.py       直接运行的入口
clean.sh                终止相关提取任务并清空 results/
uncover_tool/           配置、原生读取、映射、排除、输出和 URG 核验
tests/                  无需原生工具的逻辑测试
examples/fixture/       可重建的小型验证设计
scripts/                测试库构建、场景回归、URG 独立核对
docs/                   输出字段、统计口径与内部流程说明
validation/             验证报告、基准摘要、最新原生场景和全设计结果
merged0608.vdb/          当前工程的输入覆盖数据库
rtl/                    当前工程的 RTL
```

工具入口、脚本、文档和验证结果均位于项目根目录，与当前工程的 VDB、RTL 同级。脚本根据自身位置定位项目目录，可以从其他工作目录通过绝对路径调用。

字段含义、统计口径与内部流程见 [输出格式](docs/输出格式.md)。保留的最新结果包括 [全设计 Toggle 验证](validation/comparison-next-toggle-full-all/report.md)、[四指标 fixture 样本](validation/comparison-next-scenarios/full/report.md)及[性能验证报告](validation/comparison-continuation.md)。

独立模块差异分析的实现、验证和性能见 [模块差异分析](docs/模块差异分析.md)，实际四指标报告见 [本次全设计结果](validation/module-differences-full/report.md)。

`validation/` 保留报告、审计摘要、各组 `benchmark.json` 和最新原生/全设计验证结果。旧版、探测、失败运行及基准逐轮重复输出已清理，历史命令和测量数据仍保存在摘要中；重新核对已删除运行的原始输出需要重跑。留存范围见 [验证资料说明](validation/README.md)。保留结果中的运行配置和原生定位仍记录当时的路径。

## 6. 测试、错误处理与限制

```bash
# 无需许可证的基础测试。
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v

# 需要 VCS 许可，生成两组真实仿真覆盖数据。
python3 scripts/build_fixture.py --out validation/fixture-new

# 需要 NPI 和 URG 许可；输出目录使用新目录。
python3 scripts/run_scenarios.py \
  --vdb validation/fixture-new/model.vdb \
  --out validation/scenarios-new
```

入口会打印进度日志位置，并显示扫描进度。`--work-dir` 可指定临时磁盘，`--timeout` 控制原生提取子进程超时秒数，默认 3600。原生库在独立进程内运行；失败不会发布半份正式结果，也不会替换之前成功的结果。失败目录保留日志、配置、错误和临时工作区位置以供排查，可能占用一份 VDB 副本。

尚未声明支持 FSM、VHDL、跨编译版本目标匹配或其他厂商覆盖库；也不提供 RTL 控制路径解释和不可达性证明。统计对照覆盖两个真实工程和一个可控设计，均为 U-2023.03，不能代表其他版本已通过验证。其他工具版本的 XML 身份提示无法识别时，仍尝试原生读取，但保守关闭无法确认的实例比较。

## 7. 清理提取输出

```bash
# 先查看将终止的进程和清理范围，不终止进程、不删除文件。
./clean.sh --dry-run

# 自动终止相关提取任务，再删除 results/（含正式结果及全部隐藏残留）。
./clean.sh
```

脚本按自身所在目录定位 `results/`，可以从任意工作目录调用，也可以使用 `bash clean.sh`。如果发现输出或工作目录位于这里的提取任务，会自动终止当前用户的相关任务及子进程。先向子进程发送 SIGTERM，并恢复暂停的主进程，让主进程回收子进程后退出；等待 3 秒后仍未退出的任务会收到 SIGTERM，再等待 3 秒仍未退出则发送 SIGKILL。确认相关任务不再运行后才删除文件；终止失败时保留输出并报错。重复运行无需额外处理，下次提取会自动创建输出目录。

如果之前遇到“仍有提取进程使用 results/”，其中 `[T]` 表示进程被暂停（例如按过 Ctrl+Z），并不代表任务退出，子进程还可能继续运行。直接重新执行更新后的 `bash clean.sh` 即可自动处理，不需要手动填写 PID。`--dry-run` 仅列出将终止的进程和删除范围。

清理范围固定为本工具目录下的 `results/`。程序、文档、测试、`validation/`、`dist/` 和原始 VDB/RTL 均保留；自定义到其他位置的 `--out` 或 `--work-dir` 不在此脚本的清理范围内。
