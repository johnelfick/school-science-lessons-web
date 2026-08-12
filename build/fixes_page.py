"""Generate the nightly "corrections" page for the site editor.

Writes docs/fixes.md — a plain-language, nicely grouped list of every
problem the pipeline found in the source HTML, with a concrete suggested
fix wherever one is known. The page is rebuilt (overwritten) on every
nightly run and published with the site, so the editor can simply open a
link and work through the lists over time.

Usage: python build/fixes_page.py --source .source --docs docs
"""

from __future__ import annotations

import argparse
import datetime
import html
import posixpath
import sys
from collections import defaultdict
from pathlib import Path

# Brisbane is always UTC+10 (Queensland has no daylight saving)
BRISBANE = datetime.timezone(datetime.timedelta(hours=10), "AEST")

sys.path.insert(0, str(Path(__file__).parent))
import transform as T


def esc(s: str) -> str:
    return html.escape(str(s), quote=False)


def rel_href(from_file: str, to_file: str, frag: str | None) -> str:
    """Reconstruct the href in the editor's own convention: files in a
    subfolder always link as ../folder/file.html, root files as ./..."""
    prefix = "../" if "/" in from_file else "./"
    return prefix + to_file + (f"#{frag}" if frag else "")


def file_groups(items: dict[str, list[str]], empty_text: str) -> list[str]:
    """Render {file: [item html]} as per-file collapsible groups."""
    out = []
    if not items:
        out.append(f"<p><em>{esc(empty_text)}</em></p>")
        return out
    for f, rows in sorted(items.items(), key=lambda kv: -len(kv[1])):
        out.append(f"<details><summary><b>{esc(f)}</b> — {len(rows)} "
                   f"item{'s' if len(rows) != 1 else ''}</summary><ul>")
        out.extend(f"<li>{r}</li>" for r in rows)
        out.append("</ul></details>")
    return out


