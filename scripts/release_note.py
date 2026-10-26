#!/usr/bin/env python3
"""版本存档 + CHANGELOG + Tag 生成

存储位置: <项目根>/.lunyu/releases.json, CHANGELOG.md

用法:
  tag      生成版本存档记录、CHANGELOG 片段、tag/合并/回滚命令
  list     查看历史版本存档
  show     查看指定版本详情
  rollback 生成指定版本的回滚指引
"""
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime

SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def find_root(start=None):
    cur = os.path.abspath(start or os.getcwd())
    probe = cur
    while True:
        if os.path.isdir(os.path.join(probe, ".git")):
            return probe
        parent = os.path.dirname(probe)
        if parent == probe:
            return cur
        probe = parent


def store(root=None):
    root = root or find_root()
    d = os.path.join(root, ".lunyu")
    os.makedirs(d, exist_ok=True)
    return root, os.path.join(d, "releases.json")


def load(path):
    if not os.path.exists(path):
        return {"releases": []}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def run_git(args, root):
    try:
        r = subprocess.run(
            ["git"] + args, cwd=root, capture_output=True, text=True, timeout=15
        )
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return 1, "", "git 不可用"


def check_dirty(root):
    """检查工作区是否有未提交改动, 忽略 .lunyu/ (台账本身常未提交)"""
    rc, out, _ = run_git(["status", "--porcelain"], root)
    if rc != 0:
        return None
    lines = [l for l in out.splitlines() if ".lunyu/" not in l]
    return bool(lines)


def bump(version, part):
    m = SEMVER.match(version)
    if not m:
        return None
    major, minor, patch = map(int, m.groups())
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def collect_closed_bugs(root, version):
    """从缺陷台账取本次关闭的缺陷"""
    p = os.path.join(root, ".lunyu", "bugs.json")
    if not os.path.exists(p):
        return []
    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)
    out = []
    for b in data.get("bugs", []):
        if b.get("status") != "关闭":
            continue
        rv = b.get("related_version") or ""
        if version and version in rv:
            out.append(b)
    return out


def open_blockers(root):
    p = os.path.join(root, ".lunyu", "bugs.json")
    if not os.path.exists(p):
        return []
    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)
    return [
        b
        for b in data.get("bugs", [])
        if b.get("priority") in ("P0", "P1") and b.get("status") != "关闭"
    ]


def cmd_tag(args):
    root, spath = store(args.root)
    data = load(spath)

    version = args.version
    if version in ("major", "minor", "patch"):
        if not data["releases"]:
            print("无历史版本, 请显式指定版本号, 如 --version 0.1.0")
            sys.exit(1)
        version = bump(data["releases"][-1]["version"], version)
    if not SEMVER.match(version):
        print(f"版本号不合规: {version} (需 MAJOR.MINOR.PATCH)")
        sys.exit(1)

    if any(r["version"] == version for r in data["releases"]):
        print(f"版本 {version} 已存在存档, 拒绝重复打版")
        sys.exit(1)

    tag = f"v{version}"

    # 分支基线
    rc_br, branch, _ = run_git(["rev-parse", "--abbrev-ref", "HEAD"], root)
    branch = branch if rc_br == 0 else args.branch
    rc_sha, sha, _ = run_git(["rev-parse", "--short", "HEAD"], root)
    sha = sha if rc_sha == 0 else "unknown"

    dirty = check_dirty(root)
    if dirty is True:
        print("【警告】工作区有未提交改动, 打 tag 前请先提交, 否则快照不完整")
    elif dirty is None:
        print("【提示】非 git 仓库或 git 不可用, 存档记录将缺少 commit 信息")

    bugs = collect_closed_bugs(root, version)
    blockers = open_blockers(root)

    rec = {
        "version": version,
        "tag": tag,
        "branch": args.branch or branch,
        "baseline": args.baseline or "",
        "commit": sha,
        "summary": args.summary or "",
        "closed_bugs": [b["id"] for b in bugs],
        "open_blockers": [b["id"] for b in blockers],
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "rolled_back": False,
    }
    data["releases"].append(rec)
    save(spath, data)

    # CHANGELOG
    date = datetime.now().strftime("%Y-%m-%d")
    lines = [f"## [{version}] - {date}", ""]
    if args.summary:
        lines += [args.summary, ""]
    if args.changes:
        lines.append("### 变更")
        for c in args.changes:
            lines.append(f"- {c}")
        lines.append("")
    if bugs:
        lines.append("### 修复缺陷")
        for b in bugs:
            lines.append(f"- {b['id']} [{b['priority']}] {b['title']}")
        lines.append("")

    changelog = os.path.join(root, "CHANGELOG.md")
    header = "# CHANGELOG\n\n格式遵循 [Keep a Changelog](https://keepachangelog.com/), 版本号遵循 [SemVer](https://semver.org/)。\n\n"
    body = "\n".join(lines)
    if os.path.exists(changelog):
        with open(changelog, "r", encoding="utf-8") as f:
            old = f.read()
        # 去掉旧头部, 把新条目插到最前
        old_body = old
        if old.startswith("# CHANGELOG"):
            idx = old.find("## [")
            old_body = old[idx:] if idx != -1 else ""
        with open(changelog, "w", encoding="utf-8") as f:
            f.write(header + body + "\n" + old_body)
    else:
        with open(changelog, "w", encoding="utf-8") as f:
            f.write(header + body)

    # 输出
    print("=" * 60)
    print(f"版本存档: {version}    Tag: {tag}")
    print("=" * 60)
    print(f"基线分支 : {rec['branch']}" + (f" (基于 {rec['baseline']})" if rec["baseline"] else ""))
    print(f"Commit   : {sha}")
    print(f"变更摘要 : {rec['summary'] or '-'}")
    print(f"关闭缺陷 : {', '.join(rec['closed_bugs']) or '无'}")
    if blockers:
        print(f"【高风险】未关闭 P0/P1: {', '.join(rec['open_blockers'])} -> 不建议合入 main, 需放行说明")
    print()
    print("--- 合并命令 ---")
    if rec["branch"] != "main":
        print(f"git checkout main\ngit merge --no-ff {rec['branch']}")
    else:
        print("# 已在 main, 无需合并")
    print()
    print("--- Tag 命令 ---")
    print(f'git tag -a {tag} -m "release {version}: {rec["summary"] or "版本发布"}"')
    print(f"git push origin main --tags")
    print()
    print("--- 回滚预案 ---")
    print(f"# 方案A 撤销 tag(仅本地): git tag -d {tag} && git push origin :refs/tags/{tag}")
    print(f"# 方案B 回退到上一版本: git revert <本次合并commit> 或 git reset --hard <上一版本commit>")
    print(f"# 校验: git log --oneline -5 && git describe --tags")
    print(f"# 影响范围: {rec['branch']} 分支, 本次关闭缺陷 {', '.join(rec['closed_bugs']) or '无'} 将回退")
    print()
    print(f"CHANGELOG 已更新: {changelog}")
    print(f"存档记录: {spath}")
    return 0 if not blockers else 2


