#!/usr/bin/env python3
"""Convert dev-insider-docs/**/*.mhtml (Chrome "single file" saves) to Markdown.

Each post becomes <folder>/<same name>.md with front matter (title, url, post_id, saved,
access), and the images Chrome embedded are written to <folder>/assets/<post_id>/. Images
Chrome never loaded (lazy-loaded, not scrolled to) keep their Patreon URL; those signed URLs
expire, so they were already lost and the conversion loses nothing.

A post passes only if the Markdown holds >= 98% of the body's words and every embedded image.
Passing .mhtml files are moved to the macOS Trash with --trash; nothing is deleted outright.

  scripts/dev-insider-to-md.py            convert and verify, keep the .mhtml
  scripts/dev-insider-to-md.py --trash    also move verified .mhtml files to ~/.Trash

JSON on stdout. Exit codes: 0 all passed, 2 some failed (their .mhtml is kept), 1 error.
Needs pandoc on PATH.
"""

import email
import hashlib
import html as htmllib
import importlib.util
import json
import mimetypes
import re
import shutil
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("dev_insider_index", HERE / "dev-insider-index.py")
index = importlib.util.module_from_spec(spec)
spec.loader.exec_module(index)
ROOT = index.ROOT
TRASH = Path.home() / ".Trash" / "dev-insider-mhtml"
MIN_COVERAGE = 0.98


class BodyFinder(HTMLParser):
    """Finds the start and end offsets of the element whose class is patreon-post-content."""

    def __init__(self, source):
        super().__init__(convert_charrefs=False)
        self.source, self.tag, self.depth = source, None, 0
        self.start = self.end = None
        self.line_starts = [0] + [m.end() for m in re.finditer("\n", source)]

    def position(self):
        line, col = self.getpos()
        return self.line_starts[line - 1] + col

    def handle_starttag(self, tag, attrs):
        if self.end is not None:
            return
        if self.tag is None:
            if "patreon-post-content" in (dict(attrs).get("class") or "").split():
                self.tag, self.depth, self.start = tag, 1, self.position()
        elif tag == self.tag:
            self.depth += 1

    def handle_endtag(self, tag):
        if self.tag == tag and self.end is None:
            self.depth -= 1
            if self.depth == 0:
                self.end = self.source.find(">", self.position()) + 1


def words(text):
    return re.findall(r"\w+", text.casefold())


def convert(path):
    raw = path.read_bytes()
    msg = email.message_from_bytes(raw)
    meta = index.read_mhtml(path)
    meta.pop("raw")
    title = index.SITE_SUFFIX.sub("", meta["title"]).strip()
    page = next(p for p in msg.walk() if p.get_content_type() == "text/html")
    source = page.get_payload(decode=True).decode(page.get_content_charset() or "utf-8", "replace")
    parts = {p["Content-Location"]: p for p in msg.walk()
             if p["Content-Location"] and p.get_content_maintype() == "image"}

    finder = BodyFinder(source)
    finder.feed(source)
    if finder.start is None or finder.end is None:
        return {"file": str(path.relative_to(ROOT)), "ok": False, "error": "no post body found"}
    body = source[finder.start:finder.end]

    post_id = meta["post_id"] or hashlib.sha256(raw).hexdigest()[:12]
    assets = path.parent / "assets" / post_id
    saved, unsaved = [], []

    def localise(m):
        url = htmllib.unescape(m.group(2))
        part = parts.get(url)
        if part is None:
            unsaved.append(url)
            return m.group(0)
        data = part.get_payload(decode=True)
        ext = mimetypes.guess_extension(part.get_content_type()) or ".bin"
        name = f"{len(saved) + 1}{'.jpg' if ext == '.jpe' else ext}"
        assets.mkdir(parents=True, exist_ok=True)
        (assets / name).write_bytes(data)
        saved.append(name)
        return f'{m.group(1)}"assets/{post_id}/{name}"'

    body = re.sub(r'(<img\b[^>]*?\bsrc=)"([^"]+)"', localise, body)
    body = re.sub(r'\s(?:srcset|sizes)="[^"]*"', "", body)
    markdown = subprocess.run(
        ["pandoc", "-f", "html", "-t", "gfm-raw_html", "--wrap=none"],
        input=body, capture_output=True, text=True, check=True).stdout

    # Embedded audio/video players leave their icons, timestamps and a load error behind.
    media = len(re.findall(r"We couldn't load the \w+ player!", markdown))
    clean = re.sub(r"!\[[^\]]*\]\(data:[^)]*\)\n*", "", markdown)
    clean = re.sub(r"^(?:\d{1,2}:\d{2}(?::\d{2})?|Change progress)\n+", "", clean, flags=re.M)
    clean = re.sub(r"We couldn't load the (\w+) player! Please try a different browser\.",
                   r"*(\1 not saved)*", clean)

    front = {"title": title, "url": meta["url"], "post_id": meta["post_id"],
             "saved": meta["saved"], "access": index.access_of(title),
             "source": path.name, "images_saved": len(saved), "images_lost": len(unsaved),
             "media_lost": media}
    head = "---\n" + "".join(f"{k}: {json.dumps(v, ensure_ascii=False)}\n" for k, v in front.items())
    out = path.with_suffix(".md")
    out.write_text(f"{head}---\n\n# {title}\n\n{clean.strip()}\n")

    # Verify the written text: every word of the body survives, less the player chrome above.
    body_text = htmllib.unescape(re.sub(r"<[^>]+>", " ", body))
    body_text = re.sub(r"We couldn't load the \w+ player! Please try a different browser\.|"
                       r"\b\d{1,2}:\d{2}(?::\d{2})?\b|Change progress", " ", body_text)
    want, have = words(body_text), set(words(clean))
    coverage = sum(w in have for w in want) / len(want) if want else 1.0
    images_ok = all(f"assets/{post_id}/{n}" in clean for n in saved)
    ok = coverage >= MIN_COVERAGE and images_ok and len(want) > 0
    if not want:
        out.unlink()
    return {"file": str(path.relative_to(ROOT)), "ok": ok, "coverage": round(coverage, 4),
            "error": "empty body: saved before the post loaded, download again" if not want else None,
            "words": len(want), "images_saved": len(saved), "images_lost": len(unsaved),
            "images_ok": images_ok, "md": str(out.relative_to(ROOT))}


def main(argv):
    if shutil.which("pandoc") is None:
        print(json.dumps({"error": "pandoc not found"}))
        return 1
    trash = "--trash" in argv
    results = [convert(p) for p in sorted(ROOT.rglob("*.mhtml"))]
    if trash:
        for r in results:
            if r["ok"]:
                dest = TRASH / r["file"]
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(ROOT / r["file"]), dest)
                r["trashed"] = str(dest)
    failed = [r for r in results if not r["ok"]]
    print(json.dumps({"converted": len(results), "passed": len(results) - len(failed),
                      "failed": failed, "images_saved": sum(r.get("images_saved", 0) for r in results),
                      "images_lost": sum(r.get("images_lost", 0) for r in results),
                      "min_coverage": min((r.get("coverage", 0) for r in results), default=None),
                      "trashed_to": str(TRASH) if trash else None}, ensure_ascii=False, indent=1))
    return 2 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
