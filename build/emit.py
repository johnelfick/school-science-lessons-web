"""Phase 2/3: emit MkDocs pages from the parsed corpus.

Converts each legacy page into a Markdown file: sections become headings that
keep their original anchor ids (so old #fragments still work), "See diagram"
links become inline figures, internal links are rewritten to new-site URLs and
healed where the target anchor has moved, and every page gets a provenance
footer linking to its original version.

Usage:
  python build/emit.py --source .source --docs docs [--pages a.html b.html]
"""

from __future__ import annotations

import argparse
import html
import posixpath
import re
import shutil
import sys
from pathlib import Path
from urllib.parse import unquote

from bs4 import BeautifulSoup, NavigableString, Tag

sys.path.insert(0, str(Path(__file__).parent))
import transform as T

ORIGINAL_BASE = "https://johnelfick.github.io/school-science-lessons/"
INLINE_KEEP = {"b", "i", "u", "em", "strong", "sub", "sup", "small", "code"}
BLOCK_PASSTHROUGH = {"table", "ul", "ol", "dl", "blockquote", "pre"}

PREVIEW_PAGES = [
    "chemistry/UNChem1.html",
    "physics/UNPh06.html",
    "physics/UNPh05.html",
    "biology/UNBiol1.html",
    "primary/year1.html",
    "foodgardens/Foodgardens1.html",
    "projects/ProjSchool.html",
    "soils/Soils1.html",
    "topics/topic03.html",
    "appendices/appendixA.html",
]


