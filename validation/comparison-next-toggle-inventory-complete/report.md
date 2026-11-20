# 未覆盖信息

数据库：`/nfs/home/weijing/workspace/uncover_extract/merged0608.vdb`。测试：`test`。

以下编号行是未覆盖原子目标；对照行不计入主项。完整身份、原始状态与映射信息见 JSON。

| 指标 | 已覆盖 / 可计分 | 范围内未覆盖目标 | 输出主项 |
| --- | ---: | ---: | ---: |
| tgl | 630411/7580152 | 6949741 | 1 |

覆盖率采用原生计分值；限量仅影响输出主项。缺少源码不影响覆盖目标保留。

| 序号 | 指标 | 原始源码 | 行号 | 实例 | 状态 | 具体目标 |
| ---: | --- | --- | ---: | --- | --- | --- |
| 1 | tgl | /nfs/home/weijing/workspace/TestDongJiangV2_copy/TestDongJiangV2/build/DongJiangTop-release/rtl/Arbiter2_CMTaskBdl.sv | 3 | `tb_top.u_DongJiangTop.hnx.slice.exu.exu_0.cmTask_arb.arb` | 未覆盖 | io_in_0_ready / 0 -> 1 |

排除验证状态：`not_requested`。未覆盖不自动代表应当排除或不可达。

[JSON](uncovered_coverage.json) · [CSV](uncovered_coverage.csv) · [运行摘要](analysis_manifest.json)
