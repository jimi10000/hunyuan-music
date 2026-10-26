# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [1.0.0] - 2026-10-26

### Added

- **十二则心法**：将《论语》十二条准则映射到研发流水线的 12 个节点，作为判断依据而非装饰
- **三通道机制**：`flow.py` 按改动规模 + 敏感路径自动判定快速/标准/完整通道，避免小改动套重流程
- **分类日志系统**：`logkit.py` 生成 Python/TS 日志模块，按问题域分类 + trace_id 全链路 + 严格分级 + 结构化字段 + 耗时埋点
- **提交前密钥扫描**：`preflight.py` 检测硬编码密钥、调试残留、大文件、.gitignore 缺项，退出码可直接接 git hook / CI
- **缺陷工单台账**：`bug_ledger.py` 管理 P0-P3 缺陷，状态流转 `新建 → 修复中 → 自测通过 → CR验证 → 关闭`，`stats` 带门禁校验
- **版本存档**：`release_note.py` 生成版本记录、CHANGELOG 条目、tag/合并/回滚命令，支持 SemVer 递增
- **七维度 CR 清单**：正确性、边界、可读性、耦合、健壮性、规范、安全，每条意见给可执行改法
- **质量门禁金字塔**：编码 → 提交前 → 单测 → CI → CR → 线上，各层成本与手段明确
- **事故复盘模板**：无责模式 + 五个为什么 + MTTD/MTTR 指标 + 改进项带负责人和截止日期
- **可观测性三支柱**：日志、指标、追踪，上线前必须回答的三个问题
- **发布策略**：Feature Flag / 灰度 / 蓝绿，数据库变更的两次发布法
- **6 份参考文档**：git-release、bug-triage、review-checklist、deliverable-templates、logging-observability、quality-gates

### Scripts

- `scripts/flow.py` — 判通道（快速/标准/完整）
- `scripts/logkit.py` — 生成分类日志系统（Python/TS/Both）
- `scripts/preflight.py` — 提交前扫描（密钥/调试残留/大文件/.gitignore）
- `scripts/bug_ledger.py` — 缺陷台账（add/list/show/update/stats/export）
- `scripts/release_note.py` — 版本存档（tag/list/show/rollback）

### Notes

- 所有脚本零外部依赖，纯 Python 标准库
- 数据落盘统一在项目根 `.lunyu/` 目录
- 技能兼容 WorkBuddy Agent Skill 规范
