param([string]$DotNet = 'dotnet')
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
function Checked([scriptblock]$Command) {
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "Command failed (exit $LASTEXITCODE)" }
}
Push-Location $root
try {
    if (!(Test-Path '.tools/bin/avs3Encoder.exe') -or !(Test-Path '.venv/Scripts/python.exe')) {
        & "$PSScriptRoot/bootstrap.ps1" -DotNet $DotNet
    }
    & "$PSScriptRoot/build-cavern.ps1" -DotNet $DotNet
    # A fresh versioned staging folder prevents stale DLLs or overwritten builds.
    $stage = Join-Path $root ('dist/ADM-to-AV3S-v0.3.1-win-x64-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
    New-Item -ItemType Directory -Force $stage | Out-Null
    Checked { & $DotNet publish src/AdmToAv3s/AdmToAv3s.csproj -c Release -p:Platform=x64 -o $stage }
    # WinUI's C++ DLLs need the app-local VC runtime on a clean Windows install.
    $vswhere = "${env:ProgramFiles(x86)}/Microsoft Visual Studio/Installer/vswhere.exe"
    $vs = & $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
    if (!$vs) { throw 'Visual C++ Build Tools are required to package the app-local VC runtime.' }
    $crt = Get-ChildItem (Join-Path $vs 'VC/Redist/MSVC') -Directory |
        Where-Object { $_.Name -match '^\d+\.' } | Sort-Object { [version]$_.Name } -Descending |
        ForEach-Object { Join-Path $_.FullName 'x64/Microsoft.VC143.CRT' } |
        Where-Object { Test-Path $_ } | Select-Object -First 1
    if (!$crt) { throw 'Cannot locate the redistributable x64 Visual C++ runtime.' }
    Copy-Item "$crt/*.dll" $stage
    New-Item -ItemType Directory -Force "$stage/backend/av3s" | Out-Null
    Copy-Item backend/av3s/*.py "$stage/backend/av3s"
    New-Item -ItemType Directory -Force "$stage/tools" | Out-Null
    Copy-Item .tools/bin/avs3Encoder.exe "$stage/tools/avs3Encoder.exe"
    Copy-Item -Recurse .tools/cavern-export "$stage/tools/cavern"
    Copy-Item README.md,LICENSE,THIRD_PARTY_NOTICES.md $stage
    Copy-Item -Recurse licenses "$stage/licenses"
    $zip = Join-Path $root '.tools/python-3.12.10-embed-amd64.zip'
    if (!(Test-Path $zip)) {
        Invoke-WebRequest 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip' -OutFile $zip
    }
    Expand-Archive -LiteralPath $zip -DestinationPath "$stage/runtime"
    Checked { & .venv/Scripts/python.exe -m pip --disable-pip-version-check install --only-binary=:all: --platform win_amd64 --implementation cp --python-version 3.12 --abi cp312 --target "$stage/runtime/Lib/site-packages" 'numpy==2.5.4' 'defusedxml==0.7.1' }
    @('python312.zip', '.', 'Lib/site-packages', '../backend', 'import site') | Set-Content "$stage/runtime/python312._pth" -Encoding ascii
    Checked { & "$stage/runtime/python.exe" -m av3s.cli --help }
    # Run a complete short conversion with the bundled interpreter and encoder.
    $smoke = Join-Path $root ('artifacts/package-smoke-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
    Checked { & "$stage/runtime/python.exe" examples/make_demo.py $smoke }
    Checked { & "$stage/runtime/python.exe" -m av3s.cli convert --input "$smoke/moving.wav" --metadata "$smoke/moving.scene.jsonl" --output "$smoke/result.m4a" --encoder "$stage/tools/avs3Encoder.exe" --duration 0.1 }
    $archive = "$stage.zip"
    Compress-Archive -Path "$stage/*" -DestinationPath $archive
    Write-Host "Portable build: $stage"
    Write-Host "ZIP: $archive"
} finally { Pop-Location }
