from __future__ import annotations

import argparse
import configparser
from pathlib import Path


CONFIG_SCHEMA = {
    "recorder": {
        "save_path": "",
        "output_format": "ts",
        "quality": "original",
        "split_enabled": "true",
        "split_seconds": "1800",
        "convert_to_mp4": "true",
    },
    "monitor": {
        "loop_seconds": "300",
        "max_concurrency": "3",
    },
    "proxy": {
        "enabled": "false",
        "address": "",
    },
    "cookies": {
        "douyin": "",
        "bilibili": "",
        "huya": "",
        "douyu": "",
    },
}


def sanitize_config(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise FileNotFoundError(source)

    source_parser = configparser.ConfigParser(interpolation=None)
    source_parser.read(source, encoding="utf-8-sig")
    source_parser.defaults().clear()

    sanitized = configparser.ConfigParser(interpolation=None)
    for section, defaults in CONFIG_SCHEMA.items():
        sanitized[section] = {
            key: (
                ""
                if section == "cookies" or key == "save_path"
                else source_parser.get(section, key, fallback=default)
            )
            for key, default in defaults.items()
        }

    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8", newline="\n") as config_file:
        sanitized.write(config_file)


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
