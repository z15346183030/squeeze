#!/usr/bin/env python3
"""squeeze — 把喂给 LLM 之前的 JSON / 日志 / 文本压小。

零依赖、零配置、单文件。不下载模型，不开端口，不改你的任何配置文件。

用法:
    python squeeze.py big.json                 # 压缩后打印到 stdout
    python squeeze.py app.log --stats          # 带压缩率统计
    cat data.json | python squeeze.py -        # 从管道读
    python squeeze.py big.json -o small.txt    # 写文件
"""
from __future__ import annotations

import argparse
import json
import re
import sys

__version__ = "0.1.0"

# ---------------------------------------------------------------- token 估算

_CJK = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af\u3000-\u303f\uff00-\uffef]")


def estimate_tokens(text: str) -> int:
    """粗估 token 数。中日韩字符约 1 token/字，其余约 4 字符/token。

    这是个估算，不是精确计数——目的只是让你看到量级差多少。
    """
    cjk = len(_CJK.findall(text))
    return int(cjk + (len(text) - cjk) / 4.0)


# ---------------------------------------------------------------- 内容识别

_SEV = re.compile(r"\b(TRACE|DEBUG|INFO|NOTICE|WARN(?:ING)?|ERROR|ERR|FATAL|CRITICAL)\b")
_TS = re.compile(
    r"^\s*(?:\[?\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}|\d{2}:\d{2}:\d{2}|\[?\d{2}:\d{2}\]?)"
)
_SEV_AT_START = re.compile(r"^\s*\[?(TRACE|DEBUG|INFO|NOTICE|WARN(?:ING)?|ERROR|ERR|FATAL|CRITICAL)\]?\b")


def detect(text: str) -> str:
    """判断内容类型：json / log / text。

    刻意用纯启发式而不是 ML 分类器——省掉一个模型依赖，也不会因为
    ONNX 运行时在某个平台上卡住而整体不可用。
    """
    stripped = text.lstrip()
    if stripped[:1] in "{[":
        try:
            json.loads(text)
            return "json"
        except (ValueError, RecursionError):
            pass

    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return "text"

    hits = 0
    for ln in lines[:500]:
        if _SEV_AT_START.match(ln) or (_TS.match(ln) and _SEV.search(ln)):
            hits += 1
    if hits / min(len(lines), 500) >= 0.3:
        return "log"
    return "text"


# ---------------------------------------------------------------- JSON 压缩


def _truncate_str(s: str, max_str: int) -> str:
    if max_str <= 0 or len(s) <= max_str:
        return s
    return f"{s[:max_str]}…<+{len(s) - max_str}>"


def _cell(value: Any, max_str: int) -> str:
    """把标量渲染成表格里的一个单元格。"""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value) if isinstance(value, float) else str(value)
    text = _truncate_str(str(value), max_str)
    # 竖线是列分隔符，换行会破坏表格结构
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", "\\n").replace("\r", "")


def _is_scalar(v: Any) -> bool:
    return v is None or isinstance(v, (bool, int, float, str))


def _homogeneous_keys(items: list) -> list[str] | None:
    """如果所有元素都是键集合完全一致的 dict，返回键列表。"""
    if not items or not all(isinstance(i, dict) for i in items):
        return None
    keys = list(items[0].keys())
    key_set = set(keys)
    for item in items:
        if set(item.keys()) != key_set:
            return None
    return keys


def _render_table(items: list[dict], keys: list[str], max_str: int, max_rows: int, indent: str) -> list[str]:
    out = [f"{indent}# {len(items)} 行 × {len(keys)} 列: {', '.join(keys)}"]
    shown = items
    omitted = 0
    if max_rows > 0 and len(items) > max_rows:
        head = max_rows // 2
        tail = max_rows - head
        shown = items[:head] + items[len(items) - tail:]
        omitted = len(items) - len(shown)

    for idx, item in enumerate(shown):
        if omitted and idx == max_rows // 2:
            out.append(f"{indent}# …省略 {omitted} 行…")
        cells = [_cell(item[k], max_str) for k in keys]
        out.append(indent + " | ".join(cells))
    return out


