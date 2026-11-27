"""
NWS warnings/advisories and Local Storm Reports for case studies (NWS Tucson + Phoenix),
from the Iowa Environmental Mesonet (IEM) archive, plus verification against forecasts.

Used by export_web.py. Raw downloads are cached under outputs/nws_cache/.
Note: IEM notes its LSR archive is not complete or official.
"""

from __future__ import annotations

import io
import json
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import requests
import shapefile  # pyshp
from matplotlib.path import Path as MplPath
from shapely.geometry import box, mapping, shape
from shapely.ops import unary_union

IEM = "https://mesonet.agron.iastate.edu"
WFOS = ["TWC", "PSR"]
CACHE = Path("outputs/nws_cache")

# phenomena.significance pairs to fetch, with display names and NWS-style colors
PRODUCTS = {
    "SV.W": ("Severe Thunderstorm Warning", "#FFA500"),
    "TO.W": ("Tornado Warning", "#FF0000"),
    "FF.W": ("Flash Flood Warning", "#8B0000"),
    "FA.W": ("Flood Warning", "#00FF00"),
    "FA.Y": ("Flood Advisory", "#00FF7F"),
    "DS.W": ("Dust Storm Warning", "#FFE4C4"),
    "DS.Y": ("Dust Advisory", "#BDB76B"),
    "FF.A": ("Flash Flood Watch", "#2E8B57"),
    "SV.A": ("Severe Thunderstorm Watch", "#DB7093"),
}

LSR_GROUPS = [  # (keyword in type text, group, color)
    ("FLASH FLOOD", "Flash flood", "#4FA3FF"), ("FLOOD", "Flood", "#4FA3FF"),
    ("RAIN", "Heavy rain", "#7FE3F0"), ("HAIL", "Hail", "#7CFC00"),
    ("DUST", "Dust storm", "#E8C27A"), ("WND", "Wind", "#FFD84D"),
    ("WIND", "Wind", "#FFD84D"), ("LIGHTNING", "Lightning", "#C79BFF"),
    ("TORNADO", "Tornado", "#FF4D4D"), ("FUNNEL", "Funnel cloud", "#FF4D4D"),
]


