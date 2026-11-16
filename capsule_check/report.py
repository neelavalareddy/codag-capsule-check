"""Report rendering (Markdown + JSON). SYNTHETIC eval only."""
import json
import os

from .dataset import BANNER, build_sessions
from .judge import aggregate, evaluate


def run_suite():
    records = evaluate(build_sessions())
    return {"banner": BANNER, "metrics": aggregate(records), "sessions": records}


def _pct(x):
    return "n/a" if x is None else "%.1f%%" % (100 * x)


def to_markdown(report):
    m = report["metrics"]
    L = ["# capsule-check report", "", "> " + BANNER, "",
         "Sessions: %d | raw (uncompressed) root-cause accuracy: %s" % (m["n_sessions"], _pct(m["raw_accuracy"])), ""]
    L += ["## Headline (reference compressor vs naive first-N baseline)", "",
          "| metric | reference | baseline |", "|---|---|---|"]
    r, b = m["reference"], m["baseline"]
    L.append("| evidence recall (all %d lines) | %s | %s |" % (r["evidence_lines"], _pct(r["evidence_recall_overall"]), _pct(b["evidence_recall_overall"])))
    L.append("| root-cause accuracy (capsule only) | %s | %s |" % (_pct(r["root_cause_accuracy"]), _pct(b["root_cause_accuracy"])))
    L.append("| abstain rate | %s | %s |" % (_pct(r["abstain_rate"]), _pct(b["abstain_rate"])))
    L.append("| false-lead rate | %s | %s |" % (_pct(r["false_lead_rate"]), _pct(b["false_lead_rate"])))
    L.append("| mean per-session ratio (in/out) | %.2fx | %.2fx |" % (r["compression"]["mean_ratio"], b["compression"]["mean_ratio"]))
    L.append("| aggregate bytes in/out | %.2fx | %.2fx |" % (r["compression"]["aggregate_ratio"], b["compression"]["aggregate_ratio"]))
    L += ["", "## Evidence recall by tool-result type", "", "| type | n lines | reference | baseline |", "|---|---|---|---|"]
    for t in r["evidence_recall_by_type"]:
        L.append("| %s | %d | %s | %s |" % (t, r["evidence_count_by_type"][t], _pct(r["evidence_recall_by_type"][t]), _pct(b["evidence_recall_by_type"][t])))
    for mode, name in (("reference", "Reference"), ("baseline", "Baseline")):
        L += ["", "## Per-scenario breakdown - %s" % name, "",
              "| scenario | n | recall | raw acc | capsule acc | false-lead | mean ratio |", "|---|---|---|---|---|---|---|"]
        for t, s in m[mode]["per_scenario"].items():
            L.append("| %s | %d | %s | %s | %s | %s | %.2fx |" % (t, s["n"], _pct(s["evidence_recall"]), _pct(s["raw_accuracy"]),
                                                                 _pct(s["accuracy"]), _pct(s["false_lead_rate"]), s["mean_ratio"]))
    L += ["", "Notes: all data synthetic; triage judge is a deterministic rule-based simulator, not an LLM;",
          "numbers describe this harness's reference compressor, NOT Codag's product.", ""]
    return "\n".join(L)


def write_reports(report, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "report.json"), "w") as fh:
        json.dump(report, fh, indent=2, sort_keys=True)
    with open(os.path.join(out_dir, "report.md"), "w") as fh:
        fh.write(to_markdown(report))
    return out_dir
