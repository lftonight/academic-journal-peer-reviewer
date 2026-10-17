"""Offline candidate scan + human/model semantic review record; never edits prose."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re

SCHEMA_VERSION = 1
CHECKS = (
    "skill_loaded", "content_preserved", "evidence_preserved", "citations_preserved",
    "no_selective_reporting", "paragraph_endings_reviewed", "genre_preserved",
)
REASONS = {
    "scientific_scope", "uncertainty", "statistical_or_negative_fact",
    "journal_requirement", "necessary_comparison", "verbatim_quote",
}
LIMITS = (
    "候选扫描 + 人工/模型语义审查记录；PASS仅表示记录满足门控条件，"
    "不代表机器理解了全部文风、核实了理由或保证科学正确。"
)
PATTERNS = {
    "defensive_stock_phrase": re.compile(
        r"遗憾的是|(?:必须|需要)承认|这(?:并)?不意味着|"
        r"(?:应|需要|必须)谨慎(?:理解|解读|看待)|"
        r"不能(?:由此|据此|因此)(?:得出|推断|证明)|"
        r"(?:但|但是|然而)[^。！？!?\n]{0,30}?(?:(?:不能|不足以)(?:说明|证明)|不代表)|"
        r"\bunfortunately\b|\bmerely\b|\bonly achiev(?:e|es|ed)\b|"
        r"\bit (?:must|should) be (?:noted|acknowledged)\b|"
        r"\bwe (?:must|should) acknowledge\b|"
        r"\bthis does not (?:mean|imply|prove)\b|"
        r"\b(?:these|the|our) (?:findings|results|observations) "
        r"(?:do|does) not (?:establish|demonstrate|prove|imply|mean)\b|"
        r"\b(?:should|must) be interpreted with caution\b", re.I),
    "not_but_frame": re.compile(r"(?:不是|并非).{1,160}?而是|\bnot\b.{1,160}?\bbut\b", re.I | re.S),
}
END_PATTERN = re.compile(
    r"仍需(?:进一步)?(?:研究|验证|探索)|有待(?:进一步)?(?:研究|验证)|"
    r"(?:不能|不宜)(?:直接)?(?:推广|外推)|不代表(?:所有|全部|普遍)|具有一定局限性|仅供参考|"
    r"\bfurther (?:research|studies|validation) (?:is|are) (?:needed|required)\b|"
    r"\bcannot be generaliz(?:ed|able)\b", re.I)


def scan(text):
    """Find candidates, not errors. Necessary qualifiers alone never trigger."""
    found = []
    for pno, paragraph in enumerate(re.split(r"\n\s*\n", text), 1):
        sentences = [s.strip() for s in re.split(
            r"(?<=[。！？!?])|(?<=\.)(?=\s+[A-Z]|\s*$)", paragraph) if s.strip()]
        for sno, sentence in enumerate(sentences, 1):
            rules = [name for name, pattern in PATTERNS.items() if pattern.search(sentence)]
            if sno == len(sentences) and END_PATTERN.search(sentence):
                rules.append("paragraph_end_disclaimer")
            if rules:
                digest = hashlib.sha256(sentence.encode("utf-8")).hexdigest()[:12]
                found.append({"id": f"p{pno:04d}s{sno:03d}-{digest}",
                              "paragraph": pno, "sentence": sentence, "rules": rules})
    return found


def read_text(path):
    raw = Path(path).read_bytes()
    return raw.decode("utf-8-sig"), hashlib.sha256(raw).hexdigest()


def protect_output(output, sources):
    dest = Path(output).resolve()
    for source in sources:
        src = Path(source).resolve()
        same = os.path.normcase(str(dest)) == os.path.normcase(str(src))
        if not same and dest.exists() and src.exists():
            same = os.path.samefile(dest, src)
        if same:
            raise ValueError("输出路径不能覆盖文本输入或审查文件（包括链接别名）。")


def write_json(path, value, sources, exclusive=False):
    protect_output(path, sources)
    with Path(path).open("x" if exclusive else "w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def review_issues(record, digest, candidates):
    issues = []
    if not isinstance(record, dict):
        return ["review_structure: 审查记录必须为JSON对象。"]
    if type(record.get("schema_version")) is not int or record["schema_version"] != SCHEMA_VERSION:
        issues.append("schema_version: 审查版本必须为1。")
    if record.get("text_sha256") != digest:
        issues.append("hash_mismatch: 文本已变化，须针对当前文本重新prepare和审查。")
    checks = record.get("checks")
    if not isinstance(checks, dict):
        checks = {}
    for name in CHECKS:
        if checks.get(name) is not True:
            issues.append(f"check_not_confirmed: {name} 必须经实际审查后设为true。")
    reviews = record.get("candidates")
    if not isinstance(reviews, list):
        return issues + ["candidate_structure: candidates必须为逐项审查数组。"]
    expected = {item["id"]: item for item in candidates}
    seen = set()
    for item in reviews:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            issues.append("candidate_structure: 候选项缺少有效ID。")
            continue
        cid = item["id"]
        if cid in seen or cid not in expected:
            issues.append(f"candidate_id: 重复或未知候选ID {cid}；不支持通配或批量忽略。")
            continue
        seen.add(cid)
        if item.get("sentence") != expected[cid]["sentence"] or item.get("rules") != expected[cid]["rules"]:
            issues.append(f"candidate_changed: {cid} 的候选原句或规则被改动。")
        reason = item.get("reason_type")
        explanation = item.get("explanation", "")
        if not isinstance(reason, str) or reason not in REASONS:
            issues.append(f"candidate_unresolved: {cid} 必须使用允许的具体保留理由类型。")
        normalized = re.sub(r"\s+", "", explanation) if isinstance(explanation, str) else ""
        if len(normalized) < 12 or re.search(
                r"(?:全部|所有候选|一律|批量)(?:忽略|放行|保留)|ignore\s+all|keep\s+all|bulk\s+approve",
                explanation if isinstance(explanation, str) else "", re.I):
            issues.append(f"candidate_unresolved: {cid} 须逐项解释实际内容（至少12个非空白字符），不能批量放行。")
    for cid in expected:
        if cid not in seen:
            issues.append(f"candidate_unresolved: 缺少 {cid} 的保留理由；修改正文后须重新prepare。")
    return issues


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    sub = root.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare", help="生成未通过的审查模板；不会自动放行")
    prep.add_argument("--text", required=True, help="UTF-8 txt/md文件")
    prep.add_argument("--review-out", required=True, help="新审查JSON；拒绝覆盖已有文件")
    check = sub.add_parser("check", help="校验哈希、逐项确认与候选保留理由")
    check.add_argument("--text", required=True)
    check.add_argument("--review", required=True)
    check.add_argument("--report", required=True)
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    sources = [args.text] + ([args.review] if args.command == "check" else [])
    output = args.report if args.command == "check" else args.review_out
    outcome = {"schema_version": SCHEMA_VERSION, "status": "ERROR", "limitations": LIMITS}
    try:
        protect_output(output, sources)
        text, digest = read_text(args.text)
        candidates = scan(text)
        outcome.update(text_sha256=digest, candidate_count=len(candidates), candidates=candidates)
        if args.command == "prepare":
            record = {"schema_version": SCHEMA_VERSION, "text_sha256": digest,
                      "checks": dict.fromkeys(CHECKS, False), "limitations": LIMITS,
                      "candidates": [dict(item, reason_type="", explanation="") for item in candidates]}
            write_json(output, record, sources, exclusive=True)
            outcome.update(status="BLOCKED", issues=["审查模板已生成；所有必需语义检查均未确认。"])
            print(json.dumps(outcome, ensure_ascii=False))
            return 0  # 模板生成成功，绝非正文通过。
        try:
            review_text, _ = read_text(args.review)
        except FileNotFoundError:
            issues = ["review_missing: 缺少审查记录；先prepare，再完成实际语义审查。"]
        else:
            issues = review_issues(json.loads(review_text), digest, candidates)
        outcome.update(status="BLOCKED" if issues else "PASS", issues=issues)
    except (OSError, UnicodeError, ValueError) as exc:
        outcome.update(status="ERROR", issues=[f"{type(exc).__name__}: {exc}"])
    if args.command == "check":
        try:
            write_json(output, outcome, sources)
        except (OSError, ValueError) as exc:
            outcome["status"] = "ERROR"
            outcome.setdefault("issues", []).append(f"report_write_error: {type(exc).__name__}: {exc}")
    print(json.dumps(outcome, ensure_ascii=False))
    return {"PASS": 0, "BLOCKED": 1, "ERROR": 2}[outcome["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
