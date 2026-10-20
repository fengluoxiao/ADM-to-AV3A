using Microsoft.UI.Xaml;

namespace AdmToAv3s;

public partial class App : Application
{
    private Window? window;
    public App()
    {
        UnhandledException += (_, e) => LogStartupError(e.Exception);
        InitializeComponent();
    }
    protected override void OnLaunched(LaunchActivatedEventArgs args)
    {
        try
        {
            window = new MainWindow();
            window.Activate();
        }
        catch (Exception ex)
        {
            LogStartupError(ex);
            throw;
        }
    }

    private static void LogStartupError(Exception ex)
    {
        try
        {
            string folder = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "ADM-to-AV3S");
            Directory.CreateDirectory(folder);
            File.AppendAllText(Path.Combine(folder, "startup.log"), DateTimeOffset.Now + Environment.NewLine + ex + Environment.NewLine);
        }
        catch { /* Preserve the original startup exception. */ }
    }
}
