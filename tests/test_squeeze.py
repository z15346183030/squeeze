"""squeeze 的测试。只用标准库 unittest，跑 `python -m unittest discover` 即可。"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import squeeze  # noqa: E402


class TestDetect(unittest.TestCase):
    def test_detects_json_object(self):
        self.assertEqual(squeeze.detect('{"a": 1}'), "json")

    def test_detects_json_array(self):
        self.assertEqual(squeeze.detect('[{"a": 1}, {"a": 2}]'), "json")

    def test_broken_json_falls_through(self):
        # 以 { 开头但不是合法 JSON，不应该被当成 json
        self.assertNotEqual(squeeze.detect('{not json at all'), "json")

    def test_detects_log(self):
        text = "\n".join(
            f"2026-10-20 10:00:{i:02d} INFO starting worker {i}" for i in range(20)
        )
        self.assertEqual(squeeze.detect(text), "log")

    def test_bracket_level_log(self):
        text = "\n".join(f"[ERROR] something broke {i}" for i in range(10))
        self.assertEqual(squeeze.detect(text), "log")

    def test_detects_plain_text(self):
        text = "这是一段普通说明文字。\n它没有任何日志特征。\n也不是 JSON。"
        self.assertEqual(squeeze.detect(text), "text")


class TestJson(unittest.TestCase):
    def test_table_form_is_smaller_and_labelled(self):
        rows = [{"id": i, "name": f"user{i}", "status": "ok"} for i in range(50)]
        raw = json.dumps(rows)
        out = squeeze.squeeze_json(raw, max_rows=0)
        self.assertLess(len(out), len(raw))
        self.assertIn("50 行 × 3 列", out)
        self.assertIn("id, name, status", out)

    def test_long_strings_truncated_with_marker(self):
        payload = json.dumps({"note": "x" * 500})
        out = squeeze.squeeze_json(payload, max_str=20)
        self.assertIn("…<+480>", out)

    def test_max_str_zero_keeps_everything(self):
        payload = json.dumps({"note": "x" * 500})
        out = squeeze.squeeze_json(payload, max_str=0)
        self.assertIn("x" * 500, out)

    def test_max_rows_omits_middle(self):
        rows = [{"id": i, "v": i} for i in range(100)]
        out = squeeze.squeeze_json(json.dumps(rows), max_rows=10)
        self.assertIn("省略", out)
        # 头尾各保留 5 行
        self.assertIn("0 | 0", out)
        self.assertIn("99 | 99", out)
        self.assertNotIn("50 | 50", out)

    def test_pipe_in_value_is_escaped(self):
        rows = [{"cmd": "a|b"}, {"cmd": "c|d"}]
        out = squeeze.squeeze_json(json.dumps(rows))
        self.assertIn("a\\|b", out)

    def test_nested_object_rendered(self):
        payload = json.dumps({"outer": {"inner": 1}})
        out = squeeze.squeeze_json(payload)
        self.assertIn("inner: 1", out)

    def test_null_and_bool(self):
        out = squeeze.squeeze_json(json.dumps({"a": None, "b": True}))
        self.assertIn("a: null", out)
        self.assertIn("b: true", out)


class TestLog(unittest.TestCase):
    LOG = "\n".join([
        "2026-10-20 10:00:01 INFO 启动服务",
        "2026-10-20 10:00:02 DEBUG 加载缓存",
        "2026-10-20 10:00:03 DEBUG 加载缓存",
        "2026-10-20 10:00:04 ERROR 数据库连接失败",
        "2026-10-20 10:00:05 INFO 重试中",
        "2026-10-20 10:00:06 INFO 重试中",
        "2026-10-20 10:00:07 INFO 重试中",
        "2026-10-20 10:00:08 FATAL 放弃退出",
    ])

    def test_debug_lines_dropped_by_default(self):
        out = squeeze.squeeze_log(self.LOG)
        self.assertNotIn("加载缓存", out)
        self.assertIn("已丢弃", out)

    def test_error_and_fatal_survive(self):
        out = squeeze.squeeze_log(self.LOG)
        self.assertIn("ERROR 数据库连接失败", out)
        self.assertIn("FATAL 放弃退出", out)

    def test_consecutive_duplicates_collapsed(self):
        out = squeeze.squeeze_log(self.LOG)
        self.assertIn("×3", out)

    def test_keep_noise_preserves_debug(self):
        out = squeeze.squeeze_log(self.LOG, keep_noise=True)
        self.assertIn("加载缓存", out)
        self.assertNotIn("已丢弃", out)

    def test_blank_lines_removed(self):
        out = squeeze.squeeze_log("INFO a\n\n\nERROR b")
        self.assertNotIn("\n\n", out)

    def test_critical_lines_survive_middle_truncation(self):
        """回归测试：max_rows 截断时，夹在中间的 FATAL 绝不能被砍掉。"""
        lines = [
            f"2026-10-20 10:00:00 INFO task_{chr(65 + i % 26)}{chr(97 + (i // 26) % 26)}"
            for i in range(200)
        ]
        lines[100] = "2026-10-20 10:01:40 FATAL 数据库挂了"
        out = squeeze.squeeze_log("\n".join(lines), max_rows=10)
        self.assertIn("FATAL 数据库挂了", out)
        self.assertIn("省略", out)

    def test_all_errors_kept_even_when_budget_is_small(self):
        lines = [f"2026-10-20 10:00:00 ERROR 故障码 {chr(65 + i)}" for i in range(30)]
        out = squeeze.squeeze_log("\n".join(lines), max_rows=5)
        # 30 行全是 ERROR，预算 5 行不够，但一行都不能丢
        for i in range(30):
            self.assertIn(f"故障码 {chr(65 + i)}", out)


class TestText(unittest.TestCase):
    def test_multiple_blank_lines_collapsed(self):
        out = squeeze.squeeze_text("第一段\n\n\n\n第二段")
        self.assertNotIn("\n\n\n", out)

    def test_repeated_paragraph_deduped(self):
        para = (
            "这是一段足够长的重复内容，长度超过四十个字符用来触发去重逻辑，"
            "确保它会被正确识别成重复段落并省略掉。"
        )
        self.assertGreater(len(para), 40)
        out = squeeze.squeeze_text(f"{para}\n中间\n{para}")
        self.assertIn("重复段落，已省略", out)


class TestTokens(unittest.TestCase):
    def test_ascii_estimate(self):
        self.assertEqual(squeeze.estimate_tokens("a" * 400), 100)

    def test_cjk_costs_more(self):
        self.assertGreater(
            squeeze.estimate_tokens("中" * 100),
            squeeze.estimate_tokens("a" * 100),
        )


class TestCli(unittest.TestCase):
    def test_reads_file_and_writes_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = pathlib.Path(tmp) / "in.json"
            dst = pathlib.Path(tmp) / "out.txt"
            src.write_text(
                json.dumps([{"id": i, "name": f"u{i}"} for i in range(30)]),
                encoding="utf-8",
            )
            code = squeeze.main([str(src), "-o", str(dst)])
            self.assertEqual(code, 0)
            self.assertIn("30 行 × 2 列", dst.read_text(encoding="utf-8"))

    def test_missing_file_returns_error(self):
        self.assertEqual(squeeze.main(["definitely/not/here.json"]), 1)

    def test_invalid_json_falls_back_to_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = pathlib.Path(tmp) / "bad.json"
            src.write_text("{broken", encoding="utf-8")
            dst = pathlib.Path(tmp) / "out.txt"
            self.assertEqual(squeeze.main([str(src), "-o", str(dst)]), 0)
            self.assertEqual(dst.read_text(encoding="utf-8").strip(), "{broken")


if __name__ == "__main__":
    unittest.main()
