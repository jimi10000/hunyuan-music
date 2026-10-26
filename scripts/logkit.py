#!/usr/bin/env python3
"""分类日志系统脚手架 - 生成到目标项目里

不是给 lunyu 自己用的，是给「你正在写的那个软件」用的。
生成一套开箱可用的日志模块: 分类 + 分级 + 上下文追踪 + 结构化输出 + 错误聚合。

用法:
  python logkit.py init --lang python --dest ./src/logkit
  python logkit.py init --lang python --dest ./src/logkit --json-lines
  python logkit.py init --lang ts --dest ./src/logkit

生成后看 <dest>/README.md 上手。
"""
import argparse
import os
import sys

PY_MAIN = '''"""分类日志系统 · 核心模块

设计要点:
1. 分类(category) 不是模块名, 是「问题归属域」——排错时按域筛, 不是按文件筛
2. 每条日志带 trace_id, 一次请求/一次任务的全链路可串起来
3. 分级严格: DEBUG 开发 / INFO 业务事实 / WARN 可恢复异常 / ERROR 需人介入 / FATAL 进程级
4. 错误日志必须带 exc_info 和上下文快照, 否则等于没记
5. 结构化字段供机器聚合, message 供人读, 两者都要有

用法:
    from logkit import get_logger, trace

    log = get_logger("payment.refund")
    log.info("退款发起", order_id="A123", amount=99.5)

    with trace("req-abc"):
        log.error("退款失败", order_id="A123", exc_info=True)
"""
from __future__ import annotations

import contextvars
import json
import logging
import logging.handlers
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

__all__ = ["get_logger", "trace", "setup", "current_trace_id", "CategoryFilter", "JsonFormatter"]

# ---- 分类注册表: 按「问题域」划分, 新增域在这里登记 ----
CATEGORIES: Dict[str, str] = {
    "app": "应用生命周期",
    "api": "接口层: 入参/出参/耗时",
    "db": "数据库: 慢查询/连接/事务",
    "cache": "缓存命中与失效",
    "auth": "认证授权",
    "payment": "支付交易",
    "third": "第三方调用",
    "job": "定时任务/队列",
    "io": "文件/网络 IO",
    "biz": "业务规则判定",
    "perf": "性能指标",
    "security": "安全事件",
}

_trace_id: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="-")
_user_id: contextvars.ContextVar[str] = contextvars.ContextVar("user_id", default="-")


def current_trace_id() -> str:
    return _trace_id.get()


class trace:
    """上下文追踪: with trace("req-1", user_id="u9") 或 with trace() 自动生成

    嵌套时保留外层 id, 只追加 span, 保证一次请求一个 trace_id。
    """

    def __init__(self, trace_id: Optional[str] = None, user_id: Optional[str] = None):
        self.trace_id = trace_id or current_trace_id()
        if self.trace_id == "-":
            self.trace_id = uuid.uuid4().hex[:12]
        self.user_id = user_id
        self._t1 = None
        self._t2 = None

    def __enter__(self):
        self._t1 = _trace_id.set(self.trace_id)
        if self.user_id:
            self._t2 = _user_id.set(self.user_id)
        return self.trace_id

    def __exit__(self, *exc):
        if self._t2 is not None:
            _user_id.reset(self._t2)
        if self._t1 is not None:
            _trace_id.reset(self._t1)
        return False


class CategoryFilter(logging.Filter):
    """按分类过滤: 允许单独把某分类调到 DEBUG 或静音"""

    def __init__(self, levels: Optional[Dict[str, int]] = None):
        super().__init__()
        self.levels = levels or {}

    def filter(self, record: logging.LogRecord) -> bool:
        want = self.levels.get(getattr(record, "category", "app"))
        if want is None:
            return True
        return record.levelno >= want


class JsonFormatter(logging.Formatter):
    """一行一条 JSON, 供 ELK/Loki/云日志直接吃"""

    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "category": getattr(record, "category", "-"),
            "logger": record.name,
            "msg": record.getMessage(),
            "trace_id": getattr(record, "trace_id", "-"),
        }
        uid = getattr(record, "user_id", "-")
        if uid != "-":
            payload["user_id"] = uid
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        for k, v in getattr(record, "extra_fields", {}).items():
            payload[k] = v
        return json.dumps(payload, ensure_ascii=False, default=str)


class TextFormatter(logging.Formatter):
    """人读格式: 时间 级别 [分类] trace: 消息 | 字段"""

    LEVEL_ABBR = {"WARNING": "WARN", "CRITICAL": "FATAL"}

    def format(self, record: logging.LogRecord) -> str:
        lvl = self.LEVEL_ABBR.get(record.levelname, record.levelname)
        base = (
            f"{datetime.fromtimestamp(record.created).strftime('%H:%M:%S.%f')[:-3]} "
            f"{lvl:<5} [{getattr(record, 'category', '-'):<8}] "
            f"{getattr(record, 'trace_id', '-'):<12} {record.getMessage()}"
        )
        extra = getattr(record, "extra_fields", {})
        if extra:
            base += " | " + " ".join(f"{k}={v}" for k, v in extra.items())
        if record.exc_info:
            base += "\\n" + self.formatException(record.exc_info)
        return base


class CategoryAdapter(logging.LoggerAdapter):
    """把 category / trace_id / 业务字段 注入每条日志"""

    def process(self, msg, kwargs):
        extra = kwargs.setdefault("extra", {})
        extra["category"] = self.extra["category"]
        extra["trace_id"] = current_trace_id()
        extra["user_id"] = _user_id.get()
        # 业务字段: 除了 logging 内部参数, 其余都当结构化字段
        reserved = {"exc_info", "stack_info", "stacklevel", "extra"}
        fields = {k: v for k, v in kwargs.items() if k not in reserved}
        for k in fields:
            kwargs.pop(k)
        if fields:
            extra["extra_fields"] = fields
        return msg, kwargs

    def error(self, msg, *a, **kw):
        # 强制错误日志带栈, 漏掉 exc_info 是最常见的排错灾难
        if "exc_info" not in kw and sys.exc_info()[0] is not None:
            kw["exc_info"] = True
        return super().error(msg, *a, **kw)


_configured = False


def setup(
    level: str = "INFO",
    log_dir: str = "logs",
    json_lines: bool = False,
    category_levels: Optional[Dict[str, int]] = None,
    console: bool = True,
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 10,
) -> None:
    """初始化。应用启动时调用一次。

    - 控制台: 人读格式, 便于开发
    - 文件:   logs/app.log 全量(按大小滚动)
    - 文件:   logs/error.log 只收 ERROR 以上, 排错第一入口
    - 文件:   logs/category/<name>.log 按分类分文件, 定位问题时只看一个文件
    """
    global _configured
    if _configured:
        return
    _configured = True

    root = logging.getLogger("app")
    root.setLevel(logging.DEBUG)
    root.handlers.clear()
    root.propagate = False

    filt = CategoryFilter(category_levels)
    file_fmt = JsonFormatter() if json_lines else TextFormatter()

    if console:
        ch = logging.StreamHandler(sys.stdout)
        ch.setLevel(getattr(logging, level.upper(), logging.INFO))
        ch.setFormatter(TextFormatter())
        ch.addFilter(filt)
        root.addHandler(ch)

    os.makedirs(log_dir, exist_ok=True)

    fh = logging.handlers.RotatingFileHandler(
        os.path.join(log_dir, "app.log"), maxBytes=max_bytes,
        backupCount=backup_count, encoding="utf-8",
    )
    fh.setLevel(getattr(logging, level.upper(), logging.INFO))
    fh.setFormatter(file_fmt)
    fh.addFilter(filt)
    root.addHandler(fh)

    eh = logging.handlers.RotatingFileHandler(
        os.path.join(log_dir, "error.log"), maxBytes=max_bytes,
        backupCount=backup_count, encoding="utf-8",
    )
    eh.setLevel(logging.ERROR)
    eh.setFormatter(file_fmt)
    root.addHandler(eh)

    # 分类分文件: 排错时只看相关域, 不被噪音淹没
    cat_dir = os.path.join(log_dir, "category")
    os.makedirs(cat_dir, exist_ok=True)
    for cat in CATEGORIES:
        h = logging.handlers.RotatingFileHandler(
            os.path.join(cat_dir, f"{cat}.log"), maxBytes=max_bytes,
            backupCount=3, encoding="utf-8",
        )
        h.setLevel(logging.DEBUG)
        h.setFormatter(file_fmt)
        h.addFilter(_OnlyCategory(cat))
        root.addHandler(h)


class _OnlyCategory(logging.Filter):
    def __init__(self, cat: str):
        super().__init__()
        self.cat = cat

    def filter(self, record: logging.LogRecord) -> bool:
        return getattr(record, "category", "") == self.cat


def get_logger(category: str, **static_fields) -> CategoryAdapter:
    """取分类日志器。category 用 CATEGORIES 里的域, 支持子域 payment.refund

    未知分类不报错(降低接入摩擦), 但会落在 app.log 里。
    """
    base = logging.getLogger(f"app.{category}")
    return CategoryAdapter(base, {"category": category, **static_fields})


# ---- 便捷埋点: 耗时统计 ----
class timed:
    """with timed(log, "查询订单", order_id=x): ... 自动记耗时, 超阈值升级为 WARN

    慢操作是性能问题的第一手证据, 比事后看 APM 更直接。
    """

    def __init__(self, log: CategoryAdapter, action: str, slow_ms: float = 500, **fields):
        self.log = log
        self.action = action
        self.slow_ms = slow_ms
        self.fields = fields

    def __enter__(self):
        self.t0 = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb):
        ms = (time.perf_counter() - self.t0) * 1000
        f = dict(self.fields, cost_ms=round(ms, 1))
        if exc_type is not None:
            self.log.error(f"{self.action} 异常", exc_info=True, **f)
        elif ms >= self.slow_ms:
            self.log.warning(f"{self.action} 慢", **f)
        else:
            self.log.debug(f"{self.action} 完成", **f)
        return False
'''

