#!/usr/bin/env python3
"""Stdlib-only local secret vault for Hermes secret intake.

The vault stores values on disk under path-safe name/field components. Public
metadata APIs intentionally report only references and file metadata, never raw
secret values.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence
from urllib.parse import urlparse


DEFAULT_ROOT = Path("/var/lib/docker/volumes/hermes-data/_data/.secrets")
ENV_ROOT = "HERMES_SECRET_VAULT_DIR"
PRIVATE_DIR_MODE = 0o700
PRIVATE_FILE_MODE = 0o600
COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class SecretVaultError(ValueError):
    """Base error for invalid vault input."""


@dataclass(frozen=True)
class SecretRef:
    name: str
    field: str

    @property
    def ref(self) -> str:
        return f"secret://{self.name}/{self.field}"


def vault_root(root: Path | str | None = None) -> Path:
    """Return the configured vault root."""
    if root is not None:
        return Path(root)
    return Path(os.environ.get(ENV_ROOT, str(DEFAULT_ROOT)))


def _validate_component(kind: str, value: str) -> str:
    if not isinstance(value, str) or not value:
        raise SecretVaultError(f"{kind} must be a non-empty string")
    if ".." in value or "/" in value or "\\" in value:
        raise SecretVaultError(f"{kind} must be a path-safe component")
    if value in {".", ".."} or not COMPONENT_RE.fullmatch(value):
        raise SecretVaultError(f"{kind} must match {COMPONENT_RE.pattern}")
    return value


def _ensure_private_dir(path: Path) -> None:
    path.mkdir(mode=PRIVATE_DIR_MODE, parents=True, exist_ok=True)
    os.chmod(path, PRIVATE_DIR_MODE)


def _secret_path(root: Path | str | None, name: str, field: str, *, create: bool = False) -> Path:
    safe_name = _validate_component("name", name)
    safe_field = _validate_component("field", field)
    base = vault_root(root)
    if create:
        _ensure_private_dir(base)
        _ensure_private_dir(base / safe_name)
    full_path = (base / safe_name / safe_field).resolve(strict=False)
    base_resolved = base.resolve(strict=False)
    try:
        full_path.relative_to(base_resolved)
    except ValueError as exc:
        raise SecretVaultError("secret path escapes vault root") from exc
    return full_path


def parse_secret_ref(ref: str) -> SecretRef:
    """Parse secret://NAME/FIELD into a validated SecretRef."""
    parsed = urlparse(ref)
    if parsed.scheme != "secret" or not parsed.netloc or parsed.params or parsed.query or parsed.fragment:
        raise SecretVaultError("secret reference must be secret://NAME/FIELD")
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 1:
        raise SecretVaultError("secret reference must be secret://NAME/FIELD")
    return SecretRef(
        name=_validate_component("name", parsed.netloc),
        field=_validate_component("field", parts[0]),
    )


def store_secret(name: str, field: str, value: str, *, root: Path | str | None = None) -> str:
    """Store a secret value and return secret://NAME/FIELD."""
    path = _secret_path(root, name, field, create=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, PRIVATE_FILE_MODE)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(value)
    finally:
        os.chmod(path, PRIVATE_FILE_MODE)
    return SecretRef(name=name, field=field).ref


def get_secret(ref: str, *, root: Path | str | None = None) -> str:
    """Return the raw secret value for a secret:// reference."""
    parsed = parse_secret_ref(ref)
    path = _secret_path(root, parsed.name, parsed.field)
    return path.read_text(encoding="utf-8")


def metadata(ref: str, *, root: Path | str | None = None) -> dict[str, object]:
    """Return metadata for a secret reference without exposing its value."""
    parsed = parse_secret_ref(ref)
    path = _secret_path(root, parsed.name, parsed.field)
    stat = path.stat()
    return {
        "ref": parsed.ref,
        "name": parsed.name,
        "field": parsed.field,
        "size_bytes": stat.st_size,
        "mode": oct(stat.st_mode & 0o777),
        "modified_at": int(stat.st_mtime),
    }


def list_secrets(*, root: Path | str | None = None) -> list[dict[str, object]]:
    """List stored secret fields as metadata only."""
    base = vault_root(root)
    if not base.exists():
        return []
    entries: list[dict[str, object]] = []
    for name_dir in sorted(path for path in base.iterdir() if path.is_dir()):
        try:
            name = _validate_component("name", name_dir.name)
        except SecretVaultError:
            continue
        for field_path in sorted(path for path in name_dir.iterdir() if path.is_file()):
            try:
                field = _validate_component("field", field_path.name)
            except SecretVaultError:
                continue
            stat = field_path.stat()
            ref = SecretRef(name=name, field=field).ref
            entries.append(
                {
                    "ref": ref,
                    "name": name,
                    "field": field,
                    "size_bytes": stat.st_size,
                    "mode": oct(stat.st_mode & 0o777),
                    "modified_at": int(stat.st_mtime),
                }
            )
    return entries


def _read_value_from_args(args: argparse.Namespace) -> str:
    if args.stdin:
        return sys.stdin.read()
    if args.value_file:
        return Path(args.value_file).read_text(encoding="utf-8")
    raise SecretVaultError("set requires --stdin or --value-file")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Hermes local secret vault.")
    parser.add_argument("--root", help=f"Vault root. Defaults to ${ENV_ROOT} or {DEFAULT_ROOT}.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    set_parser = subparsers.add_parser("set", help="Store a secret value.")
    set_parser.add_argument("name")
    set_parser.add_argument("field")
    value_source = set_parser.add_mutually_exclusive_group(required=True)
    value_source.add_argument("--stdin", action="store_true", help="Read the secret value from stdin.")
    value_source.add_argument("--value-file", help="Read the secret value from a file.")

    get_parser = subparsers.add_parser("get", help="Read a secret value.")
    get_parser.add_argument("ref")
    get_parser.add_argument(
        "--unsafe-print-value",
        action="store_true",
        help="Required to print the raw secret value to stdout.",
    )

    subparsers.add_parser("list", help="List secret metadata.")

    metadata_parser = subparsers.add_parser("metadata", help="Show metadata for a secret reference.")
    metadata_parser.add_argument("ref")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "set":
            ref = store_secret(args.name, args.field, _read_value_from_args(args), root=args.root)
            print(json.dumps({"ref": ref}, sort_keys=True))
            return 0
        if args.command == "get":
            if not args.unsafe_print_value:
                print("Refusing to print secret value without --unsafe-print-value.", file=sys.stderr)
                return 2
            sys.stdout.write(get_secret(args.ref, root=args.root))
            return 0
        if args.command == "list":
            print(json.dumps({"secrets": list_secrets(root=args.root)}, sort_keys=True, indent=2))
            return 0
        if args.command == "metadata":
            print(json.dumps(metadata(args.ref, root=args.root), sort_keys=True, indent=2))
            return 0
    except (OSError, SecretVaultError) as exc:
        print(f"secret_vault: {exc}", file=sys.stderr)
        return 1
    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
