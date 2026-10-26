# 质量门禁与事故复盘

## 质量门禁金字塔

**缺陷发现得越晚，修复成本呈指数上升。** 这是软件工程里最可靠的规律。

| 阶段 | 发现成本 | 手段 |
|---|---|---|
| 编码时 | 1x | 类型检查、Linter、IDE 检查 |
| 提交前 | 5x | `preflight.py`（密钥/调试残留/大文件） |
| 单测 | 10x | 单元测试覆盖边界 |
| CI | 30x | 集成测试 + 覆盖率门禁 |
| CR | 100x | 人工评审 |
| 线上 | 1000x | 告警 + 用户投诉 |

**结论：能自动化的绝不靠人。** CR 是最后一道，不是第一道。

## 门禁分层

### 第 0 层：IDE / 编辑器

类型检查、Lint 实时提示。**不要等到 commit 才发现**。

### 第 1 层：提交前（pre-commit）

```bash
python scripts/preflight.py
```

必拦：
- 硬编码密钥 → **阻断**
- 私钥文件 → **阻断**
- 调试残留（`console.log` / `print` / `debugger`）→ 警告
- 大文件 >1MB → 警告
- `.gitignore` 缺关键项 → 警告

装成 git hook：

```bash
# .git/hooks/pre-commit
#!/bin/sh
python scripts/preflight.py --staged || exit 1
```

或更规范地用 pre-commit 框架：

```yaml
# .pre-commit-config.yaml
repos:
  - repo: local
    hooks:
      - id: lunyu-preflight
        name: lunyu preflight
        entry: python scripts/preflight.py
        language: system
        pass_filenames: false
      - id: ruff
        name: ruff
        entry: ruff check --fix
        language: system
        types: [python]
      - id: secrets
        name: detect-secrets
        entry: detect-secrets-hook
        language: system
```

### 第 2 层：CI（push / PR 触发）

必须全绿才能合：

```yaml
# .github/workflows/ci.yml
name: CI
on: [push, pull_request]

jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Setup
        run: |
          pip install -r requirements-dev.txt

      - name: Preflight
        run: python scripts/preflight.py

      - name: Lint
        run: ruff check .

      - name: Type
        run: mypy .

      - name: Test
        run: pytest -q --cov=. --cov-report=term --cov-fail-under=70

      - name: 依赖漏洞
        run: pip-audit || true    # 先观察, 稳定后改为强制

      - name: 缺陷门禁
        run: python scripts/bug_ledger.py stats
```

**`bug_ledger.py stats` 的退出码直接当门禁用**：有未关闭 P0/P1 返回 1，CI 自动红。

### 第 3 层：CR

按 `review-checklist.md` 七维度。**CR 只做人能判断的事**——风格、命名、架构合理性、业务逻辑正确性。格式和语法已经由前面三层管了，CR 里再提是浪费。

### 第 4 层：发布前

- 无未关闭 P0/P1
- 回滚预案验证过（不是写了就算）
- 数据库变更可回滚
- 监控告警已配

## 覆盖率怎么定

| 场景 | 门槛 | 说明 |
|---|---|---|
| 新项目 | 70% | 起步线 |
| 存量项目 | 不低于现状 | 不加严，但不能跌 |
| 核心业务逻辑 | 90%+ | 支付、权限、计费等 |
| UI / 胶水代码 | 不设 | 数字游戏 |

**覆盖率是下限不是目标。** 100% 覆盖率的 `assert True` 毫无价值。看的是**关键分支是否被覆盖**，不是百分比。

`--cov-fail-under` 只是防倒退的闸，别拿它当质量指标宣传。

## 依赖安全

```bash
pip-audit                    # Python
npm audit --audit-level=high # Node
cargo audit                  # Rust
govulncheck ./...            # Go
```

策略：
- **高危 CVE** → 必修，阻断发布
- 中危 → 排期修
- 低危 → 记录观察

`requirements.txt` / `package-lock.json` 必须锁版本。`:latest` 或 `^` 大范围版本在生产项目里等于埋雷。