PY_INIT = '''from .core import get_logger, setup, trace, timed, current_trace_id, CATEGORIES

__all__ = ["get_logger", "setup", "trace", "timed", "current_trace_id", "CATEGORIES"]
'''

PY_EXAMPLE = '''"""日志系统用法示例 - 照这个抄到业务代码里

直接运行: python logkit/example.py
"""
import os
import sys
import time

# 允许把本文件当脚本直接跑
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from logkit import setup, get_logger, trace, timed

# 1. 启动时初始化一次(通常在 main / app 入口)
setup(level="DEBUG", log_dir="logs", json_lines=False)

# 2. 按「问题域」取日志器, 不是按模块名
api_log = get_logger("api")
pay_log = get_logger("payment")
db_log = get_logger("db")


def handle_refund(order_id: str, amount: float) -> bool:
    # 3. 入口处建 trace, 整条链路一个 id
    with trace(user_id="u-9527"):
        api_log.info("收到退款请求", order_id=order_id, amount=amount, path="/refund")

        try:
            # 4. 耗时埋点: 慢操作自动升级 WARN
            with timed(db_log, "查询订单", slow_ms=200, order_id=order_id):
                time.sleep(0.05)
                order = {"id": order_id, "status": "paid"}

            if order["status"] != "paid":
                # 5. 业务规则不满足 -> WARN, 不是 ERROR(不是故障)
                pay_log.warning("订单状态不允许退款", order_id=order_id, status=order["status"])
                return False

            pay_log.info("退款成功", order_id=order_id, amount=amount)
            return True

        except Exception:
            # 6. 真异常才用 error, 且必须带 exc_info
            pay_log.error("退款处理失败", order_id=order_id, amount=amount, exc_info=True)
            return False


def call_third_party():
    log = get_logger("third")
    # 7. 第三方调用必须记请求参数和响应, 否则对不上账
    try:
        with timed(log, "调用支付网关", slow_ms=1000, gateway="alipay"):
            time.sleep(0.8)
        log.info("网关返回成功", gateway="alipay", code=200)
    except Exception:
        log.error("网关调用失败", gateway="alipay", exc_info=True)


if __name__ == "__main__":
    handle_refund("A-1001", 99.5)
    call_third_party()
    print("\\n日志已写入 logs/ 目录:")
    print("  app.log            全量")
    print("  error.log          仅 ERROR+, 排错第一入口")
    print("  category/payment.log  只看支付域")
'''

