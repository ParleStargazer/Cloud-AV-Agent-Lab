from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_MAX_TEXT_REDACTION_BYTES = 5 * 1024 * 1024
REDACTED = "<redacted>"

HASH_VALUE_RE = re.compile(
    r"\b[a-fA-F0-9]{32}\b|\b[a-fA-F0-9]{40}\b|\b[a-fA-F0-9]{64}\b"
)
BEARER_RE = re.compile(r"(?i)(Authorization\s*:\s*Bearer\s+)[^\r\n\s]+")
COOKIE_RE = re.compile(r"(?i)(Cookie\s*:\s*)[^\r\n]+")
SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|secret|credential|token|secret[_-]?key)"
    r"(\s*[:=]\s*)([^\s,;\"']+)"
)
WINDOWS_USER_DIR_RE = re.compile(r"(?i)\b[A-Z]:[\\/]+Users[\\/]+[^\\/\"'\s]+")

SENSITIVE_KEY_MARKERS = (
    "token",
    "secret",
    "credential",
    "api_key",
    "apikey",
    "access_key",
    "secret_key",
    "private_key",
    "authorization",
    "cookie",
)
SENSITIVE_EXACT_KEYS = {"password"}
SAFE_KEY_EXCEPTIONS = {
    "password_protected",
    "raw_binary_included",
    "source_sha256",
    "archive_sha256",
    "sha256",
    "sha1",
    "md5",
    "sample_sha256",
    "expected_sha256",
    "file_sha256",
}
EVIDENCE_ID_KEYS = {
    "case_id",
    "sample_id",
    "run_id",
    "vm_id",
    "product_id",
    "verdict",
    "confidence",
    "recname",
    "detection_name",
    "event_type",
    "timestamp_utc",
}


@dataclass(frozen=True)
class RedactionContext:
    case_workspace: str = ""
    known_paths: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class RedactionResult:
    content: bytes
    redacted: bool
    encoding: str
    warnings: tuple[str, ...] = ()


class RedactionError(RuntimeError):
    """Raised when a text artifact cannot be safely redacted."""


def redact_text_artifact(
    name: str,
    content: bytes,
    context: RedactionContext | None = None,
    max_bytes: int = DEFAULT_MAX_TEXT_REDACTION_BYTES,
) -> RedactionResult:
    if len(content) > max_bytes:
        raise RedactionError("text_redaction_size_limit_exceeded")

    text, encoding = decode_text_content(content)
    suffix = Path(name).suffix.casefold()
    if suffix == ".json":
        return _redact_json(text, context or RedactionContext(), encoding)
    if suffix == ".jsonl":
        return _redact_jsonl(text, context or RedactionContext(), encoding)
    redacted_text = redact_plain_text(text, context or RedactionContext())
    return RedactionResult(
        content=redacted_text.encode("utf-8"),
        redacted=redacted_text != text,
        encoding=encoding,
    )


def decode_text_content(content: bytes) -> tuple[str, str]:
    if content.startswith((b"\xff\xfe", b"\xfe\xff")):
        return content.decode("utf-16"), "utf-16"
    if content.startswith(b"\xef\xbb\xbf"):
        return content.decode("utf-8-sig"), "utf-8-sig"

    if b"\x00" in content:
        try:
            decoded = content.decode("utf-16le")
        except UnicodeDecodeError as exc:
            raise RedactionError("binary_like_artifact") from exc
        if _looks_like_text(decoded):
            return decoded, "utf-16le"
        raise RedactionError("binary_like_artifact")

    try:
        return content.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass

    try:
        decoded = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise RedactionError("text_decode_failed") from exc
    return decoded, "utf-8-sig"


def redact_plain_text(text: str, context: RedactionContext) -> str:
    redacted = _redact_known_paths(text, context)
    redacted = WINDOWS_USER_DIR_RE.sub("<user_home>", redacted)
    redacted = BEARER_RE.sub(r"\1" + REDACTED, redacted)
    redacted = COOKIE_RE.sub(r"\1" + REDACTED, redacted)
    redacted = SECRET_ASSIGNMENT_RE.sub(
        lambda match: f"{match.group(1)}{match.group(2)}{REDACTED}",
        redacted,
    )
    return redacted


