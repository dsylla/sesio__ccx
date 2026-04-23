"""ccxctl — control plane for the ccx coding station (EC2 + R53 + SSH)."""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import cached_property
from pathlib import Path
from typing import Annotated

import typer

# boto3 is imported lazily in Config.session below. The ssh / --help / menu-
# with-$CCX_STATE paths avoid the ~100 ms boto3 import entirely.

# --- config ---------------------------------------------------------------

COMMON_TYPES = [
    "t4g.small", "t4g.medium", "t4g.large", "t4g.xlarge", "t4g.2xlarge",
    "c7g.xlarge", "c7g.2xlarge", "c7g.4xlarge",
    "m7g.xlarge", "m7g.2xlarge",
    "r7g.xlarge",
]


@dataclass
class Config:
    aws_profile: str = os.environ.get("AWS_PROFILE") or "sesio__euwest1"
    region: str = os.environ.get("AWS_REGION", "eu-west-1")
    hostname: str = os.environ.get("CCX_HOSTNAME", "ccx.dsylla.sesio.io")
    hosted_zone: str = os.environ.get("CCX_HOSTED_ZONE", "sesio.io.")
    instance_id_file: Path = field(
        default_factory=lambda: Path(
            os.environ.get("CCX_INSTANCE_ID_FILE")
            or os.path.expanduser("~/.config/ccx/instance_id")
        )
    )
    widget_name: str = os.environ.get("CCX_WIDGET_NAME", "ccx_status")
    ssh_user: str = os.environ.get("CCX_SSH_USER", "david")
    ssh_key: Path = field(
        default_factory=lambda: Path(
            os.environ.get("CCX_SSH_KEY")
            or os.path.expanduser("~/.ssh/keys/dsylla-ccx")
        )
    )
    terminal: str = os.environ.get("CCX_TERMINAL", "alacritty")
    notify_icon: Path = field(
        default_factory=lambda: Path(
            os.environ.get("CCX_NOTIFY_ICON")
            or "/home/david/Work/ssdd/ssdd-linux/assets/logo-icon.png"
        )
    )

    @cached_property
    def instance_id(self) -> str:
        try:
            iid = self.instance_id_file.read_text().strip()
        except FileNotFoundError:
            die(f"instance id file missing: {self.instance_id_file}")
        if not iid:
            die(f"instance id file empty: {self.instance_id_file}")
        return iid

    @cached_property
    def session(self):
        import boto3  # lazy: skip ~100 ms on commands that don't touch AWS
        return boto3.session.Session(profile_name=self.aws_profile, region_name=self.region)

    @cached_property
    def ec2(self):
        return self.session.client("ec2")

    @cached_property
    def r53(self):
        return self.session.client("route53")


CFG = Config()


# --- helpers --------------------------------------------------------------

def log(msg: str) -> None:
    print(msg, flush=True)


# ANSI styling — plain `log()` stays uncoloured for grep-ability.
class _C:
    BOLD  = "\033[1m"
    DIM   = "\033[2m"
    RED   = "\033[31m"
    GREEN = "\033[32m"
    BLUE  = "\033[34m"
    RESET = "\033[0m"


def _step(msg: str) -> None:
    """Top-level step — `▶ msg`."""
    print(f"{_C.BLUE}▶{_C.RESET} {msg}", flush=True)


def _sub(msg: str) -> None:
    """Indented detail line — `  · msg` (used for state polls)."""
    print(f"  {_C.DIM}·{_C.RESET} {msg}", flush=True)


def _ok(msg: str) -> None:
    """Success line — `✓ msg`."""
    print(f"{_C.GREEN}✓{_C.RESET} {msg}", flush=True)


