using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Threading;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Documents;
using System.Windows.Markup;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Interop;
using System.Runtime.InteropServices;
using Microsoft.Win32;
using WixToolset.Dtf.WindowsInstaller;

public class SpaceWeeklyInstaller : IEmbeddedUI
{
    Thread thread;
    Wizard wizard;
    Exception uiError;
    readonly ManualResetEvent start = new ManualResetEvent(false), exit = new ManualResetEvent(false);
    public bool Initialize(Session session, string resourcePath, ref InstallUIOptions level)
    {
        if ((level & InstallUIOptions.Full) != InstallUIOptions.Full) return false;
        string packageId = session["SW_PACKAGE_ID"];
        string existing = ExistingDirectory(packageId);
        string target = existing.Length > 0 ? existing : session["VELOPACK_INSTALLDIR"];
        if (target.Length == 0) target = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), packageId);
        bool maintenance = session.EvaluateCondition("Installed");
        long bytes = 0;
        using (var view = session.Database.OpenView("SELECT `FileSize` FROM `File`")) {
            view.Execute(); Record row;
            while ((row = view.Fetch()) != null) using (row) bytes += row.GetInteger(1);
        }
        string version = session["SW_DISPLAY_VERSION"], remove = session["REMOVE"];
        thread = new Thread(delegate() {
            try {
                wizard = new Wizard(start, target, existing.Length > 0, maintenance, version, bytes, remove);
                new Application().Run(wizard.Window);
            } catch (Exception error) { uiError = error; }
            finally { exit.Set(); }
        });
        thread.SetApartmentState(ApartmentState.STA); thread.Start();
        int choice;
        // Keep MSI informed while the user reads the agreement instead of leaving its engine idle.
        while ((choice = WaitHandle.WaitAny(new WaitHandle[] {start, exit}, 10000)) == WaitHandle.WaitTimeout)
            session.Log("SpaceWeekly: waiting for installation approval; no files changed.");
        if (choice != 0) {
            if (uiError != null) throw new InstallerException("无法打开安装界面：" + uiError.Message);
            throw new InstallCanceledException();
        }
        session["VELOPACK_INSTALLDIR"] = wizard.Target;
        session["INSTALLFOLDER"] = wizard.Target;
        session["SW_DESKTOP"] = wizard.Desktop ? "1" : "0";
        session["LicenseAccepted"] = "1";
        session["SW_WIZARD_APPROVED"] = "1";
        session["SW_ACCEPT_AGREEMENT"] = "1";
        if (wizard.Operation == "remove") session["REMOVE"] = "ALL";
        if (wizard.Operation == "repair") session["REINSTALL"] = "ALL";
        session["WIXUI_EXITDIALOGOPTIONALCHECKBOX"] = "";
        level = InstallUIOptions.NoChange | InstallUIOptions.SourceResolutionOnly;
        return true;
    }
    internal static string ExistingDirectory(string packageId)
    {
        foreach (string id in new [] {"MSI:" + packageId, packageId})
            using (var key = Registry.CurrentUser.OpenSubKey(@"Software\Microsoft\Windows\CurrentVersion\Uninstall\" + id)) {
                string path = key == null ? "" : key.GetValue("InstallLocation", "").ToString();
                if (!String.IsNullOrWhiteSpace(path) && Path.IsPathRooted(path)) return path;
            }
        return "";
    }
    public MessageResult ProcessMessage(InstallMessage type, Record record, MessageButtons buttons, MessageIcon icon, MessageDefaultButton defaultButton)
    {
        return (MessageResult)wizard.Window.Dispatcher.Invoke(new Func<MessageResult>(delegate() {
            return wizard.Message(type, record, buttons);
        }));
    }
    public void Shutdown()
    {
        wizard.Window.Dispatcher.BeginInvoke(new Action(wizard.Complete));
        thread.Join();
    }
}

internal class Wizard
{
    internal Window Window;
    readonly ManualResetEvent start;
    readonly long bytes;
    readonly bool locked, maintenance;
    readonly string version;
    int page, result = -1;
    bool started, cancelling, cancelSent, allowClose;
    double total, ticks;
    int direction = 1;
    StackPanel body;
    Button next, back, cancel;
    TextBox path;
    CheckBox consent, desktop, launch;
    TextBlock heading, description, error, space, activity;
    ProgressBar progress;
    RadioButton repair, remove;
    ScrollViewer agreementReader;
    internal string Target, Operation = "install";
    internal bool Desktop = true;

