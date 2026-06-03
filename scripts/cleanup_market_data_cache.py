from __future__ import annotations

from pathlib import Path

from quant_system.core.config import settings


def main() -> None:
    cache_dir = Path(settings.cache_dir)
    if not cache_dir.exists():
        print(f"cache dir not found: {cache_dir}")
        return

    deleted = []
    kept = []
    for path in cache_dir.glob("*.json"):
        if path.name == "stock_list.json":
            kept.append(path.name)
            continue
        if path.name.startswith(("daily_", "minute_")):
            path.unlink(missing_ok=True)
            deleted.append(path.name)
        else:
            kept.append(path.name)

    print(f"cache_dir={cache_dir}")
    print(f"deleted_count={len(deleted)}")
    print(f"kept_count={len(kept)}")
    if deleted:
        print("deleted_samples=", deleted[:10])
    if kept:
        print("kept=", kept[:10])


if __name__ == "__main__":
    main()
