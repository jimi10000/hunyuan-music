#!/usr/bin/env python3
"""缺陷工单台账 - 持久化缺陷管理

存储位置: <项目根>/.lunyu/bugs.json
单号格式: BUG-0001 递增

用法:
  add      新建缺陷工单
  list     列出缺陷(可筛选)
  show     查看单个缺陷详情
  update   修改状态/优先级/根因
  stats    统计概览(含 P0/P1 门禁校验)
  export   导出 markdown 表格
"""
import argparse
import json
import os
import sys
from datetime import datetime

PRIORITIES = ("P0", "P1", "P2", "P3")
STATUSES = ("新建", "修复中", "自测通过", "CR验证", "关闭", "重开")
ROOT_CAUSES = ("需求问题", "设计问题", "编码疏漏", "测试漏测", "未定")


def find_root(start=None):
    """向上查找项目根: 优先含 .git 的目录, 否则当前目录"""
    cur = os.path.abspath(start or os.getcwd())
    probe = cur
    while True:
        if os.path.isdir(os.path.join(probe, ".git")):
            return probe
        parent = os.path.dirname(probe)
        if parent == probe:
            return cur
        probe = parent


def ledger_path(root=None):
    root = root or find_root()
    d = os.path.join(root, ".lunyu")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "bugs.json")


def load(path):
    if not os.path.exists(path):
        return {"next_id": 1, "bugs": []}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def cmd_add(args):
    path = ledger_path(args.root)
    data = load(path)
    bug_id = "BUG-%04d" % data["next_id"]
    bug = {
        "id": bug_id,
        "title": args.title,
        "priority": args.priority,
        "status": args.status,
        "repro": args.repro or "",
        "expected": args.expected or "",
        "related_pr": args.related_pr or "",
        "related_version": args.related_version or "",
        "root_cause": "未定",
        "created_at": now(),
        "updated_at": now(),
        "history": [{"at": now(), "status": args.status, "note": "创建"}],
    }
    data["bugs"].append(bug)
    data["next_id"] += 1
    save(path, data)
    print(f"已登记 {bug_id} [{bug['priority']}] {bug['title']}")
    print(f"台账: {path}")
    return 0


def cmd_list(args):
    path = ledger_path(args.root)
    data = load(path)
    bugs = data["bugs"]
    if args.priority:
        bugs = [b for b in bugs if b["priority"] == args.priority]
    if args.status:
        bugs = [b for b in bugs if b["status"] == args.status]
    if args.open_only:
        bugs = [b for b in bugs if b["status"] not in ("关闭",)]
    if not bugs:
        print("无匹配缺陷")
        return
    print(f"{'单号':<10}{'优先级':<8}{'状态':<10}{'标题'}")
    print("-" * 70)
    for b in bugs:
        print(f"{b['id']:<10}{b['priority']:<8}{b['status']:<10}{b['title']}")
    print(f"\n共 {len(bugs)} 条")


def cmd_show(args):
    path = ledger_path(args.root)
    data = load(path)
    for b in data["bugs"]:
        if b["id"].upper() == args.id.upper():
            print(json.dumps(b, ensure_ascii=False, indent=2))
            return
    print(f"未找到 {args.id}")
    sys.exit(1)


def cmd_update(args):
    path = ledger_path(args.root)
    data = load(path)
    for b in data["bugs"]:
        if b["id"].upper() == args.id.upper():
            if args.status:
                if args.status not in STATUSES:
                    print(f"非法状态, 可选: {', '.join(STATUSES)}")
                    sys.exit(1)
                b["status"] = args.status
            if args.priority:
                if args.priority not in PRIORITIES:
                    print(f"非法优先级, 可选: {', '.join(PRIORITIES)}")
                    sys.exit(1)
                b["priority"] = args.priority
            if args.root_cause:
                b["root_cause"] = args.root_cause
            if args.related_version:
                b["related_version"] = args.related_version
            if args.related_pr:
                b["related_pr"] = args.related_pr
            if args.note is not None:
                b.setdefault("history", []).append(
                    {"at": now(), "status": b["status"], "note": args.note}
                )
            elif args.status:
                b.setdefault("history", []).append(
                    {"at": now(), "status": b["status"], "note": ""}
                )
            b["updated_at"] = now()
            save(path, data)
            print(f"{b['id']} 已更新 -> [{b['priority']}] {b['status']} 根因:{b['root_cause']}")
            return
    print(f"未找到 {args.id}")
    sys.exit(1)


