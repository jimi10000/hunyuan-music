#!/usr/bin/env python3
"""提交前自检 - 质量左移门禁

在 commit 前拦掉最廉价可查的问题: 硬编码密钥、大文件、调试残留、.gitignore 缺失。
可选的 lint / type / test 执行。

用法:
  python preflight.py                 # 扫描当前项目
  python preflight.py --run           # 扫描 + 跑 lint/type/test
  python preflight.py --path <dir>    # 指定目录
  python preflight.py --json          # 机器可读输出

退出码: 0 通过 / 1 阻断(发现密钥等) / 2 仅警告
"""
import argparse
import json
import os
import re
import subprocess
import sys

SKIP_DIRS = {
    ".git", "node_modules", ".venv", "venv", "env", "__pycache__",
    "dist", "build", "target", "vendor", ".next", ".nuxt", "coverage",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".lunyu", ".idea", ".vscode",
    "Pods", ".gradle", ".terraform",
}
SKIP_EXT = {
    ".lock", ".min.js", ".min.css", ".map", ".png", ".jpg", ".jpeg", ".gif",
    ".webp", ".ico", ".svg", ".woff", ".woff2", ".ttf", ".eot", ".pdf",
    ".zip", ".tar", ".gz", ".7z", ".mp4", ".mp3", ".wav", ".so", ".dll",
    ".dylib", ".exe", ".class", ".jar", ".pyc", ".wasm", ".bin",
}
# 允许显式豁免
ALLOW_MARK = ("allowlist secret", "preflight-ignore", "noqa: secret")

SECRET_PATTERNS = [
    ("AWS Access Key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Private Key Block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP |DSA )?PRIVATE KEY-----")),
    ("GitHub Token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("Slack Token", re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}")),
    ("Google API Key", re.compile(r"\bAIza[0-9A-Za-z\-_]{35}\b")),
    ("Stripe Live Key", re.compile(r"\bsk_live_[0-9a-zA-Z]{24,}\b")),
    ("阿里云 AK", re.compile(r"\bLTAI[0-9A-Za-z]{12,}\b")),
    ("腾讯云 SecretId", re.compile(r"\bAKID[0-9A-Za-z]{13,}\b")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    ("硬编码凭证赋值", re.compile(
        r"""(?i)\b(?:api[_-]?key|apikey|secret[_-]?key|access[_-]?token|auth[_-]?token|password|passwd|pwd|private[_-]?key)\s*[:=]\s*['"]([^'"\s]{8,})['"]"""
    )),
]
# 明显是占位符的值不算
PLACEHOLDER = re.compile(
    r"(?i)^(x{4,}|\*{4,}|\.{3,}|your[_-]|changeme|placeholder|example|dummy|test|todo|<.*>|\$\{.*\}|%.*%|none|null|true|false)$"
)

DEBUG_PATTERNS = {
    ".js": [r"\bconsole\.(log|debug|dir)\(", r"\bdebugger\b"],
    ".jsx": [r"\bconsole\.(log|debug)\(", r"\bdebugger\b"],
    ".ts": [r"\bconsole\.(log|debug)\(", r"\bdebugger\b"],
    ".tsx": [r"\bconsole\.(log|debug)\(", r"\bdebugger\b"],
    ".vue": [r"\bconsole\.(log|debug)\(", r"\bdebugger\b"],
    ".py": [r"^\s*print\(", r"\bpdb\.set_trace\(", r"^\s*breakpoint\("],
    ".java": [r"System\.out\.print", r"\.printStackTrace\("],
    ".kt": [r"\bprintln\(", r"\.printStackTrace\("],
    ".go": [r"fmt\.Print", r"\blog\.Print"],
    ".rs": [r"\bdbg!\(", r"\bprintln!\("],
    ".php": [r"\bvar_dump\(", r"\bprint_r\("],
}
CODE_EXT = set(DEBUG_PATTERNS) | {".c", ".cc", ".cpp", ".h", ".hpp", ".cs", ".rb", ".swift", ".m", ".mm", ".scala", ".sh"}

LARGE_FILE_MB = 1.0

GITIGNORE_MUST = {
    ".env": "环境变量文件含密钥",
    "node_modules": "依赖目录",
    ".venv": "虚拟环境",
    "__pycache__": "Python 缓存",
    "dist": "构建产物",
    "build": "构建产物",
    "*.log": "日志文件",
}


