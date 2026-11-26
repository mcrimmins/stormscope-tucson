"""
Re-score saved hindcasts on the Tucson REGION box without rerunning the model.

Useful for full-domain (CONUS) runs: crops forecast.zarr to the REGION bounding
box, recomputes FSS / frequency bias, and redraws the comparison maps.

    uv run python rescore.py outputs/hindcast_conus_flash
    # writes <case>/scores_box.csv, <case>/box_compare_leadNNN.png,
    #        scores_box_all.csv and scores_box_summary.csv in that folder
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

from hindcast import compute_scores, plot_case, plot_fss, region_bbox


def rescore_case(case_dir: Path):
    ds = xr.open_zarr(case_dir / "forecast.zarr")
    ys, xs = region_bbox(ds.lat.values, ds.lon.values)
    d = ds.isel(y=ys, x=xs).load()

    name = case_dir.name
    t0 = np.datetime64(d.attrs["init_time"])
    leads = [int(v) for v in d.lead.values]
    fr = d.refc.values.astype(np.float32)
    obs = d.obs_refc.values
    obs0 = d.obs_refc_init.values
    valid = d.valid_mask.values.astype(bool)

    scores = compute_scores(name, fr, obs, obs0, valid, leads)
    scores.to_csv(case_dir / "scores_box.csv", index=False)

    out = case_dir / "box"
    out.mkdir(exist_ok=True)
    plot_case(name, t0, d.lat.values, d.lon.values, valid, fr, obs, leads, out)
    plot_fss(scores, name, out)
    print(f"{name}: rescored on {fr.shape[-2]}x{fr.shape[-1]} grid points")
    return scores


def main():
    root = Path(sys.argv[1])
    cases = sorted(p.parent for p in root.glob("*/forecast.zarr"))
    allsc = pd.concat([rescore_case(c) for c in cases])
    allsc.to_csv(root / "scores_box_all.csv", index=False)
    summary = (allsc.groupby(["thresh_dbz", "nbhd_km", "lead_min"])
               [["fss_stormscope", "fss_persistence", "freq_bias"]]
               .mean().reset_index())
    summary.to_csv(root / "scores_box_summary.csv", index=False)
    sel = summary[(summary.thresh_dbz == 35) & (summary.nbhd_km == 33)
                  & summary.lead_min.isin([30, 60, 120, 180, 240, 360])]
    print(sel.to_string(index=False))


if __name__ == "__main__":
    main()
