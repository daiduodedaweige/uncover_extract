# 未覆盖信息

数据库：`/tmp/uncover-comparison-fixture-native/model.vdb`。测试：`mode0`。

以下编号行是未覆盖原子目标；对照行不计入主项。完整身份、原始状态与映射信息见 JSON。

| 指标 | 已覆盖 / 可计分 | 范围内未覆盖目标 | 输出主项 |
| --- | ---: | ---: | ---: |
| line | 30/55 | 25 | 2 |
| tgl | 40/74 | 34 | 2 |
| branch | 7/17 | 10 | 2 |
| cond | 3/36 | 33 | 2 |

覆盖率采用原生计分值；限量仅影响输出主项。缺少源码不影响覆盖目标保留。

| 序号 | 指标 | 原始源码 | 行号 | 实例 | 状态 | 具体目标 |
| ---: | --- | --- | ---: | --- | --- | --- |
| 1 | line | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 26 | `fixture_tb` | 未覆盖 | IF / 26.1 |
| 2 | line | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 9 | `fixture_tb.left` | 未覆盖 | ELSE / 9 |
| | line | | | `fixture_tb.right` | uncovered (0/1) | |
| | line | | | `fixture_tb.wide` | uncovered (0/1) | |
| 3 | tgl | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 16 | `fixture_tb` | 未覆盖 | rst / 0 -> 1 |
| 4 | tgl | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 16 | `fixture_tb` | 未覆盖 | flag / 0 -> 1 |
| 5 | branch | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 26 | `fixture_tb` | 未覆盖 | (!$value$plusargs("mode=%d", mode)) / 01 |
| 6 | branch | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 9 | `fixture_tb.left` | 未覆盖 | rst / 00011010 |
| | branch | | | `fixture_tb.right` | uncovered (0/1) | |
| 7 | cond | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 8 | `fixture_tb.left` | 未覆盖 | (sel == 2'b0) / 0 |
| | cond | | | `fixture_tb.right` | uncovered (0/1) | |
| 8 | cond | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 9 | `fixture_tb.left` | 未覆盖 | ((sel == 2'b1) && flag) / 01 |
| | cond | | | `fixture_tb.right` | uncovered (0/1) | |

排除验证状态：`not_requested`。未覆盖不自动代表应当排除或不可达。

[JSON](uncovered_coverage.json) · [CSV](uncovered_coverage.csv) · [运行摘要](analysis_manifest.json)
