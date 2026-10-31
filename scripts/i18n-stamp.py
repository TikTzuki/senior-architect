#!/usr/bin/env python3
"""Track which English lesson each Vietnamese translation was made from.

Translations live in i18n/vi/production-patterns/<lesson>.md — outside the plugin, so it never
ships them — and are published to the site's /vi/ locale by TikTzuki's sync-knowledge.mjs. Each
one records `source_sha256`, the hash of the English lesson it translates. The site compares it
on every build: a mismatch publishes the page with a "may be out of date" notice, and a changed
heading structure keeps the translation off the site entirely (its anchors would break).

  scripts/i18n-stamp.py status                 every lesson: translated / stale / missing
  scripts/i18n-stamp.py stamp <lesson> [...]   record the current English hash after (re)translating

`stamp` refuses a translation whose heading levels differ from the English lesson, because the
site matches headings by position to keep anchor links working.

JSON on stdout. Exit 0 ok, 1 error, 2 a stamp was refused.
"""

import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LESSONS = ROOT / "plugins/production-patterns/skills/production-review/references"
VI = ROOT / "i18n/vi/production-patterns"
FRONT = re.compile(r"\A---\n(.*?)\n---\n?", re.S)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def heading_levels(text):
    levels, fenced = [], False
    for line in text.splitlines():
        if line.startswith("```"):
            fenced = not fenced
        m = not fenced and re.match(r"(#{1,6})\s", line)
        if m:
            levels.append(len(m.group(1)))
    return levels


def split(text):
    m = FRONT.match(text)
    if not m:
        return {}, text
    meta = dict(re.findall(r"^(\w+):\s*(.*)$", m.group(1), re.M))
    return meta, text[m.end():]


def status():
    rows = {}
    for lesson in sorted(LESSONS.glob("*.md")):
        vi = VI / lesson.name
        if not vi.exists():
            rows[lesson.name] = "missing"
            continue
        meta, _ = split(vi.read_text())
        rows[lesson.name] = "translated" if meta.get("source_sha256") == sha(lesson) else "stale"
    orphans = sorted(p.name for p in VI.glob("*.md") if not (LESSONS / p.name).exists())
    counts = {k: sum(1 for v in rows.values() if v == k) for k in ("translated", "stale", "missing")}
    return {"counts": counts, "stale": [k for k, v in rows.items() if v == "stale"],
            "orphans": orphans, "lessons": rows}


def stamp(names):
    done, refused = [], {}
    for name in names:
        name = name if name.endswith(".md") else f"{name}.md"
        lesson, vi = LESSONS / name, VI / name
        if not lesson.exists() or not vi.exists():
            refused[name] = "no such lesson" if not lesson.exists() else "no translation yet"
            continue
        meta, body = split(vi.read_text())
        en_levels, vi_levels = heading_levels(lesson.read_text()), heading_levels(body)
        if en_levels != vi_levels:
            refused[name] = (f"heading structure differs from the English lesson "
                             f"({len(vi_levels)} headings vs {len(en_levels)}); "
                             f"translate every heading, in order, at the same level")
            continue
        meta["source_sha256"] = sha(lesson)
        front = "\n".join(f"{k}: {v}" for k, v in meta.items())
        vi.write_text(f"---\n{front}\n---\n\n{body.lstrip(chr(10))}")
        done.append(name)
    return {"stamped": done, "refused": refused}


def main(argv):
    if len(argv) >= 1 and argv[0] == "status":
        print(json.dumps(status(), indent=2))
        return 0
    if len(argv) >= 2 and argv[0] == "stamp":
        result = stamp(argv[1:])
        print(json.dumps(result, indent=2))
        return 2 if result["refused"] else 0
    print(__doc__, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