def notify(
    title: str, body: str = "", *,
    urgency: str = "normal",
    timeout_ms: int | None = None,
    replace_id: int | None = None,
    print_id: bool = False,
) -> int | None:
    """Fire a desktop notification with the ccx icon.

    `replace_id` reuses a prior notification's id so the card mutates in
    place instead of stacking — critical for the "follows you across
    workspaces" behaviour (the daemon redraws on the active output each
    time we replace). `print_id=True` asks notify-send to echo the new
    id to stdout; we parse it and return it.
    """
    if not shutil.which("notify-send"):
        return None
    args = ["notify-send", "-u", urgency]
    if timeout_ms is not None:
        args += ["-t", str(timeout_ms)]
    if replace_id is not None:
        args += ["-r", str(replace_id)]
    if print_id:
        args += ["-p"]
    if CFG.notify_icon.exists():
        args += ["-i", str(CFG.notify_icon)]
    args.append(title)
    if body:
        args.append(body)
    r = subprocess.run(args, capture_output=True, text=True, check=False)
    if print_id and r.returncode == 0:
        try:
            return int(r.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            pass
    return None


# Shared across invocations so `ccxctl stop` → `ccxctl start` reuses a
# single card instead of stacking. $XDG_RUNTIME_DIR is cleared at logout
# which is the right lifetime for a transient notification handle.
_NOTIFY_ID_FILE = Path(
    os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
) / "ccx-notify-id"


# 24 hours, passed as an explicit timeout. `-t 0` per the D-Bus spec means
# "never expire", but xfce4-notifyd (and some other daemons) interpret it
# as "use the server default" — which on XFCE is 5 s. A large positive
# timeout is respected unconditionally across daemons and effectively
# means "stays put through any single ccxctl operation".
_NOTIFY_PERSIST_MS = 24 * 60 * 60 * 1000


class ProgressNotifier:
    """A single replaceable notification that accumulates step lines.

    Each `.step(line)` appends to the body and redraws in place via
    `notify-send --replace-id` with a 24 h timeout so the card persists
    on whichever workspace is currently focused. `.done(line)` uses the
    same long timeout — the card stays up until the user dismisses it
    or the next ccxctl invocation replaces it. The id is persisted to
    `$XDG_RUNTIME_DIR/ccx-notify-id` so subsequent ccxctl invocations
    target the same card instead of stacking next to it.
    """

    def __init__(self, title: str = "ccx"):
        self._title = title
        self._lines: list[str] = []
        self._id: int | None = _load_notify_id()

    def _redraw(self, *, timeout_ms: int, urgency: str, capture_id: bool) -> None:
        body = "\n".join(self._lines)
        new_id = notify(
            self._title, body,
            urgency=urgency, timeout_ms=timeout_ms,
            replace_id=self._id, print_id=capture_id,
        )
        if capture_id and new_id is not None:
            self._id = new_id
            _save_notify_id(new_id)

    def step(self, line: str, urgency: str = "normal") -> None:
        """Append a line and redraw; card persists across workspace switches."""
        self._lines.append(line)
        self._redraw(timeout_ms=_NOTIFY_PERSIST_MS, urgency=urgency, capture_id=True)

    def done(self, line: str, urgency: str = "normal") -> None:
        """Append the closing line; card persists until dismissed or replaced."""
        self._lines.append(line)
        # Capture id here too: the card survives past this invocation and the
        # next ccxctl run needs to know which id to replace.
        self._redraw(timeout_ms=_NOTIFY_PERSIST_MS, urgency=urgency, capture_id=True)


def _load_notify_id() -> int | None:
    try:
        return int(_NOTIFY_ID_FILE.read_text().strip())
    except (OSError, ValueError):
        return None


def _save_notify_id(nid: int) -> None:
    try:
        _NOTIFY_ID_FILE.write_text(str(nid))
    except OSError:
        pass


def die(msg: str) -> "typer.Exit":
    """Log + notify + exit 1."""
    print(f"error: {msg}", file=sys.stderr, flush=True)
    notify("ccx error", msg, urgency="critical")
    raise typer.Exit(code=1)


def refresh_widget() -> None:
    """Nudge the qtile CcxStatusWidget so the bar updates immediately."""
    if not shutil.which("qtile"):
        return
    subprocess.run(
        ["qtile", "cmd-obj", "-o", "widget", CFG.widget_name, "-f", "force_update"],
        check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def pick_menu(prompt: str, choices: list[str]) -> str | None:
    """Show a rofi (preferred) or dmenu list. Returns the selection or None."""
    for cmd in (["rofi", "-dmenu", "-i", "-p", prompt],
                ["dmenu", "-p", prompt]):
        if not shutil.which(cmd[0]):
            continue
        try:
            result = subprocess.run(
                cmd, input="\n".join(choices), text=True,
                capture_output=True, check=False,
            )
        except FileNotFoundError:
            continue
        if result.returncode != 0:
            return None
        choice = result.stdout.strip()
        return choice or None
    die("no menu program found (rofi or dmenu required)")
    return None  # unreachable


def public_ip_32() -> str:
    """Get the laptop's current public /32 via checkip.amazonaws.com."""
    with urllib.request.urlopen("https://checkip.amazonaws.com", timeout=5) as r:
        ip = r.read().decode().strip()
    return f"{ip}/32"


# --- EC2 queries ----------------------------------------------------------

def describe_instance() -> dict:
    """Return the single instance's describe-instances dict."""
    resp = CFG.ec2.describe_instances(InstanceIds=[CFG.instance_id])
    try:
        return resp["Reservations"][0]["Instances"][0]
    except (KeyError, IndexError):
        die(f"instance {CFG.instance_id} not found")
        return {}  # unreachable


def home_volume_id() -> str:
    for bdm in describe_instance().get("BlockDeviceMappings", []):
        if bdm.get("DeviceName") == "/dev/sdh":
            return bdm["Ebs"]["VolumeId"]
    die("home volume not found at /dev/sdh")
    return ""  # unreachable


def uptime_str(launch: datetime) -> str:
    secs = int((datetime.now(timezone.utc) - launch).total_seconds())
    if secs < 0:
        return ""
    return f"{secs // 3600}h{(secs % 3600) // 60:02d}m"


def format_status_line(inst: dict) -> str:
    """One-line status: <state> <type> <ip> <uptime> <id>."""
    state = inst.get("State", {}).get("Name", "unknown")
    type_ = inst.get("InstanceType", "?")
    ip = inst.get("PublicIpAddress", "-")
    launch = inst.get("LaunchTime")
    uptime = uptime_str(launch) if state == "running" and launch else ""
    return f"{state} {type_} {ip} {uptime} {inst.get('InstanceId', '-')}"


# --- DNS ------------------------------------------------------------------

def update_dns() -> None:
    """UPSERT the A record to the instance's current public IP."""
    inst = describe_instance()
    ip = inst.get("PublicIpAddress")
    if not ip:
        die("instance has no public IP (is it stopped?)")

    zones = CFG.r53.list_hosted_zones_by_name(DNSName=CFG.hosted_zone)["HostedZones"]
    if not zones:
        die(f"hosted zone {CFG.hosted_zone} not found")
    zone_id = zones[0]["Id"].removeprefix("/hostedzone/")

    _step(f"pointing {CFG.hostname} → {_C.BOLD}{ip}{_C.RESET} (zone {zone_id})")
    CFG.r53.change_resource_record_sets(
        HostedZoneId=zone_id,
        ChangeBatch={"Changes": [{
            "Action": "UPSERT",
            "ResourceRecordSet": {
                "Name": f"{CFG.hostname}.",
                "Type": "A",
                "TTL": 60,
                "ResourceRecords": [{"Value": ip}],
            },
        }]},
    )


# --- typer app ------------------------------------------------------------

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="ccxctl — control the ccx coding station.",
    pretty_exceptions_show_locals=False,
)

from ccx.sessions import app as _sessions_app
app.add_typer(_sessions_app, name="session", help="Manage claude sessions (tmux).")


@app.command()
def motd() -> None:
    """Print the ccx login banner (system / instance / sessions / usage / services / dotfiles)."""
    from ccx.motd import main as _motd_main
    _motd_main()


@app.command()
def status() -> None:
    """Print one-line status: state type ip uptime id."""
    log(format_status_line(describe_instance()))


def _wait_for_state(target: str, poll_seconds: float = 3.0,
                    timeout_seconds: float = 600.0) -> str:
    """Poll describe-instances until state == target, logging transitions.

    Returns the final state. `describe_instance` already calls `die()` on
    404, so we only have to worry about the happy path + timeout here.
    """
    last: str | None = None
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        state = describe_instance().get("State", {}).get("Name", "unknown")
        if state != last:
            _sub(f"state: {_C.BOLD}{state}{_C.RESET}")
            last = state
            refresh_widget()
        if state == target:
            return state
        time.sleep(poll_seconds)
    die(f"timed out waiting for state={target}")
    return ""  # unreachable


def _wait_for_ssh(host: str, port: int = 22, poll_seconds: float = 2.0,
                  timeout_seconds: float = 180.0) -> None:
    """TCP-probe host:port until reachable, then return.

    EC2's `state=running` transition only means the hypervisor has powered
    the guest on — sshd (and cloud-init) take another 15-60 s. Executing
    ssh before port 22 is up just hangs in the TCP-connect timeout. We
    probe here so the exec is only issued once the socket answers.
    """
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=3):
                return
        except (OSError, socket.timeout):
            time.sleep(poll_seconds)
    die(f"timed out waiting for {host}:{port}")


