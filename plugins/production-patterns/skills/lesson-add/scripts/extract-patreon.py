#!/usr/bin/env python3
"""Extract post content from a saved Patreon page into Markdown.

Patreon renders posts client-side, so a plain fetch returns nothing useful. Saving
the page while logged in embeds the post body in the React Server Component payload
as a TipTap document. This pulls it out.

Accepts either a saved .html page or a .mhtml / .mht MIME archive ("Save as
Webpage, Single File" in Chrome/Edge), whose HTML part is decoded automatically.

Usage:
    extract-patreon.py <saved-page.html> [-o out.md]
    extract-patreon.py *.mhtml -d out/         # batch

Exit codes: 0 extracted, 1 error, 2 content present but not viewable.
"""

import argparse
import email
import email.policy
import json
import re
import sys
from pathlib import Path


def read_page(path):
    """Return the page HTML, decoding a MIME archive if needed."""
    raw = Path(path).read_bytes()

    if Path(path).suffix.lower() not in (".mhtml", ".mht") and not raw.startswith(b"From:"):
        return raw.decode("utf-8", errors="replace")

    msg = email.message_from_bytes(raw, policy=email.policy.default)
    parts = []
    for part in msg.walk():
        if part.get_content_type() == "text/html":
            payload = part.get_payload(decode=True) or b""
            charset = part.get_content_charset() or "utf-8"
            parts.append(payload.decode(charset, errors="replace"))
    if not parts:
        raise ValueError("no text/html part found in MIME archive")
    # The main document is the largest HTML part; sub-frames are much smaller.
    return max(parts, key=len)


def find_doc(html):
    """Locate and parse the TipTap document in the RSC payload."""
    # Chunks look like:  <id>:T<hexlen>,<json>
    for m in re.finditer(r'(\d+):T([0-9a-f]+),(?=\{\\?"type\\?":\\?"doc\\?")', html):
        start = m.end()
        blob = html[start: start + int(m.group(2), 16) + 8192]
        # The payload sits inside a JS string literal, so quotes are escaped.
        for candidate in (blob.replace('\\"', '"').replace('\\\\', '\\'), blob):
            try:
                doc, _ = json.JSONDecoder().raw_decode(candidate)
            except ValueError:
                continue
            if isinstance(doc, dict) and doc.get("type") == "doc":
                return doc
    return None


def meta(html):
    """Pull title and access flags out of the payload."""

    def grab(key, pattern):
        m = re.search(key + pattern, html)
        return m.group(1) if m else None

    return {
        "title": grab(r'\\?"title\\?":\\?"', r'([^"\\]{1,300})'),
        "can_view": grab(r'\\?"current_user_can_view\\?":\\?', r'(true|false)'),
        "is_paid": grab(r'\\?"is_paid\\?":\\?', r'(true|false)'),
    }


def text_of(node):
    if node.get("type") == "text":
        t = node.get("text", "")
        for mark in node.get("marks") or []:
            kind = mark.get("type")
            if kind in ("bold", "strong"):
                t = f"**{t}**"
            elif kind in ("italic", "em"):
                t = f"*{t}*"
            elif kind == "code":
                t = f"`{t}`"
            elif kind == "link":
                href = (mark.get("attrs") or {}).get("href", "")
                t = f"[{t}]({href})" if href else t
        return t
    if node.get("type") in ("hardBreak", "br"):
        return "\n"
    return "".join(text_of(c) for c in node.get("content") or [])


def render(doc):
    out = []
    for node in doc.get("content") or []:
        kind = node.get("type")
        body = text_of(node).strip()
        if kind == "heading":
            level = (node.get("attrs") or {}).get("level", 1)
            if body:
                out.append(f"\n{'#' * min(level, 6)} {body}\n")
        elif kind in ("bulletList", "orderedList"):
            for i, item in enumerate(node.get("content") or [], 1):
                b = text_of(item).strip()
                if b:
                    out.append(("- " if kind == "bulletList" else f"{i}. ") + b)
            out.append("")
        elif kind == "blockquote":
            out.extend(f"> {ln}" for ln in body.splitlines() if ln.strip())
            out.append("")
        elif kind in ("codeBlock", "code_block"):
            lang = (node.get("attrs") or {}).get("language") or ""
            out.append(f"```{lang}\n{text_of(node)}\n```\n")
        elif kind in ("horizontalRule", "horizontal_rule"):
            out.append("\n---\n")
        elif kind == "image":
            attrs = node.get("attrs") or {}
            out.append(f"![{attrs.get('alt') or 'image'}]({attrs.get('src', '')})\n")
        elif body:
            out.append(body + "\n")
    return "\n".join(out).strip() + "\n"


# ---------------------------------------------------------------------------
# Strategy 2: rendered DOM.
#
# A .mhtml archive stores the rendered DOM, so the RSC/TipTap payload is gone
# and the post body exists as ordinary HTML inside div.patreon-post-content.
# ---------------------------------------------------------------------------

from html.parser import HTMLParser

POST_CONTAINER = "patreon-post-content"


def slice_container(html, marker=POST_CONTAINER):
    """Return the inner HTML of the first div carrying `marker` as a class."""
    m = re.search(r'<div[^>]*class="[^"]*' + re.escape(marker) + r'[^"]*"[^>]*>', html)
    if not m:
        return None
    i, depth = m.end(), 1
    for tag in re.finditer(r'<(/?)div\b[^>]*>', html[m.end():]):
        depth += -1 if tag.group(1) else 1
        if depth == 0:
            i = m.end() + tag.start()
            break
    return html[m.end():i]


