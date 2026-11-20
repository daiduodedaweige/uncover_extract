# 未覆盖信息

数据库：`/tmp/uncover-comparison-fixture-native/model.vdb`。测试：`mode0, mode1`。

以下编号行是未覆盖原子目标；对照行不计入主项。完整身份、原始状态与映射信息见 JSON。

| 指标 | 已覆盖 / 可计分 | 范围内未覆盖目标 | 输出主项 |
| --- | ---: | ---: | ---: |
| line | 46/55 | 9 | 9 |
| tgl | 66/74 | 8 | 8 |
| branch | 13/17 | 4 | 4 |
| cond | 25/36 | 11 | 11 |

覆盖率采用原生计分值；限量仅影响输出主项。缺少源码不影响覆盖目标保留。

| 序号 | 指标 | 原始源码 | 行号 | 实例 | 状态 | 具体目标 |
| ---: | --- | --- | ---: | --- | --- | --- |
| 1 | line | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 26 | `fixture_tb` | 未覆盖 | IF / 26.1 |
| 2 | line | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 9 | `fixture_tb.right` | 未覆盖 | ELSE / 9 |
| | line | | | `fixture_tb.left` | covered (1/1) | |
| | line | | | `fixture_tb.wide` | covered (1/1) | |
| 3 | line | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 9 | `fixture_tb.right` | 未覆盖 | IF / 9.1 |
| | line | | | `fixture_tb.left` | covered (1/1) | |
| | line | | | `fixture_tb.wide` | covered (1/1) | |
| 4 | line | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 9 | `fixture_tb.right` | 未覆盖 | IF / 9.2 |
| | line | | | `fixture_tb.left` | covered (1/1) | |
| | line | | | `fixture_tb.wide` | covered (1/1) | |
| 5 | line | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 10 | `fixture_tb.right` | 未覆盖 | ELSE / 10 |
| | line | | | `fixture_tb.left` | covered (1/1) | |
| | line | | | `fixture_tb.wide` | covered (1/1) | |
| 6 | line | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 10 | `fixture_tb.right` | 未覆盖 | IF / 10.1 |
| | line | | | `fixture_tb.left` | covered (1/1) | |
| | line | | | `fixture_tb.wide` | covered (1/1) | |
| 7 | line | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 10 | `fixture_tb.right` | 未覆盖 | IF / 10.2 |
| | line | | | `fixture_tb.left` | covered (1/1) | |
| | line | | | `fixture_tb.wide` | covered (1/1) | |
| 8 | line | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 11 | `fixture_tb.right` | 未覆盖 | ELSE / 11 |
| | line | | | `fixture_tb.left` | covered (1/1) | |
| | line | | | `fixture_tb.wide` | covered (1/1) | |
| 9 | line | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 11 | `fixture_tb.right` | 未覆盖 | ELSE / 11.1 |
| | line | | | `fixture_tb.left` | covered (1/1) | |
| | line | | | `fixture_tb.wide` | covered (1/1) | |
| 10 | tgl | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 16 | `fixture_tb` | 未覆盖 | rst / 0 -> 1 |
| 11 | tgl | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 3 | `fixture_tb.left` | 未覆盖 | rst / 0 -> 1 |
| | tgl | | | `fixture_tb.right` | uncovered (0/1) | |
| 12 | tgl | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 3 | `fixture_tb.right` | 未覆盖 | rst / 0 -> 1 |
| | tgl | | | `fixture_tb.left` | uncovered (0/1) | |
| 13 | tgl | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 3 | `fixture_tb.right` | 未覆盖 | sel / sel[0] / 0 -> 1 |
| | tgl | | | `fixture_tb.left` | covered (1/1) | |
| 14 | tgl | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 3 | `fixture_tb.right` | 未覆盖 | sel / sel[0] / 1 -> 0 |
| | tgl | | | `fixture_tb.left` | covered (1/1) | |
| 15 | tgl | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 3 | `fixture_tb.right` | 未覆盖 | sel / sel[1] / 0 -> 1 |
| | tgl | | | `fixture_tb.left` | covered (1/1) | |
| 16 | tgl | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 3 | `fixture_tb.right` | 未覆盖 | sel / sel[1] / 1 -> 0 |
| | tgl | | | `fixture_tb.left` | covered (1/1) | |
| 17 | tgl | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 3 | `fixture_tb.wide` | 未覆盖 | rst / 0 -> 1 |
| 18 | branch | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 26 | `fixture_tb` | 未覆盖 | (!$value$plusargs("mode=%d", mode)) / 01 |
| 19 | branch | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 9 | `fixture_tb.right` | 未覆盖 | rst / 00011010 |
| | branch | | | `fixture_tb.left` | covered (1/1) | |
| 20 | branch | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 10 | `fixture_tb.right` | 未覆盖 | rst / 01101010 |
| | branch | | | `fixture_tb.left` | covered (1/1) | |
| 21 | branch | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 11 | `fixture_tb.right` | 未覆盖 | rst / 10101010 |
| | branch | | | `fixture_tb.left` | covered (1/1) | |
| 22 | cond | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 8 | `fixture_tb.right` | 未覆盖 | (sel == 2'b0) / 0 |
| | cond | | | `fixture_tb.left` | covered (1/1) | |
| 23 | cond | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 9 | `fixture_tb.right` | 未覆盖 | ((sel == 2'b1) && flag) / 01 |
| | cond | | | `fixture_tb.left` | covered (1/1) | |
| 24 | cond | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 9 | `fixture_tb.right` | 未覆盖 | ((sel == 2'b1) && flag) / 10 |
| | cond | | | `fixture_tb.left` | covered (1/1) | |
| 25 | cond | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 9 | `fixture_tb.right` | 未覆盖 | ((sel == 2'b1) && flag) / 11 |
| | cond | | | `fixture_tb.left` | covered (1/1) | |
| 26 | cond | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 9 | `fixture_tb.right` | 未覆盖 | ((sel == 2'b1) && flag) / (sel == 2'b1) / 0 |
| | cond | | | `fixture_tb.left` | covered (1/1) | |
| 27 | cond | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 9 | `fixture_tb.right` | 未覆盖 | ((sel == 2'b1) && flag) / (sel == 2'b1) / 1 |
| | cond | | | `fixture_tb.left` | covered (1/1) | |
| 28 | cond | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 10 | `fixture_tb.right` | 未覆盖 | ((sel == 2'h2) &#124;&#124; flag) / 00 |
| | cond | | | `fixture_tb.left` | covered (1/1) | |
| 29 | cond | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 10 | `fixture_tb.right` | 未覆盖 | ((sel == 2'h2) &#124;&#124; flag) / 01 |
| | cond | | | `fixture_tb.left` | covered (1/1) | |
| 30 | cond | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 10 | `fixture_tb.right` | 未覆盖 | ((sel == 2'h2) &#124;&#124; flag) / 10 |
| | cond | | | `fixture_tb.left` | covered (1/1) | |
| 31 | cond | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 10 | `fixture_tb.right` | 未覆盖 | ((sel == 2'h2) &#124;&#124; flag) / (sel == 2'h2) / 0 |
| | cond | | | `fixture_tb.left` | covered (1/1) | |
| 32 | cond | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 10 | `fixture_tb.right` | 未覆盖 | ((sel == 2'h2) &#124;&#124; flag) / (sel == 2'h2) / 1 |
| | cond | | | `fixture_tb.left` | covered (1/1) | |

排除验证状态：`not_requested`。未覆盖不自动代表应当排除或不可达。

[JSON](uncovered_coverage.json) · [CSV](uncovered_coverage.csv) · [运行摘要](analysis_manifest.json)
