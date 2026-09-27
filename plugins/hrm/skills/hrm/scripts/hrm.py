#!/usr/bin/env python3
"""Small CLI for New Era HRM (x-hrm), made for agents. Standard library only.

Every command prints JSON to stdout. Errors print JSON to stderr and exit non-zero:
  1 = HRM answered with an error (4xx/5xx)   2 = bad usage / config   3 = network

Config (env): HRM_BASE_URL  the address you open HRM at, e.g. http://localhost:8000 (no /api)
              HRM_API_TOKEN hrm_pat_…  (Settings → API tokens in HRM)
              HRM_LANG      en | vi — language of HRM's error messages (default en)
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

SOURCES = ["LINKEDIN", "INTERNAL_REFERRAL", "FACEBOOK_GROUP", "JOB_BOARD", "DIRECT", "AGENCY", "WEBSITE", "OTHER"]
CV_SUFFIXES = {".pdf", ".docx", ".md", ".txt"}
DONE, ERROR = "done", "error"


class HrmError(Exception):
    def __init__(self, status: int, detail, code: int = 1):
        super().__init__(str(detail))
        self.status, self.detail, self.code = status, detail, code


# ------------------------------------------------------------------ transport
def _config() -> tuple[str, str]:
    base = os.environ.get("HRM_BASE_URL", "").rstrip("/")
    token = os.environ.get("HRM_API_TOKEN", "")
    if not base or not token:
        raise HrmError(0, "HRM_BASE_URL and HRM_API_TOKEN must be set — see the hrm skill install guide", 2)
    if base.endswith("/api"):
        base = base[:-4]
    return base, token


def call(method: str, path: str, *, query: dict | None = None, body: dict | None = None,
         multipart: tuple[dict, tuple[str, str, bytes]] | None = None):
    base, token = _config()
    url = base + path
    if query:
        q = {k: v for k, v in query.items() if v is not None and v != ""}
        if q:
            url += "?" + urllib.parse.urlencode(q)
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json",
               "Accept-Language": os.environ.get("HRM_LANG", "en")}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    elif multipart is not None:
        fields, (fname, ctype, content) = multipart
        boundary = uuid.uuid4().hex
        parts = []
        for k, v in fields.items():
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
        safe = fname.replace('"', "")
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{safe}"\r\n'
                     f"Content-Type: {ctype}\r\n\r\n".encode() + content + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        data = b"".join(parts)
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            raw = r.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            detail = json.loads(raw).get("detail", raw.decode(errors="replace"))
        except (ValueError, AttributeError):
            detail = raw.decode(errors="replace")[:500]
        raise HrmError(e.code, detail) from None
    except urllib.error.URLError as e:
        raise HrmError(0, f"cannot reach {base}: {e.reason}", 3) from None


def wait_job(job: dict | None, timeout: float) -> dict | None:
    """Poll an AI job until done/error or timeout. Returns the latest job state."""
    if not job:
        return job
    deadline = time.monotonic() + timeout
    delay = 1.5
    while job["status"] not in (DONE, ERROR) and time.monotonic() < deadline:
        time.sleep(delay)
        delay = min(delay * 1.5, 8)
        job = call("GET", f"/api/ai/jobs/{job['id']}")
    return job


# ------------------------------------------------------------------- commands
def cmd_whoami(a):
    return call("GET", "/api/me")


def cmd_positions(a):
    rows = call("GET", "/api/requisitions", query={"status": None if a.all else "OPEN"})
    needle = (a.q or "").casefold()
    return [{"id": r["id"], "title": r["title"], "level": r["level"], "status": r["status"],
             "department": r.get("department_name"), "headcount": r.get("headcount")}
            for r in rows if needle in r["title"].casefold()]


def cmd_search(a):
    rows = call("GET", "/api/applications/board",
                query={"q": a.query, "requisition_id": a.position, "include_closed": "true" if a.all else None})
    keep = ("applicationId", "candidateId", "name", "email", "requisitionId", "requisitionTitle",
            "departmentName", "stage", "source", "startedAt", "reviewScore", "matchPercentage")
    return [{k: r.get(k) for k in keep} for r in rows]


def cmd_upload(a):
    results = []
    for f in a.files:
        p = Path(f)
        item = {"file": str(p)}
        try:
            if not p.is_file():
                raise HrmError(0, "file not found", 2)
            if p.suffix.lower() not in CV_SUFFIXES:
                raise HrmError(0, f"unsupported type; use one of {sorted(CV_SUFFIXES)}", 2)
            ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
            fields = {"requisition_id": str(a.position), "source": a.source}
            if not a.allow_duplicate:
                fields["if_new"] = "true"
            out = call("POST", "/api/candidates/from-cv", multipart=(fields, (p.name, ctype, p.read_bytes())))
            item.update(status="created", applicationId=out["applicationId"], documentId=out["documentId"],
                        candidateId=out["candidate"]["id"])
            if not a.no_wait:
                # The extraction job may MERGE into an existing candidate (same email):
                # the candidate id from the job result is the one that survives.
                job = wait_job(out.get("job"), a.timeout)
                item["extraction"] = _job_brief(job)
                if job and job["status"] == DONE and isinstance(job.get("result"), dict):
                    res = job["result"]
                    item.update(candidateId=res.get("candidateId", item["candidateId"]), name=res.get("name"),
                                merged=res.get("merged"), missingFields=res.get("missingFields"))
            if a.review:
                item["review"] = _review(item["documentId"], force=False, run=True, timeout=a.timeout)
        except HrmError as e:
            item.update(status="duplicate" if e.status == 409 else "failed", error=e.detail)
        results.append(item)
    return results


def cmd_review(a):
    evals = call("GET", f"/api/candidates/{a.candidate}/evaluations")
    if not evals:
        raise HrmError(404, "candidate has no applications (or does not exist)")
    doc_id = next((e["cvDocumentId"] for e in evals if e.get("cvDocumentId")), None)
    general = None
    if doc_id is not None:
        general = _review(doc_id, force=a.force, run=a.run or a.force, timeout=a.timeout)
    apps = []
    for e in evals:
        match = e.get("match")
        if match is None and a.match and doc_id is not None:
            job = call("POST", f"/api/applications/{e['applicationId']}/match")["job"]
            job = wait_job(job, a.timeout)
            if job and job["status"] == DONE:
                match = next((x.get("match") for x in call("GET", f"/api/candidates/{a.candidate}/evaluations")
                              if x["applicationId"] == e["applicationId"]), None)
            else:
                match = {"pending": _job_brief(job)}
        apps.append({"applicationId": e["applicationId"], "position": e["requisitionTitle"], "level": e["level"],
                     "stage": e["stage"], "match": match})
    return {"candidateId": a.candidate, "cvDocumentId": doc_id, "cvFilename": evals[0].get("cvFilename"),
            "hasCvText": any(e.get("hasCv") for e in evals), "review": general, "applications": apps}


def _review(doc_id: int, *, force: bool, run: bool, timeout: float):
    """The stored general review of a CV; with `run`, create it (or redo it with `force`)."""
    if not force:
        try:
            return call("GET", f"/api/cvs/{doc_id}/review")
        except HrmError as e:
            if e.status != 404:
                raise
            if not run:
                return None
    out = call("POST", f"/api/cvs/{doc_id}/review", query={"force": "true" if force else None})
    if out.get("review"):
        return out["review"]
    job = wait_job(out.get("job"), timeout)
    if job and job["status"] == DONE:
        return call("GET", f"/api/cvs/{doc_id}/review")
    return {"pending": _job_brief(job)}


def cmd_job(a):
    job = call("GET", f"/api/ai/jobs/{a.id}")
    return wait_job(job, a.timeout) if a.wait else job


def _job_brief(job):
    if not job:
        return None
    return {k: job.get(k) for k in ("id", "kind", "status", "error", "result")}


# ------------------------------------------------------------------------ cli
def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="hrm", description="New Era HRM for agents — prints JSON")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("whoami", help="check config: who the token belongs to")

    s = sub.add_parser("positions", help="list open positions (requisitions)")
    s.add_argument("--q", help="filter by title substring")
    s.add_argument("--all", action="store_true", help="include closed / on-hold positions")

    s = sub.add_parser("search", help="find candidates' applications by name or email")
    s.add_argument("query", nargs="?", default="")
    s.add_argument("--position", type=int, help="only this requisition id")
    s.add_argument("--all", action="store_true", help="include hired / rejected / withdrawn")

    s = sub.add_parser("upload", help="upload CV file(s) as new candidates for a position (write token)")
    s.add_argument("files", nargs="+")
    s.add_argument("--position", type=int, required=True, help="requisition id (see: positions)")
    s.add_argument("--source", default="OTHER", choices=SOURCES)
    s.add_argument("--allow-duplicate", action="store_true",
                   help="upload even if this exact file is already in this position")
    s.add_argument("--review", action="store_true", help="also run the general CV review")
    s.add_argument("--no-wait", action="store_true", help="don't wait for AI extraction")
    s.add_argument("--timeout", type=float, default=180)

    s = sub.add_parser("review", help="a candidate's CV review + per-position match results")
    s.add_argument("candidate", type=int, help="candidate id (see: search)")
    s.add_argument("--run", action="store_true", help="run the review if there is none yet (write token)")
    s.add_argument("--force", action="store_true", help="re-run the review even if one exists (costs AI credits)")
    s.add_argument("--match", action="store_true", help="also run JD matching where missing (write token)")
    s.add_argument("--timeout", type=float, default=180)

    s = sub.add_parser("job", help="show (or wait for) an AI job")
    s.add_argument("id", type=int)
    s.add_argument("--wait", action="store_true")
    s.add_argument("--timeout", type=float, default=180)

    a = p.parse_args(argv)
    try:
        out = globals()[f"cmd_{a.cmd}"](a)
    except HrmError as e:
        print(json.dumps({"error": e.detail, "status": e.status}, ensure_ascii=False), file=sys.stderr)
        return e.code
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    if a.cmd == "upload" and any(r["status"] == "failed" for r in out):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
