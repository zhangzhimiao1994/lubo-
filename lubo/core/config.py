from __future__ import annotations

import configparser
from dataclasses import dataclass
from pathlib import Path

from .models import OutputFormat, Quality


RECORDING_SECTION = "录制设置"


@dataclass(slots=True)
class AppConfig:
    save_path: str = ""
    output_format: OutputFormat = OutputFormat.TS
    quality: Quality = Quality.ORIGINAL
    loop_seconds: int = 300
    max_concurrency: int = 3
    use_proxy: bool = False
    proxy_addr: str = ""
    split_enabled: bool = True
    split_seconds: int = 1800
    convert_to_mp4: bool = True
    douyin_cookie: str = ""


class ConfigService:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> AppConfig:
        parser = self._read()
        return AppConfig(
            save_path=self._get(parser, "直播保存路径(不填则默认)", ""),
            output_format=self._format(self._get(parser, "视频保存格式ts|mkv|flv|mp4|mp3音频|m4a音频", "ts")),
            quality=self._quality(self._get(parser, "原画|超清|高清|标清|流畅", "原画")),
            loop_seconds=self._int(self._get(parser, "循环时间(秒)", "300"), 300),
            max_concurrency=self._int(self._get(parser, "同一时间访问网络的线程数", "3"), 3),
            use_proxy=self._bool(self._get(parser, "是否使用代理ip(是/否)", ""), False),
            proxy_addr=self._get(parser, "代理地址", ""),
            split_enabled=self._bool(self._get(parser, "分段录制是否开启", ""), True),
            split_seconds=self._int(self._get(parser, "视频分段时间(秒)", "1800"), 1800),
            convert_to_mp4=self._bool(self._get(parser, "录制完成后自动转为mp4格式", ""), True),
            douyin_cookie=self._get_from_section(parser, "Cookie", "抖音cookie", ""),
        )

    def save(self, config: AppConfig) -> None:
        values = self._values(config)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text(self._new_section(values, "\n"), encoding="utf-8-sig")
            return

        content = self.path.read_text(encoding="utf-8-sig")
        newline = "\r\n" if "\r\n" in content else "\n"
        self.path.write_text(self._update_existing(content, values, newline), encoding="utf-8-sig")

    @staticmethod
    def _values(config: AppConfig) -> dict[str, str]:
        return {
            "直播保存路径(不填则默认)": config.save_path,
            "视频保存格式ts|mkv|flv|mp4|mp3音频|m4a音频": config.output_format.value,
            "原画|超清|高清|标清|流畅": config.quality.value,
            "循环时间(秒)": str(config.loop_seconds),
            "同一时间访问网络的线程数": str(config.max_concurrency),
            "是否使用代理ip(是/否)": "是" if config.use_proxy else "否",
            "代理地址": config.proxy_addr,
            "分段录制是否开启": "是" if config.split_enabled else "否",
            "视频分段时间(秒)": str(config.split_seconds),
            "录制完成后自动转为mp4格式": "是" if config.convert_to_mp4 else "否",
        }

    @staticmethod
    def _new_section(values: dict[str, str], newline: str) -> str:
        lines = [f"[{RECORDING_SECTION}]"]
        lines.extend(f"{key} = {value}" for key, value in values.items())
        return newline.join(lines) + newline

    def _update_existing(self, content: str, values: dict[str, str], newline: str) -> str:
        lines = content.splitlines(keepends=True)
        target_start = self._find_section_start(lines)
        if target_start is None:
            if lines and not lines[-1].endswith(("\n", "\r")):
                lines[-1] += newline
            if lines and lines[-1].strip():
                lines.append(newline)
            lines.extend(self._new_section(values, newline).splitlines(keepends=True))
            return "".join(lines)

        target_end = self._find_section_end(lines, target_start + 1)
        found: set[str] = set()
        for index in range(target_start + 1, target_end):
            raw, line_ending = self._split_line_ending(lines[index])
            if "=" not in raw:
                continue
            key = raw.split("=", 1)[0].strip()
            if key in values:
                lines[index] = f"{key} = {values[key]}{line_ending or newline}"
                found.add(key)

        missing = [f"{key} = {value}{newline}" for key, value in values.items() if key not in found]
        if missing:
            if target_end > 0 and not lines[target_end - 1].endswith(("\n", "\r")):
                lines[target_end - 1] += newline
            lines[target_end:target_end] = missing
        return "".join(lines)

    @staticmethod
    def _find_section_start(lines: list[str]) -> int | None:
        for index, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("[") and stripped.endswith("]") and stripped[1:-1].strip() == RECORDING_SECTION:
                return index
        return None

    @staticmethod
    def _find_section_end(lines: list[str], start: int) -> int:
        for index in range(start, len(lines)):
            stripped = lines[index].strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                return index
        return len(lines)

    @staticmethod
    def _split_line_ending(line: str) -> tuple[str, str]:
        raw = line.rstrip("\r\n")
        return raw, line[len(raw) :]

    def _read(self) -> configparser.RawConfigParser:
        parser = configparser.RawConfigParser()
        parser.optionxform = str
        if self.path.exists():
            parser.read(self.path, encoding="utf-8-sig")
        if not parser.has_section(RECORDING_SECTION):
            parser.add_section(RECORDING_SECTION)
        return parser

    @staticmethod
    def _get(parser: configparser.RawConfigParser, key: str, default: str) -> str:
        try:
            return parser.get(RECORDING_SECTION, key)
        except (configparser.NoOptionError, configparser.NoSectionError):
            return default

    @staticmethod
    def _get_from_section(
        parser: configparser.RawConfigParser,
        section: str,
        key: str,
        default: str,
    ) -> str:
        try:
            return parser.get(section, key)
        except (configparser.NoOptionError, configparser.NoSectionError):
            return default

    @staticmethod
    def _bool(value: str, default: bool) -> bool:
        normalized = value.strip()
        if normalized == "是":
            return True
        if normalized == "否":
            return False
        return default

    @staticmethod
    def _int(value: str, default: int) -> int:
        try:
            return int(str(value).strip())
        except ValueError:
            return default

    @staticmethod
    def _format(value: str) -> OutputFormat:
        normalized = value.strip().lower().replace("音频", "")
        for item in OutputFormat:
            if normalized == item.value:
                return item
        return OutputFormat.TS

    @staticmethod
    def _quality(value: str) -> Quality:
        for item in Quality:
            if value.strip() == item.value:
                return item
        return Quality.ORIGINAL
