# Academic Journal Peer Reviewer

Version **1.1.0** · **2026-10-18** · [中文说明](README.md) · [Codex guide](docs/codex.md) · [ChatGPT guide](docs/chatgpt.md)

A review assistant for routine medical and medical education manuscripts, including Chinese core journals and SCI/SCIE/SSCI submissions. It reads the available manuscript and produces specific findings with locations, evidence, and actionable recommendations.

This release adapts the user-supplied v1.0.0 package. It retains its four cross-cutting checks, review roles, two-layer reference verification, and revision checks. It revises rules that could force major revision, apply the wrong design checklist, overstate verification, or send material externally without authorization. The comparison used skill documents and fictional rule exercises; a controlled evaluation on real manuscripts has yet to be performed.

## Review approach

- Evaluate the question, structure, and argument before language and formatting.
- Match design, statistical, and reporting checks to the actual study.
- Assess issue type and severity separately, using the effect on validity, central claims, and the work needed to resolve it.
- Separate reference metadata checks (3A) from source-based claim support (3B).
- Preserve numbers, comparisons, negative findings, uncertainty, and conclusion strength, including in suggested wording.
- Verify revisions against the original concern, the response, and the revised manuscript.

A full review requires the complete manuscript and relevant figures and supplements. An abstract or excerpt supports a review of that material only. Missing data, inaccessible sources, and incomplete checks receive explicit status labels.

## Use in Codex

With Python 3.10 or later, run from the repository root:

```text
python -X utf8 scripts/install_codex.py
```

The installer targets `CODEX_HOME/skills`, or the current user's `.codex/skills` when unset. It backs up an existing skill directory before updating public files and retains other private extensions. Check its output for the actual paths, then verify discovery in your host.

```text
$academic-journal-peer-reviewer
Review the complete manuscript and supplements. Give the main findings with locations, evidence, and actionable revisions.
Match checks to the study design and distinguish reference metadata from claim support.
Review locally for now; wait for my authorization before external searches.
```

Reports follow the user's requested language. Request a full bilingual report explicitly when needed. Existing ARS and Nature workflows remain available for their specialized uses; this release makes no claim of comprehensive superiority over either.

## ChatGPT and packages

Use the route supported by your host: installed skill, plugin, or conversation files. Attach a release package or its extracted files, then ask the host to read the skill and its references. Cloud execution needs files made accessible to that session. Installation and available tools depend on the account and host.

The standalone archive contains `academic-journal-peer-reviewer/`. The plugin archive contains `plugin.json`, `skills/academic-journal-peer-reviewer/`, documentation, and release tools:

- `dist/2026-10-18_academic-journal-peer-reviewer-v1.1.0-skill.zip`
- `dist/2026-10-18_academic-journal-peer-reviewer-v1.1.0-plugin.zip`

## Confidentiality and verification

Review is read-only by default, with findings in a separate report. Follow the journal's confidentiality and AI-use requirements. External searches require authorization for the current task and begin with minimal metadata such as DOI, PMID, or title. Sending full text, private comments, or identifiable information requires separate confirmation. IMA uploads and submission-system writes also require authorization.

Metadata agreement establishes a bibliographic check; claim support requires reading the relevant source. The report distinguishes verified findings, partial checks, unavailable material, and checks not performed.

## Self-contained writing gate

The skill includes its rules, offline Python gate, and tests. Save the final report as UTF-8, run `prepare`, read the text and candidates, complete the semantic review, then run `check`. Delivery requires a `PASS` report for the exact final text; later edits require another check.

If Python or the packaged files are unavailable, explain the gap and complete the feasible reading and review preparation. Finish the gate in an environment that can run it. PASS describes compliance of the review record and candidate checks; scientific validity and review accuracy require their own evidence.

See the [gate guide](skills/academic-journal-peer-reviewer/references/anti-defensive-gate.md). Keep manuscripts, private records, and backups outside the public repository.

## Tests and release

```text
python -X utf8 -B -m unittest discover -s skills/academic-journal-peer-reviewer/scripts -p "test_*.py"
python -X utf8 -B -m unittest discover -s scripts -p "test_*.py"
python -X utf8 scripts/build_release.py --date 2026-10-18
```

Release archives use an explicit public-file allowlist. The DOI utility makes external requests and must be used within the authorized scope.

[MIT license](LICENSE) · [Attribution](ATTRIBUTION.md)
