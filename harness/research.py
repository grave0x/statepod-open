#!/usr/bin/env python3
"""Self-contained search/research handler for statepod (`sp research`).

One module, stdlib-only runtime: web search (no API keys), URL fetch,
and decode of the common formats an agent actually hits — HTML source,
PDF, DOCX/EPUB/ODT/XLSX (zip containers), RTF, JSON/XML/RSS, CSV,
plain text/markdown.  External binaries are used only as *quality
upgrades* when present (poppler's `pdftotext` for PDFs); every decoder
has a pure-Python fallback so the handler works on a bare box.

Hand-off contract with the `deep-analysis` skill:
  `sp research <query|url|file…> [--save NAME]` gathers + decodes and
  stores a *machine-labelled* knowledge kit under `research/<topic>/` —
  every source row is ✅ (fetched & decoded), ⚠️ (degraded/partial
  decode) or ✗ (fetch/decode failed) with the reason and a human
  verification path, exactly the discipline the deep-analysis skill
  demands.  Add `--deep` to have the configured brain (grok via `hw`,
  or deepseek) draft the verified narrative on top of the labelled
  manifest.

Layout of a stored kit:
    research/<topic>/
    ├── README.md        # index: scope, source-status table, gaps, sources
    ├── narrative.md     # (--deep) brain-drafted narrative + gap list
    ├── sources/raw-N.ext    # fetched bytes as received
    ├── sources/text-N.txt   # decoded text
    └── sources.jsonl    # machine-readable manifest (the label store)

Module is importable (search/fetch/decode/gather/write_kit) and has a
small CLI for standalone use / tests:
    python3 harness/research.py search "…" | fetch https://… | decode f.pdf
"""
from __future__ import annotations

import base64
import gzip
import html
import io
import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import sys
import zipfile
import zlib
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional

VERSION = "0.1.0"
KIT = "deep-analysis-labelled"  # storage contract version marker

UA_CHROME = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
UA_FIREFOX = ("Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 "
              "Firefox/128.0")

MAX_BYTES = 25 * 1024 * 1024      # per-fetch cap (raw)
MAX_DECODE_CHARS = 400_000        # decoded text cap written per source
ENGINE_TIMEOUT = 12               # seconds per search engine
FETCH_TIMEOUT = 20
PDF_TEXT_TIMEOUT = 30

_URL_RE = re.compile(r"^(?:https?://|www\.)\S+$", re.I)
_IMG_MAGIC = (b"\x89PNG", b"GIF8", b"\xff\xd8\xff", b"RIFF")
_PAPERISH = re.compile(r"\b(arxiv|paper|survey|benchmark|thesis|abstract)\b",
                       re.I)
_TRACKED = re.compile(r"^(?:utm_|fbclid|gclid|ref|source|mc_)[^=]+=.*$")
_SLUG = re.compile(r"[^a-z0-9]+")
_FMT_EXT = {"pdf": "pdf", "html": "html", "docx": "docx", "xlsx": "xlsx",
            "epub": "epub", "odt": "odt", "rtf": "rtf", "json": "json",
            "xml": "xml", "csv": "csv", "md": "md", "txt": "txt",
            "zip": "zip", "image": "img", "binary": "bin"}


# ---------------------------------------------------------------- data
@dataclass
class Result:
    url: str
    title: str = ""
    snippet: str = ""
    engine: str = ""
    pdf: str = ""          # arxiv: prefer fetching this over the abs page
    rank: int = 0


@dataclass
class EngineOutcome:
    engine: str
    ok: bool
    results: list = field(default_factory=list)
    error: str = ""


@dataclass
class Fetch:
    ok: bool
    status: int = 0
    final_url: str = ""
    content_type: str = ""
    charset: str = ""
    raw: bytes = b""
    too_big: bool = False
    error: str = ""


@dataclass
class Decoded:
    text: str = ""
    fmt: str = ""              # html|pdf|docx|… (see sniff_fmt)
    method: str = ""           # decoder used, e.g. poppler-pdftotext
    quality: str = "none"      # full|partial|degraded|none
    note: str = ""
    error: str = ""


@dataclass
class Source:
    id: str
    kind: str                  # search|url|file
    engine: str = ""
    title: str = ""
    url: str = ""
    path: str = ""
    fetch: Optional[Fetch] = None
    decoded: Optional[Decoded] = None
    error: str = ""

    @property
    def status(self) -> str:
        """deep-analysis label: ok (✅) / partial (⚠️) / failed (✗)."""
        if self.error or not self.fetch or not self.fetch.ok:
            return "failed"
        if not self.decoded or not self.decoded.text.strip():
            return "failed"
        if self.decoded.quality == "degraded":
            return "partial"
        return "ok"

    @property
    def display(self) -> str:
        return self.title or self.url or self.path or self.id


@dataclass
class Corpus:
    topic: str
    query: str = ""
    sources: list = field(default_factory=list)
    engine_notes: list = field(default_factory=list)
    ts: float = field(default_factory=time.time)

    def counts(self) -> dict:
        c = {"ok": 0, "partial": 0, "failed": 0}
        for s in self.sources:
            c[s.status] += 1
        c["total"] = len(self.sources)
        return c


# ---------------------------------------------------------------- http
def _http(url: str, *, timeout: float = FETCH_TIMEOUT,
          max_bytes: int = MAX_BYTES, ua: str = UA_CHROME) -> Fetch:
    """GET url -> Fetch. urllib-only; follows redirects, gunzips, caps size."""
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": ua,
            "Accept": ("text/html,application/xhtml+xml,application/pdf,"
                       "application/json;q=0.9,*/*;q=0.8"),
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate",
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ctype = (resp.headers.get("Content-Type") or "").split(";")[0]
            m = re.search(r"charset=([\w-]+)",
                          resp.headers.get("Content-Type") or "", re.I)
            enc = (resp.headers.get("Content-Encoding") or "").lower()
            if enc == "gzip":
                raw = gzip.decompress(resp.read())
            elif enc == "deflate":
                try:
                    raw = zlib.decompress(resp.read())
                except Exception:
                    raw = b""  # rare mislabeled; refetch below would be overkill
            else:
                raw = resp.read(max_bytes + 1)
            too_big = len(raw) > max_bytes
            raw = raw[:max_bytes]
            return Fetch(ok=True, status=resp.status,
                         final_url=resp.geturl(),
                         content_type=ctype.strip(),
                         charset=m.group(1) if m else "",
                         raw=raw, too_big=too_big or len(raw) >= max_bytes)
    except urllib.error.HTTPError as exc:
        return Fetch(ok=False, status=exc.code, error=f"HTTP {exc.code}")
    except Exception as exc:  # URLError, timeout, ssl, socket…
        return Fetch(ok=False, error=f"{type(exc).__name__}: {exc}")


def _normalize_url(url: str) -> str:
    """Dedupe key: drop fragments, tracking params, trailing slash."""
    try:
        p = urllib.parse.urlsplit(url)
        q = [kv for kv in urllib.parse.parse_qsl(p.query)
             if not _TRACKED.match(kv[0])]
        path = p.path.rstrip("/") or "/"
        return urllib.parse.urlunsplit(
            (p.scheme.lower(), p.netloc.lower(), path,
             urllib.parse.urlencode(q), ""))
    except Exception:
        return url


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


# ---------------------------------------------------------------- engines
def _bing_decode(href: str) -> str:
    """Bing wraps results in /ck/a?…&u=a1<base64url>; unwrap when present."""
    if "bing.com/ck/a" not in href and "cn.bing.com/ck/a" not in href:
        return href
    q = urllib.parse.urlsplit(html.unescape(href)).query
    u = urllib.parse.parse_qs(q).get("u", [""])[0]
    if not u.startswith("a1"):
        return href
    try:
        b64 = u[2:] + "=" * (-len(u[2:]) % 4)
        real = base64.urlsafe_b64decode(b64).decode("utf-8", "replace")
        return real if real.startswith(("http://", "https://")) else href
    except Exception:
        return href


