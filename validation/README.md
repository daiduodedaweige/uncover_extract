# 验证资料留存说明

本目录已按用户确认的清理方案精简。旧版、探测、失败运行的中间产物，以及基准每轮重复生成的完整 JSON、CSV、Markdown 和日志已删除；工具代码、原始 VDB/RTL 和目录外文件未纳入清理。

保留以下资料：

- [最新性能报告](comparison-continuation.md)、[历史恢复报告](comparison-performance.md)、[当前审计](comparison-next-audit.json)、[历史审计](comparison-recovery-audit.json)及版本、定位测量和资源采样 JSON。
- 各组 `benchmark.json`：保留全部逐次命令、输入指纹、源码哈希、耗时、资源和结果等价结论，包括早期探测或未完成组；是否有效仍按各组记录和报告判断。
- 历史 `scenario_results.json`、`identity_results.json` 汇总；旧场景的完整输出已删除。
- [最新原生场景](comparison-next-scenarios/scenario_results.json)：保留完整成功输出及预期失败的小型诊断，删除失败场景遗留的 VDB 副本、SQLite 和原生日志工作区。
- [最新缺失身份场景](comparison-next-missing-identity-verified/identity_results.json)及[磁盘缓存边界场景](comparison-next-disk-only/analysis_manifest.json)的完整结果。
- [全设计 Toggle all 结果](comparison-next-toggle-full-all/analysis_manifest.json)、[完整限量扫描](comparison-next-toggle-inventory-complete/analysis_manifest.json)、[URG 独立核验](comparison-next-toggle-urg/comparison.json)和[资源采样](comparison-next-toggle-full-all-resources.json)。

报告和审计中的等价检查已在清理前完成，结论不变。基准摘要里的输出路径、命令和历史审计路径保留原值；其中被删除的逐轮输出已不能直接打开或重新比较，需要使用记录的版本和参数重跑。最新保留结果仍可直接检查。

清理文件清单、字节数及保留文件的校验记录见 [cleanup-record.json](cleanup-record.json)。清理仅处理 `validation/`，未自动提交。
