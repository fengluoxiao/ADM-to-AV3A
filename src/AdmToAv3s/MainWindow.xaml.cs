using System.Diagnostics;
using System.Globalization;
using System.Text;
using System.Text.Json;
using Microsoft.UI;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Windows.Storage.Pickers;
using WinRT.Interop;

namespace AdmToAv3s;

public sealed partial class MainWindow : Window
{
    private readonly string home;
    private string? cancelFile;
    private string? completedOutput;
    private Process? worker;
    private bool busy;

    public MainWindow()
    {
        InitializeComponent();
        ExtendsContentIntoTitleBar = true;
        SetTitleBar(TitleBar);
        var workArea = DisplayArea.GetFromWindowId(AppWindow.Id, DisplayAreaFallback.Primary).WorkArea;
        int width = Math.Min(1180, workArea.Width - 32);
        int height = Math.Min(940, workArea.Height - 32);
        AppWindow.Resize(new Windows.Graphics.SizeInt32(width, height));
        AppWindow.Move(new Windows.Graphics.PointInt32(workArea.X + (workArea.Width - width) / 2, workArea.Y + (workArea.Height - height) / 2));
        AppWindow.TitleBar.ButtonBackgroundColor = Colors.Transparent;
        AppWindow.TitleBar.ButtonForegroundColor = Colors.White;
        home = FindHome();
        string bundled = Path.Combine(home, "runtime", "python.exe");
        string venv = Path.Combine(home, ".venv", "Scripts", "python.exe");
        PythonBox.Text = File.Exists(bundled) ? bundled : File.Exists(venv) ? venv : "python";
        EncoderBox.Text = Path.Combine(home, ".tools", "bin", "avs3Encoder.exe");
        if (!File.Exists(EncoderBox.Text)) EncoderBox.Text = Path.Combine(home, "tools", "avs3Encoder.exe");
        Ac4EncoderBox.Text = Path.Combine(home, ".tools", "iclforge-bin", "Ac4Encode.exe");
        if (!File.Exists(Ac4EncoderBox.Text)) Ac4EncoderBox.Text = Path.Combine(home, "tools", "iclforge", "Ac4Encode.exe");
        UpdateFormat();
        Closed += (_, _) => { SignalCancel(); try { if (worker is { HasExited: false }) worker.Kill(true); } catch { } };
    }

    private bool IsAc4 => FormatBox.SelectedIndex == 1;
    private bool NativeAc4 => Ac4NativeBox.IsChecked == true;
    private void Ac4ModeChanged(object sender, RoutedEventArgs e) { if (FormatFooter != null) UpdateFormat(); }
    private void FormatChanged(object sender, SelectionChangedEventArgs e)
    {
        if (OutputBox == null || FormatFooter == null) return;
        UpdateFormat();
    }
    private void UpdateFormat()
    {
        BitrateBox.Visibility = IsAc4 ? Visibility.Visible : Visibility.Collapsed;
        Ac4NativeBox.Visibility = IsAc4 ? Visibility.Visible : Visibility.Collapsed;
        FormatBadge.Text = IsAc4 ? "AC-4 A-JOC · MP4" : "AUDIO VIVID · M4A";
        OutputBox.PlaceholderText = IsAc4 ? "输出 .mp4 文件（也支持 .m4a）" : "输出 .m4a 文件";
        ReductionLabel.Text = IsAc4
            ? $"允许 JOCForge 聚类：超出 {(NativeAc4 ? 64 : 20)} 路时，非 LFE 声床＋对象缩减为最多 {(NativeAc4 ? 63 : 19)} 个对象"
            : "允许 JOCForge 聚类：超出 16 路时，非 LFE 声床＋对象缩减为最多 15 个对象";
        FormatDetails.Text = IsAc4
            ? (NativeAc4 ? "AC-4 原生模式：最多 64 路。SDA 当前无法解码超过 20 路全频重建信号或原生声床；需要在 SDA 播放时请取消此选项。" : "AC-4 A-JOC 兼容模式：合计最多 20 路；LFE 独立，其他声床转为固定位置对象（位置近似）。对象声音由下混信号有损重建。")
            : "读取最多 128 路 ADM，支持 Logic Pro 声床与位置自动化。Audio Vivid 最多 16 路，支持 2.0、5.1、7.1.2 声床＋对象。";
        FormatFooter.Text = IsAc4 ? "48 kHz  /  AC-4 A-JOC  /  Float32 输入" : "48 kHz  /  LC  /  96 kbps 每载体";
        if (!string.IsNullOrWhiteSpace(OutputBox.Text))
        {
            if (IsAc4) OutputBox.Text = OutputBox.Text.Replace(" (Audio Vivid).m4a", " (AC-4 A-JOC).mp4");
            else OutputBox.Text = OutputBox.Text.Replace(" (AC-4 A-JOC).mp4", " (Audio Vivid).m4a")
                                               .Replace(" (AC-4 A-JOC).m4a", " (Audio Vivid).m4a");
        }
        SummaryText.Text = "输出格式已更新，请检查输入。";
        Notice.IsOpen = IsAc4 && NativeAc4;
        if (Notice.IsOpen)
        {
            Notice.Severity = InfoBarSeverity.Warning;
            Notice.Title = "原生模式不保证 SDA 兼容";
            Notice.Message = "超过 20 路全频信号或含原生声床时，SDA 会拒绝解码。用于 SDA 请取消原生模式；超容量输入需开启空间聚类。";
        }
    }

