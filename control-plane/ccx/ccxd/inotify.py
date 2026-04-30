"""Asyncio-friendly inotify wrapper for ~/.claude/projects/.

Linux inotify is NOT recursive — we add a watch per directory:
- Base dir (~/.claude/projects/) watches for IN_CREATE|IN_ISDIR (new project dirs)
- Each project subdir watches for IN_CREATE|IN_MODIFY|IN_DELETE|IN_MOVED_TO

On inotify queue overflow (IN_Q_OVERFLOW), the caller should re-walk and
re-read from saved byte offsets (append-only files lose nothing).
"""
from __future__ import annotations

import os
from pathlib import Path

from inotify_simple import INotify, Event, flags


# Events for project subdirs (where jsonl files live)
_SUBDIR_MASK = flags.CREATE | flags.MODIFY | flags.DELETE | flags.MOVED_TO
# Events for the base projects dir (detect new project subdirs)
_BASE_MASK = flags.CREATE | flags.ISDIR


class InotifyWatcher:
    """Per-directory inotify watcher for the Claude projects tree."""

    def __init__(self, base_dir: Path) -> None:
        self._inotify = INotify()
        self._wd_to_path: dict[int, Path] = {}
        self._path_to_wd: dict[Path, int] = {}
        self._base_dir = base_dir

        # Watch the base directory for new subdirs
        self._add_watch_internal(base_dir, _BASE_MASK | _SUBDIR_MASK)

        # Watch all existing subdirs
        if base_dir.is_dir():
            for entry in base_dir.iterdir():
                if entry.is_dir():
                    self._add_watch_internal(entry, _SUBDIR_MASK)

    @property
    def fd(self) -> int:
        """File descriptor for use with asyncio add_reader."""
        return self._inotify.fd

    def _add_watch_internal(self, path: Path, mask: int) -> None:
        try:
            wd = self._inotify.add_watch(str(path), mask)
            self._wd_to_path[wd] = path
            self._path_to_wd[path] = wd
        except OSError:
            pass  # dir may have vanished between listdir and add_watch

    def add_watch(self, path: Path) -> None:
        """Add a subdir watch (for new project directories)."""
        self._add_watch_internal(path, _SUBDIR_MASK)

    def remove_watch(self, path: Path) -> None:
        """Remove a watch for a directory."""
        wd = self._path_to_wd.pop(path, None)
        if wd is not None:
            try:
                self._inotify.rm_watch(wd)
            except OSError:
                pass
            self._wd_to_path.pop(wd, None)

    def is_watching(self, path: Path) -> bool:
        return path in self._path_to_wd

    def read_events(self) -> list[Event]:
        """Non-blocking read of pending events."""
        return self._inotify.read(timeout=0)

    def handle_new_subdirs(self, events: list[Event]) -> list[Path]:
        """Process events and add watches for newly created subdirectories.

        Returns list of new subdirs that got watches added.
        """
        new_dirs: list[Path] = []
        for event in events:
            if flags.ISDIR & event.mask and flags.CREATE & event.mask:
                parent = self._wd_to_path.get(event.wd)
                if parent and event.name:
                    new_path = parent / event.name
                    if new_path.is_dir() and not self.is_watching(new_path):
                        self.add_watch(new_path)
                        new_dirs.append(new_path)
        return new_dirs

    def resolve_event_path(self, event: Event) -> Path | None:
        """Resolve an event to its full file path."""
        parent = self._wd_to_path.get(event.wd)
        if parent and event.name:
            return parent / event.name
        return parent

    def is_overflow(self, events: list[Event]) -> bool:
        """Check if any event indicates queue overflow."""
        return any(flags.Q_OVERFLOW & e.mask for e in events)

    def close(self) -> None:
        """Close the inotify fd."""
        try:
            self._inotify.close()
        except OSError:
            pass
