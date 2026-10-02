#!/usr/bin/env python3
"""Index dev-insider-docs/ so new downloads don't duplicate what is already saved.

The folder is gitignored subscriber content; the index it writes (INDEX.md, index.json)
lives inside it and is gitignored with it. This script holds no content of its own.

Every saved post is keyed by its Patreon post id: .mhtml files carry the post URL in their
Snapshot-Content-Location header, .md exports carry it in their image URLs
(/p/post/<id>/). Exact file hash, normalised title and (series, part) are fallbacks.

  scripts/dev-insider-index.py                  rebuild the index, report duplicates and gaps
  scripts/dev-insider-index.py check <q>...     is a post already saved? q = URL, post id or title

Output is JSON on stdout. Exit codes: build 0 clean / 2 duplicates found;
check 0 all new (safe to download) / 2 at least one already saved; 1 on error.
"""

import email.parser
import hashlib
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "dev-insider-docs"
SITE_SUFFIX = re.compile(r"\s*\|\s*TechCraft on Patreon\s*$", re.I)
POST_ID_IN_URL = re.compile(r"/posts/(?:[^/?#]*-)?(\d{6,})(?=[/?#]|$)")
POST_ID_IN_MD = re.compile(r"/p/post/(\d{6,})/")
SERIES_PART = re.compile(r"([A-Z][A-Za-z&\- ]*?[A-Za-z])(?:\s+|(?<=[a-z]))P(\d+)\b")
ENCODED_WORD = re.compile(r"=\?utf-8\?Q\?(.*?)\?=(?=\s|$)", re.I)
INTRO = re.compile(r"m[ởo] đầu\s+series\s+(.+)$", re.I)


def normalise(text):
    """Casefolded words only: drops emoji, lock icons, punctuation and accents."""
    text = unicodedata.normalize("NFKD", text.replace("đ", "d").replace("Đ", "D"))
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(re.findall(r"[a-z0-9]+", text.casefold()))


def access_of(title):
    if "🔒" in title:
        return "paid"
    if "🔓" in title:
        return "free"
    return "-"


def series_part(title, folder):
    m = INTRO.search(SITE_SUFFIX.sub("", title))
    if m:
        return m.group(1).strip(" .:!?"), 0
    hits = SERIES_PART.findall(title)
    if hits:
        name, part = hits[-1]
        return name.strip(), int(part)
    return folder.replace("-", " ").title(), None


def decode_subject(value):
    """Decode Blink's Subject header by hand.

    Blink splits encoded words mid-character and leaves a title's own '?' unescaped inside
    them, which breaks email.header. A word ends only at '?=' followed by space or the end.
    """
    value = re.sub(r"\r?\n[ \t]+", " ", value)
    words = list(ENCODED_WORD.finditer(value))
    if not words:
        return value
    out, pos = bytearray(), 0
    for m in words:
        between = value[pos:m.start()]
        if pos == 0 or between.strip():
            out += between.encode()
        out += re.sub(rb"=([0-9A-Fa-f]{2})", lambda h: bytes([int(h.group(1), 16)]),
                      m.group(1).replace("_", " ").encode())
        pos = m.end()
    out += value[pos:].encode()
    return out.decode("utf-8", "replace")


def read_mhtml(path):
    raw = path.read_bytes()
    end = raw.find(b"\r\n\r\n")
    headers = email.parser.BytesHeaderParser().parsebytes(raw[: end if end > 0 else 16384])
    title = decode_subject(headers.get("Subject", ""))
    url = headers.get("Snapshot-Content-Location", "")
    m = POST_ID_IN_URL.search(url)
    return {"title": title or path.stem, "url": url.split("?")[0],
            "post_id": m.group(1) if m else None, "saved": headers.get("Date"), "raw": raw}


def read_md(path):
    raw = path.read_bytes()
    text = raw.decode("utf-8", "replace")
    # Converted posts (scripts/dev-insider-to-md.py) carry JSON-valued front matter.
    front = re.match(r"---\n(.*?)\n---\n", text, re.S)
    if front:
        meta = {k: json.loads(v) for k, v in re.findall(r"^(\w+): (.*)$", front.group(1), re.M)}
        return {"title": meta.get("title") or path.stem, "url": meta.get("url") or "",
                "post_id": meta.get("post_id"), "saved": meta.get("saved"), "raw": raw}
    title = next((l[2:].strip() for l in text.splitlines() if l.startswith("# ")), path.stem)
    ids = Counter(POST_ID_IN_MD.findall(text))
    post_id = ids.most_common(1)[0][0] if ids else None
    url = f"https://www.patreon.com/posts/{post_id}" if post_id else ""
    return {"title": title, "url": url, "post_id": post_id, "saved": None, "raw": raw}


def scan():
    entries = []
    for path in sorted(ROOT.rglob("*")):
        if path.suffix not in (".mhtml", ".md") or path.name == "INDEX.md":
            continue
        doc = (read_mhtml if path.suffix == ".mhtml" else read_md)(path)
        raw = doc.pop("raw")
        folder = path.relative_to(ROOT).parts[0]
        title = SITE_SUFFIX.sub("", doc["title"]).strip()
        series, part = series_part(title, folder)
        entries.append({
            **doc, "title": title, "folder": folder, "series": series, "part": part,
            "access": access_of(title), "file": str(path.relative_to(ROOT)),
            "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
            "title_key": normalise(re.sub(r"^[^\w]+", "", title)),
        })
    return entries


