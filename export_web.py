"""
Export hindcast forecasts to web-ready files for the StormScope Tucson viewer.

For every case folder under an experiment (default: outputs/hindcast_final_p250_m6):
  - regrids each 10-min step to a regular lat/lon grid over REGION
  - writes transparent PNG overlays: storm / rain / strong-core / lightning
    probabilities, one ensemble member, and observed MRMS reflectivity
  - writes web/data/<case>.json with point timelines and verification scores
  - writes web/data/cases.json (index built from cases.yaml)

    uv run python export_web.py                       # default experiment
    uv run python export_web.py outputs/hindcast_xyz  # another experiment
    cd web && uv run python -m http.server 8000       # then open http://<host>:8000
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import xarray as xr  # noqa: E402
import yaml  # noqa: E402
from matplotlib.colors import ListedColormap, BoundaryNorm  # noqa: E402
from scipy.ndimage import maximum_filter  # noqa: E402
from scipy.spatial import cKDTree  # noqa: E402

REGION = (29.5, 35.5, -114.5, -107.5)       # S, N, W, E
RES = 0.025                                 # output grid, degrees (~2.5 km)
NBHD = 7                                    # ~21 km box (~10 km radius) neighborhood
GLM_THRESH = 1.0                            # lightning threshold in model glm_density units
                                            # (tune after checking the printed exceedance %)
MST = np.timedelta64(-7, "h")

CITIES = {
    "Tucson": (32.22, -110.97), "Oro Valley": (32.39, -110.97),
    "Marana": (32.44, -111.22), "Vail": (32.05, -110.71),
    "Sierra Vista": (31.55, -110.30), "Nogales": (31.34, -110.93),
    "Douglas": (31.34, -109.55), "Benson": (31.97, -110.29),
    "Willcox": (32.25, -109.83), "Safford": (32.83, -109.71),
    "Casa Grande": (32.88, -111.76), "Sells": (31.91, -111.88),
    "Ajo": (32.37, -112.86), "Phoenix": (33.45, -112.07),
}

def load_locations(path="locations.yaml"):
    """Places for timelines/timing tables; edit locations.yaml to add burn scars, washes, venues."""
    try:
        locs = yaml.safe_load(open(path))["locations"]
        return {l["name"]: (float(l["lat"]), float(l["lon"])) for l in locs}
    except FileNotFoundError:
        return CITIES


NEW_RADIUS_PTS = 21   # ~63 km box (~30 km radius): "new" = no >=35 dBZ this close at start


def new_storm_mask(obs0):
    """True where no storm (>=35 dBZ) was observed within ~30 km at the start time."""
    return ~maximum_filter(np.nan_to_num(obs0) >= 35, size=NEW_RADIUS_PTS)


# probability colors (match the viewer legend): transparent below 10%
PROB_COLORS = ["#17344D", "#1F5784", "#2E7BBE", "#5FA9E6", "#A9D6F7", "#F2A65A"]
PROB_BOUNDS = [0.1, 0.2, 0.35, 0.5, 0.7, 0.9, 1.01]
# NWS-style reflectivity colors from 15 dBZ
REFL_COLORS = ["#04E9E7", "#019FF4", "#0300F4", "#02FD02", "#01C501", "#008E00",
               "#FDF802", "#E5BC00", "#FD9500", "#FD0000", "#D40000", "#BC0000",
               "#F800FD", "#9854C6"]
REFL_BOUNDS = list(range(15, 85, 5))


def target_grid():
    s, n, w, e = REGION
    lats = np.arange(s, n + RES / 2, RES)
    lons = np.arange(w, e + RES / 2, RES)
    return lats, lons


def build_regridder(src_lat, src_lon, lats, lons):
    src_lon = np.where(src_lon > 180, src_lon - 360, src_lon)
    c = np.cos(np.deg2rad(32.5))
    tree = cKDTree(np.c_[src_lat.ravel(), src_lon.ravel() * c])
    LON, LAT = np.meshgrid(lons, lats)
    dist, idx = tree.query(np.c_[LAT.ravel(), LON.ravel() * c])
    inside = (dist < 0.06).reshape(LAT.shape)
    idx = idx.reshape(LAT.shape)

    def regrid(field):
        out = field.ravel()[idx].astype(np.float32)
        out[~inside] = np.nan
        return out
    return regrid, idx


def save_png(arr, path, colors, bounds):
    cmap = ListedColormap(colors)
    cmap.set_under((0, 0, 0, 0))
    cmap.set_bad((0, 0, 0, 0))
    norm = BoundaryNorm(bounds, cmap.N)
    rgba = cmap(norm(np.ma.masked_invalid(arr)))
    rgba[np.isnan(arr) | (arr < bounds[0]), 3] = 0
    rgba[rgba[..., 3] > 0, 3] = 0.85
    plt.imsave(path, rgba, origin="lower")


def nbhd_prob(members, thresh):
    hits = members >= thresh
    return np.stack([maximum_filter(h, size=NBHD) for h in hits]).mean(axis=0)


def mst_label(t):
    t = pd.Timestamp(t + MST)
    return t.strftime("%-I:%M %p").lower()


def export_case(case_dir: Path, meta: dict, out: Path):
    name = case_dir.name
    ds = xr.open_zarr(case_dir / "forecast.zarr").load()
    t0 = np.datetime64(ds.attrs["init_time"])
    leads = [int(v) for v in ds.lead.values]
    lats, lons = target_grid()
    regrid, _ = build_regridder(ds.lat.values, ds.lon.values, lats, lons)
    valid = ds.valid_mask.values.astype(bool)

    img = out / "img" / name
    img.mkdir(parents=True, exist_ok=True)

    refc = ds.refc.values.astype(np.float32)          # member, lead, y, x
    glm = ds.glm_density.values.astype(np.float32)
    obs = ds.obs_refc.values
    newmask = new_storm_mask(ds.obs_refc_init.values)
    cities = load_locations()

    # city -> nearest target-grid index
    city_ix = {c: (int(np.abs(lats - la).argmin()), int(np.abs(lons - lo).argmin()))
               for c, (la, lo) in cities.items()}
    points = {c: {"storm": [], "rain": [], "lightning": [], "obs_storm": []} for c in cities}
    products = {k: [] for k in ["storm", "new", "rain", "strong", "lightning", "member", "obs"]}
    glm_frac = []
    storm_grids = []

    for s, L in enumerate(leads):
        fields = {
            "storm": nbhd_prob(refc[:, s], 35),
            "new": nbhd_prob(refc[:, s], 35) * newmask,
            "rain": nbhd_prob(refc[:, s], 20),
            "strong": nbhd_prob(refc[:, s], 45),
            "lightning": nbhd_prob(glm[:, s], GLM_THRESH),
        }
        glm_frac.append(float((glm[:, s] >= GLM_THRESH)[:, valid].mean()))
        for k, f in fields.items():
            g = regrid(np.where(valid, f, np.nan))
            fn = f"{k}_{L:03d}.png"
            save_png(g, img / fn, PROB_COLORS, PROB_BOUNDS)
            products[k].append(f"data/img/{name}/{fn}")
            if k == "storm":
                storm_grids.append(g)
            if k in ("storm", "rain", "lightning"):
                for c, (i, j) in city_ix.items():
                    v = g[i, j]
                    points[c][k].append(None if np.isnan(v) else round(float(v), 3))
        for k, f in [("member", refc[0, s]), ("obs", obs[s])]:
            g = regrid(np.where(valid, f, np.nan))
            fn = f"{k}_{L:03d}.png"
            save_png(g, img / fn, REFL_COLORS, REFL_BOUNDS)
            products[k].append(f"data/img/{name}/{fn}")
        obs_n = regrid(np.where(valid, maximum_filter(np.nan_to_num(obs[s]) >= 35, size=NBHD), np.nan))
        for c, (i, j) in city_ix.items():
            v = obs_n[i, j]
            points[c]["obs_storm"].append(None if np.isnan(v) else int(v > 0.5))

    sc = pd.read_csv(case_dir / "scores.csv")
    def series(T, col):
        d = sc[(sc.thresh_dbz == T) & (sc.nbhd_km == 33)].set_index("lead_min")
        return [None if pd.isna(d[col].get(L)) else round(float(d[col].get(L)), 3) for L in leads]

    data = {
        "name": name, "init_utc": str(t0)[:16], "note": meta.get("note", ""),
        "members": int(refc.shape[0]),
        "leads": leads,
        "valid_mst": [mst_label(t0 + np.timedelta64(L, "m")) for L in leads],
        "bounds": [[REGION[2], REGION[1]], [REGION[3], REGION[1]],
                   [REGION[3], REGION[0]], [REGION[2], REGION[0]]],
        "products": products,
        "points": points,
        "cities": {c: [lo, la] for c, (la, lo) in cities.items()},
        "scores": {
            "fss35": series(35, "fss_stormscope"), "pers35": series(35, "fss_persistence"),
            "fss20": series(20, "fss_stormscope"), "pers20": series(20, "fss_persistence"),
            "bias35": series(35, "freq_bias"), "bias20": series(20, "freq_bias"),
        },
    }
    # NWS warnings/advisories + local storm reports (IEM archive) and verification
    try:
        import nws_events
        from datetime import datetime as _dt, timezone as _tz
        t_init = _dt.fromisoformat(str(t0)[:19]).replace(tzinfo=_tz.utc)
        ev = nws_events.case_events(name, t_init, leads[-1] / 60, REGION,
                                    np.stack(storm_grids), leads, lats, lons)
        (out / f"{name}_nws.json").write_text(json.dumps(ev))
        data["nws"] = f"data/{name}_nws.json"
        v = ev["verify"]["summary"]
        print(f"    NWS: {len(ev['warnings']['features'])} warnings/advisories, "
              f"{len(ev['lsrs']['features'])} reports; in window: {v['warnings_ge50']}/{v['warnings']} "
              f"warnings and {v['reports_ge50']}/{v['reports']} reports had >=50% forecast storm chance")
    except Exception as e:  # network or parsing problem: export the case without NWS layers
        print(f"    NWS events skipped: {e}")
        data["nws"] = None
    (out / f"{name}.json").write_text(json.dumps(data))
    print(f"{name}: {len(leads)} steps exported; lightning exceedance "
          f"{100*np.mean(glm_frac):.2f}% of points (GLM_THRESH={GLM_THRESH})")
    return {"name": name, "init_utc": data["init_utc"], "note": data["note"],
            "init_mst": pd.Timestamp(t0 + MST).strftime("%b %-d, %Y %-I %p MST")}


def main():
    exp = Path(sys.argv[1] if len(sys.argv) > 1 else "outputs/hindcast_final_p250_m6")
    out = Path("web/data")
    out.mkdir(parents=True, exist_ok=True)
    meta = {c["name"]: c for c in yaml.safe_load(open("cases.yaml"))["cases"]}
    index = []
    for case_dir in sorted(p.parent for p in exp.glob("*/forecast.zarr")):
        index.append(export_case(case_dir, meta.get(case_dir.name, {}), out))
    (out / "cases.json").write_text(json.dumps({"experiment": exp.name, "cases": index}))
    print(f"wrote {len(index)} cases to {out}/")


if __name__ == "__main__":
    main()