def _render(value: Any, max_str: int, max_rows: int, indent: str, depth: int) -> list[str]:
    pad = indent + "  "

    if _is_scalar(value):
        return [indent + _cell(value, max_str)]

    if isinstance(value, dict):
        if not value:
            return [indent + "{}"]
        lines = []
        for key, val in value.items():
            if _is_scalar(val):
                lines.append(f"{pad}{key}: {_cell(val, max_str)}")
            elif isinstance(val, list) and val and _homogeneous_keys(val):
                keys = _homogeneous_keys(val)
                lines.append(f"{pad}{key}:")
                lines.extend(_render_table(val, keys, max_str, max_rows, pad + "  "))
            else:
                lines.append(f"{pad}{key}:")
                lines.extend(_render(val, max_str, max_rows, pad + "  ", depth + 1))
        return [indent + "{"] + lines + [indent + "}"]

    if isinstance(value, list):
        if not value:
            return [indent + "[]"]
        keys = _homogeneous_keys(value)
        if keys:
            return _render_table(value, keys, max_str, max_rows, indent)
        if all(_is_scalar(v) for v in value):
            joined = ", ".join(_cell(v, max_str) for v in value)
            if len(joined) <= 120:
                return [indent + f"[{joined}]"]
            return [indent + "["] + [pad + _cell(v, max_str) for v in value] + [indent + "]"]
        lines = [indent + "["]
        for item in value:
            lines.extend(_render(item, max_str, max_rows, pad, depth + 1))
        lines.append(indent + "]")
        return lines

    return [indent + _cell(value, max_str)]


def squeeze_json(text: str, max_str: int = 120, max_rows: int = 40) -> str:
    obj = json.loads(text)
    return "\n".join(_render(obj, max_str, max_rows, "", 0))


# ---------------------------------------------------------------- 日志压缩

_NOISE_SEV = {"TRACE", "DEBUG"}
_KEEP_SEV = {"ERROR", "ERR", "FATAL", "CRITICAL", "WARN", "WARNING"}

_NORMALIZE = [
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I), "<uuid>"),
    (re.compile(r"\b0x[0-9a-f]+\b", re.I), "<hex>"),
    (re.compile(r"\b\d+\.\d+\b"), "<num>"),
    (re.compile(r"\b\d+\b"), "<num>"),
]


def _severity(line: str) -> str | None:
    m = _SEV_AT_START.match(line)
    if m:
        return m.group(1).upper()
    m = _SEV.search(line)
    return m.group(1).upper() if m else None


def _norm_key(line: str) -> str:
    """把易变的片段抹平，用来判断两行是不是「同一类」消息。"""
    key = line
    for pattern, repl in _NORMALIZE:
        key = pattern.sub(repl, key)
    return key


def squeeze_log(text: str, max_rows: int = 0, keep_noise: bool = False) -> str:
    """压缩日志。

    两条不可妥协的规则：
    1. ERROR / WARN / FATAL / CRITICAL 行永不丢弃——即使 max_rows 把它们
       挤在中间，也必须保留。
    2. 丢弃任何东西都要在输出里留一行说明，不能静默删。
    """
    raw_lines = text.splitlines()
    total_lines = len(raw_lines)

    # ---- 第一遍：丢噪音 + 折叠连续重复
    entries: list[list] = []  # [行内容, 重复次数]
    noise_dropped = 0
    last_key = None

    for line in raw_lines:
        if not line.strip():
            continue
        sev = _severity(line)
        if sev in _NOISE_SEV and not keep_noise:
            noise_dropped += 1
            continue

        key = _norm_key(line)
        if key == last_key and entries:
            entries[-1][1] += 1
            continue

        entries.append([line, 1])
        last_key = key

    def render(entry: list) -> str:
        line, count = entry
        return f"{line}  ×{count}" if count > 1 else line

    # ---- 第二遍：按预算截断，但关键行免疫
    omitted_total = 0
    if max_rows > 0 and len(entries) > max_rows:
        critical = [i for i, e in enumerate(entries) if _severity(e[0]) in _KEEP_SEV]
        critical_set = set(critical)
        others = [i for i in range(len(entries)) if i not in critical_set]

        budget = max(max_rows - len(critical), 0)
        head = budget // 2
        tail = budget - head
        keep = set(critical) | set(others[:head])
        if tail:
            keep |= set(others[len(others) - tail:])

        body: list[str] = []
        skipped = 0
        for i, entry in enumerate(entries):
            if i in keep:
                if skipped:
                    body.append(f"# …省略 {skipped} 行…")
                    omitted_total += skipped
                    skipped = 0
                body.append(render(entry))
            else:
                skipped += 1
        if skipped:
            body.append(f"# …省略 {skipped} 行…")
            omitted_total += skipped
    else:
        body = [render(e) for e in entries]

    header = []
    if noise_dropped:
        header.append(f"# 已丢弃 {noise_dropped} 行 TRACE/DEBUG（需要时用 --keep-noise 保留）")
    if len(entries) != total_lines:
        header.append(
            f"# 原始 {total_lines} 行 → {len(body)} 行"
            f"（重复行折叠为 ×N，ERROR/WARN/FATAL 全部保留）"
        )
    return "\n".join(header + body)


