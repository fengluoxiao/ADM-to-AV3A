# GPL-3.0-or-later. Build the adapter from the enclosed corresponding source.
param([string]$VisualStudio = '', [int]$Jobs = 6)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
function Checked([scriptblock]$Command) {
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "Command failed (exit $LASTEXITCODE)" }
}
if (!$VisualStudio) {
    $vswhere = "${env:ProgramFiles(x86)}/Microsoft Visual Studio/Installer/vswhere.exe"
    if (Test-Path $vswhere) {
        $VisualStudio = & $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
    }
    if (!$VisualStudio) { $VisualStudio = "${env:ProgramFiles(x86)}/Microsoft Visual Studio/2022/BuildTools" }
}
$devcmd = Join-Path $VisualStudio 'Common7/Tools/VsDevCmd.bat'
if (!(Test-Path $devcmd)) { throw 'Install Visual Studio C++ Build Tools with CMake and Ninja.' }
$environmentCommand = "call `"$devcmd`" -no_logo -arch=x64 -host_arch=x64 >nul && set"
& $env:ComSpec /d /c $environmentCommand | ForEach-Object {
    if ($_ -match '^([^=]+)=(.*)$') { [Environment]::SetEnvironmentVariable($matches[1], $matches[2], 'Process') }
}
$cmake = Join-Path $VisualStudio 'Common7/IDE/CommonExtensions/Microsoft/CMake/CMake/bin/cmake.exe'
$ninja = Join-Path $VisualStudio 'Common7/IDE/CommonExtensions/Microsoft/CMake/Ninja/ninja.exe'
Push-Location $root
try {
    $deps = Join-Path $root '.tools/iclforge-build/_deps'
    Checked { & $cmake -S native/ac4 -B build -G Ninja "-DCMAKE_MAKE_PROGRAM=$ninja" -DCMAKE_BUILD_TYPE=Release '-DCMAKE_CXX_FLAGS=/EHsc /utf-8' -DBUILD_SHARED_LIBS=OFF -DICLFORGE_BUILD_CLI=OFF -DICLFORGE_BUILD_GUI=OFF -DICLFORGE_BUILD_HEARTH=OFF -DICLFORGE_BUILD_TESTS=OFF -DICLFORGE_BUILD_EXAMPLES=OFF -DICLFORGE_BUILD_ADM=OFF -DICLFORGE_BUILD_CAPI=OFF -DFETCHCONTENT_FULLY_DISCONNECTED=ON "-DFETCHCONTENT_SOURCE_DIR_FMT=$deps/fmt-src" "-DFETCHCONTENT_SOURCE_DIR_NLOHMANN_JSON=$deps/nlohmann_json-src" "-DFETCHCONTENT_SOURCE_DIR_FAST_FLOAT=$deps/fast_float-src" }
    Checked { & $cmake --build build --target Ac4Encode --parallel $Jobs }
} finally { Pop-Location }
