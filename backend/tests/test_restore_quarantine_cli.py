"""Restore quarantine command preconditions; no database is contacted."""

import pytest

from polymath import restore_quarantine


def test_confirmation_flag_is_required(monkeypatch):
    monkeypatch.setattr(
        restore_quarantine, "build_engine", lambda settings: pytest.fail("connected")
    )
    with pytest.raises(SystemExit) as caught:
        restore_quarantine.main([])
    assert caught.value.code == 2


def test_enabled_execution_gate_refuses_before_connecting(monkeypatch, capsys):
    monkeypatch.setenv("LITERATURE_INGESTION_ENABLED", "true")
    monkeypatch.setattr(
        restore_quarantine, "build_engine", lambda settings: pytest.fail("connected")
    )
    assert restore_quarantine.main(["--confirm-restored-database"]) == 2
    assert "literature_ingestion_enabled" in capsys.readouterr().out


def test_unmigrated_restore_is_refused_with_guidance(monkeypatch, capsys):
    disposed = []

    class Engine:
        def dispose(self):
            disposed.append(True)

    def refuse(engine, heads):
        raise restore_quarantine.QuarantineRefused("schema_not_at_head")

    monkeypatch.setattr(restore_quarantine, "build_engine", lambda settings: Engine())
    monkeypatch.setattr(restore_quarantine, "quarantine", refuse)
    assert restore_quarantine.main(["--confirm-restored-database"]) == 2
    assert "Run migrations on the restored copy first" in capsys.readouterr().out
    assert disposed == [True]
