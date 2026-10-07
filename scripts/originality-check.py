#!/usr/bin/env python3
"""Gate: a lesson must not transcribe its subscriber source.

Lessons are published (public repo + site); the Dev Insider posts are paid content that is
not ours to redistribute. This check catches the mechanical forms of copying. It cannot catch
a paragraph-by-paragraph translation, so the lesson-sweep skill keeps a human-judgement step
for that. Output names locations and counts only, never the matched source text.

  scripts/originality-check.py <lesson.md> --source <post.md> [<post.md> ...]

Fails (exit 2) on any of:
  - shared 8-word runs between lesson and source        (> --max-shingles, default 2)
  - identical lines of 40+ chars, code included         (> --max-lines, default 1)
  - references to saved source material or its images   (any)
  - missing attribution line naming TechCraft/Patreon    (in the last 15 lines)
Exit 0 pass, 1 error.
"""

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

FORBIDDEN = [r"dev-insider-docs", r"(?<![\w/])assets/\d{6,}", r"patreonusercontent\.com",
             r"patreon\.com/(?:file|media)", r"c10\.patreon"]


def norm(text):
    return unicodedata.normalize("NFC", text).lower()


def words(text):
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)|\]\([^)]*\)|https?://\S+", " ", norm(text))
    return re.findall(r"\w+", text)


def shingles(ws, k):
    return {tuple(ws[i:i + k]) for i in range(len(ws) - k + 1)}


def strip_front_matter(text):
    return re.sub(r"\A---\n.*?\n---\n", "", text, flags=re.S)


def main(argv):
    p = argparse.ArgumentParser()
    p.add_argument("lesson")
    p.add_argument("--source", nargs="+", required=True)
    p.add_argument("-k", type=int, default=8)
    p.add_argument("--max-shingles", type=int, default=2)
    p.add_argument("--max-lines", type=int, default=1)
    a = p.parse_args(argv)

    lesson_text = Path(a.lesson).read_text()
    src_text = "\n".join(strip_front_matter(Path(s).read_text()) for s in a.source)

    lw = words(lesson_text)
    shared = shingles(lw, a.k) & shingles(words(src_text), a.k)
    # Locate shared runs by lesson line number without echoing their text.
    shingle_lines = sorted({i + 1 for i, line in enumerate(lesson_text.splitlines())
                            for sh in [shingles(words(line), a.k)] if sh & shared})

    src_lines = {norm(l).strip() for l in src_text.splitlines() if len(l.strip()) >= 40}
    copied_lines = [i + 1 for i, l in enumerate(lesson_text.splitlines())
                    if len(l.strip()) >= 40 and norm(l).strip() in src_lines]

    refs = [(i + 1, pat) for i, l in enumerate(lesson_text.splitlines())
            for pat in FORBIDDEN if re.search(pat, l)]

    tail = "\n".join(lesson_text.splitlines()[-15:])
    attributed = bool(re.search(r"techcraft|patreon\.com/techcraft", tail, re.I))

    problems = []
    if len(shared) > a.max_shingles:
        problems.append(f"{len(shared)} shared {a.k}-word runs (max {a.max_shingles})")
    if len(copied_lines) > a.max_lines:
        problems.append(f"{len(copied_lines)} identical 40+ char lines (max {a.max_lines})")
    if refs:
        problems.append(f"{len(refs)} references to saved source material")
    if not attributed:
        problems.append("no TechCraft/Patreon attribution in the last 15 lines")

    print(json.dumps({
        "lesson": a.lesson, "sources": len(a.source), "pass": not problems, "problems": problems,
        "shared_runs": len(shared), "shared_run_lines": shingle_lines[:30],
        "identical_lines": copied_lines[:30], "forbidden_refs": refs[:30], "attributed": attributed,
        "note": "mechanical check only; translation and structure-mirroring need the human review step",
    }, indent=2))
    return 0 if not problems else 2


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except OSError as e:
        print(json.dumps({"error": str(e)})); sys.exit(1)