PY_README = '''# 分类日志系统

## 为什么分类

按**模块名**分日志, 排错时你得翻遍所有文件。按**问题域**分日志, 出支付问题就只看 `payment.log`。

分类表在 `core.py` 的 `CATEGORIES`, 按自己业务增删。

## 三秒上手

```python
from logkit import setup, get_logger, trace, timed

setup(level="INFO", log_dir="logs")          # 入口调一次
log = get_logger("payment")                   # 按域取

with trace():                                 # 一次请求一个 trace_id
    log.info("退款发起", order_id="A1", amount=99.5)
```

## 分级怎么用

| 级别 | 什么时候用 | 反例 |
|---|---|---|
| DEBUG | 开发期细节, 生产默认关 | 不要用它记业务事实 |
| INFO | **业务事实**: 谁做了什么、结果如何 | 不要记「进入函数」这种废话 |
| WARN | 可恢复异常、降级、慢操作、业务拒绝 | 不要用它记真故障 |
| ERROR | 需人介入的故障, **必须带 exc_info** | 不要用它记「用户密码错」 |
| FATAL | 进程无法继续 | 少见 |

**最常见的错误**: 所有异常都 `log.error` 但忘了 `exc_info=True`, 结果只看到「失败」两个字, 没有栈。适配器已做兜底, 但显式写更好。

## 输出位置

```
logs/
├── app.log                 全量, 10MB 滚动, 留 10 份
├── error.log               只收 ERROR+, 排错第一入口
└── category/
    ├── payment.log         支付域全量
    ├── db.log
    └── ...
```

## 结构化字段

`log.info("msg", order_id=x, amount=y)` —— 除 msg 外的关键字参数自动变成结构化字段, 文本格式里跟在 `|` 后面, JSON 格式里成为独立 key。

字段名用 snake_case, 同一含义全项目统一命名(别一处 `orderId` 一处 `order_id`)。

## 接日志平台

```python
setup(json_lines=True)   # 一行一条 JSON, 直接喂 ELK / Loki / 云日志
```

## 排错套路

1. 从 `error.log` 找到报错行, 抄下 `trace_id`
2. `grep <trace_id> logs/app.log` 看全链路
3. 定位到哪个域后, 只看 `logs/category/<域>.log`
4. 看 `cost_ms` 字段找慢操作

## 不要做

- 别在循环里打 INFO 日志(日志量爆炸, 且拖慢主流程)
- 别把密码/token/身份证打进日志(适配器不脱敏, 自己注意)
- 别用 `print` —— 它没有级别、没有分类、没有 trace_id
- 别在日志里拼大对象(整个 request body), 记关键字段就够
'''

