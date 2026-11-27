# /// script
# requires-python = ">=3.11"
# ///
"""审校意见的检查与应用，只用标准库。

  uv run proofread.py check <目标文档> <意见文件>
  uv run proofread.py apply <目标文档> <意见文件>

check：检查意见文件的结构和决定栏；对每条要应用的意见（决定为「采纳」或「改为：…」，且还没标已应用），
       检查原文在目标文档里恰好出现一次，各条原文的区间互不重叠。有问题时逐条列出，退出码 1。
apply：先做同样的检查，有问题时两个文件都不写。通过后按各条原文在目标文档里的原始位置一次替换完，
       互不影响；再在这些意见的决定栏下一行加「- 状态：已应用」。已应用的意见跳过，重复运行不会重复替换。

目标文档或意见文件不存在时退出码 2。意见文件的格式见本 skill 的 SKILL.md「意见文件」一节；
原文、建议和改为后的文字里，⏎ 表示换行。
"""

import re
import sys
from pathlib import Path

FIELDS = ("位置", "原文", "建议", "理由", "决定", "状态")
REQUIRED = FIELDS[:5]
APPLIED = "已应用"
HEADING = re.compile(r"^## (\d+) (\S.*)$")
FIELD = re.compile(r"^- ([^：\s]+)：(.*)$")


def read(path):
    try:
        with open(path, encoding="utf-8", newline="") as f:
            return f.read()
    except FileNotFoundError:
        print(f"文件不存在：{path}", file=sys.stderr)
        sys.exit(2)


def write(path, text):
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def short(text, n=40):
    text = text.replace("\n", "⏎")
    return text if len(text) <= n else text[:n] + "…"


def parse(lines):
    """把意见文件拆成意见列表，返回 (意见, 错误)。第一个 ## 标题之前的内容不解析。"""
    items, errors, current = [], [], None
    for i, line in enumerate(lines):
        if line.startswith("## "):
            m = HEADING.match(line)
            if not m:
                errors.append(f"标题格式不对，应为「## 编号 标签」：{line}")
                current = None
                continue
            current = {"num": m.group(1), "fields": {}, "line": i}
            items.append(current)
        elif current is None or not line.strip():
            continue
        elif m := FIELD.match(line):
            name, value = m.groups()
            if name not in FIELDS:
                errors.append(f"{current['num']}：不认识的字段「{name}」")
            elif name in current["fields"]:
                errors.append(f"{current['num']}：字段「{name}」重复")
            else:
                current["fields"][name] = (value, i)
        else:
            errors.append(f"{current['num']}：无法识别的行「{short(line)}」")
    return items, errors


def replacement(item, errors):
    """要应用时返回替换文字，不改、留空或已应用时返回 None。"""
    num, fields = item["num"], item["fields"]
    decision = fields["决定"][0].strip()
    status = fields.get("状态", ("", 0))[0].strip()
    if "状态" in fields and status != APPLIED:
        errors.append(f"{num}：状态只能写「{APPLIED}」，现在是「{status}」")
    if decision.startswith("改为："):
        text = decision[len("改为："):]
        if not text.strip():
            errors.append(f"{num}：决定栏「改为：」后面为空")
            return None
    elif decision == "采纳":
        text = fields["建议"][0]
    elif decision in ("不改", ""):
        return None
    else:
        errors.append(f"{num}：决定栏写法不对「{decision}」，应为 采纳、不改、改为：<文字>，或留空")
        return None
    return None if status == APPLIED else text.replace("⏎", "\n")


def check(doc, lines):
    """返回 (要应用的 [(起, 止, 替换文字, 意见)], 意见, 错误)。"""
    items, errors = parse(lines)
    seen = set()
    for item in items:
        num, fields = item["num"], item["fields"]
        if num in seen:
            errors.append(f"{num}：编号重复")
        seen.add(num)
        missing = [name for name in REQUIRED if name not in fields]
        for name in missing:
            errors.append(f"{num}：缺少字段「{name}」")
        if missing:
            continue
        quote, suggestion = fields["原文"][0], fields["建议"][0]
        if not quote.strip():
            errors.append(f"{num}：原文为空")
        if not suggestion.strip():
            errors.append(f"{num}：建议为空")
        elif suggestion == quote:
            errors.append(f"{num}：建议与原文相同")
        item["new"] = replacement(item, errors)
        if item["new"] is not None and item["new"] == quote.replace("⏎", "\n"):
            errors.append(f"{num}：改为后的文字与原文相同")

    spans = []
    for item in items:
        if item.get("new") is None or not item["fields"]["原文"][0].strip():
            continue
        quote = item["fields"]["原文"][0].replace("⏎", "\n")
        start = doc.find(quote)
        if start < 0:
            errors.append(f"{item['num']}：原文在目标文档里找不到：{short(quote)}")
        elif doc.find(quote, start + 1) >= 0:
            count = max(doc.count(quote), 2)
            errors.append(f"{item['num']}：原文在目标文档里出现 {count} 次，多复制几个字让它唯一：{short(quote)}")
        else:
            spans.append((start, start + len(quote), item["new"], item))

    spans.sort(key=lambda s: s[0])
    for prev, nxt in zip(spans, spans[1:]):
        if nxt[0] < prev[1]:
            errors.append(f"{prev[3]['num']} 和 {nxt[3]['num']}：原文区间重叠，合并成一条或缩短其中一条")
    return spans, items, errors


def summary(items, spans):
    applied = sum(1 for it in items if it["fields"].get("状态", ("",))[0].strip() == APPLIED)
    return f"共 {len(items)} 条：待应用 {len(spans)} 条，已应用 {applied} 条，不改或留空 {len(items) - len(spans) - applied} 条"


def main(argv):
    if len(argv) != 4 or argv[1] not in ("check", "apply"):
        print(__doc__, file=sys.stderr)
        return 2
    cmd, target, opinions = argv[1], Path(argv[2]), Path(argv[3])
    doc = read(target)
    text = read(opinions)
    lines = text.split("\n")
    spans, items, errors = check(doc, lines)

    if errors:
        print(f"{opinions}：{len(errors)} 处问题，没有写入任何文件")
        for err in errors:
            print(f"- {err}")
        return 1
    if cmd == "check":
        print(f"检查通过。{summary(items, spans)}")
        return 0
    if not spans:
        print(f"没有待应用的意见。{summary(items, spans)}")
        return 0

    before = summary(items, spans)
    for start, end, new, _ in sorted(spans, key=lambda s: s[0], reverse=True):
        doc = doc[:start] + new + doc[end:]
    write(target, doc)

    marks = sorted((it["fields"]["决定"][1] for _, _, _, it in spans), reverse=True)
    for i in marks:
        lines.insert(i + 1, f"- 状态：{APPLIED}")
    write(opinions, "\n".join(lines))
    print(f"已应用 {len(spans)} 条，写入 {target}（应用前：{before}）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
