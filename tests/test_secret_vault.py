from __future__ import annotations

import json
import stat
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import secret_vault  # noqa: E402


def run_cli(*args: str, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "secret_vault.py"), *args],
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_store_get_and_permissions(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    ref = secret_vault.store_secret("telegram", "bot-token", "super-secret-value", root=root)

    assert ref == "secret://telegram/bot-token"
    assert secret_vault.get_secret(ref, root=root) == "super-secret-value"
    assert mode(root) == 0o700
    assert mode(root / "telegram") == 0o700
    assert mode(root / "telegram" / "bot-token") == 0o600


def test_default_root_uses_environment(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "from-env"
    monkeypatch.setenv(secret_vault.ENV_ROOT, str(root))

    ref = secret_vault.store_secret("telegram", "chat-id", "12345")

    assert ref == "secret://telegram/chat-id"
    assert (root / "telegram" / "chat-id").read_text(encoding="utf-8") == "12345"


def test_rejects_traversal_and_unsafe_components(tmp_path: Path) -> None:
    root = tmp_path / "vault"

    for name, field in [
        ("../outside", "token"),
        ("telegram", "../token"),
        ("telegram/ops", "token"),
        ("telegram", "token/value"),
        ("telegram", "..token"),
    ]:
        try:
            secret_vault.store_secret(name, field, "value", root=root)
        except secret_vault.SecretVaultError:
            pass
        else:
            raise AssertionError(f"accepted unsafe component: {name!r} {field!r}")

    for ref in [
        "secret://../token",
        "secret://telegram/../token",
        "secret://telegram/token/extra",
        "file://telegram/token",
    ]:
        try:
            secret_vault.parse_secret_ref(ref)
        except secret_vault.SecretVaultError:
            pass
        else:
            raise AssertionError(f"accepted unsafe ref: {ref!r}")


def test_metadata_and_list_do_not_expose_values(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    secret = "never-print-this-secret"
    ref = secret_vault.store_secret("telegram", "bot-token", secret, root=root)

    metadata = secret_vault.metadata(ref, root=root)
    listed = secret_vault.list_secrets(root=root)
    payload = json.dumps({"metadata": metadata, "list": listed}, sort_keys=True)

    assert secret not in payload
    assert metadata["ref"] == ref
    assert listed == [metadata]


def test_cli_list_and_metadata_do_not_expose_secret(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    secret = "stdout-must-not-leak-this"
    ref = secret_vault.store_secret("telegram", "bot-token", secret, root=root)

    list_result = run_cli("--root", str(root), "list")
    metadata_result = run_cli("--root", str(root), "metadata", ref)

    assert list_result.returncode == 0, list_result.stderr
    assert metadata_result.returncode == 0, metadata_result.stderr
    assert secret not in list_result.stdout
    assert secret not in metadata_result.stdout
    assert ref in list_result.stdout
    assert ref in metadata_result.stdout


def test_cli_get_requires_unsafe_print_value(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    secret = "only-print-when-explicitly-unsafe"
    ref = secret_vault.store_secret("telegram", "bot-token", secret, root=root)

    safe_result = run_cli("--root", str(root), "get", ref)
    unsafe_result = run_cli("--root", str(root), "get", ref, "--unsafe-print-value")

    assert safe_result.returncode == 2
    assert secret not in safe_result.stdout
    assert secret not in safe_result.stderr
    assert unsafe_result.returncode == 0, unsafe_result.stderr
    assert unsafe_result.stdout == secret


def test_cli_set_from_stdin_and_value_file(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    value_file = tmp_path / "value.txt"
    value_file.write_text("file-secret", encoding="utf-8")

    stdin_result = run_cli("--root", str(root), "set", "telegram", "from-stdin", "--stdin", input_text="stdin-secret")
    file_result = run_cli("--root", str(root), "set", "telegram", "from-file", "--value-file", str(value_file))

    assert stdin_result.returncode == 0, stdin_result.stderr
    assert file_result.returncode == 0, file_result.stderr
    assert json.loads(stdin_result.stdout)["ref"] == "secret://telegram/from-stdin"
    assert json.loads(file_result.stdout)["ref"] == "secret://telegram/from-file"
    assert secret_vault.get_secret("secret://telegram/from-stdin", root=root) == "stdin-secret"
    assert secret_vault.get_secret("secret://telegram/from-file", root=root) == "file-secret"
