"""Behavioral checks for the offline writing gate; stdlib only."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest

import writing_gate as gate


class WritingGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.text = self.root / "draft.md"
        self.review = self.root / "review.json"
        self.report = self.root / "report.json"

    def run_cli(self, *args):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = gate.main(list(map(str, args)))
        return code, json.loads(stdout.getvalue())

    def prepare(self, text):
        self.text.write_text(text, encoding="utf-8")
        code, output = self.run_cli("prepare", "--text", self.text, "--review-out", self.review)
        self.assertEqual(code, 0)
        self.assertEqual(output["status"], "BLOCKED")
        record = json.loads(self.review.read_text(encoding="utf-8"))
        self.assertTrue(all(value is False for value in record["checks"].values()))
        return record

    def save_review(self, record, resolve=False):
        record["checks"] = dict.fromkeys(gate.CHECKS, True)
        if resolve:
            for item in record["candidates"]:
                item.update(reason_type="uncertainty",
                            explanation="跨领域泛化尚未评估，这一未知状态限定本句结果的适用范围。")
        self.review.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")

    def check(self, **kwargs):
        return self.run_cli("check", "--text", self.text, "--review", self.review,
                            "--report", kwargs.get("report", self.report))

    def test_no_defense_requires_semantic_review_then_passes(self):
        record = self.prepare("本研究纳入20份教学材料。教师完成独立评分。")
        self.assertEqual(record["candidates"], [])
        self.assertEqual(self.check()[0], 1)
        self.save_review(record)
        code, report = self.check()
        self.assertEqual((code, report["status"]), (0, "PASS"))
        self.assertIn("不代表机器理解", report["limitations"])

    def test_obvious_stock_phrases_and_ids_are_deterministic(self):
        text = "必须承认，这并不意味着所有问题均已解决。仍需进一步验证。\n\nThis does not imply universal benefit."
        candidates = gate.scan(text)
        self.assertEqual(candidates, gate.scan(text))
        self.assertGreaterEqual(len(candidates), 3)
        self.assertEqual(len({item["id"] for item in candidates}), len(candidates))
        self.assertTrue(any("paragraph_end_disclaimer" in item["rules"] for item in candidates))

    def test_paragraph_tail_but_cannot_show_is_a_candidate(self):
        for ending in ("但不能说明所有教学问题已经解决", "但这些发现不能说明教学能力提高",
                       "但这些发现不能证明教学能力提高", "但这些发现不代表教学能力提高"):
            with self.subTest(ending=ending):
                candidates = gate.scan(f"本次评分支持反馈，{ending}。")
                self.assertEqual(len(candidates), 1)
                self.assertIn("defensive_stock_phrase", candidates[0]["rules"])

    def test_explicit_english_weakening_and_reference_footer_are_candidates(self):
        for text in ("Unfortunately, the result was limited.", "The system merely records responses.",
                     "The model only achieves 65% accuracy.", "本次建议仅供参考。"):
            with self.subTest(text=text):
                self.assertEqual(len(gate.scan(text)), 1)

    def test_scientific_uncertainty_and_negative_facts_alone_are_not_candidates(self):
        text = ("本组样本提示X可能与Y相关。未检出差异。机制尚不明确。"
                "本研究不能推断因果关系。该量表仅适用于本组对象。\n\n"
                "X may be associated with Y. These results suggest an association. "
                "X might vary. No statistically significant difference was detected. "
                "This measure only applies to the sampled population.")
        self.assertEqual(gate.scan(text), [])

    def test_not_but_frames_are_candidates_not_automatic_errors(self):
        candidates = gate.scan("该指标不是能力总分，而是过程记录。\n\nThis is not efficacy but feasibility.")
        self.assertEqual(len(candidates), 2)
        self.assertTrue(all("not_but_frame" in item["rules"] for item in candidates))

    def test_finding_based_causal_closing_requires_explicit_review(self):
        sentence = "These findings do not establish that verification caused higher quality."
        candidates = gate.scan(sentence)
        self.assertEqual(len(candidates), 1)
        self.assertIn("defensive_stock_phrase", candidates[0]["rules"])
        record = self.prepare(sentence)
        self.save_review(record)
        self.assertEqual(self.check()[0], 1)
        record["candidates"][0].update(
            reason_type="uncertainty",
            explanation="This statement retains the manuscript's explicit unestablished causal inference; it must not be deleted as empty caution.")
        self.save_review(record)
        self.assertEqual(self.check()[0], 0)

    def test_missing_review_is_blocked_and_reported(self):
        self.text.write_text("评分已完成。", encoding="utf-8")
        code, report = self.check()
        self.assertEqual((code, report["status"]), (1, "BLOCKED"))
        self.assertIn("review_missing", report["issues"][0])
        self.assertTrue(self.report.exists())

    def test_hash_change_invalidates_review(self):
        record = self.prepare("评分已完成。")
        self.save_review(record)
        self.text.write_text("评分已完成，已交叉核对。", encoding="utf-8")
        code, report = self.check()
        self.assertEqual(code, 1)
        self.assertTrue(any("hash_mismatch" in issue for issue in report["issues"]))

    def test_unresolved_candidate_blocks_and_scientific_reason_passes(self):
        record = self.prepare("这些结果应谨慎解读，跨领域泛化尚未评估。")
        self.assertEqual(len(record["candidates"]), 1)
        self.save_review(record)
        self.assertEqual(self.check()[0], 1)
        self.save_review(record, resolve=True)
        self.assertEqual(self.check()[0], 0)

    def test_boolean_strings_and_unsupported_reason_do_not_pass(self):
        record = self.prepare("这些结果应谨慎解读，跨领域泛化尚未评估。")
        self.save_review(record, resolve=True)
        record["checks"]["skill_loaded"] = "true"
        record["candidates"][0]["reason_type"] = "style_preference"
        self.review.write_text(json.dumps(record), encoding="utf-8")
        code, report = self.check()
        self.assertEqual(code, 1)
        self.assertTrue(any("skill_loaded" in issue for issue in report["issues"]))
        self.assertTrue(any("理由类型" in issue for issue in report["issues"]))

    def test_unknown_duplicate_and_blanket_reasons_cannot_bypass_gate(self):
        record = self.prepare("这些结果应谨慎解读，跨领域泛化尚未评估。")
        self.save_review(record, resolve=True)
        record["candidates"][0]["explanation"] = "全部忽略，本次所有候选均保留不需要实际解释。"
        record["candidates"].append(dict(record["candidates"][0]))
        record["candidates"].append({"id": "*", "reason_type": "style", "explanation": "无需修改"})
        self.save_review(record)
        code, report = self.check()
        self.assertEqual(code, 1)
        self.assertTrue(any("不支持通配" in issue for issue in report["issues"]))
        self.assertTrue(any("不能批量放行" in issue for issue in report["issues"]))

    def test_invalid_utf8_and_invalid_json_return_error(self):
        self.text.write_bytes(b"\xff\xfe")
        self.assertEqual(self.check()[1]["status"], "ERROR")
        self.text.write_text("记录完整。", encoding="utf-8")
        self.review.write_text("{broken", encoding="utf-8")
        self.assertEqual(self.check()[1]["status"], "ERROR")

    def test_outputs_cannot_overwrite_text_review_or_link_alias(self):
        record = self.prepare("记录完整。")
        self.save_review(record)
        text_before, review_before = self.text.read_bytes(), self.review.read_bytes()
        self.assertEqual(self.check(report=self.text)[0], 2)
        self.assertEqual(self.check(report=self.review)[0], 2)
        self.assertEqual(self.run_cli("prepare", "--text", self.text,
                                     "--review-out", self.text)[0], 2)
        self.assertEqual(self.text.read_bytes(), text_before)
        self.assertEqual(self.review.read_bytes(), review_before)
        alias = self.root / "alias.json"
        try:
            os.link(self.text, alias)
        except OSError:
            return  # 链接创建不可用时，前面的路径保护检查仍已执行。
        self.assertEqual(self.check(report=alias)[0], 2)
        self.assertEqual(self.text.read_bytes(), text_before)

    def test_prepare_preserves_existing_review(self):
        self.prepare("记录完整。")
        original = self.review.read_bytes()
        self.assertEqual(self.run_cli("prepare", "--text", self.text,
                                     "--review-out", self.review)[0], 2)
        self.assertEqual(self.review.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
