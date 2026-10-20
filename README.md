# ADM to AV3A

**WinUI 3 + Python 的 Audio Vivid / AC-4 A-JOC 对象音频制作工具。**

输入 Atmos M4A/MP4、MPEG-H 360RA、E-AC-3 JOC 裸流、ADM/BW64 母版或 WAV＋单独空间元数据，输出 Audio Vivid（`av3a`）或实验性 AC-4 A-JOC（`ac-4`）的 `.m4a`。超出所选模式容量时可显式启用 JOCForge 聚类，合并声音并近似位置及混合增益。AC-4 A-JOC 将对象声音下混后有损重建，不是每个对象独立编码或无损转换。

## AC-4 A-JOC 输出（v0.5）

WinUI 的“输出格式”选择 **AC-4 A-JOC**，码率可选 256 / 448 / 768 / 1500 kbps，默认 768。Atmos 和 360RA 同样可先解码对象再转入此输出。M4A 标签、源空间元数据备份、取消和不覆盖已有文件的行为与 Vivid 相同。

| 模式 | 输入容量与声床处理 |
|---|---|
| 兼容对象（默认） | 合计最多 20 路，LFE 独立；非 LFE 声床按扬声器角色映射成固定位置对象，属于位置近似，逐项记录 `bed_object_mapping`。超过容量需显式聚类，最多 19 个聚类对象＋一个 LFE。 |
| 原生声床（实验选项） | 合计最多 64 路，直接写入原生 bed assignment、LFE 和动态对象。超过容量需显式聚类；目前部分独立解码器拒绝此声床语法或高对象数，不作为默认播放兼容方案。 |

