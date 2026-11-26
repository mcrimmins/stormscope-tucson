"""
StormScope hindcasts for the Tucson CWA (southern Arizona).

For each case in cases.yaml:
  1. Fetch GOES / MRMS / GLM initial conditions.
  2. Run an N-member StormScope Flash ensemble (default 16 members, 6 h).
  3. Fetch observed MRMS reflectivity at every valid time.
  4. Save forecast + obs to Zarr, compute FSS vs. persistence, and plot
     side-by-side maps (observed | P(>=35 dBZ) | one member).

Run on the ThinkStation:
    source cuda-env.sh   # only needed if anything has to (re)build
    uv run python hindcast.py                      # all cases
    uv run python hindcast.py --only 2024-07-25_21z --members 4 --hours 2

Model-loading and the forecast loop follow NVIDIA's earth2studio 0.20.0
StormScope GOES/MRMS example (Apache-2.0); see smoke_tucson.py.
"""

from __future__ import annotations

import argparse
import os
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import xarray as xr
import yaml
from scipy.ndimage import maximum_filter, uniform_filter
from tqdm import trange

from dotenv import load_dotenv

load_dotenv()

from earth2studio.data import GOES, MRMS, GOESGLMGrid, fetch_data  # noqa: E402
from earth2studio.models.px.stormscope import (  # noqa: E402
    StormScopeBase,
    StormScopeGOES,
    StormScopeMRMS,
)

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
REGION = (29.5, 35.5, -114.5, -107.5)  # (S, N, W, E) Tucson CWA + Sonora/Rim
PADDING = 25
MODEL_NAME = "3km_10min_flash"
STEP_MIN = 10

THRESHOLDS = [20, 35, 45]          # dBZ
NEIGHBORHOODS = [1, 5, 11]         # grid points (3 km): ~gridpoint, 15 km, 33 km
PROB_THRESH = 35                   # dBZ for the probability map
PROB_RADIUS_PTS = 7                # ~21 km box (~10 km radius) for neighborhood max
PLOT_LEADS_MIN = [60, 120, 180, 360]

GOES19_START = np.datetime64("2025-04-07")  # GOES-19 became GOES-East

OUT_ROOT = Path("outputs/hindcast")


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def to_np(a):
    return a.detach().cpu().numpy() if torch.is_tensor(a) else np.asarray(a)


def goes_satellite_for(t: np.datetime64) -> str:
    return "goes19" if t >= GOES19_START else "goes16"


def load_models(device):
    package = StormScopeBase.load_default_package()
    model = StormScopeGOES.load_model(
        package=package, conditioning_data_source=None,
        model_name=MODEL_NAME, amp=True, compile=True,
    ).to(device).eval()
    model_mrms = StormScopeMRMS.load_model(
        package=package, conditioning_data_source=GOES(),
        glm_data_source=GOESGLMGrid(satellite="east"),
        model_name=MODEL_NAME, amp=True, compile=True,
    ).to(device).eval()
    model = model.set_domain(*REGION, padding=PADDING)
    model_mrms = model_mrms.set_domain(*REGION, padding=PADDING)
    return model, model_mrms