    static Stream Resource(string name) { return Assembly.GetExecutingAssembly().GetManifestResourceStream(name); }
    internal Wizard(ManualResetEvent start, string target, bool locked, bool maintenance, string version, long bytes, string remove)
    {
        this.start = start; Target = target; this.locked = locked; this.maintenance = maintenance;
        this.version = version; this.bytes = bytes;
        using (Stream source = Resource("Installer.xaml")) Window = (Window)XamlReader.Load(source);
        using (Stream source = Resource("App.ico")) Window.Icon = BitmapFrame.Create(source,BitmapCreateOptions.PreservePixelFormat,BitmapCacheOption.OnLoad);
        Window.Width=Math.Min(Window.Width,SystemParameters.WorkArea.Width-24);
        Window.Height=Math.Min(Window.Height,SystemParameters.WorkArea.Height-24);
        Window.MinWidth=Math.Min(Window.MinWidth,Window.Width);Window.MinHeight=Math.Min(Window.MinHeight,Window.Height);
        body = Find<StackPanel>("Content"); next = Find<Button>("Next"); back = Find<Button>("Back"); cancel = Find<Button>("Cancel");
        heading = Find<TextBlock>("Heading"); description = Find<TextBlock>("Description"); error = Find<TextBlock>("Error");
        Find<ScrollViewer>("ContentScroll").SizeChanged += delegate { if (agreementReader!=null) agreementReader.Height=Math.Max(32,Find<ScrollViewer>("ContentScroll").ActualHeight-52); };
        Find<TextBlock>("Version").Text = "版本 " + version;
        ApplyTheme();
        Window.SourceInitialized += delegate { ApplyTheme(); };
        UserPreferenceChangedEventHandler themeChanged = delegate { Window.Dispatcher.BeginInvoke(new Action(ApplyTheme)); };
        SystemEvents.UserPreferenceChanged += themeChanged;
        Window.Closed += delegate { SystemEvents.UserPreferenceChanged -= themeChanged; };
        next.Click += delegate { Advance(); }; back.Click += delegate { page--; Show(); };
        cancel.Click += delegate { Find<Border>("CancelPanel").Visibility = Visibility.Visible; };
        Find<Button>("Keep").Click += delegate { Find<Border>("CancelPanel").Visibility = Visibility.Collapsed; };
        Find<Button>("ConfirmCancel").Click += delegate {
            if (!started) { allowClose = true; Window.Close(); }
            else { cancelling = true; Find<Border>("CancelPanel").Visibility = Visibility.Collapsed; cancel.IsEnabled = false; activity.Text = "正在取消并恢复已执行的安装操作…"; }
        };
        Window.Closing += delegate(object sender, System.ComponentModel.CancelEventArgs e) {
            if (!allowClose) { e.Cancel = true; Find<Border>("CancelPanel").Visibility = Visibility.Visible; }
        };
        if (String.Equals(remove,"ALL",StringComparison.OrdinalIgnoreCase)) Operation = "remove";
        Show();
    }
    T Find<T>(string name) { return (T)Window.FindName(name); }
    TextBlock Text(string text, double size = 14) {
        var item = new TextBlock {Text = text, TextWrapping = TextWrapping.Wrap, FontSize = size, Margin = new Thickness(0,0,0,12)};
        body.Children.Add(item); return item;
    }
    void Show()
    {
        body.Children.Clear();agreementReader=null;error.Text = ""; next.IsEnabled = true;
        Find<ScrollViewer>("ContentScroll").VerticalScrollBarVisibility=page==1?ScrollBarVisibility.Disabled:ScrollBarVisibility.Auto;
        heading.FontSize=page==0 && !maintenance?34:26;description.FontSize=page==0 && !maintenance?20:15;
        Find<Grid>("Body").Margin=page==0 && !maintenance?new Thickness(56,48,48,28):new Thickness(32,24,32,16);
        Find<Border>("CancelPanel").Visibility = Visibility.Collapsed;
        back.Visibility = page > 0 && page < 4 ? Visibility.Visible : Visibility.Collapsed;
        cancel.Visibility = page < 5 ? Visibility.Visible : Visibility.Collapsed;
        next.Content = page == 3 ? (Operation == "remove" ? "开始卸载" : "开始安装") : page == 5 ? "完成" : "下一步";
        if (page == 0) {
            heading.Text = maintenance ? "管理 SpaceWeekly" : "欢迎使用 SpaceWeekly";
            if (!maintenance) {
                heading.Inlines.Clear();heading.Inlines.Add(new Run("欢迎使用 "));
                heading.Inlines.Add(new Run("SpaceWeekly") {Foreground=new SolidColorBrush(Color.FromRgb(83,117,255))});
            }
            description.Text = "航天新闻与发射综述制作工具";
            if (!maintenance) { Text("•  整理航天信息",19).Margin=new Thickness(0,28,0,16);Text("•  制作发射综述",19); }
            if (maintenance) {
                repair = new RadioButton { Content = "修复程序", IsChecked = Operation != "remove", Margin = new Thickness(0,16,0,8) };
                remove = new RadioButton { Content = "卸载程序，保留作品、配置和模型", IsChecked = Operation == "remove", Margin = new Thickness(0,8,0,0) };
                body.Children.Add(repair); body.Children.Add(remove);
            }
        } else if (page == 1) {
            heading.Text = "阅读用户协议"; description.Text = "测试草案 · 版本 2026-11-07-draft.1 · 尚未经过法律审核";
            var paper = new StackPanel {Margin = new Thickness(16)};
            using (var reader = new StreamReader(Resource("Agreement.md"))) {
                string line; while ((line = reader.ReadLine()) != null) if (line.Length > 0)
                    paper.Children.Add(new TextBlock {Text = line.TrimStart('#',' '), TextWrapping = TextWrapping.Wrap,
                        FontSize = line.StartsWith("#") ? 17 : 14, FontWeight = line.StartsWith("#") ? FontWeights.SemiBold : FontWeights.Normal,
                        Margin = new Thickness(0,0,0,12)});
            }
            agreementReader=new ScrollViewer {Content=paper,Height=Math.Max(32,Find<ScrollViewer>("ContentScroll").ActualHeight-52),VerticalScrollBarVisibility=ScrollBarVisibility.Auto};
            var agreement = new Border {BorderThickness = new Thickness(1), CornerRadius = new CornerRadius(8),Child=agreementReader};
            agreement.SetResourceReference(Border.BackgroundProperty,"Panel");agreement.SetResourceReference(Border.BorderBrushProperty,"Line");body.Children.Add(agreement);
            consent = new CheckBox { Content = "我已阅读并同意用户协议（测试草案）", IsChecked = false };
            consent.Checked += delegate { next.IsEnabled = true; }; consent.Unchecked += delegate { next.IsEnabled = false; };
            body.Children.Add(consent); next.IsEnabled = false;
        } else if (page == 2) {
            heading.Text = "选择安装位置"; description.Text = locked ? "已检测到现有程序，将沿用原目录。" : "选择程序保存位置，支持其他磁盘及中文、空格路径。";
            Text("程序文件夹");
            var grid = new Grid(); grid.ColumnDefinitions.Add(new ColumnDefinition()); grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            path = new TextBox {Text = Target, IsReadOnly = locked};
            var browse = new Button {Content = "浏览", IsEnabled = !locked}; Grid.SetColumn(browse,1); grid.Children.Add(path); grid.Children.Add(browse);
            browse.Click += delegate {
                using (var picker = new System.Windows.Forms.FolderBrowserDialog { Description = "选择 SpaceWeekly 程序安装位置", SelectedPath = path.Text })
                    if (picker.ShowDialog() == System.Windows.Forms.DialogResult.OK) path.Text = picker.SelectedPath;
            };
            body.Children.Add(grid); space = Text("",13); space.Margin = new Thickness(0,12,0,20);
            path.TextChanged += delegate { RefreshSpace(); }; RefreshSpace();
            Text("工作区、词典、音色与模型不会随安装目录移动。普通卸载会保留这些资料。");
            desktop = new CheckBox {Content = "创建桌面快捷方式", IsChecked = Desktop}; body.Children.Add(desktop);
        } else if (page == 3) {
            heading.Text = Operation == "remove" ? "确认卸载" : "准备安装"; description.Text = "请确认以下选项。";
            Text("SpaceWeekly  " + version,18); Text("安装位置\n" + Target);
            Text(Operation == "remove" ? "仅卸载程序，保留独立保存的工作区、音色与模型。" : "开始菜单快捷方式\n" + (Desktop ? "同时创建桌面快捷方式" : "不创建桌面快捷方式"));
            Text("语音模型不包含在安装包中，可在软件的“配置语音”中准备。");
        } else if (page == 4) {
            heading.Text = Operation == "remove" ? "正在卸载" : "正在安装 SpaceWeekly"; description.Text = "请等待操作完成。";
            progress = new ProgressBar {Height = 8, IsIndeterminate = true, Margin = new Thickness(0,24,0,24), Foreground = new SolidColorBrush(Color.FromRgb(68,104,232))}; body.Children.Add(progress);
            activity = Text("正在准备安装文件…"); next.Visibility = Visibility.Collapsed;
        } else {
            bool success = result == 1 || result == 0 || result == 3010;
            heading.Text = success ? (Operation == "remove" ? "卸载完成" : "安装完成") : cancelling ? "安装已取消" : "安装未完成";
            description.Text = success ? "可以关闭此向导。" : "未将失败结果标记为可用，请查看安装日志后重试。";
            Text(success ? (Operation == "remove" ? "工作区、配置和模型仍保存在原位置。" : "SpaceWeekly 已准备就绪。") : "安装返回码：" + result);
            if (success && Operation != "remove") { launch = new CheckBox {Content = "完成后启动 SpaceWeekly", IsChecked = true}; body.Children.Add(launch); }
            next.Visibility = Visibility.Visible;
        }
    }
    void RefreshSpace() {
        try { var drive = new DriveInfo(Path.GetPathRoot(Path.GetFullPath(path.Text)));
            space.Text = String.Format("程序约需 {0:N0} MB  ·  当前磁盘可用 {1:N1} GB", bytes / 1048576.0, drive.AvailableFreeSpace / 1073741824.0);
        } catch (Exception) { space.Text = "填写有效的本地路径后，将显示磁盘空间。"; }
    }
    [DllImport("dwmapi.dll")] static extern int DwmSetWindowAttribute(IntPtr window, int attribute, ref int value, int size);
    void ApplyTheme()
    {
        bool dark = false;
        using (var key = Registry.CurrentUser.OpenSubKey(@"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"))
            dark = key != null && Convert.ToInt32(key.GetValue("AppsUseLightTheme",1)) == 0;
        ApplyTheme(dark);
    }
    void ApplyTheme(bool dark)
    {
        string[] names = {"Background","Panel","Foreground","Muted","Line","Tint"};
        string[] colors = dark ? new [] {"#111827","#1C2638","#E7ECF5","#A9B6CA","#3A475E","#273753"} :
            new [] {"#F5F6F9","#FFFFFF","#253047","#768197","#DCE1EA","#EEF2FB"};
        for (int i=0;i<names.Length;i++) Window.Resources[names[i]] = new SolidColorBrush((Color)ColorConverter.ConvertFromString(colors[i]));
        if (dark) Window.Resources["Background"]=new LinearGradientBrush(Color.FromRgb(19,28,43),Color.FromRgb(24,35,52),45);
        using (Stream file = Resource(dark ? "BrandWhite.png" : "BrandColor.png")) {
            BitmapSource source = BitmapFrame.Create(file,BitmapCreateOptions.PreservePixelFormat,BitmapCacheOption.OnLoad);
            source = new FormatConvertedBitmap(source,PixelFormats.Bgra32,null,0);
            int width=source.PixelWidth,height=source.PixelHeight,stride=width*4,left=width,top=height,right=0,bottom=0;
            byte[] pixels = new byte[stride*height];source.CopyPixels(pixels,stride,0);
            for (int y=0;y<height;y++) for(int x=0;x<width;x++) if (pixels[y*stride+x*4+3] > 0) {
                left=Math.Min(left,x);right=Math.Max(right,x);top=Math.Min(top,y);bottom=Math.Max(bottom,y);
            }
            if (right>=left && bottom>=top) source = new CroppedBitmap(source,new Int32Rect(left,top,right-left+1,bottom-top+1));
            source.Freeze();Find<System.Windows.Controls.Image>("Brand").Source=source;
        }
        IntPtr handle = new WindowInteropHelper(Window).Handle;
        if (handle != IntPtr.Zero) { int value = dark ? 1 : 0; DwmSetWindowAttribute(handle,20,ref value,4); }
    }
    void Advance()
    {
        try {
            if (page == 0 && maintenance) { Operation = remove.IsChecked == true ? "remove" : "repair"; page = 3; }
            else if (page == 1 && consent.IsChecked != true) return;
            else if (page == 2) { Target = ValidateTarget(path.Text, bytes, locked); Desktop = desktop.IsChecked == true; page++; }
            else if (page == 3) { page = 4; started = true; Show(); start.Set(); return; }
            else if (page == 5) {
                allowClose = true; Window.Close();
                if (launch != null && launch.IsChecked == true) Process.Start(new ProcessStartInfo(Path.Combine(Target,"current","SpaceWeekly.exe")) {UseShellExecute = true});
                return;
            }
            else page++;
            Show();
        } catch (Exception e) { error.Text = e.Message; }
    }
    internal static string ValidateTarget(string value, long bytes, bool existing)
    {
        if (String.IsNullOrWhiteSpace(value) || !Path.IsPathRooted(value) || value.StartsWith(@"\\")) throw new IOException("请选择有效的本地绝对路径。");
        string target = Path.GetFullPath(value.Trim()).TrimEnd('\\');
        if (target.Length < 4 || target.IndexOfAny(new [] {'"','<','>','|','*','?'}) >= 0) throw new IOException("请为程序指定文件夹，不能直接安装到磁盘根目录。");
        foreach (string part in target.Substring(3).Split('\\'))
            if (part.EndsWith(".") || part.EndsWith(" ") || System.Text.RegularExpressions.Regex.IsMatch(part,@"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)",System.Text.RegularExpressions.RegexOptions.IgnoreCase))
                throw new IOException("安装路径包含 Windows 不支持的文件夹名称。");
        if (File.Exists(target)) throw new IOException("该路径是文件，请选择文件夹。");
        if (!existing && Directory.Exists(target) && Directory.GetFileSystemEntries(target).Length > 0) throw new IOException("请选择空文件夹，避免覆盖其他程序或用户资料。");
        var drive = new DriveInfo(Path.GetPathRoot(target));
        if (drive.AvailableFreeSpace < bytes * 2 + 104857600) throw new IOException("磁盘空间不足，请选择其他位置或先释放空间。");
        string parent = target;
        while (!Directory.Exists(parent)) parent = Path.GetDirectoryName(parent);
        string probe = Path.Combine(parent,".spaceweekly-write-" + Guid.NewGuid().ToString("N"));
        try { using (File.Create(probe)) {} } catch (UnauthorizedAccessException) { throw new IOException("此位置不可写，请选择当前用户有权限的文件夹。"); }
        finally { if (File.Exists(probe)) File.Delete(probe); }
        return target + "\\";
    }
    internal MessageResult Message(InstallMessage type, Record record, MessageButtons buttons)
    {
        if (type == InstallMessage.InstallEnd && record.FieldCount >= 3) result = record.GetInteger(3);
        if (type == InstallMessage.ActionStart) {
            string action = record.GetString(1);
            activity.Text = action == "InstallFiles" ? "正在复制程序文件…" : action == "CreateShortcuts" ? "正在创建快捷方式…" : action == "RemoveFiles" ? "正在移除程序文件…" : action == "InstallFinalize" ? "正在完成安装…" : "正在处理安装配置…";
        }
        if (type == InstallMessage.Progress && record.FieldCount >= 3) {
            int kind = record.GetInteger(1), value = record.GetInteger(2);
            if (kind == 0) { total = value; direction = record.GetInteger(3) == 0 ? 1 : -1; ticks = direction == 1 ? 0 : total; }
            else if (kind == 2) ticks += direction * value;
            else if (kind == 3) total += value;
            progress.IsIndeterminate = total <= 0; if (total > 0) progress.Value = Math.Max(0,Math.Min(100,100*ticks/total));
        }
        if (type == InstallMessage.Error || type == InstallMessage.FatalExit) { error.Text = "安装遇到错误，请查看安装日志并重试。"; return buttons == MessageButtons.RetryCancel ? MessageResult.Cancel : MessageResult.OK; }
        if (type == InstallMessage.FilesInUse || type == InstallMessage.RMFilesInUse) {
            error.Text = "程序文件正在使用。请先保存并退出 SpaceWeekly，再重试安装。"; cancelling = true; return MessageResult.Cancel;
        }
        if (cancelling && !cancelSent) { cancelSent = true; return MessageResult.Cancel; }
        return MessageResult.OK;
    }
    internal void Complete() { if (result == 2 || result == 1602) cancelling = true; page = 5; Show(); }
}