def _ssh_raw() -> None:
    """Replace the current process with a plain `ssh`, no tmux wrapping.

    `ccxctl start` uses this; users who want the shared tmux session
    still have `ccxctl ssh` (the default) and the widget's "new session"
    rofi entry.
    """
    os.execvp("ssh", [
        "ssh", "-i", str(CFG.ssh_key),
        "-o", "IdentitiesOnly=yes",
        "-o", "StrictHostKeyChecking=accept-new",
        "-t", f"{CFG.ssh_user}@{CFG.hostname}",
    ])


@app.command()
def start(
    no_ssh: Annotated[bool, typer.Option("--no-ssh", help="Skip the auto-SSH at the end.")] = False,
) -> None:
    """Start the instance, refresh sg/dns, then drop into the shared tmux.

    The notification updates in place through big milestones (starting →
    running → ready); detailed state transitions + SG/DNS work show on
    stdout only. Security-group refresh before SSH is why this doesn't
    time out after a coffee break from a new IP.
    """
    iid = CFG.instance_id
    notifier = ProgressNotifier()
    start_state = describe_instance().get("State", {}).get("Name", "unknown")
    _step(f"current state: {_C.BOLD}{start_state}{_C.RESET}")

    if start_state != "running":
        _step(f"starting {_C.BOLD}{iid}{_C.RESET}")
        notifier.step(f"▶ starting {iid}")
        CFG.ec2.start_instances(InstanceIds=[iid])
        _wait_for_state("running")
        notifier.step("✓ running")
    else:
        notifier.step("✓ already running")

    refresh_sg()
    notifier.step("✓ security group refreshed")
    update_dns()
    inst = describe_instance()
    ip = inst.get("PublicIpAddress", "?")
    notifier.step(f"✓ dns → {ip}")
    refresh_widget()

    itype = inst.get("InstanceType", "?")
    _ok(f"ready — {_C.BOLD}{itype}{_C.RESET} {_C.BOLD}{ip}{_C.RESET}  {iid}")

    if no_ssh:
        notifier.done(f"✓ ready — {itype} {ip}")
        return

    notifier.step(f"✓ ready — {itype} {ip}")
    _step(f"waiting for sshd on {CFG.hostname}:22")
    notifier.step("▶ waiting for sshd")
    _wait_for_ssh(CFG.hostname)
    _step(f"ssh {CFG.ssh_user}@{CFG.hostname} (raw)")
    notifier.done("✓ ssh ready — connecting")
    _ssh_raw()


