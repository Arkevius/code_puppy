"""Tests for the one-shot workspace-backfill migration.

Mirrors the structure of test_session_migration.py. Monkeypatches
config.AUTOSAVE_DIR to a tmp_path so the real sessions dir is never touched.
"""

from __future__ import annotations

import json
import pickle
import time
import types
from pathlib import Path

import pytest


def _make_history(text: str = "hi there") -> list:
    """Build a minimal duck-typed history that extract_session_title can parse."""
    part = types.SimpleNamespace(content=text, part_kind="user-prompt")
    msg = types.SimpleNamespace(kind="request", parts=[part])
    return [msg]


@pytest.fixture
def backfill_dir(tmp_path: Path, monkeypatch):
    """Wire AUTOSAVE_DIR to a clean tmp_path."""
    autosaves = tmp_path / "autosaves"
    autosaves.mkdir()
    monkeypatch.setattr("code_puppy.config.AUTOSAVE_DIR", str(autosaves))
    return autosaves


def _write_session(directory: Path, stem: str, history=None, meta_extra=None):
    """Write a .pkl + _meta.json pair under directory."""
    if history is None:
        history = _make_history()
    pkl = directory / f"{stem}.pkl"
    meta = directory / f"{stem}_meta.json"
    pkl.write_bytes(pickle.dumps(history))
    base_meta = {"session_name": stem, "timestamp": "2024-01-01T00:00:00"}
    if meta_extra:
        base_meta.update(meta_extra)
    meta.write_text(json.dumps(base_meta), encoding="utf-8")
    return pkl, meta


class TestBackfillSessionMetadata:
    def test_backfills_title_into_legacy_meta(self, backfill_dir):
        """Legacy meta (no title) gets title populated and workspace set to empty string."""
        from code_puppy.session_backfill import backfill_session_metadata

        history = _make_history("hello world")
        pkl, meta = _write_session(backfill_dir, "sess1", history)

        backfill_session_metadata()

        result = json.loads(meta.read_text())
        assert result["title"]  # non-empty
        assert result["workspace"] == ""

    def test_skips_meta_with_existing_title(self, backfill_dir):
        """Meta that already has a non-empty title is left unchanged."""
        from code_puppy.session_backfill import backfill_session_metadata

        pkl, meta = _write_session(
            backfill_dir, "sess2", meta_extra={"title": "My Existing Title"}
        )
        original_meta = meta.read_text()

        backfill_session_metadata()

        assert meta.read_text() == original_meta

    def test_idempotent_second_call_is_noop(self, backfill_dir):
        """Second call returns immediately via sentinel; files unchanged."""
        from code_puppy.session_backfill import backfill_session_metadata

        pkl, meta = _write_session(backfill_dir, "sess3")

        backfill_session_metadata()
        after_first = meta.read_text()

        # Modify meta to simulate something that would be re-processed
        # if the sentinel check did not work.
        data = json.loads(after_first)
        data["title"] = ""
        meta.write_text(json.dumps(data))

        backfill_session_metadata()

        # Should still be empty because sentinel prevented re-run.
        result = json.loads(meta.read_text())
        assert result["title"] == ""

    def test_pkl_mtime_preserved_after_backfill(self, backfill_dir):
        """The .pkl file mtime must not change -- cleanup_sessions uses it for pruning."""
        from code_puppy.session_backfill import backfill_session_metadata

        pkl, meta = _write_session(backfill_dir, "sess4")
        mtime_before = pkl.stat().st_mtime

        time.sleep(0.05)

        backfill_session_metadata()

        mtime_after = pkl.stat().st_mtime
        assert mtime_before == mtime_after

    def test_corrupted_pickle_does_not_crash(self, backfill_dir):
        """Bad pickle is counted as failure; sentinel still written; others processed."""
        from code_puppy.session_backfill import backfill_session_metadata

        # Good session
        pkl_good, meta_good = _write_session(backfill_dir, "good_sess")
        # Bad session -- write garbage bytes so pickle.loads fails
        bad_pkl = backfill_dir / "bad_sess.pkl"
        bad_pkl.write_bytes(b"not a pickle")
        bad_meta = backfill_dir / "bad_sess_meta.json"
        bad_meta.write_text(json.dumps({"session_name": "bad_sess"}))

        # Must not raise
        backfill_session_metadata()

        # Sentinel written
        assert (backfill_dir / ".workspace_backfill_done").exists()
        # Good session got its title
        good_result = json.loads(meta_good.read_text())
        assert good_result.get("title")

    def test_missing_sidecar_pickle_skipped(self, backfill_dir):
        """A .pkl without a _meta.json sidecar is silently skipped."""
        from code_puppy.session_backfill import backfill_session_metadata

        pkl = backfill_dir / "orphan.pkl"
        pkl.write_bytes(pickle.dumps(_make_history()))
        # No _meta.json written

        # Must not raise
        backfill_session_metadata()

        # No sidecar created
        assert not (backfill_dir / "orphan_meta.json").exists()
        # Sentinel still written
        assert (backfill_dir / ".workspace_backfill_done").exists()
