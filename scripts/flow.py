#!/usr/bin/env python3
"""流程通道判定 - 按改动风险决定走几步

避免"30 行脚本也走 12 步流程"。读 git diff 统计改动规模, 输出应走的通道与必做节点。

通道:
  fast      <=2 文件 且 <=50 行, 无敏感路径   -> 5 步
  standard  中等改动                          -> 8 步
  full      大改动/多文件/敏感路径/依赖变更    -> 12 步

用法:
  python flow.py                  # 分析当前工作区改动
  python flow.py --staged         # 只看已暂存
  python flow.py --range main..HEAD
  python flow.py --json
"""
import argparse
import json
import os
import re
import subprocess
import sys

SENSITIVE = [
    (r"(^|/)(auth|login|oauth|session|token|jwt|permission|acl|rbac)", "认证授权"),
    (r"(^|/)(payment|billing|order|checkout|refund)", "支付交易"),
    (r"(^|/)(migration|migrations|schema|alembic|flyway)", "数据库迁移"),
    (r"(^|/)(Dockerfile|docker-compose|\.github/workflows|\.gitlab-ci|Jenkinsfile)", "构建部署"),
    (r"(^|/)(\.env|config|settings|secrets)", "配置密钥"),
    (r"(^|/)requirements\.txt$|(^|/)package\.json$|(^|/)go\.mod$|(^|/)Cargo\.toml$|(^|/)pom\.xml$", "依赖清单"),
    (r"(^|/)(crypto|encrypt|decrypt|hash|sign)", "加密签名"),
    (r"\.sql$", "SQL"),
]
TEST_RE = re.compile(r"(^|/)(test|tests|spec|__tests__)/|(_test\.|\.test\.|\.spec\.|Test\.java$|_test\.go$)")

FAST_FILES, FAST_LINES = 2, 50
FULL_FILES, FULL_LINES = 8, 300


def git(args, root):
    try:
        r = subprocess.run(["git"] + args, cwd=root, capture_output=True,
                           text=True, timeout=20, errors="ignore")
        return r.returncode, r.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return 1, ""