def find_displaced_sections(repo: Path, pages) -> dict[str, list[str]]:
    """Sections whose text sits far from their group in the file.

    Uses the same claim logic as emit.py's section tree: a contents-list
    block claims the sections it links to. A claimed child is 'displaced'
    when another top-level section lies between its group and its text
    (e.g. Foodgardens1: 10.1-10.5 located after 12.0).
    """
    from bs4 import BeautifulSoup
    out: dict[str, list[str]] = {}
    for rel, page in pages.items():
        if len(page.sections) < 3:
            continue
        text = T.read_html(repo / rel)
        soup = BeautifulSoup(text, "lxml")
        T.sanitize_soup(soup)
        if soup.body is None:
            continue
        blocks = T.split_blocks(soup.body)
        secs = [s for s in page.sections[1:]]
        anchor_to = {}
        for s in secs:
            if s.anchor:
                anchor_to.setdefault(s.anchor, s)
            for a in s.extra_anchors:
                anchor_to.setdefault(a, s)
        num_to = {}
        for s in secs:
            if s.number:
                num_to.setdefault(s.number + "H", s)
        parent = {}
        for s in secs:
            if s.kind != "contents-list" or s.block_index >= len(blocks):
                continue
            for frag in T.leading_frag_info(blocks[s.block_index])[0]:
                child = anchor_to.get(frag) or num_to.get(frag)
                if child is None or child is s or child.block_index in parent:
                    continue
                parent[child.block_index] = s
        roots = [s for s in secs if s.block_index not in parent]
        for s in secs:
            g = parent.get(s.block_index)
            if g is None:
                continue
            lo, hi = sorted((g.block_index, s.block_index))
            between = [r for r in roots if lo < r.block_index < hi and r is not g]
            if between:
                gname = f"{g.number or ''} {g.title}".strip()
                sname = f"{s.number or ''} {s.title}".strip()
                bname = f"{between[0].number or ''} {between[0].title}".strip()
                out.setdefault(rel, []).append(
                    f"The text of <b>{esc(sname)}</b> belongs under "
                    f"<b>{esc(gname)}</b>, but in the file it sits beyond "
                    f"<b>{esc(bname)}</b>. Moving it next to the other "
                    f"sections of its group makes the page easier to follow.")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=".source")
    ap.add_argument("--docs", default="docs")
    args = ap.parse_args()
    repo = Path(args.source).resolve()

    pages, all_links, disk_files, disk_html, lower_map = T.crawl(repo, "lxml")
    issues, _stats = T.validate_links(pages, all_links, disk_files, lower_map, repo)
    dupes = T.find_duplicate_anchors(repo, pages, "lxml")
    unreachable = sorted(disk_html - set(pages))
    displaced = find_displaced_sections(repo, pages)

    anchor_owners: dict[str, set[str]] = defaultdict(set)
    for p in pages.values():
        for a in p.anchors:
            anchor_owners[a].add(p.path)

    # ---- bucket the link issues, with suggestions where known
    typos: dict[str, list[str]] = defaultdict(list)
    moved: dict[str, list[str]] = defaultdict(list)
    gone: dict[str, list[str]] = defaultdict(list)
    ambiguous: dict[str, list[str]] = defaultdict(list)
    other: dict[str, list[str]] = defaultdict(list)

    def clean_frag(frag: str) -> str:
        c = frag.lstrip("#").strip().rstrip(".,:;")
        return c if c in anchor_owners or not (c + "H") in anchor_owners else c + "H"

    for i in issues:
        target, frag = T.resolve_href(i.source, i.href)
        code = f"<code>{esc(i.href)}</code>"
        p = i.problem
        if "typo" in p:
            c = clean_frag(frag or "")
            fix = rel_href(i.source, target, c) if target else f"#{c}"
            typos[i.source].append(
                f"{code} has a small typing mistake — change it to "
                f"<code>{esc(fix)}</code>")
        elif "moved to another page" in p:
            c = clean_frag(frag or "")
            owners = anchor_owners.get(c) or anchor_owners.get(frag or "")
            owner = next(iter(owners)) if owners else None
            if owner:
                moved[i.source].append(
                    f"{code} — this section now lives in <b>{esc(owner)}</b>. "
                    f"Change the link to <code>{esc(rel_href(i.source, owner, c))}</code>")
        elif "gone from corpus" in p:
            gone[i.source].append(
                f"{code} — the section <code>#{esc(frag or '')}</code> no longer "
                f"exists anywhere, so the link only reaches the top of the page. "
                f"Remove the link, or point it at the right section.")
        elif "ambiguous" in p:
            c = clean_frag(frag or "")
            owners = sorted(anchor_owners.get(c, []))[:4]
            ambiguous[i.source].append(
                f"{code} — a section with this name exists in several files "
                f"({esc(', '.join(owners))}). The link should point at one of them.")
        else:
            other[i.source].append(f"{code} — {esc(p)}")

    # ---- HTML problems from the parser's repair log (page.warnings)
    html_problems: dict[str, list[str]] = defaultdict(list)
    for p in pages.values():
        for w in p.warnings:
            if w.startswith("closed unterminated link"):
                href = w.split("(", 1)[-1].rstrip(")")
                html_problems[p.path].append(
                    f"A link is missing its closing tag. Search for "
                    f"<code>{esc(href)}</code> and add <code>&lt;/a&gt;</code> "
                    f"at the end of the link text.")
            elif w.startswith("unwrapped unknown tag"):
                tag = w.split("<", 1)[-1].rstrip(">")
                html_problems[p.path].append(
                    f"The text contains <code>&lt;{esc(tag)}&gt;</code>, which is "
                    f"not a real HTML tag. It should probably be removed or "
                    f"spelled differently.")
            elif w.startswith("rebuilt table"):
                html_problems[p.path].append(
                    "A table is written without <code>&lt;tr&gt;</code> and "
                    "<code>&lt;td&gt;</code> row tags, so browsers show its "
                    "text outside the table. Each row needs "
                    "<code>&lt;tr&gt;&lt;td&gt;...&lt;/td&gt;&lt;/tr&gt;</code> tags.")
            elif "</body> tags" in w:
                html_problems[p.path].append(
                    "The file contains <code>&lt;/body&gt;&lt;/html&gt;</code> in "
                    "the middle, with more content after it. Delete the early "
                    "closing tags — only the very end of the file should have them.")
            else:
                html_problems[p.path].append(esc(w))

    dup_groups: dict[str, list[str]] = {}
    for f, d in dupes.items():
        rows = [f"<code>{esc(k)}</code> appears {v} times — each section name "
                f"should be used only once in a file"
                for k, v in sorted(d.items(), key=lambda kv: -kv[1])[:10]]
        if len(d) > 10:
            rows.append(f"... and {len(d) - 10} more duplicated names")
        dup_groups[f] = rows

    n_typos = sum(len(v) for v in typos.values())
    n_moved = sum(len(v) for v in moved.values())
    n_gone = sum(len(v) for v in gone.values())
    n_amb = sum(len(v) for v in ambiguous.values())
    n_other = sum(len(v) for v in other.values())
    n_html = sum(len(v) for v in html_problems.values())

    now = datetime.datetime.now(BRISBANE)
    date_line = now.strftime("%A %d %B %Y, %I:%M %p (Brisbane time)")

    out: list[str] = []
    w = out.append
    w("---")
    w('title: "Corrections list"')
    w("search:")
    w("  exclude: true")
    w("---")
    w("")
    w("# Corrections list")
    w("")
    w(f'<p class="ssl-source">Generated automatically on <b>{date_line}</b>. '
      f"This page is rebuilt every night from the latest version of the "
      f"website files.</p>")
    w("")
    w("<p>This page lists small problems found automatically in the source "
      "files of <a href='https://johnelfick.github.io/school-science-lessons/' "
      "target='_blank' rel='noopener'>School Science Lessons</a>. "
      "The modern website repairs most of them on the fly, but fixing them in "
      "the source files makes both websites better. Click a file name to see "
      "its items.</p>")
    w("")
    w("| Kind of problem | How many |")
    w("|---|---|")
    w(f"| Typing mistakes in links (fix suggested) | {n_typos} |")
    w(f"| Links to sections that moved (fix suggested) | {n_moved} |")
    w(f"| Links to sections that no longer exist | {n_gone} |")
    w(f"| Links to section names used in several files | {n_amb} |")
    w(f"| HTML problems (missing closing tags, etc.) | {n_html} |")
    w(f"| Files with duplicated section names | {len(dup_groups)} |")
    w(f"| Sections sitting away from their group | {sum(len(v) for v in displaced.values())} |")
    w(f"| Files no longer linked from the website | {len(unreachable)} |")
    if n_other:
        w(f"| Other link problems | {n_other} |")
    w("")

    w("## 1. Typing mistakes in links")
    w("")
    w("<p>Small slips — a doubled <code>##</code>, a full stop at the end, a "
      "missing <code>.html</code>. Each one comes with the exact replacement.</p>")
    out.extend(file_groups(typos, "None found — well done!"))
    w("")
    w("## 2. Links to sections that moved")
    w("")
    w("<p>These links point at a file that no longer contains that section — "
      "the section now lives in a different file. Each item says where it "
      "went and what the link should say instead.</p>")
    out.extend(file_groups(moved, "None found."))
    w("")
    w("## 3. Links to sections that no longer exist")
    w("")
    w("<p>The section these links point to could not be found in any file — "
      "it was probably renamed or renumbered at some point. On the website "
      "these links still open the right page, but land at the top instead of "
      "the section.</p>")
    out.extend(file_groups(gone, "None found."))
    w("")
    w("## 4. Links to section names used in several files")
    w("")
    out.extend(file_groups(ambiguous, "None found."))
    w("")
    w("## 5. HTML problems")
    w("")
    w("<p>Missing closing tags and mistyped tags. These can make text after "
      "them display incorrectly.</p>")
    out.extend(file_groups(html_problems, "None found."))
    w("")
    w("## 6. Duplicated section names")
    w("")
    out.extend(file_groups(dup_groups, "None found."))
    w("")
    w("## 7. Sections sitting away from their group")
    w("")
    w("<p>These sections belong to a numbered group (according to the "
      "contents list of that group), but their text is located somewhere "
      "else in the file, past other sections. The graphical website "
      "re-orders them automatically; moving the text in the source file "
      "puts both websites right.</p>")
    out.extend(file_groups(displaced, "None found."))
    w("")
    w("## 8. Files no longer linked from the website")
    w("")
    w("<p>No page links to these files any more, so visitors cannot reach "
      "them. They are probably old copies. If they are not needed, they can "
      "be deleted; if they are needed, a link to them should be added "
      "somewhere.</p>")
    if unreachable:
        w("<ul>")
        out.extend(f"<li><code>{esc(f)}</code></li>" for f in unreachable)
        w("</ul>")
    else:
        w("<p><em>None found.</em></p>")
    w("")

    dest = Path(args.docs) / "fixes.md"
    dest.write_text("\n".join(out), encoding="utf-8")
    total = n_typos + n_moved + n_gone + n_amb + n_html + n_other
    print(f"Wrote {dest}: {total} link/HTML items, "
          f"{len(dup_groups)} dup-anchor files, {len(unreachable)} unlinked files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
