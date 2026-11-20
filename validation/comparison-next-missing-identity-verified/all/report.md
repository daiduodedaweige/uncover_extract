# 未覆盖信息

数据库：`/tmp/uncover-comparison-fixture-native/model.vdb`。测试：`mode0`。

以下编号行是未覆盖原子目标；对照行不计入主项。完整身份、原始状态与映射信息见 JSON。

| 指标 | 已覆盖 / 可计分 | 范围内未覆盖目标 | 输出主项 |
| --- | ---: | ---: | ---: |
| line | 30/55 | 25 | 25 |
| tgl | 40/74 | 34 | 34 |
| branch | 7/17 | 10 | 10 |
| cond | 3/36 | 33 | 33 |

覆盖率采用原生计分值；限量仅影响输出主项。缺少源码不影响覆盖目标保留。

| 序号 | 指标 | 原始源码 | 行号 | 实例 | 状态 | 具体目标 |
| ---: | --- | --- | ---: | --- | --- | --- |
| 1 | line | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 26 | `fixture_tb` | 未覆盖 | IF / 26.1 |
| 2 | line | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 9 | `fixture_tb.left` | 未覆盖 | ELSE / 9 |
| 3 | line | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 9 | `fixture_tb.left` | 未覆盖 | IF / 9.1 |
| 4 | line | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 9 | `fixture_tb.left` | 未覆盖 | IF / 9.2 |
| 5 | line | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 10 | `fixture_tb.left` | 未覆盖 | ELSE / 10 |
| 6 | line | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 10 | `fixture_tb.left` | 未覆盖 | IF / 10.1 |
| 7 | line | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 10 | `fixture_tb.left` | 未覆盖 | IF / 10.2 |
| 8 | line | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 11 | `fixture_tb.left` | 未覆盖 | ELSE / 11 |
| 9 | line | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 11 | `fixture_tb.left` | 未覆盖 | ELSE / 11.1 |
| 10 | line | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 9 | `fixture_tb.right` | 未覆盖 | ELSE / 9 |
| 11 | line | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 9 | `fixture_tb.right` | 未覆盖 | IF / 9.1 |
| 12 | line | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 9 | `fixture_tb.right` | 未覆盖 | IF / 9.2 |
| 13 | line | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 10 | `fixture_tb.right` | 未覆盖 | ELSE / 10 |
| 14 | line | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 10 | `fixture_tb.right` | 未覆盖 | IF / 10.1 |
| 15 | line | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 10 | `fixture_tb.right` | 未覆盖 | IF / 10.2 |
| 16 | line | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 11 | `fixture_tb.right` | 未覆盖 | ELSE / 11 |
| 17 | line | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 11 | `fixture_tb.right` | 未覆盖 | ELSE / 11.1 |
| 18 | line | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 9 | `fixture_tb.wide` | 未覆盖 | ELSE / 9 |
| 19 | line | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 9 | `fixture_tb.wide` | 未覆盖 | IF / 9.1 |
| 20 | line | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 9 | `fixture_tb.wide` | 未覆盖 | IF / 9.2 |
| 21 | line | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 10 | `fixture_tb.wide` | 未覆盖 | ELSE / 10 |
| 22 | line | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 10 | `fixture_tb.wide` | 未覆盖 | IF / 10.1 |
| 23 | line | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 10 | `fixture_tb.wide` | 未覆盖 | IF / 10.2 |
| 24 | line | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 11 | `fixture_tb.wide` | 未覆盖 | ELSE / 11 |
| 25 | line | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 11 | `fixture_tb.wide` | 未覆盖 | ELSE / 11.1 |
| 26 | tgl | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 16 | `fixture_tb` | 未覆盖 | rst / 0 -> 1 |
| 27 | tgl | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 16 | `fixture_tb` | 未覆盖 | flag / 0 -> 1 |
| 28 | tgl | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 16 | `fixture_tb` | 未覆盖 | flag / 1 -> 0 |
| 29 | tgl | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 17 | `fixture_tb` | 未覆盖 | sel / sel[0] / 0 -> 1 |
| 30 | tgl | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 17 | `fixture_tb` | 未覆盖 | sel / sel[0] / 1 -> 0 |
| 31 | tgl | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 17 | `fixture_tb` | 未覆盖 | sel / sel[1] / 0 -> 1 |
| 32 | tgl | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 17 | `fixture_tb` | 未覆盖 | sel / sel[1] / 1 -> 0 |
| 33 | tgl | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 3 | `fixture_tb.left` | 未覆盖 | rst / 0 -> 1 |
| 34 | tgl | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 3 | `fixture_tb.left` | 未覆盖 | sel / sel[0] / 0 -> 1 |
| 35 | tgl | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 3 | `fixture_tb.left` | 未覆盖 | sel / sel[0] / 1 -> 0 |
| 36 | tgl | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 3 | `fixture_tb.left` | 未覆盖 | sel / sel[1] / 0 -> 1 |
| 37 | tgl | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 3 | `fixture_tb.left` | 未覆盖 | sel / sel[1] / 1 -> 0 |
| 38 | tgl | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 4 | `fixture_tb.left` | 未覆盖 | flag / 0 -> 1 |
| 39 | tgl | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 4 | `fixture_tb.left` | 未覆盖 | flag / 1 -> 0 |
| 40 | tgl | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 5 | `fixture_tb.left` | 未覆盖 | seen / 0 -> 1 |
| 41 | tgl | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 5 | `fixture_tb.left` | 未覆盖 | seen / 1 -> 0 |
| 42 | tgl | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 3 | `fixture_tb.right` | 未覆盖 | rst / 0 -> 1 |
| 43 | tgl | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 3 | `fixture_tb.right` | 未覆盖 | sel / sel[0] / 0 -> 1 |
| 44 | tgl | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 3 | `fixture_tb.right` | 未覆盖 | sel / sel[0] / 1 -> 0 |
| 45 | tgl | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 3 | `fixture_tb.right` | 未覆盖 | sel / sel[1] / 0 -> 1 |
| 46 | tgl | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 3 | `fixture_tb.right` | 未覆盖 | sel / sel[1] / 1 -> 0 |
| 47 | tgl | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 4 | `fixture_tb.right` | 未覆盖 | flag / 0 -> 1 |
| 48 | tgl | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 4 | `fixture_tb.right` | 未覆盖 | flag / 1 -> 0 |
| 49 | tgl | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 5 | `fixture_tb.right` | 未覆盖 | seen / 0 -> 1 |
| 50 | tgl | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 5 | `fixture_tb.right` | 未覆盖 | seen / 1 -> 0 |
| 51 | tgl | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 3 | `fixture_tb.wide` | 未覆盖 | rst / 0 -> 1 |
| 52 | tgl | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 3 | `fixture_tb.wide` | 未覆盖 | sel / sel[0] / 0 -> 1 |
| 53 | tgl | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 3 | `fixture_tb.wide` | 未覆盖 | sel / sel[0] / 1 -> 0 |
| 54 | tgl | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 3 | `fixture_tb.wide` | 未覆盖 | sel / sel[1] / 0 -> 1 |
| 55 | tgl | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 3 | `fixture_tb.wide` | 未覆盖 | sel / sel[1] / 1 -> 0 |
| 56 | tgl | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 4 | `fixture_tb.wide` | 未覆盖 | flag / 0 -> 1 |
| 57 | tgl | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 4 | `fixture_tb.wide` | 未覆盖 | flag / 1 -> 0 |
| 58 | tgl | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 5 | `fixture_tb.wide` | 未覆盖 | seen / 0 -> 1 |
| 59 | tgl | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 5 | `fixture_tb.wide` | 未覆盖 | seen / 1 -> 0 |
| 60 | branch | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 26 | `fixture_tb` | 未覆盖 | (!$value$plusargs("mode=%d", mode)) / 01 |
| 61 | branch | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 9 | `fixture_tb.left` | 未覆盖 | rst / 00011010 |
| 62 | branch | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 10 | `fixture_tb.left` | 未覆盖 | rst / 01101010 |
| 63 | branch | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 11 | `fixture_tb.left` | 未覆盖 | rst / 10101010 |
| 64 | branch | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 9 | `fixture_tb.right` | 未覆盖 | rst / 00011010 |
| 65 | branch | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 10 | `fixture_tb.right` | 未覆盖 | rst / 01101010 |
| 66 | branch | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 11 | `fixture_tb.right` | 未覆盖 | rst / 10101010 |
| 67 | branch | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 9 | `fixture_tb.wide` | 未覆盖 | rst / 00011010 |
| 68 | branch | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 10 | `fixture_tb.wide` | 未覆盖 | rst / 01101010 |
| 69 | branch | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 11 | `fixture_tb.wide` | 未覆盖 | rst / 10101010 |
| 70 | cond | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 8 | `fixture_tb.left` | 未覆盖 | (sel == 2'b0) / 0 |
| 71 | cond | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 9 | `fixture_tb.left` | 未覆盖 | ((sel == 2'b1) && flag) / 01 |
| 72 | cond | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 9 | `fixture_tb.left` | 未覆盖 | ((sel == 2'b1) && flag) / 10 |
| 73 | cond | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 9 | `fixture_tb.left` | 未覆盖 | ((sel == 2'b1) && flag) / 11 |
| 74 | cond | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 9 | `fixture_tb.left` | 未覆盖 | ((sel == 2'b1) && flag) / (sel == 2'b1) / 0 |
| 75 | cond | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 9 | `fixture_tb.left` | 未覆盖 | ((sel == 2'b1) && flag) / (sel == 2'b1) / 1 |
| 76 | cond | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 10 | `fixture_tb.left` | 未覆盖 | ((sel == 2'h2) &#124;&#124; flag) / 00 |
| 77 | cond | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 10 | `fixture_tb.left` | 未覆盖 | ((sel == 2'h2) &#124;&#124; flag) / 01 |
| 78 | cond | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 10 | `fixture_tb.left` | 未覆盖 | ((sel == 2'h2) &#124;&#124; flag) / 10 |
| 79 | cond | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 10 | `fixture_tb.left` | 未覆盖 | ((sel == 2'h2) &#124;&#124; flag) / (sel == 2'h2) / 0 |
| 80 | cond | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 10 | `fixture_tb.left` | 未覆盖 | ((sel == 2'h2) &#124;&#124; flag) / (sel == 2'h2) / 1 |
| 81 | cond | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 8 | `fixture_tb.right` | 未覆盖 | (sel == 2'b0) / 0 |
| 82 | cond | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 9 | `fixture_tb.right` | 未覆盖 | ((sel == 2'b1) && flag) / 01 |
| 83 | cond | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 9 | `fixture_tb.right` | 未覆盖 | ((sel == 2'b1) && flag) / 10 |
| 84 | cond | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 9 | `fixture_tb.right` | 未覆盖 | ((sel == 2'b1) && flag) / 11 |
| 85 | cond | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 9 | `fixture_tb.right` | 未覆盖 | ((sel == 2'b1) && flag) / (sel == 2'b1) / 0 |
| 86 | cond | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 9 | `fixture_tb.right` | 未覆盖 | ((sel == 2'b1) && flag) / (sel == 2'b1) / 1 |
| 87 | cond | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 10 | `fixture_tb.right` | 未覆盖 | ((sel == 2'h2) &#124;&#124; flag) / 00 |
| 88 | cond | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 10 | `fixture_tb.right` | 未覆盖 | ((sel == 2'h2) &#124;&#124; flag) / 01 |
| 89 | cond | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 10 | `fixture_tb.right` | 未覆盖 | ((sel == 2'h2) &#124;&#124; flag) / 10 |
| 90 | cond | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 10 | `fixture_tb.right` | 未覆盖 | ((sel == 2'h2) &#124;&#124; flag) / (sel == 2'h2) / 0 |
| 91 | cond | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 10 | `fixture_tb.right` | 未覆盖 | ((sel == 2'h2) &#124;&#124; flag) / (sel == 2'h2) / 1 |
| 92 | cond | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 8 | `fixture_tb.wide` | 未覆盖 | (sel == 2'b0) / 0 |
| 93 | cond | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 9 | `fixture_tb.wide` | 未覆盖 | ((sel == 2'b1) && flag) / 01 |
| 94 | cond | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 9 | `fixture_tb.wide` | 未覆盖 | ((sel == 2'b1) && flag) / 10 |
| 95 | cond | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 9 | `fixture_tb.wide` | 未覆盖 | ((sel == 2'b1) && flag) / 11 |
| 96 | cond | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 9 | `fixture_tb.wide` | 未覆盖 | ((sel == 2'b1) && flag) / (sel == 2'b1) / 0 |
| 97 | cond | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 9 | `fixture_tb.wide` | 未覆盖 | ((sel == 2'b1) && flag) / (sel == 2'b1) / 1 |
| 98 | cond | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 10 | `fixture_tb.wide` | 未覆盖 | ((sel == 2'h2) &#124;&#124; flag) / 00 |
| 99 | cond | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 10 | `fixture_tb.wide` | 未覆盖 | ((sel == 2'h2) &#124;&#124; flag) / 01 |
| 100 | cond | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 10 | `fixture_tb.wide` | 未覆盖 | ((sel == 2'h2) &#124;&#124; flag) / 10 |
| 101 | cond | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 10 | `fixture_tb.wide` | 未覆盖 | ((sel == 2'h2) &#124;&#124; flag) / (sel == 2'h2) / 0 |
| 102 | cond | /nfs/home/weijing/workspace/uncover_extract/examples/fixture/fixture.sv | 10 | `fixture_tb.wide` | 未覆盖 | ((sel == 2'h2) &#124;&#124; flag) / (sel == 2'h2) / 1 |

排除验证状态：`not_requested`。未覆盖不自动代表应当排除或不可达。

[JSON](uncovered_coverage.json) · [CSV](uncovered_coverage.csv) · [运行摘要](analysis_manifest.json)
