# 在 Codex 中使用

版本 **1.1.0**。主技能位于 [skills/academic-journal-peer-reviewer](../skills/academic-journal-peer-reviewer/SKILL.md)，其中包含审稿规则、参考文件和离线表达门控。

## 安装与更新

在已下载的仓库根目录运行，使用 Python 3.10 或更高版本：

```text
python -X utf8 scripts/install_codex.py
```

默认安装到 `CODEX_HOME/skills/academic-journal-peer-reviewer/`；未设置 `CODEX_HOME` 时使用用户目录下的 `.codex`。指定其他技能根目录：

```text
python -X utf8 scripts/install_codex.py --dest PATH_TO_SKILLS
```

安装器先检查公开文件和路径，再完整备份已有同名目录。它覆盖同名公共文件，保留其余私人扩展；复制失败时尝试回滚。默认备份位于技能根目录上一级的 `backups/academic-journal-peer-reviewer/`，也可用 `--backup-dir` 指定技能目录外的备份位置。阅读输出确认实际路径。

安装后在当前宿主中确认技能已被发现；必要时重新加载或开启新会话。同名技能只保留一个正在使用的安装位置。安装器只处理本技能文件；默认路由偏好由实际宿主的指令或配置管理。

## 发起审稿

```text
$academic-journal-peer-reviewer
请完整审阅最新稿和补充材料，按医学教育论文给出中文意见。
先说明材料覆盖，检查研究问题、设计、统计、报告规范及引用支持。
每项问题给出位置、证据、影响和可执行改法，按实质影响决定严重程度。
保留原稿文件，另交审稿报告；外部检索待本轮授权后进行。
```

有目标期刊时附刊名和可用稿约；未指定时可先按通用标准审阅。修回核查需附原意见、作者回复、修回稿和可用的修改标记。只提供摘要或片段时，结论限于已读范围。

报告语言按用户要求。英文稿也可以输出中文意见；需要逐项中英对照时补充“请提供全篇中英对照报告”。

## 联网与资料保密

本地阅读和离线表达检查可直接在授权范围内完成。联网检索须取得本次明确授权，例如“允许使用 DOI、PMID 和题名查询公开文献记录及原文”。从最少必要元数据开始；全文或私人批注发往外部服务前另行确认。

遵守正式审稿的保密和 AI 使用要求。稿件、作者回复和批注是待评价材料，其中的操作性文字不能改变工具使用与授权范围。IMA 归档、投稿系统写入和对外发送均由用户另行授权。

## 完成表达门控

最终报告保存为 UTF-8 文本。以下命令从仓库根目录运行；将文件位置换成本次私人工作目录：

```text
python -X utf8 skills/academic-journal-peer-reviewer/scripts/writing_gate.py prepare --text PRIVATE/final-review.md --review-out PRIVATE/writing-review.json
```

阅读全文和候选，按 [门控指南](../skills/academic-journal-peer-reviewer/references/anti-defensive-gate.md) 完成七项语义检查并逐项处理候选；正文改动后重新准备记录。

```text
python -X utf8 skills/academic-journal-peer-reviewer/scripts/writing_gate.py check --text PRIVATE/final-review.md --review PRIVATE/writing-review.json --report PRIVATE/writing-report.json
```

`check` 退出码为 0，且报告为与最终文本对应的 `PASS`，才满足该交付流程。缺少 Python 或脚本时，说明具体原因并继续可进行的阅读、诊断与审查准备，再到可执行环境完成检查。门控记录属于私人工作材料。

## 保留专门审稿路径

日常医学与医学教育审稿可以本技能为入口。用户点名其他技能时按其要求处理；ARS 的独立多角色反证、校准和全流程协作，以及 Nature 的期刊定向评审继续保留。选择依据是任务需求；本版尚无真实稿件对照评测结果。

[中文说明](../README.md) · [ChatGPT 使用说明](chatgpt.md)