def _clean(blob: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", blob)).strip()


def search_bing(query: str, limit: int = 10) -> EngineOutcome:
    """Bing HTML scrape (no key). Best-effort: bot walls yield zero blocks."""
    url = ("https://www.bing.com/search?q=" + urllib.parse.quote(query)
           + "&count=" + str(min(limit, 20)))
    f = _http(url, timeout=ENGINE_TIMEOUT)
    if not f.ok:
        return EngineOutcome("bing", False, [], f.error)
    text = f.raw.decode("utf-8", "replace")
    out: list[Result] = []
    for block in re.findall(r'<li class="b_algo".*?</li>', text, re.S):
        m = re.search(r'<h2[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
                      block, re.S)
        if not m:
            continue
        real = _bing_decode(m.group(1))
        if not real.startswith(("http://", "https://")):
            continue
        pm = re.search(r"<p[^>]*>(.*?)</p>", block, re.S)
        out.append(Result(url=real, title=_clean(m.group(2)),
                          snippet=_clean(pm.group(1)) if pm else "",
                          engine="bing", rank=len(out) + 1))
        if len(out) >= limit:
            break
    if not out:
        return EngineOutcome("bing", False, [],
                             "no result blocks (bot-walled or markup change)")
    return EngineOutcome("bing", True, out)


def search_ddg(query: str, limit: int = 10) -> EngineOutcome:
    """DuckDuckGo html endpoint. Frequently bot-walled -> caller falls through."""
    url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query)
    f = _http(url, timeout=ENGINE_TIMEOUT, ua=UA_FIREFOX)
    if not f.ok:
        return EngineOutcome("ddg", False, [], f.error)
    text = f.raw.decode("utf-8", "replace")
    if "result__a" not in text:
        why = ("anomaly wall" if re.search(r"anomaly|challenge", text, re.I)
               else "no result markup")
        return EngineOutcome("ddg", False, [], why)
    out: list[Result] = []
    for m in re.finditer(
            r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', text, re.S):
        href = html.unescape(m.group(1))
        if "/l/?uddg=" in href:  # ddg redirect wrapper
            q = urllib.parse.parse_qs(
                urllib.parse.urlsplit(href).query).get("uddg", [""])
            href = urllib.parse.unquote(q[0]) if q else href
        out.append(Result(url=href, title=_clean(m.group(2)), engine="ddg",
                          rank=len(out) + 1))
        if len(out) >= limit:
            break
    return EngineOutcome("ddg", bool(out), out,
                         "" if out else "no parseable results")


def search_wikipedia(query: str, limit: int = 10) -> EngineOutcome:
    """Wikipedia full-text API — stable, high-quality, keyless."""
    url = ("https://en.wikipedia.org/w/api.php?action=query&list=search"
           "&format=json&srsearch=" + urllib.parse.quote(query)
           + "&srlimit=" + str(min(limit, 20)) + "&srprop=snippet")
    f = _http(url, timeout=ENGINE_TIMEOUT)
    if not f.ok:
        return EngineOutcome("wikipedia", False, [], f.error)
    try:
        data = json.loads(f.raw.decode("utf-8", "replace"))
    except Exception as exc:
        return EngineOutcome("wikipedia", False, [], f"bad json: {exc}")
    out: list[Result] = []
    for hit in data.get("query", {}).get("search", []):
        title = hit.get("title", "")
        page = urllib.parse.quote(title.replace(" ", "_"))
        out.append(Result(url=f"https://en.wikipedia.org/wiki/{page}",
                          title=title,
                          snippet=_clean(hit.get("snippet", "")),
                          engine="wikipedia", rank=len(out) + 1))
        if len(out) >= limit:
            break
    return EngineOutcome("wikipedia", bool(out), out,
                         "" if out else "no hits")


def search_arxiv(query: str, limit: int = 5) -> EngineOutcome:
    """arXiv API (Atom). Only meaningful for paper-ish queries."""
    url = ("https://export.arxiv.org/api/query?search_query=all:"
           + urllib.parse.quote('"' + query + '"')
           + "&start=0&max_results=" + str(min(limit, 20))
           + "&sortBy=relevance&sortOrder=descending")
    f = _http(url, timeout=ENGINE_TIMEOUT)
    if not f.ok:
        return EngineOutcome("arxiv", False, [], f.error)
    text = f.raw.decode("utf-8", "replace")
    out: list[Result] = []
    for entry in re.findall(r"<entry>.*?</entry>", text, re.S):
        def g(tag: str) -> str:
            m = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", entry, re.S)
            return _clean(m.group(1)) if m else ""
        abs_url = g("id").replace("http://", "https://")
        pdf = re.search(r'<link[^>]*title="pdf"[^>]*href="([^"]+)"', entry)
        out.append(Result(url=abs_url, title=html.unescape(g("title")),
                          snippet=g("summary"), engine="arxiv",
                          rank=len(out) + 1,
                          pdf=(pdf.group(1).replace("http://", "https://")
                               if pdf else "")))
        if len(out) >= limit:
            break
    return EngineOutcome("arxiv", bool(out), out,
                         "" if out else "no hits")


ENGINES = {"bing": search_bing, "ddg": search_ddg,
           "wikipedia": search_wikipedia, "arxiv": search_arxiv}
DEFAULT_ENGINES = ("bing", "ddg", "wikipedia")


def search(query: str, limit: int = 12, engines=None,
           paper_hint: bool | None = None) -> tuple[list[Result], list[str]]:
    """Multi-engine search with fallthrough. Returns (results, notes).

    Earlier engines are preferred; results dedupe by normalized URL.
    arxiv is consulted when the query looks paper-ish, or as a last
    resort when every other engine came back empty.
    """
    engines = tuple(engines) if engines else DEFAULT_ENGINES
    notes: list[str] = []
    merged: list[Result] = []
    seen: set[str] = set()
    want_arxiv = (paper_hint if paper_hint is not None
                  else bool(_PAPERISH.search(query)))
    eff = (list(engines) + (["arxiv"] if want_arxiv and "arxiv" not in engines
                            else []))

    def add(outcome: EngineOutcome) -> None:
        if not outcome.ok:
            notes.append(f"{outcome.engine}: {outcome.error}")
            return
        for r in outcome.results:
            key = _normalize_url(r.pdf or r.url)
            if not key or key in seen:
                continue
            seen.add(key)
            merged.append(r)

    for name in eff:
        if name not in ENGINES:
            continue
        # resolve live each call so monkeypatches/tests apply
        fn = globals().get("search_" + name)
        if fn is None:
            continue
        try:
            add(fn(query, limit=max(5, limit)))
        except Exception as exc:  # never let one engine kill the search
            notes.append(f"{name}: exception {exc}")
        if len(merged) >= limit:
            break
    if not merged and not want_arxiv:
        try:
            add(search_arxiv(query, limit=limit))
        except Exception:
            pass
    return merged[:limit], notes


# ---------------------------------------------------------------- decode
def _looks_json(raw: bytes) -> bool:
    try:
        json.loads(raw[:1_000_000].decode("utf-8", "replace"))
        return True
    except Exception:
        return False


def _looks_text(raw: bytes) -> bool:
    if not raw or b"\x00" in raw[:8192]:
        return False
    sample = raw[:8192]
    try:
        sample.decode("utf-8")
    except Exception:
        return False
    bad = sum(1 for b in sample if b < 9 or (13 < b < 32 and b != 27))
    return bad < len(sample) * 0.05


def sniff_fmt(raw: bytes, ctype: str = "", hint: str = "") -> str:
    """Format by magic bytes, then content-type, then extension hint."""
    if not raw:
        return "empty"
    if raw.startswith(b"%PDF"):
        return "pdf"
    if raw.startswith(b"PK\x03\x04"):
        return _zip_fmt(raw)
    if raw.startswith(b"{\\rtf"):
        return "rtf"
    if any(raw.startswith(m) for m in _IMG_MAGIC):
        return "image"
    head = raw[:512].lstrip().lower()
    if head.startswith(b"<!doctype html") or b"<html" in raw[:2048].lower() \
            or head.startswith(b"<head") or head.startswith(b"<body"):
        return "html"
    if re.match(rb"<(?:rss|feed|rdf|opml)[\s>]", head):
        return "xml"
    ct = (ctype or "").lower()
    if "pdf" in ct:
        return "pdf"
    if "html" in ct:
        return "html"
    if "json" in ct:
        return "json"
    if any(k in ct for k in ("xml", "rss", "atom", "xhtml")):
        return "xml"
    if "rtf" in ct:
        return "rtf"
    if "csv" in ct:
        return "csv"
    if raw.lstrip().startswith(b"<?xml"):
        return "xml"
    if raw.lstrip()[:1] in (b"{", b"[") and _looks_json(raw):
        return "json"
    ext = (Path(urllib.parse.urlsplit(hint).path).suffix.lower()
           if hint else "")
    if ext in (".json",):
        return "json"
    if ext in (".html", ".htm", ".xhtml"):
        return "html"
    if ext in (".pdf",):
        return "pdf"
    if ext in (".csv",):
        return "csv"
    if ext in (".md", ".markdown"):
        return "md"
    if ext in (".rtf",):
        return "rtf"
    if ext in (".xml", ".rss", ".atom"):
        return "xml"
    if ext in (".txt", ".log", ".text"):
        return "txt"
    if _looks_text(raw):
        return "txt"
    return "binary"


def _zip_fmt(raw: bytes) -> str:
    """docx / xlsx / epub / odt / plain zip by container contents."""
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            names = z.namelist()
            mimetype = ""
            if "mimetype" in names:
                try:
                    mimetype = z.read("mimetype").decode("ascii", "replace")
                except Exception:
                    pass
            if mimetype.startswith("application/epub"):
                return "epub"
            if mimetype.startswith(
                    "application/vnd.oasis.opendocument.text"):
                return "odt"
            if "[Content_Types].xml" in names:
                if "word/document.xml" in names:
                    return "docx"
                if "xl/workbook.xml" in names:
                    return "xlsx"
            if "content.xml" in names:
                return "odt"
            if any(n.endswith((".xhtml", ".html")) for n in names) \
                    and "META-INF/container.xml" in names:
                return "epub"
    except Exception:
        pass
    return "zip"


def _decode_bytes(raw: bytes, charset: str = "") -> tuple[str, str]:
    """bytes -> (text, note). declared charset, then utf-8, latin-1 last."""
    cands = []
    if charset:
        cands.append(charset)
    cands += ["utf-8", "latin-1"]
    for enc in cands:
        try:
            text = raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
        return text.lstrip("\ufeff"), "" if enc != "latin-1" \
            else "bytes decoded as latin-1 (non-UTF-8 source?)"
    return raw.decode("utf-8", "replace"), "undecodable bytes replaced"


# -- html ---------------------------------------------------------------
class _HtmlText(HTMLParser):
    """HTML -> readable text. Links kept as markdown [t](url); block
    elements start new lines; script/style/svg/head noise dropped."""

    SKIP = {"script", "style", "noscript", "iframe", "svg", "head",
            "template"}
    BLOCK = {"p", "div", "section", "article", "header", "footer", "main",
             "nav", "aside", "ul", "ol", "dl", "table", "blockquote",
             "figure", "form", "details", "summary", "pre", "br", "tr",
             "li", "h1", "h2", "h3", "h4", "h5", "h6", "hr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lines: list[str] = []
        self._cur = ""
        self._skip = 0
        self._in_pre = False
        self._link: Optional[tuple[str, str]] = None  # (href, text)

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
            return
        a = dict(attrs)
        if tag == "pre":
            self._in_pre = True
        elif tag == "a" and self._link is None:
            self._link = (a.get("href", ""), "")
        elif tag == "br":
            self._flush()

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag == "pre":
            self._in_pre = False
            self._flush()
        elif tag == "a" and self._link is not None:
            href, text = self._link
            self._link = None
            text = text.strip()
            if href.startswith(("http://", "https://")) and text:
                self._cur += f"[{text}]({href}) "
        elif tag in self.BLOCK:
            self._flush()

    def handle_data(self, data):
        if self._skip or not data.strip():
            return
        if self._link is not None:
            self._link = (self._link[0], self._link[1] + data)
        else:
            self._cur += data if self._in_pre else re.sub(r"\s+", " ", data)

    def _flush(self):
        if self._cur.strip():
            self.lines.append(self._cur.strip())
        self._cur = ""

    def finish(self) -> str:
        self._flush()
        return "\n".join(self.lines)


def decode_html(raw: bytes, charset: str = "") -> Decoded:
    text, note = _decode_bytes(raw, charset)
    p = _HtmlText()
    try:
        p.feed(text)
        p.close()
    except Exception as exc:
        return Decoded("", "html", "html.parser", "none",
                       f"parse failed: {exc}", str(exc))
    body = p.finish()[:MAX_DECODE_CHARS]
    title = ""
    m = re.search(r"<title[^>]*>(.*?)</title>", text, re.S | re.I)
    if m:
        title = _clean(m.group(1))
    extra = f"title: {title[:120]}" if title else note
    quality = "full" if body else "none"
    return Decoded(body, "html", "html.parser", quality, extra)


# -- pdf -----------------------------------------------------------------
def _pdf_str(s: bytes) -> str:
    s = s.strip()
    if s.startswith(b"(") and s.endswith(b")"):
        s = s[1:-1]  # strip the literal-string delimiters
    s = re.sub(rb"\\([()\\])", rb"\1", s)
    s = re.sub(rb"\\(\d{1,3})",
               lambda m: bytes([int(m.group(1), 8) % 256]), s)
    return s.decode("latin-1", "replace")


def _pdf_text_py(raw: bytes) -> tuple[str, str]:
    """Pure-python PDF text: inflate FlateDecode content streams and pull
    the text-showing operators. Best-effort — covers the common case of
    text PDFs with plain/flate content streams and no image predictors.
    ponytail: ceiling is scanned/encrypted/exotic-encoded PDFs; the
    upgrade path is poppler, which is preferred automatically when the
    binary is installed."""
    if re.search(rb"/Encrypt\b", raw):
        return "", "encrypted PDF — no text without a password"
    texts: list[str] = []
    note_parts: list[str] = []
    for m in re.finditer(rb"stream\r?\n", raw):
        end = raw.find(b"endstream", m.end())
        if end < 0:
            break
        header_start = raw.rfind(b"<<", 0, m.start())
        header = (raw[header_start:m.start()]
                  if header_start >= 0 else b"")
        body = raw[m.end():end]
        if b"/Image" in header or b"/FontFile" in header:
            continue
        if b"/FlateDecode" in header:
            try:
                body = zlib.decompress(body)
            except Exception:
                note_parts.append("a flate stream failed to inflate")
                continue
        elif b"/LZWDecode" in header or b"/DCTDecode" in header:
            continue
        for seg in re.findall(rb"BT(.*?)ET", body, re.S):
            for op in re.finditer(
                    rb"\((?:\\.|[^\\()])*\)\s*Tj|\[(?:[^\[\]]*)\]\s*TJ|"
                    rb"\((?:\\.|[^\\()])*\)\s*['\"]|"
                    rb"<([0-9A-Fa-f\s]+)>\s*Tj", seg):
                token = op.group(0)
                if token.startswith(b"["):
                    parts = re.findall(rb"\((?:\\.|[^\\()])*\)", token)
                    line = "".join(_pdf_str(s) for s in parts)
                elif token.startswith(b"<"):
                    hexs = re.sub(rb"\s", b"", op.group(1))
                    if len(hexs) % 2:
                        hexs += b"0"
                    line = bytes.fromhex(hexs.decode()).decode(
                        "latin-1", "replace")
                else:
                    line = _pdf_str(token[:token.rfind(b")") + 1])
                if line.strip():
                    texts.append(line)
    joined = "\n".join(texts)
    return joined, "; ".join(dict.fromkeys(note_parts))


def decode_pdf(raw: bytes, *, force: str = "") -> Decoded:
    """PDF text: poppler pdftotext when present, else pure-python fallback.
    force='poppler'|'python' pins the decoder (tests)."""
    poppler = shutil.which("pdftotext")
    if poppler and force != "python":
        try:
            p = subprocess.run([poppler, "-", "-"], input=raw,
                               capture_output=True, timeout=PDF_TEXT_TIMEOUT)
            if p.returncode == 0:
                out = p.stdout.decode("utf-8", "replace")
                if out.strip():
                    if len(out) > 80:
                        return Decoded(out[:MAX_DECODE_CHARS], "pdf",
                                       "poppler-pdftotext", "full",
                                       "text layer extracted by poppler")
                    return Decoded(out, "pdf", "poppler-pdftotext",
                                   "degraded",
                                   "very little text — scanned/image PDF?")
        except Exception as exc:
            if force == "poppler":
                return Decoded("", "pdf", "poppler-pdftotext", "none",
                               f"pdftotext failed: {exc}", str(exc))
    text, note = _pdf_text_py(raw)
    if not text.strip():
        note = note or ("no text in content streams — scanned or "
                        "non-standard encoding (needs OCR / a viewer)")
        return Decoded("", "pdf", "pdf-python-zlib", "none", note, note)
    return Decoded(text[:MAX_DECODE_CHARS], "pdf", "pdf-python-zlib",
                   "degraded", note or "best-effort stream extraction")


# -- zip containers -----------------------------------------------------
def _strip_tags(text: str, breaks: tuple[str, ...]) -> str:
    for br in breaks:
        text = text.replace(br, "\n")
    return re.sub(r"<[^>]+>", "", text)


def _zip_text(names: list[str], read) -> Decoded:
    """Shared zip-container decoder; `read` maps a zip name to bytes."""
    try:
        if "word/document.xml" in names:
            raw = read("word/document.xml")
            body = _strip_tags(_decode_bytes(raw)[0], ("</w:p>", "</w:tr>"))
            body = re.sub(r"\n{3,}", "\n\n", body).strip()
            return Decoded(body[:MAX_DECODE_CHARS], "docx", "zipfile+xml",
                           "full" if body else "none",
                           "OOXML body text (structure simplified)")
        if "xl/workbook.xml" in names or "xl/sharedStrings.xml" in names:
            shared: list[str] = []
            if "xl/sharedStrings.xml" in names:
                try:
                    root = ET.fromstring(read("xl/sharedStrings.xml"))
                    shared = ["".join(si.itertext())
                              for si in root.iter()
                              if _localname(si.tag) == "si"]
                except Exception:
                    pass
            cells: list[str] = []
            for sheet in sorted(n for n in names
                                if re.match(r"xl/worksheets/sheet\d+\.xml$",
                                            n)):
                try:
                    root = ET.fromstring(read(sheet))
                    for row in root.iter():
                        if _localname(row.tag) != "row":
                            continue
                        vals: list[str] = []
                        for c in row:
                            if _localname(c.tag) != "c":
                                continue
                            v = next((ch for ch in c
                                      if _localname(ch.tag) == "v"), None)
                            if v is None or v.text is None:
                                continue
                            if (c.get("t") or "") == "s":
                                try:
                                    vals.append(shared[int(v.text)])
                                except (ValueError, IndexError):
                                    pass
                            else:
                                vals.append(v.text)
                        if vals:
                            cells.append("\t".join(vals))
                except Exception:
                    continue
            body = "\n".join(cells)
            return Decoded(body[:MAX_DECODE_CHARS], "xlsx", "zipfile+xml",
                           "partial" if body else "none",
                           "sheet cell text (values only)")
        if "content.xml" in names:
            raw = read("content.xml")
            body = _strip_tags(_decode_bytes(raw)[0],
                               ("</text:p>", "</text:h>"))
            body = re.sub(r"\n{3,}", "\n\n", body).strip()
            return Decoded(body[:MAX_DECODE_CHARS], "odt", "zipfile+xml",
                           "partial" if body else "none",
                           "ODF body text (structure simplified)")
        if "META-INF/container.xml" in names:  # epub
            try:
                cont = ET.fromstring(read("META-INF/container.xml"))
                opf = next(el for el in cont.iter()
                           if _localname(el.tag) == "rootfile")
                opf_path = opf.get("full-path", "")
            except Exception:
                opf_path = ""
            if opf_path and opf_path in names:
                try:
                    root = ET.fromstring(read(opf_path))
                    base = str(Path(opf_path).parent)
                    manifest: dict[str, str] = {}
                    for it in root.iter():
                        if _localname(it.tag) == "item":
                            manifest[it.get("id")] = it.get("href", "")
                    chapters: list[str] = []
                    for ref in root.iter():
                        if _localname(ref.tag) != "itemref":
                            continue
                        href = manifest.get(ref.get("idref") or "")
                        if not href:
                            continue
                        name = f"{base}/{href}" if base != "." else href
                        if name in names and name.endswith(
                                (".xhtml", ".html", ".htm")):
                            t, _ = _decode_bytes(read(name))
                            chapters.append(
                                re.sub(r"<[^>]+>", " ", t).strip())
                    body = "\n\n".join(chapters)
                    if body:
                        return Decoded(body[:MAX_DECODE_CHARS], "epub",
                                       "zipfile+opf", "partial",
                                       "chapter text (tags stripped)")
                except Exception as exc:
                    return Decoded("", "epub", "zipfile+opf", "none",
                                   f"epub spine failed: {exc}", str(exc))
    except Exception as exc:
        return Decoded("", "zip", "zipfile", "none",
                       f"zip decode failed: {exc}", str(exc))
    return Decoded("", "zip", "zipfile", "none",
                   "unknown zip container "
                   "(expected docx/epub/odt/xlsx)", "unknown container")


def decode_zip(raw: bytes) -> Decoded:
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            return _zip_text(z.namelist(), lambda n: z.read(n))
    except Exception as exc:
        return Decoded("", "zip", "zipfile", "none",
                       f"not a readable zip: {exc}", str(exc))


# -- rtf / json / xml / plain -------------------------------------------
def decode_rtf(raw: bytes) -> Decoded:
    text, _ = _decode_bytes(raw)
    text = re.sub(r"\\'([0-9a-fA-F]{2})",
                  lambda m: chr(int(m.group(1), 16)), text)
    text = re.sub(r"\\u-?\d+ ?", "", text)
    text = re.sub(r"\\[a-zA-Z]+-?\d* ?", "", text)
    text = re.sub(r"[{}]", "", text)
    body = re.sub(r"[ \t]+", " ", text)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    if not body:
        return Decoded("", "rtf", "rtf-strip", "none",
                       "no readable text after stripping", "empty")
    return Decoded(body[:MAX_DECODE_CHARS], "rtf", "rtf-strip",
                   "degraded", "coarse control-word stripping")


def decode_json(raw: bytes) -> Decoded:
    text, note = _decode_bytes(raw)
    try:
        pretty = json.dumps(json.loads(text), indent=2, ensure_ascii=False)
        return Decoded(pretty[:MAX_DECODE_CHARS], "json", "json.loads",
                       "full", note)
    except Exception:
        return Decoded(text[:MAX_DECODE_CHARS], "json", "passthrough",
                       "full", note or "not valid JSON — raw text kept")


def decode_xml(raw: bytes, charset: str = "") -> Decoded:
    """XML -> text; RSS/Atom feeds keep their item structure."""
    text, note = _decode_bytes(raw, charset)
    try:
        root = ET.fromstring(text)
    except Exception:
        return Decoded("", "xml", "xml.etree", "none",
                       "not well-formed XML", "parse error")

    def child(el, name: str) -> Optional[ET.Element]:
        return next((c for c in el if _localname(c.tag) == name), None)

    tag = _localname(root.tag)
    if tag in ("rss", "feed"):  # feed-shaped
        channel = child(root, "channel")
        container = channel if channel is not None else root
        items = [c for c in container if _localname(c.tag) in ("item",
                                                               "entry")]
        out: list[str] = []
        for it in items:
            title_el = child(it, "title")
            title = ("".join(title_el.itertext()).strip()
                     if title_el is not None else "")
            link_el = child(it, "link")
            if link_el is not None and link_el.get("href"):
                link = link_el.get("href", "").strip()
            elif link_el is not None:
                link = "".join(link_el.itertext()).strip()
            else:
                link = ""
            desc_el = child(it, "description")
            if desc_el is None:
                desc_el = child(it, "summary")
            desc = ("".join(desc_el.itertext()).strip()
                    if desc_el is not None else "")
            desc = re.sub(r"\s+", " ", desc)[:600]
            line = f"[{title}]({link})" if link.startswith("http") else title
            out.append(f"{line}\n{desc}" if desc else line)
        body = "\n\n".join(out)
        return Decoded(body[:MAX_DECODE_CHARS], "xml", "xml.etree",
                       "full" if body else "none", note or "feed items")
    flat = re.sub(r"\s+", " ", "".join(root.itertext())).strip()
    return Decoded(flat[:MAX_DECODE_CHARS], "xml", "xml.etree",
                   "partial" if flat else "none",
                   note or "generic XML, structure flattened")


def decode_plain(raw: bytes, fmt: str = "txt", charset: str = "") -> Decoded:
    text, note = _decode_bytes(raw, charset)
    quality = "full" if text.strip() else "none"
    return Decoded(text[:MAX_DECODE_CHARS], fmt, "passthrough", quality, note)


def decode(raw: bytes, fmt: str = "", *, ctype: str = "", hint: str = "",
           charset: str = "", force_pdf: str = "") -> Decoded:
    """Dispatch by sniffed format. fmt overrides sniffing when non-empty."""
    fmt = fmt or sniff_fmt(raw, ctype, hint)
    if fmt == "html":
        return decode_html(raw, charset)
    if fmt == "pdf":
        return decode_pdf(raw, force=force_pdf)
    if fmt in ("docx", "epub", "odt", "xlsx", "zip"):
        return decode_zip(raw)
    if fmt == "rtf":
        return decode_rtf(raw)
    if fmt == "json":
        return decode_json(raw)
    if fmt == "xml":
        return decode_xml(raw, charset)
    if fmt in ("csv", "md", "txt"):
        return decode_plain(raw, fmt, charset)
    if fmt == "image":
        return Decoded("", "image", "none", "none",
                       "image — no OCR in this handler "
                       "(ponytail: add tesseract if needed)")
    if fmt == "binary":
        return Decoded("", "binary", "none", "none",
                       "unrecognised binary format", "binary")
    if fmt == "empty":
        return Decoded("", "empty", "none", "none", "empty body", "empty")
    return Decoded("", fmt, "none", "none", f"no decoder for {fmt!r}",
                   "unknown")


# ---------------------------------------------------------------- gather
def looks_like_url(s: str) -> bool:
    return bool(_URL_RE.match(s.strip()))


def _looks_like_filename(s: str) -> bool:
    """Path-ish (has dir slash or extension) and no spaces => user meant
    a file, not a search query."""
    return (" " not in s) and (("/" in s and "." in s.rsplit("/", 1)[-1])
                               or Path(s).suffix != "")


def read_local(path: str, max_bytes: int = MAX_BYTES) -> Fetch:
    p = Path(path)
    try:
        raw = p.read_bytes()
        too_big = len(raw) > max_bytes
        raw = raw[:max_bytes]
        return Fetch(ok=True, status=200, final_url=str(p.resolve()),
                     content_type="", raw=raw, too_big=too_big)
    except Exception as exc:
        return Fetch(ok=False, error=f"read failed: {exc}")


def _decode_fetch(f: Fetch, hint: str) -> Optional[Decoded]:
    if not f.ok:
        return None
    return decode(f.raw, ctype=f.content_type, hint=hint, charset=f.charset)


def gather(targets, limit: int = 5, *, engines=None,
           timeout: float = FETCH_TIMEOUT, topic: str = "") -> Corpus:
    """Turn user targets (query | urls | local files) into a decoded corpus.

    - one bare non-URL/non-file target  -> web search, fetch top results
    - http(s) URLs                      -> fetched and decoded
    - existing paths                    -> read and decoded
    Mixed lists are fine; each entry is handled by its own kind.
    """
    targets = [str(t) for t in targets]
    corpus = Corpus(topic=topic or _slug(" ".join(targets)[:64]),
                    query=" ".join(targets))
    single_query = (len(targets) == 1 and not looks_like_url(targets[0])
                    and not Path(targets[0]).expanduser().exists()
                    and not _looks_like_filename(targets[0]))
    if single_query:
        results, notes = search(targets[0], limit=max(limit * 3, 8),
                                engines=engines)
        corpus.engine_notes = notes
        for r in results[:limit]:
            url = r.pdf or r.url
            f = _http(url, timeout=timeout)
            src = Source(id=f"s{len(corpus.sources) + 1}", kind="search",
                         engine=r.engine, title=r.title, url=url, fetch=f)
            src.decoded = _decode_fetch(f, r.url)
            corpus.sources.append(src)
        return corpus
    for t in targets:
        t = t.strip()
        if looks_like_url(t):
            url = t if "://" in t else "https://" + t
            f = _http(url, timeout=timeout)
            src = Source(id=f"s{len(corpus.sources) + 1}", kind="url",
                         url=url, fetch=f)
            src.decoded = _decode_fetch(f, url)
            corpus.sources.append(src)
        elif Path(t).expanduser().exists():
            p = Path(t).expanduser()
            f = read_local(str(p))
            src = Source(id=f"s{len(corpus.sources) + 1}", kind="file",
                         path=str(p), title=p.name, fetch=f)
            if f.ok:
                src.decoded = _decode_fetch(f, p.name)
            corpus.sources.append(src)
        else:
            corpus.sources.append(Source(
                id=f"s{len(corpus.sources) + 1}", kind="url", url=t,
                error=f"not a URL or an existing path: {t!r}"))
    return corpus


# ---------------------------------------------------------------- storage
def _slug(s: str, maxlen: int = 64) -> str:
    slug = _SLUG.sub("-", s.lower()).strip("-")
    return slug[:maxlen].rstrip("-") or "research"


def _status_row(s: Source) -> tuple[str, str]:
    st = s.status
    if st == "ok":
        d = s.decoded
        return "✅", (f"{d.method}/{d.quality}, {len(d.text):,} chars")
    if st == "partial":
        return "⚠️", (f"decode degraded ({s.decoded.method})"
                      if s.decoded else "partial")
    if s.error:
        return "✗", s.error
    if s.fetch and not s.fetch.ok:
        return "✗", f"fetch failed: {s.fetch.error}"
    if s.decoded and s.decoded.error:
        return "✗", s.decoded.error
    return "✗", "no usable text"


def write_kit(corpus: Corpus, outdir: str | Path, name: str = "") -> Path:
    """Store the deep-analysis-labelled kit under outdir/<slug>/.

    README.md index (status table + gaps + sources table), raw + decoded
    text per source in sources/, and the machine manifest sources.jsonl.
    """
    outdir = Path(outdir)
    slug = _slug(name or corpus.topic)
    kit = outdir / slug
    src_dir = kit / "sources"
    src_dir.mkdir(parents=True, exist_ok=True)
    (kit / ".gitignore").write_text("sources/raw-*\n")
    counts = corpus.counts()
    manifest = []
    rows: list[tuple[str, str, str]] = []
    for i, s in enumerate(corpus.sources, 1):
        mark, detail = _status_row(s)
        rows.append((mark, s.id, detail))
        rec = {"id": s.id, "kind": s.kind, "engine": s.engine,
               "title": s.title, "url": s.url, "path": s.path,
               "status": s.status, "label": mark,
               "fetch_status": (s.fetch.status if s.fetch else None),
               "fetch_error": (s.fetch.error if s.fetch and not s.fetch.ok
                               else s.error),
               "decoded_chars": (len(s.decoded.text)
                                 if s.decoded and s.decoded.text else 0),
               "fmt": s.decoded.fmt if s.decoded else "",
               "method": s.decoded.method if s.decoded else "",
               "quality": s.decoded.quality if s.decoded else "",
               "decode_note": s.decoded.note if s.decoded else "",
               "text_file": (f"sources/text-{i}.txt"
                             if s.decoded and s.decoded.text else ""),
               "raw_file": None}
        if s.fetch and s.fetch.ok and s.fetch.raw:
            ext = _FMT_EXT.get(s.decoded.fmt if s.decoded else "bin", "bin")
            rawf = src_dir / f"raw-{i}.{ext}"
            rawf.write_bytes(s.fetch.raw)
            rec["raw_file"] = f"sources/raw-{i}.{ext}"
        if s.decoded and s.decoded.text:
            (src_dir / f"text-{i}.txt").write_text(s.decoded.text)
        manifest.append(rec)

    when = time.strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        f"# {slug}",
        "",
        f"> Scope: {corpus.query or slug} · gathered {when} · "
        f"handler v{VERSION} · kit contract `{KIT}`",
        "",
        f"**{counts['total']} sources: {counts['ok']} ✅ · "
        f"{counts['partial']} ⚠️ · {counts['failed']} ✗**",
        "",
        "## Source status (auto-labelled)",
        "",
        "Legend: ✅ fetched & decoded (usable) · ⚠️ decoded but degraded "
        "(use with caution) · ✗ fetch/decode failed (reason given).",
        "",
        "| # | Source | Status | Detail |",
        "|---|--------|--------|--------|",
    ]
    for mark, sid, detail in rows:
        src = corpus.sources[int(sid[1:]) - 1]
        who = src.display
        if src.engine:
            who = f"{who} *(via {src.engine})*"
        lines.append(f"| {sid} | {who[:90]} | {mark} | {detail[:110]} |")
    lines += [
        "", "## Unverified / gaps (auto)", "",
        "Items below could not be fetched or fully decoded by the handler. "
        "**How to verify**: open the URL in a browser, re-fetch with curl, "
        "or decode with a fuller tool (poppler/OCR for scanned PDFs).", ""]
    gaps = 0
    for i, s in enumerate(corpus.sources, 1):
        mark, detail = _status_row(s)
        if mark == "✅":
            continue
        gaps += 1
        lines.append(f"- ⚠️ `{s.id}` {s.display[:100]}: {detail[:200]}")
    if not gaps:
        lines.append("- none — every source fetched and decoded.")
    lines += ["", "## Sources", "",
              "| # | Source | Fetch | Decode |",
              "|---|--------|-------|--------|"]
    for i, s in enumerate(corpus.sources, 1):
        fstat = (f"HTTP {s.fetch.status}" if s.fetch and s.fetch.ok
                 else (s.fetch.error if s.fetch else s.error or "-"))
        dstat = (f"{s.decoded.method} · {s.decoded.quality}"
                 if s.decoded else "-")
        lines.append(f"| {s.id} | {s.display[:100]} | {fstat[:40]} | "
                     f"{dstat[:40]} |")
    lines += ["", "## Narrative sections", "",
              "Pending `deep-analysis`: apply the deep-analysis skill to "
              "this manifest (verified narrative + per-facet sections), or "
              "run `sp research <topic> --deep` for the configured brain "
              "to draft them from `sources/text-*.txt`.", ""]
    (kit / "README.md").write_text("\n".join(lines))
    (kit / "sources.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in manifest) + "\n")
    return kit


def bundle_for_brain(corpus: Corpus, max_chars: int = 6000) -> str:
    """Compact per-source text bundle (labelled) for an LLM brain."""
    parts = [f"# Research corpus: {corpus.topic}",
             f"Targets: {corpus.query}"]
    for i, s in enumerate(corpus.sources, 1):
        mark, detail = _status_row(s)
        parts.append(f"\n## [{s.id}] {mark} {s.display}")
        parts.append(f"kind={s.kind} engine={s.engine or '-'} "
                     f"status={s.status} detail={detail}")
        if s.decoded and s.decoded.text:
            parts.append(s.decoded.text[:max_chars])
    return "\n".join(parts)


# ---------------------------------------------------------------- --deep
def brain_available(prefer: str = "") -> str:
    """Which narrative brain could run: 'grok' (hw wrapper), 'deepseek',
    or ''. Availability probe only — nothing is invoked implicitly."""
    if prefer in ("grok", "hw", ""):
        hw = os.environ.get("HW_BIN") or shutil.which("hw")
        grok_auth = Path.home() / ".grok" / "auth.json"
        if hw and grok_auth.is_file():
            return "grok"
    if prefer in ("deepseek", ""):
        try:
            from planner import deepseek_config
            if deepseek_config()[1]:
                return "deepseek"
        except Exception:
            pass
    return ""


def deep_synthesize(corpus: Corpus, kit_dir: Path, brain: str) -> str:
    """Draft the deep-analysis narrative over the labelled corpus.

    brain='grok': hw run grok <deep-analysis prompt + text bundle>.
    brain='deepseek': planner._chat_completion (deepseek-v4-flash).
    Writes narrative.md into the kit and returns the model text; on
    failure returns '' and the kit keeps its scaffold.
    """
    if brain == "auto" or not brain:
        brain = brain_available()
    if not brain:
        return ""
    bundle = bundle_for_brain(corpus)
    system = (
        "You are a research analyst applying the deep-analysis discipline. "
        "Produce a MARKDOWN knowledge note with: a scope line, a short "
        "factual narrative (max ~600 words) answering the research target, "
        "and an explicit 'Unverified / gaps' list. Mark every claim with "
        "the source id it came from, e.g. ([s1]); never invent URLs, "
        "numbers, quotes, or dates — if a claim is not in the corpus, say "
        "so explicitly. Claims backed by a ✅ source are verified; claims "
        "from ⚠️ sources are plausible but unconfirmed; label them "
        "accordingly. No preamble, markdown only.")
    user = f"Research target: {corpus.query}\n\n{bundle[:80_000]}"
    text = ""
    text = brain_chat(brain, system, user)
    if text:
        (kit_dir / "narrative.md").write_text(text)
        readme = (kit_dir / "README.md").read_text()
        readme = readme.replace(
            "Pending `deep-analysis`: apply the deep-analysis skill to "
            "this manifest (verified narrative + per-facet sections), or "
            "run `sp research <topic> --deep` for the configured brain "
            "to draft them from `sources/text-*.txt`.",
            f"Drafted by the {brain} brain — see `narrative.md`. Treat as "
            "a draft; re-verify claims against `sources/text-*.txt` "
            "before relying on them.")
        (kit_dir / "README.md").write_text(readme)
    return text



# ---------------------------------------------------------------- team
# A research team = several brains (grok, deepseek, ...) working one kit.
# Design invariant: the kit DIRECTORY is the only shared context.  Every
# step reads its inputs from kit files and writes its output to a file
# no other step rewrites (one writer per file -- the merge-notes rule),
# so any step can run in any process with any brain, and re-running
# with different members never loses work: done steps are skipped.
# That is what makes brains hot-swappable mid-research.
# One team run per kit at a time: state.json is atomic but the pipeline
# itself is single-writer (two concurrent runs race on phase skips).

TEAM_PLAN_SYS = (
    "You are a research planner. Decompose the research topic into 2-5 "
    "non-overlapping tracks that together cover the topic. Reply with "
    "ONLY a JSON array of short track queries (strings), no prose.")

TEAM_TRACK_SYS = (
    "You are a research analyst applying the deep-analysis discipline. "
    "Work only inside ONE track of a larger study. Produce MARKDOWN: a "
    "one-line scope, a factual narrative (max ~450 words) answering the "
    "track query, and an explicit 'Unverified / gaps' list. Mark every "
    "claim with its source id, e.g. ([s1]); claims from ✅ sources are "
    "verified, from ⚠️ sources plausible-but-unconfirmed -- label them. "
    "Never invent URLs, numbers, quotes or dates; if the corpus does "
    "not answer the query, say so. No preamble, markdown only.")

TEAM_MERGE_SYS = (
    "You are the single-writer consolidator for a research team. You "
    "receive per-track analyst notes over disjoint tracks of one topic. "
    "Produce MARKDOWN: a unified narrative (dedupe, keep per-claim "
    "source ids and ✅/⚠️ discipline), a 'Conflicts' section quoting "
    "both sides where tracks disagree (do not resolve silently), and "
    "one combined 'Unverified / gaps' list. Never add claims of your "
    "own. No preamble, markdown only.")


def _planner():
    """Import harness/planner with the repo root importable (planner
    pulls in the statepod module from the repo root)."""
    root = str(Path(__file__).resolve().parent.parent)
    if root not in sys.path:
        sys.path.insert(0, root)
    import planner
    return planner


_ollama_ok: bool | None = None   # probe once per process


def _local_brain_up() -> bool:
    """Ollama (or any OpenAI-compat local server) reachable?  This is the
    statepod fallback backend: embedded/daemon llama on localhost."""
        # global _ollama_ok  # refactored: use class attribute
    if _ollama_ok is None:
        try:
            req = urllib.request.Request(
                f"{os.environ.get('SW_LOCAL_BASE', 'http://localhost:11434')}"
                "/v1/models")
            with urllib.request.urlopen(req, timeout=3) as r:
                _ollama_ok = r.status == 200
        except Exception:
            _ollama_ok = False
    return _ollama_ok


def local_model() -> str:
    return os.environ.get("SW_LOCAL_MODEL") or "qwen2.5-coder:1.5b"


def brains_available() -> list:
    """All usable brains right now, preference order: ['grok',
    'deepseek', 'local']."""
    out = []
    hw = os.environ.get("HW_BIN") or shutil.which("hw")
    if hw and (Path.home() / ".grok" / "auth.json").is_file():
        out.append("grok")
    try:
        if _planner().deepseek_config()[1]:
            out.append("deepseek")
    except Exception:
        pass
    if _local_brain_up():
        out.append("local")
    return out


def brain_chat(brain: str, system: str, user: str,
               timeout: float = 270.0) -> str:
    """One-shot text-in/text-out against a brain. Returns '' on failure.
    This is the whole brain interface: stateless, so any brain can be
    swapped into any step."""
    if brain == "grok":
        hw = os.environ.get("HW_BIN") or shutil.which("hw")
        if not hw:
            return ""
        try:
            p = subprocess.run(
                [hw, "run", "grok", system + "\n\n" + user,
                 "--timeout", str(int(timeout - 30))],
                capture_output=True, text=True, timeout=timeout)
            return (p.stdout or "").strip()
        except Exception as exc:
            print(f"  team: hw run grok failed ({exc})")
            return ""
    if brain == "local":
        if not _local_brain_up():
            return ""
        base = os.environ.get("SW_LOCAL_BASE",
                              "http://localhost:11434").rstrip("/")
        body = {
            "model": local_model(),
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user[:24_000]},
            ],
            "temperature": 0.1,
            "stream": False,
        }
        req = urllib.request.Request(
            base + "/v1/chat/completions",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"})
        for attempt in (1, 2):   # local servers occasionally 5xx
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    data = json.loads(resp.read())
                return ((data.get("choices") or [{}])[0]
                        .get("message", {}).get("content") or "").strip()
            except Exception as exc:
                if attempt == 2:
                    print(f"  team: local ({local_model()}) failed ({exc})")
                    return ""
                time.sleep(3)
    if brain == "deepseek":
        try:
            pl = _planner()
            base, key = pl.deepseek_config()
            if not key:
                return ""
            data = pl._chat_completion(base, key, "deepseek-v4-flash",
                                       system, user, want_json=False)
            return ((data.get("choices") or [{}])[0]
                    .get("message", {}).get("content") or "").strip()
        except Exception as exc:
            print(f"  team: deepseek chat failed ({exc})")
            return ""
    return ""


def bundle_from_kit(kit_dir: Path, max_chars: int = 6000) -> str:
    """Rebuild the labelled text bundle from stored kit files only
    (sources.jsonl + sources/text-*.txt) -- no in-memory Corpus needed,
    so later team steps work in a fresh process on any machine."""
    kit_dir = Path(kit_dir)
    mf = kit_dir / "sources.jsonl"
    if not mf.is_file():
        return ""
    parts = [f"# Research corpus: {kit_dir.name}"]
    for line in mf.read_text().splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        parts.append(f"\n## [{rec.get('id')}] {rec.get('label', '')} "
                     f"{rec.get('title') or rec.get('url') or rec.get('path')}")
        parts.append(f"kind={rec.get('kind')} status={rec.get('status')} "
                     f"detail={rec.get('decode_note') or rec.get('fetch_error') or ''}")
        tf = rec.get("text_file")
        if tf and rec.get("status") in ("ok", "partial"):
            tpath = kit_dir / tf
            if tpath.is_file():
                parts.append(tpath.read_text(errors="replace")[:max_chars])
    return "\n".join(parts)


def _team_load(kit: Path) -> dict:
    f = kit / "team" / "state.json"
    if f.is_file():
        try:
            return json.loads(f.read_text())
        except ValueError:
            pass
    return {}


def _team_save(kit: Path, state: dict) -> None:
    d = kit / "team"
    d.mkdir(parents=True, exist_ok=True)
    # atomic: a killed run must not leave a half-written state behind
    tmp = d / "state.json.tmp"
    tmp.write_text(json.dumps(state, indent=1))
    os.replace(tmp, d / "state.json")


def _team_pick(members: list, i: int) -> str:
    avail = [m for m in members if m in brains_available()]
    if not avail:
        return ""
    return avail[i % len(avail)]


def team_plan(kit: Path, topic: str, query: str, members: list,
              n_tracks: int, state: dict, redo: bool, log=print) -> dict:
    """Phase 1: decompose the topic into track queries -> team/plan.md."""
    if state.get("plan", {}).get("done") and not redo:
        log(f"  plan: done already ({state['plan']['brain']}) -- skip")
        return state
    brain = _team_pick(members, 0)
    tracks = []
    if brain:
        raw = brain_chat(brain, TEAM_PLAN_SYS,
                         f"Topic: {topic}\nResearch target: {query}")
        m = re.search(r"\[.*\]", raw, re.S)
        if m:
            try:
                tracks = [str(t).strip() for t in json.loads(m.group(0))
                          if str(t).strip()][:5]
            except ValueError:
                tracks = []
        if not tracks:
            log(f"  plan: {brain} gave no parsable tracks; fallback")
    if not tracks:
        tracks = [query]                      # deterministic fallback
        brain = (brain + " -> fallback single track") if brain \
            else "none (mechanical)"
    kit_dir = Path(kit)
    tdir = kit_dir / "team"
    tdir.mkdir(parents=True, exist_ok=True)
    body = [f"# Team plan: {topic}", "",
            f"Research target: {query}", "",
            f"Planner: {brain}", ""]
    for i, t in enumerate(tracks, 1):
        body.append(f"- t{i}: {t}")
    (tdir / "plan.md").write_text("\n".join(body) + "\n")
    state["plan"] = {"done": True, "brain": brain, "ts": time.time()}
    state["tracks"] = state.get("tracks") or []
    state["tracks"] = [t for t in state["tracks"]
                       if t["n"] <= len(tracks)]
    for i, t in enumerate(tracks, 1):
        if i > len(state["tracks"]):
            state["tracks"].append({"n": i, "q": t, "done": False})
        elif state["tracks"][i - 1].get("q") != t:
            # replanned track: its finding refers to the old query
            state["tracks"][i - 1] = {"n": i, "q": t, "done": False}
    state.setdefault("topic", topic)
    state.setdefault("query", query)
    _team_save(kit, state)
    log(f"  plan: {len(tracks)} tracks via {brain} -> team/plan.md")
    return state


def team_track(kit: Path, state: dict, tr: dict, members: list,
               limit: int, engines, timeout: float,
               redo: bool, log=print) -> dict:
    """Phase 2 (one track): gather a track corpus, store it as its own
    mini-kit, then the assigned member drafts team/<kit>/finding.md.
    The finding file has exactly one writer ever: the member in its name."""
    if tr.get("done") and not redo:
        log(f"  t{tr['n']}: done already ({tr.get('brain')}) -- skip")
        return state
    tdir = Path(kit) / "team"
    topic = state.get("topic") or Path(kit).name
    tkit_name = f"{_slug(topic)}-t{tr['n']}"
    tkit = tdir / tkit_name
    corpus = gather([tr["q"]], limit=limit, engines=engines,
                    timeout=timeout, topic=tkit_name)
    write_kit(corpus, tdir, name=tkit_name)
    brain = _team_pick(members, tr["n"] - 1)
    finding = ""
    if brain:
        bundle = bundle_from_kit(tkit)
        finding = brain_chat(brain, TEAM_TRACK_SYS,
                             f"Track query: {tr['q']}\n\n{bundle[:80_000]}")
        if not finding:
            log(f"  t{tr['n']}: {brain} returned nothing; mechanical")
    if finding:
        (tkit / "finding.md").write_text(finding)
    else:
        # mechanical fallback: labelled manifest, zero model claims
        rows = ["# Mechanical track note (no brain available)", "",
                f"Track query: {tr['q']}", "",
                "| id | label | source | detail |", "| :-- | :--- | :--- | :--- |"]
        try:
            for rec in [json.loads(l) for l in
                        (tkit / "sources.jsonl").read_text().splitlines()
                        if l.strip()]:
                rows.append(f"| {rec.get('id')} | {rec.get('label')} | "
                            f"{(rec.get('title') or rec.get('url') or '')[:90]} | "
                            f"{(rec.get('decode_note') or rec.get('fetch_error') or '')[:80]} |")
        except FileNotFoundError:
            rows.append("| - | ✗ | (corpus empty) | |")
        rows += ["", "Text corpus: `sources/text-*.txt` -- run with a "
                     "brain (or swap members) to draft the analysis."]
        brain = (f"{brain} -> none (mechanical)" if brain
                 else "none (mechanical)")
        (tkit / "finding.md").write_text("\n".join(rows) + "\n")
    tr.update(done=True, brain=brain, kit=f"team/{tkit_name}",
              finding=f"team/{tkit_name}/finding.md", ts=time.time())
    _team_save(Path(kit), state)
    log(f"  t{tr['n']}: {brain} -> {tr['finding']}")
    return state


def team_merge(kit: Path, state: dict, members: list,
               redo: bool, log=print) -> dict:
    """Phase 3: single writer consolidates all track findings -> merge.md."""
    if state.get("merge", {}).get("done") and not redo:
        log(f"  merge: done already ({state['merge']['brain']}) -- skip")
        return state
    kit = Path(kit)
    tracks = [t for t in state.get("tracks", []) if t.get("done")]
    if not tracks:
        log("  merge: no finished tracks, nothing to merge")
        return state
    parts = [f"# Track findings: {state.get('topic', kit.name)}"]
    for t in tracks:
        f = kit / t["finding"]
        parts.append(f"\n---\n\n## From {t['finding']} (by {t.get('brain')})\n\n"
                     + (f.read_text(errors="replace") if f.is_file() else "(missing)"))
    brain = _team_pick(members, 0)
    merged = ""
    if brain:
        merged = brain_chat(brain, TEAM_MERGE_SYS, "\n".join(parts)[:120_000])
        if not merged:
            log(f"  merge: {brain} returned nothing; mechanical")
    if not merged:
        brain = (f"{brain} -> none (mechanical)" if brain
                 else "none (mechanical)")
        merged = ("# Mechanical merge (no brain available)\n\n"
                  "Track findings follow, unmerged.\n" + "\n".join(parts))
    (kit / "team" / "merge.md").write_text(merged)
    state["merge"] = {"done": True, "brain": brain,
                      "file": "team/merge.md", "ts": time.time()}
    _team_save(kit, state)
    # index the team output into the kit README (between markers, idempotent)
    readme = kit / "README.md"
    if readme.is_file():
        txt = readme.read_text()
        links = "\n".join(f"- `{t['finding']}` (by {t.get('brain')})"
                           for t in tracks)
        block = ("\n<!-- team -->\n## Research team\n\n"
                 f"Consolidated report: `team/merge.md` (by "
                 f"{state['merge']['brain']}).\n\nTrack findings:\n{links}\n"
                 "<!-- /team -->\n")
        if "<!-- team -->" in txt:
            txt = re.sub(r"\n<!-- team -->.*?<!-- /team -->\n", block,
                         txt, flags=re.S)
        else:
            txt = txt.rstrip("\n") + "\n" + block
        readme.write_text(txt)
    log(f"  merge: {brain} -> team/merge.md")
    return state


def team_run(kit: Path, topic: str, query: str, members: list,
             n_tracks: int = 3, limit: int = 4, engines=None,
             timeout: float = FETCH_TIMEOUT, redo: str = "",
             log=print) -> dict:
    """Run (or resume) the team pipeline over a kit.  Idempotent: done
    phases are skipped, so calling again with different members only
    fills the gaps -- that is the model-swap story."""
    kit = Path(kit)
    state = _team_load(kit)
    state.setdefault("topic", topic)
    state.setdefault("query", query)
    state["members_last"] = members
    redo_all = redo == "all"
    state = team_plan(kit, topic, query, members, n_tracks, state,
                      redo=(redo in ("plan", "all")), log=log)
    for tr in state.get("tracks", []):
        if not tr.get("done") or redo in ("research", "all"):
            team_track(kit, state, tr, members, limit, engines,
                       timeout, redo=(redo in ("research", "all")), log=log)
    state = team_merge(kit, state, members,
                       redo=(redo in ("merge", "all")), log=log)
    return state


# ---------------------------------------------------------------- CLI
def cmd_search(args) -> int:
    results, notes = search(args.query, limit=args.limit,
                            engines=args.engines.split(",")
                            if args.engines else None)
    for n in notes:
        print(f"[engine] {n}")
    for i, r in enumerate(results, 1):
        print(f"{i}. {r.title} [{r.engine}]")
        print(f"   {r.url}")
        if r.snippet:
            print(f"   {r.snippet[:160]}")
    print(f"{len(results)} results")
    return 0 if results else 1


def cmd_fetch(args) -> int:
    rc = 0
    for url in args.urls:
        f = _http(url, timeout=args.timeout)
        if not f.ok:
            print(f"FAIL {url}: {f.error}")
            rc = 1
            continue
        d = decode(f.raw, ctype=f.content_type, hint=url, charset=f.charset)
        print(f"{f.status} {f.final_url} ctype={f.content_type} "
              f"{len(f.raw)}B{' (truncated)' if f.too_big else ''}")
        print(f"  -> {d.fmt} via {d.method} [{d.quality}] "
              f"{len(d.text):,} chars · {d.note}")
        if args.raw:
            Path(args.raw).write_bytes(f.raw)
            print(f"  raw -> {args.raw}")
    return rc


def cmd_decode(args) -> int:
    rc = 0
    for path in args.paths:
        p = Path(path)
        if not p.exists():
            print(f"FAIL {path}: no such file")
            rc = 1
            continue
        d = decode(p.read_bytes(), hint=path, force_pdf=args.force_pdf)
        print(f"{path}: {d.fmt} via {d.method} [{d.quality}] "
              f"{len(d.text):,} chars · {d.note}")
        if d.text:
            print(d.text[:args.head] + ("…" if len(d.text) > args.head
                                        else ""))
    return rc


def cmd_kit(args) -> int:
    corpus = gather(args.targets, limit=args.limit,
                    engines=args.engines.split(",") if args.engines else None,
                    timeout=args.timeout, topic=args.name or "")
    kit = write_kit(corpus, args.outdir, name=args.name or "")
    counts = corpus.counts()
    print(f"kit -> {kit}")
    print(f"sources: {counts['total']} (ok {counts['ok']}, "
          f"partial {counts['partial']}, failed {counts['failed']})")
    for n in corpus.engine_notes:
        print(f"[engine] {n}")
    if args.deep and counts["ok"] + counts["partial"] >= 1:
        brain = brain_available(args.deep if args.deep not in
                                ("auto", "1", "true") else "")
        if brain:
            print(f"deep: drafting narrative via {brain}…")
            ok = deep_synthesize(corpus, kit, brain)
            print(f"deep: {'ok — see narrative.md' if ok else 'no output'}")
        else:
            print("deep: no brain available (need hw + grok auth, or "
                  "DEEPSEEK_API_KEY)")
    return 0 if counts["ok"] + counts["partial"] else 1


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(
        prog="research", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("search", help="multi-engine web search")
    s.add_argument("query")
    s.add_argument("--limit", type=int, default=10)
    s.add_argument("--engines", default=None,
                   help="comma list: bing,ddg,wikipedia,arxiv")
    f = sub.add_parser("fetch", help="fetch + decode url(s)")
    f.add_argument("urls", nargs="+")
    f.add_argument("--timeout", type=float, default=FETCH_TIMEOUT)
    f.add_argument("--raw", default=None, help="also save raw bytes to path")
    d = sub.add_parser("decode", help="decode local file(s)")
    d.add_argument("paths", nargs="+")
    d.add_argument("--head", type=int, default=500)
    d.add_argument("--force-pdf", default="",
                   choices=("", "poppler", "python"))
    k = sub.add_parser("kit", help="gather corpus and store labelled kit")
    k.add_argument("targets", nargs="+",
                   help="query (bare) or urls / file paths")
    k.add_argument("--name", default="", help="kit topic slug")
    k.add_argument("--limit", type=int, default=5,
                   help="max web sources to fetch for a query")
    k.add_argument("--engines", default=None)
    k.add_argument("--timeout", type=float, default=FETCH_TIMEOUT)
    k.add_argument("--outdir", default="research",
                   help="parent dir for the kit (default ./research)")
    k.add_argument("--deep", nargs="?", const="auto", default=False,
                   help="draft narrative via a brain (grok|deepseek|auto)")
    t = sub.add_parser("team", help="run/resume a multi-brain research "
                                    "team over a kit")
    t.add_argument("targets", nargs="*", help="query (bare) or urls/files")
    t.add_argument("--name", default="", help="kit topic slug")
    t.add_argument("--kit", default="", help="existing kit dir to team over")
    t.add_argument("--members", default="",
                   help="comma list of brains (grok,deepseek); default: all "
                        "available; change between runs to swap models")
    t.add_argument("--tracks", type=int, default=3,
                   help="max planned tracks (2-5)")
    t.add_argument("--limit", type=int, default=4,
                   help="web sources per track")
    t.add_argument("--engines", default=None)
    t.add_argument("--timeout", type=float, default=FETCH_TIMEOUT)
    t.add_argument("--outdir", default="research")
    t.add_argument("--redo", default="", choices=("", "plan", "research",
                                                  "merge", "all"))
    args = ap.parse_args(argv)
    cmds = {"search": cmd_search, "fetch": cmd_fetch,
            "decode": cmd_decode, "kit": cmd_kit, "team": cmd_team}
    return cmds[args.cmd](args)


def cmd_team(args) -> int:
    explicit_none = bool(args.members.strip()) and not [
        m for m in args.members.split(",")
        if m.strip() and m.strip() not in ("none", "mechanical")]
    members = [m.strip() for m in args.members.split(",")
               if m.strip() and m.strip() not in ("none", "mechanical")]
    avail = brains_available()
    if explicit_none:
        members = []                      # mechanical mode, on purpose
    elif members:
        bad = [m for m in members if m not in avail and m != "none"]
        if bad:
            print(f"team: brains not available now: {', '.join(bad)} "
                  f"(available: {', '.join(avail) or 'none'})")
        members = [m for m in members if m in avail]
    else:
        members = avail
    if args.kit:
        kit = Path(args.kit)
        st = _team_load(kit)
        if not st:
            print(f"team: no state in {kit}/team -- pass targets to start")
            return 1
        topic, query = st.get("topic", kit.name), st.get("query", kit.name)
    else:
        if not args.targets:
            print("team: needs targets, or --kit <dir> to resume")
            return 1
        topic = args.name or args.targets[0]
        query = " ".join(args.targets)
        corpus_stub = Corpus(topic=topic, query=query)
        kit = Path(args.outdir) / _slug(topic)
        if not (kit / "sources.jsonl").is_file():
            corpus = gather(args.targets, limit=max(args.limit, 4),
                            engines=args.engines.split(",")
                            if args.engines else None,
                            timeout=args.timeout, topic=topic[:80])
            kit = write_kit(corpus, args.outdir, name=topic[:80])
        st = _team_load(kit) or {"topic": topic, "query": query}
        if st.get("query") != query:
            st["query"] = query
    print(f"team over {kit} "
          f"(members: {', '.join(members) or 'none -> mechanical'})")
    state = team_run(kit, topic, query, members, n_tracks=args.tracks,
                     limit=args.limit,
                     engines=args.engines.split(",") if args.engines else None,
                     timeout=args.timeout, redo=args.redo)
    done = sum(1 for t in state.get("tracks", []) if t.get("done"))
    print(f"team: {done}/{len(state.get('tracks', []))} tracks, "
          f"merge {'done' if state.get('merge', {}).get('done') else 'pending'}"
          f" -> {kit}/team/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
