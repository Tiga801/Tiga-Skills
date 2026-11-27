"""proofread.py 端到端测试。

在 Tiga-Skills 仓库根目录运行：uv run python -m unittest discover -s skills/proofread-markdown/tests -v

每个用例在临时目录里写一份目标文档和一份审校意见文件，用子进程调用真实脚本，再检查退出码、输出和两个文件的内容。

覆盖的失败方式：
1. 合法意见没有正确应用：采纳的没换成建议、改为的没换成改为后的文字、不改和留空的被改动、
   应用后没在意见文件里标记已应用；合法文件被 check 误报
2. 原文在目标文档里找不到，没报错
3. 原文在目标文档里出现不止一次，没报错（替换会落在错误的位置）
4. 两条要应用的意见原文区间重叠，没报错（先替换的会让后一条找不到原文或改坏前一条）
5. 一条意见的建议里含有另一条意见的原文，后一条被替换到前一条的新文字里
6. 决定栏写法非法（不认识的词、「改为：」后面为空），没报错
7. 意见结构有误：缺字段、字段重复、不认识的字段或游离文字、编号重复、标题格式不对，没报错
8. 建议为空或与原文相同，没报错
9. 跨行原文（⏎ 表示换行）匹配不上，或建议里的 ⏎ 没写成换行
10. 重复运行 apply 重复替换：已应用的意见再次被应用（建议里含有原文时尤其明显）
11. 有错误时 apply 仍写回了一部分：目标文档或意见文件被改动
12. 目标文档或意见文件不存在时没有明确报错（退出码 2）
13. 不改或留空的意见原文抄错，却阻止了应用（它们不会被应用，不该检查原文）
"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "proofread.py"


def item(num, quote, suggestion, decision="采纳", status=None, tag="语病", loc="L1", reason="读不通"):
    lines = [
        f"## {num} {tag}",
        "",
        f"- 位置：{loc}",
        f"- 原文：{quote}",
        f"- 建议：{suggestion}",
        f"- 理由：{reason}",
        f"- 决定：{decision}",
    ]
    if status:
        lines.append(f"- 状态：{status}")
    return "\n".join(lines) + "\n"


def opinions(*items):
    return "# 审校意见\n\n- 目标：doc.md\n\n" + "\n".join(items)


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.doc = self.dir / "doc.md"
        self.ops = self.dir / "opinions.md"

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, doc, ops):
        self.doc.write_text(doc, encoding="utf-8")
        self.ops.write_text(ops, encoding="utf-8")

    def run_cmd(self, cmd, doc=None, ops=None):
        return subprocess.run(
            [sys.executable, str(SCRIPT), cmd, str(doc or self.doc), str(ops or self.ops)],
            capture_output=True,
            text=True,
        )

    def assert_rejected(self, doc, ops, *needles):
        """check 和 apply 都报错（退出码 1），两个文件都不被改动，输出里含有每个 needle。"""
        self.write(doc, ops)
        for cmd in ("check", "apply"):
            result = self.run_cmd(cmd)
            out = result.stdout + result.stderr
            self.assertEqual(result.returncode, 1, f"{cmd} 应当报错：\n{out}")
            for needle in needles:
                self.assertIn(needle, out, f"{cmd} 的输出缺少「{needle}」：\n{out}")
        self.assertEqual(self.doc.read_text(encoding="utf-8"), doc)
        self.assertEqual(self.ops.read_text(encoding="utf-8"), ops)


class ApplyTest(Case):
    def test_decisions_applied(self):
        doc = "甲句子不通。\n乙句子啰嗦。\n丙句子可以。\n丁句子留空。\n"
        ops = opinions(
            item("01", "甲句子不通。", "甲句子通顺。"),
            item("02", "乙句子啰嗦。", "乙句子简短。", decision="改为：乙句子更简短。"),
            item("03", "丙句子可以。", "丙句子更好。", decision="不改"),
            item("04", "丁句子留空。", "丁句子改了。", decision=""),
        )
        self.write(doc, ops)
        result = self.run_cmd("check")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        result = self.run_cmd("apply")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            self.doc.read_text(encoding="utf-8"),
            "甲句子通顺。\n乙句子更简短。\n丙句子可以。\n丁句子留空。\n",
        )
        text = self.ops.read_text(encoding="utf-8")
        self.assertEqual(text.count("- 状态：已应用"), 2)
        sections = text.split("## ")
        self.assertIn("- 状态：已应用", sections[1])
        self.assertIn("- 状态：已应用", sections[2])
        self.assertNotIn("- 状态：已应用", sections[3])
        self.assertNotIn("- 状态：已应用", sections[4])
        self.assertIn("2", result.stdout)

    def test_check_still_passes_after_apply(self):
        doc = "原来的句子。\n"
        self.write(doc, opinions(item("01", "原来的句子。", "新的句子。")))
        self.assertEqual(self.run_cmd("apply").returncode, 0)
        result = self.run_cmd("check")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_suggestion_containing_other_quote(self):
        doc = "甲乙。丙丁。\n"
        ops = opinions(
            item("01", "甲乙。", "甲乙丙丁。"),
            item("02", "丙丁。", "戊己。"),
        )
        self.write(doc, ops)
        result = self.run_cmd("apply")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.doc.read_text(encoding="utf-8"), "甲乙丙丁。戊己。\n")

    def test_multiline_quote_and_suggestion(self):
        doc = "第一行，\n第二行。\n其他。\n"
        ops = opinions(item("01", "第一行，⏎第二行。", "合并成一行。⏎另起一行。"))
        self.write(doc, ops)
        result = self.run_cmd("apply")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.doc.read_text(encoding="utf-8"), "合并成一行。\n另起一行。\n其他。\n")

    def test_apply_twice_does_not_reapply(self):
        doc = "token 很重要。\n"
        ops = opinions(item("01", "token", "token（词元）"))
        self.write(doc, ops)
        self.assertEqual(self.run_cmd("apply").returncode, 0)
        result = self.run_cmd("apply")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.doc.read_text(encoding="utf-8"), "token（词元） 很重要。\n")
        self.assertEqual(self.ops.read_text(encoding="utf-8").count("- 状态：已应用"), 1)

    def test_partially_applied_file(self):
        doc = "已经改好。\n还没改。\n"
        ops = opinions(
            item("01", "原来的。", "已经改好。", status="已应用"),
            item("02", "还没改。", "现在改了。"),
        )
        self.write(doc, ops)
        result = self.run_cmd("apply")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.doc.read_text(encoding="utf-8"), "已经改好。\n现在改了。\n")
        self.assertEqual(self.ops.read_text(encoding="utf-8").count("- 状态：已应用"), 2)

    def test_skipped_items_quotes_not_checked(self):
        doc = "要改的句子。\n"
        ops = opinions(
            item("01", "要改的句子。", "改好的句子。"),
            item("02", "文档里没有这句。", "随便。", decision="不改"),
            item("03", "文档里也没有。", "随便。", decision=""),
        )
        self.write(doc, ops)
        result = self.run_cmd("apply")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.doc.read_text(encoding="utf-8"), "改好的句子。\n")


class RejectTest(Case):
    def test_quote_not_found(self):
        self.assert_rejected("实际的句子。\n", opinions(item("01", "不存在的句子。", "新句子。")), "01", "找不到")

    def test_quote_not_unique(self):
        self.assert_rejected("重复。重复。\n", opinions(item("01", "重复。", "不重复。")), "01", "2 次")

    def test_overlapping_quotes(self):
        doc = "一二三四五。\n"
        ops = opinions(item("01", "一二三", "甲"), item("02", "三四五", "乙"))
        self.assert_rejected(doc, ops, "01", "02", "重叠")

    def test_bad_decision(self):
        self.assert_rejected("句子。\n", opinions(item("01", "句子。", "新句子。", decision="同意")), "01", "决定")

    def test_empty_rewrite(self):
        self.assert_rejected("句子。\n", opinions(item("01", "句子。", "新句子。", decision="改为：")), "01", "决定")

    def test_missing_field(self):
        ops = opinions(item("01", "句子。", "新句子。").replace("- 理由：读不通\n", ""))
        self.assert_rejected("句子。\n", ops, "01", "理由")

    def test_duplicate_field(self):
        ops = opinions(item("01", "句子。", "新句子。").replace("- 理由：读不通\n", "- 理由：读不通\n- 理由：又一遍\n"))
        self.assert_rejected("句子。\n", ops, "01", "理由")

    def test_unknown_field(self):
        ops = opinions(item("01", "句子。", "新句子。") + "- 备注：多余\n")
        self.assert_rejected("句子。\n", ops, "01", "备注")

    def test_stray_line(self):
        ops = opinions(item("01", "句子。", "新句子。") + "接在后面的游离文字\n")
        self.assert_rejected("句子。\n", ops, "01", "游离文字")

    def test_duplicate_number(self):
        doc = "甲。乙。\n"
        ops = opinions(item("01", "甲。", "丙。"), item("01", "乙。", "丁。"))
        self.assert_rejected(doc, ops, "01", "重复")

    def test_bad_heading(self):
        ops = opinions(item("01", "句子。", "新句子。").replace("## 01 语病", "## 第一条"))
        self.assert_rejected("句子。\n", ops, "第一条")

    def test_empty_suggestion(self):
        self.assert_rejected("句子。\n", opinions(item("01", "句子。", "")), "01", "建议")

    def test_suggestion_same_as_quote(self):
        self.assert_rejected("句子。\n", opinions(item("01", "句子。", "句子。")), "01", "相同")

    def test_one_bad_item_blocks_all_writes(self):
        doc = "好的句子。\n"
        ops = opinions(item("01", "好的句子。", "更好的句子。"), item("02", "没有这句。", "新句子。"))
        self.assert_rejected(doc, ops, "02")


class MissingFileTest(Case):
    def test_missing_target(self):
        self.ops.write_text(opinions(item("01", "句子。", "新句子。")), encoding="utf-8")
        for cmd in ("check", "apply"):
            result = self.run_cmd(cmd, doc=self.dir / "nope.md")
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn("nope.md", result.stderr)

    def test_missing_opinions(self):
        self.doc.write_text("句子。\n", encoding="utf-8")
        for cmd in ("check", "apply"):
            result = self.run_cmd(cmd, ops=self.dir / "nope.md")
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn("nope.md", result.stderr)


if __name__ == "__main__":
    unittest.main()
