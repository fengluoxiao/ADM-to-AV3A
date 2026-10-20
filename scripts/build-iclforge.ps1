param([string]$CMake = '', [int]$Jobs = 6)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$commit = '315899937e5c337ab34e3e27471539b9810e3b5f'
function Checked([scriptblock]$Command) {
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "Command failed (exit $LASTEXITCODE)" }
}
Push-Location $root
try {
    $source = Join-Path $root '.tools/iclforge'
    if (!(Test-Path "$source/.git")) { Checked { git -c http.version=HTTP/1.1 clone --depth 1 https://github.com/iainchesworthlabs/iclforge.git $source } }
    if ((git -C $source rev-parse HEAD) -ne $commit) {
        Checked { git -C $source fetch --depth 1 origin $commit }
        Checked { git -C $source checkout --detach $commit }
    }
    if (git -C $source status --porcelain) { throw 'ICL Forge source must be an unmodified checkout.' }
    $vswhere = "${env:ProgramFiles(x86)}/Microsoft Visual Studio/Installer/vswhere.exe"
    $vs = if (Test-Path $vswhere) { & $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath }
    if (!$vs) { $vs = "${env:ProgramFiles(x86)}/Microsoft Visual Studio/2022/BuildTools" }
    $devcmd = Join-Path $vs 'Common7/Tools/VsDevCmd.bat'
    if (!(Test-Path $devcmd)) { throw 'Install Visual Studio C++ Build Tools with CMake and Ninja.' }
    $env:VSLANG = '1033'
    # Import the compiler environment into this process only.
    $environmentCommand = "call `"$devcmd`" -no_logo -arch=x64 -host_arch=x64 >nul && set"
    & $env:ComSpec /d /c $environmentCommand | ForEach-Object {
        if ($_ -match '^([^=]+)=(.*)$') { [Environment]::SetEnvironmentVariable($matches[1], $matches[2], 'Process') }
    }
    if (!$CMake) { $CMake = Join-Path $vs 'Common7/IDE/CommonExtensions/Microsoft/CMake/CMake/bin/cmake.exe' }
    $ninja = Join-Path $vs 'Common7/IDE/CommonExtensions/Microsoft/CMake/Ninja/ninja.exe'
    $build = Join-Path $root '.tools/iclforge-build'
    # Our existing ADM reader supplies PCM + object metadata to a thin adapter.
    # The optional Boost/libadm module and all desktop applications are unnecessary.
    Checked { & $CMake -S $source -B $build -G Ninja "-DCMAKE_MAKE_PROGRAM=$ninja" -DCMAKE_BUILD_TYPE=Release '-DCMAKE_CXX_FLAGS=/EHsc /utf-8' -DCMAKE_MSVC_SHOWINCLUDES_PREFIX='Note: including file: ' -DBUILD_SHARED_LIBS=OFF -DICLFORGE_BUILD_CLI=ON -DICLFORGE_BUILD_GUI=OFF -DICLFORGE_BUILD_HEARTH=OFF -DICLFORGE_BUILD_CRUCIBLE=OFF -DICLFORGE_BUILD_TESTS=OFF -DICLFORGE_BUILD_EXAMPLES=OFF -DICLFORGE_BUILD_ADM=OFF -DICLFORGE_BUILD_PYTHON=OFF -DICLFORGE_BUILD_CAPI=OFF }
    Checked { & $CMake --build $build --target forge --parallel $Jobs }
    $destination = Join-Path $root '.tools/iclforge-bin'
    New-Item -ItemType Directory -Force $destination | Out-Null
    Copy-Item "$build/bin/forge.exe" $destination -Force
    Get-ChildItem "$build/bin" -Filter '*.dll' | Copy-Item -Destination $destination -Force
    Copy-Item "$source/LICENSE" "$destination/COPYING.txt" -Force
    $adapterBuild = Join-Path $root '.tools/ac4-build'
    Checked { & $CMake -S native/ac4 -B $adapterBuild -G Ninja "-DCMAKE_MAKE_PROGRAM=$ninja" -DCMAKE_BUILD_TYPE=Release '-DCMAKE_CXX_FLAGS=/EHsc /utf-8' -DBUILD_SHARED_LIBS=OFF -DICLFORGE_BUILD_CLI=OFF -DICLFORGE_BUILD_GUI=OFF -DICLFORGE_BUILD_HEARTH=OFF -DICLFORGE_BUILD_TESTS=OFF -DICLFORGE_BUILD_EXAMPLES=OFF -DICLFORGE_BUILD_ADM=OFF -DICLFORGE_BUILD_CAPI=OFF "-DFETCHCONTENT_SOURCE_DIR_FMT=$build/_deps/fmt-src" "-DFETCHCONTENT_SOURCE_DIR_NLOHMANN_JSON=$build/_deps/nlohmann_json-src" }
    Checked { & $CMake --build $adapterBuild --target Ac4Encode --parallel $Jobs }
    Copy-Item "$adapterBuild/Ac4Encode.exe" $destination -Force
    @{ repository = 'https://github.com/iainchesworthlabs/iclforge'; commit = $commit; bridge = 'native/ac4 streaming adapter; unchanged upstream codec'; codec_mode = 'ASPX'; adapter_sha256 = (Get-FileHash "$destination/Ac4Encode.exe" -Algorithm SHA256).Hash.ToLowerInvariant() } | ConvertTo-Json | Set-Content "$destination/build.json" -Encoding utf8
    Write-Host "ICL Forge AC-4 backend: $destination"
} finally { Pop-Location }