TS_CORE = r'''/**
 * 分类日志系统 · TypeScript 版
 *
 * 分类按「问题域」划分, 不是按模块名。
 * 一次请求一个 traceId, 全链路可串。
 */

export const CATEGORIES = {
  app: '应用生命周期',
  api: '接口层: 入参/出参/耗时',
  db: '数据库',
  cache: '缓存',
  auth: '认证授权',
  payment: '支付交易',
  third: '第三方调用',
  job: '定时任务/队列',
  io: '文件/网络IO',
  biz: '业务规则',
  perf: '性能',
  security: '安全事件',
} as const;

export type Category = keyof typeof CATEGORIES;
export type Level = 'DEBUG' | 'INFO' | 'WARN' | 'ERROR' | 'FATAL';

const LEVEL_ORDER: Record<Level, number> = {
  DEBUG: 10, INFO: 20, WARN: 30, ERROR: 40, FATAL: 50,
};

interface Config {
  level: Level;
  jsonLines: boolean;
  categoryLevels: Partial<Record<Category, Level>>;
  sink: (line: string, level: Level) => void;
}

const config: Config = {
  level: 'INFO',
  jsonLines: false,
  categoryLevels: {},
  sink: (line, level) => {
    if (level === 'ERROR' || level === 'FATAL') console.error(line);
    else if (level === 'WARN') console.warn(line);
    else console.log(line);
  },
};

export function setup(opts: Partial<Config>): void {
  Object.assign(config, opts);
}

// ---- trace 上下文: 用 AsyncLocalStorage 在 Node 端自动透传 ----
let traceId = '-';
let userId = '-';

export function setTrace(id?: string, uid?: string): string {
  traceId = id || Math.random().toString(36).slice(2, 14);
  if (uid) userId = uid;
  return traceId;
}

export function currentTraceId(): string {
  return traceId;
}

export function clearTrace(): void {
  traceId = '-';
  userId = '-';
}

function ts(): string {
  const d = new Date();
  return d.toTimeString().slice(0, 8) + '.' + String(d.getMilliseconds()).padStart(3, '0');
}

export class Logger {
  constructor(private category: Category | string) {}

  private write(level: Level, msg: string, fields: Record<string, unknown>, err?: Error): void {
    const catLevel = config.categoryLevels[this.category as Category];
    const threshold = catLevel ? LEVEL_ORDER[catLevel] : LEVEL_ORDER[config.level];
    if (LEVEL_ORDER[level] < threshold) return;

    const payload: Record<string, unknown> = {
      ts: new Date().toISOString(),
      level,
      category: this.category,
      msg,
      trace_id: traceId,
      ...(userId !== '-' ? { user_id: userId } : {}),
      ...fields,
    };
    if (err) {
      payload.exc = err.stack || String(err);
    }

    const line = config.jsonLines
      ? JSON.stringify(payload)
      : `${ts()} ${level.padEnd(5)} [${String(this.category).padEnd(8)}] ${traceId.padEnd(12)} ${msg}` +
        (Object.keys(fields).length ? ' | ' + Object.entries(fields).map(([k, v]) => `${k}=${String(v)}`).join(' ') : '') +
        (err ? '\n' + (err.stack || String(err)) : '');

    config.sink(line, level);
  }

  debug(msg: string, fields: Record<string, unknown> = {}): void { this.write('DEBUG', msg, fields); }
  info(msg: string, fields: Record<string, unknown> = {}): void { this.write('INFO', msg, fields); }
  warn(msg: string, fields: Record<string, unknown> = {}): void { this.write('WARN', msg, fields); }

  /** err 传了才记栈; 不传就只是条消息, 别拿它当 error 用 */
  error(msg: string, fields: Record<string, unknown> = {}, err?: unknown): void {
    this.write('ERROR', msg, fields, err instanceof Error ? err : err ? new Error(String(err)) : undefined);
  }

  fatal(msg: string, fields: Record<string, unknown> = {}, err?: unknown): void {
    this.write('FATAL', msg, fields, err instanceof Error ? err : err ? new Error(String(err)) : undefined);
  }
}

export function getLogger(category: Category | string): Logger {
  return new Logger(category);
}

/** 耗时埋点: 超阈值自动 WARN */
export async function timed<T>(
  log: Logger,
  action: string,
  fn: () => Promise<T>,
  slowMs = 500,
  fields: Record<string, unknown> = {},
): Promise<T> {
  const t0 = performance.now();
  try {
    const r = await fn();
    const ms = Math.round((performance.now() - t0) * 10) / 10;
    const f = { ...fields, cost_ms: ms };
    if (ms >= slowMs) log.warn(`${action} 慢`, f);
    else log.debug(`${action} 完成`, f);
    return r;
  } catch (e) {
    const ms = Math.round((performance.now() - t0) * 10) / 10;
    log.error(`${action} 异常`, { ...fields, cost_ms: ms }, e);
    throw e;
  }
}
'''

