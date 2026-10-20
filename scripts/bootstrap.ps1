param([switch]$SkipPython, [string]$DotNet = 'dotnet')
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
function Checked([scriptblock]$Command) {
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "Command failed (exit $LASTEXITCODE)" }
}
Push-Location $root
try {
    New-Item -ItemType Directory -Force .tools | Out-Null
    $source = Join-Path $root '.tools/av3a'
    $commit = 'b44b5321862385a06ad6c427de5791e18f16c16b'
    if (!(Test-Path "$source/.git")) { Checked { git clone --depth 1 https://github.com/Dreamello/av3a.git $source } }
    $actual = git -C $source rev-parse HEAD
    if ($actual -ne $commit) {
        Checked { git -C $source fetch --depth 1 origin $commit }
        Checked { git -C $source checkout --detach $commit }
    }
    if (git -C $source status --porcelain) { throw 'Reference encoder source has local changes; use a clean checkout.' }
    $cmake = Get-Command cmake -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source
    if (!$cmake) {
        $vswhere = "${env:ProgramFiles(x86)}/Microsoft Visual Studio/Installer/vswhere.exe"
        if (!(Test-Path $vswhere)) { throw 'Install Visual Studio 2022 C++ Build Tools and CMake tools.' }
        $vs = & $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
        $cmake = Join-Path $vs 'Common7/IDE/CommonExtensions/Microsoft/CMake/CMake/bin/cmake.exe'
    }
    Checked { & $cmake -S native -B .tools/encoder-build -G 'Visual Studio 17 2022' -A x64 }
    Checked { & $cmake --build .tools/encoder-build --config Release --parallel }
    New-Item -ItemType Directory -Force .tools/bin | Out-Null
    Copy-Item .tools/encoder-build/Release/avs3Encoder.exe .tools/bin/avs3Encoder.exe -Force
    if (!$SkipPython) {
        if (!(Test-Path .venv/Scripts/python.exe)) { Checked { python -m venv .venv } }
        Checked { & .venv/Scripts/python.exe -m pip --disable-pip-version-check install -e . }
    }
    & "$PSScriptRoot/build-cavern.ps1" -DotNet $DotNet
    & "$PSScriptRoot/build-mpegh.ps1"
    & "$PSScriptRoot/build-iclforge.ps1"
    Write-Host 'Ready: Audio Vivid / AC-4 encoders + Cavern / MPEG-H object importers + Python backend.'
} finally { Pop-Location }
