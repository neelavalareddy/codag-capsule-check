"""Unit tests (SYNTHETIC data only). Run: python -m unittest discover -s tests -v
NOTE: verified — 20/20 tests pass (Codex + local re-run, 2026-11-16)."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from capsule_check import compressor as C
from capsule_check import dataset as D
from capsule_check import judge as J


def _log_line(i, lvl, msg, svc="svc"):
    return "2026-03-11 08:%02d:%02d %-5s %s %s" % (i // 60, i % 60, lvl, svc, msg)


class LogRules(unittest.TestCase):
    def setUp(self):
        lines = ["# SYNTHETIC"]
        for i in range(60):
            lines.append(_log_line(i, "INFO", "GET /api/orders 200 %dms" % (i + 3)))
        lines[20] = _log_line(19, "INFO", "deploy: rolled out svc v1.2.3 to 100% of pods")
        lines[40] = _log_line(39, "ERROR", "boom happened here")
        lines[50] = _log_line(49, "INFO", "weird one-off line xyz")
        self.lines = lines
        self.out = C.compress_log("\n".join(lines)).split("\n")

    def test_causal_error_and_outlier_kept(self):
        for i in (20, 40, 50):
            self.assertIn(self.lines[i], self.out)

    def test_noise_dropped_with_retrieval_marker(self):
        self.assertLess(len(self.out), len(self.lines))
        self.assertTrue(any("omitted" in l and "retrieve by line range" in l for l in self.out))

    def test_chronology_preserved(self):
        kept = [l for l in self.out if l in self.lines]
        self.assertEqual(kept, [l for l in self.lines if l in kept])

    def test_first_and_last_anchor_kept(self):
        self.assertIn(self.lines[0], self.out)
        self.assertIn(self.lines[-1], self.out)


class TestRules(unittest.TestCase):
    TEXT = "\n".join([
        "# SYNTHETIC", "collected 3 items",
        "tests/test_a_1.py::test_case_1 PASSED", "tests/test_a_2.py::test_case_1 PASSED",
        "================ FAILURES ================", "____ test_x ____",
        "Traceback (most recent call last):",
        '  File "src/m.py", line 42, in fn', "    return payload['k']",
        "KeyError: 'k'", "tests/test_m.py:57: KeyError",
        "FAILED tests/test_m.py::test_x - KeyError: 'k'",
        "=========== 1 failed, 2 passed in 1.0s ===========", "exit status: 1"])

    def test_failure_trace_location_exit_kept_passed_dropped(self):
        out = C.compress_test(self.TEXT)
        for needle in ['  File "src/m.py", line 42, in fn', "    return payload['k']", "tests/test_m.py:57: KeyError",
                       "FAILED tests/test_m.py::test_x - KeyError: 'k'", "exit status: 1"]:
            self.assertIn(needle, out)
        self.assertNotIn("test_case_1 PASSED", out)


class SearchRules(unittest.TestCase):
    TEXT = "\n".join([
        "# SYNTHETIC", "$ rg -n x",
        "src/a.py-9-    ctx", "src/a.py:10:import logging", "src/a.py-11-    ctx", "--",
        "src/b.py:20:import logging", "--",
        "src/c.py:30:the real hit", "--",
        "vendor/lib1/core.py:5:import logging", "vendor/lib2/core.py:6:import logging"])

    def test_exact_refs_kept(self):
        out = C.compress_search(self.TEXT)
        for ref in ("src/a.py:10", "src/b.py:20", "src/c.py:30"):
            self.assertIn(ref, out)

    def test_context_dropped_vendor_collapsed(self):
        out = C.compress_search(self.TEXT)
        self.assertNotIn("ctx", out)
        self.assertNotIn("vendor/lib1/core.py:5", out)
        self.assertIn("2 vendor hits", out)


class StructRules(unittest.TestCase):
    def test_tree_keeps_dirs_and_annotated(self):
        lines = ["# SYNTHETIC", "repo/", "  src/"] + ["    f_%d.py" % i for i in range(10)]
        lines.insert(8, "    hot.py  [modified in v9.9.9]")
        lines.append("  tests/")
        out = C.compress_tree("\n".join(lines))
        self.assertIn("    hot.py  [modified in v9.9.9]", out)
        self.assertIn("  src/", out)
        self.assertIn("  tests/", out)
        self.assertIn("omitted", out)

    def test_api_keeps_flagged_item_and_json_path_hint(self):
        items = [{"name": "s%d" % i, "status": "healthy"} for i in range(10)]
        items[5] = {"name": "bad", "status": "degraded"}
        lines = ["# SYNTHETIC", '{"items": [']
        lines += [" " + json.dumps(it, sort_keys=True) + "," for it in items]
        lines.append('], "total": 10}')
        out = C.compress_api("\n".join(lines))
        self.assertIn(json.dumps(items[5], sort_keys=True), out)
        self.assertIn("JSON path", out)
        self.assertNotIn(json.dumps(items[3], sort_keys=True), out)


class BaselineRule(unittest.TestCase):
    def test_first_n_lines(self):
        text = "\n".join("l%d" % i for i in range(100))
        out = C.compress_baseline(text, 10)
        self.assertEqual(out.split("\n"), ["l%d" % i for i in range(10)])


class DatasetCoverage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sessions = D.build_sessions()

    def test_counts(self):
        self.assertEqual(len(self.sessions), 60)
        for t in D.INCIDENT_TYPES:
            self.assertEqual(sum(s["incident_type"] == t for s in self.sessions), 10)

    def test_synthetic_marked_everywhere(self):
        for s in self.sessions:
            self.assertTrue(s["synthetic"])
            self.assertIn("SYNTHETIC", s["banner"])
            for r in s["tool_results"]:
                self.assertIn("SYNTHETIC", r["text"].split("\n")[0])

    def test_every_evidence_line_in_raw(self):
        for s in self.sessions:
            raw = {r["id"]: r["text"] for r in s["tool_results"]}
            for e in s["minimal_evidence"]:
                self.assertTrue(J.survives(e, raw[e["result_id"]]), (s["session_id"], e))

    def test_seeded_coverage(self):
        for s in self.sessions:
            kinds = [e["kind"] for e in s["minimal_evidence"] if e["type"] == "log"]
            self.assertGreaterEqual(kinds.count("chain"), 3)
            self.assertGreaterEqual(kinds.count("outlier"), 1)
            self.assertLessEqual(kinds.count("outlier"), 2)
            types = {e["type"] for e in s["minimal_evidence"]}
            self.assertEqual(types, {"log", "test", "search", "tree", "api"})
            tk = {e["kind"] for e in s["minimal_evidence"] if e["type"] == "test"}
            self.assertTrue({"trace", "location", "failure", "exit"} <= tk)

    def test_generation_deterministic(self):
        self.assertEqual(json.dumps(D.build_sessions(), sort_keys=True), json.dumps(self.sessions, sort_keys=True))


class JudgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sessions = D.build_sessions(per_type=2)

    def test_determinism(self):
        a = json.dumps(J.aggregate(J.evaluate(self.sessions)), sort_keys=True)
        b = json.dumps(J.aggregate(J.evaluate(self.sessions)), sort_keys=True)
        self.assertEqual(a, b)

    def test_layer1_identity_capsule_full_recall(self):
        for s in self.sessions:
            raw = {r["id"]: r["text"] for r in s["tool_results"]}
            self.assertTrue(all(e["survived"] for e in J.layer1(s, raw)))

    def test_layer1_detects_missing(self):
        s = self.sessions[0]
        empty = {r["id"]: "" for r in s["tool_results"]}
        self.assertFalse(any(e["survived"] for e in J.layer1(s, empty)))

    def test_triage_picks_and_abstains(self):
        pick, _ = J.triage("deploy: rolled out x v1.0.1 to 100% of pods\nschema mismatch: field a")
        self.assertEqual(pick, "deploy_regression")
        self.assertEqual(J.triage("nothing relevant")[0], J.UNKNOWN)

    def test_false_lead_definition(self):
        for rec in J.evaluate(self.sessions):
            for mode in ("reference", "baseline"):
                m = rec[mode]
                if m["false_lead"]:
                    self.assertTrue(rec["raw"]["correct"])
                    self.assertFalse(m["correct"])


if __name__ == "__main__":
    unittest.main()