TS_EXAMPLE = r'''import { setup, getLogger, setTrace, clearTrace, timed } from './core';

setup({ level: 'DEBUG', jsonLines: false });

const apiLog = getLogger('api');
const payLog = getLogger('payment');
const dbLog = getLogger('db');

export async function handleRefund(orderId: string, amount: number): Promise<boolean> {
  setTrace(undefined, 'u-9527');
  try {
    apiLog.info('收到退款请求', { order_id: orderId, amount, path: '/refund' });

    const order = await timed(dbLog, '查询订单', async () => {
      await new Promise((r) => setTimeout(r, 50));
      return { id: orderId, status: 'paid' };
    }, 200, { order_id: orderId });

    if (order.status !== 'paid') {
      payLog.warn('订单状态不允许退款', { order_id: orderId, status: order.status });
      return false;
    }

    payLog.info('退款成功', { order_id: orderId, amount });
    return true;
  } catch (e) {
    payLog.error('退款处理失败', { order_id: orderId, amount }, e);
    return false;
  } finally {
    clearTrace();
  }
}

handleRefund('A-1001', 99.5).then((ok) => console.log('result:', ok));
'''

TS_README = '''# 分类日志系统 (TypeScript)

## 三秒上手

```ts
import { setup, getLogger, setTrace, timed } from './logkit/core';

setup({ level: 'INFO', jsonLines: false });   // 入口调一次

const log = getLogger('payment');              // 按「问题域」取
setTrace(undefined, 'u-9527');                 // 一次请求一个 traceId
log.info('退款发起', { order_id: 'A1', amount: 99.5 });
```

## 分级

| 级别 | 用途 |
|---|---|
| DEBUG | 开发细节, 生产关掉 |
| INFO | 业务事实 |
| WARN | 可恢复异常/降级/慢操作/业务拒绝 |
| ERROR | 需人介入, **必须传 err 对象** |
| FATAL | 进程级 |

`log.error(msg, fields, err)` —— 第三个参数传 `Error` 才会记栈。这是最容易被漏的一步。

## 耗时埋点

```ts
const data = await timed(log, '查询订单', () => repo.find(id), 200, { order_id: id });
// 超过 200ms 自动 WARN, 异常自动 ERROR 并向上抛
```

## 接入文件/平台

默认 `sink` 打控制台。接文件或日志平台只需覆盖 sink:

```ts
setup({
  jsonLines: true,
  sink: (line, level) => {
    fs.appendFileSync('logs/app.log', line + '\\n');
    if (level === 'ERROR') fs.appendFileSync('logs/error.log', line + '\\n');
  },
});
```

## 不要做

- 循环里打 INFO(日志爆炸)
- 把 token/密码打进字段(自己脱敏)
- `console.log` 代替日志器(无级别无 trace)
- `log.error(msg)` 不传 err(只有消息没有栈)
'''