def cmd_stats(args):
    path = ledger_path(args.root)
    data = load(path)
    bugs = data["bugs"]
    if not bugs:
        print("台账为空")
        return 0
    print(f"缺陷总数: {len(bugs)}")
    by_pri = {}
    for b in bugs:
        by_pri.setdefault(b["priority"], []).append(b)
    for p in PRIORITIES:
        items = by_pri.get(p, [])
        closed = sum(1 for b in items if b["status"] == "关闭")
        print(f"  {p}: 共 {len(items):<3} 已关闭 {closed:<3} 未关闭 {len(items) - closed}")

    open_blockers = [b for b in bugs if b["priority"] in ("P0", "P1") and b["status"] != "关闭"]
    print()
    if open_blockers:
        print("【门禁校验: 未通过】存在未关闭 P0/P1 缺陷, 不建议合入 main:")
        for b in open_blockers:
            print(f"  {b['id']} [{b['priority']}] {b['status']} - {b['title']}")
        return 1
    print("【门禁校验: 通过】无未关闭 P0/P1 缺陷")
    return 0


def cmd_export(args):
    path = ledger_path(args.root)
    data = load(path)
    bugs = data["bugs"]
    if not bugs:
        print("台账为空")
        return
    lines = [
        "| 单号 | 标题 | 优先级 | 状态 | 关联PR/版本 | 根因 |",
        "|---|---|---|---|---|---|",
    ]
    for b in bugs:
        rel = " / ".join(x for x in (b.get("related_pr"), b.get("related_version")) if x) or "-"
        lines.append(
            f"| {b['id']} | {b['title']} | {b['priority']} | {b['status']} | {rel} | {b['root_cause']} |"
        )
    out = "\n".join(lines)
    print(out)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(out + "\n")
        print(f"\n已写入 {args.out}")


def main():
    p = argparse.ArgumentParser(description="缺陷工单台账")
    p.add_argument("--root", help="项目根目录(默认自动查找 .git)")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="新建缺陷")
    a.add_argument("--title", required=True)
    a.add_argument("--priority", default="P2", choices=PRIORITIES)
    a.add_argument("--status", default="新建", choices=STATUSES)
    a.add_argument("--repro", help="复现步骤")
    a.add_argument("--expected", help="期望行为")
    a.add_argument("--related-pr")
    a.add_argument("--related-version")
    a.set_defaults(func=cmd_add)

    l = sub.add_parser("list", help="列出缺陷")
    l.add_argument("--priority", choices=PRIORITIES)
    l.add_argument("--status", choices=STATUSES)
    l.add_argument("--open-only", action="store_true")
    l.set_defaults(func=cmd_list)

    s = sub.add_parser("show", help="查看详情")
    s.add_argument("id")
    s.set_defaults(func=cmd_show)

    u = sub.add_parser("update", help="更新缺陷")
    u.add_argument("id")
    u.add_argument("--status", choices=STATUSES)
    u.add_argument("--priority", choices=PRIORITIES)
    u.add_argument("--root-cause", choices=ROOT_CAUSES)
    u.add_argument("--related-version", help="关联版本, 如 v1.2.0")
    u.add_argument("--related-pr", help="关联PR")
    u.add_argument("--note")
    u.set_defaults(func=cmd_update)

    st = sub.add_parser("stats", help="统计+门禁校验")
    st.set_defaults(func=cmd_stats)

    e = sub.add_parser("export", help="导出 markdown 表格")
    e.add_argument("--out")
    e.set_defaults(func=cmd_export)

    args = p.parse_args()
    rc = args.func(args)
    sys.exit(rc or 0)


if __name__ == "__main__":
    main()