def duplicates(entries):
    found = []
    for key, label in (("post_id", "same post id"), ("sha256", "identical file"),
                       ("title_key", "same title")):
        groups = defaultdict(list)
        for e in entries:
            if e[key]:
                groups[e[key]].append(e["file"])
        found += [{"reason": label, "key": k, "files": f} for k, f in groups.items() if len(f) > 1]
    parts = defaultdict(list)
    for e in entries:
        if e["part"] is not None:
            parts[(normalise(e["series"]), e["part"])].append(e["file"])
    found += [{"reason": "same series part", "key": f"{s} P{p}", "files": f}
              for (s, p), f in parts.items() if len(f) > 1]
    # One pair of files shows up under several reasons; report it once, under the strongest.
    seen, unique = set(), []
    for d in found:
        files = frozenset(d["files"])
        if files not in seen:
            seen.add(files)
            unique.append(d)
    return unique


def gaps(entries):
    """Per folder (one series each): part numbers absent from 1..max, and the posts whose
    title carries no part number, which usually are those parts."""
    parts, unnumbered = defaultdict(set), defaultdict(list)
    for e in entries:
        if e["part"]:
            parts[e["folder"]].add(e["part"])
        elif e["part"] is None:
            unnumbered[e["folder"]].append(e["file"])
    found = {}
    for folder, p in sorted(parts.items()):
        missing = sorted(set(range(1, max(p) + 1)) - p)
        if missing or unnumbered[folder]:
            found[folder] = {"missing": missing, "unnumbered": unnumbered[folder]}
    return found


def write_index(entries, dups, missing):
    public = [{k: v for k, v in e.items() if k != "title_key"} for e in entries]
    (ROOT / "index.json").write_text(json.dumps(public, ensure_ascii=False, indent=1) + "\n")

    lines = ["# dev-insider-docs index", "",
             "Generated by `scripts/dev-insider-index.py`; do not edit. Run "
             "`scripts/dev-insider-index.py check <url>` before saving a new post.", "",
             f"{len(entries)} posts in {len({e['folder'] for e in entries})} folders, "
             f"{sum(e['access'] == 'paid' for e in entries)} paid, "
             f"{sum(e['access'] == 'free' for e in entries)} free.", ""]
    if dups:
        lines += ["## Duplicates", ""] + [
            f"- **{d['reason']}** `{d['key']}`: " + ", ".join(f"`{f}`" for f in d["files"])
            for d in dups] + [""]
    if missing:
        lines += ["## Gaps", "",
                  "Part numbers not seen, next to posts whose title has no part number "
                  "(usually the same posts). A real gap is one the unnumbered posts can't fill.", ""]
        for folder, g in missing.items():
            gap = ", ".join(f"P{p}" for p in g["missing"]) or "none"
            lines.append(f"- **{folder}**: missing {gap}; unnumbered {len(g['unnumbered'])}")
            lines += [f"  - `{f}`" for f in g["unnumbered"]]
        lines.append("")
    no_id = [e["file"] for e in entries if not e["post_id"]]
    if no_id:
        lines += ["## No post id (check by title)", ""] + [f"- `{f}`" for f in no_id] + [""]

    for folder in sorted({e["folder"] for e in entries}):
        rows = sorted((e for e in entries if e["folder"] == folder),
                      key=lambda e: (e["part"] is None, e["part"] or 0, e["title"]))
        lines += [f"## {folder}", "", "| Part | Access | Title | Post id | Saved |",
                  "|---|---|---|---|---|"]
        for e in rows:
            part = "-" if e["part"] is None else f"P{e['part']}"
            post = f"[{e['post_id']}]({e['url']})" if e["post_id"] else "-"
            title = e["title"].replace("|", "\\|")
            lines.append(f"| {part} | {e['access']} | {title} | {post} | {(e['saved'] or '-')[:16]} |")
        lines.append("")
    (ROOT / "INDEX.md").write_text("\n".join(lines))


def check(queries, entries):
    results, any_known = [], False
    for q in queries:
        m = POST_ID_IN_URL.search(q) or re.fullmatch(r"\s*(\d{6,})\s*", q)
        if m:
            hits = [e for e in entries if e["post_id"] == m.group(1)]
        else:
            key = normalise(SITE_SUFFIX.sub("", q))
            hits = [e for e in entries if key and (key in e["title_key"] or e["title_key"] in key)]
        any_known |= bool(hits)
        results.append({"query": q, "saved": bool(hits),
                        "matches": [{"file": e["file"], "post_id": e["post_id"]} for e in hits]})
    print(json.dumps(results, ensure_ascii=False, indent=1))
    return 2 if any_known else 0


def main(argv):
    if not ROOT.is_dir():
        print(json.dumps({"error": f"{ROOT} not found"}))
        return 1
    entries = scan()
    if argv[:1] == ["check"]:
        if len(argv) < 2:
            print(json.dumps({"error": "usage: check <url | post id | title>..."}))
            return 1
        return check(argv[1:], entries)
    dups, missing = duplicates(entries), gaps(entries)
    write_index(entries, dups, missing)
    print(json.dumps({"posts": len(entries), "duplicates": dups, "missing_parts": missing,
                      "no_post_id": [e["file"] for e in entries if not e["post_id"]],
                      "index": str(ROOT / "INDEX.md")}, ensure_ascii=False, indent=1))
    return 2 if dups else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