def _iso(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%MZ")


def _parse(s) -> datetime | None:
    if s is None:
        return None
    s = str(s).strip()
    for fmt in ("%Y%m%d%H%M", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%MZ", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def _get(url, params, cache_file: Path, binary=False):
    CACHE.mkdir(parents=True, exist_ok=True)
    if cache_file.exists():
        return cache_file.read_bytes() if binary else cache_file.read_text()
    r = requests.get(url, params=params, timeout=120)
    r.raise_for_status()
    if binary:
        cache_file.write_bytes(r.content)
        return r.content
    cache_file.write_text(r.text)
    return r.text


def fetch_warnings(name, t0: datetime, t1: datetime, region):
    """Warnings/advisories/watches issued between t0 and t1 for TWC+PSR, clipped to region."""
    ph, sig = zip(*[k.split(".") for k in PRODUCTS])
    params = [("accept", "shapefile"), ("sts", _iso(t0)), ("ets", _iso(t1)), ("timeopt", "1"),
              ("phenomena", ",".join(ph)), ("significance", ",".join(sig))]
    params += [("wfo[]", w) for w in WFOS]
    raw = _get(f"{IEM}/cgi-bin/request/gis/watchwarn.py", params, CACHE / f"{name}_wwa.zip", binary=True)
    s, n, w, e = region
    clip = box(w, s, e, n)
    # Collect every record, then build ONE feature per event:
    #  - storm-based events (GTYPE "P"): the warning polygon only, never the county records
    #  - county/zone-based events (watches): the union of all their counties/zones
    rows = []
    with tempfile.TemporaryDirectory() as td:
        zipfile.ZipFile(io.BytesIO(raw)).extractall(td)
        shp = next(Path(td).glob("*.shp"), None)
        if shp is None:
            return {"type": "FeatureCollection", "features": []}
        for sr in shapefile.Reader(str(shp)).shapeRecords():
            rec = {k.upper(): v for k, v in sr.record.as_dict().items()}
            key = f"{rec.get('PHENOM')}.{rec.get('SIG')}"
            if key not in PRODUCTS or not sr.shape.points:
                continue
            iss = _parse(rec.get("INIT_ISS") or rec.get("ISSUED"))
            exp = _parse(rec.get("EXPIRED") or rec.get("INIT_EXP"))
            if iss is None or exp is None:
                continue
            geom = shape(sr.shape.__geo_interface__)
            if not geom.is_valid:
                geom = geom.buffer(0)
            rows.append({"key": key, "wfo": rec.get("WFO"), "etn": rec.get("ETN"),
                         "gtype": str(rec.get("GTYPE") or "").upper(), "geom": geom,
                         "iss": iss, "exp": exp, "status": rec.get("STATUS")})

    events = {}
    for r in rows:
        # SPC watches share one number across offices; everything else is per office
        ek = (r["key"], r["etn"]) if r["key"].endswith(".A") else (r["key"], r["wfo"], r["etn"])
        events.setdefault(ek, []).append(r)

    out = []
    for ek, rs in events.items():
        polys = [r for r in rs if r["gtype"] == "P"]
        if polys:   # storm-based: initial polygon (earliest issued record)
            r0 = min(polys, key=lambda r: r["iss"])
            geom, iss, exp = r0["geom"], r0["iss"], max(r["exp"] for r in polys)
        else:       # county/zone-based: merge all counties/zones of the event
            geom = unary_union([r["geom"] for r in rs])
            iss, exp = min(r["iss"] for r in rs), max(r["exp"] for r in rs)
        geom = geom.intersection(clip).simplify(0.003)
        if geom.is_empty:
            continue
        r0 = rs[0]
        label, color = PRODUCTS[r0["key"]]
        wfos = sorted({r["wfo"] for r in rs if r["wfo"]})
        out.append({"type": "Feature", "geometry": mapping(geom), "properties": {
            "key": r0["key"], "label": label, "color": color, "wfo": "/".join(wfos),
            "etn": r0["etn"], "gtype": "P" if polys else "C",
            "issued": iss.isoformat()[:16] + "Z", "expires": exp.isoformat()[:16] + "Z",
            "iss": int(iss.timestamp()), "exp": int(exp.timestamp()),
        }})
    out.sort(key=lambda f: f["properties"]["iss"])
    return {"type": "FeatureCollection", "features": out}


def fetch_lsrs(name, t0: datetime, t1: datetime, region):
    params = {"wfos": ",".join(WFOS), "sts": _iso(t0), "ets": _iso(t1)}
    txt = _get(f"{IEM}/geojson/lsr.geojson", params, CACHE / f"{name}_lsr.geojson")
    s, n, w, e = region
    out = []
    for f in json.loads(txt).get("features", []):
        p = f.get("properties", {})
        lon, lat = f["geometry"]["coordinates"][:2]
        if not (s <= lat <= n and w <= lon <= e):
            continue
        t = _parse(p.get("valid"))
        if t is None:
            continue
        tt = (p.get("typetext") or p.get("type") or "").upper()
        grp, col = next(((g, c) for k, g, c in LSR_GROUPS if k in tt), ("Other", "#E6EDF2"))
        mag = p.get("magnitude")
        out.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]},
                    "properties": {"valid": t.isoformat()[:16] + "Z", "t": int(t.timestamp()),
                                   "typetext": p.get("typetext"), "group": grp, "color": col,
                                   "magnitude": None if mag in (None, "") else mag, "unit": p.get("unit"),
                                   "city": p.get("city"), "county": p.get("county"),
                                   "source": p.get("source"), "remark": p.get("remark")}})
    return {"type": "FeatureCollection", "features": sorted(out, key=lambda f: f["properties"]["t"])}


def _poly_mask(geom, lats, lons):
    LON, LAT = np.meshgrid(lons, lats)
    pts = np.c_[LON.ravel(), LAT.ravel()]
    mask = np.zeros(LON.size, bool)
    polys = getattr(geom, "geoms", [geom])
    for p in polys:
        if p.geom_type != "Polygon":
            continue
        inside = MplPath(np.asarray(p.exterior.coords)).contains_points(pts)
        for hole in p.interiors:
            inside &= ~MplPath(np.asarray(hole.coords)).contains_points(pts)
        mask |= inside
    return mask.reshape(LON.shape)


