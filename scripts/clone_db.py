"""Utility to clone the main receipts.db into isolated dev/prod copies."""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

DEFAULT_SOURCE = Path("receipts.db")
DEFAULT_DEV_TARGET = Path("env/dev/receipts.dev.db")
DEFAULT_PROD_TARGET = Path("env/prod/receipts.prod.db")


def clone_database(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    print(f"Copied {source} -> {target}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Clone the main receipts.db into env-specific copies.")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE, help="Path to the source SQLite DB")
    parser.add_argument("--dev-target", type=Path, default=DEFAULT_DEV_TARGET, help="Destination for dev DB")
    parser.add_argument("--prod-target", type=Path, default=DEFAULT_PROD_TARGET, help="Destination for prod DB")
    parser.add_argument("--only", choices=["dev", "prod"], help="Clone only one environment")
    args = parser.parse_args()

    if not args.source.exists():
        raise SystemExit(f"Source database not found at {args.source}. Run the app once or create the DB before cloning.")

    targets = []
    if args.only == "dev":
        targets.append(args.dev_target)
    elif args.only == "prod":
        targets.append(args.prod_target)
    else:
        targets.extend([args.dev_target, args.prod_target])

    for target in targets:
        clone_database(args.source, target)


if __name__ == "__main__":
    main()
