from __future__ import annotations

import argparse
from pathlib import Path


SENSITIVE_SECTIONS = {"Cookie", "Authorization", "账号密码"}
SAVE_PATH_KEY_PREFIX = "直播保存路径"


def sanitize_config(source: Path, destination: Path) -> None:
    content = source.read_text(encoding="utf-8-sig")
    newline = "\r\n" if "\r\n" in content else "\n"
    section = ""
    sanitized: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1].strip()
            sanitized.append(line)
            continue
        if "=" not in line:
            sanitized.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if section in SENSITIVE_SECTIONS or key.startswith(SAVE_PATH_KEY_PREFIX):
            indentation = line[: len(line) - len(line.lstrip())]
            sanitized.append(f"{indentation}{key} =")
        else:
            sanitized.append(line)

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        newline.join(sanitized) + newline,
        encoding="utf-8-sig",
    )


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
    (args.output / "URL_config.ini").write_text("", encoding="utf-8-sig")


if __name__ == "__main__":
    main()
