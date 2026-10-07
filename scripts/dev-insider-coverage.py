#!/usr/bin/env python3
"""Track which saved Dev Insider posts have been turned into production-patterns lessons.

Reads dev-insider-docs/index.json (built by dev-insider-index.py) and keeps a ledger at
dev-insider-docs/coverage.json. Both are gitignored with the folder: the ledger names posts,
so it stays local. Lessons themselves never reference these files.

  scripts/dev-insider-coverage.py status [--series S]       covered / skipped / open per series
  scripts/dev-insider-coverage.py next [--series S] [-n N]  next open posts, in series-part order
  scripts/dev-insider-coverage.py suggest <post_id>         existing lessons that likely overlap
  scripts/dev-insider-coverage.py mark <post_id>... --as lesson|extend|skip (--target F | --reason R)

JSON on stdout. Exit codes: 0 ok, 1 error.
"""

import argparse
import datetime as dt
import json
import math
import re
import sys
import unicodedata
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DOCS = REPO / "dev-insider-docs"
LEDGER = DOCS / "coverage.json"
LESSONS = REPO / "plugins/production-patterns/skills/production-review/references"
KMAP = REPO / "knowledge-map.yaml"
STOP = set("a an and the of for to in on with by is are be at as or from when not your you vs "
           "khi la va cua cho trong mot cac nhung khong de duoc voi theo phan series".split())


def load(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def fold(text):
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c)).replace("đ", "d")


def tokens(text):
    return {t for t in re.findall(r"[a-z0-9]+", fold(text)) if len(t) > 2 and t not in STOP}


def lesson_titles():
    titles, cur = {}, None
    for line in KMAP.read_text().splitlines() if KMAP.exists() else []:
        m = re.match(r"^  ([\w.-]+\.md):\s*$", line)
        if m:
            cur = m.group(1)
            titles[cur] = cur[:-3].replace("-", " ")
        elif cur and (m := re.match(r"^\s+title:\s*(.+)$", line)):
            titles[cur] += " " + m.group(1)
    for f in LESSONS.glob("*.md"):
        titles.setdefault(f.name, f.stem.replace("-", " "))
    index = LESSONS.parent / "SKILL.md"
    for line in index.read_text().splitlines() if index.exists() else []:
        m = re.match(r"^\|\s*\[([\w.-]+\.md)\]\(references/[^)]*\)\s*\|(.*)\|\s*$", line)
        if m and m.group(1) in titles:
            titles[m.group(1)] += " " + m.group(2)  # the "load when" triggers
    return titles


def short(title):
    title = re.sub(r"\s*[|_]\s*TechCraft on Patreon\s*$", "", title)
    return re.sub(r"^[^\w]+", "", title)[:110]


def row(e, ledger):
    entry = ledger.get(e["post_id"], {})
    return {"post_id": e["post_id"], "series": e["folder"], "part": e.get("part"),
            "access": e.get("access"), "title": short(e["title"]), "file": e["file"],
            "status": entry.get("status", "open"), **{k: v for k, v in entry.items() if k != "status"}}


def main(argv):
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("status"); s.add_argument("--series")
    n = sub.add_parser("next"); n.add_argument("--series"); n.add_argument("-n", type=int, default=5)
    g = sub.add_parser("suggest"); g.add_argument("post_id")
    m = sub.add_parser("mark"); m.add_argument("post_ids", nargs="+")
    m.add_argument("--as", dest="status", required=True, choices=["lesson", "extend", "skip"])
    m.add_argument("--target"); m.add_argument("--reason")
    a = p.parse_args(argv)

    index = load(DOCS / "index.json", None)
    if index is None:
        sys.exit("dev-insider-docs/index.json missing: run scripts/dev-insider-index.py first")
    ledger = load(LEDGER, {})
    posts = sorted((e for e in index if e.get("post_id")),
                   key=lambda e: (e["folder"], e.get("part") if e.get("part") is not None else 999, e["title"]))
    if getattr(a, "series", None):
        posts = [e for e in posts if e["folder"] == a.series]

    if a.cmd == "status":
        out = {}
        for e in posts:
            st = ledger.get(e["post_id"], {}).get("status", "open")
            out.setdefault(e["folder"], {"lesson": 0, "extend": 0, "skip": 0, "open": 0})[st] += 1
        print(json.dumps({"series": out, "open_total": sum(v["open"] for v in out.values())}, indent=2))
    elif a.cmd == "next":
        rows = [row(e, ledger) for e in posts if e["post_id"] not in ledger]
        print(json.dumps(rows[: a.n], ensure_ascii=False, indent=2))
    elif a.cmd == "suggest":
        e = next((e for e in index if e.get("post_id") == a.post_id), None)
        if not e:
            sys.exit(f"post {a.post_id} not in index")
        want = tokens(short(e["title"]))
        docs = {f: tokens(t) for f, t in lesson_titles().items()}
        df = {t: sum(t in d for d in docs.values()) for t in want}
        idf = {t: math.log((1 + len(docs)) / (1 + df[t])) for t in want}
        scored = sorted(((round(sum(idf[t] for t in want & d), 2), sorted(want & d), f)
                         for f, d in docs.items()), reverse=True)
        print(json.dumps({"post": row(e, ledger), "candidates": [
            {"lesson": f, "score": s, "terms": t} for s, t, f in scored if s > 0][:5]},
            ensure_ascii=False, indent=2))
    elif a.cmd == "mark":
        if a.status in ("lesson", "extend") and not a.target:
            sys.exit("--target <lesson.md> is required for lesson/extend")
        if a.status == "skip" and not a.reason:
            sys.exit("--reason is required for skip")
        known = {e["post_id"] for e in index}
        for pid in a.post_ids:
            if pid not in known:
                sys.exit(f"post {pid} not in index")
            ledger[pid] = {"status": a.status, "date": dt.date.today().isoformat(),
                           **({"target": a.target} if a.target else {}),
                           **({"reason": a.reason} if a.reason else {})}
        LEDGER.write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({"marked": a.post_ids, "as": a.status}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
