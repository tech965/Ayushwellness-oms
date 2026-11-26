"""`OMS_ENV_FILE`: a staging run must read only its own file, never `.env`.
Each case imports the settings in a fresh process, in a scratch directory.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

API_DIR = Path(__file__).resolve().parents[1]

DEFAULT_ENV = (
    "DATABASE_URL=postgresql+asyncpg://u:p@default-env-host/db\n"
    "SHOPIFY_ACCESS_TOKEN=token-from-default-env\n"
    "WHATSAPP_ACCESS_TOKEN=whatsapp-from-default-env\n"
)
PROBE = (
    "from app.core.config import settings;"
    "print(settings.DATABASE_URL);"
    "print(settings.SHOPIFY_ACCESS_TOKEN);"
    "print(settings.WHATSAPP_ACCESS_TOKEN)"
)
DROP = ("DATABASE_URL", "SHOPIFY_ACCESS_TOKEN", "WHATSAPP_ACCESS_TOKEN", "OMS_ENV_FILE")


def _run(directory: Path, **extra: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k not in DROP}
    env.update(PYTHONPATH=str(API_DIR), **extra)
    return subprocess.run(
        [sys.executable, "-c", PROBE],
        cwd=directory,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_dot_env_is_still_the_default(tmp_path):
    (tmp_path / ".env").write_text(DEFAULT_ENV)
    out = _run(tmp_path)
    assert out.returncode == 0, out.stderr
    assert "default-env-host" in out.stdout and "token-from-default-env" in out.stdout


def test_a_chosen_file_is_the_only_one_read(tmp_path):
    (tmp_path / ".env").write_text(DEFAULT_ENV)
    (tmp_path / ".env.staging.local").write_text(
        "DATABASE_URL=postgresql+asyncpg://u:p@staging-host/db\n"
    )
    out = _run(tmp_path, OMS_ENV_FILE=".env.staging.local")
    assert out.returncode == 0, out.stderr
    assert "staging-host" in out.stdout
    # Nothing leaks from .env: no live third-party credential, no other database.
    assert "default-env" not in out.stdout
    assert out.stdout.split()[1:] == ["None", "None"]


def test_a_missing_chosen_file_stops_startup_instead_of_using_dot_env(tmp_path):
    (tmp_path / ".env").write_text(DEFAULT_ENV)
    for value in ("typo.env", ""):
        out = _run(tmp_path, OMS_ENV_FILE=value)
        assert out.returncode != 0, value
        assert "Refusing to start" in out.stderr
        assert "default-env" not in out.stdout
