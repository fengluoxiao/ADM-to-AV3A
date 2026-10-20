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
        Closed += (_, _) => { SignalCancel(); try { if (worker is { HasExited: false }) worker.Kill(true); } catch { } };
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
        if (await Pick(".wav", ".bw64", ".rf64") is string path)
        {
            InputBox.Text = path;
            OutputBox.Text = Path.Combine(Path.GetDirectoryName(path)!, Path.GetFileNameWithoutExtension(path) + " (Audio Vivid).m4a");
            string sidecar = Path.ChangeExtension(path, ".scene.jsonl");
            MetadataBox.Text = File.Exists(sidecar) ? sidecar : "";
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
            OutputBox.Text = Path.Combine(folder.Path, string.IsNullOrWhiteSpace(name) ? "Audio Vivid.m4a" : name);
        }
    }
    private void InputChanged(object sender, TextChangedEventArgs e)
    {
        if (SummaryText != null) SummaryText.Text = "输入已变化，请重新检查。";
    }

    private List<string> Arguments(string command)
    {
        if (!File.Exists(InputBox.Text)) throw new InvalidOperationException("请选择有效的 WAV 母版。");
        var args = new List<string> { "-m", "av3s.cli", command, "--input", Path.GetFullPath(InputBox.Text) };
        if (!string.IsNullOrWhiteSpace(MetadataBox.Text)) args.AddRange(new[] { "--metadata", Path.GetFullPath(MetadataBox.Text) });
        return args;
    }

    private async void InspectInput(object sender, RoutedEventArgs e)
    {
        if (busy) return;
        try { await Run(Arguments("inspect"), false); }
        catch (Exception ex) { ShowError(ex.Message); }
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
            if (!double.TryParse(StartBox.Text, NumberStyles.Float, CultureInfo.InvariantCulture, out double start) || !double.IsFinite(start) || start < 0)
                throw new InvalidOperationException("起始时间必须是非负秒数（小数点使用 .）。");
            args.AddRange(new[] { "--start", start.ToString(CultureInfo.InvariantCulture) });
            if (!string.IsNullOrWhiteSpace(DurationBox.Text))
            {
                if (!double.TryParse(DurationBox.Text, NumberStyles.Float, CultureInfo.InvariantCulture, out double duration) || !double.IsFinite(duration) || duration <= 0)
                    throw new InvalidOperationException("时长必须大于 0。");
                args.AddRange(new[] { "--duration", duration.ToString(CultureInfo.InvariantCulture) });
            }
            if (!string.IsNullOrWhiteSpace(TagsBox.Text)) args.AddRange(new[] { "--tags", Path.GetFullPath(TagsBox.Text) });
            await Run(args, true);
        }
        catch (Exception ex) { ShowError(ex.Message); }
    }

    private async Task Run(List<string> args, bool conversion)
    {
        busy = true;
        ConvertButton.IsEnabled = InspectButton.IsEnabled = false;
        CancelButton.IsEnabled = conversion;
        OpenButton.IsEnabled = false;
        Notice.IsOpen = false;
        LogBox.Text = "";
        Progress.Value = 0;
        StatusText.Text = conversion ? "开始转换…" : "检查输入…";
        cancelFile = Path.Combine(Path.GetTempPath(), "av3s-cancel-" + Guid.NewGuid().ToString("N"));
        if (conversion) args.AddRange(new[] { "--cancel-file", cancelFile });
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
                        SummaryText.Text = $"{ev.GetProperty("objects").GetInt32()} 个独立对象 · {ev.GetProperty("channels").GetInt32()} 路载体\n48 kHz · {ev.GetProperty("seconds").GetDouble():F2} 秒\n{ev.GetProperty("metadata_updates").GetInt32()} 次空间更新\n{ev.GetProperty("bitrate").GetInt32() / 1000} kbps 总码率";
                        StatusText.Text = "输入检查通过";
                        terminalEvent = true;
                    }
                    if (type == "error") { ShowError(msg.GetString() ?? "转换失败"); terminalEvent = true; }
                    if (type == "cancelled") { terminalEvent = true; }
                    if (type == "complete")
                    {
                        completedOutput = ev.GetProperty("output").GetString();
                        OpenButton.IsEnabled = true;
                        Notice.Severity = InfoBarSeverity.Success;
                        Notice.Title = "转换完成";
                        Notice.Message = "M4A、原始空间元数据和转换报告已保存。";
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
            ConvertButton.IsEnabled = InspectButton.IsEnabled = true;
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
