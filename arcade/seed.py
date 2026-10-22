"""Import checked alphaBell seed games: python -m arcade.seed DIRECTORY."""
import argparse
import json
from pathlib import Path
from .settings import Settings
from .storage import Storage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    settings = Settings()
    storage = Storage(settings.data_dir)
    count = 0
    for path in sorted(args.directory.glob("*.json")):
        result = json.loads(path.read_text())
        if "html" not in result:
            result["html"] = path.with_suffix(".html").read_text()
        if len(result["html"].encode()) > settings.max_html_bytes:
            raise ValueError("Seed document exceeds the size limit")
        storage.import_game(result.get("id", path.stem), result, settings.model_label)
        count += 1
    print(f"Imported {count} checked alphaBell games.")


if __name__ == "__main__":
    main()
