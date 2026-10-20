param()
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
function Checked([scriptblock]$Command) {
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "Command failed (exit $LASTEXITCODE)" }
}
Push-Location $root
try {
    New-Item -ItemType Directory -Force .tools | Out-Null
    $source = Join-Path $root '.tools/libmpegh'
    $commit = 'f7ff0ac78d4d83f0b853bf2dff2ef075c92724f8'
    if (!(Test-Path "$source/.git")) {
        Checked { git clone --depth 1 --no-checkout https://github.com/ittiam-systems/libmpegh.git $source }
    }
    $actual = git -C $source rev-parse HEAD
    if ($actual -ne $commit -or !(Test-Path "$source/CMakeLists.txt")) {
        Checked { git -C $source fetch --depth 1 origin $commit }
        Checked { git -C $source checkout --detach $commit }
    }
    if (git -C $source status --porcelain) { throw 'libmpegh source has local changes; use a clean checkout.' }
    $cmake = Get-Command cmake -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source
    if (!$cmake) {
        $vswhere = "${env:ProgramFiles(x86)}/Microsoft Visual Studio/Installer/vswhere.exe"
        $vs = & $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
        $cmake = Join-Path $vs 'Common7/IDE/CommonExtensions/Microsoft/CMake/CMake/bin/cmake.exe'
    }
    Checked { & $cmake -S native/mpegh -B .tools/mpegh-export-build -G 'Visual Studio 17 2022' -A x64 -DCMAKE_POLICY_VERSION_MINIMUM=3.5 }
    Checked { & $cmake --build .tools/mpegh-export-build --config Release --target MpeghExport --parallel }
    New-Item -ItemType Directory -Force .tools/mpegh | Out-Null
    Copy-Item .tools/mpegh-export-build/Release/MpeghExport.exe .tools/mpegh/
    Copy-Item licenses/libmpegh*.txt .tools/mpegh/
    Write-Host 'Ready: unmodified libmpegh LC Level 4 external renderer exporter.'
} finally { Pop-Location }
