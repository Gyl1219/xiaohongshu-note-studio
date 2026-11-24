#!/usr/bin/env python3
"""小红书笔记发布前自检。

确定性检查，不做主观打分：
  标题长度 / 正文字数 / 标签数量与格式 / 违禁词与高风险词 /
  emoji 密度与堆叠 / 段落长度 / 求互动套话 / 站外链接

用法：
    python xhs_check.py note.md
    python xhs_check.py note.md --json
    python xhs_check.py note.json --ban-words my-bans.txt --strict

输入格式：
    .md/.txt  第一行是标题，其余是正文；`#话题` 自动识别为标签
    .json     {"title": "...", "body": "...", "tags": ["#a", "#b"]}

退出码：
    0  没有 error 级问题
    2  存在 error 级问题（--strict 下 warn 也算 error）
    1  输入错误（文件不存在、JSON 解析失败等）

仅依赖标准库。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# ---------- 内置词表（与 references/ban-words.md 保持一致） ----------

BUILTIN_WORDS: dict[str, list[str]] = {
    "error:极限词": [
        "最好", "最佳", "最优", "最强", "最高级", "第一", "第一名", "NO.1", "Top1",
        "顶级", "顶尖", "唯一", "独家", "国家级", "世界级", "全球首发",
        "史无前例", "绝无仅有", "万能", "100%", "百分百", "绝对", "永久", "终身",
    ],
    "error:医疗功效": [
        "治疗", "治愈", "根治", "疗效", "药效", "消炎", "抗菌", "杀菌", "抗病毒",
        "祛痘印", "祛斑", "生发", "降血压", "降血糖", "降血脂",
        "助眠", "镇静", "排毒", "瘦身", "燃脂", "减肥", "丰胸", "壮阳",
        "增强免疫力", "防癌", "抗癌",
    ],
    "error:平台敏感与交易": [
        "刷单", "刷量", "刷粉", "代购", "代拍", "返现", "返利", "水军", "控评",
        "内部价", "特殊渠道", "一手货源", "低价出", "私域", "站外",
    ],
    "error:站外引流": [
        "加微信", "加V", "加vx", "vx号", "微信号", "私信我买", "扫码", "二维码",
        "进群", "群号", "手机号", "电话下单", "QQ群", "淘宝搜", "拼多多搜",
    ],
    "error:未成年人": ["长高", "补脑", "提高智力", "增强抵抗力"],
    "error:金融医疗建议": [
        "保本", "稳赚", "包赚", "躺赚", "收益率保证", "零风险", "荐股", "内幕",
        "确诊", "处方", "用药建议",
    ],
    "warn:从众断言": [
        "全网都在用", "所有人都在买", "爆火", "断货王", "卖疯了", "人手一个",
        "99% 的人", "99%的人", "用了都说好",
    ],
    "warn:空心夸赞": [
        "绝了", "yyds", "无敌", "封神", "天花板", "性价比之王",
        "好用到哭", "宝藏", "神仙",
    ],
}

# 求互动套话：平台不鼓励，且会削弱信息密度
ENGAGEMENT_CLICHES = [
    "喜欢就点", "点赞关注", "求关注", "记得点赞", "关注我", "双击", "收藏起来慢慢看",
]

EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U0001F1E6-\U0001F1FF\U00002600-\U000027BF"
    "\U00002B00-\U00002BFF\U0001F900-\U0001F9FF]"
)
TAG_RE = re.compile(r"#[^\s#\[\]【】《》()（）,，。.、!！?？]{1,30}")
URL_RE = re.compile(r"(https?://|www\.)[^\s]+", re.IGNORECASE)

TITLE_MAX_OK = 20
TITLE_MAX_HARD = 24
TITLE_MIN = 8
BODY_MIN = 150
BODY_MAX = 1200
TAGS_MIN = 3
TAGS_MAX = 10
TAG_MIN_LEN = 2
EMOJI_PER_100 = 8
PARA_LINE_MAX = 3
PARA_CHAR_MAX = 120


def load_bans(extra_file: Path | None) -> dict[str, list[str]]:
    """内置词表 + 外部词表（一行一词，# 开头为注释）。"""
    table = {k: list(v) for k, v in BUILTIN_WORDS.items()}
    if extra_file is None:
        return table
    extra: list[str] = []
    for raw in extra_file.read_text(encoding="utf-8-sig").splitlines():
        word = raw.strip()
        if not word or word.startswith("#"):
            continue
        extra.append(word)
    if extra:
        table.setdefault("error:自定义词表", []).extend(extra)
    return table


