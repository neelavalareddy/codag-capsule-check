"""SYNTHETIC-EVAL reference compressor + naive baseline (stdlib only, deterministic).

This is NOT Codag's product code. It is a reference implementation of the
per-type rules Codag states publicly:
  logs    -> chronology + causal evidence + outliers
  tests   -> failures, traces, file locations, exit status
  searches-> exact file:line references
  tree/api-> structure kept, bulk items elided
Omitted data is replaced by markers that say how to retrieve it (line range /
JSON path).
"""
import json
import re
from collections import Counter

BASELINE_N = 30

_LOG_LINE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) (\w+)\s+(\S+)\s(.*)$")
KEEP_LEVELS = {"WARN", "WARNING", "ERROR", "FATAL", "CRITICAL"}
# Generic ops vocabulary (deliberately NOT the judge's signature list).
CAUSAL = re.compile(
    r"deploy|rolled out|rollback|config|refused|timed out|timeout|circuit|exhaust|"
    r"OOM|OutOfMemory|outage|mismatch|retry|unexpected|leak|reserved|fail",
    re.I,
)
OUTLIER_MAX_COUNT = 2  # a message template seen <= this many times is an outlier

TEST_KEEP = [
    re.compile(p)
    for p in (
        r"^FAILED\b",
        r"^ERROR\b",
        r"^E\s",
        r"Traceback",
        r'^\s*File ".+", line \d+',
        r"^\S+\.\w+:\d+",
        r"Error\b|Exception\b",
        r"^=+ .+ =+$",
        r"^_+ .+ _+$",
        r"exit (status|code)",
        r"RERUN",
        r"PASSED \(on rerun",
        r"^assert ",
    )
]
_FILE_FRAME = re.compile(r'^\s*File ".+", line \d+')

_HIT = re.compile(r"^(?P<path>[^\s:]+):(?P<ln>\d+):(?P<text>.*)$")
VENDOR_PREFIXES = ("vendor/", "node_modules/", "third_party/")
SEARCH_TEXT_MAX = 120

BAD_STATUS = {"degraded", "outage", "drifted", "saturated", "oom_restarting", "flapping"}
_ITEM_LINE = re.compile(r"^\s*\{.*\}\s*,?\s*$")


def _emit(lines, keep, kind, hint="retrieve by line range"):
    out, i, n = [], 0, len(lines)
    while i < n:
        if i in keep:
            out.append(lines[i])
            i += 1
            continue
        j = i
        while j < n and j not in keep:
            j += 1
        out.append("[... %d %s omitted: lines %d-%d; %s ...]" % (j - i, kind, i + 1, j, hint))
        i = j
    return "\n".join(out)


def _template(level, msg):
    return (level, re.sub(r"\d+", "#", msg))


def compress_log(text):
    lines = text.split("\n")
    n = len(lines)
    parsed = [_LOG_LINE.match(l) for l in lines]
    counts = Counter(_template(m.group(2), m.group(4)) for m in parsed if m)
    keep = {0, n - 1}
    idx = [i for i, m in enumerate(parsed) if m]
    if idx:
        keep.add(idx[0])
        keep.add(idx[-1])
    for i, m in enumerate(parsed):
        if not m:
            if lines[i].strip():
                keep.add(i)  # unparsed (banner / stack continuation): keep
            continue
        level, msg = m.group(2), m.group(4)
        if level in KEEP_LEVELS or CAUSAL.search(msg) or counts[_template(level, msg)] <= OUTLIER_MAX_COUNT:
            keep.add(i)
    return _emit(lines, keep, "log lines")


def compress_test(text):
    lines = text.split("\n")
    n = len(lines)
    keep = {0, n - 1}
    prev_frame = False
    for i, l in enumerate(lines):
        hit = any(p.search(l) for p in TEST_KEEP)
        if prev_frame:
            hit = True  # source line under a trace frame
        if hit:
            keep.add(i)
        prev_frame = bool(_FILE_FRAME.match(l))
    return _emit(lines, keep, "test lines")


def compress_search(text):
    lines = text.split("\n")
    out, groups, order = [], {}, []
    vendor_n, vendor_files, vendor_first = 0, set(), None
    for i, l in enumerate(lines):
        if l.startswith("#") or l.startswith("$"):
            out.append(("raw", l))
            continue
        m = _HIT.match(l)
        if not m:
            continue  # context lines and '--' separators dropped
        path, ln, body = m.group("path"), m.group("ln"), m.group("text").strip()
        if path.startswith(VENDOR_PREFIXES):
            vendor_n += 1
            vendor_files.add(path)
            if vendor_first is None:
                vendor_first = i + 1
            continue
        if body not in groups:
            groups[body] = []
            order.append(body)
            out.append(("hit", body))
        groups[body].append("%s:%s" % (path, ln))
    res = []
    for kind, v in out:
        if kind == "raw":
            res.append(v)
            continue
        refs = groups[v]
        line = "%s:%s" % (refs[0], v[:SEARCH_TEXT_MAX])
        if len(refs) > 1:
            line += "  (+%d same text: %s)" % (len(refs) - 1, ", ".join(refs[1:]))
        res.append(line)
    if vendor_n:
        res.append("[... %d vendor hits in %d files omitted; retrieve by line range from line %d ...]"
                   % (vendor_n, len(vendor_files), vendor_first))
    return "\n".join(res)


def _annotated(line):
    return line.rstrip().endswith("]")


def compress_tree(text):
    lines = text.split("\n")
    n = len(lines)
    keep = {0, n - 1}
    run = 0
    for i, l in enumerate(lines):
        s = l.rstrip()
        if i == 0 or s.endswith("/") or _annotated(s) or s.startswith("$"):
            keep.add(i)
            run = 0
            continue
        run += 1
        if run <= 3:
            keep.add(i)
    return _emit(lines, keep, "tree entries")


def compress_api(text):
    lines = text.split("\n")
    n = len(lines)
    keep = {0, n - 1}
    items = []
    for i, l in enumerate(lines):
        if _ITEM_LINE.match(l):
            try:
                obj = json.loads(l.strip().rstrip(","))
            except ValueError:
                keep.add(i)
                continue
            items.append((i, obj))
        else:
            keep.add(i)
    for pos, (i, obj) in enumerate(items):
        flagged = isinstance(obj, dict) and any(
            isinstance(v, str) and v in BAD_STATUS for v in obj.values())
        if flagged or pos < 2 or pos == len(items) - 1:
            keep.add(i)
    return _emit(lines, keep, "api items", "retrieve by line range or JSON path $.items")


def compress_baseline(text, n=BASELINE_N):
    lines = text.split("\n")
    if len(lines) <= n:
        return text
    return "\n".join(lines[:n])


COMPRESSORS = {
    "log": compress_log,
    "test": compress_test,
    "search": compress_search,
    "tree": compress_tree,
    "api": compress_api,
}


def compress(result_type, text, mode="reference"):
    if mode == "baseline":
        return compress_baseline(text)
    if mode == "reference":
        return COMPRESSORS[result_type](text)
    raise ValueError("unknown mode %r" % mode)


def compress_session(session, mode="reference"):
    """Return {result_id: capsule_text}."""
    return {r["id"]: compress(r["type"], r["text"], mode) for r in session["tool_results"]}