采用未修改的 [ICL Forge](https://github.com/iainchesworthlabs/iclforge) 提交 `315899937e5c337ab34e3e27471539b9810e3b5f`。`native/ac4` 是流式适配器，逐块提供 Float32 PCM，调用真实 A-JOC computed downmix 编码（`b_static_dmx=false`），没有改解码器。固定使用 ASPX，48 kHz / 2048 采样帧；源增益逐采样写入 PCM，超峰值时全场景统一降增益并记录。对象位置每 256 采样采样并以线性 ramp 写入；ADM X/Y 映射到 AC-4 的 `[0,1]` 房间坐标，Z 保留正负。X/Y 步长 1/62、Z 1/15、尺寸 1/31，存在量化及时间近似。Dolby 专有 dbmd 参数仅另存备份，不声称全量映射。

封装写入真实 `dac4`、变长 sample table 和随机访问帧表，逐帧校验 AC-4 CRC，使用编码器报告的编码/解码延迟合计 4385 采样写入 edit list。原始源元数据按原有机制备份。报告记录编码组件 SHA-256、构建来源、输出模式、对象合并/声床映射与量化误差；误差统计不代表实测听感或 A-JOC 重建误差。

已用未修改的 [MacinDecode-AC4-Core v0.1.0](https://github.com/SakuzyPeng/MacinDecode-AC4-Core/releases/tag/v0.1.0) 将默认兼容输出解码回 ADM，验证动态对象、LFE 及区间时长，包括 66 轨 Logic ADM 的两秒聚类输出。测试还覆盖合成 7.1.2 声床到固定对象、标签复制及取消。该版本解码器的完整音频重建限制为最多 20 路全频信号，且不支持原生 bed assignment；软件默认保守使用总计 20 路。原生模式仅通过 ICL Forge 自身编解码检查。不能据此保证所有 Dolby 设备、播放器或认证兼容性。

```powershell
./.venv/Scripts/python.exe -m av3s.cli convert --input master.wav --output ajoc.m4a --codec ac4 --bitrate 768
# 超容量母版，允许对象合并和位置近似
./.venv/Scripts/python.exe -m av3s.cli convert --input master.wav --output reduced-ajoc.m4a --codec ac4 --spatial-reduction
# 实验性原生声床与 64 路配置
./.venv/Scripts/python.exe -m av3s.cli convert --input master.wav --output native-ajoc.m4a --codec ac4 --ac4-native
```

`scripts/build-iclforge.ps1` 单独构建该组件。适配器与所链接的 ICL Forge 使用 GPL；便携包附带许可和 `sources/ICLForge-corresponding-source.zip` 完整对应源码、依赖及离线构建脚本，见第三方声明。

## 功能

- WinUI 3 深色界面：文件选择、输入检查、区间转换、进度日志、取消、输出目录入口。
- Windows 原生 AVS3-P3 LC 编码，不依赖 WSL、FFmpeg 或修改后的解码器。
- 内置 Cavern 对象导入：直接选择可读取的 Apple Music Atmos M4A，自动解码 JOC 对象、动态位置、尺寸、增益与 LFE，再编码 Vivid。M4A 标签自动复制；外置元数据留空即可。
- 可单独导出 Float32 对象 WAV（Cavern 内嵌位置 ADM）、同名 `.scene.jsonl` 和 `.export.json`，供编辑或后续编码。
- 48 kHz，96 kbps / 载体；读取最多 128 路，编码最多 16 路。支持 2.0、5.1、7.1.2 DirectSpeakers 声床、独立对象和一个单独 LFE。
- Logic Pro ADM：支持 `RC_` 声床标签、省略 Cartesian Z、非零跳变插值长度及一采样时间取整误差。
- JOCForge 空间缩减：非 LFE 声床和对象共同缩减到最多 15 个聚类对象，LFE 独立保留；默认关闭。
- 可从 M4A/MP4 **逐字节复制 `udta`**，保留其中的封面、歌词、歌名等标签。
- 每次转换逐帧检查配置和 CRC；补偿 1024 采样编码延迟，并写入 M4A edit list。
- 同时保存原始空间元数据和 JSON 转换报告。已有文件不会被覆盖。
- Python CLI 与 WinUI 使用同一条转换链路，通过 UTF-8 JSON Lines 传递事件。

## 输入范围（Vivid 默认模式）

| 输入 | 支持情况 |
|---|---|
| Atmos M4A / MP4 | 一条未加密 `ec-3` 音轨；48 kHz；支持一段正常速率的 MP4 edit list，保留源时间裁剪；结尾超出实际音轨时取有效交集并记录差额 |
| MPEG-H / 360RA | M4A/MP4/MHA/MHM 容器中一条未加密 `mha1` / `mhm1` 音轨，或 MHAS 裸流；48 kHz、单一纯对象组、最多 32 路；声道组、混合场景、HOA、未支持的 preroll 和配置变化会明确报错 |
| E-AC-3 JOC 裸流 | `.eac3` / `.ec3`；必须成功读出 JOC 和 OAMD，不回退成普通多声道 |
| RIFF / RF64 / BW64 | PCM 16/24/32-bit、Float32；48 kHz；1–128 声道；超出 16 路需显式聚类 |
| 内嵌 ADM | 读取 `axml`＋`chna`，包括音频 data 后方的元数据块 |
| 外置 ADM XML | WAV 必须含 `chna`，通过 TrackUID → TrackFormat/StreamFormat → ChannelFormat 或直接 ChannelFormat 映射 |
| ADM Objects | 点对象；Cartesian 或球坐标；动态块线性插值、跳变与非零 interpolationLength；块级增益 |
| ADM DirectSpeakers | 完整 2.0、5.1、7.1.2 布局；通过 speakerLabel 映射静态声床，源增益写入 PCM |
| 独立 LFE | 一个 LFE，映射到静态 DirectSpeakers LFE；源增益写入该载体 PCM |
| Cavern JSONL | 与前期导出的 `*.scene.jsonl` 格式兼容；支持归一化 `size` |

HOA、Matrix、其他 DirectSpeakers 布局、嵌套对象、多节目、交互参数、ADM 非零 extent、未覆盖完整音频的块等会报错。不会把未知映射猜成固定声床。普通 AAC/ALAC、Audio Vivid 和加密 M4A 不属于 Cavern JOC 导入范围；TrueHD 不在此版本导入范围内。

## 直接转换 Apple Music Atmos 文件

在 WinUI 中选择已下载且可读取的 Atmos M4A，外置元数据留空，点击“检查输入”后转换即可。不需要事先准备 ADM。源 M4A 存在 `udta` 时自动复制封面、歌词和歌曲信息；选择其他标签来源可覆盖此默认行为。16 路以内的 JOC 解码结果不需要启用聚类。

“单独导出空间对象 WAV＋元数据”会让你选择输出目录，使用输入文件名加 `(decoded objects).wav` 保存。转换区间设置同样生效。再次导入该 WAV 时选择同名 JSONL：它保存源增益、尺寸及 LFE 标记，优先于 Cavern 的内嵌位置 ADM。

```powershell
./.venv/Scripts/python.exe -m av3s.cli convert --input atmos.m4a --output vivid.m4a
./.venv/Scripts/python.exe -m av3s.cli export --input atmos.m4a --output objects.wav --start 30 --duration 10
```

导入采用未修改的 [Cavern](https://github.com/VoidXH/Cavern) 库，作者为 [Bence Sgánetz](https://en.sbence.hu/)。Cavern 使用[自定义许可](licenses/Cavern.txt)，并非 MIT；完整条款和署名随便携包附带，见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。JOC 是有损对象重建，得到的对象不等于制作阶段的原始 ADM。遇到稀疏 JOC 兼容处理时会显示提示并记录 `sparse_workaround`。

导出端每 256 采样获取对象和空间状态，转换端仍按 1024 采样写入 Vivid 元数据；MP4 起始裁剪及区间截取使用连续预热、固定块缓存，保留滤波历史并准确截取 PCM。转换报告和 `.source.cavern.json` 记录组件版本、裁剪、采样间隔和兼容处理，提取的 JSONL 和 Cavern 生成的 ADM 另外备份。它们是解码后的表示，不声称复制 Atmos 的全部专有元数据。取消或失败时不发布临时导出。

AVS3 空间信息有量化：XYZ 为 8/6/4 bit，extent 为 7/5/4 bit，gain 为 7 bit、范围 0–6；动态元数据按 1024 采样帧采样。JSONL 更新按时间保持，ADM 块按其持续时间插值。原始 XML/JSONL 以及存在的 chna、dbmd、bext、iXML、LIST 块另外逐字节备份并记录 SHA-256；保留 dbmd 备份不代表 Dolby 专有渲染参数已映射进 Vivid。参考编码器内部使用 16-bit PCM，普通模式拒绝超范围 PCM；聚类模式必要时统一降低全场景增益避免削波。

M4A 里的编码 profile 是 Mixed / ObjectsOnly；声床和 LFE 由静态空间元数据路由，因此仅看帧头的工具可能把全部 16 路载体计作对象。支持对象渲染的播放器才能正确播放空间场景；“原版 Rust 解码通过”与播放器已完成对象渲染是两种验证。忽略 edit list 的裸流解码器会输出首尾填充。

## 直接转换 MPEG-H / 360 Reality Audio

选择实际含 MPEG-H 音轨的 360RA 文件，空间元数据留空。软件按 MP4 音轨类型自动选择解码器。超过 16 路时需显式开启现有空间聚类；也可先导出全部独立对象 WAV，再编辑或重新编码。仅有双耳 AAC 的文件不能恢复对象。

当前支持 48 kHz、单一对象组的 1–32 路对象场景，以及 `mha1` / `mhm1` MP4 和裸 MHAS。含声道组、混合场景、HOA、多段编辑或多帧 preroll 的输入会明确拒绝，避免错位导出。已用原创动态 4 对象和 20 对象流测试，并覆盖两种 MP4 封装；真实歌曲仍需逐文件验证。

若单帧外部 OAM 超过上游固定的 768 字节缓冲区，当前会拒绝导入，避免保存已经回绕损坏的二进制元数据。

下半球对象保留为负 Z，不抬升到水平面。参考 AVS3 编解码器的对象仰角支持负值，Cartesian Z 范围标注为 −1～+1；本工具使用 4-bit Z，存在高度量化。元数据能表示下方位置，不代表任意播放器或没有底部扬声器的布局都能还原下方听感。

```powershell
./.venv/Scripts/python.exe -m av3s.cli inspect --input 360ra.m4a
./.venv/Scripts/python.exe -m av3s.cli convert --input 360ra.m4a --output vivid.m4a --spatial-reduction
./.venv/Scripts/python.exe -m av3s.cli export --input 360ra.m4a --output objects.wav
```

`scripts/build-mpegh.ps1` 构建 [Ittiam libmpegh](https://github.com/ittiam-systems/libmpegh) 固定版本 `f7ff0ac78d4d83f0b853bf2dff2ef075c92724f8`，启用 LC Level 4，保持上游源码不变。`native/mpegh` 是本项目的导出适配器：调用解码 API，保存外部渲染接口的原始 24-bit PCM，只读获取各子帧完整量化 OAM。使用内部结构的原因：该上游版本的外部 OAM writer 在部分 intra 帧没有输出 `has_obj_md` 对应的初始值，单独解析 `.bs` 会漏位置。适配器不修改解码算法、Rust 播放器或上游 PCM。

导出为 24-bit 对象 WAV＋`.scene.jsonl`，不含内嵌 ADM。转换和导出都保存 `.source.mpegh.oam.jsonl`（完整量化对象状态）、`.source.mpegh.oam.bs`（上游原始外部接口）及报告；转换另存 `.source.mpegh.json`。备份包含整条源时间线，报告记录所选区间和 MP4 edit list。检查输入目前会完整解码一次，转换时再解码一次。

空间映射会产生明确记录的近似：MPEG-H 正方位角向左，映射成 X 向右、Y 向前、Z 向上；全场景半径统一除以不小于 1 的最大半径，以适配 Vivid 单位空间；非等向扩散映射成三轴最大归一化值的等向 `size`。OAM 子帧按时间保持，目标仍每 1024 采样写元数据，与 MPEG-H 扬声器渲染器的增益插值不同。diffuseness、divergence、排除扇区与优先级没有等价映射，外部接口原始数据另存。上游 24-bit 外部 PCM 可能已有饱和处理；不声称恢复编码前母版、全部 MPEG-H 交互信息或 Sony 认证渲染效果。

## JOCForge 聚类（有损）

WinUI 中手动勾选“允许 JOCForge 聚类”，或在 CLI 传入 `--spatial-reduction`。该选项在输入超过 16 路时生效。例如 10 路 7.1.2 声床＋56 路对象会成为 **1 路 LFE＋15 路聚类对象**：65 路非 LFE 音频均参与聚类，原声床不再作为完整静态声床独立保留。所有源轨都有贡献映射，但对象独立性、位置和混合平衡会变化。

算法移植自 [JOCForge reduction.rs](https://github.com/chyinan/JOCForge/blob/05a4108e0c6288130dec1203b301979a91475fca/crates/jocforge-core/src/reduction.rs)：16 段归一化音频特征、信号/空间/能量权重 0.55/0.35/0.10、最远点种子、RMS 加权混合与质心、0.15 分组切换惩罚，以及 0.90 簇峰值上限。移植核心与未修改 Rust reducer 的原创合成输入结果做数值对照，覆盖普通输入、超峰值输入和语义种子。

为适配 Logic 动态 ADM 和 Audio Vivid，使用完整所选区间初始化种子，在每 1024 采样窗口更新特征和位置，使用 ADM Cartesian XYZ 计算距离，并在分组/增益切换时用 240 采样（5 ms）线性交叉淡化。这些是本工具的适配，不是 Dolby 官方算法，也不声称与 JOCForge 整条 E-AC-3 输出或 Dolby 编码器等价。JOCForge 的 JOC 编码器没有被打包进本工具。

输出 `.conversion.json` 记录算法来源、适配点、源角色、合并/增益变化及空间误差；`.reduction.npz` 保存每帧绝对采样位置、源轨索引、目标分组、贡献增益和聚合 XYZ，可用 `numpy.load(..., allow_pickle=False)` 读取。分组索引从 0 开始，实际输出载体需加独立 LFE 数量；PCM 按报告淡化长度从上一帧系数过渡到当前系数，之后应用全局 uniform_gain。位置误差只统计非静音分析窗口起点、Vivid 量化及交叉淡化前的差异，不代表实测渲染或听感质量。

## Windows 开发运行

要求：Windows 10 2004+ x64（建议 Windows 11）、Python 3.11+、.NET 8 SDK 或更新版本、Visual Studio 2022 C++ Build Tools（含 CMake 和 Windows SDK）、Git。

```powershell
git clone https://github.com/fengluoxiao/ADM-to-AV3A.git
cd ADM-to-AV3A
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

超过 16 路的 ADM 可接受有损空间缩减时：

```powershell
./.venv/Scripts/python.exe -m av3s.cli convert --input master.wav --output reduced.m4a --spatial-reduction
```

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

打包脚本会生成 `dist/ADM-to-AV3A-v0.5.0-win-x64-时间戳.zip`，内含 WinUI、自包含运行时、Cavern / MPEG-H 导入组件、Vivid / AC-4 编码器和 AC-4 对应源码；解压后运行 `AdmToAv3s.exe`，无需另装 Python、.NET 或 WSL。脚本使用包内运行时分别测试两个输出。AC-4 组件会重建；Cavern / MPEG-H 已存在时复用，可传 `-RebuildImporters` 重建。开发构建需要较新的 VS 2022 C++ 工具（MSVC 14.44+）、.NET SDK 及联网恢复依赖。AC-4 当前构建启用 AVX2；Vivid 编码器仍使用标量 C。GitHub Actions 会编译界面并运行真实编码测试。

仓库不含私人音乐、之前转换的歌曲或外部编码器二进制。原始应用代码使用 MIT，JOCForge 移植核心使用 Apache-2.0，AC-4 原生适配器使用 GPL-3.0-or-later；外部组件各自的条款见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
