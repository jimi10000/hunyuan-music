# 分类日志与可观测性规范

## 为什么必须按「问题域」分类

按模块名分日志的后果：线上支付失败，你得翻 api.log、service.log、handler.log、repository.log 四个文件才能拼出一次调用的全貌。

按问题域分：直接看 `payment.log`，一次调用所有相关记录都在里面。

**分类维度和代码结构解耦**。一个 `payment` 分类会横跨 controller、service、repository 三层——这是刻意的，因为排错时你关心的是「支付出了什么事」，不是「哪一层出的」。

## 分类表怎么定

生成的 `CATEGORIES` 是通用起点，必须按自己业务改。判断标准：

- **排错时会单独关注的域**，就给它一个分类
- 分类总数控制在 **8-15 个**。超过 20 个说明划分过细，排错时又要翻多个文件
- 用 `父域.子域` 表达细分：`payment.refund`、`payment.callback`，都落到 `payment.log`

典型业务分类：

| 业务 | 建议分类 |
|---|---|
| 电商 | api / order / payment / inventory / user / db / third / job |
| SaaS | api / auth / tenant / billing / quota / db / third |
| 车载/嵌入式 | app / media / audio / sensor / power / io / security |
| 数据管道 | ingest / transform / sink / schedule / db / third |

## 分级：最容易搞错的地方

| 级别 | 判断标准 | 例子 |
|---|---|---|
| **DEBUG** | 只有开发期需要 | 请求体全文、SQL 语句、中间变量 |
| **INFO** | **业务事实**，事后要能回答「发生了什么」 | 订单创建成功、用户登录、任务开始/结束 |
| **WARN** | 异常但**系统能自己消化** | 重试成功、降级到缓存、慢查询、业务规则拒绝、限流触发 |
| **ERROR** | **需要人介入**，系统无法自愈 | 数据库连不上、第三方持续失败、数据不一致 |
| **FATAL** | 进程无法继续 | 配置缺失、端口占用 |

### 三条铁律

**1. 用户输错密码是 WARN，不是 ERROR。**
这是最高频的误用。ERROR 日志一旦混入大量非故障，就没人看了，真正的事故会淹在里面。

判断口诀：**这条日志会不会把人半夜叫起来？** 会 → ERROR。不会 → WARN 或 INFO。

**2. INFO 记结果，不记过程。**
- ❌ `log.info("进入 handleRefund")` / `log.info("开始查询订单")` —— 这是噪音
- ✅ `log.info("退款成功", order_id=x, amount=y)` —— 这是事实

**3. DEBUG 生产必须关。**
`setup(level="INFO")`。需要临时排查时用环境变量或配置热调，不要为了省事一直开 DEBUG——日志量能拖垮磁盘和 CPU。

## 错误日志三要素

缺任何一条，这条日志在排错时都无价值：

```
1. 异常栈        Python: exc_info=True    TS: 传 err 对象
2. 上下文快照    关键入参、业务ID、状态值
3. trace_id      能反查整条链路
```

反例（等于没记）：

```python
except Exception:
    log.error("退款失败")          # 没有栈、没有订单号、不知道是谁
```

正例：

```python
except Exception:
    log.error("退款失败", order_id=order_id, amount=amount, gateway="alipay", exc_info=True)
```

生成的 `CategoryAdapter.error()` 做了兜底——在 `except` 块里调用时若没传 `exc_info`，会自动带上当前栈。但**显式写更好**，因为兜底只在 `except` 块内生效。

## trace_id：串起全链路

一次用户请求会穿过 N 个模块、可能跨服务。靠什么把散落的日志串成一条线？trace_id。

```python
with trace(user_id="u-9527"):        # 入口处建
    handle(...)                       # 内部所有日志自动带上这个 id
```

嵌套时**保留外层 id**，只追加信息。这样一次请求只有一个 trace_id，不会分裂。

跨服务传递：HTTP 头 `X-Trace-Id`、消息队列的消息属性。收到时读出来 `trace()` 进去，不要重新生成。

排错套路：

```bash
grep "d2c493c42212" logs/app.log          # 全链路时间线
grep "d2c493c42212" logs/category/*.log   # 看跨了哪些域
```

## 结构化字段

```python
log.info("退款成功", order_id="A1", amount=99.5, gateway="alipay")
```