class DomToMarkdown(HTMLParser):
    """Convert the subset of HTML that Patreon's rich text actually emits."""

    BLOCK = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote",
             "ul", "ol", "li", "pre", "hr", "figure", "table", "tr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.buf = [], []
        self.stack, self.list_stack = [], []
        self.skip = 0

    # -- helpers ----------------------------------------------------------
    def _flush(self):
        text = "".join(self.buf).strip()
        self.buf.clear()
        if not text:
            return
        tag = next((t for t in reversed(self.stack) if t in self.BLOCK), "p")
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.out.append(f"\n{'#' * int(tag[1])} {text}\n")
        elif tag == "blockquote":
            self.out.extend(f"> {ln}" for ln in text.splitlines() if ln.strip())
            self.out.append("")
        elif tag == "li":
            ordered = self.list_stack and self.list_stack[-1] == "ol"
            self.out.append(("1. " if ordered else "- ") + text)
        elif tag == "pre":
            self.out.append(f"```\n{text}\n```\n")
        else:
            self.out.append(text + "\n")

    # -- parser callbacks -------------------------------------------------
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("script", "style", "svg", "button"):
            self.skip += 1
            return
        if self.skip:
            return
        if tag in self.BLOCK:
            self._flush()
            self.stack.append(tag)
            if tag in ("ul", "ol"):
                self.list_stack.append(tag)
        elif tag in ("strong", "b"):
            self.buf.append("**")
        elif tag in ("em", "i"):
            self.buf.append("*")
        elif tag == "code":
            self.buf.append("`")
        elif tag == "a" and a.get("href"):
            self.buf.append("[")
            self.stack.append(("a", a["href"]))
        elif tag == "img" and a.get("src"):
            self._flush()
            self.out.append(f"![{a.get('alt') or 'image'}]({a['src']})\n")
        elif tag == "br":
            self.buf.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "svg", "button"):
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        if tag in self.BLOCK:
            if tag == "hr":
                self.out.append("\n---\n")
            self._flush()
            if tag in self.stack:
                self.stack.reverse();
                self.stack.remove(tag);
                self.stack.reverse()
            if tag in ("ul", "ol") and self.list_stack:
                self.list_stack.pop()
                self.out.append("")
        elif tag in ("strong", "b"):
            self.buf.append("**")
        elif tag in ("em", "i"):
            self.buf.append("*")
        elif tag == "code":
            self.buf.append("`")
        elif tag == "a":
            href = next((h for t, h in
                         [x for x in self.stack if isinstance(x, tuple)][::-1]), "")
            self.buf.append(f"]({href})")
            self.stack = [x for x in self.stack if not isinstance(x, tuple)]

    def handle_data(self, data):
        if not self.skip:
            self.buf.append(data)

    def result(self):
        self._flush()
        md = "\n".join(self.out)
        md = re.sub(r"\*\*\s*\*\*", "", md)  # empty bold from icon spans

        # Drop embedded audio/video player chrome, which is UI, not post content.
        NOISE = re.compile(
            r"^(?:\d+:\d+(?:\s*/\s*\d+:\d+)?"
            r"|Change progress|Change volume|Mute|Unmute|Play|Pause"
            r"|We couldn't load the (?:audio|video) player.*"
            r"|!\[image\]\(https://image\.mux\.com/.*"
            r"|!\[image\]\(https://c10\.patreonusercontent\.com/4/patreon-media/p/campaign/.*"
            r")\s*$")
        kept = [ln for ln in md.splitlines() if not NOISE.match(ln.strip())]

        md = re.sub(r"\n{3,}", "\n\n", "\n".join(kept))
        return md.strip() + "\n"


def render_dom(html):
    """Return Markdown, or a (None, reason) pair when extraction is not possible."""
    inner = slice_container(html)
    if inner is None:
        return None
    parser = DomToMarkdown()
    parser.feed(inner)
    md = parser.result()
    if len(md) > 400:
        return md
    # The container exists but holds no prose: the page was saved before the
    # post body finished rendering. Distinguish it from "container missing".
    render_dom.empty_container = True
    return None


def process(path, out_path):
    try:
        html = read_page(path)
    except (OSError, ValueError) as exc:
        print(f"{path}: {exc}", file=sys.stderr)
        return 1
    info = meta(html)
    doc = find_doc(html)
    md = render(doc) if doc is not None else render_dom(html)

    if md is None:
        if info["can_view"] == "false":
            print(f"{path}: post is not viewable by the saved session "
                  f"(is_paid={info['is_paid']}) — no content to extract", file=sys.stderr)
            return 2
        if getattr(render_dom, "empty_container", False):
            render_dom.empty_container = False
            print(f"{path}: post container is present but empty — the page was saved "
                  f"before the body rendered. Reopen it, let the post display fully, "
                  f"then save again.", file=sys.stderr)
        else:
            print(f"{path}: no post content found. Was the page saved as "
                  f"'Webpage, Complete' (or .mhtml) while logged in?", file=sys.stderr)
        return 1

    if info["title"]:
        md = f"# {info['title']}\n\n{md}"
    Path(out_path).write_text(md, encoding="utf-8")
    print(f"{path} -> {out_path}  ({len(md)} chars, {md.count(chr(10)) + 1} lines)")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pages", nargs="+", help="saved .html or .mhtml page(s)")
    ap.add_argument("-o", "--output", help="output file (single input only)")
    ap.add_argument("-d", "--out-dir", help="output directory for batch mode")
    args = ap.parse_args()

    if args.output and len(args.pages) > 1:
        ap.error("-o takes a single input; use -d for batch")

    worst = 0
    for page in args.pages:
        if args.output:
            dest = args.output
        else:
            d = Path(args.out_dir or ".")
            d.mkdir(parents=True, exist_ok=True)
            dest = d / (Path(page).stem + ".md")
        worst = max(worst, process(page, dest))
    return worst


if __name__ == "__main__":
    sys.exit(main())
