# Git 分支规范 · 语义化版本 · 发布回滚

## 分支模型

```
main ────●────────────●────────────●────  受保护, 只接 dev/hotfix, 打正式 tag
          \          /            /
dev ───────●──●──●──●──●──●──●──●──────   集成分支, 日常迭代合入
            \    /     \    /
feature/a    ●──●       ●──●              单人开发, 从 dev 拉出
                            \
hotfix/x ────────────────────●──●         从 main 拉出, 修完同时合 main + dev
```

| 分支 | 来源 | 合入目标 | 生命周期 | 保护 |
|---|---|---|---|---|
| `main` | — | — | 永久 | 必须 PR + CR, 禁直推 |
| `dev` | main | main | 永久 | 必须 PR, 禁 force push |
| `feature/*` | dev | dev | 功能上线后删除 | 无 |
| `fix/*` | dev | dev | 修复合入后删除 | 无 |
| `hotfix/*` | main | main **和** dev | 发布后删除 | 无 |

### 命名

```
feature/user-oauth-login
feature/JIRA-123-payment-retry
fix/null-pointer-on-empty-cart
hotfix/1.2.1-token-expire
```

用小写 + 连字符。带工单号更利于追溯。

## 常用命令

```bash
# 开新功能
git checkout dev && git pull origin dev
git checkout -b feature/user-oauth-login

# 提交
git add -A
git commit -m "feat(auth): 支持 OAuth2 第三方登录"

# 同步主干(避免最后大冲突)
git fetch origin && git rebase origin/dev

# 推送
git push -u origin feature/user-oauth-login

# 合并到 dev(推荐 --no-ff 保留分支痕迹)
git checkout dev && git pull
git merge --no-ff feature/user-oauth-login
git push origin dev

# 清理
git branch -d feature/user-oauth-login
git push origin --delete feature/user-oauth-login
```

### 线上热修

```bash
git checkout main && git pull
git checkout -b hotfix/1.2.1-token-expire
# ...修复...
git commit -m "fix(auth): 修复 token 过期后未刷新"

git checkout main && git merge --no-ff hotfix/1.2.1-token-expire
git tag -a v1.2.1 -m "hotfix: token 过期刷新"
git push origin main --tags

# 关键: 必须同步回 dev, 否则下次发版会把修复覆盖掉
git checkout dev && git merge --no-ff hotfix/1.2.1-token-expire
git push origin dev
```

## Conventional Commits

```
<type>(<scope>): <subject>

[optional body]

[optional footer]
```

| type | 用途 |
|---|---|
| `feat` | 新功能 → MINOR |
| `fix` | 缺陷修复 → PATCH |
| `docs` | 仅文档 |
| `refactor` | 重构, 不改行为 |
| `perf` | 性能优化 |
| `test` | 测试相关 |
| `chore` | 构建/依赖/杂务 |
| `style` | 格式, 不影响逻辑 |

**破坏性变更**：type 后加 `!`，footer 写 `BREAKING CHANGE:`，→ MAJOR。

```
feat(api)!: 移除 v1 用户接口

BREAKING CHANGE: /api/v1/user 已删除, 请迁移到 /api/v2/user
```

subject 用祈使句、不加句号、不超 50 字符。body 解释**为什么**改，不是改了什么（diff 已经说明了）。

## 语义化版本

`MAJOR.MINOR.PATCH`

- **MAJOR**：不兼容的 API 变更
- **MINOR**：向下兼容的新功能
- **PATCH**：向下兼容的缺陷修复

规则：
- `0.x.y` 视为开发期，API 不稳定
- 正式版 tag 带 `v` 前缀：`v1.2.0`
- 预发布：`v1.2.0-rc.1`、`v1.2.0-beta.2`
- tag 只增不改。已推送的 tag 发现打错，用新版本号覆盖，不要 force 重打

## 发布检查清单

打 tag 前逐项确认：

- [ ] 工作区干净（`git status` 无未提交改动）
- [ ] 目标分支已同步远端
- [ ] 单元测试全绿
- [ ] 无未关闭 P0/P1 缺陷（`bug_ledger.py stats` 返回 0）
- [ ] CHANGELOG 已生成
- [ ] 版本号与变更类型匹配（有 feat 就至少 MINOR）
- [ ] 回滚方案已明确

```bash
# 一条命令生成存档+CHANGELOG+tag指令
python scripts/release_note.py tag --version 1.2.0 --branch main --baseline dev \
  --summary "支持 OAuth 登录" --changes "新增第三方登录" "优化 token 刷新"
```

## 回滚

**优先 `revert`，不用 `reset --hard`**。reset 会改写历史，已推送的分支上做等于给所有人埋雷。

```bash
# 方案A: revert 合并提交(保留历史, 安全)
git checkout main
git revert -m 1 <merge-commit-sha>
git commit -m "revert: 回滚 v1.2.0"

# 方案B: 紧急情况回到上一个 tag
git reset --hard v1.1.0
git push --force-with-lease origin main   # 仅限确认无人基于此分支工作时

# 方案C: 只撤 tag
git tag -d v1.2.0
git push origin :refs/tags/v1.2.0
```

回滚后必须做的三件事：
1. 跑一遍单元测试，确认回到可用状态
2. 在 CHANGELOG 记录回滚原因
3. 缺陷台账登记导致回滚的缺陷，标 P0

## 反模式

| 禁止 | 原因 |
|---|---|
| `git push --force` 到 main/dev | 覆盖他人提交 |
| 在 main 上直接改代码 | 绕过 CR |
| 长期不合并的 feature 分支 | 冲突爆炸 |
| hotfix 只合 main 不合 dev | 下个版本修复丢失 |
| tag 打在未测试的 commit 上 | 发布即故障 |
| 一个 commit 混多个不相关改动 | 无法单独回滚 |
