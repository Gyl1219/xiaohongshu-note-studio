# 小红书笔记工坊 / xiaohongshu-note-studio

一个可直接提交到华为云 Skill 市场的 Agent Skill：把真实的生活经历加工成能发的小红书笔记，并在发布前做确定性自检。

## 目录结构

```
xiaohongshu-note-studio/
├── SKILL.md                      # 技能入口：工作流 + 质量红线 + 资源导航
├── references/
│   ├── writing-rules.md          # 8 个标题公式、开头钩子、正文结构、标签策略、限流原因
│   ├── templates.md              # 8 个场景模板（探店/测评/教程/避坑/清单/plog/吐槽/经验帖）
│   └── ban-words.md              # 9 类违禁词与高风险词，每条附改写方向
├── scripts/
│   └── xhs_check.py              # 发布前自检，标准库零依赖
├── assets/
│   └── note-template.md          # 落盘骨架
└── examples/
    ├── good-note.md              # 合规示例（自检 PASS）
    └── bad-note.md               # 违规示例（自检 FAIL，13 个 error）
```

## 用法

在支持 Agent Skills 的客户端里把本目录放进 `skills/` 下，技能名 `xiaohongshu-note-studio`。触发词："写小红书 / 发笔记 / 笔记文案 / 种草文 / 探店笔记 / 笔记自检"。

单独跑自检：

```bash
python scripts/xhs_check.py examples/good-note.md          # 人类可读报告
python scripts/xhs_check.py examples/bad-note.md --json    # JSON，给程序用
python scripts/xhs_check.py note.md --strict               # warn 也算 error
python scripts/xhs_check.py note.md --ban-words my-bans.txt # 追加自己的词表
```

退出码：`0` 通过，`2` 有 error，`1` 输入错误。

## 输入格式

- `.md` / `.txt`：第一行是标题，其余是正文，`#话题` 自动识别为标签。
- `.json`：`{"title": "...", "body": "...", "tags": ["#a", "#b"]}`

## 自检项

| 检查 | 阈值 | 级别 |
| --- | --- | --- |
| 标题字数 | 8–20 通过，21–24 warn，>24 error | warn/error |
| 正文字数 | 150–1200，越界 warn | warn |
| 段落行数 | 每段 ≤3 行，超出 warn | warn |
| 段落字数 | 每段 ≤120 字，超出 warn | warn |
| 标签数量 | 3–10，0 个 error | warn/error |
| 标签长度 | 去 `#` 后 ≥2 字 | error |
| 违禁词 | 9 类子串匹配，含行号 | error/warn |
| emoji 密度 | ≤8 个/百字 | warn |
| emoji 堆叠 | 连续 ≥3 个 | warn |
| 求互动套话 | 8 条固定短语 | warn |
| 外链 | 出现 http/www 即提示 | warn |

## 设计取舍

- **只做确定性检查**。语法、语气、故事性这些交给模型判断，脚本不装作能评"写得好不好"。
- **词表带原因**。`ban-words.md` 每类都写了为什么禁、往哪个方向改，避免改成同义词后照样违规。
- **不编事实**。SKILL.md 明确要求素材缺失时先问，不许拿想象补体验——这也是平台最容易被判"虚假种草"的地方。
- **零依赖**。`xhs_check.py` 只用标准库，Windows / macOS / Linux 直接跑。

## 许可

MIT。