"""Phase 1: crawl, parse and validate the school-science-lessons corpus.

Reads John's source repo (legacy HTML), discovers all pages reachable from
index.html, splits every page into <hr>-delimited sections keyed by
<a name="..."> anchors, and validates the whole link graph (internal links,
anchors, images). Emits:

  report/validation.md   human-readable validation report
  report/site-index.json machine-readable section index + link map foundation

Usage:
  python build/transform.py --source .source [--out report]
"""

from __future__ import annotations

import argparse
import json
import posixpath
import re
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlsplit

from bs4 import BeautifulSoup, NavigableString, Tag

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[a-z]?$")
SECTION_NUM_RE = re.compile(r"^\d+(?:\.\d+)*[a-z]?$")
IMAGE_EXTS = {".gif", ".jpg", ".jpeg", ".png", ".jfif"}


# ---------------------------------------------------------------- data model

@dataclass
class Section:
    anchor: str          # first <a name> in the block, e.g. "2.4.1H"
    number: str | None   # leading section number if present, e.g. "2.4.1"
    title: str           # first text line of the block
    kind: str            # "content" | "contents-list" | "header"
    block_index: int
    n_lines: int
    extra_anchors: list[str] = field(default_factory=list)


@dataclass
class Page:
    path: str                       # repo-relative posix path
    title: str = ""
    h1: str = ""                    # body <h1> (newer files only)
    description: str = ""
    date: str | None = None
    sections: list[Section] = field(default_factory=list)
    anchors: set[str] = field(default_factory=set)
    blocks_without_anchor: int = 0
    warnings: list[str] = field(default_factory=list)


@dataclass
class LinkIssue:
    source: str
    href: str
    problem: str


# ---------------------------------------------------------------- utilities

CLOSERS_RE = re.compile(r"</(?:body|html)\s*>", re.IGNORECASE)


def read_html(path: Path, page_warnings: list[str] | None = None) -> str:
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        if page_warnings is not None:
            page_warnings.append("not valid UTF-8, decoded as latin-1")
        text = raw.decode("latin-1")
    # Some files contain </body></html> mid-document with real content after
    # it; lxml stops parsing there. Strip all closers (lxml re-synthesizes
    # them) and flag the file.
    n_body_closers = len(re.findall(r"</body\s*>", text, re.IGNORECASE))
    if n_body_closers > 1 and page_warnings is not None:
        page_warnings.append(
            f"{n_body_closers} </body> tags (content after premature close)")
    return CLOSERS_RE.sub("", text)


KNOWN_TAGS = {
    "html", "head", "body", "meta", "title", "link", "script", "style",
    "a", "b", "i", "u", "em", "strong", "sub", "sup", "small", "big", "code",
    "tt", "s", "strike", "abbr", "cite", "q", "mark", "wbr",
    "br", "hr", "p", "div", "span", "center", "font", "img", "figure",
    "figcaption", "table", "tbody", "thead", "tfoot", "tr", "td", "th",
    "caption", "colgroup", "col", "ul", "ol", "li", "dl", "dt", "dd",
    "blockquote", "pre", "h1", "h2", "h3", "h4", "h5", "h6", "form",
    "input", "button", "iframe", "video", "audio", "source", "nobr",
}


def sanitize_soup(soup: BeautifulSoup) -> list[str]:
    """Best-guess repairs of malformed markup, logged for review.

    - Unwrap non-HTML tags (e.g. a literal <sun>) so they cannot swallow
      the rest of the document.
    - Close unterminated links: an <a href> that ends up containing <br>,
      <hr> or another <a> was never closed in the source (links are always
      single-line in this corpus); everything from the first such element
      onward is moved back out of the link.
    """
    actions = []
    for el in soup.find_all(True):
        if el.name not in KNOWN_TAGS:
            actions.append(f"unwrapped unknown tag <{el.name}>")
            el.unwrap()
    for a in soup.find_all("a", href=True):
        if a.find(["br", "hr", "a"]) is None:
            continue
        actions.append(f"closed unterminated link ({a.get('href', '')[:60]})")
        moved, move = False, []
        for child in list(a.children):
            if not moved and isinstance(child, Tag) and (
                    child.name in ("br", "hr", "a")
                    or child.find(["br", "hr", "a"]) is not None):
                moved = True
            if moved:
                move.append(child)
        for node in reversed(move):
            a.insert_after(node)
    return actions


