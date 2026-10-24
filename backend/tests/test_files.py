"""File actions: path check, tidy-up with undo, quarantine, recycle bin."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from sentinel.automation import files
from sentinel.permissions import ActionDenied, Source
from sentinel.services import Services


def make_file(folder: Path, name: str, age_s: int = 3600) -> Path:
    path = folder / name
    path.write_text("x", encoding="utf-8")
    past = time.time() - age_s
    os.utime(path, (past, past))
    return path


def test_resolve_inside_rejects_outside_paths(tmp_path: Path, managed: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("x")
    inside = make_file(managed, "a.txt")
    assert files.resolve_inside(str(inside), [managed]) == inside.resolve()
    with pytest.raises(ActionDenied):
        files.resolve_inside(str(outside), [managed])
    with pytest.raises(ActionDenied):
        files.resolve_inside(str(managed / ".." / "outside.txt"), [managed])
    with pytest.raises(ActionDenied):
        files.resolve_inside("a.txt", [managed])
    with pytest.raises(ActionDenied):
        files.resolve_inside(str(managed / "missing.txt"), [managed])


def test_organize_and_undo(svc: Services, managed: Path) -> None:
    make_file(managed, "photo.jpg")
    make_file(managed, "invoice.pdf")
    make_file(managed, "setup.exe")
    make_file(managed, "new.pdf", age_s=0)  # too fresh
    make_file(managed, "loading.crdownload")

    outcome = svc.engine.request(
        "organize_folder", {"folder": str(managed)}, source=Source.ASSISTANT, actor="jarvis"
    )
    assert outcome.status == "executed", outcome.message
    assert (managed / "Images" / "photo.jpg").exists()
    assert (managed / "Documents" / "invoice.pdf").exists()
    assert (managed / "Installers" / "setup.exe").exists()
    assert (managed / "new.pdf").exists()
    assert (managed / "loading.crdownload").exists()

    assert svc.engine.undo(outcome.undo_id).status == "executed"
    assert (managed / "photo.jpg").exists()
    assert not (managed / "Images").exists()


def test_organize_outside_managed_is_denied(svc: Services, tmp_path: Path) -> None:
    other = tmp_path / "other"
    other.mkdir()
    outcome = svc.engine.request(
        "organize_folder", {"folder": str(other)}, source=Source.USER, actor="user"
    )
    assert outcome.status == "denied"


def test_quarantine_and_undo(svc: Services, managed: Path) -> None:
    bad = make_file(managed, "invoice.pdf.exe")
    outcome = svc.engine.request(
        "quarantine_file", {"path": str(bad)}, source=Source.ASSISTANT, actor="jarvis"
    )
    assert outcome.status == "executed", outcome.message
    assert not bad.exists()
    stored = list(svc.config.quarantine_dir.glob("*.quarantined"))
    assert len(stored) == 1
    assert svc.engine.undo(outcome.undo_id).status == "executed"
    assert bad.exists()


def test_restore_quarantine_via_user(svc: Services, managed: Path) -> None:
    bad = make_file(managed, "x.scr")
    svc.engine.request("quarantine_file", {"path": str(bad)}, source=Source.USER, actor="user")
    entry = svc.db.query_one("SELECT id FROM quarantine")
    outcome = svc.engine.request(
        "restore_quarantine", {"id": entry["id"]}, source=Source.USER, actor="user"
    )
    assert outcome.status == "executed"
    assert bad.exists()


def test_recycle_always_needs_confirmation(svc: Services, managed: Path) -> None:
    target = make_file(managed, "alt.txt")
    svc.engine.set_autonomy(3, actor="user")
    for source in (Source.USER, Source.ASSISTANT):
        outcome = svc.engine.request(
            "recycle_file", {"path": str(target)}, source=source, actor="x"
        )
        assert outcome.status == "pending"
    assert target.exists()


def test_sentinel_data_dir_is_protected(svc: Services, tmp_path: Path) -> None:
    svc.config.managed_folders.append(svc.config.data_dir)
    outcome = svc.engine.request(
        "quarantine_file", {"path": str(svc.config.db_path)}, source=Source.USER, actor="user"
    )
    assert outcome.status == "denied"
