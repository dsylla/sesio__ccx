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

    captured_headers: list[dict] = []

    def fake_urlopen(req, timeout=None):
        url = getattr(req, "full_url", str(req))
        headers = dict(getattr(req, "headers", {}) or {})
        captured_headers.append(headers)
        if "api/token" in url:
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
            if url.endswith(k):
                return FakeResp(v)
        return FakeResp("")

    monkeypatch.setattr("ccx.motd.urllib.request.urlopen", fake_urlopen)
    r = collect_instance()
    assert r["instance_id"] == "i-abc"
    assert r["instance_type"] == "t4g.xlarge"
    assert r["region"] == "eu-west-1"
    assert r["public_ip"] == "1.2.3.4"
    # At least one GET must have passed the X-aws-ec2-metadata-token header.
    # Header names in urllib.request.Request are stored capitalized, not raw.
    any_token_header = any(
        any(k.lower() == "x-aws-ec2-metadata-token" and v == "TOKEN" for k, v in h.items())
        for h in captured_headers[1:]  # skip the first call (the PUT for the token)
    )
    assert any_token_header, f"no token header in GETs: {captured_headers}"


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


def test_collect_dotfiles_no_upstream_returns_none_behind():
    from ccx.motd import collect_dotfiles
    from unittest.mock import MagicMock
    def fake_run(argv, **kw):
        m = MagicMock()
        if "rev-parse" in argv:
            m.returncode = 0
            m.stdout = "abc1234\n"
        elif "rev-list" in argv:
            m.returncode = 128  # git: "no upstream configured"
            m.stdout = ""
        return m
    with patch("ccx.motd.subprocess.run", side_effect=fake_run):
        r = collect_dotfiles()
    any_none = any(v["behind"] is None for v in r.values())
    assert any_none


def test_render_motd_smoke():
    """Just ensure the renderer produces non-empty boxed output for a sample payload."""
    from ccx.motd import render_motd
    system = {"hostname": "ccx", "uptime": "1h 2m", "cpu_pct": 5, "ram_pct": 10,
              "disk_used": "10G", "disk_total": "100G", "disk_pct": 10}
    instance = {"instance_id": "i-abc", "instance_type": "t4g.xlarge",
                "region": "eu-west-1", "az": "eu-west-1a",
                "public_ip": "1.2.3.4", "public_hostname": "h.example.com"}
    services = {"services": [("docker", "active"), ("ssh", "active")]}
    sessions = {"sessions": []}
    usage = {"today": {"input": 100, "output": 50, "total": 150}}
    dotfiles = {"sesio__ccx": {"sha": "abc1234", "behind": 0}}

    out = render_motd(system, instance, sessions, usage, services, dotfiles)
    assert "ccx" in out
    assert "t4g.xlarge" in out
    assert "docker" in out
    assert "abc1234" in out
    assert "▎" in out  # left-rule vertical bar
    assert "SYSTEM" in out and "DOTFILES" in out
