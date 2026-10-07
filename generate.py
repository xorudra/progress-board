#!/usr/bin/env python3
"""Progress board data generator (stdlib only).

Builds data.json from live local truth (git logs + LeakGuard's
PHASE_STATUS.md) and injects it into board.html between the
<!--DATA_START--> / <!--DATA_END--> markers, inside a
<script id="board-data" type="application/json"> block.

Run it any time: python3 generate.py
"""
import json
import re
import subprocess
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOME = Path.home()
TOKEN_FILE = HOME / ".siteguard-github-token"
SEP = "\x1f"

# ---------------------------------------------------------------- git helpers

def is_repo(path) -> bool:
    """True if git can read a log there (handles .git dirs, worktrees,
    and bare filter-repo dirs alike)."""
    if not path or not Path(path).exists():
        return False
    try:
        subprocess.run(["git", "-C", str(path), "log", "-1", "--format=%h"],
                       capture_output=True, timeout=15, check=True)
        return True
    except Exception:
        return False


def git_log(repo: Path, count: int):
    """Latest `count` commits from a local repo, or None if unavailable."""
    if not repo or not is_repo(repo):
        return None
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "log", f"-{count}",
             f"--format=%h{SEP}%s{SEP}%cI"],
            capture_output=True, text=True, timeout=30, check=True,
        ).stdout.strip()
    except Exception:
        return None
    commits = []
    for line in out.splitlines():
        parts = line.split(SEP)
        if len(parts) == 3:
            commits.append({"hash": parts[0], "subject": parts[1],
                            "date": parts[2]})
    return commits or None


def git_head_time(repo: Path):
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "log", "-1", "--format=%ct"],
            capture_output=True, text=True, timeout=15, check=True,
        ).stdout.strip()
        return out
    except Exception:
        return ""


def newest_local_repo(candidates):
    """Pick the candidate repo whose HEAD commit is newest."""
    best, best_time = None, ""
    for cand in candidates:
        if cand and is_repo(cand):
            t = git_head_time(cand)
            if t > best_time:
                best, best_time = cand, t
    return best


