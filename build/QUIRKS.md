# QUIRKS.md — the source-HTML dialect and how the pipeline handles it

The source corpus is ~265 hand-written HTML files, edited nearly daily
since the 1990s, by one author, without a validator. It is remarkably
*consistent in intent* and wildly inconsistent in syntax. This file
catalogues every class of quirk the pipeline understands: what the
source contains, what would go wrong naively, what we do about it, and
where in code. **Add an entry every time a new quirk is handled.**

Legend: `T` = build/transform.py, `E` = build/emit.py.
Every best-guess repair is logged per page to `report/emit-log.txt` and
explained on the public corrections page (`/fixes/`, build/fixes_page.py).

---

## A. Markup repairs (sanitize_soup in T)

Run on every page before any parsing, in this order.

| # | Quirk | Real example | Failure if unhandled | Repair |
|---|---|---|---|---|
| A1 | Unknown/invented tags | `<sun>` (UNBiology6), `<br.>`, `<heliosupine>` (a chemical name in angle brackets) | lxml treats them as unclosed elements that swallow the rest of the file into one blob | unwrap (keep children), log |
| A2 | Misplaced quotes in attributes | `<a name=">UNBiol1aH</a>` (UNBiol1a) | attribute value swallows following markup; anchor ids contain HTML | reduce `name`/`href` values containing `<>` or whitespace to their plausible token, log |
| A3 | `<font>` wrappers, often unclosed | `</font>Table 7.5.4<font color="#ff0000H">` before a real table (UNChem1) | unclosed font nests block content (tables) into inline context → flattened to text | unwrap ALL font tags unconditionally (their styling is discarded anyway) |
| A4 | Unterminated `<a>` links | `<a href="../images/9.56.1.gif">See diagram…<br>` with no `</a>` (UNBiology1 area; 161 cases) | link swallows following lines/sections; next section becomes a figure caption | if an `<a>` contains `<br>/<hr>/<a>`, move everything from the first such element back out of the link, log |
| A5 | Bare `<a>` with no attributes | `12.2.2.1<a> Decomposition…` (UNChem1; 119 cases) | never closes, swallows the whole section into the heading line | unwrap attribute-less `<a>`, log |
| A6 | Tables with no `<tr>/<td>` at all | UNChem1 "Table 7.5.4": bare text lines, blank line between rows, stray `</tr>` closers | browsers hoist text out of the table; renders as run-together prose (broken on John's site too) | rebuild: blank-line groups = rows, lines = cells, log |
| A7 | Tables with `<tr>` but no `<td>` | UNPh07 "Table 4.2.3": text lines directly inside `<tr>` | same hoisting | wrap each row's text lines as `<td>`s, log |
| A8 | Mid-file `</body></html>` | UNPh05: closers at line 1333, 350 more lines of content after | lxml stops parsing at the first closer; rest of file invisible | `read_html` (T) strips all body/html closers pre-parse, warns if >1 |
| A9 | Non-UTF-8 files | GreekAlphabet.html (latin-1) | decode crash | fall back to latin-1, log |

## B. Structure derivation (T parse_page / split_blocks, E emit_page)

The rules here embody one principle: **John's contents lists are the
authoritative structure; section numbers and file position are not.**

- **B1. Block splitting.** Sections are `<hr>`-separated blocks — but
  newer files (UNBiol1, appendixB, topic04, ProjBan; John is converting
  more) drop the `<hr>`s entirely. A line-starting `<a name>` whose text
  is a section number also starts a block (`split_blocks`).
- **B2. Section-defining anchors.** Only an anchor that STARTS a block's
  first line defines a section. John scatters empty `<a name>` anchors
  mid-body (UNPh05 6.4.x) — sometimes not even adjacent to the matching
  content; those become invisible `<span id>` targets only. Otherwise
  neighbouring prose becomes gigantic headings (`first_line_anchor`).
- **B3. Anchor-less headings.** A block whose first line looks like a
  heading (dotted number + short title, e.g. `6.4.2 Ångström unit, A`)
  is a section even with no anchor; the conventional `<number>H` id is
  synthesized when free so John's links to it still resolve.
- **B4. Titles.** Page h1: body `<h1>` (newer files) > first section
  title > `<title>`. Section titles come from the anchor's own line
  (`anchor_line_title`), never from block position — headers/breadcrumbs
  share blocks in newer files. Trailing "Contents" and "See diagram…"
  text is stripped from titles.
- **B5. The section tree** (E). Contents-list blocks (≥3 lines BEGINNING
  with same-page links, >50% of lines — inline cross-references in prose
  must NOT count) claim the linked sections as children, in listed
  order. Children are pulled from wherever they sit in the file (content
  blocks are interleaved in source, e.g. UNBiology1 Stems). The page's
  top contents list orders the top level. Claims that hit a misplaced
  anchor fall back to matching by section number. Unclaimed sections
  keep file order. Numbers never decide nesting (John files
  `9.9.4.2 Celery stalk` under `9.9.9.0 Stems`).
- **B6. Anchor-less continuation blocks** attach to the preceding
  section.
- **B7. Header-block boilerplate** (breadcrumb, date line, prev/next
  chapter links, "Please send comments", pasted link-checker notes
  `https://… not found`) is dropped (`filter_title_block` in E).
- **B8. Dates.** `2026-05-22a` — revision letter suffixes are part of
  John's convention; DATE_RE accepts them.

## C. Link rewriting and healing (E rewrite_href / heal_fragment)

All ~90k internal links are rewritten to new-site URLs. The source
contains thousands of stale links (John renumbers constantly; browsers
silently scroll to top on a missing anchor, so he never sees it).

- **C1. Typos, auto-fixed:** doubled `##`, trailing `.,:;`, missing
  `.html`, missing `#` (href="9.8.3H" meant "#9.8.3H"), missing `H`
  suffix.
- **C2. Moved anchors, auto-healed:** if the target anchor exists in
  exactly one other page corpus-wide, the link is redirected there
  (~600 links).
- **C3. Ambiguous/gone anchors:** left pointing at the page top —
  identical behaviour to John's site. Listed on the corrections page.
- **C4. Links to excluded pages** (unreachable from index.html — mostly
  stale root-level duplicates) point to the ORIGINAL site.
- **C5. Reachability = crawl from index.html.** Unlinked files are
  excluded automatically; no hand-maintained lists.

## D. Readability enhancements (E render pipeline)

Faithful-but-nicer rendering of John's text conventions:

- **D1. "See diagram" links → inline `<figure>`** with caption. The
  original site has exactly ONE `<img>` tag; all 4,000+ diagrams are
  bare links.
- **D2. Tab-separated text tables** (2+ tabs per line, 2+ consecutive
  lines) → real tables; browsers collapse tabs so these are invisible
  as tables otherwise (UNChem1 "Table 7.5.3").
- **D3. `* ` line runs** (2+) → real `<ul>` bullets. Single `*` lines
  stay: sometimes footnote markers.
- **D4. Leading enumeration markers** (`1.`, `2a.`, `1.2`, `(a)`, and
  the glued `4.Sunglasses` variant) → `<b class="ssl-num">`, bold and
  slightly larger. Styling only — grouping is genuinely ambiguous in the
  source, so we never re-nest.
- **D5. Bare `Experiment(s)` label lines** → styled mini-headings
  (`.ssl-minihead`), NOT real headings (1,200+ of them would flood the
  ToC). Dropped inside contents lists where the linked experiments
  become real headings.
- **D6. Chemical formulas** → `<sub>/<sup>` + real arrows (`-->` → →,
  `<=>` → ⇌). STRICT validation: token must parse completely against
  the 118 element symbols, ≥2 elements (whitelist for O2, N2, Cl2…), so
  "vitamin B12"/"UNPh05" are never touched. Every changed line gets a
  `source` hover-popup with John's original text (`.ssl-src`,
  data-src attribute — note check_links must not parse data-src as a
  link).
- **D7. Passthrough HTML (tables/lists) is serialized on ONE line** —
  blank lines inside a raw HTML block make python-markdown strip
  `<tr>/<td>` tags.

## E. Generated artifacts (regenerated by emit --all; markers, do not edit between them)

- Navigation: `mkdocs.yml` between `BEGIN/END GENERATED NAV` markers.
  Order = natural filename sort per folder + `NAV_SORT_OVERRIDES` (the
  declared exception table).
- Home-page "Recently updated": `docs/index.md` between
  `BEGIN/END GENERATED UPDATES` markers, from per-page date stamps.
- Corrections page: `docs/fixes.md`, fully regenerated by
  fixes_page.py.
- `.gitignore` excludes all generated docs content; only hand-written
  pages (index.md, credits.md, about-john.md) and assets are committed.

## F. Search & checking

- Search index would be 28 MB naive; trim_search.py caps section text
  at 400 chars → ~4 MB (titles carry the retrieval value).
- check_links.py skips the /fixes/ page (it quotes broken hrefs as
  text) and must not match `data-src=` attributes.
- Expected steady-state: ~2,300 "missing fragment" links (John's dead
  anchors, same behaviour as his site) and a handful of unfixable
  malformed-source links. A sudden jump means a pipeline regression.