def find_root(start):
    cur = os.path.abspath(start)
    while True:
        if os.path.isdir(os.path.join(cur, ".git")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return cur
        cur = parent


def get_diff_args(args):
    if args.staged:
        return ["diff", "--cached"]
    if args.range:
        return ["diff", args.range]
    return ["diff", "HEAD"]


def parse_numstat(root, diff_args):
    """返回 {file: (added, deleted)}; 含未跟踪的新文件"""
    rc, out = git(diff_args + ["--numstat"], root)
    if rc != 0:
        return None
    files = {}
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        a, d, path = parts
        try:
            files[path] = (int(a), int(d))
        except ValueError:
            files[path] = (0, 0)  # 二进制文件

    # 未跟踪文件不会出现在 diff 里, 单独统计, 否则新功能被误判为"无改动"
    rc2, untracked = git(["ls-files", "--others", "--exclude-standard"], root)
    if rc2 == 0:
        for path in untracked.splitlines():
            if not path.strip() or path in files:
                continue
            try:
                with open(os.path.join(root, path), "r", encoding="utf-8", errors="ignore") as f:
                    n = sum(1 for _ in f)
            except OSError:
                n = 0
            files[path] = (n, 0)
    return files


def classify(files):
    total_lines = sum(a + d for a, d in files.values())
    n = len(files)

    # 只改文档 -> 降级
    doc_ext = (".md", ".txt", ".rst", ".adoc")
    if files and all(p.lower().endswith(doc_ext) for p in files):
        return "fast", "仅文档改动", total_lines, n

    # 只改测试 -> 降级
    if files and all(TEST_RE.search(p) for p in files):
        return "fast", "仅测试改动", total_lines, n

    hits = []
    for path in files:
        for pat, label in SENSITIVE:
            if re.search(pat, path, re.I):
                hits.append(f"{label}({path})")
                break
    if hits:
        return "full", "触及敏感路径: " + "; ".join(hits[:3]), total_lines, n

    if n <= FAST_FILES and total_lines <= FAST_LINES:
        return "fast", f"小改动 {n} 文件 {total_lines} 行", total_lines, n
    if n >= FULL_FILES or total_lines >= FULL_LINES:
        return "full", f"大改动 {n} 文件 {total_lines} 行", total_lines, n
    return "standard", f"中等改动 {n} 文件 {total_lines} 行", total_lines, n


STEPS = {
    "fast": {
        "name": "快速通道",
        "steps": ["Step1 需求澄清(简)", "Step5 编码", "Step6 自测", "Step8 归档(可选)", "Step11 收尾"],
        "must": ["说明改了什么", "实际跑一次验证", "commit 信息规范"],
        "skip": ["Step2 范围评审", "Step3 技术方案", "Step4 开源预研", "Step7 正式 CR", "Step9 风险清单", "Step10 复盘"],
    },
    "standard": {
        "name": "标准通道",
        "steps": ["Step1 需求澄清", "Step2 范围评审(简)", "Step3 方案(简)", "Step5 编码",
                  "Step6 自测+缺陷登记", "Step7 自评/轻量CR", "Step8 归档", "Step11 收尾"],
        "must": ["In/Out of Scope", "方案取舍一句话", "单测覆盖主流程+边界", "缺陷登记"],
        "skip": ["Step4 开源预研(无新依赖可跳)", "Step9 独立风险清单(并入交付说明)", "Step10 独立复盘(并入收尾)"],
    },
    "full": {
        "name": "完整通道",
        "steps": [f"Step{i}" for i in range(0, 12)],
        "must": ["全部交付物落盘", "三道门禁 G1/G2/G3 全过", "CI 全绿", "回滚预案", "风险清单", "复盘纪要"],
        "skip": [],
    },
}

CHECKLISTS = {
    "fast": [
        "改动意图一句话说得清",
        "本地跑过, 有真实输出",
        "无硬编码密钥",
        "commit 符合 feat/fix/docs 等前缀",
    ],
    "standard": [
        "In Scope / Out of Scope 已写",
        "方案取舍一句话说明",
        "单测覆盖主流程 + 边界",
        "无新增 P0/P1 缺陷未修",
        "PR 描述含自测情况",
    ],
    "full": [
        "需求确认单已确认 (G1)",
        "技术方案含取舍对比表",
        "开源调研含 License 核查",
        "单测全绿 + 覆盖率达标",
        "secrets / 依赖漏洞扫描通过",
        "CR 通过 (G3)",
        "无未关闭 P0/P1 (G2)",
        "回滚预案可执行",
        "风险清单已出",
        "复盘纪要含缺陷根因",
    ],
}


def main():
    ap = argparse.ArgumentParser(description="流程通道判定")
    ap.add_argument("--path", default=".")
    ap.add_argument("--staged", action="store_true")
    ap.add_argument("--range")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    root = find_root(args.path)
    files = parse_numstat(root, get_diff_args(args))

    if files is None:
        print("无法读取 git diff, 请确认在 git 仓库内")
        sys.exit(1)
    if not files:
        print("无改动 (工作区干净)")
        sys.exit(0)

    tier, reason, lines, n = classify(files)
    cfg = STEPS[tier]

    if args.json:
        print(json.dumps({
            "tier": tier, "name": cfg["name"], "reason": reason,
            "files": n, "lines": lines,
            "file_list": {k: v for k, v in sorted(files.items())},
            "steps": cfg["steps"], "must": cfg["must"], "skip": cfg["skip"],
            "checklist": CHECKLISTS[tier],
        }, ensure_ascii=False, indent=2))
        sys.exit(0)

    print("=" * 62)
    print(f"通道判定: 【{cfg['name']}】")
    print("=" * 62)
    print(f"依据    : {reason}")
    print(f"改动    : {n} 个文件, {lines} 行")
    print()
    print("改动文件:")
    for p, (a, d) in sorted(files.items(), key=lambda kv: -(kv[1][0] + kv[1][1]))[:12]:
        print(f"  +{a:<5}-{d:<5} {p}")
    if len(files) > 12:
        print(f"  ... 另有 {len(files) - 12} 个")
    print()
    print("必做节点:")
    for s in cfg["steps"]:
        print(f"  -> {s}")
    if cfg["skip"]:
        print()
        print("可跳过:")
        for s in cfg["skip"]:
            print(f"  -- {s}")
    print()
    print("验收清单:")
    for c in CHECKLISTS[tier]:
        print(f"  [ ] {c}")
    print()
    if tier == "full":
        print("提示: 敏感路径或大改动, 走完整 12 步 + 三道门禁")
    elif tier == "fast":
        print("提示: 小改动不要套完整流程, 但验证必须实际跑")
    sys.exit(0)


if __name__ == "__main__":
    main()