    private static string FindHome()
    {
        var configured = Environment.GetEnvironmentVariable("AV3S_HOME");
        if (!string.IsNullOrWhiteSpace(configured)) return Path.GetFullPath(configured);
        DirectoryInfo? dir = new(AppContext.BaseDirectory);
        while (dir != null)
        {
            if (Directory.Exists(Path.Combine(dir.FullName, "backend", "av3s"))) return dir.FullName;
            dir = dir.Parent;
        }
        return AppContext.BaseDirectory;
    }

    private async Task<string?> Pick(params string[] extensions)
    {
        if (busy) return null;
        var picker = new FileOpenPicker();
        InitializeWithWindow.Initialize(picker, WindowNative.GetWindowHandle(this));
        foreach (string ext in extensions) picker.FileTypeFilter.Add(ext);
        return (await picker.PickSingleFileAsync())?.Path;
    }

    private async void BrowseInput(object sender, RoutedEventArgs e)
    {
        if (await Pick(".wav", ".bw64", ".rf64", ".m4a", ".mp4", ".eac3", ".ec3", ".mhas", ".mha", ".mhm") is string path)
        {
            InputBox.Text = path;
            OutputBox.Text = Path.Combine(Path.GetDirectoryName(path)!, Path.GetFileNameWithoutExtension(path) + (IsAc4 ? " (AC-4 A-JOC).mp4" : " (Audio Vivid).m4a"));
            string sidecar = Path.ChangeExtension(path, ".scene.jsonl");
            bool compressed = new[] { ".m4a", ".mp4", ".eac3", ".ec3", ".mhas", ".mha", ".mhm" }.Contains(Path.GetExtension(path).ToLowerInvariant());
            MetadataBox.Text = !compressed && File.Exists(sidecar) ? sidecar : "";
        }
    }
    private async void BrowseMetadata(object sender, RoutedEventArgs e) { if (await Pick(".xml", ".axml", ".jsonl") is string p) MetadataBox.Text = p; }
    private async void BrowseTags(object sender, RoutedEventArgs e) { if (await Pick(".m4a", ".mp4") is string p) TagsBox.Text = p; }
    private async void BrowseOutput(object sender, RoutedEventArgs e)
    {
        if (busy) return;
        var picker = new FolderPicker();
        picker.FileTypeFilter.Add("*");
        InitializeWithWindow.Initialize(picker, WindowNative.GetWindowHandle(this));
        var folder = await picker.PickSingleFolderAsync();
        if (folder != null)
        {
            string name = Path.GetFileName(OutputBox.Text);
            OutputBox.Text = Path.Combine(folder.Path, string.IsNullOrWhiteSpace(name) ? (IsAc4 ? "AC-4 A-JOC.mp4" : "Audio Vivid.m4a") : name);
        }
    }
    private void InputChanged(object sender, TextChangedEventArgs e)
    {
        if (SummaryText != null) SummaryText.Text = "输入已变化，请重新检查。";
    }