@app.command()
def stop() -> None:
    """Stop the instance, update widget."""
    iid = CFG.instance_id
    notifier = ProgressNotifier()
    _step(f"stopping {_C.BOLD}{iid}{_C.RESET}")
    notifier.step(f"▶ stopping {iid}")
    CFG.ec2.stop_instances(InstanceIds=[iid])
    _wait_for_state("stopped")
    refresh_widget()

    inst = describe_instance()
    itype = inst.get("InstanceType", "?")
    _ok(f"stopped — {_C.BOLD}{itype}{_C.RESET}  {iid}")
    notifier.done(f"✓ stopped — {itype}")


@app.command()
def ssh(
    raw: Annotated[bool, typer.Option("--raw", "-R", help="Plain shell, skip tmux.")] = False,
    args: Annotated[list[str] | None, typer.Argument(help="Extra ssh arguments.")] = None,
) -> None:
    """SSH to the instance. Default: attach to shared tmux session `ccx`."""
    base_argv = [
        "ssh",
        "-i", str(CFG.ssh_key),
        "-o", "IdentitiesOnly=yes",
        "-o", "StrictHostKeyChecking=accept-new",
        "-t",
        f"{CFG.ssh_user}@{CFG.hostname}",
    ]
    extra = list(args or [])
    if raw:
        os.execvp("ssh", base_argv + extra)
    else:
        # Attach or create the shared session on the remote.
        os.execvp("ssh", base_argv + ["tmux", "new-session", "-A", "-s", "ccx"] + extra)