除 msg 外的关键字参数自动变成结构化字段：
- 文本格式：`... 退款成功 | order_id=A1 amount=99.5 gateway=alipay`
- JSON 格式：独立 key，可被 ELK/Loki 直接索引查询

**命名必须全项目统一**：`order_id` 就一直是 `order_id`，不要一处 `orderId` 一处 `oid`。否则聚合查询会漏。

**字段选择原则**：只记排错时需要过滤/关联的字段。整包 request body 塞进去，日志会爆炸且含敏感信息。

## 脱敏（适配器不做，必须自己注意）

绝不能进日志的：密码、token、API key、身份证、银行卡号、手机号（可脱敏保留后四位）、完整地址、人脸/生物特征。

```python
log.info("登录", user_id=uid, phone=mask(phone))   # 不要 phone=phone
```

宁可少记，不可误记。日志文件往往权限比数据库松，泄露面更大。

## 耗时埋点

慢操作是性能问题的第一手证据，比事后看 APM 更直接（APM 有采样，日志不丢）。

```python
with timed(log, "查询订单", slow_ms=200, order_id=oid):
    order = repo.find(oid)
# 超过 200ms 自动 WARN 并记 cost_ms，异常自动 ERROR
```

阈值按业务定：内部 RPC 50ms、DB 查询 200ms、第三方调用 1000ms、批处理任务按批大小。

**日志里累计 `cost_ms` 就能算出 P50/P95/P99**，不需要额外接监控系统。

## 接日志平台

```python
setup(json_lines=True)    # 一行一条 JSON
```

- **ELK / OpenSearch**：Filebeat 采集，按 `category` 建索引
- **Loki + Grafana**：Promtail 采集，用 `{category="payment"}` 查询
- **云日志**（阿里 SLS / 腾讯 CLS）：直接投递 JSON

不用平台也能用：`grep` + `jq` 足够应付中小规模。

## 文件布局与轮转

```
logs/
├── app.log              全量，10MB 滚动，留 10 份
├── error.log            仅 ERROR+，排错第一入口
└── category/
    ├── payment.log      该域全量（含 DEBUG）
    ├── db.log
    └── ...
```

分类文件保留份数可以少（3 份），因为 app.log 已有全量兜底。

**磁盘保护**：生产环境必须设 `max_bytes` 和 `backup_count`，否则日志能把磁盘写满——这是真实发生过的故障原因。

## 日志 vs 指标 vs 追踪

| | 日志 | 指标 | 追踪 |
|---|---|---|---|
| 回答什么 | **发生了什么，为什么** | 现在健康吗，趋势如何 | 这次请求慢在哪一段 |
| 成本 | 高（存储） | 低 | 中 |
| 采样 | 不采样 | 聚合 | 通常采样 |
| 用途 | 排错根因 | 告警、看板 | 性能分析 |

**日志不能替代指标做告警**——基于日志的告警延迟高、易漏、成本高。关键指标（错误率、P95 延迟、QPS）要单独打点。

## 生产必查清单

- [ ] 生产 `level=INFO`，未开 DEBUG
- [ ] 日志文件设了轮转（大小 + 份数）
- [ ] 异常路径都有 `exc_info=True`
- [ ] 关键链路有 trace_id 透传
- [ ] error.log 有告警（至少日报或 IM 推送）
- [ ] 无密码/token/身份证进日志
- [ ] 循环里没有 INFO 日志
- [ ] 第三方调用记了耗时和返回码
- [ ] 慢操作有阈值告警

## 反模式

| 反模式 | 后果 |
|---|---|
| `print` / `console.log` | 无级别、无分类、无 trace、无法关闭 |
| 所有异常都 `log.error("失败")` 不带栈 | 只知道坏了，不知道哪坏了 |
| 用户输入错误记 ERROR | error.log 被噪音淹没，真故障被漏 |
| 循环里打 INFO | 日志量爆炸，拖慢主流程 |
| 一个分类打天下 | 等于没分类 |
| 把整个 request body 打进日志 | 存储爆炸 + 敏感信息泄露 |
| 生产开 DEBUG 忘关 | 磁盘写满，服务挂掉 |
| 只记日志不打指标 | 出问题只能等用户投诉 |
