from __future__ import annotations

import json
from unittest.mock import patch


def test_format_uptime_examples():
    from ccx.motd import format_uptime
    assert format_uptime(0) == "0m"
    assert format_uptime(59) == "0m"
    assert format_uptime(60) == "1m"
    assert format_uptime(3700) == "1h 1m"
    assert format_uptime(90061) == "1d 1h 1m"


def test_format_bytes_examples():
    from ccx.motd import format_bytes
    assert format_bytes(0) == "0B"
    assert format_bytes(1023) == "1023B"
    assert format_bytes(2048) == "2K"
    assert format_bytes(3 * 1024 * 1024) == "3M"
    assert format_bytes(5 * 1024**3) == "5.0G"


def test_visible_len_strips_ansi():
    from ccx.motd import visible_len
    assert visible_len("\x1b[31mhello\x1b[0m") == 5
