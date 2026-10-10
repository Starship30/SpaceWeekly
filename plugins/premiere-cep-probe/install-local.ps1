# Installs only this experiment's panel; shared protocol and mapping stay in the checkout.
# Does not change signature checks, registry, or the existing UXP plugin.
$probeSource = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$probeParent = Join-Path $env:APPDATA 'Adobe\CEP\extensions'
$probeInstall = Join-Path $probeParent 'spaceweekly.cep.singlecard'
if (Test-Path -LiteralPath $probeInstall) {
    throw "Already exists: $probeInstall. Inspect it instead of overwriting."
}
New-Item -ItemType Directory -Path $probeInstall -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $probeSource 'index.html') -Destination $probeInstall
Copy-Item -LiteralPath (Join-Path $probeSource 'panel.js') -Destination $probeInstall
@{ source_dir = $probeSource } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $probeInstall 'probe-source.json') -Encoding UTF8
# Publish the manifest last, after all panel files are present.
$probeManifestDir = Join-Path $probeInstall 'CSXS'
New-Item -ItemType Directory -Path $probeManifestDir -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $probeSource 'CSXS\manifest.xml') -Destination $probeManifestDir
Write-Output "Installed experiment: $probeInstall; shared files: $probeSource"
Write-Output 'No registry changes made. Premiere 26.3 / CEP 12 must allow this unsigned development panel before it will appear.'