class Report:
    def __init__(self):
        self.blockers = []
        self.warnings = []
        self.infos = []

    def block(self, msg):
        self.blockers.append(msg)

    def warn(self, msg):
        self.warnings.append(msg)

    def info(self, msg):
        self.infos.append(msg)


def walk_files(root, max_files=20000):
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            ext = os.path.splitext(fn)[1].lower()
            if ext in SKIP_EXT or fn.endswith(".min.js") or fn.endswith(".min.css"):
                continue
            p = os.path.join(dirpath, fn)
            yield p
            count += 1
            if count >= max_files:
                return


def rel(p, root):
    try:
        return os.path.relpath(p, root).replace("\\", "/")
    except ValueError:
        return p


def scan_secrets(root, rep):
    hits = 0
    for p in walk_files(root):
        ext = os.path.splitext(p)[1].lower()
        if ext not in CODE_EXT and ext not in ("", ".env", ".yaml", ".yml", ".json", ".toml", ".ini", ".conf", ".cfg", ".properties", ".xml"):
            continue
        try:
            if os.path.getsize(p) > 2 * 1024 * 1024:
                continue
            with open(p, "r", encoding="utf-8", errors="ignore") as f:
                for lineno, line in enumerate(f, 1):
                    if len(line) > 2000:
                        continue
                    low = line.lower()
                    if any(m in low for m in ALLOW_MARK):
                        continue
                    for name, pat in SECRET_PATTERNS:
                        m = pat.search(line)
                        if not m:
                            continue
                        val = m.group(1) if m.groups() else m.group(0)
                        if m.groups() and PLACEHOLDER.match(val.strip()):
                            continue
                        rep.block(f"疑似{name}: {rel(p, root)}:{lineno}")
                        hits += 1
                        break
        except (OSError, UnicodeError):
            continue
    if hits:
        rep.info("确认是误报可在该行加 `# allowlist secret` 注释")
    return hits


def scan_large_files(root, rep):
    for p in walk_files(root):
        try:
            size = os.path.getsize(p)
        except OSError:
            continue
        if size > LARGE_FILE_MB * 1024 * 1024:
            rep.warn(f"大文件 {size / 1048576:.1f}MB: {rel(p, root)}")


def scan_debug(root, rep):
    found = {}
    for p in walk_files(root):
        ext = os.path.splitext(p)[1].lower()
        pats = DEBUG_PATTERNS.get(ext)
        if not pats:
            continue
        name = rel(p, root)
        # 测试与脚本里 print 是正常的
        if any(seg in name for seg in ("test", "spec", "__main__", "scripts/", "examples/", "bench")):
            continue
        try:
            with open(p, "r", encoding="utf-8", errors="ignore") as f:
                for lineno, line in enumerate(f, 1):
                    for pat in pats:
                        if re.search(pat, line):
                            found.setdefault(name, []).append(lineno)
                            break
        except (OSError, UnicodeError):
            continue
    total = sum(len(v) for v in found.values())
    if total:
        top = sorted(found.items(), key=lambda kv: -len(kv[1]))[:5]
        detail = "; ".join(f"{k}({len(v)})" for k, v in top)
        rep.warn(f"调试残留 {total} 处: {detail}")
    return total