@app.command("refresh-sg")
def refresh_sg() -> None:
    """Revoke stale /32 ingress rules on port 22, authorize current public IP."""
    inst = describe_instance()
    sg_id = inst["SecurityGroups"][0]["GroupId"]
    new_cidr = public_ip_32()
    _step(f"current public ip: {_C.BOLD}{new_cidr}{_C.RESET}")

    sg = CFG.ec2.describe_security_groups(GroupIds=[sg_id])["SecurityGroups"][0]
    existing: list[str] = []
    for perm in sg.get("IpPermissions", []):
        if perm.get("FromPort") == 22:
            existing.extend(r["CidrIp"] for r in perm.get("IpRanges", []))

    for cidr in existing:
        if cidr != new_cidr:
            _sub(f"revoking stale cidr {cidr}")
            CFG.ec2.revoke_security_group_ingress(
                GroupId=sg_id, IpProtocol="tcp", FromPort=22, ToPort=22, CidrIp=cidr,
            )

    if new_cidr not in existing:
        _sub(f"authorizing {new_cidr}")
        CFG.ec2.authorize_security_group_ingress(
            GroupId=sg_id, IpProtocol="tcp", FromPort=22, ToPort=22, CidrIp=new_cidr,
        )


@app.command("refresh-dns")
def refresh_dns() -> None:
    """Force-update the Route 53 A record to the instance's current public IP."""
    update_dns()


@app.command()
def resize(
    new_type: Annotated[str | None, typer.Argument(help="Instance type (rofi menu if omitted).")] = None,
) -> None:
    """Change instance type. Requires stopped state."""
    if not new_type:
        new_type = pick_menu("ccx resize:", COMMON_TYPES)
    if not new_type:
        return
    inst = describe_instance()
    state = inst["State"]["Name"]
    if state != "stopped":
        die(f"resize requires stopped state (currently: {state})")
    log(f"resizing {CFG.instance_id} -> {new_type}")
    CFG.ec2.modify_instance_attribute(
        InstanceId=CFG.instance_id, InstanceType={"Value": new_type},
    )
    notify("ccx", f"resized to {new_type}")


def _grow_volume(vol_id: str, new_gb: int) -> None:
    import time
    current = CFG.ec2.describe_volumes(VolumeIds=[vol_id])["Volumes"][0]["Size"]
    if new_gb <= current:
        die(f"requested size {new_gb} GB <= current {current} GB")
    log(f"growing {vol_id}: {current} -> {new_gb} GB")
    CFG.ec2.modify_volume(VolumeId=vol_id, Size=new_gb)
    while True:
        mods = CFG.ec2.describe_volumes_modifications(VolumeIds=[vol_id])
        state = mods["VolumesModifications"][0]["ModificationState"]
        if state != "modifying":
            break
        time.sleep(5)


def _ssh_exec(remote_cmd: str) -> None:
    """Run a one-shot command on the instance via SSH (for resize2fs / growpart)."""
    subprocess.run(
        ["ssh", "-i", str(CFG.ssh_key), "-o", "IdentitiesOnly=yes",
         "-o", "StrictHostKeyChecking=accept-new",
         f"{CFG.ssh_user}@{CFG.hostname}", remote_cmd],
        check=True,
    )


@app.command("grow-home")
def grow_home(
    new_gb: Annotated[int | None, typer.Argument(help="New size in GB (menu if omitted).")] = None,
) -> None:
    """Grow the /home volume and resize2fs over SSH."""
    if new_gb is None:
        sizes = ["150", "200", "300", "500", "750", "1000"]
        choice = pick_menu("ccx grow home GB:", sizes)
        if not choice:
            return
        new_gb = int(choice)
    vol_id = home_volume_id()
    _grow_volume(vol_id, new_gb)
    stripped = vol_id.removeprefix("vol-")
    _ssh_exec(f"sudo resize2fs /dev/disk/by-id/nvme-Amazon_Elastic_Block_Store_vol{stripped}")
    notify("ccx", f"home grown to {new_gb} GB")


