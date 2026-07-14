from __future__ import annotations

from pathlib import Path

from .models import Quality, RecordingTarget, normalize_url


QUALITY_BY_VALUE = {item.value: item for item in Quality}


def _looks_like_url(value: str) -> bool:
    candidate = value.strip().lower()
    return (
        "://" in candidate
        or candidate.startswith(("www.", "live.", "v."))
        or "." in candidate
    )


class UrlStore:
    def __init__(
        self,
        path: str | Path,
        default_quality: Quality = Quality.ORIGINAL,
    ) -> None:
        self.path = Path(path)
        self.default_quality = default_quality

    def load(self) -> list[RecordingTarget]:
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8-sig", errors="ignore").splitlines()
        return self.load_from_lines(lines)

    def load_from_lines(self, lines: list[str]) -> list[RecordingTarget]:
        targets: list[RecordingTarget] = []
        seen: set[str] = set()
        for raw_line in lines:
            line = raw_line.strip()
            if not line:
                continue
            enabled = not line.startswith("#")
            if not enabled:
                line = line.lstrip("#").strip()
            if not line:
                continue
            target = self._parse_line(line, enabled)
            if target.url in seen:
                continue
            seen.add(target.url)
            targets.append(target)
        return targets

    def save(self, targets: list[RecordingTarget]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lines = [self._format_target(target) for target in targets]
        content = "\n".join(lines) + ("\n" if lines else "")
        temp_path = self.path.with_name(f"{self.path.name}.tmp")
        temp_path.write_text(content, encoding="utf-8-sig")
        temp_path.replace(self.path)

    def add(
        self,
        targets: list[RecordingTarget],
        url: str,
        quality: Quality | None = None,
        name: str = "",
    ) -> list[RecordingTarget]:
        normalized = normalize_url(url)
        if any(target.url == normalized for target in targets):
            return list(targets)
        return [
            *targets,
            RecordingTarget(
                url=normalized,
                quality=quality or self.default_quality,
                display_name=name,
            ),
        ]

    def _parse_line(self, line: str, enabled: bool) -> RecordingTarget:
        parts = [part.strip() for part in line.replace("，", ",").split(",")]
        quality = self.default_quality
        url = ""
        name = ""
        if len(parts) == 1:
            url = parts[0]
        elif parts[0] in QUALITY_BY_VALUE:
            quality = QUALITY_BY_VALUE[parts[0]]
            url = parts[1]
            name = parts[2] if len(parts) > 2 else ""
        elif len(parts) >= 2 and not _looks_like_url(parts[0]) and _looks_like_url(parts[1]):
            url = parts[1]
            name = parts[2] if len(parts) > 2 else ""
        else:
            url = parts[0]
            name = parts[1] if len(parts) > 1 else ""
        return RecordingTarget(url=url, quality=quality, display_name=name, enabled=enabled)

    def _format_target(self, target: RecordingTarget) -> str:
        prefix = "" if target.enabled else "#"
        parts = [target.quality.value, target.url]
        if target.display_name:
            parts.append(target.display_name)
        return prefix + ",".join(parts)
