# ADM to AV3S

**WinUI 3 + Python 的 Audio Vivid 对象音频制作工具。**

输入 ADM/BW64 母版，或 WAV＋单独空间元数据，输出带 `av3a` 音轨的 `.m4a`。保留独立对象音频、动态位置、增益与受支持的尺寸信息；不自动渲染成固定 7.1.4 声床。

## 功能

- WinUI 3 深色界面：文件选择、输入检查、区间转换、进度日志、取消、输出目录入口。
- Windows 原生 AVS3-P3 LC 编码，不依赖 WSL、FFmpeg 或修改后的解码器。
- 48 kHz，96 kbps / 载体，最多 16 路实际音频载体；支持独立对象和一个单独 LFE。
- 可从 M4A/MP4 **逐字节复制 `udta`**，保留其中的封面、歌词、歌名等标签。
- 每次转换逐帧检查配置和 CRC；补偿 1024 采样编码延迟，并写入 M4A edit list。
- 同时保存原始空间元数据和 JSON 转换报告。已有文件不会被覆盖。
- Python CLI 与 WinUI 使用同一条转换链路，通过 UTF-8 JSON Lines 传递事件。

## 输入范围（v0.1）

| 输入 | 支持情况 |
|---|---|
| RIFF / RF64 / BW64 | PCM 16/24/32-bit、Float32；48 kHz；1–16 声道 |
| 内嵌 ADM | 读取 `axml`＋`chna`，包括音频 data 后方的元数据块 |
| 外置 ADM XML | WAV 必须含 `chna`，通过 TrackUID → TrackFormat/StreamFormat → ChannelFormat 或直接 ChannelFormat 映射 |
| ADM Objects | 点对象；Cartesian 或球坐标；动态块线性插值、零插值长度的跳变；块级增益 |
| 独立 LFE | 一个 LFE，映射到静态 DirectSpeakers LFE；源增益写入该载体 PCM |
| Cavern JSONL | 与前期导出的 `*.scene.jsonl` 格式兼容；支持归一化 `size` |

这是有明确输入边界的第一版。非 LFE 的 DirectSpeakers 声床、HOA、Matrix、嵌套对象、多节目、交互参数、ADM 非零 extent、未覆盖完整音频的块等会报错。不会把未知映射猜成固定声床。Atmos M4A **不是直接输入格式**；需先在外部导出对象 WAV＋ADM/scene。

AVS3 空间信息有量化：XYZ 为 8/6/4 bit，extent 为 7/5/4 bit，gain 为 7 bit、范围 0–6；动态元数据按 1024 采样帧采样。JSONL 更新按时间保持，ADM 块按其持续时间插值。原始 XML/JSONL 另外逐字节备份。原 ADM 的对象名称写入报告；不会声称 ADM 的完整原始层级原样进入码流。参考编码器内部使用 16-bit PCM，有削波风险的母版会被拒绝。

M4A 里的编码 profile 是 Mixed / ObjectsOnly；LFE 由空间元数据路由，因此仅看帧头的工具可能把 LFE 载体也计作一个对象。支持对象渲染的播放器才能正确播放空间场景；“原版 Rust 解码通过”与播放器已完成对象渲染是两种验证。忽略 edit list 的裸流解码器会输出首尾填充。

## Windows 开发运行

要求：Windows 10 2004+ x64（建议 Windows 11）、Python 3.11+、.NET 8 SDK 或更新版本、Visual Studio 2022 C++ Build Tools（含 CMake 和 Windows SDK）、Git。

```powershell
git clone https://github.com/fengluoxiao/ADM-to-AV3S.git
cd ADM-to-AV3S
./scripts/bootstrap.ps1
dotnet build src/AdmToAv3s/AdmToAv3s.csproj -c Release -p:Platform=x64
```

打开 `src/AdmToAv3s/bin/x64/Release/net8.0-windows10.0.19041.0/win-x64/AdmToAv3s.exe`。应用自动找到仓库的 `.venv` 和 `.tools/bin/avs3Encoder.exe`，也可以在“运行环境”中指定路径。`AV3S_HOME` 可显式指定程序根目录。

`bootstrap.ps1` 会把外部编码器固定在提交 `b44b5321862385a06ad6c427de5791e18f16c16b`，保持原始 16 路容量，使用标量 C 路径，无 AVX2 硬件要求。

## 命令行

```powershell
# 生成原创双对象测试信号
./.venv/Scripts/python.exe examples/make_demo.py

# 检查 WAV + 独立空间元数据
./.venv/Scripts/python.exe -m av3s.cli inspect `
  --input artifacts/demo/moving.wav `
  --metadata artifacts/demo/moving.scene.jsonl

# 转换为 M4A；内嵌 ADM 可省略 --metadata
./.venv/Scripts/python.exe -m av3s.cli convert `
  --input artifacts/demo/moving.wav `
  --metadata artifacts/demo/moving.scene.jsonl `
  --output artifacts/demo/moving.m4a
```

可选参数：`--tags original.m4a`、`--start 0.5`、`--duration 10`、`--encoder path/to/avs3Encoder.exe`。外部程序可通过创建 `--cancel-file` 指定的文件请求取消，返回码为 2；错误为 1，成功为 0。

JSONL 每行示例（声道顺序必须与 WAV 完全一致；坐标 X 向右、Y 向前、Z 向上）：

```json
{"sample":0,"objects":[{"index":0,"name":"Object 1","lfe":false,"x":-0.5,"y":0.8,"z":0.2,"gain":1.0,"size":0.0}]}
```

第一行 `sample=0`，后续采样点严格递增；每行必须覆盖所有 WAV 声道，LFE 映射固定。JSONL X/Y/Z 范围 -1–1，size 0–1，gain 0–6。超范围、NaN、对象数量变化会被拒绝。

## 测试与便携包

```powershell
./.venv/Scripts/python.exe -m unittest discover -s tests -v
./scripts/publish.ps1
```

打包脚本会生成 `dist/ADM-to-AV3S-win-x64-时间戳.zip`，内含 WinUI、.NET/Windows App SDK 自包含文件、Python 嵌入式运行时和编码器；解压后运行 `AdmToAv3s.exe`，无需另装 Python 或 WSL。脚本会先用包内运行时做一次完整短音频转换。构建需要联网恢复 NuGet/Python 包。GitHub Actions 会编译界面并运行真实编码测试。

仓库不含私人音乐、之前转换的歌曲、开发机路径或外部编码器二进制。项目代码使用 MIT；外部 AVS3 参考编码器受其自身 UWA Code Sharing Policy 约束，详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