# ---------------------------------------------------------------- 文本压缩


def squeeze_text(text: str) -> str:
    lines = text.splitlines()
    out: list[str] = []
    blank = 0
    seen: set[str] = set()

    for line in lines:
        if not line.strip():
            blank += 1
            if blank > 1:
                continue
        else:
            blank = 0

        stripped = line.strip()
        if stripped and len(stripped) > 40:
            if stripped in seen:
                out.append(f"[重复段落，已省略: {stripped[:60]}…]")
                continue
            seen.add(stripped)

        out.append(line)

    return "\n".join(out)


# ---------------------------------------------------------------- CLI


def squeeze(text: str, mode: str = "auto", max_str: int = 120,
            max_rows: int = 40, keep_noise: bool = False) -> tuple[str, str]:
    if mode == "auto":
        mode = detect(text)

    if mode == "json":
        try:
            return squeeze_json(text, max_str=max_str, max_rows=max_rows), "json"
        except ValueError as exc:
            print(f"squeeze: 不是合法 JSON（{exc}），退回文本模式", file=sys.stderr)
            return squeeze_text(text), "text"

    if mode == "log":
        return squeeze_log(text, max_rows=max_rows, keep_noise=keep_noise), "log"

    return squeeze_text(text), "text"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="squeeze",
        description="把喂给 LLM 之前的 JSON / 日志 / 文本压小。零依赖、零配置。",
    )
    ap.add_argument("path", help="输入文件，用 - 表示从 stdin 读")
    ap.add_argument("-o", "--output", help="输出文件（默认打印到 stdout）")
    ap.add_argument("--mode", choices=["auto", "json", "log", "text"], default="auto",
                    help="强制指定内容类型（默认自动识别）")
    ap.add_argument("--max-str", type=int, default=120, help="JSON 里单个字符串最长保留多少字符（0 表示不截断）")
    ap.add_argument("--max-rows", type=int, default=40, help="JSON 数组/日志最多保留多少行（0 表示不限）")
    ap.add_argument("--keep-noise", action="store_true", help="日志模式保留 TRACE/DEBUG 行")
    ap.add_argument("--stats", action="store_true", help="把压缩率打到 stderr")
    ap.add_argument("--version", action="version", version=f"squeeze {__version__}")
    args = ap.parse_args(argv)

    if args.path == "-":
        text = sys.stdin.read()
    else:
        try:
            with open(args.path, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError as exc:
            print(f"squeeze: 读不到 {args.path}: {exc}", file=sys.stderr)
            return 1

    result, used_mode = squeeze(
        text, mode=args.mode, max_str=args.max_str,
        max_rows=args.max_rows, keep_noise=args.keep_noise,
    )

    if args.output:
        try:
            with open(args.output, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(result)
        except OSError as exc:
            print(f"squeeze: 写不了 {args.output}: {exc}", file=sys.stderr)
            return 1
    else:
        sys.stdout.write(result)
        if not result.endswith("\n"):
            sys.stdout.write("\n")

    if args.stats:
        before, after = estimate_tokens(text), estimate_tokens(result)
        saved = before - after
        pct = (saved / before * 100) if before else 0.0
        print(
            f"[squeeze] 类型={used_mode}  字符 {len(text)}→{len(result)}  "
            f"token≈{before}→{after}  省 {pct:.1f}%",
            file=sys.stderr,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
