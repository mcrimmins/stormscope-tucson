"""
Live StormScope Tucson nowcasts: run every 30 minutes and publish to web/data/live/.

Loads and compiles the model once, then loops:
  - picks the newest start time with data available (now - LATENCY, floored to 10 min)
  - runs the operational setup (Flash, padding 250, 6 members, 3 h)
  - writes overlays + JSON to web/data/live/<YYYYmmdd_HHMM>/ and web/data/live/index.json
  - keeps the last KEEP_RUNS runs, deletes older ones

    nohup uv run python run_live.py > live.log 2>&1 &
    uv run python run_live.py --once            # single run, for testing
    uv run python run_live.py --once --init 2026-08-05T21:00   # replay a past time
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import torch

import hindcast as hc
import export_web as ew

MEMBERS = 6
HOURS = 3
PADDING = 250
LATENCY_MIN = 20          # wait this long after a start time for GOES/MRMS/GLM to land on AWS
EVERY_MIN = 30
KEEP_RUNS = 12            # 6 hours of runs
LIVE = Path("web/data/live")


def latest_init(now=None, latency=LATENCY_MIN):
    now = now or datetime.now(timezone.utc)
    t = now - timedelta(minutes=latency)
    t = t.replace(minute=t.minute - t.minute % 10, second=0, microsecond=0, tzinfo=None)
    return np.datetime64(t)


def run_once(model, model_mrms, t0, device):
    stamp = str(t0)[:16].replace("-", "").replace("T", "_").replace(":", "")
    out = LIVE / stamp
    img = out / "img"
    img.mkdir(parents=True, exist_ok=True)
    print(f"=== live run {t0} UTC -> {out}", flush=True)
    t_start = time.time()

    (y, yc), (ym, ymc), mrms, radar_vars = hc.build_initial_state(model, model_mrms, t0, MEMBERS, device)
    ys, xs = hc.output_slices(model_mrms)
    valid = hc.to_np(model_mrms.valid_mask)[ys, xs].astype(bool)
    lat = hc.to_np(model_mrms.latitudes)[ys, xs]
    lon = hc.to_np(model_mrms.longitudes)[ys, xs]
    refc_i = list(model_mrms.variables).index("refc")
    glm_i = list(model_mrms.variables).index(model_mrms.glm_variables[0])

    n_steps = int(HOURS * 60 / hc.STEP_MIN)
    H, W = lat.shape
    refc = np.zeros((MEMBERS, n_steps, H, W), np.float32)
    glm = np.zeros((MEMBERS, n_steps, H, W), np.float32)
    leads = []
    with torch.no_grad():
        for s in range(n_steps):
            yp, ypc = model(y, yc)
            ymp, ympc = model_mrms.call_with_conditioning(ym, ymc, conditioning=y, conditioning_coords=yc)
            y, yc = model.next_input(yp, ypc, y, yc)
            ym, ymc = model_mrms.next_input(ymp, ympc, ym, ymc)
            refc[:, s] = ymp[:, 0, 0, refc_i][:, ys, xs].float().cpu().numpy()
            glm[:, s] = ymp[:, 0, 0, glm_i][:, ys, xs].float().cpu().numpy()
            leads.append(int(ympc["lead_time"][-1] / np.timedelta64(1, "m")))

    obs0 = hc.fetch_obs_refc(mrms, model_mrms, radar_vars, t0, ys, xs, device)
    newmask = ew.new_storm_mask(obs0)
    cities = ew.load_locations()

    # ---- export (same products and colors as the case-study viewer) ----
    lats, lons = ew.target_grid()
    regrid, _ = ew.build_regridder(lat, lon, lats, lons)
    city_ix = {c: (int(np.abs(lats - la).argmin()), int(np.abs(lons - lo).argmin()))
               for c, (la, lo) in cities.items()}
    points = {c: {"storm": [], "rain": [], "lightning": []} for c in cities}
    products = {k: [] for k in ["storm", "new", "rain", "strong", "lightning", "member"]}
    rel = f"data/live/{stamp}/img"
    for s, L in enumerate(leads):
        fields = {
            "storm": ew.nbhd_prob(refc[:, s], 35), "new": ew.nbhd_prob(refc[:, s], 35) * newmask,
            "rain": ew.nbhd_prob(refc[:, s], 20),
            "strong": ew.nbhd_prob(refc[:, s], 45), "lightning": ew.nbhd_prob(glm[:, s], ew.GLM_THRESH),
        }
        for k, f in fields.items():
            g = regrid(np.where(valid, f, np.nan))
            ew.save_png(g, img / f"{k}_{L:03d}.png", ew.PROB_COLORS, ew.PROB_BOUNDS)
            products[k].append(f"{rel}/{k}_{L:03d}.png")
            if k in points["Tucson"]:
                for c, (i, j) in city_ix.items():
                    v = g[i, j]
                    points[c][k].append(None if np.isnan(v) else round(float(v), 3))
        g = regrid(np.where(valid, refc[0, s], np.nan))
        ew.save_png(g, img / f"member_{L:03d}.png", ew.REFL_COLORS, ew.REFL_BOUNDS)
        products["member"].append(f"{rel}/member_{L:03d}.png")
    ew.save_png(regrid(np.where(valid, obs0, np.nan)), img / "obs_init.png", ew.REFL_COLORS, ew.REFL_BOUNDS)

    data = {
        "name": f"live_{stamp}", "init_utc": str(t0)[:16], "members": MEMBERS, "leads": leads,
        "valid_mst": [ew.mst_label(t0 + np.timedelta64(L, "m")) for L in leads],
        "bounds": [[hc.REGION[2], hc.REGION[1]], [hc.REGION[3], hc.REGION[1]],
                   [hc.REGION[3], hc.REGION[0]], [hc.REGION[2], hc.REGION[0]]],
        "products": products, "obs_init": f"{rel}/obs_init.png", "points": points,
        "cities": {c: [lo, la] for c, (la, lo) in cities.items()},
        "run_seconds": round(time.time() - t_start), "made_utc": datetime.now(timezone.utc).isoformat()[:19],
    }
    (out / "run.json").write_text(json.dumps(data))
    update_index()
    print(f"    done in {data['run_seconds']} s", flush=True)
    if Path("s3.env").exists():   # publish to the S3 website if configured
        r = subprocess.run(["bash", "sync_s3.sh", "live"], capture_output=True, text=True)
        print("    s3 sync:", "ok" if r.returncode == 0 else f"FAILED\n{r.stdout}{r.stderr}", flush=True)


def update_index():
    runs = sorted([p for p in LIVE.iterdir() if (p / "run.json").exists()], reverse=True)
    for old in runs[KEEP_RUNS:]:
        shutil.rmtree(old, ignore_errors=True)
    runs = runs[:KEEP_RUNS]
    (LIVE / "index.json").write_text(json.dumps({
        "updated_utc": datetime.now(timezone.utc).isoformat()[:19],
        "runs": [f"data/live/{p.name}/run.json" for p in runs],
    }))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="single run, then exit")
    ap.add_argument("--init", default=None, help="start time (UTC, ISO) for --once; default = latest")
    args = ap.parse_args()

    LIVE.mkdir(parents=True, exist_ok=True)
    device = torch.device("cuda")
    model, model_mrms = hc.load_models(device, hc.MODEL_NAME, False, PADDING)

    if args.once:
        t0 = np.datetime64(args.init) if args.init else latest_init()
        run_once(model, model_mrms, t0, device)
        return

    last = None
    while True:
        t0 = latest_init()
        # only run on the :00 / :30 start times
        if t0 != last and int(str(t0)[14:16]) % EVERY_MIN == 0:
            for back in (0, 10, 20):   # if data isn't there yet, fall back 10-20 min
                try:
                    run_once(model, model_mrms, t0 - np.timedelta64(back, "m"), device)
                    break
                except Exception as e:
                    print(f"!!! run {t0 - np.timedelta64(back, 'm')} failed: {e}", flush=True)
            last = t0
            torch.cuda.empty_cache()
        time.sleep(30)


if __name__ == "__main__":
    main()
