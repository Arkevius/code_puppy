from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Callable, List

import pytest

from code_puppy.session_storage import (
    cleanup_sessions,
    list_sessions,
    load_session,
    save_session,
)


@pytest.fixture()
def history() -> List[str]:
    return ["one", "two", "three"]


@pytest.fixture()
def token_estimator() -> Callable[[object], int]:
    return lambda message: len(str(message))


def test_save_and_load_session(tmp_path: Path, history: List[str], token_estimator):
    session_name = "demo_session"
    timestamp = "2024-01-01T00:00:00"
    metadata = save_session(
        history=history,
        session_name=session_name,
        base_dir=tmp_path,
        timestamp=timestamp,
        token_estimator=token_estimator,
    )

    assert metadata.session_name == session_name
    assert metadata.message_count == len(history)
    assert metadata.total_tokens == sum(token_estimator(m) for m in history)
    assert metadata.pickle_path.exists()
    assert metadata.metadata_path.exists()

    with metadata.metadata_path.open() as meta_file:
        stored = json.load(meta_file)
    assert stored["session_name"] == session_name
    assert stored["auto_saved"] is False

    loaded_history = load_session(session_name, tmp_path)
    assert loaded_history == history


def test_list_sessions(tmp_path: Path, history: List[str], token_estimator):
    names = ["beta", "alpha", "gamma"]
    for name in names:
        save_session(
            history=history,
            session_name=name,
            base_dir=tmp_path,
            timestamp="2024-01-01T00:00:00",
            token_estimator=token_estimator,
        )

    assert list_sessions(tmp_path) == sorted(names)


def test_cleanup_sessions(tmp_path: Path, history: List[str], token_estimator):
    session_names = ["session_earliest", "session_middle", "session_latest"]
    for index, name in enumerate(session_names):
        metadata = save_session(
            history=history,
            session_name=name,
            base_dir=tmp_path,
            timestamp="2024-01-01T00:00:00",
            token_estimator=token_estimator,
        )
        os.utime(metadata.pickle_path, (0, index))

    removed = cleanup_sessions(tmp_path, 2)
    assert removed == ["session_earliest"]
    remaining = list_sessions(tmp_path)
    assert sorted(remaining) == sorted(["session_middle", "session_latest"])


# --- workspace + auto-title metadata (PUP session-grouping) ---

from types import SimpleNamespace as _NS  # noqa: E402

from code_puppy.session_storage import extract_session_title  # noqa: E402


def _part(content, kind="user-prompt"):
    return _NS(content=content, part_kind=kind)


def _req(*parts):
    return _NS(kind="request", parts=list(parts))


def _resp(*parts):
    return _NS(kind="response", parts=list(parts))


def test_extract_title_uses_first_user_message_and_skips_system():
    hist = [
        _req(_part("You are a puppy.", "system-prompt")),
        _req(_part("Fix the flaky test in auth")),
        _resp(_part("sure", "text")),
    ]
    assert extract_session_title(hist) == "Fix the flaky test in auth"


def test_extract_title_skips_tool_return_only_requests():
    hist = [
        _req(_part("You are a puppy.", "system-prompt")),
        _req(_part("tool output", "tool-return")),
        _req(_part("The real question")),
    ]
    assert extract_session_title(hist) == "The real question"


def test_extract_title_empty_and_truncated():
    assert extract_session_title([]) == ""
    long = "word " * 40
    title = extract_session_title([_req(_part(long))])
    assert len(title) <= 60
    assert title.endswith("…")


def test_save_session_persists_workspace_and_title(tmp_path: Path, token_estimator):
    hist = [
        _req(_part("You are a puppy.", "system-prompt")),
        _req(_part("Investigate startup performance")),
    ]
    meta = save_session(
        history=hist,
        session_name="s1",
        base_dir=tmp_path,
        timestamp="2024-01-01T00:00:00",
        token_estimator=token_estimator,
    )
    assert meta.title == "Investigate startup performance"
    assert meta.workspace  # realpath(cwd) at import time
    stored = json.loads(meta.metadata_path.read_text())
    assert stored["title"] == "Investigate startup performance"
    assert "workspace" in stored


def test_save_session_keeps_workspace_and_title_sticky(tmp_path: Path, token_estimator):
    first = [_req(_part("Original prompt that becomes the title"))]
    m1 = save_session(
        history=first,
        session_name="s1",
        base_dir=tmp_path,
        timestamp="2024-01-01T00:00:00",
        token_estimator=token_estimator,
    )
    # Re-save with a different first message and a different (mocked) cwd:
    second = [_req(_part("A totally different first prompt"))]
    m2 = save_session(
        history=second,
        session_name="s1",
        base_dir=tmp_path,
        timestamp="2024-01-02T00:00:00",
        token_estimator=token_estimator,
    )
    assert m2.title == m1.title == "Original prompt that becomes the title"
    assert m2.workspace == m1.workspace


def test_empty_history_gets_empty_title_then_fills_on_next_save(
    tmp_path: Path, token_estimator
):
    m1 = save_session(
        history=[],
        session_name="s1",
        base_dir=tmp_path,
        timestamp="2024-01-01T00:00:00",
        token_estimator=token_estimator,
    )
    assert m1.title == ""
    m2 = save_session(
        history=[_req(_part("First real prompt"))],
        session_name="s1",
        base_dir=tmp_path,
        timestamp="2024-01-02T00:00:00",
        token_estimator=token_estimator,
    )
    assert m2.title == "First real prompt"