## 事故复盘（P0 必做）

### 核心原则：无责（blameless）

**追流程漏洞，不追人。** 原因很简单：如果复盘会追责，下一个人会隐瞒事故，你会在更晚的时候以更大代价知道它。

人犯错是常态，**流程的作用是让错误不致命**。所以复盘问的是：为什么这个错误没被拦住？哪一层门禁失效了？

### 事故报告模板

```markdown
## 【交付物：事故报告】

**事件编号**：INC-2026-001
**等级**：P0
**影响时长**：42 分钟（14:03 - 14:45）
**发现方式**：用户投诉（❌ 告警未触发）

### 时间线
| 时间 | 事件 |
|---|---|
| 14:03 | 发布 v1.3.0 |
| 14:07 | 支付成功率从 99.8% 掉到 62% |
| 14:31 | 首个用户投诉，客服转技术 |
| 14:38 | 定位到新加的限流配置错误 |
| 14:45 | 回滚完成，指标恢复 |

### 影响面
- 受影响订单：约 1,240 笔
- 直接损失：约 ¥8.6 万
- 用户端：支付页报错，无数据损坏

### 根因分析（五个为什么）
1. 为什么支付失败？→ 限流阈值配成了 10 QPS，正常峰值 800
2. 为什么配错？→ 拷贝了测试环境配置，未改
3. 为什么没发现？→ 配置项没有校验上下界
4. 为什么 CI 没拦？→ 配置校验不在测试范围内
5. 为什么没有告警？→ 支付成功率指标没配告警

**根本原因**：配置变更缺少校验 + 核心指标无告警。不是「某人手滑」。

### 改进项
| # | 改进 | 类型 | 负责 | 截止 | 状态 |
|---|---|---|---|---|---|
| 1 | 限流配置加上下界校验，越界启动失败 | 代码 | @后端 | 10-28 | 待做 |
| 2 | 支付成功率配告警（<95% 持续 2 分钟） | 监控 | @运维 | 10-27 | 待做 |
| 3 | 配置项纳入单测覆盖 | 测试 | @后端 | 10-30 | 待做 |
| 4 | 发布清单加「配置对比」一项 | 流程 | @TL | 10-27 | 待做 |

### 关键指标
- **MTTD**（发现耗时）：28 分钟 —— 因依赖用户投诉，**不合格**
- **MTTR**（恢复耗时）：42 分钟

### 做对了什么
回滚决策快（14:38 定位后 7 分钟完成回滚），没有试图「先修再看」。
```

### 报告里必须避免的三件事

| 避免 | 原因 |
|---|---|
| 「XX 同学操作失误」 | 追人不追流程，下次没人报 |
| 「加强培训」 | 不是可执行改进项 |
| 没有负责人和截止日期的改进项 | 等于没改进 |

### MTTD / MTTR

- **MTTD**（Mean Time To Detect）：从故障发生到被发现
- **MTTR**（Mean Time To Recover）：从被发现到恢复

**这两个数字比故障次数更能反映工程成熟度。**

MTTD 长说明**监控缺失**——靠用户投诉发现是最差的情况。
MTTR 长说明**回滚机制不可靠**或**定位手段不足**。

改进优先级：先降 MTTD（加告警），再降 MTTR（练回滚）。

## 门禁反模式

| 反模式 | 后果 |
|---|---|
| 只有 CR，没有自动化检查 | 人看格式和密钥，浪费且不可靠 |
| CI 红了先合再说 | 门禁形同虚设 |
| 覆盖率强制 100% | 逼人写无意义的测试 |
| 密钥扫描漏掉历史提交 | 密钥已在 git history 里，删当前文件没用，必须**吊销** |
| 复盘报告没人跟进改进项 | 同样的事故重复发生 |
| 告警只看 CPU/内存 | 业务指标（成功率/延迟）才是用户感知的 |
| 回滚预案写了没演练 | 真出事时发现命令跑不通 |

**密钥泄露的正确处置**：发现即**吊销/轮换**，删文件、改历史都是次要的。只要密钥进过 git history，就当它已泄露。
