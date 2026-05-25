from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .schema import KNOWN_FIELD_LABELS, Qihoo360ParsedEvent

MARKER_RE = re.compile(rb"@[0-9A-Za-z]{3}")
TYPE_TEXT = 0x08
TYPE_INTEGER = 0x17
TYPE_SENTINEL = 0x0B
CONTAINER_FLAG = 0x01
LEAF_FLAG = 0x00
FILETIME_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)


@dataclass(frozen=True)
class _ParsedField:
    code: str
    value: Any
    body: bytes


def parse_qihoo360_fc_blob(
    blob: bytes,
    source_row_id: int | None = None,
) -> Qihoo360ParsedEvent:
    warnings: list[str] = []
    fields = _parse_tlv_fields(blob, warnings)
    raw_fields = _raw_fields(fields)
    unknown_fields = _unknown_fields(fields)

    event_time_raw = _field_text(raw_fields, "@206")
    observed_at_utc, time_confidence, time_warning = _parse_event_time(event_time_raw)
    if time_warning:
        warnings.append(time_warning)

    file_size = _field_int(raw_fields, "@501")
    if _field_text(raw_fields, "@501") and file_size is None:
        warnings.append("field_501_file_size_not_integer")

    return Qihoo360ParsedEvent(
        source_row_id=source_row_id,
        event_source=_field_text(raw_fields, "@200"),
        raw_product=_field_text(raw_fields, "@201"),
        threat_name=_field_text(raw_fields, "@203"),
        threat_category=_field_text(raw_fields, "@204"),
        raw_action_text=_field_text(raw_fields, "@205"),
        event_time_raw=event_time_raw,
        observed_at_utc=observed_at_utc,
        time_confidence=time_confidence,
        related_paths=tuple(
            value
            for value in (
                *_field_texts(raw_fields, "@208"),
                *_field_texts(raw_fields, "@209"),
            )
            if value
        ),
        file_path=_field_text(raw_fields, "@500"),
        file_size=file_size,
        quarantine_path=_field_text(raw_fields, "@502"),
        md5=_field_text(raw_fields, "@510").casefold(),
        sha1=_field_text(raw_fields, "@512").casefold(),
        sha256=_field_text(raw_fields, "@513").casefold(),
        raw_category_hint=_field_text(raw_fields, "@514"),
        raw_fields=raw_fields,
        unknown_fields=unknown_fields,
        parse_warnings=tuple(dict.fromkeys(warnings)),
    )


def decode_qihoo360_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip("\x00")
    if not isinstance(value, bytes):
        return str(value)
    if not value:
        return ""

    for encoding in ("utf-8", "utf-16le", "gb18030", "gbk", "big5"):
        try:
            decoded = value.decode(encoding)
        except UnicodeDecodeError:
            continue
        decoded = decoded.rstrip("\x00")
        if _looks_printable(decoded):
            return decoded
    return value.decode("utf-8", errors="replace").rstrip("\x00")


def _parse_tlv_fields(data: bytes, warnings: list[str]) -> list[_ParsedField]:
    fields: list[_ParsedField] = []
    position = 0
    while position < len(data):
        marker = MARKER_RE.search(data, position)
        if marker is None:
            break
        start = marker.start()
        if start + 9 > len(data):
            warnings.append("tlv_header_truncated")
            break
        code = marker.group().decode("ascii", errors="replace")
        flag = data[start + 4]
        length = int.from_bytes(data[start + 5 : start + 9], "little", signed=False)
        body_start = start + 9
        body_end = body_start + length
        if body_end > len(data):
            warnings.append(f"field_{code[1:]}_length_exceeds_blob")
            break
        body = data[body_start:body_end]
        if flag == CONTAINER_FLAG:
            fields.extend(_parse_tlv_fields(body, warnings))
        elif flag == LEAF_FLAG:
            fields.append(
                _ParsedField(code=code, value=_decode_leaf(code, body), body=body)
            )
        else:
            warnings.append(f"field_{code[1:]}_unknown_flag_{flag}")
            fields.append(
                _ParsedField(code=code, value=decode_qihoo360_text(body), body=body)
            )
        position = max(body_end, start + 1)
    return fields


def _decode_leaf(code: str, body: bytes) -> Any:
    if len(body) < 2:
        return ""
    value_type = int.from_bytes(body[:2], "little", signed=False)
    payload = body[2:]
    if value_type == TYPE_TEXT:
        if len(payload) >= 4:
            value_length = int.from_bytes(payload[:4], "little", signed=False)
            return decode_qihoo360_text(payload[4 : 4 + value_length])
        return decode_qihoo360_text(payload)
    if value_type == TYPE_INTEGER:
        if len(payload) >= 4:
            return int.from_bytes(payload[:4], "little", signed=False)
        return None
    if value_type == TYPE_SENTINEL:
        if payload == b"\xff\xff":
            return None
        return payload.hex()
    return decode_qihoo360_text(payload)


def _raw_fields(fields: list[_ParsedField]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for field in fields:
        existing = payload.get(field.code)
        if existing is None and field.code not in payload:
            payload[field.code] = field.value
        elif isinstance(existing, list):
            existing.append(field.value)
        else:
            payload[field.code] = [existing, field.value]
    return payload


def _unknown_fields(fields: list[_ParsedField]) -> dict[str, Mapping[str, str]]:
    unknown: dict[str, Mapping[str, str]] = {}
    for field in fields:
        if field.code in KNOWN_FIELD_LABELS:
            continue
        unknown[field.code] = {
            "decoded_preview": decode_qihoo360_text(field.body)[:120],
            "hex_preview": _hex_preview(field.body),
        }
    return unknown


def _field_text(raw_fields: Mapping[str, Any], code: str) -> str:
    values = _field_texts(raw_fields, code)
    return values[0] if values else ""


def _field_texts(raw_fields: Mapping[str, Any], code: str) -> list[str]:
    value = raw_fields.get(code)
    if isinstance(value, list):
        return [decode_qihoo360_text(item).strip() for item in value]
    if value is None:
        return []
    return [decode_qihoo360_text(value).strip()]


def _field_int(raw_fields: Mapping[str, Any], code: str) -> int | None:
    value = raw_fields.get(code)
    if isinstance(value, list):
        value = value[0] if value else None
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _parse_event_time(raw_value: str) -> tuple[str, str, str]:
    if not raw_value:
        return "", "unknown", ""
    try:
        numeric_value = int(raw_value)
    except ValueError:
        return "", "unknown", "field_206_time_parse_failed"
    if numeric_value <= 0:
        return "", "unknown", "field_206_time_non_positive"
    try:
        if numeric_value > 10_000_000_000_000_000:
            observed = FILETIME_EPOCH + timedelta(microseconds=numeric_value / 10)
            confidence = "low"
            warning = "field_206_filetime_like_low_confidence"
        else:
            observed = datetime.fromtimestamp(numeric_value, timezone.utc)
            confidence = "medium"
            warning = ""
    except (OverflowError, OSError, ValueError):
        return "", "unknown", "field_206_time_parse_failed"
    return observed.isoformat().replace("+00:00", "Z"), confidence, warning


def _hex_preview(value: bytes, max_bytes: int = 64) -> str:
    return " ".join(f"{byte:02x}" for byte in value[:max_bytes])


def _looks_printable(text: str) -> bool:
    if not text:
        return True
    sample = text[:4096]
    printable = sum(
        1 for character in sample if character in "\r\n\t" or character.isprintable()
    )
    return printable / max(len(sample), 1) >= 0.8