def build_initial_state(model, model_mrms, t0, members, device):
    """Fetch GOES + MRMS + GLM for init time t0 and return batched states."""
    start = [t0]
    sat = goes_satellite_for(t0)
    goes = GOES(satellite=sat, scan_mode="C")
    goes_lat, goes_lon = GOES.grid(satellite=sat, scan_mode="C")
    model.build_input_interpolator(goes_lat, goes_lon)

    in_coords = model.input_coords()
    x, x_coords = fetch_data(
        goes, time=start, variable=np.array(in_coords["variable"]),
        lead_time=in_coords["lead_time"], device=device,
    )

    mrms = MRMS()
    mrms_in = model_mrms.input_coords()
    radar_vars = np.array(
        [v for v in model_mrms.variables if v not in set(model_mrms.glm_variables)]
    )
    x_radar, xc_radar = fetch_data(
        mrms, time=start, variable=radar_vars,
        lead_time=mrms_in["lead_time"], device=device,
    )
    model_mrms.build_input_interpolator(xc_radar["lat"], xc_radar["lon"])
    model_mrms.build_conditioning_interpolator(goes_lat, goes_lon)
    x_radar = model_mrms.input_interp(x_radar)

    glm_coords = mrms_in.copy()
    glm_coords["time"] = np.array(start)
    x_glm, _ = model_mrms.fetch_glm(glm_coords, device=device)

    x_mrms = torch.cat([x_radar, x_glm], dim=2).to(dtype=torch.float32)
    xc_mrms = xc_radar.copy()
    xc_mrms["variable"] = np.array(model_mrms.variables)
    del xc_mrms["lat"], xc_mrms["lon"]
    xc_mrms["y"] = model_mrms.y
    xc_mrms["x"] = model_mrms.x

    # batch dimension -> ensemble members
    x = x.unsqueeze(0).repeat(members, 1, 1, 1, 1, 1).to(torch.float32)
    x_coords["batch"] = np.arange(members)
    x_coords.move_to_end("batch", last=False)
    x_mrms = x_mrms.unsqueeze(0).repeat(members, 1, 1, 1, 1, 1)
    xc_mrms["batch"] = np.arange(members)
    xc_mrms.move_to_end("batch", last=False)

    return (x, x_coords), (x_mrms, xc_mrms), mrms, radar_vars


def fetch_obs_refc(mrms, model_mrms, radar_vars, t, ys, xs, device):
    """Observed MRMS composite reflectivity regridded to the cropped model grid."""
    xo, _ = fetch_data(
        mrms, time=[t], variable=radar_vars,
        lead_time=np.array([np.timedelta64(0, "m")]), device=device,
    )
    xo = model_mrms.input_interp(xo)
    i = list(radar_vars).index("refc")
    obs = xo[0, 0, i][ys, xs].float().cpu().numpy()
    # MRMS uses large negative sentinels for no coverage
    obs = np.where(np.isfinite(obs) & (obs > -500), obs, np.nan)
    return obs


def fss(f_frac, o_frac, mask):
    num = np.sum(((f_frac - o_frac) ** 2)[mask])
    den = np.sum((f_frac ** 2 + o_frac ** 2)[mask])
    return np.nan if den == 0 else 1.0 - num / den


def neighborhood_prob(members_field, thresh, size):
    """Fraction of members with >=thresh anywhere in a size x size box."""
    hits = members_field >= thresh                      # [M, H, W]
    nmax = np.stack([maximum_filter(h, size=size) for h in hits])
    return nmax.mean(axis=0)