def verify(warn_fc, lsr_fc, storm_grids, leads, t_init: datetime, lats, lons):
    """Compare warnings issued and storms reported during the forecast window with the
    forecast storm chance (>=35 dBZ within ~10 km) from the forecast started at t_init.

    storm_grids: [n_leads, ny, nx] storm-chance on the regular lat/lon grid (NaN outside data)."""
    lead_t = np.array([t_init.timestamp() + 60 * L for L in leads])
    t_end = lead_t[-1]
    wrows, lrows = [], []
    for f in warn_fc["features"]:
        p = f["properties"]
        if p["key"].endswith(".A"):
            continue
        if not (t_init.timestamp() < p["iss"] <= t_end):
            wrows.append({"label": p["label"], "key": p["key"], "wfo": p["wfo"], "issued": p["issued"],
                          "in_window": False, "before": p["iss"] <= t_init.timestamp()})
            continue
        m = _poly_mask(shape(f["geometry"]), lats, lons)
        if not m.any():
            continue
        i = int(np.abs(lead_t - p["iss"]).argmin())
        inside = np.nan_to_num(storm_grids[:, m])          # [n_leads, n_points]
        at_issue = float(inside[i].max())
        j1 = int(np.searchsorted(lead_t, p["exp"], side="right"))
        during = float(inside[i:max(j1, i + 1)].max())
        ge50 = np.where(inside.max(axis=1) >= 0.5)[0]
        first50 = int(leads[ge50[0]]) if len(ge50) else None
        wrows.append({"label": p["label"], "key": p["key"], "wfo": p["wfo"], "issued": p["issued"],
                      "in_window": True, "lead_at_issue": int(leads[i]), "p_at_issue": round(at_issue, 2),
                      "p_during": round(during, 2), "first_ge50_lead": first50,
                      "signal_before_issue_min": (None if first50 is None
                                                  else int(round((p["iss"] - (t_init.timestamp() + 60 * first50)) / 60)))})
    for f in lsr_fc["features"]:
        p = f["properties"]
        if not (t_init.timestamp() < p["t"] <= t_end + 600):
            lrows.append({"valid": p["valid"], "group": p["group"], "typetext": p["typetext"],
                          "city": p["city"], "in_window": False, "before": p["t"] <= t_init.timestamp()})
            continue
        lon, lat = f["geometry"]["coordinates"]
        yi, xi = int(np.abs(lats - lat).argmin()), int(np.abs(lons - lon).argmin())
        near = np.abs(lead_t - p["t"]) <= 20 * 60       # +/- 20 minutes
        vals = np.nan_to_num(storm_grids[near, yi, xi]) if near.any() else np.array([0.0])
        lrows.append({"valid": p["valid"], "group": p["group"], "typetext": p["typetext"],
                      "city": p["city"], "in_window": True, "p_near": round(float(vals.max()), 2),
                      "lead": int(leads[int(np.abs(lead_t - p['t']).argmin())])})
    win_w = [r for r in wrows if r["in_window"]]
    win_l = [r for r in lrows if r["in_window"]]
    summ = {
        "warnings": len(win_w), "warnings_ge50": sum(r["p_during"] >= 0.5 for r in win_w),
        "reports": len(win_l), "reports_ge50": sum(r["p_near"] >= 0.5 for r in win_l),
        "warnings_day": len(wrows), "reports_day": len(lrows),
    }
    return {"summary": summ, "warnings": wrows, "reports": lrows}


def storm_day(t_init: datetime, t_end: datetime):
    """Noon to midnight MST of the local day the forecast starts on, widened to cover
    2 h before the start and the full forecast if they fall outside it."""
    mst = timedelta(hours=-7)
    local = (t_init + mst).date()
    noon = datetime(local.year, local.month, local.day, 12, tzinfo=timezone.utc) - mst
    midnight = noon + timedelta(hours=12)
    return min(noon, t_init - timedelta(hours=2)), max(midnight, t_end)


def case_events(name, t_init: datetime, hours, region, storm_grids, leads, lats, lons):
    """Fetch the whole storm day's warnings and reports; verify those inside the forecast window."""
    t_end = t_init + timedelta(hours=hours) + timedelta(minutes=10)
    d0, d1 = storm_day(t_init, t_end)
    warn = fetch_warnings(f"{name}_day", d0 - timedelta(hours=6), d1, region)
    lsr = fetch_lsrs(f"{name}_day", d0, d1, region)
    ver = verify(warn, lsr, storm_grids, leads, t_init, lats, lons)
    return {"warnings": warn, "lsrs": lsr, "verify": ver,
            "window": {"day_start": int(d0.timestamp()), "day_end": int(d1.timestamp()),
                       "fcst_start": int(t_init.timestamp()), "fcst_end": int(t_end.timestamp())}}
