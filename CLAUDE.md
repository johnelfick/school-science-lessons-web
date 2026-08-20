# CLAUDE.md — project guide

## What this is

The **graphical website** for School Science Lessons: a modern MkDocs
Material site generated automatically from Dr John Elfick's hand-written
HTML site. John (in his 80s, editing near-daily for decades) keeps his
workflow untouched; this repo rebuilds shortly after his commits
(hourly poll + optional instant trigger).

- Source (John's, read-only): https://github.com/johnelfick/school-science-lessons
- This site: https://johnelfick.github.io/school-science-lessons-web/
- Maintainer: Patrick Janssen (John's son-in-law).

## Architecture

```
.source/                shallow clone of John's repo (never committed)
build/transform.py      parse: crawl from index.html, sanitize markup,
                        split pages into sections, validate all links
build/emit.py           generate: docs/*.md pages, nav in mkdocs.yml,
                        home-page updates list, link rewriting/healing,
                        readability enhancements
build/fixes_page.py     docs/fixes.md — plain-language corrections page
                        so John can gradually fix source errors
build/trim_search.py    post-build: cap search-index text per section
build/check_links.py    post-build: verify every link in site/
.github/workflows/build.yml  hourly-poll rebuild (skip-if-unchanged),
                        optional instant repository_dispatch trigger
                        (setup/source-repo-workflow.yml goes in John's
                        repo), Pages deploy, .last-built-sha stamp commit
```

Full local build (see README for the exact commands): emit --all →
fixes_page → mkdocs build → trim_search → check_links.

## Core invariants — do not break these

1. **1:1 page mapping.** Every source HTML file becomes exactly one site
   page at the same relative path. Provenance footers, link healing, the
   corrections page and automatic regeneration all assume it. Never merge
   or split pages.
2. **John's repo is read-only — STRICTLY, never push to it.** John's
   manual workflow cannot handle merges; any push we make creates
   conflicts he cannot resolve. All repairs happen in our pipeline;
   problems in his files are *reported* (corrections page, which shows
   him the exact line to paste) not edited. The one-time repair pass of
   2026-08-13 was done in person with Patrick syncing John's machine
   afterwards — that condition does not normally hold. Lost canonical
   tags etc. accumulate on the corrections page for John to fix
   himself.
3. **Anchors are sacred.** Section ids keep John's anchor names
   (`2.4.1H` style) so decades of inbound links keep working. Section
   *numbers* are display text — never renumber them.
4. **Structure: membership from John's contents lists, top-level order
   from section numbers.** His group link-lists decide which sections
   nest where (and child order); numbers decide top-level order only.
   File position decides neither (content blocks are interleaved).
   Page-top lists that mix many cross-page links are shortcut indexes,
   kept as collapsed "Quick links" panels. See QUIRKS.md part B.
5. **Best-guess repairs must be logged.** Anything the pipeline guesses
   goes to `report/emit-log.txt` (build artifact) and, in John-friendly
   words, to the public corrections page at /fixes/.
6. **The source format keeps evolving.** John actively modernizes his
   HTML (h1 adoption, dropping <hr> dividers, date suffixes). Expect new
   variants; extend the parser generally, never with per-file hacks.

## The quirks catalogue

`build/QUIRKS.md` documents every class of human error and format
variant the pipeline handles, with real examples and code pointers.
**Read it before touching transform.py or emit.py**, and add an entry
whenever a new quirk is handled.

## Known one-off exceptions

- `NAV_SORT_OVERRIDES` in emit.py — the only place navigation deviates
  from natural filename order (currently one entry). Data, not logic.

## State / pending

- Local preview: `python -m http.server 8123 --bind 127.0.0.1 --directory site`
- GitHub repo creation under the johnelfick account + Pages setup +
  first push: pending (needs John's login; steps in README).
- GoatCounter analytics: live (code "johnelfick", script injected via
  overrides/main.html extrahead block; public dashboard
  https://johnelfick.goatcounter.com, linked under About > Visitor
  statistics). Account currently under Patrick's email, to be moved to
  John's later.
- MkDocs 2.0 will break Material eventually; mkdocs-material is pinned
  in requirements.txt. Revisit before upgrading anything.
