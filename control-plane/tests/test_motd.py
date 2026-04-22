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


def test_collect_system_reads_proc(tmp_path, monkeypatch):
    from ccx.motd import collect_system
    proc = tmp_path / "proc"
    proc.mkdir()
    (proc / "uptime").write_text("45.0 99.99\n")
    (proc / "meminfo").write_text("MemTotal: 1000 kB\nMemAvailable: 500 kB\n")
    (proc / "stat").write_text("cpu  100 0 100 800 0 0 0 0 0 0\n")
    monkeypatch.setattr("ccx.motd._PROC", str(proc))
    monkeypatch.setattr("ccx.motd._DISK_FN", lambda p: type("D", (), {"used": 2**30, "total": 4*(2**30)})())
    monkeypatch.setattr("ccx.motd._SLEEP", lambda _: None)
    s = collect_system()
    assert s is not None
    assert s["uptime"] == "0m"
    assert s["ram_pct"] == 50
    assert 0 <= s["cpu_pct"] <= 100
    assert s["disk_pct"] == 25


def test_collect_services_parses_systemctl():
    from ccx.motd import collect_services
    def fake_run(argv, **kw):
        name = argv[-1].replace(".service", "")
        from unittest.mock import MagicMock
        m = MagicMock()
        m.stdout = "active\n" if name == "docker" else "inactive\n"
        m.returncode = 0
        return m
    with patch("ccx.motd.subprocess.run", side_effect=fake_run):
        r = collect_services()
    names = {n for n, _ in r["services"]}
    assert "docker" in names
    assert "ssh" in names


def test_collect_dotfiles_reads_git_heads(tmp_path, monkeypatch):
    from ccx.motd import collect_dotfiles
    def fake_run(argv, **kw):
        from unittest.mock import MagicMock
        m = MagicMock()
        m.returncode = 0
        if "rev-parse" in argv:
            m.stdout = "abc1234\n"
        elif "rev-list" in argv:
            m.stdout = "3\n"
        else:
            m.stdout = ""
        return m
    with patch("ccx.motd.subprocess.run", side_effect=fake_run):
        r = collect_dotfiles()
    assert r["sesio__ccx"]["sha"] == "abc1234"
    assert r["sesio__ccx"]["behind"] == 3


def test_collect_instance_imdsv2(monkeypatch):
    from ccx.motd import collect_instance

    class FakeResp:
        def __init__(self, body: str): self.body = body.encode()
        def read(self): return self.body
        def __enter__(self): return self
        def __exit__(self, *a): pass

    def fake_urlopen(req, timeout=None):
        url = getattr(req, "full_url", req) if hasattr(req, "full_url") else req
        if "api/token" in str(url):
            return FakeResp("TOKEN")
        mapping = {
            "instance-id":      "i-abc",
            "instance-type":    "t4g.xlarge",
            "placement/region": "eu-west-1",
            "placement/availability-zone": "eu-west-1a",
            "public-ipv4":      "1.2.3.4",
            "public-hostname":  "ec2-1-2-3-4.eu-west-1.compute.amazonaws.com",
        }
        for k, v in mapping.items():
            if str(url).endswith(k):
                return FakeResp(v)
        return FakeResp("")

    monkeypatch.setattr("ccx.motd.urllib.request.urlopen", fake_urlopen)
    r = collect_instance()
    assert r["instance_id"] == "i-abc"
    assert r["instance_type"] == "t4g.xlarge"
    assert r["region"] == "eu-west-1"
    assert r["public_ip"] == "1.2.3.4"


def test_collect_sessions_wraps_sessions_module():
    from ccx.motd import collect_motd_sessions
    rows = [{"slug": "ccx", "tokens_today": {"input": 10, "output": 5}}]
    with patch("ccx.motd.collect_sessions", return_value=rows):
        r = collect_motd_sessions()
    assert r == {"sessions": rows}


def test_collect_usage_today_sums_across_projects(tmp_path, monkeypatch):
    from ccx.motd import collect_usage
    import datetime as dt
    today = dt.datetime.now(dt.timezone.utc).isoformat()
    (tmp_path / "projA").mkdir()
    (tmp_path / "projA/log.jsonl").write_text(
        json.dumps({"timestamp": today, "message": {"usage": {"input_tokens": 100, "output_tokens": 50}}}) + "\n"
    )
    (tmp_path / "projB").mkdir()
    (tmp_path / "projB/log.jsonl").write_text(
        json.dumps({"timestamp": today, "message": {"usage": {"input_tokens": 7, "output_tokens": 3}}}) + "\n"
    )
    monkeypatch.setattr("ccx.motd._CLAUDE_PROJECTS_DIR", str(tmp_path))
    r = collect_usage()
    assert r["today"] == {"input": 107, "output": 53, "total": 160}