def resolve_href(from_path: str, href: str) -> tuple[str | None, str | None]:
    """Resolve a relative href against a repo-relative posix path.

    Returns (target_path, fragment); target_path is None for pure-fragment
    links, and the caller filters external schemes before calling.
    """
    parts = urlsplit(href.strip())
    frag = unquote(parts.fragment) or None
    if not parts.path:
        return None, frag
    base = posixpath.dirname(from_path)
    target = posixpath.normpath(posixpath.join(base, unquote(parts.path)))
    return target.lstrip("./"), frag


def is_external(href: str) -> bool:
    return bool(re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", href))  # http:, mailto:, ...


# ---------------------------------------------------------------- parsing

def split_blocks(body: Tag) -> list[list]:
    """Split the direct children of <body> into blocks at <hr> tags."""
    blocks: list[list] = [[]]
    for node in body.children:
        if isinstance(node, Tag) and node.name == "hr":
            blocks.append([])
        else:
            blocks[-1].append(node)
    return [b for b in blocks if any(
        (isinstance(n, Tag)) or (isinstance(n, NavigableString) and n.strip())
        for n in b
    )]


def block_lines(nodes: list) -> list[str]:
    """Flatten a block into text lines, treating <br> as newline."""
    parts: list[str] = []

    def walk(node):
        if isinstance(node, NavigableString):
            parts.append(str(node))
        elif isinstance(node, Tag):
            if node.name == "br":
                parts.append("\n")
            elif node.name in ("script", "style"):
                return
            elif node.name in ("table", "ul", "ol"):
                parts.append(f"\n[{node.name}]\n")
            else:
                for child in node.children:
                    walk(child)

    for n in nodes:
        walk(n)
    text = "".join(parts)
    return [ln.strip() for ln in text.split("\n") if ln.strip()]


def block_anchors(nodes: list) -> list[str]:
    names: list[str] = []
    for n in nodes:
        if isinstance(n, Tag):
            if n.name == "a" and n.get("name"):
                names.append(n["name"])
            names.extend(a["name"] for a in n.find_all("a", attrs={"name": True}))
    return names


def anchor_line_title(nodes: list, anchor_name: str) -> str | None:
    """Text of the anchor element plus what follows it up to the next <br>.

    More reliable than the block's first line: newer files put breadcrumbs and
    dates in the same block before the title anchor.
    """
    for n in nodes:
        if not isinstance(n, Tag):
            continue
        el = n if (n.name == "a" and n.get("name") == anchor_name) \
            else n.find("a", attrs={"name": anchor_name})
        if el is None:
            continue
        parts = [el.get_text()]
        for sib in el.next_siblings:
            if isinstance(sib, Tag) and sib.name in ("br", "hr", "table"):
                break
            parts.append(sib.get_text() if isinstance(sib, Tag) else str(sib))
        title = " ".join("".join(parts).split())
        # John sometimes puts a "See diagram" link on the title line
        return re.split(r"\s+See diagram\b", title)[0].strip()
    return None


def leading_frag_info(nodes: list) -> tuple[list[str], int]:
    """Per-line analysis: which lines BEGIN with a same-page fragment link.

    Returns (targets of leading fragment links in order, number of non-empty
    lines). Only leading links count — inline cross-references inside prose
    must not make a block look like a contents list, nor claim children.
    """
    lines: list[list] = [[]]

    def walk(n):
        if isinstance(n, NavigableString):
            lines[-1].append(("text", str(n)))
        elif isinstance(n, Tag):
            if n.name == "br":
                lines.append([])
            elif n.name in ("script", "style"):
                return
            elif n.name == "a" and n.get("href") is not None:
                href = (n.get("href") or "").strip()
                target = unquote(href[1:]) if href.startswith("#") else None
                lines[-1].append(("link", target))
            else:
                for c in n.children:
                    walk(c)

    for n in nodes:
        walk(n)

    targets, n_lines = [], 0
    for toks in lines:
        meaningful = [t for t in toks
                      if not (t[0] == "text"
                              and re.fullmatch(r"[\s|,.:;·•-]*", t[1]))]
        if not meaningful:
            continue
        n_lines += 1
        kind, value = meaningful[0]
        if kind == "link" and value:
            targets.append(value)
    return targets, n_lines


def classify_block(nodes: list, lines: list[str]) -> str:
    """A block where most lines begin with same-page links is a contents list."""
    targets, n_lines = leading_frag_info(nodes)
    if len(targets) >= 3 and n_lines and len(targets) / n_lines > 0.5:
        return "contents-list"
    return "content"


def parse_page(repo: Path, rel_path: str, parser: str) -> tuple[Page, list[tuple[str, str]]]:
    """Parse one file. Returns (Page, list of (href, link_text)) for link audit."""
    page = Page(path=rel_path)
    text = read_html(repo / rel_path, page.warnings)
    soup = BeautifulSoup(text, parser)
    page.warnings.extend(sanitize_soup(soup))

    if soup.title and soup.title.string:
        page.title = soup.title.string.strip()
    desc = soup.find("meta", attrs={"name": "description"})
    if desc and desc.get("content"):
        page.description = desc["content"].strip()

    body = soup.body
    if body is None:
        page.warnings.append("no <body> element found")
        return page, []

    # Collect anchors from the whole document, not per block, so nothing is
    # missed even if markup quirks relocate content.
    page.anchors.update(a["name"] for a in soup.find_all("a", attrs={"name": True}))

    h1_el = body.find("h1")
    if h1_el:
        page.h1 = h1_el.get_text(" ", strip=True)

    hrefs = [(a.get("href", ""), a.get_text(" ", strip=True))
             for a in body.find_all("a", href=True)]

    for i, nodes in enumerate(split_blocks(body)):
        lines = block_lines(nodes)
        anchors = block_anchors(nodes)
        page.anchors.update(anchors)

        if i == 0:
            for ln in lines:
                if DATE_RE.match(ln):
                    page.date = ln
                    break

        if not anchors:
            page.blocks_without_anchor += 1
            continue

        first_line = anchor_line_title(nodes, anchors[0]) \
            or (lines[0] if lines else "")
        number = None
        m = re.match(r"^(\d+(?:\.\d+)*[a-z]?)\s+(.*)", first_line)
        title = first_line
        if m and SECTION_NUM_RE.match(m.group(1)):
            number, title = m.group(1), m.group(2)

        kind = "header" if i == 0 else classify_block(nodes, lines)
        page.sections.append(Section(
            anchor=anchors[0], number=number, title=title.strip(),
            kind=kind, block_index=i, n_lines=len(lines),
            extra_anchors=anchors[1:],
        ))

    return page, hrefs


# ---------------------------------------------------------------- crawl

def crawl(repo: Path, parser: str):
    """BFS from index.html over internal .html links."""
    disk_files = {p.relative_to(repo).as_posix()
                  for p in repo.rglob("*") if p.is_file()}
    disk_html = {p for p in disk_files if p.endswith(".html")
                 and not posixpath.basename(p).startswith("._")}
    # Windows is case-insensitive; GitHub Pages is not. Keep a lowercase map
    # so we can distinguish "missing" from "wrong case".
    lower_map = defaultdict(list)
    for p in disk_files:
        lower_map[p.lower()].append(p)

    pages: dict[str, Page] = {}
    all_links: list[tuple[str, str, str]] = []   # (source, href, text)
    queue = ["index.html"]
    seen = {"index.html"}

    while queue:
        rel = queue.pop(0)
        page, hrefs = parse_page(repo, rel, parser)
        pages[rel] = page
        for href, link_text in hrefs:
            all_links.append((rel, href, link_text))
            if is_external(href) or href.startswith("#"):
                continue
            target, _frag = resolve_href(rel, href)
            if target and target.endswith(".html") and target in disk_html \
                    and target not in seen:
                seen.add(target)
                queue.append(target)

    return pages, all_links, disk_files, disk_html, lower_map


# ---------------------------------------------------------------- validation

def check_fragment(frag: str, anchors: set[str]) -> str | None:
    """Classify a fragment against a page's anchor set.

    Returns None if it resolves, "fixable" if a mechanical cleanup (extra
    leading '#', trailing punctuation, stray whitespace) makes it resolve,
    else "dangling".
    """
    if frag in anchors:
        return None
    cleaned = frag.lstrip("#").strip().rstrip(".,:;")
    if cleaned in anchors or (cleaned + "H") in anchors:
        return "fixable"
    return "dangling"


def classify_dangling(frag: str, anchor_owners: dict[str, set[str]]) -> str:
    """For a fragment missing from its target page, check the whole corpus."""
    cleaned = frag.lstrip("#").strip().rstrip(".,:;")
    owners = (anchor_owners.get(frag) or anchor_owners.get(cleaned)
              or anchor_owners.get(cleaned + "H"))
    if owners and len(owners) == 1:
        return "anchor moved to another page (auto-healable)"
    if owners:
        return "anchor ambiguous (exists in multiple pages)"
    return "anchor gone from corpus"


def validate_links(pages, all_links, disk_files, lower_map, repo: Path):
    issues: list[LinkIssue] = []
    stats = Counter()

    anchor_owners: dict[str, set[str]] = defaultdict(set)
    for p in pages.values():
        for a in p.anchors:
            anchor_owners[a].add(p.path)

    for source, href, _text in all_links:
        if not href.strip():
            issues.append(LinkIssue(source, href, "empty href"))
            continue
        if is_external(href):
            stats["external"] += 1
            continue
        if href.startswith("#"):
            stats["same-page fragment"] += 1
            frag = unquote(href[1:])
            verdict = check_fragment(frag, pages[source].anchors) if frag else None
            if verdict == "fixable":
                issues.append(LinkIssue(source, href, "same-page anchor typo (auto-fixable)"))
            elif verdict == "dangling":
                issues.append(LinkIssue(
                    source, href,
                    "same-page " + classify_dangling(frag, anchor_owners)))
            continue

        target, frag = resolve_href(source, href)
        if target is None:
            continue
        ext = posixpath.splitext(target)[1].lower()

        if ext in IMAGE_EXTS:
            stats["image link"] += 1
            if target not in disk_files:
                hit = lower_map.get(target.lower())
                problem = f"image wrong case (disk: {hit[0]})" if hit else "image file missing"
                issues.append(LinkIssue(source, href, problem))
            continue

        if ext == ".html":
            stats["internal page link"] += 1
            if target not in disk_files:
                hit = lower_map.get(target.lower())
                problem = f"page wrong case (disk: {hit[0]})" if hit else "target page missing"
                issues.append(LinkIssue(source, href, problem))
                continue
            if frag:
                tpage = pages.get(target)
                if tpage is None:
                    issues.append(LinkIssue(source, href, "links to page outside crawl"))
                else:
                    verdict = check_fragment(frag, tpage.anchors)
                    if verdict == "fixable":
                        issues.append(LinkIssue(source, href, "cross-page anchor typo (auto-fixable)"))
                    elif verdict == "dangling":
                        issues.append(LinkIssue(
                            source, href,
                            "cross-page " + classify_dangling(frag, anchor_owners)))
            continue

        stats[f"other ({ext or 'no ext'})"] += 1
        if target not in disk_files:
            issues.append(LinkIssue(source, href, "target file missing"))

    return issues, stats


def find_duplicate_anchors(repo: Path, pages, parser: str):
    """Anchors declared more than once in the same file (link ambiguity)."""
    dupes = {}
    for rel in pages:
        text = read_html(repo / rel)
        names = re.findall(r'<a\s[^>]*name="([^"]+)"', text)
        counted = Counter(names)
        d = {k: v for k, v in counted.items() if v > 1}
        if d:
            dupes[rel] = d
    return dupes


# ---------------------------------------------------------------- reporting

def write_reports(out: Path, repo: Path, pages, all_links, issues, stats,
                  disk_html, dupes, elapsed: float):
    reachable = set(pages)
    unreachable = sorted(disk_html - reachable)
    total_sections = sum(len(p.sections) for p in pages.values())
    content_sections = sum(1 for p in pages.values()
                           for s in p.sections if s.kind == "content")
    no_section_pages = sorted(p.path for p in pages.values() if not p.sections)
    warn_pages = {p.path: p.warnings for p in pages.values() if p.warnings}

    by_problem = defaultdict(list)
    for i in issues:
        by_problem[i.problem.split(" (disk:")[0]].append(i)

    lines = []
    w = lines.append
    w("# Corpus validation report")
    w("")
    w(f"Generated by `build/transform.py` in {elapsed:.1f}s")
    w("")
    w("## Summary")
    w("")
    w(f"| Metric | Value |")
    w(f"|---|---|")
    w(f"| HTML files on disk | {len(disk_html)} |")
    w(f"| Pages reachable from index.html | {len(reachable)} |")
    w(f"| Pages excluded (unreachable) | {len(unreachable)} |")
    w(f"| Total sections | {total_sections} |")
    w(f"| ... content sections | {content_sections} |")
    w(f"| ... contents-list / header blocks | {total_sections - content_sections} |")
    w(f"| Total links audited | {len(all_links)} |")
    for k, v in stats.most_common():
        w(f"| ... {k} | {v} |")
    w(f"| Link issues | {len(issues)} |")
    w(f"| Files with duplicate anchors | {len(dupes)} |")
    w("")

    w("## Pages excluded from the new site (not linked from anywhere reachable)")
    w("")
    for p in unreachable:
        w(f"- {p}")
    w("")

    if no_section_pages:
        w("## Pages where no sections could be parsed")
        w("")
        for p in no_section_pages:
            w(f"- {p}")
        w("")

    if warn_pages:
        w("## Page-level warnings")
        w("")
        for p, ws in sorted(warn_pages.items()):
            for msg in ws:
                w(f"- {p}: {msg}")
        w("")

    w("## Link issues by type")
    w("")
    for problem, items in sorted(by_problem.items(), key=lambda kv: -len(kv[1])):
        w(f"### {problem} ({len(items)})")
        w("")
        for i in items[:40]:
            w(f"- {i.source} -> `{i.href}`" +
              (f"  ({i.problem})" if "(disk:" in i.problem else ""))
        if len(items) > 40:
            w(f"- ... and {len(items) - 40} more")
        w("")

    if dupes:
        w("## Duplicate anchors within a file (top 25 files)")
        w("")
        ranked = sorted(dupes.items(), key=lambda kv: -sum(kv[1].values()))
        for rel, d in ranked[:25]:
            worst = sorted(d.items(), key=lambda kv: -kv[1])[:5]
            w(f"- {rel}: {len(d)} duplicated names, e.g. " +
              ", ".join(f"`{k}`x{v}" for k, v in worst))
        w("")

    (out / "validation.md").write_text("\n".join(lines), encoding="utf-8")

    index = {
        "generated_from": str(repo),
        "pages": [
            {
                "path": p.path,
                "title": p.title,
                "description": p.description,
                "date": p.date,
                "sections": [
                    {"anchor": s.anchor, "number": s.number, "title": s.title,
                     "kind": s.kind, "lines": s.n_lines,
                     "extra_anchors": s.extra_anchors}
                    for s in p.sections
                ],
            }
            for p in pages.values()
        ],
        "excluded": unreachable,
    }
    (out / "site-index.json").write_text(
        json.dumps(index, indent=1), encoding="utf-8")


# ---------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=".source", help="path to John's repo")
    ap.add_argument("--out", default="report")
    ap.add_argument("--parser", default="lxml", choices=["lxml", "html5lib"])
    args = ap.parse_args()

    repo = Path(args.source).resolve()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    print(f"Crawling from {repo / 'index.html'} ...")
    pages, all_links, disk_files, disk_html, lower_map = crawl(repo, args.parser)
    print(f"  {len(pages)} pages reachable, {len(all_links)} links found")

    print("Validating links ...")
    issues, stats = validate_links(pages, all_links, disk_files, lower_map, repo)
    print(f"  {len(issues)} issues")

    print("Checking duplicate anchors ...")
    dupes = find_duplicate_anchors(repo, pages, args.parser)

    write_reports(out, repo, pages, all_links, issues, stats,
                  disk_html, dupes, time.time() - t0)
    print(f"Wrote {out / 'validation.md'} and {out / 'site-index.json'} "
          f"in {time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
