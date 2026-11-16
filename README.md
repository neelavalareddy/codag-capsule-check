# capsule-check

Incident-triage **compression-fidelity** eval harness for tool-result compression
(logs, test output, searches, file trees, API output). Stdlib-only Python, offline, deterministic.

> **All data is SYNTHETIC.** Every session, tool result and JSON file carries a SYNTHETIC banner.
> The reference compressor is a re-implementation of publicly *stated* per-type rules, **not Codag's product code**,
> so results say nothing about Codag's actual compressor.
> **Status:** verified by independent execution — 20/20 tests pass, demo exits 0; see "Verification status".

## What it measures
- **Layer 1 – evidence recall** per tool-result type (log / test / search / tree / api): does each labeled
  minimal-evidence line survive in the capsule? (Search evidence = exact `path:line` ref.)
- **Layer 2 – simulated triage**: given ONLY the capsule, a deterministic rule-based triage simulator picks a root cause
  from the fixed candidate list. Reports root-cause accuracy raw vs. compressed, an abstain rate, and the
  **false-lead rate** = sessions where the raw text yields the right pick but the capsule yields a wrong, non-abstaining pick.
- **Compression ratio** (bytes in / bytes out) per session, mean/median/aggregate, plus per-scenario breakdown.
- Two compressors compared: `reference` (per-type rules) vs. `baseline` (first-30-lines truncation).

Reference rules (`capsule_check/compressor.py`): logs keep first/last line, WARN+ levels, generic causal vocabulary and
rare-template outliers, in original order; tests keep FAILED/trace frames+source/`path:line` locations/summaries/exit status;
searches keep every non-vendor `path:line` (identical-text hits grouped, refs retained), drop context; tree/API keep structure,
annotated/flagged entries, and head/tail items. Omissions are replaced by markers giving a line range / JSON path to retrieve.

Known, intentional weak spot: INFO-level lines whose message template is also common noise (e.g. `pool stats ... waiting=37`,
`heap used=1890MB ... gc_pause=840ms`) are expected to be dropped by the reference rules — "subtle" evidence that tests whether
a compressor notices numeric outliers. Caveat: the judge's signatures and the generator's templates are co-designed, so
absolute numbers are optimistic; use the harness for relative comparison.

## Data
`capsule_check/dataset.py` generates **60 sessions** (6 incident types x 10): deploy_regression, flaky_test, dependency_outage,
config_drift, db_connection_exhaustion, memory_leak. Each has 5 tool results (log 160–260 lines with a 3-line causal chain +
1 outlier + 2 red-herring types; pytest-style output; ripgrep output; tree; JSON API), a root-cause label, and `minimal_evidence`
(by construction 12 lines per session, 14 for flaky_test = 740 total — derived from the code, not run-verified).
Write them as files: `python -m capsule_check.dataset data/sessions` (or `python demo.py --write-data data/sessions`).

## Run
```
python -m unittest discover -s tests -v
python demo.py --out reports        # prints report, writes reports/report.md + report.json
```

## Differentiation
- **KEEP-Bench** (Oct 2026): evidence preservation in *generic context compression* for QA.
- **Compact-Bench**: compaction damage for *coding agents*.
- **capsule-check**: per-type *tool-result capsules* on *incident triage* — logs + test failures + searches + tree/API combined,
  measuring survival of the evidence an on-call agent needs and the false leads compression can introduce.

## Extend with a new incident type
1. Add the name to `INCIDENT_TYPES` in `dataset.py`.
2. Add a `SPEC[name]` entry (chain, outliers, optional `subtle_noise`, test, search, search_path2, tree, api) and a
   `HERRINGS[name]` weak red-herring line.
3. Add `SIGNATURES[name]` regexes in `judge.py`.
4. Run the tests (counts in `DatasetCoverage` assume 6 types x 10 — update).

## Verification status
Verified by independent execution (Codex, Python 3.9.6, 2026-11-16) and re-run locally with identical results: 20/20 tests pass, demo exits 0. Evidence recall 97.3% (reference) vs 17.7% (baseline) across 740 synthetic evidence lines; root-cause accuracy 100.0% vs 86.7%; false-lead rate 0.0%; mean compression 4.67x vs 4.02x. Full run output in reports/report.md and reports/report.json; execution evidence in VERIFICATION.md. All data is synthetic; the triage judge is a deterministic rule-based simulator, not an LLM.
