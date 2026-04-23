"""ccxctl motd — ANSI-boxed login banner for the ccx coding station."""
from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Optional

import typer

from ccx.sessions import collect_sessions, parse_jsonl_tokens_today


class C:
    CYAN = "\033[36m"
    GREEN = "\033[32m"
    RED = "\033[31m"
    YELLOW = "\033[33m"
    DIM = "\033[2m"
    BOLD = "\033[1m"
    RESET = "\033[0m"


def format_uptime(seconds: float) -> str:
    days = int(seconds // 86400)
    hours = int((seconds % 86400) // 3600)
    minutes = int((seconds % 3600) // 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if days or hours:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)


def format_bytes(n: int) -> str:
    if n >= 1_073_741_824:
        return f"{n / 1_073_741_824:.1f}G"
    if n >= 1_048_576:
        return f"{n / 1_048_576:.0f}M"
    if n >= 1024:
        return f"{n / 1024:.0f}K"
    return f"{n}B"


def visible_len(s: str) -> int:
    return len(re.sub(r"\033\[[0-9;]*m", "", s))


def _compute_widths() -> tuple[int, int]:
    term_w = shutil.get_terminal_size((80, 24)).columns
    inner = term_w - 3
    left = max(26, inner * 30 // 64)
    right = inner - left
    return left, right


LEFT_W, RIGHT_W = _compute_widths()
FULL_W = LEFT_W + RIGHT_W + 1


def box_mid(lt: str, rt: str) -> str:
    l = f"══ {lt} "
    r = f"══ {rt} "
    return f"{C.DIM}╠{l}{'═' * (LEFT_W - len(l))}╬{r}{'═' * (RIGHT_W - len(r))}╣{C.RESET}"


def box_full_mid(title: str) -> str:
    t = f"══ {title} "
    return f"{C.DIM}╠{t}{'═' * (FULL_W - len(t))}╣{C.RESET}"


def box_bottom() -> str:
    return f"{C.DIM}╚{'═' * LEFT_W}╩{'═' * RIGHT_W}╝{C.RESET}"


def box_full_bottom() -> str:
    return f"{C.DIM}╚{'═' * FULL_W}╝{C.RESET}"


def row(left: str, right: str) -> str:
    l_pad = LEFT_W - visible_len(left)
    r_pad = RIGHT_W - visible_len(right)
    return f"{C.DIM}║{C.RESET}{left}{' ' * max(0, l_pad)}{C.DIM}║{C.RESET}{right}{' ' * max(0, r_pad)}{C.DIM}║{C.RESET}"


def full_row(content: str) -> str:
    pad = FULL_W - visible_len(content)
    return f"{C.DIM}║{C.RESET}{content}{' ' * max(0, pad)}{C.DIM}║{C.RESET}"


def box_top(lt: str, rt: str) -> str:
    l = f"══ {lt} "
    r = f"══ {rt} "
    return f"{C.DIM}╔{l}{'═' * (LEFT_W - len(l))}╦{r}{'═' * (RIGHT_W - len(r))}╗{C.RESET}"


# Free-text banner printed above the box (matches the pattern in
# sesio__motd where the logo sits outside the framed sections).
LOGO = f"""{C.CYAN}{C.BOLD}\
   ██████╗ ██████╗██╗  ██╗
  ██╔════╝██╔════╝╚██╗██╔╝
  ██║     ██║      ╚███╔╝
  ██║     ██║      ██╔██╗
  ╚██████╗╚██████╗██╔╝ ██╗
   ╚═════╝ ╚═════╝╚═╝  ╚═╝{C.RESET}  {C.DIM}Claude Code X · ssdd linux{C.RESET}"""


def status_dot(ok: bool, label: str) -> str:
    if ok:
        return f"{C.GREEN}●{C.RESET} {label}"
    return f"{C.RED}✗{C.RESET} {label}"


def service_dot(state: str) -> str:
    if state == "active":
        return f"{C.GREEN}●{C.RESET}"
    if state == "failed":
        return f"{C.RED}✗{C.RESET}"
    if state in ("inactive", "dead"):
        return f"{C.DIM}○{C.RESET}"
    return f"{C.YELLOW}◐{C.RESET}"


_PROC = "/proc"
_SLEEP = time.sleep
_DISK_FN = shutil.disk_usage
_SUBPROC_TIMEOUT = 3


def _read_cpu_pct() -> float:
    def _sample():
        with open(f"{_PROC}/stat") as f:
            parts = f.readline().split()
        idle = int(parts[4])
        total = sum(int(x) for x in parts[1:])
        return idle, total
    try:
        i1, t1 = _sample()
        _SLEEP(0.5)
        i2, t2 = _sample()
        d_idle = i2 - i1
        d_total = t2 - t1
        if d_total <= 0:
            return 0.0
        return round((1.0 - d_idle / d_total) * 100, 0)
    except (OSError, IndexError, ValueError):
        return 0.0


def collect_system() -> Optional[dict[str, Any]]:
    try:
        with open(f"{_PROC}/uptime") as f:
            uptime_s = float(f.read().split()[0])
        info: dict[str, int] = {}
        with open(f"{_PROC}/meminfo") as f:
            for line in f:
                p = line.split()
                if p[0] in ("MemTotal:", "MemAvailable:"):
                    info[p[0]] = int(p[1])
                if len(info) == 2:
                    break
        ram_pct = round((1 - info["MemAvailable:"] / info["MemTotal:"]) * 100)
        disk = _DISK_FN("/")
        return {
            "hostname": socket.gethostname(),
            "uptime": format_uptime(uptime_s),
            "cpu_pct": int(_read_cpu_pct()),
            "ram_pct": ram_pct,
            "disk_used": format_bytes(disk.used),
            "disk_total": format_bytes(disk.total),
            "disk_pct": round(disk.used / disk.total * 100),
        }
    except Exception:
        return None


_IMDS = "http://169.254.169.254/latest"


def _imds_token() -> Optional[str]:
    req = urllib.request.Request(
        f"{_IMDS}/api/token",
        method="PUT",
        headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"},
    )
    try:
        with urllib.request.urlopen(req, timeout=2) as resp:
            return resp.read().decode().strip()
    except Exception:
        return None


def _imds_get(path: str, token: str) -> Optional[str]:
    req = urllib.request.Request(
        f"{_IMDS}/meta-data/{path}",
        headers={"X-aws-ec2-metadata-token": token},
    )
    try:
        with urllib.request.urlopen(req, timeout=2) as resp:
            return resp.read().decode().strip()
    except Exception:
        return None


def collect_instance() -> Optional[dict[str, Any]]:
    token = _imds_token()
    if not token:
        return None
    keys = {
        "instance_id":     "instance-id",
        "instance_type":   "instance-type",
        "region":          "placement/region",
        "az":              "placement/availability-zone",
        "public_ip":       "public-ipv4",
        "public_hostname": "public-hostname",
    }
    return {k: _imds_get(v, token) for k, v in keys.items()}


CCX_SERVICES = ["docker", "ssh", "fail2ban", "unattended-upgrades"]


def collect_services() -> Optional[dict[str, Any]]:
    try:
        services: list[tuple[str, str]] = []
        for svc in CCX_SERVICES:
            try:
                r = subprocess.run(
                    ["/usr/bin/systemctl", "is-active", f"{svc}.service"],
                    capture_output=True, text=True, timeout=_SUBPROC_TIMEOUT,
                )
                state = r.stdout.strip() or "unknown"
            except (subprocess.TimeoutExpired, OSError):
                state = "unknown"
            services.append((svc, state))
        return {"services": services}
    except Exception:
        return None


def _git_sha(repo_dir: str) -> Optional[str]:
    try:
        r = subprocess.run(
            ["git", "-C", repo_dir, "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=_SUBPROC_TIMEOUT,
        )
        return r.stdout.strip() or None
    except (subprocess.TimeoutExpired, OSError):
        return None


def _git_behind(repo_dir: str) -> int | None:
    """Commits behind upstream. None if no upstream configured."""
    try:
        r = subprocess.run(
            ["git", "-C", repo_dir, "rev-list", "--count", "HEAD..@{u}"],
            capture_output=True, text=True, timeout=_SUBPROC_TIMEOUT,
        )
        if r.returncode != 0 or not r.stdout.strip():
            return None
        return int(r.stdout.strip())
    except (subprocess.TimeoutExpired, OSError, ValueError):
        return None


DOTFILES_REPOS = {
    "sesio__ccx":    "/home/david/sesio__ccx",
    "claude-config": "/home/david/claude-config",
}


def collect_dotfiles() -> Optional[dict[str, Any]]:
    out = {}
    for name, path in DOTFILES_REPOS.items():
        sha = _git_sha(path)
        if sha is None:
            continue
        out[name] = {"sha": sha, "behind": _git_behind(path)}
    return out or None


_CLAUDE_PROJECTS_DIR = os.path.expanduser("~/.claude/projects")


def collect_motd_sessions() -> Optional[dict[str, Any]]:
    try:
        return {"sessions": collect_sessions()}
    except Exception:
        return None


def collect_usage() -> Optional[dict[str, Any]]:
    try:
        all_jsonl: list[Path] = []
        root = Path(_CLAUDE_PROJECTS_DIR)
        if root.is_dir():
            for proj in root.iterdir():
                if proj.is_dir():
                    all_jsonl.extend(proj.glob("*.jsonl"))
        tk = parse_jsonl_tokens_today(all_jsonl)
        return {"today": {**tk, "total": tk["input"] + tk["output"]}}
    except Exception:
        return None


COLLECT_TIMEOUT = 5


def render_motd(
    system: Optional[dict], instance: Optional[dict],
    sessions: Optional[dict], usage: Optional[dict],
    services: Optional[dict], dotfiles: Optional[dict],
) -> str:
    lines: list[str] = []
    # ---- LOGO (free text, above the framed sections) ----
    lines.append(LOGO)
    lines.append("")
    # ---- SYSTEM / INSTANCE ----
    lines.append(box_top("SYSTEM", "INSTANCE"))
    sys_l = [" unavailable", "", "", ""]
    if system:
        s = system
        sys_l = [
            f" Host:   {C.BOLD}{s['hostname']}{C.RESET}",
            f" Uptime: {C.BOLD}{s['uptime']}{C.RESET}",
            f" CPU: {C.BOLD}{s['cpu_pct']}%{C.RESET}  RAM: {C.BOLD}{s['ram_pct']}%{C.RESET}",
            f" Disk: {C.BOLD}{s['disk_used']}/{s['disk_total']}{C.RESET} ({s['disk_pct']}%)",
        ]
    ins_l = [" unavailable", "", "", ""]
    if instance:
        i = instance
        ins_l = [
            f" Type: {C.BOLD}{i['instance_type']}{C.RESET}",
            f" Reg:  {C.BOLD}{i['region']}{C.RESET} ({i['az']})",
            f" IP:   {C.BOLD}{i['public_ip']}{C.RESET}",
            f" ID:   {C.DIM}{i['instance_id']}{C.RESET}",
        ]
    for l, r in zip(sys_l, ins_l):
        lines.append(row(l, r))

    # ---- SESSIONS (full width) ----
    lines.append(box_full_mid("SESSIONS"))
    if sessions and sessions["sessions"]:
        for s in sessions["sessions"]:
            up = format_uptime(s["uptime_seconds"] or 0) if s.get("uptime_seconds") else "-"
            toks = s["tokens_today"]
            content = (
                f" {C.BOLD}{s['slug']}{C.RESET}"
                f"  up {up}"
                f"  in {C.BOLD}{toks['input']}{C.RESET}"
                f"  out {C.BOLD}{toks['output']}{C.RESET}"
                f"  {C.DIM}{s['cwd']}{C.RESET}"
            )
            lines.append(full_row(content))
    else:
        lines.append(full_row(f" {C.DIM}(no sessions){C.RESET}"))

    # ---- USAGE / SERVICES ----
    lines.append(box_mid("USAGE (today)", "SERVICES"))
    us_l = [" unavailable", "", ""]
    if usage:
        t = usage["today"]
        us_l = [
            f" In:    {C.BOLD}{t['input']}{C.RESET}",
            f" Out:   {C.BOLD}{t['output']}{C.RESET}",
            f" Total: {C.BOLD}{t['total']}{C.RESET}",
        ]
    sv_l = [" unavailable", "", ""]
    if services:
        sv_l = []
        for name, state in services["services"]:
            sv_l.append(f" {service_dot(state)} {name:<20s} {state}")
        while len(sv_l) < 3:
            sv_l.append("")
    max_n = max(len(us_l), len(sv_l))
    us_l += [""] * (max_n - len(us_l))
    sv_l += [""] * (max_n - len(sv_l))
    for l, r in zip(us_l, sv_l):
        lines.append(row(l, r))

    # ---- DOTFILES (full width) ----
    lines.append(box_full_mid("DOTFILES"))
    if dotfiles:
        for name, info in dotfiles.items():
            behind = info["behind"]
            if behind is None:
                drift = f" {C.DIM}(no upstream){C.RESET}"
            elif behind:
                drift = f" ({C.YELLOW}{behind} behind{C.RESET})"
            else:
                drift = ""
            lines.append(full_row(f" {C.BOLD}{name}{C.RESET}  {info['sha']}{drift}"))
    else:
        lines.append(full_row(f" {C.DIM}unavailable{C.RESET}"))
    lines.append(box_full_bottom())
    return "\n".join(lines)


def main() -> int:
    collectors = {
        "system":    collect_system,
        "instance":  collect_instance,
        "sessions":  collect_motd_sessions,
        "usage":     collect_usage,
        "services":  collect_services,
        "dotfiles":  collect_dotfiles,
    }
    results: dict[str, Any] = {}
    with ThreadPoolExecutor(max_workers=len(collectors)) as ex:
        futs = {ex.submit(fn): n for n, fn in collectors.items()}
        try:
            for fut in as_completed(futs, timeout=COLLECT_TIMEOUT):
                n = futs[fut]
                try:
                    results[n] = fut.result()
                except Exception:
                    results[n] = None
        except TimeoutError:
            pass
    for n in collectors:
        results.setdefault(n, None)
    print(render_motd(
        results["system"], results["instance"], results["sessions"],
        results["usage"], results["services"], results["dotfiles"],
    ))
    return 0
