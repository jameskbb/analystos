"""CLI: `python -m analystos_demo generate --out data/demo/summit-supply-seed42 [--seed 42]`."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .generator import generate
from .loader import bootstrap_paths, default_cache_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="analystos_demo", description="Summit Supply Co. demo dataset")
    sub = parser.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate", help="generate the dataset (always regenerates)")
    g.add_argument("--out", type=Path, default=None, help="output directory (default: $DATA_DIR/demo/...)")
    g.add_argument("--seed", type=int, default=42)
    e = sub.add_parser("ensure", help="generate only if the cached copy is missing or stale")
    e.add_argument("--out", type=Path, default=None)
    e.add_argument("--seed", type=int, default=42)
    s = sub.add_parser("scenarios", help="print the answer-key headlines")
    s.add_argument("--out", type=Path, default=None)
    s.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(argv)
    out = args.out or default_cache_dir(args.seed)
    if args.cmd == "generate":
        t0 = time.perf_counter()
        m = generate(out, seed=args.seed)
        print(
            f"generated {len(m.files)} files, {m.order_count:,} orders, {m.order_line_count:,} lines "
            f"in {time.perf_counter() - t0:.1f}s -> {out}\ncontent hash {m.content_hash}"
        )
        return 0
    paths = bootstrap_paths(out, args.seed)
    if args.cmd == "ensure":
        print(f"dataset ready at {paths.data_dir} (hash {paths.manifest.content_hash})")
        return 0
    data = json.loads(paths.scenarios_path.read_text(encoding="utf-8"))
    for sid, story in data["stories"].items():
        print(f"{sid}: {json.dumps(story['headline'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
