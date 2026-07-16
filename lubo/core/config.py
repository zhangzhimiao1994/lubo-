from __future__ import annotations

import configparser
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from .models import OutputFormat, Quality


PLATFORM_KEYS = ("douyin", "bilibili", "huya", "douyu")


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
    minimum_free_space_mb: int = 1024
    cookies: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        supplied = dict(self.cookies)
        self.cookies = MappingProxyType(
            {key: supplied.get(key, "") for key in PLATFORM_KEYS}
        )


class ConfigService:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> AppConfig:
        parser = self._read()
        return AppConfig(
            save_path=self._get(parser, "recorder", "save_path", ""),
            output_format=self._format(
                self._get(parser, "recorder", "output_format", "ts")
            ),
            quality=self._quality(
                self._get(parser, "recorder", "quality", "original")
            ),
            loop_seconds=self._int(
                self._get(parser, "monitor", "loop_seconds", "300"), 300
            ),
            max_concurrency=self._int(
                self._get(parser, "monitor", "max_concurrency", "3"), 3
            ),
            use_proxy=self._bool(
                self._get(parser, "proxy", "enabled", "false"), False
            ),
            proxy_addr=self._get(parser, "proxy", "address", ""),
            split_enabled=self._bool(
                self._get(parser, "recorder", "split_enabled", "true"), True
            ),
            split_seconds=self._int(
                self._get(parser, "recorder", "split_seconds", "1800"), 1800
            ),
            convert_to_mp4=self._bool(
                self._get(parser, "recorder", "convert_to_mp4", "true"), True
            ),
            minimum_free_space_mb=self._non_negative_int(
                self._get(
                    parser,
                    "recorder",
                    "minimum_free_space_mb",
                    "1024",
                ),
                1024,
            ),
            cookies={
                key: self._get(parser, "cookies", key, "") for key in PLATFORM_KEYS
            },
        )

    def save(self, config: AppConfig) -> None:
        parser = self._parser()
        parser["recorder"] = {
            "save_path": config.save_path,
            "output_format": config.output_format.value,
            "quality": config.quality.name.lower(),
            "split_enabled": self._bool_text(config.split_enabled),
            "split_seconds": str(config.split_seconds),
            "convert_to_mp4": self._bool_text(config.convert_to_mp4),
            "minimum_free_space_mb": str(config.minimum_free_space_mb),
        }
        parser["monitor"] = {
            "loop_seconds": str(config.loop_seconds),
            "max_concurrency": str(config.max_concurrency),
        }
        parser["proxy"] = {
            "enabled": self._bool_text(config.use_proxy),
            "address": config.proxy_addr,
        }
        parser["cookies"] = {
            key: config.cookies.get(key, "") for key in PLATFORM_KEYS
        }

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8", newline="\n") as config_file:
            parser.write(config_file)

    def _read(self) -> configparser.ConfigParser:
        parser = self._parser()
        if self.path.exists():
            parser.read(self.path, encoding="utf-8-sig")
        return parser

    @staticmethod
    def _parser() -> configparser.ConfigParser:
        return configparser.ConfigParser(interpolation=None)

    @staticmethod
    def _get(
        parser: configparser.ConfigParser,
        section: str,
        key: str,
        default: str,
    ) -> str:
        return parser.get(section, key, fallback=default)

    @staticmethod
    def _bool(value: str, default: bool) -> bool:
        normalized = value.strip().casefold()
        if normalized in {"true", "yes", "1"}:
            return True
        if normalized in {"false", "no", "0"}:
            return False
        return default

    @staticmethod
    def _bool_text(value: bool) -> str:
        return "true" if value else "false"

    @staticmethod
    def _int(value: str, default: int) -> int:
        try:
            return int(value.strip())
        except (AttributeError, ValueError):
            return default

    @classmethod
    def _non_negative_int(cls, value: str, default: int) -> int:
        parsed = cls._int(value, default)
        return parsed if parsed >= 0 else default

    @staticmethod
    def _format(value: str) -> OutputFormat:
        normalized = value.strip().casefold()
        for item in OutputFormat:
            if normalized in {item.name.casefold(), item.value.casefold()}:
                return item
        return OutputFormat.TS

    @staticmethod
    def _quality(value: str) -> Quality:
        normalized = value.strip().casefold()
        for item in Quality:
            if normalized in {item.name.casefold(), item.value.casefold()}:
                return item
        return Quality.ORIGINAL
