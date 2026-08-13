# school-science-lessons-web

Modern web edition of [School Science Lessons](https://johnelfick.github.io/school-science-lessons/)
by Dr John Elfick. This repository holds the build pipeline that converts the
original hand-written HTML into a modern MkDocs Material site, rebuilt
automatically from the
[source repository](https://github.com/johnelfick/school-science-lessons).

The source repository is treated as read-only: the original site and its
editing workflow are never modified.

Code in this repository is MIT-licensed (see LICENSE); the lesson content
remains © Dr John Elfick, free to use for educational purposes.

## Documentation

- [CLAUDE.md](CLAUDE.md) — project guide: architecture, core invariants,
  pending work. Start here.
- [build/QUIRKS.md](build/QUIRKS.md) — the catalogue of source-HTML
  quirks (human errors and format variants) the pipeline handles, with
  examples and code pointers. Required reading before changing
  transform.py or emit.py.

## Pipeline

```
.source/               fresh clone of johnelfick/school-science-lessons (not committed)
build/transform.py     crawl from index.html -> parse sections -> validate links
report/validation.md   per-build corpus health report
report/site-index.json section index / link map (input for site generation)
```

Local run:

```
pip install -r requirements.txt
git clone --depth 1 https://github.com/johnelfick/school-science-lessons .source
python build/transform.py --source .source --out report
```

## Full build

```
python build/emit.py --source .source --docs docs --all   # all pages + nav
python build/fixes_page.py --source .source --docs docs   # editor's corrections page
python -m mkdocs build                                     # -> site/
python build/trim_search.py                                # shrink search index
python build/check_links.py                                # verify built links
```

The corrections page (published at /fixes/) is a plain-language list of
every problem found in the source files — broken links with suggested
replacements, missing closing tags, unused files — regenerated on every
build so
the editor can gradually fix the source at their own pace.

## Automatic build (GitHub Actions)

[.github/workflows/build.yml](.github/workflows/build.yml) polls
hourly, and can also be triggered instantly on every source commit by
installing [setup/source-repo-workflow.yml](setup/source-repo-workflow.yml)
in the source repository (optional; see the comments in that file):

1. Clones the source repository (shallow).
2. Skips the build if the source HEAD matches `.last-built-sha`
   (manual and pipeline-push runs always build).
3. Runs the transform, builds the site, trims the search index, verifies
   links, and uploads `report/` as a build artifact (30-day retention).
4. Deploys to GitHub Pages and commits the new `.last-built-sha` stamp.
   The stamp commit also keeps the scheduled workflow from being disabled
   by GitHub's 60-day repository-inactivity rule.

## One-time GitHub setup

Needs to be done once, signed in to the `johnelfick` GitHub account
(or after adding a collaborator):

1. Create a new **public** repository named `school-science-lessons-web`
   (empty — no README/.gitignore).
2. Optional: Settings → Collaborators → add the maintainer's own account.
3. Push this repository:
   `git remote add origin https://github.com/johnelfick/school-science-lessons-web.git`
   then `git push -u origin main`.
4. Settings → Pages → **Build and deployment → Source: GitHub Actions**.
5. Actions tab → "Build and deploy" → **Run workflow** for the
   first build. The site appears at
   <https://johnelfick.github.io/school-science-lessons-web/>.

## Status

- Phase 1 (parser + corpus validation): done
- Phase 2 (design + MkDocs skeleton): done
- Phase 3 (full site generation, search tuning, link check): done
- Phase 4 (automatic GitHub Action + Pages): workflow ready — awaiting the
  one-time GitHub setup above
