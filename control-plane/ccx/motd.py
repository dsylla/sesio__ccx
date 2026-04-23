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
    BLUE = "\033[34m"
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


# Free-text banner printed above the sections.
LOGO = f"""{C.CYAN}{C.BOLD}\
   ██████╗ ██████╗██╗  ██╗
  ██╔════╝██╔════╝╚██╗██╔╝
  ██║     ██║      ╚███╔╝
  ██║     ██║      ██╔██╗
  ╚██████╗╚██████╗██╔╝ ██╗
   ╚═════╝ ╚═════╝╚═╝  ╚═╝{C.RESET}  {C.DIM}Claude Code X · ssdd linux{C.RESET}"""


def section(color: str, title: str, body_lines: list[str]) -> list[str]:
    """Left-rule section — coloured `▎` bar, section title, then body.

    A trailing blank line separates this section from the next.
    """
    rule = f"{color}▎{C.RESET}"
    out = [f"  {rule} {color}{title}{C.RESET}"]
    for line in body_lines:
        out.append(f"  {rule}  {line}")
    out.append("")
    return out


def kv(label: str, value: str, label_w: int = 7) -> str:
    """`label  value` — `label` dim-padded to label_w, value raw."""
    return f"{C.DIM}{label:<{label_w}}{C.RESET}  {value}"


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
    lines.append(LOGO)
    lines.append("")

    # SYSTEM — cyan
    if system:
        s = system
        sys_body = [
            kv("host",   f"{C.BOLD}{s['hostname']}{C.RESET}"),
            kv("uptime", f"{C.BOLD}{s['uptime']}{C.RESET}"),
            kv("cpu",    f"{C.BOLD}{s['cpu_pct']}%{C.RESET}   {C.DIM}ram{C.RESET} {C.BOLD}{s['ram_pct']}%{C.RESET}"),
            kv("disk",   f"{C.BOLD}{s['disk_used']}{C.RESET} / {C.BOLD}{s['disk_total']}{C.RESET} {C.DIM}({s['disk_pct']}%){C.RESET}"),
        ]
    else:
        sys_body = [f"{C.DIM}unavailable{C.RESET}"]
    lines.extend(section(C.CYAN, "SYSTEM", sys_body))

    # INSTANCE — blue
    if instance:
        i = instance
        ins_body = [
            kv("type",   f"{C.BOLD}{i['instance_type']}{C.RESET}"),
            kv("region", f"{C.BOLD}{i['region']}{C.RESET}  {C.DIM}({i['az']}){C.RESET}"),
            kv("ip",     f"{C.BOLD}{i['public_ip']}{C.RESET}"),
            kv("id",     f"{C.DIM}{i['instance_id']}{C.RESET}"),
        ]
    else:
        ins_body = [f"{C.DIM}unavailable{C.RESET}"]
    lines.extend(section(C.BLUE, "INSTANCE", ins_body))

    # SESSIONS — green
    if sessions and sessions["sessions"]:
        ses_body = []
        for s in sessions["sessions"]:
            up = format_uptime(s["uptime_seconds"] or 0) if s.get("uptime_seconds") else "-"
            toks = s["tokens_today"]
            ses_body.append(
                f"{C.GREEN}●{C.RESET} {C.BOLD}{s['slug']:<6}{C.RESET} "
                f"{C.DIM}{s['cwd']}{C.RESET}   "
                f"up {C.BOLD}{up}{C.RESET}   "
                f"in {C.BOLD}{toks['input']}{C.RESET}  "
                f"out {C.BOLD}{toks['output']}{C.RESET}"
            )
    else:
        ses_body = [f"{C.DIM}(no sessions){C.RESET}"]
    lines.extend(section(C.GREEN, "SESSIONS", ses_body))

    # USAGE — yellow
    if usage:
        t = usage["today"]
        use_body = [
            f"in {C.BOLD}{t['input']}{C.RESET}   {C.DIM}·{C.RESET}   "
            f"out {C.BOLD}{t['output']}{C.RESET}   {C.DIM}·{C.RESET}   "
            f"total {C.BOLD}{t['total']}{C.RESET}   {C.DIM}(today){C.RESET}"
        ]
    else:
        use_body = [f"{C.DIM}unavailable{C.RESET}"]
    lines.extend(section(C.YELLOW, "USAGE", use_body))

    # SERVICES — cyan
    if services:
        svc_body = [
            "   ".join(
                f"{service_dot(state)} {name}" for name, state in services["services"]
            )
        ]
    else:
        svc_body = [f"{C.DIM}unavailable{C.RESET}"]
    lines.extend(section(C.CYAN, "SERVICES", svc_body))

    # DOTFILES — blue
    if dotfiles:
        dot_body = []
        for name, info in dotfiles.items():
            behind = info["behind"]
            if behind is None:
                drift = f"  {C.DIM}(no upstream){C.RESET}"
            elif behind:
                drift = f"  ({C.YELLOW}{behind} behind{C.RESET})"
            else:
                drift = ""
            dot_body.append(
                f"{C.BOLD}{name:<14}{C.RESET} {C.DIM}{info['sha']}{C.RESET}{drift}"
            )
    else:
        dot_body = [f"{C.DIM}unavailable{C.RESET}"]
    lines.extend(section(C.BLUE, "DOTFILES", dot_body))

    # Trailing blank from last section leaves a nice bottom margin —
    # trim so the output ends right after DOTFILES.
    while lines and lines[-1] == "":
        lines.pop()
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
