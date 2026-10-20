# 灵感来源：[headroomlabs-ai/headroom](https://github.com/headroomlabs-ai/headroom)

- **星标**：76,324
- **许可证**：Apache-2.0
- **语言**：Python（另有 TypeScript 包）
- **仓库规模**：94,520 KB，492 个未关 issue，4,000+ PR
- **它承诺解决**：在提示词进入 LLM 之前压缩工具输出、日志、RAG 片段和文件，答案不变、token 大幅减少。

---

## 它做对了什么

**1. 抓住了一个真实且持续变大的痛点。**

上下文窗口在涨，但涨得没有 agent 产生的数据快。一个 coding agent 跑十几轮工具调用，日志和文件内容就能把窗口撑爆。压缩不是优化，是刚需。

**2. 按内容类型分流，而不是一把梭。**

`ContentRouter` 先判断内容类型，再交给对应压缩器：JSON 走 `SmartCrusher`，代码走 AST 级 `CodeCompressor`，散文走自己的模型 `Kompress-v2-base`。这个分层是对的——不同类型的最优压缩策略确实不同。

**3. 把「可逆」当成一等功能。**

`CCR` 把原始内容缓存在本地，模型需要细节时可以调 `headroom_retrieve` 拿回原文。这让压缩从「有损」变成「按需无损」，是很聪明的设计。

**4. 接入形态铺得很全。**

库、代理、`wrap` 命令行包装、MCP server 四种方式，几乎覆盖了所有使用场景。这也是它能冲到 76k 星的重要原因——不管你用什么工具，都有一条路能接上。

---

## 它在哪里让人痛苦

**痛点一：装上去容易，拆下来是一场寻宝。**

> "The pieces to undo a Headroom install exist, but they're fragmented and undocumented — so a full teardown is a multi-command scavenger hunt."
>
> —— [issue #748](https://github.com/headroomlabs-ai/headroom/issues/748)

要完整卸载，得依次执行 `headroom unwrap codex`、`headroom unwrap claude`、`headroom mcp uninstall`、`headroom install agent remove`，再手动 `pip uninstall`。没有统一的 `uninstall` 命令。

这意味着 `wrap` 会改你的 agent 配置。对一个只想压缩一下日志的人来说，这个代价太大了。

**痛点二：ML 内容识别在 Windows 上直接把工具跑挂。**

> "`headroom._core.detect_content_type()` hangs on Windows with `headroom-ai 0.24.0` and Python 3.13. This makes `headroom_compress` unusable for any content large enough to enter the real compression path."
>
> —— [issue #845](https://github.com/headroomlabs-ai/headroom/issues/845)

判断「这是什么类型的内容」这一步，用的是 ONNX 上的 magika 模型。这个依赖在 Windows + Python 3.13 上会挂起，导致整个压缩链路不可用。相关 issue 还有 [#600](https://github.com/headroomlabs-ai/headroom/issues/600)（大 JSON 挂起）和 [#4200](https://github.com/headroomlabs-ai/headroom/issues/4200)（要求出个禁用 magika 的 lite 镜像）。

**痛点三：代理模式不稳定。**

[#3259](https://github.com/headroomlabs-ai/headroom/issues/3259) 报告 proxy 在 upstream_connect 上挂 180-220 秒然后 502；[#980](https://github.com/headroomlabs-ai/headroom/issues/980) 是 Docker bridge 网络下同步 DNS 阻塞事件循环。代理是个强大的形态，但它把网络问题也一起引入了。

**痛点四：概念太多，文档跟不上。**

库 / 代理 / wrap / MCP / 跨 agent 记忆 / `learn` / 输出 token 压缩 / CCR——八个概念，每个都有自己的配置和坑。[#207](https://github.com/headroomlabs-ai/headroom/issues/207) 直接就是「多个文档问题」。

---

## 我们的取舍

### 砍掉了什么

| 砍掉 | 理由 |
|------|------|
| ML 内容识别（magika/ONNX） | 这是 #845 的根因。用 `json.loads` 试探 + 日志行正则，准确率对这个场景够用，且零依赖不会挂 |
| 代理模式 | 引入端口、网络、DNS 一整套故障面。CLI 读写文件已经能覆盖绝大多数场景 |
| `wrap` / MCP / 库三种接入 | 都要改别人的配置。一个 CLI 不需要改任何东西 |
| AST 代码压缩 | 收益不稳定，破坏语义的风险高，不如不做 |
| reversible cache（CCR） | 留原文件就行。多一层缓存就多一层要维护的状态 |
| 跨 agent 记忆 / `learn` | 和「压缩一段数据」是两件事，不该塞进同一个工具 |

### 保留了什么

- **按类型分流压缩**：JSON 表格化、日志去重、文本折叠，三种策略分开实现
- **压缩率统计**：让你看到省了多少，而不是盲信
- **错误行免疫**：这是原版也做对的事，我们保留并做成了硬编码

### 加了一条原版没有的

**关键行截断免疫。** 原版没有「行数预算」这个概念（因为它不做行级截断）。我们加了 `--max-rows`，但明确规定 `ERROR` / `WARN` / `FATAL` / `CRITICAL` 行不受预算约束。

开发过程中真的踩到了这个坑：第一版实现盲目砍掉中间行，结果把夹在第 200 行的 `FATAL` 吞了。发现后重写了截断逻辑，并加了两个回归测试锁住这个行为。

---

## 与原版的差异

| 维度 | headroom | squeeze |
|------|----------|---------|
| 安装 | pip / npm，带 ML 依赖 | 无。下载一个 `.py` 文件即可 |
| 依赖 | magika、onnxruntime、fastapi 等 | **标准库，零依赖** |
| 配置 | 需要 wrap / proxy / MCP 配置 | **零配置** |
| 接入方式 | 库 / 代理 / wrap / MCP | **CLI** |
| 改别人的配置 | `wrap` 会改 agent 配置 | **不会碰任何配置** |
| 内容识别 | ML 模型（Windows+Py3.13 会挂） | 启发式（不会挂） |
| 可逆 | CCR 本地缓存 + retrieve | 自己留原文件 |
| 代码压缩 | AST 级 | 不支持 |
| 启动开销 | 加载模型 | 毫秒级 |
| 代码量 | 94 MB 仓库 | 单文件约 380 行 |
| 适合谁 | 想要全局无感压缩的 agent 重度用户 | 只想快速压一下手头这份数据的人 |

---

## 许可证说明

参考项目 **headroomlabs-ai/headroom** 采用 **Apache-2.0** 许可证。

本工具（`squeeze.py`）**未复用该项目的任何代码**。所有实现均为独立编写：架构参考了它「按内容类型分流压缩」的思路，但具体算法、数据结构和输出格式都是自行设计的。

MIT 与 Apache-2.0 兼容，本仓库整体采用 MIT 许可证不构成冲突。