def cmd_list(args):
    root, spath = store(args.root)
    data = load(spath)
    if not data["releases"]:
        print("暂无版本存档")
        return
    print(f"{'版本':<10}{'Tag':<10}{'分支':<12}{'日期':<22}{'状态'}")
    print("-" * 70)
    for r in data["releases"]:
        state = "已回滚" if r.get("rolled_back") else "生效"
        print(f"{r['version']:<10}{r['tag']:<10}{r['branch']:<12}{r['created_at']:<22}{state}")


def cmd_show(args):
    root, spath = store(args.root)
    data = load(spath)
    for r in data["releases"]:
        if r["version"] == args.version.lstrip("v"):
            print(json.dumps(r, ensure_ascii=False, indent=2))
            return
    print(f"未找到版本 {args.version}")
    sys.exit(1)


def cmd_rollback(args):
    root, spath = store(args.root)
    data = load(spath)
    target = args.version.lstrip("v")
    idx = None
    for i, r in enumerate(data["releases"]):
        if r["version"] == target:
            idx = i
            break
    if idx is None:
        print(f"未找到版本 {target}")
        sys.exit(1)
    cur = data["releases"][idx]
    prev = data["releases"][idx - 1] if idx > 0 else None

    print(f"回滚指引: {cur['version']} -> {prev['version'] if prev else '无上一版本'}")
    print("-" * 60)
    print(f"目标版本 : {cur['version']} (tag {cur['tag']}, commit {cur['commit']})")
    print(f"回退到   : {prev['version'] + ' (tag ' + prev['tag'] + ')' if prev else '无'}")
    print()
    print("--- 命令 ---")
    if prev:
        print(f"git checkout {cur['branch']}")
        print(f"git revert -m 1 <合并commit>   # 推荐, 保留历史")
        print(f"# 或强制回退: git reset --hard {prev['tag']}")
        print(f"git tag -a v{cur['version']}-rollback -m \"rollback to {prev['version']}\"")
        print(f"git push origin {cur['branch']} --tags")
    else:
        print("# 无上一版本, 只能撤销本次发布")
        print(f"git tag -d {cur['tag']}")
        print(f"git push origin :refs/tags/{cur['tag']}")
    print()
    print("--- 影响范围 ---")
    print(f"分支: {cur['branch']}")
    print(f"本次关闭缺陷将回退: {', '.join(cur.get('closed_bugs', [])) or '无'}")
    print(f"变更摘要: {cur.get('summary') or '-'}")
    print()
    print("--- 校验 ---")
    print("git log --oneline -5")
    print("git describe --tags")
    print("重新跑单元测试确认功能正常")

    if args.confirm:
        cur["rolled_back"] = True
        save(spath, data)
        print(f"\n已标记 {cur['version']} 为已回滚")


def main():
    p = argparse.ArgumentParser(description="版本存档与 Tag 管理")
    p.add_argument("--root", help="项目根目录(默认自动查找 .git)")
    sub = p.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("tag", help="打版本存档")
    t.add_argument("--version", required=True, help="版本号 或 major/minor/patch")
    t.add_argument("--branch", help="目标分支(默认当前分支)")
    t.add_argument("--baseline", help="基线分支, 如 dev")
    t.add_argument("--summary", help="变更摘要")
    t.add_argument("--changes", nargs="*", help="变更条目")
    t.set_defaults(func=cmd_tag)

    l = sub.add_parser("list", help="版本列表")
    l.set_defaults(func=cmd_list)

    s = sub.add_parser("show", help="版本详情")
    s.add_argument("version")
    s.set_defaults(func=cmd_show)

    r = sub.add_parser("rollback", help="回滚指引")
    r.add_argument("version")
    r.add_argument("--confirm", action="store_true", help="标记为已回滚")
    r.set_defaults(func=cmd_rollback)

    args = p.parse_args()
    rc = args.func(args)
    sys.exit(rc or 0)


if __name__ == "__main__":
    main()
