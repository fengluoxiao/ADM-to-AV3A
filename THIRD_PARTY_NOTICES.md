# Third-party components

The MIT license covers this repository's original Python, C#, build scripts and examples. It does not relicense the reference codec or dependencies.

| Component | Source / license |
|---|---|
| AVS3-P3 reference encoder | [Dreamello/av3a](https://github.com/Dreamello/av3a), pinned commit `b44b5321862385a06ad6c427de5791e18f16c16b`; source file notices specify the [UWA Code Sharing Policy](http://www.theuwa.com/UWA_Code_Sharing_Policy.pdf). Use and redistribution are subject to those terms. |
| Windows App SDK / WinUI 3 | Microsoft; licenses in the NuGet packages and [WindowsAppSDK](https://github.com/microsoft/WindowsAppSDK). |
| .NET | Microsoft / .NET contributors, MIT and bundled third-party notices. |
| Visual C++ runtime | Microsoft Visual Studio redistributable components, distributed app-locally under Microsoft's redistribution terms; not covered by this repository's MIT license. |
| CPython 3.12 | Python Software Foundation license; `runtime/LICENSE.txt` in the portable build. |
| NumPy | BSD-3-Clause and bundled dependencies; see `runtime/Lib/site-packages/numpy-*.dist-info/licenses/`. |
| defusedxml | PSF license; see its installed package metadata. |

The encoder is fetched separately by `scripts/bootstrap.ps1`. Its sources, model and binaries are not committed to this repository. The native CMake target builds only the encoder with its original 16-channel capacity. A Windows spelling shim for `strcasecmp` is supplied; decoder code is neither built nor patched.

The Rust decoder used for independent local verification is [1254qwer/avs3a-rust](https://github.com/1254qwer/avs3a-rust), commit `59213e47a27ffa6ba9211bac1f5a6c6fd182e9d8`. It is not a runtime dependency and is not bundled.