# --------------------------------------------------------------------------
# Main per-case routine
# --------------------------------------------------------------------------
def run_case(case, model, model_mrms, members, hours, device):
    name = case["name"]
    t0 = np.datetime64(datetime.fromisoformat(case["init"]))
    out = OUT_ROOT / name
    out.mkdir(parents=True, exist_ok=True)
    n_steps = int(hours * 60 / STEP_MIN)
    print(f"\n=== {name}: init {t0} UTC, {members} members, {n_steps} steps ===")

    (y, yc), (ym, ymc), mrms, radar_vars = build_initial_state(
        model, model_mrms, t0, members, device
    )

    ys, xs = model_mrms.region_info.output_slices
    valid = to_np(model_mrms.valid_mask)[ys, xs].astype(bool)
    lat = to_np(model_mrms.latitudes)[ys, xs]
    lon = to_np(model_mrms.longitudes)[ys, xs]

    refc_i = list(model_mrms.variables).index("refc")
    glm_i = list(model_mrms.variables).index(model_mrms.glm_variables[0])
    ir_i = list(model.variables).index("abi13c")

    H, W = lat.shape
    f_refc = np.zeros((members, n_steps, H, W), np.float16)
    f_glm = np.zeros((members, n_steps, H, W), np.float16)
    f_ir = np.zeros((members, n_steps, H, W), np.float16)
    leads = []

    for s in trange(n_steps, desc=f"{name} forecast"):
        yp, ypc = model(y, yc)
        ymp, ympc = model_mrms.call_with_conditioning(
            ym, ymc, conditioning=y, conditioning_coords=yc
        )
        y, yc = model.next_input(yp, ypc, y, yc)
        ym, ymc = model_mrms.next_input(ymp, ympc, ym, ymc)

        f_refc[:, s] = ymp[:, 0, 0, refc_i][:, ys, xs].float().cpu().numpy()
        f_glm[:, s] = ymp[:, 0, 0, glm_i][:, ys, xs].float().cpu().numpy()
        f_ir[:, s] = yp[:, 0, 0, ir_i][:, ys, xs].float().cpu().numpy()
        leads.append(int(ympc["lead_time"][-1] / np.timedelta64(1, "m")))

    peak = torch.cuda.max_memory_allocated() / 2**30
    print(f"peak VRAM so far: {peak:.1f} GiB")

    # ---- observations ----------------------------------------------------
    print("Fetching observed MRMS for verification...")
    obs0 = fetch_obs_refc(mrms, model_mrms, radar_vars, t0, ys, xs, device)
    obs = np.full((n_steps, H, W), np.nan, np.float32)
    for s, L in enumerate(leads):
        try:
            obs[s] = fetch_obs_refc(
                mrms, model_mrms, radar_vars,
                t0 + np.timedelta64(L, "m"), ys, xs, device,
            )
        except Exception as e:  # missing file etc.
            print(f"  obs missing at +{L} min: {e}")

    # ---- save ------------------------------------------------------------
    ds = xr.Dataset(
        {
            "refc": (("member", "lead", "y", "x"), f_refc),
            "glm_density": (("member", "lead", "y", "x"), f_glm),
            "abi13c": (("member", "lead", "y", "x"), f_ir),
            "obs_refc": (("lead", "y", "x"), obs),
            "obs_refc_init": (("y", "x"), obs0),
            "valid_mask": (("y", "x"), valid),
        },
        coords={
            "member": np.arange(members),
            "lead": ("lead", np.array(leads), {"units": "minutes"}),
            "lat": (("y", "x"), lat),
            "lon": (("y", "x"), lon),
        },
        attrs={"init_time": str(t0), "region": str(REGION), "model": MODEL_NAME},
    )
    ds.to_zarr(out / "forecast.zarr", mode="w")

    # ---- scores ----------------------------------------------------------
    rows = []
    fr = f_refc.astype(np.float32)
    for s, L in enumerate(leads):
        o = obs[s]
        mask = valid & np.isfinite(o) & np.isfinite(obs0)
        if mask.sum() == 0:
            continue
        o_f = np.nan_to_num(o)
        p_f = np.nan_to_num(obs0)
        for T in THRESHOLDS:
            ens_exceed = (fr[:, s] >= T).mean(axis=0)
            for n in NEIGHBORHOODS:
                of = uniform_filter((o_f >= T).astype(np.float32), size=n)
                ff = uniform_filter(ens_exceed, size=n)
                pf = uniform_filter((p_f >= T).astype(np.float32), size=n)
                rows.append(dict(
                    case=name, lead_min=L, thresh_dbz=T, nbhd_km=n * 3,
                    fss_stormscope=fss(ff, of, mask),
                    fss_persistence=fss(pf, of, mask),
                    obs_frac=float((o_f >= T)[mask].mean()),
                    fcst_frac=float(ens_exceed[mask].mean()),
                    freq_bias=(float(ens_exceed[mask].mean())
                               / max(float((o_f >= T)[mask].mean()), 1e-9)),
                ))
    scores = pd.DataFrame(rows)
    scores.to_csv(out / "scores.csv", index=False)

    plot_case(name, t0, lat, lon, valid, fr, obs, leads, out)
    plot_fss(scores, name, out)
    return scores


# --------------------------------------------------------------------------
# Plotting
# --------------------------------------------------------------------------
def _ax(fig, pos, proj):
    import cartopy.feature as cfeature
    ax = fig.add_subplot(1, 3, pos, projection=proj)
    ax.coastlines(color="black", linewidth=0.6)
    ax.add_feature(cfeature.STATES, edgecolor="black", linewidth=0.5)
    ax.add_feature(cfeature.BORDERS, edgecolor="black", linewidth=0.6)
    return ax


