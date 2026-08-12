"""Post-build: shrink the client-side search index.

Material's search index carries the full text of every section; for this
corpus that is ~13 MB, which is too heavy for school connections. Section
titles carry most of the retrieval value, so cap each entry's text at a fixed
number of characters (word-aligned). Titles are never touched.

Usage: python build/trim_search.py [--site site] [--max-chars 400]
"""

import argparse
import json
from pathlib import Path

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", default="site")
    ap.add_argument("--max-chars", type=int, default=400)
    args = ap.parse_args()

    path = Path(args.site) / "search" / "search_index.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    before = path.stat().st_size
    for doc in data["docs"]:
        text = doc.get("text", "")
        if len(text) > args.max_chars:
            doc["text"] = text[:args.max_chars].rsplit(" ", 1)[0] + " …"
    path.write_text(json.dumps(data, ensure_ascii=False,
                               separators=(",", ":")), encoding="utf-8")
    after = path.stat().st_size
    print(f"search index: {before/1e6:.1f} MB -> {after/1e6:.1f} MB")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
