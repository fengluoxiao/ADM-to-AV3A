# Original synthetic MPEG-H fixtures

No songs, private music, Dolby SDK or Sony SDK are included. Each source is
16,384 samples at 48 kHz, with independent low-level sine tones at
`300 + 73 * object_index` Hz. OAM v2 supplies changing azimuth, gain and spread.
`generate.py` contains the complete source signal and OAM construction.

Encoded with [Ittiam libmpeghe](https://github.com/ittiam-systems/libmpeghe),
commit `603275bb7647cdf8db86dbdf2291495d8fdcfa7f`, on Windows x64/MSVC.
The encoder is a fixture-generation tool only; it is not bundled or required
by the application. The test-only source patch remains under the original
[encoder licence](encoder-LICENSE.txt) and [additional terms](encoder-LICENSE2.txt).

- `moving4.mhas`: unmodified encoder defaults (CPE + enhanced noise filling).
- `moving4-sce.mhas`, `moving20-sce.mhas`: test-only encoder configuration patch
  `lc4-test-encoder.patch`, using SCE objects and disabling enhanced noise
  filling. The pinned LC4 decoder requires these restrictions above 16 core
  channels. The production decoder remains unmodified.
- `below4-sce.mhas`: same configuration, elevations −30°, −15°, 0° and +15°,
  for checking that the lower hemisphere survives coordinate mapping.
- `subframe4-sce.mhas`: OAM every 256 samples, for checking subframe ordering.
- `moving4-mha1.mp4`, `moving4-mhm1.mp4`: same SCE tones in both container
  formats. The generator corrects the upstream MP4 writer's zero edit rate to
  one; audio payloads are unchanged. Start trim is 1,600 samples.

To regenerate, build a clean pinned encoder, then run from repository root:

```powershell
./.venv/Scripts/python.exe tests/fixtures/mpegh/generate.py --encoder PATH/TO/MPEGHEncoder.exe --output artifacts/fixtures-default --unpatched
git -C PATH/TO/libmpeghe apply ABSOLUTE/PATH/TO/lc4-test-encoder.patch
# Rebuild the test encoder, then:
./.venv/Scripts/python.exe tests/fixtures/mpegh/generate.py --encoder PATH/TO/MPEGHEncoder.exe --output artifacts/fixtures-sce
```

The first SCE four-object PCM export was independently compared byte for byte
with the pinned upstream testbench's `-ext_ren:1 -pcmsz:24 -cicp:2` output.
Both are 196,608 bytes with SHA-256
`d4d022d5687a782a62695735abff2cb618b6b71980fcc0106a5fadba2e1ef08e`.
Both executables need a 16 MiB Windows stack for the LC4 initialization arrays.