    private List<string> Arguments(string command)
    {
        if (!File.Exists(InputBox.Text)) throw new InvalidOperationException("请选择有效的母版、Atmos 或 360RA 文件。");
        var args = new List<string> { "-m", "av3s.cli", command, "--input", Path.GetFullPath(InputBox.Text) };
        if (!string.IsNullOrWhiteSpace(MetadataBox.Text)) args.AddRange(new[] { "--metadata", Path.GetFullPath(MetadataBox.Text) });
        return args;
    }

    private async void InspectInput(object sender, RoutedEventArgs e)
    {
        if (busy) return;
        try { var args = Arguments("inspect"); AddCodecArguments(args); await Run(args, false); }
        catch (Exception ex) { ShowError(ex.Message); }
    }

    private void AddCodecArguments(List<string> args)
    {
        args.AddRange(new[] { "--codec", IsAc4 ? "ac4" : "av3a" });
        if (IsAc4 && BitrateBox.SelectedItem is ComboBoxItem item)
            args.AddRange(new[] { "--bitrate", item.Tag.ToString()! });
        if (IsAc4 && NativeAc4) args.Add("--ac4-native");
    }

    private async void ExportObjects(object sender, RoutedEventArgs e)
    {
        if (busy) return;
        try
        {
            var args = Arguments("export");
            var picker = new FolderPicker();
            InitializeWithWindow.Initialize(picker, WindowNative.GetWindowHandle(this));
            picker.FileTypeFilter.Add("*");
            var folder = await picker.PickSingleFolderAsync();
            if (folder == null) return;
            string output = Path.Combine(folder.Path, Path.GetFileNameWithoutExtension(InputBox.Text) + " (decoded objects).wav");
            args.AddRange(new[] { "--output", output });
            AddRangeArguments(args);
            await Run(args, true);
        }
        catch (Exception ex) { ShowError(ex.Message); }
    }

    private void AddRangeArguments(List<string> args)
    {
        if (!double.TryParse(StartBox.Text, NumberStyles.Float, CultureInfo.InvariantCulture, out double start) || !double.IsFinite(start) || start < 0)
            throw new InvalidOperationException("起始时间必须是非负秒数（小数点使用 .）。");
        args.AddRange(new[] { "--start", start.ToString(CultureInfo.InvariantCulture) });
        if (!string.IsNullOrWhiteSpace(DurationBox.Text))
        {
            if (!double.TryParse(DurationBox.Text, NumberStyles.Float, CultureInfo.InvariantCulture, out double duration) || !double.IsFinite(duration) || duration <= 0)
                throw new InvalidOperationException("时长必须大于 0。");
            args.AddRange(new[] { "--duration", duration.ToString(CultureInfo.InvariantCulture) });
        }
    }

    private async void StartConversion(object sender, RoutedEventArgs e)
    {
        if (busy) return;
        try
        {
            var args = Arguments("convert");
            if (string.IsNullOrWhiteSpace(OutputBox.Text)) throw new InvalidOperationException("请选择输出路径。");
            if (File.Exists(OutputBox.Text)) throw new InvalidOperationException("输出文件已存在，请使用新的文件名。");
            args.AddRange(new[] { "--output", Path.GetFullPath(OutputBox.Text), "--encoder", EncoderBox.Text });
            AddCodecArguments(args);
            if (IsAc4) args.AddRange(new[] { "--ac4-encoder", Ac4EncoderBox.Text });
            AddRangeArguments(args);
            if (!string.IsNullOrWhiteSpace(TagsBox.Text)) args.AddRange(new[] { "--tags", Path.GetFullPath(TagsBox.Text) });
            if (ReductionBox.IsChecked == true) args.Add("--spatial-reduction");
            await Run(args, true);
        }
        catch (Exception ex) { ShowError(ex.Message); }
    }

