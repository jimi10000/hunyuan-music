# 贡献指南

感谢你愿意为论语贡献！

## 提交 Issue

1. 先搜一下有没有重复的 Issue
2. 用中文描述清楚：遇到什么问题、什么场景下出现、期望什么行为
3. 附带 `flow.py` 的通道判定结果、`preflight.py` 的扫描输出

## 提交 PR

### 1. 改什么

| 类型 | 位置 | 要求 |
|---|---|---|
| 流程调整 | `SKILL.md` | 保持 12 步结构，改 Step 内容 |
| 脚本新增/修改 | `scripts/` | 纯标准库，加 `--help`，写 docstring |
| 参考文档新增/修改 | `references/` | 中文，标题层级清晰 |
| 模板新增/修改 | `references/deliverable-templates.md` | 保持 markdown 代码块格式 |

### 2. 脚本规范

- **零外部依赖**：只用 Python 标准库
- **必须有 `--help`**：所有命令行参数要能自解释
- **必须有 docstring**：文件头三行说明用途 + 用法
- **退出码语义清晰**：0=成功 / 1=阻断 / 2=警告，别混用
- **测试**：提交前用真实项目跑一遍，贴输出

### 3. 提交规范

遵循 Conventional Commits：

```
feat: 新增 XX 分类
fix: 修复 XX 脚本的退出码
docs: 补充 XX 参考文档
refactor: 重构 XX 脚本结构
test: 新增 XX 测试
chore: 更新版本号
```

### 4. 版本号

遵循 SemVer：

- **MAJOR**：不兼容的流程变更（如删掉某个 Step）
- **MINOR**：新增功能（新脚本、新参考文档）
- **PATCH**：修复 bug、文案调整

改完记得更新：
1. `SKILL.md` 的 `metadata.version`
2. `CHANGELOG.md`
3. `README.md` 的版本 badge

### 5. 不许做

- 不要在脚本里加外部依赖（`pip install` / `npm install`）
- 不要在 SKILL.md 里堆砌《论语》原文当装饰
- 不要省略边界场景的处理
- 不要把 `print` / `console.log` 当日志

## 本地测试

```bash
# 1. 判通道
cd /tmp && mkdir test && cd test && git init
python scripts/flow.py

# 2. 日志系统
python scripts/logkit.py init --lang python --dest ./src/logkit
cd src && python logkit/example.py

# 3. 密钥扫描
python scripts/preflight.py --path ./src

# 4. 缺陷台账
python scripts/bug_ledger.py add --title "测试" --priority P1
python scripts/bug_ledger.py stats

# 5. 版本存档
python scripts/release_note.py tag --version 1.0.0 --summary "首个版本"
```

## 许可证

[MIT](LICENSE)
