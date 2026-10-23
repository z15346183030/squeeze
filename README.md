# squeeze

**Shrink JSON and logs before they hit the context window.** Zero dependencies, zero config, single file.

```
JSON sample:  15,755 chars → 5,025 chars   (60.4% smaller)
Log sample:   20,739 chars → 2,343 chars   (87.1% smaller)
```

One rule that is not configurable: **error lines are never dropped.** A `FATAL` buried on line 200 of a 401-line log survives every truncation pass. The output always states exactly what was removed, so nothing disappears silently.

```bash
python squeeze.py big_response.json --stats
cat app.log | python squeeze.py -
```

No model download, no server, no changes to your config files.

<sub>中文说明见下方 · Chinese documentation below</sub>

---

把喂给 LLM 之前的 JSON / 日志 / 文本压小。**零依赖、零配置、单文件。**

不下载模型，不开端口，不改你任何配置文件。一个 `python squeeze.py` 就完事。

```
JSON 示例:  15,755 字符 → 5,025 字符   省 60.4%
日志示例:  20,739 字符 → 2,343 字符   省 87.1%
```

---

## 为什么要有这个东西

大模型上下文里最占地方的，往往不是你的问题，而是你贴进去的那坨数据——一份 500 行的 API 响应、一段 400 行的构建日志。

这些东西的共同点是：**结构重复度极高**。JSON 数组里每一行都在重复同一组 key，日志里 60% 是 DEBUG 噪音。LLM 不需要看这些重复。

`squeeze` 做的就是把这部分冗余去掉，同时保证关键信息一个字都不丢。

---

## 用法

不需要安装。Python 3.9+ 直接跑：

```bash
# 压缩一份 JSON
python squeeze.py big_response.json

# 看压缩了多少
python squeeze.py big_response.json --stats

# 从管道读
cat app.log | python squeeze.py -

# 写文件
python squeeze.py app.log -o small.log

# 强制按日志处理（自动识别猜错时）
python squeeze.py weird.txt --mode log
```

### 输出示例

一份 80 条记录的 API 响应，压缩前：

```json
[{"id":0,"username":"user_000","email":"user0@example.com","role":"dev",...}, {...}, ...]
```

压缩后：

```
# 80 行 × 7 列: id, username, email, role, active, created_at, bio
0 | user_000 | user0@example.com | dev | true | 2026-07-10T08:00:00Z | 这是一个很长的个人简介…
1 | user_001 | user1@example.com | admin | true | 2026-02-15T08:00:00Z | 这是一个很长的个人简介…
2 | user_002 | user2@example.com | viewer | false | 2026-09-13T08:00:00Z | 这是一个很长的个人简介…
```

字段名只出现一次，不再每条重复。

日志压缩后：

```
# 已丢弃 250 行 TRACE/DEBUG（需要时用 --keep-noise 保留）
# 原始 401 行 → 64 行（重复行折叠为 ×N，ERROR/WARN/FATAL 全部保留）
2026-10-20 10:00:04 WARN  slow query took 1.8s
2026-10-20 10:03:20 FATAL 无法连接数据库，进程退出
...
```

---

## 参数

| 参数 | 默认 | 说明 |
|------|------|------|
| `--mode` | `auto` | `auto` / `json` / `log` / `text` |
| `--max-str` | `120` | JSON 里单个字符串最长保留多少字符，`0` 表示不截断 |
| `--max-rows` | `40` | JSON 数组 / 日志最多保留多少行，`0` 表示不限 |
| `--keep-noise` | 关 | 日志模式保留 TRACE/DEBUG |
| `--stats` | 关 | 把压缩率打到 stderr，不影响 stdout |
| `-o` | stdout | 输出文件 |

---

## 两条设计红线

**1. 错误行永不丢弃。**

日志模式下 `ERROR` / `WARN` / `FATAL` / `CRITICAL` 行免疫 `--max-rows`。哪怕 401 行日志被压到 64 行，夹在第 200 行的 `FATAL` 也一定在里面。

这不是个可配置项，是硬编码的。压缩日志最怕的就是把唯一那条有用的错误吞掉——那种工具不如不用。

**2. 删了什么必须说出来。**

输出头部永远有一行说明：丢了多少噪音行、折叠了多少重复、省略了多少行。不会有静默丢失。

---

## 它不做什么

诚实地划个边界，免得你期待错：

- **不做内容类型识别的 ML 模型。** 纯正则 + `json.loads` 试探。够用，而且不会在某个平台上卡死。
- **不做 AST 级代码压缩。** 代码结构复杂，粗暴压缩容易破坏语义，收益也不稳定。
- **不做代理、不做 MCP、不做 reversible cache。** 就一个 CLI，读文件写文件。要 reversible 的话自己留一份原文件。
- **不保证输出是合法 JSON。** 输出是给人/给模型读的紧凑文本，不是用来 `json.loads` 的。

---

## 测试

```bash
python -m unittest discover -s tests -v
```

27 个测试，覆盖类型识别、JSON 表格化、字符串截断、行数截断、日志去重、关键行保留、CLI 端到端。

---

## 许可证

MIT，见 [LICENSE](LICENSE)。