def scan_gitignore(root, rep):
    gi = os.path.join(root, ".gitignore")
    if not os.path.exists(gi):
        rep.warn("缺少 .gitignore, 密钥/依赖/产物可能被误提交")
        return
    with open(gi, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()
    missing = [f"{k} ({v})" for k, v in GITIGNORE_MUST.items() if k not in content]
    if missing:
        rep.warn(".gitignore 缺关键条目: " + ", ".join(missing))


def detect_project(root):
    """返回 (类型, lint命令, type命令, test命令)"""
    def has(*names):
        return any(os.path.exists(os.path.join(root, n)) for n in names)

    if has("package.json"):
        cmds = []
        pkg = os.path.join(root, "package.json")
        scripts = {}
        try:
            with open(pkg, "r", encoding="utf-8") as f:
                scripts = json.load(f).get("scripts", {})
        except (OSError, ValueError):
            pass
        lint = "npm run lint" if "lint" in scripts else None
        typ = "npm run typecheck" if "typecheck" in scripts else ("npx tsc --noEmit" if has("tsconfig.json") else None)
        test = "npm test" if "test" in scripts else None
        return "Node", lint, typ, test
    if has("pyproject.toml", "setup.py", "requirements.txt"):
        lint = "ruff check ." if has("ruff.toml", ".ruff.toml") or _pyproject_has(root, "ruff") else None
        typ = "mypy ." if _pyproject_has(root, "mypy") else None
        test = "pytest -q" if has("tests", "test") or _pyproject_has(root, "pytest") else None
        return "Python", lint, typ, test
    if has("go.mod"):
        return "Go", "gofmt -l .", "go vet ./...", "go test ./..."
    if has("Cargo.toml"):
        return "Rust", "cargo clippy -- -D warnings", None, "cargo test"
    if has("pom.xml"):
        return "Maven", None, None, "mvn -q test"
    if has("build.gradle", "build.gradle.kts"):
        return "Gradle", None, None, "./gradlew test"
    return None, None, None, None


def _pyproject_has(root, key):
    p = os.path.join(root, "pyproject.toml")
    if not os.path.exists(p):
        return False
    try:
        with open(p, "r", encoding="utf-8", errors="ignore") as f:
            return key in f.read()
    except OSError:
        return False


def run_cmd(cmd, root, timeout=180):
    try:
        r = subprocess.run(cmd, cwd=root, shell=True, capture_output=True,
                           text=True, timeout=timeout, errors="ignore")
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, f"超时(>{timeout}s)"
    except OSError as e:
        return 127, str(e)


def run_gates(root, rep, lang, lint, typ, test):
    for label, cmd in (("lint", lint), ("type", typ), ("test", test)):
        if not cmd:
            rep.info(f"{label}: 未检测到配置, 跳过")
            continue
        rc, out = run_cmd(cmd, root)
        tail = "\n".join(out.strip().splitlines()[-8:])
        if rc == 0:
            rep.info(f"{label}: 通过 ({cmd})")
        elif rc == 127:
            rep.info(f"{label}: 命令不可用, 跳过 ({cmd})")
        elif rc == 124:
            rep.warn(f"{label}: 超时 ({cmd})")
        else:
            rep.block(f"{label} 失败 (exit {rc}): {cmd}\n{tail}")


def main():
    ap = argparse.ArgumentParser(description="提交前自检")
    ap.add_argument("--path", default=".", help="项目目录")
    ap.add_argument("--run", action="store_true", help="执行 lint/type/test")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    ap.add_argument("--skip-secrets", action="store_true")
    args = ap.parse_args()

    root = os.path.abspath(args.path)
    if not os.path.isdir(root):
        print(f"目录不存在: {root}")
        sys.exit(1)

    rep = Report()
    if not args.skip_secrets:
        scan_secrets(root, rep)
    scan_large_files(root, rep)
    scan_debug(root, rep)
    scan_gitignore(root, rep)

    lang, lint, typ, test = detect_project(root)
    if lang:
        rep.info(f"识别项目类型: {lang}")
    else:
        rep.info("未识别项目类型, 仅做通用扫描")
    if args.run and lang:
        run_gates(root, rep, lang, lint, typ, test)

    code = 0
    if rep.blockers:
        code = 1
    elif rep.warnings:
        code = 2

    if args.json:
        print(json.dumps({
            "root": root, "lang": lang, "exit_code": code,
            "blockers": rep.blockers, "warnings": rep.warnings, "infos": rep.infos,
        }, ensure_ascii=False, indent=2))
        sys.exit(code)

    print("=" * 60)
    print(f"提交前自检  {root}")
    print("=" * 60)
    if rep.blockers:
        print(f"\n[阻断] {len(rep.blockers)} 项 —— 必须处理才能提交")
        for m in rep.blockers:
            print(f"  X {m}")
    if rep.warnings:
        print(f"\n[警告] {len(rep.warnings)} 项")
        for m in rep.warnings:
            print(f"  ! {m}")
    if rep.infos:
        print("\n[信息]")
        for m in rep.infos:
            print(f"  - {m}")
    print()
    if code == 0:
        print("结论: 通过, 可以提交")
    elif code == 1:
        print("结论: 阻断, 修完再提交")
    else:
        print("结论: 通过(有警告), 建议处理")
    sys.exit(code)


if __name__ == "__main__":
    main()
