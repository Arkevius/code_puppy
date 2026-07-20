"""One-shot backfill of ``title`` and ``workspace`` into legacy session metadata sidecars.

Sessions created before the workspace-grouping feature lack ``title`` and
``workspace`` keys in their ``_meta.json`` sidecars. This module backfills
those keys on first startup so the session picker can display titles
uniformly without special-casing legacy entries.

The backfill is **idempotent** via a sentinel file named
``.workspace_backfill_done`` inside AUTOSAVE_DIR. Two simultaneous startups
racing the backfill are benign -- per-file writes use tempfile + os.replace,
and the sentinel is touched atomically at the end.
"""

from __future__ import annotations

import json
import os
import pathlib
import tempfile


_SENTINEL_FILENAME = ".workspace_backfill_done"


def _autosave_dir() -> pathlib.Path:
    """Return AUTOSAVE_DIR as a Path. Lazy import dodges a config cycle."""
    from code_puppy.config import AUTOSAVE_DIR

    return pathlib.Path(AUTOSAVE_DIR)


def _atomic_touch(path: pathlib.Path) -> None:
    """Create an empty file at ``path`` via tempfile + ``os.replace``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".sentinel_", dir=str(path.parent))
    try:
        os.close(fd)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _emit_info_safely(message: str) -> None:
    """Best-effort ``emit_info`` -- never crash the backfill over UX wiring."""
    try:
        from code_puppy.messaging import emit_info

        emit_info(message)
    except Exception:
        pass


def backfill_session_metadata() -> None:
    """Backfill ``title`` and ``workspace`` into legacy session metadata sidecars.

    Idempotent via a sentinel. Safe to call on every startup; the second
    call is an O(1) sentinel check. Failure modes are best-effort logged
    and never abort the caller.

    This is the ONLY caller-facing function in this module.
    """
    try:
        autosave_dir = _autosave_dir()
        sentinel = autosave_dir / _SENTINEL_FILENAME

        if sentinel.exists():
            return

        autosave_dir.mkdir(parents=True, exist_ok=True)

        from code_puppy.session_storage import extract_session_title, load_session

        changed = 0
        failures = 0

        for pkl_path in sorted(autosave_dir.glob("*.pkl")):
            stem = pkl_path.stem
            meta_path = autosave_dir / f"{stem}_meta.json"

            if not meta_path.exists():
                # No sidecar -- skip, the picker tolerates missing metadata.
                continue

            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception as exc:
                failures += 1
                try:
                    from code_puppy.error_logging import log_error_message

                    log_error_message(
                        f"Backfill: could not read {meta_path}: {exc!r}",
                        context="session_backfill.backfill_session_metadata",
                    )
                except Exception:
                    pass
                continue

            # Already backfilled individually -- skip.
            if meta.get("title"):
                continue

            try:
                history = load_session(stem, autosave_dir)
                title = extract_session_title(history)
            except Exception as exc:
                failures += 1
                try:
                    from code_puppy.error_logging import log_error_message

                    log_error_message(
                        f"Backfill: could not load {pkl_path}: {exc!r}",
                        context="session_backfill.backfill_session_metadata",
                    )
                except Exception:
                    pass
                continue

            meta["title"] = title
            meta["workspace"] = meta.get("workspace", "")

            try:
                fd, tmp_name = tempfile.mkstemp(prefix=".meta_", dir=str(autosave_dir))
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as f:
                        json.dump(meta, f, indent=2)
                    os.replace(tmp_name, str(meta_path))
                except Exception:
                    try:
                        os.unlink(tmp_name)
                    except OSError:
                        pass
                    raise
                changed += 1
            except Exception as exc:
                failures += 1
                try:
                    from code_puppy.error_logging import log_error_message

                    log_error_message(
                        f"Backfill: could not write {meta_path}: {exc!r}",
                        context="session_backfill.backfill_session_metadata",
                    )
                except Exception:
                    pass

        _atomic_touch(sentinel)

        if changed or failures:
            _emit_info_safely(
                f"Backfilled session titles for {changed} session(s) "
                f"({failures} failed)."
            )

    except Exception as exc:  # pragma: no cover - defensive
        # Backfill MUST NOT crash the app.
        try:
            from code_puppy.error_logging import log_error_message

            log_error_message(
                f"Session metadata backfill aborted: {exc!r}",
                context="session_backfill.backfill_session_metadata",
            )
        except Exception:
            pass
