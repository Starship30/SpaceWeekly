using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Security.Cryptography;
using System.Windows.Forms;

// EXE only carries the exact MSI and delegates the entire UI and installation to it.
internal static partial class Setup
{
    [STAThread]
    static int Main(string[] args)
    {
        string directory = Path.Combine(Path.GetTempPath(), "SpaceWeekly-Setup-" + Guid.NewGuid().ToString("N"));
        try {
            Application.EnableVisualStyles();
            Directory.CreateDirectory(directory);
            string msi = Path.Combine(directory, "SpaceWeekly.msi");
            using (Stream source = Assembly.GetExecutingAssembly().GetManifestResourceStream("SpaceWeekly.msi"))
            using (FileStream target = new FileStream(msi, FileMode.CreateNew, FileAccess.Write)) source.CopyTo(target);
            using (var sha = SHA256.Create())
            using (var file = File.OpenRead(msi)) {
                string hash = BitConverter.ToString(sha.ComputeHash(file)).Replace("-", "").ToLowerInvariant();
                if (hash != PayloadHash) throw new InvalidDataException("安装包校验失败，请重新获取安装程序。");
            }
            var start = new ProcessStartInfo(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), "msiexec.exe"),
                "/i \"" + msi + "\" /norestart");
            start.UseShellExecute = false;
            using (Process process = Process.Start(start)) { process.WaitForExit(); return process.ExitCode; }
        }
        catch (Exception error) {
            MessageBox.Show(error.Message, "SpaceWeekly 安装", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
        finally {
            // Only remove the MSI extracted by this process. Windows Installer retains its own cache.
            try { File.Delete(Path.Combine(directory, "SpaceWeekly.msi")); Directory.Delete(directory); } catch (IOException) { }
        }
    }
}
