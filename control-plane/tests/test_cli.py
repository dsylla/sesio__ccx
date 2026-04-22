"""Tests for ccx.cli. Uses moto for AWS mocking."""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from unittest.mock import patch

import boto3
import pytest
from moto import mock_aws
from typer.testing import CliRunner


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture(autouse=True)
def stub_env(tmp_path: Path, monkeypatch):
    """Isolate tests from the real AWS profile + the user's home dir."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-1")
    monkeypatch.delenv("AWS_PROFILE", raising=False)

    iid_file = tmp_path / "instance_id"
    monkeypatch.setenv("CCX_INSTANCE_ID_FILE", str(iid_file))
    monkeypatch.setenv("CCX_SSH_KEY", str(tmp_path / "fake-key"))
    monkeypatch.setenv("CCX_NOTIFY_ICON", str(tmp_path / "no-icon"))

    # Force a fresh module so env-driven Config picks up the overrides.
    import ccx.cli
    import importlib
    importlib.reload(ccx.cli)
    yield


def _seed_instance(instance_id_file: Path):
    """Create a real moto EC2 + return the running instance id."""
    ec2 = boto3.client("ec2", region_name="eu-west-1")
    ami = ec2.describe_images()["Images"][0]["ImageId"]
    r = ec2.run_instances(ImageId=ami, MinCount=1, MaxCount=1, InstanceType="t4g.xlarge")
    iid = r["Instances"][0]["InstanceId"]
    instance_id_file.write_text(iid)
    return iid


# --- pure-function tests (no AWS) -----------------------------------------

def test_uptime_str_basic():
    from ccx.cli import uptime_str
    launch = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=2, minutes=14)
    out = uptime_str(launch)
    assert out in ("2h14m", "2h13m")  # tolerate 1-min drift


def test_uptime_str_negative_returns_empty():
    from ccx.cli import uptime_str
    future = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)
    assert uptime_str(future) == ""


def test_format_status_line_running():
    from ccx.cli import format_status_line
    line = format_status_line({
        "State": {"Name": "running"},
        "InstanceType": "t4g.xlarge",
        "PublicIpAddress": "1.2.3.4",
        "LaunchTime": dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1, minutes=5),
        "InstanceId": "i-abc",
    })
    assert line.startswith("running t4g.xlarge 1.2.3.4 1h0")
    assert line.endswith(" i-abc")


def test_format_status_line_stopped():
    from ccx.cli import format_status_line
    line = format_status_line({
        "State": {"Name": "stopped"},
        "InstanceType": "t4g.xlarge",
        "PublicIpAddress": "-",
        "InstanceId": "i-abc",
    })
    assert line == "stopped t4g.xlarge -  i-abc"


# --- CLI tests ------------------------------------------------------------

def test_help(runner):
    from ccx.cli import app
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "ccxctl" in result.stdout
    assert "refresh-dns" in result.stdout


def test_no_args_is_help(runner):
    from ccx.cli import app
    result = runner.invoke(app, [])
    assert "Usage" in result.stdout


def test_missing_instance_id_file_errors(runner, tmp_path, monkeypatch):
    monkeypatch.setenv("CCX_INSTANCE_ID_FILE", str(tmp_path / "nope"))
    import importlib, ccx.cli
    importlib.reload(ccx.cli)
    result = runner.invoke(ccx.cli.app, ["status"])
    assert result.exit_code != 0


# --- AWS-integration tests via moto ---------------------------------------

@mock_aws
def test_status_against_moto(runner, tmp_path, monkeypatch):
    monkeypatch.setenv("CCX_INSTANCE_ID_FILE", str(tmp_path / "iid"))
    import importlib, ccx.cli
    importlib.reload(ccx.cli)
    _seed_instance(tmp_path / "iid")

    result = runner.invoke(ccx.cli.app, ["status"])
    assert result.exit_code == 0, result.stdout
    assert "running t4g.xlarge" in result.stdout


@mock_aws
def test_stop_transitions_to_stopped(runner, tmp_path, monkeypatch):
    monkeypatch.setenv("CCX_INSTANCE_ID_FILE", str(tmp_path / "iid"))
    import importlib, ccx.cli
    importlib.reload(ccx.cli)
    iid = _seed_instance(tmp_path / "iid")

    # Patch refresh_widget + notify so the test doesn't hit qtile/notify-send.
    with patch("ccx.cli.refresh_widget"), patch("ccx.cli.notify"):
        result = runner.invoke(ccx.cli.app, ["stop"])
    assert result.exit_code == 0, result.stdout

    ec2 = boto3.client("ec2", region_name="eu-west-1")
    state = ec2.describe_instances(InstanceIds=[iid])["Reservations"][0]["Instances"][0]["State"]["Name"]
    assert state == "stopped"


@mock_aws
def test_snapshot_tags_and_description(runner, tmp_path, monkeypatch):
    monkeypatch.setenv("CCX_INSTANCE_ID_FILE", str(tmp_path / "iid"))
    import importlib, ccx.cli
    importlib.reload(ccx.cli)
    _seed_instance(tmp_path / "iid")

    # The run_instances in moto doesn't wire a /dev/sdh volume — attach one.
    ec2 = boto3.client("ec2", region_name="eu-west-1")
    iid = (tmp_path / "iid").read_text().strip()
    vol = ec2.create_volume(Size=100, AvailabilityZone="eu-west-1a")["VolumeId"]
    ec2.attach_volume(VolumeId=vol, InstanceId=iid, Device="/dev/sdh")

    with patch("ccx.cli.notify"):
        result = runner.invoke(ccx.cli.app, ["snapshot", "test-note"])
    assert result.exit_code == 0, result.stdout
    # Find *our* snapshot by filtering on the Project tag (moto's describe_snapshots
    # also returns the large set of public AMI-related snapshots owned by Amazon).
    snaps = ec2.describe_snapshots(
        Filters=[{"Name": "tag:Project", "Values": ["ccx"]}],
    )["Snapshots"]
    assert len(snaps) == 1
    tag_kv = {t["Key"]: t["Value"] for t in snaps[0]["Tags"]}
    assert tag_kv["Project"] == "ccx"
    assert tag_kv["Note"] == "test-note"
    assert "ccx home snapshot" in snaps[0]["Description"]


def test_menu_no_selection_is_noop(runner, tmp_path, monkeypatch):
    monkeypatch.setenv("CCX_INSTANCE_ID_FILE", str(tmp_path / "iid"))
    import importlib, ccx.cli
    importlib.reload(ccx.cli)
    (tmp_path / "iid").write_text("i-deadbeef")

    @mock_aws
    def run():
        _seed_instance(tmp_path / "iid")
        with patch("ccx.cli.pick_menu", return_value=None):
            return runner.invoke(ccx.cli.app, ["menu"])

    result = run()
    assert result.exit_code == 0, result.stdout


def test_ssh_default_uses_tmux(monkeypatch):
    """Default ssh should request `tmux new-session -A -s ccx` on the remote."""
    import importlib, ccx.cli
    importlib.reload(ccx.cli)
    captured: list[list[str]] = []

    def fake_execvp(prog, argv):
        captured.append(argv)

    monkeypatch.setattr(ccx.cli.os, "execvp", fake_execvp)
    from typer.testing import CliRunner
    CliRunner().invoke(ccx.cli.app, ["ssh"])
    assert captured, "execvp was not called"
    cmd = captured[0]
    assert "tmux" in " ".join(cmd)
    assert "new-session" in " ".join(cmd)
    assert "-A" in cmd
    assert "-s" in cmd and "ccx" in cmd


def test_ssh_raw_skips_tmux(monkeypatch):
    # Use a fixed key path that does not contain the word "tmux" so that the
    # assertion "tmux not in joined argv" only tests for the tmux remote command.
    monkeypatch.setenv("CCX_SSH_KEY", "/dev/null")
    import importlib, ccx.cli
    importlib.reload(ccx.cli)
    captured: list[list[str]] = []
    monkeypatch.setattr(ccx.cli.os, "execvp", lambda _, argv: captured.append(argv))
    from typer.testing import CliRunner
    CliRunner().invoke(ccx.cli.app, ["ssh", "--raw"])
    cmd = " ".join(captured[0])
    assert "tmux" not in cmd