def _redact_json(
    text: str,
    context: RedactionContext,
    encoding: str,
) -> RedactionResult:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        redacted_text = redact_plain_text(text, context)
        return RedactionResult(
            content=redacted_text.encode("utf-8"),
            redacted=redacted_text != text,
            encoding=encoding,
            warnings=("json_parse_failed_fallback_text_redaction",),
        )
    redacted_payload = redact_json_value(payload, context)
    redacted_text = json.dumps(redacted_payload, ensure_ascii=False, indent=2)
    return RedactionResult(
        content=redacted_text.encode("utf-8"),
        redacted=redacted_payload != payload,
        encoding=encoding,
    )


def _redact_jsonl(
    text: str,
    context: RedactionContext,
    encoding: str,
) -> RedactionResult:
    lines = text.splitlines()
    output_lines: list[str] = []
    warnings: list[str] = []
    changed = False
    for line in lines:
        if not line.strip():
            output_lines.append(line)
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            redacted_line = redact_plain_text(line, context)
            output_lines.append(redacted_line)
            changed = changed or redacted_line != line
            warnings.append("jsonl_line_parse_failed_fallback_text_redaction")
            continue
        redacted_payload = redact_json_value(payload, context)
        output_line = json.dumps(
            redacted_payload, ensure_ascii=False, separators=(",", ":")
        )
        output_lines.append(output_line)
        changed = changed or redacted_payload != payload
    return RedactionResult(
        content=(
            "\n".join(output_lines) + ("\n" if text.endswith("\n") else "")
        ).encode("utf-8"),
        redacted=changed,
        encoding=encoding,
        warnings=tuple(dict.fromkeys(warnings)),
    )


def redact_json_value(value: Any, context: RedactionContext, key: str = "") -> Any:
    if isinstance(value, Mapping):
        return {
            str(item_key): redact_json_value(item_value, context, key=str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, list):
        return [redact_json_value(item, context, key=key) for item in value]
    if _is_sensitive_key(key):
        return REDACTED
    if isinstance(value, str):
        if (
            key.casefold() in EVIDENCE_ID_KEYS
            or _is_hash_key(key)
            or HASH_VALUE_RE.fullmatch(value)
        ):
            return value
        return redact_plain_text(value, context)
    return value


def _is_sensitive_key(key: str) -> bool:
    lowered = key.casefold()
    if lowered in SAFE_KEY_EXCEPTIONS or _is_hash_key(lowered):
        return False
    if lowered in SENSITIVE_EXACT_KEYS:
        return True
    return any(marker in lowered for marker in SENSITIVE_KEY_MARKERS)


def _is_hash_key(key: str) -> bool:
    lowered = key.casefold()
    return lowered in {"md5", "sha1", "sha256"} or lowered.endswith("_sha256")


def _redact_known_paths(text: str, context: RedactionContext) -> str:
    replacements: list[tuple[str, str]] = []
    if context.case_workspace:
        replacements.append((context.case_workspace, "<case_workspace>"))
    replacements.extend(
        (path, placeholder)
        for path, placeholder in context.known_paths.items()
        if path and placeholder
    )
    redacted = text
    for path, placeholder in _dedupe_replacements(replacements):
        redacted = _replace_path_case_insensitive(redacted, path, placeholder)
        redacted = _replace_path_case_insensitive(
            redacted,
            path.replace("\\", "/"),
            placeholder,
        )
        redacted = _replace_path_case_insensitive(
            redacted,
            path.replace("/", "\\"),
            placeholder,
        )
    return redacted


def _dedupe_replacements(
    replacements: Sequence[tuple[str, str]],
) -> list[tuple[str, str]]:
    seen: set[str] = set()
    ordered: list[tuple[str, str]] = []
    for path, placeholder in sorted(
        replacements, key=lambda item: len(item[0]), reverse=True
    ):
        normalized = path.casefold().replace("/", "\\")
        if normalized in seen:
            continue
        seen.add(normalized)
        ordered.append((path, placeholder))
    return ordered


def _replace_path_case_insensitive(text: str, path: str, placeholder: str) -> str:
    if not path:
        return text
    return re.sub(re.escape(path), placeholder, text, flags=re.IGNORECASE)


def _looks_like_text(decoded: str) -> bool:
    if not decoded:
        return True
    sample = decoded[:4096]
    printable = sum(
        1 for character in sample if character in "\r\n\t" or character.isprintable()
    )
    asciiish = sum(
        1 for character in sample if character in "\r\n\t" or " " <= character <= "~"
    )
    return (
        printable / max(len(sample), 1) >= 0.85
        and asciiish / max(len(sample), 1) >= 0.6
    )
