# 设计后端与内部命令

## KDB / NPI（优先）

```bash
python3 <skill>/scripts/design_query.py check --result <结果> --uc UC-000123 \
  --backend kdb --kdb <simv.daidir> --seconds 90
python3 <skill>/scripts/design_query.py trace --result <结果> --uc UC-000123 \
  --backend kdb --kdb <simv.daidir> --instance top.u --signal 'q[0]' \
  --nodes 300 --branches 64 --seconds 60
```

`--kdb` 接受含 `simv.kdb` 的目录或该文件。Verdi 安装路径依次来自 `--verdi-home`、原事实配置、`VERDI_HOME`。需 Python NPI ABI 和可用许可证。脚本在子进程中调用 `npisys.load_design(... -dbdir ...)`，用 language 对象和 trace_driver2 返回具体实例、声明范围、驱动对象、表达式及语句祖先。端口和选择对象保留原生关系，未能可靠映射的位不会补全。图中 lhs/rhs、condition、stmt/else_stmt、operand、事件和赋值 blocking 属性保留各自语义。

进程崩溃、许可证错误或超时保存在本点 `logs/query-*/` 中，不静默切换后端。VDB 原生查询使用受指纹核对的副本，原库只读；KDB 查询前后核对目录内容指纹。日志及请求/响应均在本点目录中。KDB 中精确实例/信号不存在时不得按模块同名查找替代。

## pyslang（显式备用）

需要 `pyslang>=11,<12`（开发验证为 11.0.0）。缺依赖时说明安装条件，不切换成正则 RTL 解析器。使用 slang 的编译驱动读取原 filelist、宏、include、库、顶层及参数，禁止仅遍历 RTL 目录猜测完整构建。官方编译模型：[slang Compilation Overview](https://sv-lang.com/overview.html)。

在本点目录保存 `design-config.json`：

```json
{
  "cwd": "/absolute/build/input/directory",
  "args": ["-f", "/absolute/compile.f", "--top", "top", "-D", "FEATURE=1", "-I", "/absolute/include"],
  "instance_map": {}
}
```

`args` 采用已安装 slang 接受的编译参数。`instance_map` 只有在用户/构建配置明确证明层级映射时填写，键是覆盖实例的完整路径，值是重建设计实例完整路径。不要用后缀/同名猜测替代。

```bash
python3 <skill>/scripts/design_query.py inspect --result <结果> --uc UC-000123 \
  --backend pyslang --config <本点目录>/design-config.json --seconds 60
python3 <skill>/scripts/design_query.py trace --result <结果> --uc UC-000123 \
  --backend pyslang --config <本点目录>/design-config.json --instance top.u --signal en
```

编译有错误时停止使用部分 AST。返回展开实例、参数、端口实际连接、局部赋值、控制/事件上下文、共享表达式节点和数据依赖。位映射采用 LSB 偏移并保留声明下标，支持直接引用、常量、拼接、静态切片、常见整数转换的符号/零扩展和截断；算术节点保留表达式依赖，不擅自拆成单源位。每条赋值默认最多展示 32 位，可通过追踪具体位缩小范围。

过程保留阻塞/非阻塞、顺序、复位/使能路径及 case 匹配规则，但不做完整过程符号执行；同一过程后写优先和未赋值保持须结合完整过程说明。多驱动、动态索引、聚合类型、函数调用、循环/提前退出等不支持结构返回边界。图有节点、分支、递归预算，缺失节点不是无依赖证明。重新编译查询不会建立全设计长公式；复用当前运行已有证据，避免无意义重复编译。

## 对应状态

- `verified`：已有历史构建来源记录将选定 VDB、当前设计身份、目标源码和编译配置绑定，所有核验通过。
- `unconfirmed`：对象与源码未见冲突，但缺少历史编译证据。只能给附假设的候选分析。
- `conflict`：模块、目标位、源码指纹或插桩范围等存在冲突。保留冲突，停止历史归因。

`design_query.py` 每次返回 correspondence，默认不会因为编译成功给 verified。`--build-record <path>` 可读取**已有构建系统产生的历史来源记录**：

```json
{
  "schema_version": "coverage-build-identity-v1",
  "origin": "构建系统已有记录及其可复核位置",
  "database_id": "VDB 全文件指纹",
  "design_sha256": "本次后端 design_identity.sha256 或 KDB fingerprint",
  "source_hashes": {"/absolute/target.sv": "同次编译源码指纹"},
  "compile_configuration": {"说明": "原始宏、filelist、参数、顶层和工具选项"}
}
```

不能为得到 verified 事后编造这份记录，不能将查询到的当前指纹反填后充当历史证据。构建记录是信任输入，脚本核验其绑定的一致性，不验证记录发布者的真实性。历史原生定义 checksum 不是 SHA256；不可直接混比。源码映射中 sha256 是提取时看到的文件，不当然是历史仿真的源码。

`check`、`inspect`、`trace` 共用局部查询实现，分别为证据标记不同意图；`trace` 不自动递归追整个设计，当前 Codex 根据未解决问题发起下一次查询。每个查询包含已定位的覆盖身份、设计身份、源码/原生证据和能力边界。