@app.command("grow-root")
def grow_root(
    new_gb: Annotated[int | None, typer.Argument(help="New size in GB (menu if omitted).")] = None,
) -> None:
    """Grow the root volume (growpart + resize2fs over SSH)."""
    if new_gb is None:
        sizes = ["40", "50", "60", "80", "100"]
        choice = pick_menu("ccx grow root GB:", sizes)
        if not choice:
            return
        new_gb = int(choice)
    inst = describe_instance()
    root_dev = inst["RootDeviceName"]
    root_vol_id = next(
        (bdm["Ebs"]["VolumeId"]
         for bdm in inst["BlockDeviceMappings"]
         if bdm["DeviceName"] == root_dev),
        None,
    )
    if not root_vol_id:
        die("root volume not found")
    _grow_volume(root_vol_id, new_gb)
    _ssh_exec("sudo growpart /dev/nvme0n1 1 && sudo resize2fs /dev/nvme0n1p1")
    notify("ccx", f"root grown to {new_gb} GB")


@app.command()
def snapshot(
    note: Annotated[str, typer.Argument(help="Tag the snapshot with this note.")] = "manual",
) -> None:
    """Snapshot the /home volume, tagged with date + note."""
    vol_id = home_volume_id()
    date_tag = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")
    log(f"snapshotting {vol_id} ...")
    resp = CFG.ec2.create_snapshot(
        VolumeId=vol_id,
        Description=f"ccx home snapshot {date_tag}: {note}",
        TagSpecifications=[{
            "ResourceType": "snapshot",
            "Tags": [
                {"Key": "Project", "Value": "ccx"},
                {"Key": "Date", "Value": date_tag},
                {"Key": "Note", "Value": note},
            ],
        }],
    )
    snap_id = resp["SnapshotId"]
    log(snap_id)
    notify("ccx", f"snapshot started: {snap_id}")


@app.command()
def menu() -> None:
    """State-aware rofi menu. Dispatches the chosen subcommand."""
    # Fast path: the widget/caller may pass the known state via $CCX_STATE to
    # save us a ~400 ms describe-instances round-trip before showing rofi.
    state = os.environ.get("CCX_STATE") or describe_instance()["State"]["Name"]

    actions_by_state = {
        "running":       ["stop", "ssh", "refresh-sg", "refresh-dns", "snapshot", "status"],
        "stopped":       ["start", "resize", "grow-home", "grow-root", "snapshot", "status"],
        "pending":       ["status"],
        "stopping":      ["status"],
        "shutting-down": ["status"],
    }
    actions = actions_by_state.get(state, ["status"])
    choice = pick_menu(f"ccx ({state}):", actions)
    if not choice:
        return

    # `ssh` and `start` both want an interactive TTY → spawn a terminal.
    if choice in ("ssh", "start"):
        _spawn_terminal_action(choice)
        return

    # Everything else runs headlessly in-process.
    dispatch = {
        "status":       status,
        "stop":         stop,
        "refresh-sg":   refresh_sg,
        "refresh-dns":  refresh_dns,
        "snapshot":     lambda: snapshot("from-menu"),
        "resize":       lambda: resize(None),
        "grow-home":    lambda: grow_home(None),
        "grow-root":    lambda: grow_root(None),
    }
    fn = dispatch.get(choice)
    if fn:
        fn()


def _spawn_terminal_action(choice: str) -> None:
    """Spawn `$CCX_TERMINAL` running the given subcommand; keep open on failure."""
    # `ccxctl start` now refresh-sg's + update-dns + exec's ssh itself,
    # so we no longer need to chain `&& ccxctl ssh` at the caller.
    script = {
        "ssh":   "ccxctl ssh",
        "start": "ccxctl start",
    }[choice]
    # Include ~/.local/bin in PATH in case the terminal env doesn't.
    wrapped = (
        f'export PATH="$HOME/.local/bin:$PATH"; '
        f'{script} || {{ echo; echo "[ccx] {choice} exited — press enter to close"; read; }}'
    )
    subprocess.Popen(
        [CFG.terminal, "-e", "bash", "-lc", wrapped],
        start_new_session=True,
    )


if __name__ == "__main__":
    app()
