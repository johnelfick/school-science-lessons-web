"""Post-build: verify every internal link in the built site resolves.

Walks site/**/*.html, resolves every relative href/src, and checks that the
target file exists; for #fragments into HTML pages, checks the id exists in
the target document. External links are not checked (that is John's own
link-rot tooling's job).

Usage: python build/check_links.py [--site site] [--max-report 30]
"""

import argparse
import posixpath
import re
import sys
from collections import Counter
from functools import lru_cache
from pathlib import Path
from urllib.parse import unquote, urlsplit

HREF_RE = re.compile(r"""(?<![-\w])(?:href|src)=["']([^"']+)["']""")
ID_RE = re.compile(r"""(?:id|name)=["']([^"']+)["']""")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", default="site")
    ap.add_argument("--max-report", type=int, default=30)
    args = ap.parse_args()
    site = Path(args.site).resolve()

    # the corrections page quotes broken hrefs as text; don't scan it
    pages = [p for p in sorted(site.rglob("*.html"))
             if p.parent.name != "fixes"]

    @lru_cache(maxsize=512)
    def ids_of(path_str: str) -> frozenset:
        text = Path(path_str).read_text(encoding="utf-8", errors="replace")
        return frozenset(ID_RE.findall(text))

    broken = []
    checked = Counter()
    for page in pages:
        rel_dir = page.parent
        text = page.read_text(encoding="utf-8", errors="replace")
        for href in HREF_RE.findall(text):
            if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", href) or href.startswith("//"):
                checked["external"] += 1
                continue
            parts = urlsplit(href)
            frag = unquote(parts.fragment)
            path = unquote(parts.path)
            if not path:
                checked["same-page fragment"] += 1
                target = page
            else:
                if path.startswith("/"):
                    # site-root-absolute (404 page): strip the site_url prefix
                    root_rel = re.sub(r"^/[^/]+/", "", path)
                    target = (site / root_rel).resolve()
                else:
                    target = (rel_dir / Path(posixpath.normpath(
                        posixpath.join(".", path)))).resolve()
                if path.endswith("/") or (target.is_dir()):
                    target = target / "index.html"
                checked["internal"] += 1
                if not target.exists():
                    broken.append((page.relative_to(site), href, "missing file"))
                    continue
            if frag and target.suffix == ".html" and target.exists():
                if frag not in ids_of(str(target)):
                    broken.append((page.relative_to(site), href,
                                   "missing fragment"))

    print(f"pages: {len(pages)}")
    for k, v in checked.most_common():
        print(f"  {k}: {v}")
    by_kind = Counter(b[2] for b in broken)
    print(f"broken: {len(broken)} {dict(by_kind)}")
    for page, href, kind in broken[:args.max_report]:
        print(f"  [{kind}] {page} -> {href}")
    if len(broken) > args.max_report:
        print(f"  ... and {len(broken) - args.max_report} more")
    return 0


if __name__ == "__main__":
    sys.exit(main())
