"""Tests for ccx.ccxd.inotify — asyncio inotify wrapper.

These tests use tmp_path directories but may not work in all CI environments
(inotify requires Linux). Tests are skipped if inotify_simple is unavailable.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

pytest.importorskip("inotify_simple", reason="inotify_simple requires Linux")

from ccx.ccxd.inotify import InotifyWatcher


@pytest.fixture
def watcher(tmp_path: Path):
    w = InotifyWatcher(tmp_path)
    yield w
    w.close()


class TestInotifyWatcher:
    def test_watches_base_dir(self, watcher: InotifyWatcher, tmp_path: Path):
        # The base directory should be watched
        assert watcher.is_watching(tmp_path)

    def test_add_subdir_watch(self, watcher: InotifyWatcher, tmp_path: Path):
        sub = tmp_path / "project-a"
        sub.mkdir()
        watcher.add_watch(sub)
        assert watcher.is_watching(sub)

    def test_remove_watch(self, watcher: InotifyWatcher, tmp_path: Path):
        sub = tmp_path / "project-b"
        sub.mkdir()
        watcher.add_watch(sub)
        watcher.remove_watch(sub)
        assert not watcher.is_watching(sub)

    def test_walk_and_watch_existing(self, tmp_path: Path):
        (tmp_path / "proj1").mkdir()
        (tmp_path / "proj2").mkdir()
        w = InotifyWatcher(tmp_path)
        assert w.is_watching(tmp_path / "proj1")
        assert w.is_watching(tmp_path / "proj2")
        w.close()

    @pytest.mark.asyncio
    async def test_read_events_on_file_create(self, tmp_path: Path):
        w = InotifyWatcher(tmp_path)
        try:
            # Create a file — should trigger an event
            (tmp_path / "test.jsonl").write_text("hello\n")
            # Give inotify a moment
            await asyncio.sleep(0.05)
            events = w.read_events()
            assert len(events) > 0
            assert any("test.jsonl" in str(e.name) for e in events
                       if hasattr(e, "name"))
        finally:
            w.close()

    @pytest.mark.asyncio
    async def test_new_subdir_detected(self, tmp_path: Path):
        w = InotifyWatcher(tmp_path)
        try:
            new_dir = tmp_path / "new-project"
            new_dir.mkdir()
            await asyncio.sleep(0.05)
            events = w.read_events()
            # After processing events, the new dir should auto-get a watch
            w.handle_new_subdirs(events)
            assert w.is_watching(new_dir)
        finally:
            w.close()
