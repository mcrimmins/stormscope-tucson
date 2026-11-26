<#
.SYNOPSIS
  Copy StormScope hindcast results (PNGs, CSVs) from the ThinkStation to this laptop.
  Zarr forecast files are skipped (large; they stay on the ThinkStation).

.EXAMPLES
  .\pull_results.ps1                          # all cases
  .\pull_results.ps1 -Case 2024-07-25_21z     # one case
  .\pull_results.ps1 -Logs                    # also bring back *.log files

  If Windows blocks the script, run it as:
  powershell -ExecutionPolicy Bypass -File .\pull_results.ps1
#>
param(
    [string]$Case = "",                              # empty = all cases
    [string]$Remote = "nickynacky@192.168.0.96",
    [string]$RemoteDir = "~/stormscope",
    [switch]$Logs
)
$ErrorActionPreference = "Stop"

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$dest = Join-Path $here "results"
New-Item -ItemType Directory -Force $dest | Out-Null

$target = if ($Case) { "outputs/hindcast*/$Case" } else { "outputs/hindcast*" }   # includes tagged experiments
$logPart = if ($Logs) { " `$(ls -1 *.log 2>/dev/null)" } else { "" }

# 1. Pack results on the ThinkStation (no Zarr)
$remoteCmd = "cd $RemoteDir && tar czf /tmp/ss_results.tgz --exclude='*.zarr' $target$logPart"
Write-Host "Packing on $Remote ..."
ssh $Remote $remoteCmd
if ($LASTEXITCODE -ne 0) { throw "Remote packing failed (does $target exist?)" }

# 2. Copy the archive to the laptop
$tgz = Join-Path $dest "ss_results.tgz"
Write-Host "Copying ..."
scp "${Remote}:/tmp/ss_results.tgz" $tgz
if ($LASTEXITCODE -ne 0) { throw "scp failed" }

# 3. Unpack into .\results (outputs/hindcast/... -> results/hindcast/...)
tar -xzf $tgz -C $dest --strip-components 1
Remove-Item $tgz
ssh $Remote "rm -f /tmp/ss_results.tgz" | Out-Null

Write-Host "`nDone. Files in $dest :"
Get-ChildItem $dest -Recurse -File | Select-Object -ExpandProperty FullName
