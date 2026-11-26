# stormscope-tucson

Prototype 0–6 h storm nowcasting for the NWS Tucson forecast area (southern Arizona)
using NVIDIA Earth-2 **StormScope** (GOES + MRMS + GLM, 3 km / 10 min, Flash variant),
run locally on an RTX 3090.

> Experimental research prototype — not an official forecast.

## Hardware / software (ThinkStation)

| | |
|---|---|
| GPU | RTX 3090, 24 GB (Ampere, sm_86), driver 580 (CUDA 13.0) |
| OS | Ubuntu 24.04 |
| Python env | uv project, Python 3.12 |
| Key pins | `torch==2.13.0+cu130`, `natten==0.21.7+torch2130cu130` (prebuilt), `earth2studio` 0.20.0 (git tag), `earth2grid` built locally |
| Build toolkit | CUDA Toolkit 13.0 at `/usr/local/cuda-13.0` (see `cuda-env.sh`) |

## Measured performance (Tucson region, 2 h = 13 steps)

| Run | s / 10-min step | 6 h forecast | Peak VRAM |
|---|---|---|---|
| Full CONUS, 1 member | 22 | ~13 min | 10.2 GiB |
| Region, 1 member | 4.5 | ~3 min | 6.4 GiB |
| Region, 4 members | 7.2 | ~4.5 min | 8.3 GiB |
| Region, 16 members | 17.3 | ~10.5 min | 19.0 GiB |

16 members is the practical ceiling on the 3090 (it also drives the display).

## Setup from a fresh clone

```bash
# one-time: CUDA 13.0 toolkit (compiler only; leaves the driver alone)
wget https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2404/x86_64/cuda-keyring_1.1-1_all.deb
sudo dpkg -i cuda-keyring_1.1-1_all.deb && sudo apt update
sudo apt install -y cuda-toolkit-13-0 ninja-build

git clone https://github.com/mcrimmins/stormscope-tucson.git ~/stormscope
cd ~/stormscope
source cuda-env.sh
uv sync --no-install-package earth2grid   # torch first (earth2grid builds against it)
uv sync                                   # then earth2grid (~2 min compile)
uv run python -c "import torch, natten, earth2grid, earth2studio; print(torch.__version__, torch.cuda.get_device_name(0))"
```

### Gotchas we hit
- `earth2grid` isn't on PyPI → install earth2studio from its git tag (uv handles the git dep).
- Ubuntu's `nvidia-cuda-toolkit` (nvcc 12.0) mismatches torch's CUDA 13 → use `/usr/local/cuda-13.0`; CMake needs `CUDACXX` set.
- Latest torch requires C++20, which natten 0.21.7 can't build → pin torch 2.13 and use natten's prebuilt wheel.
- `earth2grid` stamps the torch version it was built with into its version; after changing torch run `uv cache clean earth2grid`.
- In earth2studio 0.20.0 the Flash `region` is a tuple `(lat_min, lat_max, lon_min, lon_max)`.
- Inputs come from **GOES-East** (GOES-16 before 2025-04-07, GOES-19 after) — expect some parallax over Arizona.

## Scripts

| File | Purpose |
|---|---|
| `smoke_tucson.py` | NVIDIA's earth2studio StormScope GOES/MRMS example (Apache-2.0), edited for the Tucson region |
| `hindcast.py` | Ensemble hindcasts for cases in `cases.yaml`; saves Zarr, FSS vs. persistence, comparison maps |
| `cases.yaml` | Hindcast case list (init times in UTC) |
| `cuda-env.sh` | Environment variables for building CUDA extensions |
| `pull_results.ps1` | (Laptop) copy hindcast PNGs/CSVs from the ThinkStation into `results/` |

```bash
uv run python hindcast.py                                   # all cases, 16 members, 6 h
uv run python hindcast.py --only 2024-07-25_21z --members 4 --hours 2   # quick test
```

Outputs go to `outputs/hindcast/<case>/` (git-ignored): `forecast.zarr`, `scores.csv`,
`compare_leadNNN.png`, `fss_vs_lead.png`; plus `outputs/hindcast/scores_summary.csv`.

## Workflow: laptop ↔ GitHub ↔ ThinkStation

Code is edited on the laptop (`PyProjects\stormscope-tucson`), pushed to GitHub,
and run on the ThinkStation (`~/stormscope`, IP 192.168.0.96).

```powershell
# laptop (PowerShell): publish code changes
git add . ; git commit -m "message" ; git push
```
```bash
# ThinkStation: get changes and run
cd ~/stormscope && git pull
uv run python hindcast.py 2>&1 | tee hindcast.log
```
```powershell
# laptop: bring results back (PNGs + CSVs, no Zarr) into .\results
powershell -ExecutionPolicy Bypass -File .\pull_results.ps1
powershell -ExecutionPolicy Bypass -File .\pull_results.ps1 -Case 2024-07-25_21z -Logs
```

### One-time: password-free SSH from the laptop
```powershell
ssh-keygen -t ed25519            # accept defaults
type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh nickynacky@192.168.0.96 "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
```

### Single files by hand
```powershell
scp "nickynacky@192.168.0.96:~/stormscope/outputs/hindcast/<case>/compare_lead060.png" .\results\
```

## Region

`(29.5, 35.5, -114.5, -107.5)` — Tucson CWA plus Sonora and Mogollon Rim context
(~670 × 650 km; Flash needs ≥ 200 × 200 grid points).

## Licenses

Model weights: NVIDIA Open Model License (downloaded at runtime, not in this repo).
`smoke_tucson.py` and the inference loop in `hindcast.py` derive from NVIDIA's
earth2studio examples (Apache-2.0).