class Emitter:
    def __init__(self, repo: Path, docs: Path, pages, anchor_owners):
        self.repo = repo
        self.docs = docs
        self.pages = pages          # rel_path -> transform.Page
        self.anchor_owners = anchor_owners
        self.copied_images: set[str] = set()
        self.log: dict[str, list[str]] = {}   # rel_path -> best-guess actions

    def note(self, rel: str, action: str):
        self.log.setdefault(rel, []).append(action)

    def write_log(self, path: Path):
        lines = [f"Build log — best-guess actions taken by emit.py",
                 f"Pages with interventions: {len(self.log)}", ""]
        for rel in sorted(self.log):
            lines.append(f"{rel}:")
            from collections import Counter
            for action, n in Counter(self.log[rel]).most_common():
                lines.append(f"  - {action}" + (f" (x{n})" if n > 1 else ""))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines), encoding="utf-8")

    # ---------------------------------------------------------- link logic

    def heal_fragment(self, frag: str | None, target: str) -> tuple[str, str | None]:
        """Return (possibly redirected target page, cleaned fragment)."""
        if not frag:
            return target, None
        tpage = self.pages.get(target)
        if tpage and frag in tpage.anchors:
            return target, frag
        cleaned = frag.lstrip("#").strip().rstrip(".,:;")
        if tpage:
            for cand in (cleaned, cleaned + "H"):
                if cand in tpage.anchors:
                    return target, cand
        owners = (self.anchor_owners.get(frag)
                  or self.anchor_owners.get(cleaned)
                  or self.anchor_owners.get(cleaned + "H"))
        if owners and len(owners) == 1:
            new_target = next(iter(owners))
            new_page = self.pages[new_target]
            for cand in (frag, cleaned, cleaned + "H"):
                if cand in new_page.anchors:
                    return new_target, cand
        return target, frag  # unhealable: behave like the original site

    def new_url(self, source_rel: str, target: str, frag: str | None) -> str:
        prefix = "../" * len(Path(source_rel).parts)  # directory-URL depth
        if target == "index.html":
            path = prefix
        else:
            path = prefix + posixpath.splitext(target)[0] + "/"
        return path + (f"#{frag}" if frag else "")

    def rewrite_href(self, source_rel: str, href: str) -> str:
        href = href.strip()
        if not href or T.is_external(href):
            return href
        if href.startswith("#"):
            frag = unquote(href[1:])
            t, healed = self.heal_fragment(frag, source_rel)
            if t != source_rel:
                self.note(source_rel, f"healed link: #{frag} moved to {t}")
                return self.new_url(source_rel, t, healed)
            if healed != frag:
                self.note(source_rel, f"fixed anchor typo: #{frag} -> #{healed}")
            return f"#{healed}" if healed else "#"
        target, frag = T.resolve_href(source_rel, href)
        if target is None:
            return href
        ext = posixpath.splitext(target)[1].lower()
        if ext in T.IMAGE_EXTS:
            return self.image_url(source_rel, target)
        if ext == "" and target + ".html" in self.pages:
            self.note(source_rel, f"fixed link missing .html: {href}")
            target += ".html"              # John omitted ".html"
            ext = ".html"
        if ext == "" and not frag and re.fullmatch(r"[\w.]+H", posixpath.basename(target)) \
                and posixpath.basename(target) in self.anchor_owners:
            # John omitted the "#": href="9.8.3H" meant href="#9.8.3H"
            self.note(source_rel, f"fixed link missing #: {href}")
            t, healed = self.heal_fragment(posixpath.basename(target), source_rel)
            if t != source_rel:
                return self.new_url(source_rel, t, healed)
            return f"#{healed}"
        if ext == ".html":
            if target not in self.pages:   # excluded page -> original site
                self.note(source_rel, f"link to excluded page kept on original site: {target}")
                return ORIGINAL_BASE + target + (f"#{frag}" if frag else "")
            new_target, new_frag = self.heal_fragment(frag, target)
            if new_target != target:
                self.note(source_rel, f"healed link: {target}#{frag} moved to {new_target}")
            elif frag and new_frag != frag:
                self.note(source_rel, f"fixed anchor typo: {target}#{frag} -> #{new_frag}")
            return self.new_url(source_rel, new_target, new_frag)
        return href

    def image_url(self, source_rel: str, image_rel: str) -> str:
        src = self.repo / image_rel
        if src.is_file() and image_rel not in self.copied_images:
            dest = self.docs / image_rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)
            self.copied_images.add(image_rel)
        return "../" * len(Path(source_rel).parts) + image_rel

    # ---------------------------------------------------------- rendering

    # Sentinels that survive inline flattening: BR ends a line, IMG marks an
    # image link's position. Split out again in render_block.flush().
    BR = "\x00"
    IMG = "\x01"

    def render_inline(self, node, source_rel: str) -> str:
        """Render one inline node to HTML with BR/IMG sentinels."""
        if isinstance(node, NavigableString):
            return html.escape(str(node), quote=False)
        if not isinstance(node, Tag):
            return ""
        if node.name == "br":
            return self.BR
        if node.name == "a":
            if node.get("name") and not node.get("href"):
                inner = "".join(self.render_inline(c, source_rel)
                                for c in node.children)
                return f'<span id="{html.escape(node["name"])}">{inner}</span>'
            href = node.get("href", "")
            text = "".join(self.render_inline(c, source_rel)
                           for c in node.children)
            target, _ = T.resolve_href(source_rel, href) if not T.is_external(href) \
                else (None, None)
            if target and posixpath.splitext(target)[1].lower() in T.IMAGE_EXTS:
                src = self.image_url(source_rel, target)
                return f"{text}{self.IMG}{src}{self.IMG}"
            new_href = self.rewrite_href(source_rel, href)
            external = ' target="_blank" rel="noopener"' if T.is_external(href) else ""
            return f'<a href="{html.escape(new_href)}"{external}>{text}</a>'
        if node.name in INLINE_KEEP:
            inner = "".join(self.render_inline(c, source_rel)
                            for c in node.children)
            return f"<{node.name}>{inner}</{node.name}>"
        if node.name == "img":
            src = node.get("src", "")
            target, _ = T.resolve_href(source_rel, src)
            if target:
                return f"{self.IMG}{self.image_url(source_rel, target)}{self.IMG}"
            return ""
        # font, span, center, etc: keep children, drop the wrapper
        return "".join(self.render_inline(c, source_rel)
                       for c in node.children)

    def render_passthrough(self, node: Tag, source_rel: str) -> str:
        """Serialize tables/lists, rewriting hrefs and img srcs inside."""
        for a in node.find_all("a", href=True):
            a["href"] = self.rewrite_href(source_rel, a["href"])
        for img in node.find_all("img", src=True):
            target, _ = T.resolve_href(source_rel, img["src"])
            if target:
                img["src"] = self.image_url(source_rel, target)
        if node.name == "table":
            for attr in ("border", "width", "cellpadding", "cellspacing", "bgcolor"):
                if node.get(attr) is not None:
                    del node[attr]
            return f'<div class="ssl-table-wrap">\n{node}\n</div>'
        return str(node)

    def render_block(self, nodes: list, source_rel: str,
                     skip_frag_link_lines) -> list[str]:
        """Render a block's nodes into a list of HTML chunks.

        skip_frag_link_lines: False keeps everything; True drops every line
        that starts with a same-page fragment link (page-level contents);
        a set drops only lines whose leading link targets an anchor in the
        set (a group's claimed children, which become nested headings).
        """
        chunks: list[str] = []
        parts: list[str] = []

        def emit_line(text: str):
            srcs = re.findall(f"{self.IMG}([^{self.IMG}]*){self.IMG}", text)
            visible = re.sub(
                f"{self.IMG}[^{self.IMG}]*{self.IMG}", "", text).strip()
            if srcs:
                caption = re.sub(r"<[^>]+>", "", visible).strip().rstrip(":")
                caption = caption[:300]
                for src in srcs:
                    chunks.append(
                        f'<figure class="ssl-figure">'
                        f'<a href="{src}"><img src="{src}" alt="{html.escape(caption)}" loading="lazy"></a>'
                        f"<figcaption>{html.escape(caption)}</figcaption></figure>")
                return
            m = re.match(
                r'^[|\s]*(?:<span id="[^"]*">[^<]*</span>\s*)?[|\s]*<a href="#([^"]*)"',
                visible)
            skip = False
            if m and skip_frag_link_lines:
                skip = (skip_frag_link_lines is True
                        or unquote(m.group(1)) in skip_frag_link_lines)
            if visible and not skip:
                chunks.append(f"<p>{visible}</p>")

        def flush():
            for ln in "".join(parts).split(self.BR):
                if ln.strip():
                    emit_line(ln.strip())
            parts.clear()

        for node in nodes:
            if isinstance(node, Tag) and node.name in ("h1", "h2", "h3", "h4"):
                flush()  # page <h1> is emitted separately as the title
            elif isinstance(node, Tag) and node.name in BLOCK_PASSTHROUGH:
                flush()
                chunks.append(self.render_passthrough(node, source_rel))
            elif isinstance(node, Tag) and node.name in ("div", "p", "center"):
                flush()
                chunks.extend(self.render_block(
                    list(node.children), source_rel, skip_frag_link_lines))
            else:
                parts.append(self.render_inline(node, source_rel))
        flush()
        return chunks

    # ---------------------------------------------------------- page emit

    @staticmethod
    def plain(chunk: str) -> str:
        return " ".join(re.sub(r"<[^>]+>", " ", chunk).split())

    @classmethod
    def is_link_only(cls, chunk: str) -> bool:
        """True if a <p> chunk consists solely of links and separators."""
        if not chunk.startswith("<p>"):
            return False
        stripped = re.sub(r"<a\b[^>]*>.*?</a>", "", chunk, flags=re.DOTALL)
        return not re.search(r"[A-Za-z0-9]", cls.plain(stripped))

    def filter_title_block(self, chunks: list[str], h1: str) -> list[str]:
        """Drop breadcrumb/date/title/contents boilerplate, keep real notes."""
        kept = []
        h1_norm = self.plain(f"<p>{h1}</p>").lower()
        for c in chunks:
            text = self.plain(c)
            low = text.lower()
            if not text:
                continue
            if low in ("school science lessons", "contents", "table of contents"):
                continue
            if low.startswith("please send comments"):
                continue
            if T.DATE_RE.match(text):
                continue
            if low == h1_norm or low.rstrip(".") == h1_norm:
                continue
            if re.match(r"^\(?[A-Za-z0-9.]+\)?$", text) and len(text) <= 14:
                continue  # file-code lines like "(UNPh06)"
            if self.is_link_only(c):
                continue  # chapter prev/next nav, replaced by site nav
            kept.append(c)
        return kept

    def emit_page(self, rel: str) -> Path:
        page = self.pages[rel]
        warnings: list[str] = []
        text = T.read_html(self.repo / rel, warnings)
        soup = BeautifulSoup(text, "lxml")
        for action in T.sanitize_soup(soup) + warnings:
            self.note(rel, action)
        blocks = T.split_blocks(soup.body)

        by_index = {s.block_index: s for s in page.sections}
        emitted_ids: set[str] = set()
        out: list[str] = []

        # Page title: body <h1> (newer files) > first section title > <title>.
        titled = page.sections[0] if page.sections else None
        h1 = page.h1 or (titled.title if titled and titled.title else "") \
            or page.title or rel
        h1 = re.split(r"\s+Contents\b", h1)[0]
        h1 = re.sub(r"\s*,?\s*Contents$", "", h1).strip()
        h1_id = ""
        if titled:
            h1_id = f" {{#{titled.anchor}}}"
            emitted_ids.add(titled.anchor)

        safe_title = h1.replace('"', "'")
        out.append("---")
        out.append(f'title: "{safe_title}"')
        out.append("---")
        out.append("")
        out.append(f"# {h1}{h1_id}")
        out.append("")
        if page.date:
            out.append(f'<p class="ssl-source">Last updated {page.date} '
                       f"by Dr John Elfick</p>")
            out.append("")

        # ---- gather sections and attach anchor-less continuation blocks
        sections = [by_index[i] for i in sorted(by_index) if by_index[i] is not titled]
        sec_blocks: dict[int, list[list]] = {}   # section block_index -> node lists
        prev = None
        for i, nodes in enumerate(blocks):
            section = by_index.get(i)
            if section is titled or (i == 0 and section is None):
                body = self.render_block(nodes, rel, skip_frag_link_lines=True)
                out.extend(self.filter_title_block(body, h1))
                out.append("")
                self.note_stray_anchors(nodes, emitted_ids, out)
                continue
            if section is None:
                if prev is not None:
                    sec_blocks[prev.block_index].append(nodes)
                else:
                    out.extend(self.render_block(nodes, rel, False))
                    out.append("")
                continue
            sec_blocks[section.block_index] = [nodes]
            prev = section

        # ---- build the section tree from John's contents lists.
        # A group block's list of same-page links names its children and
        # their intended order; section numbers are too unreliable to use.
        anchor_to_sec = {}
        for s in sections:
            anchor_to_sec.setdefault(s.anchor, s)
            for a in s.extra_anchors:
                anchor_to_sec.setdefault(a, s)

        def frag_targets(node_lists) -> list[str]:
            found = []
            for nodes in node_lists:
                found.extend(T.leading_frag_info(nodes)[0])
            return found

        parent: dict[int, Section] = {}          # child block_index -> group
        kids: dict[int, list] = {}               # group block_index -> children

        def is_ancestor(candidate, s) -> bool:
            while s is not None:
                if s is candidate:
                    return True
                s = parent.get(s.block_index)
            return False

        for s in sections:
            if s.kind != "contents-list":
                continue
            for frag in frag_targets(sec_blocks.get(s.block_index, [])):
                child = anchor_to_sec.get(frag)
                if child is None or child is s or child.block_index in parent \
                        or is_ancestor(child, s):
                    continue
                parent[child.block_index] = s
                kids.setdefault(s.block_index, []).append(child)

        # top-level order: the page contents list first, then file order
        top_frags = frag_targets([blocks[titled.block_index]]) if titled else []
        top_listed = []
        for frag in top_frags:
            s = anchor_to_sec.get(frag)
            if s is not None and s.block_index not in parent and s not in top_listed:
                top_listed.append(s)
        roots = top_listed + [s for s in sections
                              if s.block_index not in parent and s not in top_listed]

        if parent:
            self.note(rel, f"nested {len(parent)} sections under "
                           f"{len(kids)} groups per contents lists")

        # ---- emit depth-first
        emitted_secs: set[int] = set()

        def emit_subtree(section, level: int):
            if section.block_index in emitted_secs:
                return
            emitted_secs.add(section.block_index)
            head = f"{section.number} {section.title}" if section.number \
                else section.title
            head = head.strip() or section.anchor
            out.append(f'{"#" * min(level, 4)} {head} {{#{section.anchor}}}')
            emitted_ids.add(section.anchor)
            out.append("")
            own_kids = kids.get(section.block_index, [])
            if section.kind == "contents-list":
                skip = {c.anchor for c in own_kids} | \
                       {a for c in own_kids for a in c.extra_anchors}
            else:
                skip = False
            body = []
            for nodes in sec_blocks.get(section.block_index, []):
                body.extend(self.render_block(nodes, rel, skip_frag_link_lines=skip))
            head_norm = self.plain(f"<p>{head}</p>").lower()
            body = [c for j, c in enumerate(body)
                    if not (j < 2 and self.plain(c).lower() == head_norm)]
            size = sum(len(c) for c in body)
            if size > 60000:
                self.note(rel, f"section {section.anchor}: unusually large "
                               f"render ({size // 1000} KB) — check structure")
            out.extend(body)
            out.append("")
            for child in own_kids:
                emit_subtree(child, level + 1)

        for s in roots:
            emit_subtree(s, 2)
        for s in sections:      # safety net: anything cycle-broken or orphaned
            emit_subtree(s, 2)

        out.append("---")
        out.append("")
        out.append(
            f'<p class="ssl-source">This page was generated automatically from '
            f'<a href="{ORIGINAL_BASE}{rel}" target="_blank" rel="noopener">'
            f"the original page</a> on John Elfick's School Science Lessons "
            f"website. If something looks wrong, please check the original.</p>")

        dest = self.docs / (posixpath.splitext(rel)[0] + ".md")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("\n".join(out), encoding="utf-8")
        return dest, h1

    def note_stray_anchors(self, nodes, emitted_ids, out):
        """Anchors living in skipped header blocks still need landing spots."""
        strays = [a for a in T.block_anchors(nodes) if a not in emitted_ids]
        if strays:
            out.append("".join(f'<span id="{html.escape(a)}"></span>'
                               for a in strays))
            out.append("")
            emitted_ids.update(strays)


