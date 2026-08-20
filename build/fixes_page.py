"""Generate the "corrections" page for the site editor.

Writes docs/fixes.md — a plain-language, nicely grouped list of every
problem the pipeline found in the source HTML, with a concrete suggested
fix wherever one is known. The page is rebuilt (overwritten) on every
build and published with the site, so the editor can simply open a
link and work through the lists over time.

Usage: python build/fixes_page.py --source .source --docs docs
"""

from __future__ import annotations

import argparse
import datetime
import html
import posixpath
import sys
from urllib.parse import unquote
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


def find_displaced_sections(repo: Path, pages, all_links=None):
    """Structural analysis: displaced sections and renumbering suggestions.

    Uses the same claim logic as emit.py's section tree: a contents-list
    block claims the sections it links to. A claimed child is 'displaced'
    when another top-level section lies between its group and its text
    (e.g. Foodgardens1: 10.1-10.5 located after 12.0). A child whose
    number does not extend its group's number gets a renumbering
    suggestion (e.g. 9.9.1 listed under 9.9.9.0 Stems).
    """
    import re as _re
    from bs4 import BeautifulSoup
    from collections import defaultdict as _dd
    refs = _dd(int)
    for src, href, _t in (all_links or []):
        if T.is_external(href):
            continue
        if href.startswith("#"):
            refs[(src, unquote(href[1:]))] += 1
        else:
            tgt, frag = T.resolve_href(src, href)
            if tgt and frag:
                refs[(tgt, unquote(frag))] += 1
    renumber: dict[str, list[str]] = {}
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
        kids: dict[int, list] = {}
        for s in secs:
            if s.kind != "contents-list" or s.block_index >= len(blocks):
                continue
            for frag in T.leading_frag_info(blocks[s.block_index])[0]:
                child = anchor_to.get(frag) or num_to.get(frag)
                if child is None or child is s or child.block_index in parent:
                    continue
                parent[child.block_index] = s
                kids.setdefault(s.block_index, []).append(child)

        # renumbering suggestions: children whose number is from another family
        for s in secs:
            if not s.number or s.block_index not in kids:
                continue
            prefix = _re.sub(r"\.0$", "", s.number) + "."
            wrong = [c for c in kids[s.block_index]
                     if c.number and not (c.number + ".").startswith(prefix)]
            if not wrong:
                continue
            gname = f"{s.number} {s.title}".strip()
            nums = ", ".join(c.number for c in wrong[:10]) \
                + (" …" if len(wrong) > 10 else "")
            n_refs = sum(refs.get((rel, c.anchor), 0) for c in wrong if c.anchor)
            renumber.setdefault(rel, []).append(
                f"The group <b>{esc(gname)}</b> lists sections numbered "
                f"{esc(nums)}, which do not match the group's own number. "
                f"If they belong here, renumbering them {esc(prefix)}1, "
                f"{esc(prefix)}2, … makes the numbering consistent — but "
                f"then the {n_refs} link(s) pointing at them must be "
                f"updated too. If they are only cross-references to other "
                f"groups, or the group's own number is the wrong one, a "
                f"different fix applies. Please judge case by case.")
        roots = [s for s in secs if s.block_index not in parent]
        by_group: dict[int, tuple] = {}
        for s in secs:
            g = parent.get(s.block_index)
            if g is None:
                continue
            lo, hi = sorted((g.block_index, s.block_index))
            # Interposing contents-list roots are John's normal layout
            # (all group lists at the top); only unrelated CONTENT between
            # a group and its child means real displacement.
            between = [r for r in roots
                       if lo < r.block_index < hi and r is not g
                       and r.kind == "content"]
            if between:
                gname, gkids, seps = by_group.setdefault(
                    g.block_index,
                    (f"{g.number or ''} {g.title}".strip(), [], set()))
                gkids.append(s.number or s.title)
                seps.add(f"{between[0].number or ''} {between[0].title}".strip())
        for gname, gkids, seps in by_group.values():
            shown = ", ".join(gkids[:8]) + (" …" if len(gkids) > 8 else "")
            out.setdefault(rel, []).append(
                f"The sections of <b>{esc(gname)}</b> ({esc(shown)}) have "
                f"their text located beyond <b>{esc(sorted(seps)[0])}</b>. "
                f"Moving them next to their group makes the page easier "
                f"to follow.")
    return out, renumber


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
    displaced, renumber = find_displaced_sections(repo, pages, all_links)

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
        elif "empty href" in p:
            other[i.source].append(
                "A link with no address — <code>href=\"\"</code>. Give the "
                "link an address, or remove the link tags around the text.")
        else:
            other[i.source].append(
                f"{code} points to a file that does not exist. Correct the "
                f"address or remove the link.")

    # ---- HTML problems from the parser's repair log (page.warnings)
    # Every message must tell John exactly what to search for and what to
    # do — never show him raw pipeline log lines.
    STRAY_A = ("The file contains a stray <code>&lt;a&gt;</code> tag with "
               "nothing in it — it does nothing and confuses browsers. "
               "Search for <code>&lt;a&gt;</code> and delete it.")
    html_problems: dict[str, list[str]] = defaultdict(list)
    for p in pages.values():
        for w in p.warnings:
            if w.startswith("closed unterminated link"):
                href = w.split("(", 1)[-1].rstrip(")")
                if "no attributes" in href:
                    html_problems[p.path].append(STRAY_A)
                else:
                    html_problems[p.path].append(
                        f"A link is missing its closing tag. Search for "
                        f"<code>{esc(href)}</code> and add <code>&lt;/a&gt;</code> "
                        f"at the end of the link text.")
            elif w.startswith("unwrapped empty <a> tag"):
                html_problems[p.path].append(STRAY_A)
            elif w.startswith("unwrapped unknown tag"):
                tag = w.split("<", 1)[-1].rstrip(">")
                html_problems[p.path].append(
                    f"The text contains <code>&lt;{esc(tag)}&gt;</code>, which is "
                    f"not a real HTML tag. It should probably be removed or "
                    f"spelled differently.")
            elif w.startswith("repaired malformed anchor name"):
                val = w.split("(", 1)[-1].rstrip(")").strip("'\"")
                html_problems[p.path].append(
                    f"A quotation mark is in the wrong place in a section-name "
                    f"tag — search for <code>{esc(val[:40])}</code>. The name "
                    f"must sit inside the quotes, like "
                    f"<code>&lt;a name=\"...\"&gt;</code>.")
            elif w.startswith("repaired malformed link address"):
                val = w.split("(", 1)[-1].rstrip(")").strip("'\"")
                html_problems[p.path].append(
                    f"A quotation mark is in the wrong place in a link — "
                    f"search for <code>{esc(val[:40])}</code>. The address "
                    f"must sit inside the quotes, like "
                    f"<code>&lt;a href=\"...\"&gt;</code>.")
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
            elif w.startswith("not valid UTF-8"):
                html_problems[p.path].append(
                    "The file is saved in an old text encoding. Re-saving it "
                    "as UTF-8 (the editor's default) fixes accented letters "
                    "and symbols.")
            else:
                html_problems[p.path].append(esc(w))

    # fold the few remaining link oddities into HTML problems (one section
    # fewer for the editor)
    for k, v in other.items():
        html_problems[k].extend(v)

    # merge repeated identical messages within a file into one line
    from collections import Counter as _Counter
    for f in list(html_problems):
        merged = []
        for msg, n in _Counter(html_problems[f]).items():
            if n > 1 and msg == STRAY_A:
                merged.append(
                    f"The file contains {n} stray <code>&lt;a&gt;</code> tags "
                    f"with nothing in them — they do nothing and confuse "
                    f"browsers. Search for <code>&lt;a&gt;</code> and delete "
                    f"each one.")
            elif n > 1:
                merged.append(f"{msg} <em>({n} times in this file)</em>")
            else:
                merged.append(msg)
        html_problems[f] = merged

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
      f"This page is rebuilt automatically whenever the website files "
      f"change.</p>")
    w("")
    w("<p>This page lists small problems found automatically in the source "
      "files of <a href='https://johnelfick.github.io/school-science-lessons/' "
      "target='_blank' rel='noopener'>School Science Lessons</a>. "
      "These issues are being worked on and are gradually being fixed — in "
      "the meantime the website repairs most of them automatically. If you "
      "find any other errors, please let us know at "
      "<a href='mailto:j.elfick@uq.edu.au'>j.elfick@uq.edu.au</a>. "
      "Click a file name to see its items.</p>")
    w("")
    # canonical-link check: every reachable page should declare its
    # graphical-edition equivalent (added 2026-08-13; a page saved from an
    # older copy loses the line)
    NEW_BASE = "https://johnelfick.github.io/school-science-lessons-web/"
    import re as _re2
    canonical_missing: dict[str, list[str]] = {}
    for rel in pages:
        raw = T.read_html(repo / rel)
        expected = NEW_BASE if rel == "index.html" \
            else NEW_BASE + posixpath.splitext(rel)[0] + "/"
        m = _re2.search(r'<link rel="canonical" href="([^"]+)"', raw)
        line = f'&lt;link rel="canonical" href="{expected}"&gt;'
        if m is None:
            canonical_missing.setdefault(rel, []).append(
                f"The canonical line is missing. Add this inside "
                f"<code>&lt;head&gt;</code>: <code>{line}</code>")
        elif m.group(1) != expected:
            canonical_missing.setdefault(rel, []).append(
                f"The canonical line points to <code>{esc(m.group(1))}</code> "
                f"but should be <code>{line}</code>")

    unlinked_groups: dict[str, list[str]] = {}
    for f in unreachable:
        if f.startswith("google") and f.endswith(".html"):
            continue   # Google site-verification file: unlinked by design
        unlinked_groups[f] = [
            "No page links to this file, so visitors cannot reach it. "
            "If it is not needed it can be deleted; if it is needed, "
            "add a link to it somewhere."]
    unlinked_body = file_groups(unlinked_groups, "None found.")

    # ---- unused images: on disk but not referenced by any linked page
    import hashlib
    referenced_imgs = set()
    for src, href, _t in all_links:
        if T.is_external(href) or href.startswith("#"):
            continue
        tgt, _f = T.resolve_href(src, href)
        if tgt and posixpath.splitext(tgt)[1].lower() in T.IMAGE_EXTS:
            referenced_imgs.add(tgt)
    disk_imgs = {f for f in disk_files
                 if posixpath.splitext(f)[1].lower() in T.IMAGE_EXTS}
    unused_imgs = disk_imgs - referenced_imgs
    # images used only by the unlinked files above
    used_by_unlinked = set()
    for rel in unreachable:
        raw = T.read_html(repo / rel)
        for m in __import__("re").finditer(r"images/([\w.\-]+)", raw):
            used_by_unlinked.add("images/" + m.group(1))
    truly_unused = sorted(unused_imgs - used_by_unlinked)
    tied_to_unlinked = sorted(unused_imgs & used_by_unlinked)
    img_body = [
        "<p><b>Before deleting anything:</b> other websites may link "
        "directly to image files, so a quick check of any file you are "
        "unsure about is worthwhile.</p>"]
    if truly_unused:
        img_body.append(
            f"<details><summary><b>{len(truly_unused)} images not used "
            f"anywhere</b></summary><ul>" + "".join(
                f"<li><code>{esc(f)}</code></li>" for f in truly_unused)
            + "</ul></details>")
    if tied_to_unlinked:
        img_body.append(
            f"<details><summary><b>{len(tied_to_unlinked)} images used only "
            f"by the unlinked files listed above</b> (delete together with "
            f"those files, or keep if the files stay)</summary><ul>" + "".join(
                f"<li><code>{esc(f)}</code></li>" for f in tied_to_unlinked)
            + "</ul></details>")
    if not truly_unused and not tied_to_unlinked:
        img_body = ["<p><em>None found — every image is in use.</em></p>"]

    # ---- duplicate files: identical content in more than one place
    def norm_hash(path):
        raw = (repo / path).read_bytes()
        return hashlib.md5(b"".join(raw.split())).hexdigest()

    by_hash: dict[str, list[str]] = {}
    for f in sorted(disk_html | disk_imgs):
        try:
            by_hash.setdefault(norm_hash(f), []).append(f)
        except OSError:
            continue
    # which pages link to each file (so the editor knows where to look)
    users: dict[str, set] = defaultdict(set)
    for src, href, _t in all_links:
        if T.is_external(href) or href.startswith("#"):
            continue
        tgt, _f = T.resolve_href(src, href)
        if tgt:
            users[tgt].add(src)

    dup_groups_files: dict[str, list[str]] = {}
    reachable_set = set(pages)
    for group in by_hash.values():
        if len(group) < 2:
            continue

        def used(f):
            return f in reachable_set or f in referenced_imgs

        def where(f):
            u = sorted(users.get(f, []))
            if not u:
                return "not linked — candidate for deletion"
            shown = ", ".join(f"<code>{esc(x)}</code>" for x in u[:3])
            more = f" and {len(u) - 3} more pages" if len(u) > 3 else ""
            return f"used by {shown}{more}"
        primary = sorted(group, key=lambda f: not used(f))[0]
        items = [f"Identical content to <code>{esc(other)}</code> "
                 f"({where(other)}). This copy: {where(primary)}. Point all "
                 f"links at one copy, then delete the other."
                 for other in group if other != primary]
        dup_groups_files[primary] = items
    n_dup_groups = len(dup_groups_files)
    dup_body = (["<p>The same content stored under more than one name or "
                 "folder. Usually the linked copy should stay and the "
                 "unlinked one can go; if both are in use, the links should "
                 "be pointed at one of them first.</p>"]
                + file_groups(dup_groups_files, "None found."))
    if not dup_groups_files:
        dup_body = ["<p><em>None found.</em></p>"]

    # One list drives BOTH the summary table and the sections, so their
    # titles, order and counts always match; the table links to each section.
    sections_def = [
        ("Typing mistakes in links", n_typos,
         "<p>Small slips — a doubled <code>##</code>, a full stop at the end, "
         "a missing <code>.html</code>. Each one comes with the exact "
         "replacement.</p>",
         file_groups(typos, "None found — well done!")),
        ("Links to sections that moved", n_moved,
         "<p>These links point at a file that no longer contains that "
         "section — the section now lives in a different file. Each item "
         "says where it went and what the link should say instead.</p>",
         file_groups(moved, "None found.")),
        ("Links to sections that no longer exist", n_gone,
         "<p>The section these links point to could not be found in any "
         "file — it was probably renamed or renumbered at some point. On the "
         "website these links still open the right page, but land at the top "
         "instead of the section.</p>",
         file_groups(gone, "None found.")),
        ("Links to section names used in several files", n_amb, None,
         file_groups(ambiguous, "None found.")),
        ("HTML problems", n_html,
         "<p>Missing closing tags and mistyped tags. These can make text "
         "after them display incorrectly.</p>",
         file_groups(html_problems, "None found.")),
        ("Duplicated section names", len(dup_groups), None,
         file_groups(dup_groups, "None found.")),
        ("Sections in the wrong order",
         sum(len(v) for v in displaced.values()),
         "<p>These sections belong to a numbered group (according to the "
         "contents list of that group), but their text is located somewhere "
         "else in the file, past other sections. The graphical website "
         "re-orders them automatically; moving the text in the source file "
         "puts both websites right.</p>",
         file_groups(displaced, "None found.")),
        ("Suggested renumbering", sum(len(v) for v in renumber.values()),
         "<p>These groups list sections whose numbers come from a different "
         "family (for example 9.9.1 listed under 9.9.9.0 Stems). The website "
         "shows them in the right place regardless, so there is no urgency — "
         "but consistent numbering makes the files easier to maintain. These "
         "need judgment: some entries may be deliberate cross-references, "
         "and sometimes it is the group's own number that is wrong.</p>",
         file_groups(renumber, "None found.")),
        ("Pages missing their canonical link",
         sum(len(v) for v in canonical_missing.values()),
         "<p>Every page carries one line in its <code>&lt;head&gt;</code> "
         "telling search engines that the graphical website is the official "
         "version. A page saved from an older copy can lose it; this list "
         "shows the exact line to put back.</p>",
         file_groups(canonical_missing, "None — every page has its line.")),
        ("Files no longer linked from the website", len(unlinked_groups),
         "<p>No page links to these files any more, so visitors cannot "
         "reach them. They are probably old copies. If they are not needed, "
         "they can be deleted; if they are needed, a link to them should be "
         "added somewhere.</p>",
         unlinked_body),
        ("Unused images", len(truly_unused) + len(tied_to_unlinked), None,
         img_body),
        ("Duplicate files", n_dup_groups, None, dup_body),
    ]

    # Ordering (per Patrick, 2026-08-20): the short, actionable sections
    # first; the very long lists (moved/gone/ambiguous links, displaced
    # sections, renumbering) at the bottom in this fixed order — NOT sorted
    # by count, which would shuffle as numbers change.
    _order = [
        "Typing mistakes in links",
        "HTML problems",
        "Duplicated section names",
        "Pages missing their canonical link",
        "Files no longer linked from the website",
        "Unused images",
        "Duplicate files",
        "Suggested renumbering",
        "Sections in the wrong order",
        "Links to sections that moved",
        "Links to section names used in several files",
        "Links to sections that no longer exist",
    ]
    sections_def.sort(key=lambda s: _order.index(s[0]))

    w("| Kind of problem | How many |")
    w("|---|---|")
    for i, (title, count, _intro, _body) in enumerate(sections_def, 1):
        w(f"| [{i}. {title}](#sec-{i}) | {count} |")
    w("")
    for i, (title, count, intro, body) in enumerate(sections_def, 1):
        w(f"## {i}. {title} {{#sec-{i}}}")
        w("")
        if intro:
            w(intro)
        out.extend(body)
        w("")

    dest = Path(args.docs) / "fixes.md"
    dest.write_text("\n".join(out), encoding="utf-8")
    total = n_typos + n_moved + n_gone + n_amb + n_html + n_other
    print(f"Wrote {dest}: {total} link/HTML items, "
          f"{len(dup_groups)} dup-anchor files, {len(unreachable)} unlinked files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
