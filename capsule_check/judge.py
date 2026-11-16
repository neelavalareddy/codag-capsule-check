"""Two-layer judge (SYNTHETIC eval; deterministic, no model calls).

Layer 1: does each labeled evidence line survive in the capsule?
Layer 2: given ONLY the capsule text, a rule-based triage simulator picks a root
cause from the fixed candidate list; a false lead is a session where
compression flips a correct raw-text pick into a wrong (non-abstaining) pick.
"""
import re
from statistics import median

from .compressor import compress_session
from .dataset import INCIDENT_TYPES

# (regex, weight). Per pattern, at most 3 distinct matching lines count.
SIGNATURES = {
    "deploy_regression": [(r"deploy: rolled out", 3), (r"since deploy|after upgrade to", 3), (r"schema mismatch", 3),
                          (r"modified in v\d", 3), (r'"previous_version"', 2), (r"changed in v\d|rewritten in v\d", 2),
                          (r"deploy window", 2)],
    "flaky_test": [(r"retry attempt", 3), (r"ordering race", 3), (r"shared fixture", 2), (r"on rerun", 3),
                   (r"RERUN", 2), (r"clock skew", 3), (r"slow test", 2)],
    "dependency_outage": [(r"connection refused", 3), (r"circuit breaker OPEN", 3), (r"major outage", 3),
                          (r"fallback cache empty", 3), (r'"status": "outage"', 3), (r"health probe timed out", 2)],
    "config_drift": [(r"config reload", 3), (r"config drift detected", 3), (r"out of allowed range", 2),
                     (r"unexpected env", 3), (r"source=hotfix-override", 2), (r"hand-edited on host", 3),
                     (r'"status": "drifted"', 3), (r"config lint warning", 2)],
    "db_connection_exhaustion": [(r"connection pool exhausted", 3), (r"acquiring connection", 3),
                                 (r"pool stats .*waiting=[1-9]", 3), (r"reserved for superuser", 3),
                                 (r'"status": "saturated"', 3), (r"slow query", 2)],
    "memory_leak": [(r"gc_pause=\d{3,}ms", 3), (r"OOMKilled", 3), (r"OutOfMemoryError", 3), (r"full GCs in", 3),
                    (r'"status": "oom_restarting"', 3), (r"minor GC pause", 2)],
}
_COMPILED = {c: [(re.compile(p), w) for p, w in sigs] for c, sigs in SIGNATURES.items()}
UNKNOWN = "unknown"


def triage(text):
    """Return (pick, scores). Ties resolve in INCIDENT_TYPES order; all-zero -> 'unknown'."""
    lines = text.split("\n")
    scores = {}
    for cand in INCIDENT_TYPES:
        s = 0
        for rx, w in _COMPILED[cand]:
            s += w * min(3, sum(1 for l in lines if rx.search(l)))
        scores[cand] = s
    best = max(scores.values())
    if best == 0:
        return UNKNOWN, scores
    return next(c for c in INCIDENT_TYPES if scores[c] == best), scores


def survives(ev, capsule_text):
    if ev["kind"] == "ref":
        return re.search(re.escape(ev["line"]) + r"(?!\d)", capsule_text) is not None
    return ev["line"] in capsule_text


def layer1(session, capsules):
    return [dict(result_id=e["result_id"], type=e["type"], kind=e["kind"], line=e["line"],
                 survived=survives(e, capsules[e["result_id"]]))
            for e in session["minimal_evidence"]]


def layer2(session, capsules):
    text = "\n".join(capsules[r["id"]] for r in session["tool_results"])
    return triage(text)


def evaluate_session(session):
    raw = {r["id"]: r["text"] for r in session["tool_results"]}
    gold = session["root_cause"]
    raw_pick, _ = layer2(session, raw)
    rec = {"session_id": session["session_id"], "incident_type": session["incident_type"],
           "raw": {"pick": raw_pick, "correct": raw_pick == gold,
                   "bytes": sum(len(t.encode()) for t in raw.values())}}
    for mode in ("reference", "baseline"):
        caps = compress_session(session, mode)
        l1 = layer1(session, caps)
        pick, _ = layer2(session, caps)
        b_out = sum(len(t.encode()) for t in caps.values())
        rec[mode] = {
            "layer1": l1, "pick": pick, "correct": pick == gold,
            "abstained": pick == UNKNOWN,
            "false_lead": pick != gold and pick != UNKNOWN and raw_pick == gold,
            "bytes_in": rec["raw"]["bytes"], "bytes_out": b_out,
            "ratio": round(rec["raw"]["bytes"] / b_out, 4),
        }
    return rec


def evaluate(sessions):
    return [evaluate_session(s) for s in sessions]


def _rate(a, b):
    return round(a / b, 4) if b else None


def aggregate(records):
    out = {"n_sessions": len(records)}
    out["raw_accuracy"] = _rate(sum(r["raw"]["correct"] for r in records), len(records))
    for mode in ("reference", "baseline"):
        by_type, tot_h, tot_n = {}, 0, 0
        for r in records:
            for e in r[mode]["layer1"]:
                h, n = by_type.get(e["type"], (0, 0))
                by_type[e["type"]] = (h + e["survived"], n + 1)
                tot_h += e["survived"]
                tot_n += 1
        ratios = [r[mode]["ratio"] for r in records]
        b_in = sum(r[mode]["bytes_in"] for r in records)
        b_out = sum(r[mode]["bytes_out"] for r in records)
        scen = {}
        for t in INCIDENT_TYPES:
            rs = [r for r in records if r["incident_type"] == t]
            if not rs:
                continue
            ev = [e for r in rs for e in r[mode]["layer1"]]
            scen[t] = {
                "n": len(rs),
                "evidence_recall": _rate(sum(e["survived"] for e in ev), len(ev)),
                "raw_accuracy": _rate(sum(r["raw"]["correct"] for r in rs), len(rs)),
                "accuracy": _rate(sum(r[mode]["correct"] for r in rs), len(rs)),
                "false_lead_rate": _rate(sum(r[mode]["false_lead"] for r in rs), len(rs)),
                "mean_ratio": round(sum(r[mode]["ratio"] for r in rs) / len(rs), 4),
            }
        out[mode] = {
            "evidence_lines": tot_n,
            "evidence_recall_overall": _rate(tot_h, tot_n),
            "evidence_recall_by_type": {t: _rate(h, n) for t, (h, n) in sorted(by_type.items())},
            "evidence_count_by_type": {t: n for t, (h, n) in sorted(by_type.items())},
            "root_cause_accuracy": _rate(sum(r[mode]["correct"] for r in records), len(records)),
            "abstain_rate": _rate(sum(r[mode]["abstained"] for r in records), len(records)),
            "false_lead_rate": _rate(sum(r[mode]["false_lead"] for r in records), len(records)),
            "compression": {
                "mean_ratio": round(sum(ratios) / len(ratios), 4),
                "median_ratio": round(median(ratios), 4),
                "min_ratio": min(ratios), "max_ratio": max(ratios),
                "aggregate_ratio": round(b_in / b_out, 4),
                "mean_reduction": round(sum(1 - 1 / x for x in ratios) / len(ratios), 4),
                "total_bytes_in": b_in, "total_bytes_out": b_out,
            },
            "per_scenario": scen,
        }
    return out
