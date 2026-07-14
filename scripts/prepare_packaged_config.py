from __future__ import annotations

import argparse
import configparser
from pathlib import Path


def sanitize_config(source: Path, destination: Path) -> None:
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(source, encoding="utf-8-sig")

    if parser.has_option("recorder", "save_path"):
        parser.set("recorder", "save_path", "")
    if parser.has_section("cookies"):
        for key in parser.options("cookies"):
            parser.set("cookies", key, "")

    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8", newline="\n") as config_file:
        parser.write(config_file)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    sanitize_config(
        args.source / "config.ini",
        args.output / "config.ini",
    )
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "URL_config.ini").write_bytes(b"")


if __name__ == "__main__":
    main()