def github_commits(owner_repo: str, count: int):
    """Fallback: latest commits via the GitHub API (token never printed)."""
    try:
        token = TOKEN_FILE.read_text().strip()
    except Exception:
        return None
    req = urllib.request.Request(
        f"https://api.github.com/repos/{owner_repo}/commits?per_page={count}",
        headers={"Authorization": f"Bearer {token}",
                 "Accept": "application/vnd.github+json",
                 "User-Agent": "progress-board-generator"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    commits = []
    for c in data:
        commits.append({
            "hash": c["sha"][:7],
            "subject": c["commit"]["message"].splitlines()[0],
            "date": c["commit"]["committer"]["date"],
        })
    return commits or None


def commits_for(candidates, owner_repo, count):
    repo = newest_local_repo(candidates)
    if repo:
        got = git_log(repo, count)
        if got:
            return got, f"local {repo}"
    got = github_commits(owner_repo, count)
    if got:
        return got, f"github api {owner_repo}"
    return [], "none"

# ---------------------------------------------------------------- leakguard

P2_BATCHES = [
    ("P2-A", "Records", [80, 131, 132]),
    ("P2-B", "Performance", [115, 116]),
    ("P2-C", "Observability & alerts", [76, 77]),
    ("P2-D", "Cost", [66, 124]),
    ("P2-E", "Scan budget & data quality", [158, 160]),
    ("P2-F", "Broker depth", [111, 162, 31, 137, 166]),
    ("P2-G", "Removal depth", [153, 141, 119]),
    ("P2-H", "Surfaces", [125, 40, 96, 67]),
    ("P2-I", "Closeout", [175, 178]),
]


def leakguard_phase_data():
    text = (HOME / "workspace/leakguard/PHASE_STATUS.md").read_text()
    counts = {}
    m = re.search(r"## Counts\s*\n\s*\| Status \| Count \|.*?\n((?:\|.*\n)+)",
                  text)
    if m:
        for line in m.group(1).splitlines():
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) == 2 and cells[1].isdigit():
                counts[cells[0]] = int(cells[1])
    statuses = {}
    row_re = re.compile(r"^\| (\d+) — .*?\| P\d \| ([A-Z ]+?) \|", re.M)
    for num, status in row_re.findall(text):
        statuses[int(num)] = status.strip()
    return counts, statuses


def build_leakguard():
    commits, _src = commits_for([HOME / "workspace/leakguard"],
                                "xorudra/leakguard", 6)
    counts, statuses = leakguard_phase_data()
    total = counts.get("Total") or sum(
        v for k, v in counts.items() if k != "Total")
    done = counts.get("DONE", 0)

    batch_bits, p2_done, p2_total, building_seen = [], 0, 0, False
    for code, name, phases in P2_BATCHES:
        p2_total += len(phases)
        states = [statuses.get(p, "?") for p in phases]
        if all(s == "DONE" for s in states):
            state = "DONE"
            p2_done += len(phases)
        elif not building_seen:
            state = "BUILDING"
            building_seen = True
            p2_done += sum(1 for s in states if s == "DONE")
        else:
            state = "QUEUED"
        batch_bits.append(f"{code} {name}: {state}")
    counts_bit = " · ".join(
        f"{counts[k]} {k}" for k in
        ("DONE", "PARTIAL", "MISSING", "UNVERIFIED", "NOT APPLICABLE")
        if k in counts)
    status_line = (
        f"P2 completion program running — {p2_done}/{p2_total} P2 phases "
        f"closed · " + " · ".join(batch_bits) +
        f" · production commit 1387fc4 · scoreboard {counts_bit}")
    return {
        "slug": "leakguard",
        "name": "LeakGuard",
        "status_line": status_line,
        "progress": {"done": done, "total": total,
                     "label": f"{done} of {total} phases DONE"},
        "live_url": "https://leakguard-hh8e.onrender.com",
        "commits": commits,
    }

# ---------------------------------------------------------------- dsrclone

def build_dsrclone():
    repo = HOME / "workspace/dsrclone-rewrite"
    commits, _src = commits_for([repo], "xorudra/dsrclone", 6)
    subjects = []
    try:
        subjects = subprocess.run(
            ["git", "-C", str(repo), "log", "--format=%s"],
            capture_output=True, text=True, timeout=30,
            check=True).stdout.splitlines()
    except Exception:
        pass
    prompts = set()
    foundation = False
    for s in subjects:
        m = re.search(r"DSRclone Prompt (\d+)", s)
        if m:
            prompts.add(int(m.group(1)))
        if "foundation" in s.lower():
            foundation = True
    # Prompt-numbered phase commits + the foundation commit (Prompts 1+2).
    landed = len(prompts) + (2 if foundation else 0)
    latest = ""
    if commits:
        m = re.search(r"DSRclone Prompt (\d+)", commits[0]["subject"])
        if m:
            latest = f" · latest phase: Prompt {m.group(1)}"
    return {
        "slug": "dsrclone",
        "name": "DSRclone",
        "status_line": ("Private repo · VAPT platform build in progress"
                        + latest),
        "progress": {"done": landed, "total": 40,
                     "label": f"{landed} of 40 spec phases landed"},
        "live_url": None,
        "commits": commits,
    }

# ---------------------------------------------------------------- others

def build_nexus():
    cands = [HOME / "workspace/Nexus-Local", HOME / "workspace/nexus-local",
             HOME / "workspace/nexus-rewrite"]
    commits, _src = commits_for(cands, "xorudra/Nexus-Local", 6)
    return {
        "slug": "nexus-local",
        "name": "Nexus Local",
        "status_line": "Live · capability integration complete "
                       "(chat failover, image, voice, agent tools)",
        "progress": None,
        "live_url": "https://nexus-local.onrender.com",
        "commits": commits,
    }


def build_siteguard():
    cands = [HOME / "workspace/siteguard", HOME / "workspace/siteguard-rewrite"]
    commits, _src = commits_for(cands, "xorudra/siteguard", 3)
    return {
        "slug": "siteguard",
        "name": "SiteGuard",
        "status_line": "Live, not in active build",
        "progress": None,
        "live_url": "https://siteguard-vf9c.onrender.com",
        "commits": commits,
    }

# ---------------------------------------------------------------- main

def main():
    projects = [build_leakguard(), build_dsrclone(), build_nexus(),
                build_siteguard()]
    data = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "projects": projects,
    }
    (HERE / "data.json").write_text(json.dumps(data, indent=2) + "\n")

    board = HERE / "board.html"
    if board.exists():
        html = board.read_text()
        block = ('<script id="board-data" type="application/json">\n'
                 + json.dumps(data, indent=2) + "\n</script>")
        replacement = "<!--DATA_START-->\n" + block + "\n<!--DATA_END-->"
        new_html, n = re.subn(
            r"<!--DATA_START-->.*<!--DATA_END-->",
            lambda _m: replacement,  # literal: JSON has backslash escapes
            html, flags=re.S)
        if n:
            board.write_text(new_html)
            print("injected fresh data into board.html")
        else:
            print("WARNING: DATA markers not found in board.html",
                  file=sys.stderr)
    else:
        print("board.html not present yet — data.json written only")

    for p in projects:
        head = p["commits"][0] if p["commits"] else None
        print(f"{p['name']}: head={head['hash'] if head else 'NONE'} "
              f"commits={len(p['commits'])} "
              f"progress={p['progress']}")
    print(f"generated_at={data['generated_at']}")


if __name__ == "__main__":
    main()