NAV_BEGIN = "# --- BEGIN GENERATED NAV (written by build/emit.py --all) ---"
NAV_END = "# --- END GENERATED NAV ---"

NAV_TABS = [
    ("Chemistry", [("chemistry", None)]),
    ("Physics", [("physics", None)]),
    ("Biology", [("biology", None)]),
    ("Primary science", [("primary", None)]),
    ("Agriculture", [("foodgardens", "Food gardens"), ("soils", "Soils"),
                     ("projects", "School projects")]),
    ("Topics", [("topics", None)]),
    ("Appendices", [("appendices", None)]),
]


def natural_key(s: str):
    return [int(t) if t.isdigit() else t.lower()
            for t in re.split(r"(\d+)", s)]


def nav_label(title: str, rel: str) -> str:
    label = title.strip() or posixpath.basename(rel)
    label = re.sub(r"[.\s]+$", "", label)
    if len(label) > 58:
        label = label[:58].rsplit(" ", 1)[0] + "…"
    return label.replace('"', "'")


def write_nav(mkdocs_yml: Path, titles: dict[str, str]) -> None:
    """Regenerate the nav block in mkdocs.yml from emitted page titles."""
    lines = []

    def add(indent: int, text: str):
        lines.append("  " * indent + text)

    for tab, folders in NAV_TABS:
        add(1, f"- {tab}:")
        for folder, sublabel in folders:
            pages = sorted((r for r in titles if r.startswith(folder + "/")),
                           key=natural_key)
            if not pages:
                continue
            # Topics: split the A-Z chemical index into its own subgroup
            indent = 2
            if sublabel:
                add(2, f"- {sublabel}:")
                indent = 3
            main = [r for r in pages
                    if not posixpath.basename(r).startswith("topicIndex")]
            index_pages = [r for r in pages if r not in main]
            for rel in main:
                md = posixpath.splitext(rel)[0] + ".md"
                add(indent, f'- "{nav_label(titles[rel], rel)}": {md}')
            if index_pages:
                add(indent, '- "Chemical index A–Z":')
                for rel in index_pages:
                    md = posixpath.splitext(rel)[0] + ".md"
                    add(indent + 1,
                        f'- "{nav_label(titles[rel], rel)}": {md}')

    text = mkdocs_yml.read_text(encoding="utf-8")
    begin = text.index(NAV_BEGIN)
    end = text.index(NAV_END)
    new = text[:begin] + NAV_BEGIN + "\n" + "\n".join(lines) + "\n  " \
        + text[end:]
    mkdocs_yml.write_text(new, encoding="utf-8")
    print(f"Nav written: {sum(len(v) for _, v in NAV_TABS)} groups, "
          f"{len(titles)} pages")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=".source")
    ap.add_argument("--docs", default="docs")
    ap.add_argument("--pages", nargs="*", default=None,
                    help="repo-relative pages to emit (default: preview set)")
    ap.add_argument("--all", action="store_true", help="emit every reachable page")
    args = ap.parse_args()

    repo = Path(args.source).resolve()
    docs = Path(args.docs)

    print("Crawling corpus ...")
    pages, _links, _df, _dh, _lm = T.crawl(repo, "lxml")
    anchor_owners = {}
    from collections import defaultdict
    anchor_owners = defaultdict(set)
    for p in pages.values():
        for a in p.anchors:
            anchor_owners[a].add(p.path)

    emitter = Emitter(repo, docs, pages, anchor_owners)
    targets = (sorted(pages) if args.all
               else (args.pages or PREVIEW_PAGES))
    titles: dict[str, str] = {}
    for rel in targets:
        if rel == "index.html":
            continue
        if rel not in pages:
            print(f"  SKIP {rel}: not reachable in corpus")
            continue
        dest, h1 = emitter.emit_page(rel)
        titles[rel] = h1
        print(f"  {rel} -> {dest}")
    print(f"Copied {len(emitter.copied_images)} images.")
    emitter.write_log(Path("report/emit-log.txt"))
    print(f"Best-guess actions on {len(emitter.log)} pages "
          f"-> report/emit-log.txt")
    if args.all:
        write_nav(Path("mkdocs.yml"), titles)
    return 0


if __name__ == "__main__":
    sys.exit(main())
