from __future__ import annotations


def prompt_default(label: str, current: str = "", default: str = "") -> str:
    if current:
        return current
    suffix = f" [{default}]" if default else ""
    value = input(f"{label}{suffix}: ").strip()
    if value:
        return value
    return default


def prompt_bool(label: str, current: bool | None = None, default: bool = False) -> bool:
    if current is not None:
        return current
    default_text = "y" if default else "n"
    value = input(f"{label} [y/N]" if not default else f"{label} [Y/n]").strip()
    if not value:
        value = default_text
    return value.casefold() in {"y", "yes", "true", "1"}