    private async Task Run(List<string> args, bool conversion)
    {
        busy = true;
        ConvertButton.IsEnabled = InspectButton.IsEnabled = ExportButton.IsEnabled = false;
        FormatBox.IsEnabled = BitrateBox.IsEnabled = Ac4NativeBox.IsEnabled = false;
        CancelButton.IsEnabled = true;
        OpenButton.IsEnabled = false;
        Notice.IsOpen = false;
        LogBox.Text = "";
        Progress.Value = 0;
        StatusText.Text = conversion ? "开始转换…" : "检查输入…";
        cancelFile = Path.Combine(Path.GetTempPath(), "av3s-cancel-" + Guid.NewGuid().ToString("N"));
        args.AddRange(new[] { "--cancel-file", cancelFile });
        var psi = new ProcessStartInfo(PythonBox.Text.Trim())
        {
            WorkingDirectory = home, UseShellExecute = false, CreateNoWindow = true,
            RedirectStandardOutput = true, RedirectStandardError = true,
            StandardOutputEncoding = Encoding.UTF8, StandardErrorEncoding = Encoding.UTF8
        };
        foreach (string arg in args) psi.ArgumentList.Add(arg);
        psi.Environment["PYTHONPATH"] = Path.Combine(home, "backend");
        psi.Environment["PYTHONUTF8"] = "1";
        bool terminalEvent = false;
        try
        {
            using var process = Process.Start(psi) ?? throw new InvalidOperationException("无法启动 Python。");
            worker = process;
            var errors = process.StandardError.ReadToEndAsync();
            string? line;
            while ((line = await process.StandardOutput.ReadLineAsync()) != null)
            {
                try
                {
                    using var doc = JsonDocument.Parse(line);
                    var ev = doc.RootElement;
                    string type = ev.GetProperty("type").GetString()!;
                    if (ev.TryGetProperty("message", out var msg))
                    {
                        StatusText.Text = msg.GetString();
                        LogBox.Text = (LogBox.Text + msg.GetString() + Environment.NewLine);
                        if (LogBox.Text.Length > 12000) LogBox.Text = LogBox.Text[^10000..];
                        LogBox.SelectionStart = LogBox.Text.Length;
                    }
                    if (ev.TryGetProperty("progress", out var progress)) Progress.Value = progress.GetDouble();
                    if (type == "inspection")
                    {
                        int channels = ev.GetProperty("channels").GetInt32();
                        int bed = ev.GetProperty("bed_channels").GetInt32();
                        bool fits = ev.GetProperty("fits_encoder").GetBoolean();
                        int capacity = ev.GetProperty("encoder_capacity").GetInt32();
                        string layout = ev.GetProperty("bed_layout").GetString() ?? "无";
                        SummaryText.Text = $"{ev.GetProperty("objects").GetInt32()} 个独立对象 · {bed} 路声床（{layout}）\n{channels} 路 PCM 轨道 · 48 kHz\n{ev.GetProperty("seconds").GetDouble():F2} 秒 · {ev.GetProperty("metadata_updates").GetInt32()} 个空间更新时间\n" +
                            (fits ? $"{ev.GetProperty("bitrate").GetInt32() / 1000} kbps 总码率" : $"超过当前 {capacity} 路编码容量");
                        if (ev.TryGetProperty("decoder", out var decoder))
                            SummaryText.Text += decoder.GetString() == "Ittiam/libmpegh"
                                ? "\n360RA → MPEG-H 对象解码；位置与尺寸映射存在近似，原始 OAM 另存。"
                                : "\nAtmos JOC → Cavern 对象解码；转换时自动读取空间元数据和源标签。";
                        StatusText.Text = fits ? "输入检查通过" : "输入已读取；轨道数超过编码容量";
                        if (!fits)
                        {
                            Notice.Severity = InfoBarSeverity.Warning;
                            Notice.Title = "母版读取成功，尚不能全量编码";
                            Notice.Message = $"文件含 {channels} 路实际 PCM 轨道。当前输出编码器最多支持 {capacity} 路；如接受合并对象和位置近似，请在输出设置中开启空间聚类。";
                            Notice.IsOpen = true;
                        }
                        terminalEvent = true;
                    }
                    if (type == "error") { ShowError(msg.GetString() ?? "转换失败"); terminalEvent = true; }
                    if (type == "cancelled") { terminalEvent = true; }
                    if (type == "export_complete")
                    {
                        completedOutput = ev.GetProperty("output").GetString();
                        OpenButton.IsEnabled = true;
                        Notice.Severity = InfoBarSeverity.Success;
                        Notice.Title = "对象导出完成";
                        Notice.Message = "已保存对象 WAV、scene.jsonl 和导出报告。再次编码时选同名 JSONL，以保留空间元数据；360RA 另存原始 OAM。";
                        Notice.IsOpen = true;
                        terminalEvent = true;
                    }
                    if (type == "complete")
                    {
                        completedOutput = ev.GetProperty("output").GetString();
                        OpenButton.IsEnabled = true;
                        Notice.Severity = InfoBarSeverity.Success;
                        Notice.Title = "转换完成";
                        bool reduced = ev.TryGetProperty("spatial_reduction", out var reduction) && reduction.GetBoolean();
                        Notice.Message = reduced
                            ? $"已输出 {ev.GetProperty("bed_channels").GetInt32()} 路独立 LFE＋{ev.GetProperty("encoded_objects").GetInt32()} 路 JOCForge 聚类对象。非 LFE 声床与对象已合并，位置及混合增益有变化；逐帧映射、原始元数据和误差报告已保存。"
                            : "输出音频、原始空间元数据和转换报告已保存。";
                        if (ev.TryGetProperty("output_codec", out var codec) && codec.GetString() == "ac4")
                        {
                            Notice.Severity = InfoBarSeverity.Warning;
                            Notice.Message += " AC-4 A-JOC 为实验编码：对象音频经有损重建，播放器兼容性待验证；详情见转换报告。";
                            if (ev.TryGetProperty("sda_structure_compatible", out var compatible) && !compatible.GetBoolean())
                                Notice.Message += " 此输出的对象数量或声床结构超出 SDA 当前支持范围；请用兼容模式重新转换。";
                        }
                        Notice.IsOpen = true;
                        terminalEvent = true;
                    }
                }
                catch (JsonException) { LogBox.Text += line + Environment.NewLine; }
            }
            await process.WaitForExitAsync();
            string stderr = await errors;
            if (!terminalEvent || (process.ExitCode != 0 && process.ExitCode != 2 && !Notice.IsOpen))
                ShowError(string.IsNullOrWhiteSpace(stderr) ? $"后端意外退出（{process.ExitCode}）。" : stderr[^Math.Min(2000, stderr.Length)..]);
        }
        finally
        {
            worker = null;
            if (cancelFile != null) File.Delete(cancelFile);
            cancelFile = null;
            busy = false;
            ConvertButton.IsEnabled = InspectButton.IsEnabled = ExportButton.IsEnabled = true;
            FormatBox.IsEnabled = BitrateBox.IsEnabled = Ac4NativeBox.IsEnabled = true;
            CancelButton.IsEnabled = false;
        }
    }

    private void SignalCancel()
    {
        if (cancelFile != null) File.WriteAllText(cancelFile, "cancel");
    }
    private void CancelConversion(object sender, RoutedEventArgs e)
    {
        SignalCancel();
        StatusText.Text = "正在取消…";
        CancelButton.IsEnabled = false;
    }
    private void ShowError(string message)
    {
        StatusText.Text = "未完成";
        Notice.Severity = InfoBarSeverity.Error;
        Notice.Title = "请检查输入或运行环境";
        Notice.Message = message;
        Notice.IsOpen = true;
    }
    private void OpenOutput(object sender, RoutedEventArgs e)
    {
        if (completedOutput != null)
        {
            var psi = new ProcessStartInfo("explorer.exe") { UseShellExecute = false };
            psi.ArgumentList.Add("/select,");
            psi.ArgumentList.Add(completedOutput);
            Process.Start(psi);
        }
    }
}
