param([string]$DotNet = 'dotnet')
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
function Checked([scriptblock]$Command) {
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "Command failed (exit $LASTEXITCODE)" }
}
Push-Location $root
try {
    New-Item -ItemType Directory -Force .tools | Out-Null
    $source = Join-Path $root '.tools/Cavern'
    $commit = '583fe204df95c83ec3ecb78bba63986e437a911d'
    if (!(Test-Path "$source/.git")) { Checked { git clone --depth 1 --no-checkout https://github.com/VoidXH/Cavern.git $source } }
    $actual = git -C $source rev-parse HEAD
    if ($actual -ne $commit -or !(Test-Path "$source/Cavern/Cavern.csproj")) {
        Checked { git -C $source fetch --depth 1 origin $commit }
        Checked { git -C $source checkout --detach $commit }
    }
    if (git -C $source status --porcelain) { throw 'Cavern source has local changes; use a clean checkout.' }
    Checked { & $DotNet publish src/CavernExport/CavernExport.csproj -c Release -o .tools/cavern-export }
    Copy-Item licenses/Cavern.txt .tools/cavern-export/LICENSE-Cavern.txt -Force
    Write-Host 'Ready: Cavern object decoder/exporter.'
} finally { Pop-Location }
