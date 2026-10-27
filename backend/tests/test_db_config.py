from pathlib import Path

import pytest

from app.db import configured_database_url, connect_args_for_url


def test_managed_database_url_is_used_when_secret_mount_is_unavailable(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PASSWORD_FILE", str(tmp_path / "missing-secret"))
    monkeypatch.setenv("DATABASE_URL", "postgresql://managed:secret@db/orchestra")

    assert configured_database_url() == "postgresql+psycopg://managed:secret@db/orchestra"


def test_managed_database_url_uses_psycopg3_without_secret_file(monkeypatch):
    monkeypatch.delenv("DATABASE_PASSWORD_FILE", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgres://managed:secret@db/orchestra")

    assert configured_database_url() == "postgresql+psycopg://managed:secret@db/orchestra"


def test_postgres_connections_have_a_bounded_connect_timeout():
    assert connect_args_for_url("postgresql+psycopg://managed:secret@db/orchestra") == {
        "connect_timeout": 15
    }


def test_sqlite_connections_keep_thread_compatibility():
    assert connect_args_for_url("sqlite:///./orchestra.db") == {"check_same_thread": False}


def test_missing_secret_mount_without_database_url_fails(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PASSWORD_FILE", str(tmp_path / "missing-secret"))
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(RuntimeError, match="secret file is missing"):
        configured_database_url()


def test_mounted_secret_still_builds_compose_database_url(monkeypatch, tmp_path):
    password_file = Path(tmp_path / "db_password")
    password_file.write_text("compose-secret\n", encoding="utf-8")
    monkeypatch.setenv("DATABASE_PASSWORD_FILE", str(password_file))
    monkeypatch.delenv("DATABASE_URL", raising=False)

    assert configured_database_url() == (
        "postgresql+psycopg://orchestra:compose-secret@postgres:5432/orchestra"
    )