def parse_input(path: Path) -> dict:
    """返回 {title, body, tags}。"""
    if not path.exists():
        raise FileNotFoundError(f"找不到文件：{path}")
    suffix = path.suffix.lower()
    if suffix == ".json":
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSON 解析失败：{exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("JSON 顶层必须是对象：{\"title\": ..., \"body\": ..., \"tags\": [...]}")
        title = str(data.get("title") or "")
        body = str(data.get("body") or "")
        raw_tags = data.get("tags") or []
        if isinstance(raw_tags, str):
            raw_tags = [raw_tags]
        tags = [str(t) for t in raw_tags]
        if not tags:
            tags = TAG_RE.findall(body)
        return {"title": title, "body": body, "tags": tags}

    text = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    lines = text.split("\n")
    title = ""
    start = 0
    for idx, line in enumerate(lines):
        if line.strip():
            title = line.strip().lstrip("#").strip()
            start = idx + 1
            break
    body = "\n".join(lines[start:])
    return {"title": title, "body": body, "tags": TAG_RE.findall(body)}


def normalize_tag(tag: str) -> str:
    return tag if tag.startswith("#") else "#" + tag


def add(findings: list[dict], level: str, code: str, message: str, hint: str = "") -> None:
    findings.append({"level": level, "code": code, "message": message, "hint": hint})


def visible_len(text: str) -> int:
    """去掉空白后的字符数，中文按 1 计。"""
    return len(re.sub(r"\s+", "", text))


def check_title(finding_list: list[dict], title: str) -> None:
    if not title:
        add(finding_list, "error", "title.missing", "没有标题（.md 的第一行会被当作标题）")
        return
    n = visible_len(title)
    if n > TITLE_MAX_HARD:
        add(finding_list, "error", "title.too_long",
            f"标题 {n} 字，超过 {TITLE_MAX_HARD} 字",
            f"压到 {TITLE_MAX_OK} 字以内；信息流会截断长标题")
    elif n > TITLE_MAX_OK:
        add(finding_list, "warn", "title.long",
            f"标题 {n} 字，建议 ≤{TITLE_MAX_OK} 字",
            "把括号里的补充信息挪到正文第一段")
    elif n < TITLE_MIN:
        add(finding_list, "warn", "title.short", f"标题只有 {n} 字，信息量偏少",
            "补一个数字、场景或结果")


def check_body(finding_list: list[dict], body: str) -> None:
    n = visible_len(body)
    if n == 0:
        add(finding_list, "error", "body.empty", "正文为空")
        return
    if n < BODY_MIN:
        add(finding_list, "warn", "body.short", f"正文 {n} 字，偏短（{BODY_MIN}–{BODY_MAX} 较稳）",
            "补一个具体细节或一条可执行步骤")
    elif n > BODY_MAX:
        add(finding_list, "warn", "body.long", f"正文 {n} 字，超过 {BODY_MAX} 字",
            "拆成分点，或把过程细节挪到评论区补充")


def check_paragraphs(finding_list: list[dict], body: str) -> None:
    blocks = [b for b in re.split(r"\n\s*\n", body) if b.strip()]
    for i, block in enumerate(blocks, start=1):
        lines = [ln for ln in block.split("\n") if ln.strip()]
        if len(lines) > PARA_LINE_MAX:
            add(finding_list, "warn", "para.too_many_lines",
                f"第 {i} 段有 {len(lines)} 行，超过 {PARA_LINE_MAX} 行",
                "在句意断开处空一行")
        if visible_len(block) > PARA_CHAR_MAX:
            add(finding_list, "warn", "para.too_long",
                f"第 {i} 段 {visible_len(block)} 字，超过 {PARA_CHAR_MAX} 字",
                "拆成两段，或删掉铺垫句")


def check_tags(finding_list: list[dict], tags: list[str]) -> None:
    seen: set[str] = set()
    for tag in tags:
        norm = normalize_tag(tag.strip())
        if norm in seen:
            add(finding_list, "warn", "tag.duplicate", f"重复标签 {norm}")
            continue
        seen.add(norm)
        if visible_len(norm) - 1 < TAG_MIN_LEN:
            add(finding_list, "error", "tag.too_short",
                f"标签 {norm} 只有 {visible_len(norm) - 1} 字，检索意义不大")
    count = len(seen)
    if count == 0:
        add(finding_list, "error", "tag.missing", "没有话题标签",
            "至少给 3 个：1 个大词 + 2 个精准词")
    elif count < TAGS_MIN:
        add(finding_list, "warn", "tag.few", f"标签 {count} 个，建议 {TAGS_MIN}–{TAGS_MAX} 个")
    elif count > TAGS_MAX:
        add(finding_list, "warn", "tag.many", f"标签 {count} 个，超过 {TAGS_MAX} 个会稀释权重")


def check_bans(finding_list: list[dict], text: str, bans: dict[str, list[str]]) -> None:
    for key, words in bans.items():
        level = key.split(":", 1)[0]
        category = key.split(":", 1)[1]
        for word in words:
            for hit in re.finditer(re.escape(word), text):
                line_no = text.count("\n", 0, hit.start()) + 1
                add(finding_list, level, f"ban.{category}",
                    f"命中「{word}」（正文第 {line_no} 行）",
                    "按 references/ban-words.md 该分类的改写方向换词，不要只做同义词替换")


def check_emoji(finding_list: list[dict], body: str) -> None:
    marks = [m.group(0) for m in EMOJI_RE.finditer(body)]
    n = visible_len(body)
    if n == 0:
        return
    density = len(marks) / n * 100
    if density > EMOJI_PER_100:
        add(finding_list, "warn", "emoji.density",
            f"emoji 密度 {density:.1f}/百字，超过 {EMOJI_PER_100}",
            "只保留段首和并列项前的 emoji")
    runs = re.findall(r"(?:[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U00002B00-\U00002BFF\U0001F900-\U0001F9FF][\ufe0f\u200d]*){3,}", body)
    if runs:
        add(finding_list, "warn", "emoji.stack",
            f"有 {len(runs)} 处连续 3 个以上 emoji",
            "连堆的 emoji 会被当成模板文案")


def check_misc(finding_list: list[dict], text: str, body: str) -> None:
    for phrase in ENGAGEMENT_CLICHES:
        if phrase in text:
            add(finding_list, "warn", "engagement.cliche", f"出现求互动套话「{phrase}」",
                "结尾改成可执行的下一步（去哪买、先改哪个动作）")
    urls = URL_RE.findall(text)
    if urls:
        add(finding_list, "warn", "link.outbound",
            f"正文含 {len(urls)} 个外链", "外链易触发限流，改成平台内商品名或搜索词")
    if re.search(r"[（(]\s*广告\s*[）)]|#广告|#赞助", body):
        add(finding_list, "info", "ad.label", "已标注广告/赞助，合规")


def build_report(path: Path, data: dict, bans: dict[str, list[str]], strict: bool) -> dict:
    findings: list[dict] = []
    title, body, tags = data["title"], data["body"], data["tags"]
    check_title(findings, title)
    check_body(findings, body)
    check_paragraphs(findings, body)
    check_tags(findings, tags)
    check_bans(findings, title + "\n" + body, bans)
    check_emoji(findings, body)
    check_misc(findings, title + "\n" + body, body)

    if strict:
        for f in findings:
            if f["level"] == "warn":
                f["level"] = "error"
                f["code"] += ".strict"

    errors = [f for f in findings if f["level"] == "error"]
    warns = [f for f in findings if f["level"] == "warn"]
    infos = [f for f in findings if f["level"] == "info"]
    return {
        "file": str(path),
        "stats": {
            "title_chars": visible_len(title),
            "body_chars": visible_len(body),
            "paragraphs": len([b for b in re.split(r"\n\s*\n", body) if b.strip()]),
            "tags": len({normalize_tag(t.strip()) for t in tags}),
            "emoji": len(EMOJI_RE.findall(body)),
        },
        "summary": {
            "error": len(errors),
            "warn": len(warns),
            "info": len(infos),
            "verdict": "FAIL" if errors else ("PASS_WITH_WARNINGS" if warns else "PASS"),
        },
        "findings": errors + warns + infos,
    }


def print_human(report: dict) -> None:
    s = report["stats"]
    v = report["summary"]
    print(f"文件：{report['file']}")
    print(f"标题 {s['title_chars']} 字 | 正文 {s['body_chars']} 字 | 段落 {s['paragraphs']} "
          f"| 标签 {s['tags']} 个 | emoji {s['emoji']} 个")
    print(f"结论：{v['verdict']}  (error {v['error']} / warn {v['warn']} / info {v['info']})")
    if not report["findings"]:
        print("没有发现问题。")
        return
    print()
    mark = {"error": "[ERROR]", "warn": "[WARN ]", "info": "[INFO ]"}
    for f in report["findings"]:
        print(f"{mark[f['level']]} {f['message']}")
        if f["hint"]:
            print(f"        → {f['hint']}")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="小红书笔记发布前自检（字数/标签/违禁词/emoji/段落）")
    parser.add_argument("path", help="笔记文件（.md / .txt / .json）")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--ban-words", dest="ban_words", default=None,
                        help="追加违禁词表（UTF-8，一行一词，# 开头为注释）")
    parser.add_argument("--strict", action="store_true", help="把 warn 提升为 error")
    args = parser.parse_args(argv)

    path = Path(args.path)
    try:
        data = parse_input(path)
    except (FileNotFoundError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    extra = Path(args.ban_words) if args.ban_words else None
    if extra is not None and not extra.exists():
        print(f"[ERROR] 找不到词表：{extra}", file=sys.stderr)
        return 1

    report = build_report(path, data, load_bans(extra), args.strict)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_human(report)
    return 2 if report["summary"]["error"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))