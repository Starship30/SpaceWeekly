using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Text.RegularExpressions;
using System.Windows.Forms;
using Microsoft.Win32;

internal static partial class Uninstall
{
    internal static string ProductCode(string command)
    {
        Match match = Regex.Match(command ?? "", @"^msiexec\.exe\s+/x\s+(\{[0-9a-f-]{36}\})$", RegexOptions.IgnoreCase);
        Guid product;
        if (!match.Success || !Guid.TryParse(match.Groups[1].Value, out product)) throw new InvalidDataException("Windows Installer 注册信息无效，请用安装包修复。");
        return match.Groups[1].Value;
    }

    internal static ProcessStartInfo Backend(string root, bool silent)
    {
        foreach (RegistryHive hive in new[] { RegistryHive.CurrentUser, RegistryHive.LocalMachine })
        foreach (RegistryView view in new[] { RegistryView.Registry64, RegistryView.Registry32 })
        {
            using (RegistryKey baseKey = RegistryKey.OpenBaseKey(hive, view))
            using (RegistryKey key = baseKey.OpenSubKey(@"Software\Microsoft\Windows\CurrentVersion\Uninstall\MSI:" + PackageId))
            {
                if (key == null) continue;
                string location = key.GetValue("InstallLocation") as string;
                if (String.IsNullOrEmpty(location) || !SamePath(location, root)) continue;
                string product = ProductCode(key.GetValue("UninstallString") as string);
                return new ProcessStartInfo(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), "msiexec.exe"),
                    "/x " + product + (silent ? " /qn /norestart" : ""));
            }
        }
        string updater = Path.Combine(root, "Update.exe");
        if (!File.Exists(updater)) throw new FileNotFoundException("找不到卸载组件，请用安装包修复。");
        return new ProcessStartInfo(updater, silent ? "--silent uninstall" : "uninstall");
    }

    internal static bool SamePath(string first, string second)
    {
        return String.Equals(Path.GetFullPath(first).TrimEnd('\\', '/'), Path.GetFullPath(second).TrimEnd('\\', '/'), StringComparison.OrdinalIgnoreCase);
    }

    static List<Process> Running(string root)
    {
        List<Process> result = new List<Process>();
        foreach (Process process in Process.GetProcessesByName("SpaceWeekly"))
        {
            try
            {
                if (process.SessionId == Process.GetCurrentProcess().SessionId &&
                    process.MainModule.FileName.StartsWith(root.TrimEnd('\\') + "\\", StringComparison.OrdinalIgnoreCase)) result.Add(process);
                else process.Dispose();
            }
            catch (InvalidOperationException) { process.Dispose(); }
        }
        return result;
    }

    static bool CloseSafely(List<Process> processes)
    {
        using (Form form = new Form())
        using (Timer timer = new Timer())
        {
            form.Text = "卸载 SpaceWeekly"; form.ClientSize = new Size(520, 180);
            form.AutoScaleDimensions = new SizeF(96, 96); form.AutoScaleMode = AutoScaleMode.Dpi;
            form.Font = new Font("Microsoft YaHei UI", 10); form.BackColor = Color.White; form.ForeColor = Color.FromArgb(34,43,61);
            form.FormBorderStyle = FormBorderStyle.FixedDialog; form.MaximizeBox = false; form.MinimizeBox = false;
            form.StartPosition = FormStartPosition.CenterScreen;
            Label status = new Label { Text = "正在等待 SpaceWeekly 保存并关闭。\n请在软件中处理未保存内容和后台任务。\n取消此窗口不会卸载程序。", AutoSize = false, Location = new Point(24,20), Size = new Size(470,100) };
            Button cancel = new Button { Text = "取消卸载", DialogResult = DialogResult.Cancel, Location = new Point(370,130), Size = new Size(124,32) };
            form.Controls.Add(status); form.Controls.Add(cancel); form.CancelButton = cancel;
            timer.Interval = 300;
            timer.Tick += delegate {
                if (processes.TrueForAll(p => p.HasExited)) { timer.Stop(); form.DialogResult = DialogResult.OK; form.Close(); }
            };
            foreach (Process process in processes) { if (!process.HasExited && process.MainWindowHandle != IntPtr.Zero) process.CloseMainWindow(); }
            timer.Start(); return form.ShowDialog() == DialogResult.OK;
        }
    }

    [STAThread]
    static int Main(string[] args)
    {
        bool silent = Array.IndexOf(args, "--silent") >= 0;
        try
        {
            Application.EnableVisualStyles(); Application.SetCompatibleTextRenderingDefault(false);
            string root = Path.GetDirectoryName(Application.ExecutablePath);
            if (String.Equals(Path.GetFileName(root), "current", StringComparison.OrdinalIgnoreCase)) root = Path.GetDirectoryName(root);
            if (!File.Exists(Path.Combine(root, "Update.exe"))) throw new InvalidOperationException("请从安装目录运行 Uninstall.exe；便携版不需要卸载。");
            if (!silent && Array.IndexOf(args, "--confirmed") < 0 && MessageBox.Show("将卸载 SpaceWeekly 程序。\n工作区、词典、图片、作品和独立语音模型会保留。\n\n是否继续？", "卸载 SpaceWeekly", MessageBoxButtons.YesNo, MessageBoxIcon.Question) != DialogResult.Yes) return 1;
            List<Process> running = Running(root);
            try
            {
                if (running.Count > 0 && (silent || !CloseSafely(running))) return 10;
            }
            finally { running.ForEach(p => p.Dispose()); }
            ProcessStartInfo command = Backend(root, silent);
            command.WorkingDirectory = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            command.UseShellExecute = true;
            Process.Start(command);
            // Exit before the framework removes this executable and its containing directory.
            return 0;
        }
        catch (Exception error)
        {
            if (!silent) MessageBox.Show("尚未开始卸载；程序和用户数据保留。\n" + error.Message, "卸载 SpaceWeekly", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 2;
        }
    }
}
