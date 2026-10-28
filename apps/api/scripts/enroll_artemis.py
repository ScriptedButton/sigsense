"""Enroll Artemis DB spectrum images as open-set reference prototypes.

    python apps/api/scripts/enroll_artemis.py [--library DIR] [--limit N]
                                              [--cache DIR] [--ref main] [--purge]

Each signal in https://github.com/AresValley/Artemis-DB with a spectrum image
becomes one prototype (label = signal title, source = "artemis") in the FAISS
library the API searches for neighbours. The images are wiki screenshots, so
treat the resulting neighbours as hints: the embedding network was trained on
synthetic spectrograms. Re-running skips signals that are already enrolled;
--purge removes every "artemis" prototype again.

By default this writes the live library under models/. Point --library at a
scratch directory to try it first.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SOURCE = "artemis"
REPO = "AresValley/Artemis-DB"


def fetch(url: str, timeout: float = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "SigSenseArtemisImport/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def list_ids(ref: str) -> list[str]:
    tree = json.loads(
        fetch(f"https://api.github.com/repos/{REPO}/git/trees/{ref}?recursive=1")
    )
    if tree.get("truncated"):
        raise SystemExit("GitHub tree listing was truncated")
    ids = {
        p["path"].split("/")[1]
        for p in tree["tree"]
        if p["path"].startswith("static/") and p["path"].endswith("/media/1.png")
    }
    return sorted(ids, key=int)


def describe(sig: dict) -> str:
    def vals(key: str) -> str:
        return "/".join(str(v["value"]) for v in sig.get(key, [])[:3])

    freq = [v["value"] for v in sig.get("frequency", [])]
    parts = [f"Artemis #{sig['pageid']}"]
    if freq:
        parts.append(f"{min(freq) / 1e6:g}-{max(freq) / 1e6:g} MHz")
    if vals("modulation"):
        parts.append(vals("modulation"))
    if vals("bandwidth"):
        parts.append(f"{vals('bandwidth')} Hz")
    return " · ".join(parts)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--library", type=Path, help="models dir holding library.sqlite/.faiss")
    ap.add_argument("--cache", type=Path, help="download cache directory")
    ap.add_argument("--ref", default="main")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--purge", action="store_true", help="remove all artemis prototypes")
    args = ap.parse_args()

    from sigsense_api.inference import InferenceEngine

    engine = InferenceEngine(models_dir=args.library)
    library = engine.library
    if args.purge:
        print(f"removed {library.remove_source(SOURCE)} artemis prototypes")
        return

    ids = list_ids(args.ref)[: args.limit]
    print(f"{len(ids)} signals with spectrum images")
    base = f"https://raw.githubusercontent.com/{REPO}/{args.ref}/static"

    def load(sid: str) -> tuple[str, dict, bytes] | None:
        try:
            cached = args.cache / f"{sid}.png" if args.cache else None
            if cached and cached.exists():
                png = cached.read_bytes()
            else:
                png = fetch(f"{base}/{sid}/media/1.png")
                if cached:
                    cached.parent.mkdir(parents=True, exist_ok=True)
                    cached.write_bytes(png)
            return sid, json.loads(fetch(f"{base}/{sid}/signal.json")), png
        except Exception as exc:  # one bad record must not stop the batch
            print(f"  skip {sid}: {exc}")
            return None

    enrolled = skipped = failed = 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        for item in pool.map(load, ids):
            if item is None:
                failed += 1
                continue
            sid, sig, png = item
            tag = f"Artemis #{sid}"
            if library.has_note(SOURCE, tag):
                skipped += 1
                continue
            try:
                embedding = engine.analyze_image(png).embedding
            except Exception as exc:
                print(f"  skip {sid} ({sig.get('title')}): {exc}")
                failed += 1
                continue
            library.enroll(
                embedding, sig["title"][:64], source=SOURCE, notes=describe(sig)
            )
            enrolled += 1
            if enrolled % 50 == 0:
                print(f"  enrolled {enrolled}")
    print(f"done: enrolled {enrolled}, already present {skipped}, failed {failed}")


if __name__ == "__main__":
    main()
