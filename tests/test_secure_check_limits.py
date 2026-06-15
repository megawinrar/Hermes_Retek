from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import secure_check_limits  # noqa: E402


def test_secure_check_limits_moves_literals_to_private_env(tmp_path: Path) -> None:
    script = tmp_path / "check_limits.sh"
    env = tmp_path / ".check_limits.env"
    token = "123456789:" + "T" * 35
    key = "sk_" + "K" * 35
    script.write_text(
        "\n".join(
            [
                "#!/bin/bash",
                f'TELEGRAM_BOT_TOKEN="{token}"',
                'TELEGRAM_CHAT_ID="245167740"',
                f'API_KEY="{key}"',
                'echo "$API_KEY"',
            ]
        )
        + "\n"
    )

    changed, backup = secure_check_limits.secure_check_limits(script, env)

    assert changed is True
    assert backup is not None and backup.exists()
    updated = script.read_text()
    assert token not in updated
    assert key not in updated
    assert "BOTHUB_API_KEY" in updated
    env_text = env.read_text()
    assert token in env_text
    assert key in env_text
    assert oct(env.stat().st_mode & 0o777) == "0o600"
    assert oct(script.stat().st_mode & 0o777) == "0o755"

    changed_again, backup_again = secure_check_limits.secure_check_limits(script, env)
    assert changed_again is False
    assert backup_again is None