def plot_case(name, t0, lat, lon, valid, fr, obs, leads, out):
    import cartopy.crs as ccrs
    import matplotlib.pyplot as plt

    proj = ccrs.LambertConformal(
        central_longitude=262.5, central_latitude=38.5,
        standard_parallels=(38.5, 38.5),
        globe=ccrs.Globe(semimajor_axis=6371229, semiminor_axis=6371229),
    )
    pc = ccrs.PlateCarree()
    for L in PLOT_LEADS_MIN:
        if L not in leads:
            continue
        s = leads.index(L)
        valid_t = t0 + np.timedelta64(L, "m")
        prob = neighborhood_prob(fr[:, s], PROB_THRESH, PROB_RADIUS_PTS)
        prob = np.where(valid, prob, np.nan)
        o = np.where(valid & (obs[s] > 0), obs[s], np.nan)
        m0 = np.where(valid & (fr[0, s] > 0), fr[0, s], np.nan)

        fig = plt.figure(figsize=(16, 5.5))
        for pos, field, title, kw in [
            (1, o, "Observed MRMS refc", dict(cmap="inferno", vmin=0, vmax=60)),
            (2, prob, f"P(≥{PROB_THRESH} dBZ within ~10 km)",
             dict(cmap="YlGnBu", vmin=0, vmax=1)),
            (3, m0, "Member 0 refc", dict(cmap="inferno", vmin=0, vmax=60)),
        ]:
            ax = _ax(fig, pos, proj)
            im = ax.pcolormesh(lon, lat, field, transform=pc, shading="auto", **kw)
            ax.set_title(title)
            plt.colorbar(im, ax=ax, orientation="horizontal", pad=0.04, shrink=0.8)
        fmt = lambda t: str(t)[:16].replace("T", " ")
        fig.suptitle(
            f"{name}  |  init {fmt(t0)} UTC  |  lead +{L//60}h{L%60:02d}  "
            f"|  valid {fmt(valid_t)} UTC"
        )
        fig.savefig(out / f"compare_lead{L:03d}.png", dpi=150, bbox_inches="tight")
        plt.close(fig)


def plot_fss(scores, name, out, thresh=35, nbhd_km=33):
    import matplotlib.pyplot as plt
    d = scores[(scores.thresh_dbz == thresh) & (scores.nbhd_km == nbhd_km)]
    if d.empty:
        return
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.plot(d.lead_min / 60, d.fss_stormscope, label="StormScope ensemble", lw=2)
    ax.plot(d.lead_min / 60, d.fss_persistence, label="Persistence", lw=2, ls="--")
    ax.set_xlabel("Lead time (h)")
    ax.set_ylabel(f"FSS (≥{thresh} dBZ, {nbhd_km} km)")
    ax.set_ylim(0, 1)
    ax.grid(alpha=0.3)
    ax.legend()
    ax.set_title(name)
    fig.savefig(out / "fss_vs_lead.png", dpi=150, bbox_inches="tight")
    plt.close(fig)


# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default="cases.yaml")
    ap.add_argument("--only", default=None, help="run a single case by name")
    ap.add_argument("--members", type=int, default=16)
    ap.add_argument("--hours", type=float, default=6)
    args = ap.parse_args()

    cases = yaml.safe_load(open(args.cases))["cases"]
    if args.only:
        cases = [c for c in cases if c["name"] == args.only]

    device = torch.device("cuda")
    model, model_mrms = load_models(device)

    all_scores = []
    for case in cases:
        try:
            all_scores.append(
                run_case(case, model, model_mrms, args.members, args.hours, device)
            )
        except Exception as e:
            print(f"!!! case {case['name']} failed: {e}")
        torch.cuda.empty_cache()

    if all_scores:
        allsc = pd.concat(all_scores)
        OUT_ROOT.mkdir(parents=True, exist_ok=True)
        allsc.to_csv(OUT_ROOT / "scores_all.csv", index=False)
        summary = (
            allsc.groupby(["thresh_dbz", "nbhd_km", "lead_min"])
            [["fss_stormscope", "fss_persistence", "freq_bias"]].mean().reset_index()
        )
        summary.to_csv(OUT_ROOT / "scores_summary.csv", index=False)
        print("\nMean FSS (>=35 dBZ, 33 km) by lead:")
        print(summary[(summary.thresh_dbz == 35) & (summary.nbhd_km == 33)]
              .to_string(index=False))


if __name__ == "__main__":
    main()
