"""
Choose forecast start times for each storm day from NWS warnings + storm reports.

For every storm day in cases.yaml (grouped by local MST date), this:
  - downloads that day's warnings and local storm reports (cached, via nws_events)
  - places up to MAX_RUNS start times ~LEAD_MIN before each cluster of activity,
    so every run has warnings/reports inside its 3-hour window
  - keeps runs that already exist, and writes cases_runs.yaml (all runs, grouped by day)
  - prints the hindcast command for the runs that still need the GPU

    uv run python pick_inits.py
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

import nws_events as ne

REGION = (29.5, 35.5, -114.5, -107.5)
EXPERIMENT = Path("outputs/hindcast_final_p250_m6")
HOURS = 3
LEAD_MIN = 75          # start this long before the first event of a cluster
COVER_MIN = 165        # a run "covers" events up to 2 h 45 min after its start
MAX_RUNS = 4
MIN_GAP_MIN = 60       # don't add a run within an hour of an existing one
MST = timedelta(hours=-7)


def floor10(t: datetime) -> datetime:
    return t.replace(minute=t.minute - t.minute % 10, second=0, microsecond=0)


def day_events(day_key: str, local_date):
    """Warning issuance times (not watches) and report times, 5 am - midnight MST."""
    start = datetime(local_date.year, local_date.month, local_date.day, 5, tzinfo=timezone.utc) - MST
    end = start + timedelta(hours=19)
    warn = ne.fetch_warnings(f"day_{day_key}", start - timedelta(hours=6), end, REGION)
    lsr = ne.fetch_lsrs(f"day_{day_key}", start, end, REGION)
    times = [f["properties"]["iss"] for f in warn["features"]
             if not f["properties"]["key"].endswith(".A") and start.timestamp() <= f["properties"]["iss"] <= end.timestamp()]
    times += [f["properties"]["t"] for f in lsr["features"]]
    return sorted(datetime.fromtimestamp(t, timezone.utc) for t in times), len(warn["features"]), len(lsr["features"])


def pick(events, existing):
    inits = []
    i = 0
    while i < len(events) and len(inits) < MAX_RUNS:
        t0 = floor10(events[i] - timedelta(minutes=LEAD_MIN))
        if all(abs((t0 - e).total_seconds()) >= MIN_GAP_MIN * 60 for e in existing + inits):
            inits.append(t0)
        cover_end = t0 + timedelta(minutes=COVER_MIN)
        while i < len(events) and events[i] <= cover_end:
            i += 1
    return inits


def main():
    base = yaml.safe_load(open("cases.yaml"))["cases"]
    days = {}
    for c in base:
        t = datetime.fromisoformat(c["init"]).replace(tzinfo=timezone.utc)
        local = (t + MST).date()
        days.setdefault(local, []).append((c, t))

    runs, todo = [], []
    for local, cs in sorted(days.items()):
        key = local.isoformat()
        label = local.strftime("%b %-d, %Y")
        note = cs[0][0].get("note", "")
        events, n_w, n_l = day_events(key, local)
        existing = [t for _, t in cs]
        new = pick(events, existing) if events else []
        all_runs = [(c["name"], t, c.get("note", note)) for c, t in cs] + \
                   [(t.strftime("%Y-%m-%d_%H%Mz"), t, note) for t in new]
        all_runs.sort(key=lambda r: r[1])
        print(f"{label}: {n_w} warnings/advisories, {n_l} reports -> starts at "
              + ", ".join((t + MST).strftime("%-I:%M %p") for _, t, _ in all_runs) + " MST")
        for k, (name, t, n) in enumerate(all_runs, 1):
            runs.append({"name": name, "init": t.strftime("%Y-%m-%dT%H:%M"), "note": n,
                         "day": key, "day_label": label, "run": k, "runs_in_day": len(all_runs)})
            if not (EXPERIMENT / name / "forecast.zarr").exists():
                todo.append(name)

    yaml.safe_dump({"cases": runs}, open("cases_runs.yaml", "w"), sort_keys=False)
    hrs = len(todo) * 12 / 60
    print(f"\nwrote cases_runs.yaml: {len(runs)} runs on {len(days)} storm days; "
          f"{len(todo)} need the GPU (~{hrs:.1f} h)")
    if todo:
        print("\nnohup uv run python hindcast.py --cases cases_runs.yaml --members 6 --hours 3 "
              f"--padding 250 --tag final_p250_m6 \\\n  --only {','.join(todo)} > runs.log 2>&1 &")


if __name__ == "__main__":
    main()