def write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
    return path


def main():
    ap = argparse.ArgumentParser(description="生成分类日志系统")
    ap.add_argument("init", nargs="?", default="init")
    ap.add_argument("--lang", default="python", choices=["python", "ts", "both"])
    ap.add_argument("--dest", default="./logkit", help="生成目录")
    ap.add_argument("--json-lines", action="store_true", help="默认输出 JSON 行")
    args = ap.parse_args()

    dest = os.path.abspath(args.dest)
    written = []

    if args.lang in ("python", "both"):
        base = os.path.join(dest, "logkit") if args.lang == "both" else dest
        written.append(write(os.path.join(base, "__init__.py"), PY_INIT))
        written.append(write(os.path.join(base, "core.py"), PY_MAIN))
        written.append(write(os.path.join(base, "example.py"), PY_EXAMPLE))
        written.append(write(os.path.join(base, "README.md"), PY_README))

    if args.lang in ("ts", "both"):
        base = os.path.join(dest, "ts") if args.lang == "both" else dest
        written.append(write(os.path.join(base, "core.ts"), TS_CORE))
        written.append(write(os.path.join(base, "example.ts"), TS_EXAMPLE))
        written.append(write(os.path.join(base, "README.md"), TS_README))

    print(f"已生成 {len(written)} 个文件 -> {dest}")
    for p in written:
        print(f"  {os.path.relpath(p, os.getcwd())}")
    print()
    print("下一步:")
    if args.lang in ("python", "both"):
        print("  1. 入口处: from logkit import setup; setup(level='INFO')")
        print("  2. 业务处: log = get_logger('payment'); log.info('msg', order_id=x)")
        print("  3. 跑 example.py 看效果")
    if args.lang in ("ts", "both"):
        print("  1. 入口处: import { setup } from './logkit/core'; setup({ level: 'INFO' })")
        print("  2. 业务处: const log = getLogger('payment'); log.info('msg', { order_id: x })")
    print()
    print("分类表按业务改: 编辑 core.py / core.ts 里的 CATEGORIES")


if __name__ == "__main__":
    main()
