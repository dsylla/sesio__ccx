"""ccxctl motd — ANSI-boxed login banner for the ccx coding station."""
from __future__ import annotations

import re
import shutil


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


def box_top(lt: str, rt: str) -> str:
    l = f"══ {lt} "
    r = f"══ {rt} "
    return f"{C.DIM}╔{l}{'═' * (LEFT_W - len(l))}╦{r}{'═' * (RIGHT_W - len(r))}╗{C.RESET}"


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
