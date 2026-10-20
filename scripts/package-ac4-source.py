"""Bundle complete corresponding source for the separately linked GPL bridge."""
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / '.tools/iclforge-bin/ICLForge-corresponding-source.zip'
REPOS = ['.tools/iclforge', '.tools/iclforge-build/_deps/fmt-src',
         '.tools/iclforge-build/_deps/nlohmann_json-src',
         '.tools/iclforge-build/_deps/fast_float-src']


def git(path, *args):
    return subprocess.check_output(['git', '-C', str(path), *args])


def main():
    manifest = {}
    with zipfile.ZipFile(DEST, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for rel in REPOS:
            repo = ROOT / rel
            if git(repo, 'status', '--porcelain').strip():
                raise ValueError(f'Source checkout must be clean: {rel}')
            manifest[rel] = git(repo, 'rev-parse', 'HEAD').decode().strip()
            files = git(repo, 'ls-files', '-z').decode('utf-8').split('\0')
            for name in files:
                if name:
                    archive.write(repo / name, f'{rel}/{name}')
        for rel in ('native/ac4/CMakeLists.txt', 'native/ac4/main.cpp',
                    'scripts/build-ac4-offline.ps1'):
            archive.write(ROOT / rel, rel)
            manifest[rel] = hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()
        archive.writestr('source-manifest.json', json.dumps(manifest, indent=2) + '\n')
        archive.writestr('README.txt',
            'Corresponding source for Ac4Encode.exe (GPL-3.0-or-later).\n'
            'ICL Forge is unmodified; native/ac4 is this project\'s streaming adapter.\n'
            'All tracked upstream files, fmt, nlohmann/json and fast_float sources\n'
            'and their original licences are included. See .tools/iclforge/LICENSE\n'
            'and source-manifest.json for revisions and adapter hashes.\n'
            'Build on Windows with Visual Studio 2022 C++ Build Tools (MSVC 14.44\n'
            'or newer), Windows SDK, CMake >=3.28 and Ninja installed:\n'
            '  powershell -ExecutionPolicy Bypass -File scripts/build-ac4-offline.ps1\n'
            'This uses the enclosed dependencies and requires no network download.\n'
            'The executable is written to build/Ac4Encode.exe.\n')
    print(f'Corresponding source: {DEST} ({DEST.stat().st_size:,} bytes)')


if __name__ == '__main__':
    main()
