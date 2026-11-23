// Component checks only. Real MSI installation and update are verified separately.
using System;
using System.IO;
using System.Reflection;
using System.Threading;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Imaging;

internal static class InstallerProbe
{
    static void Check(bool value, string reason) { if (!value) throw new Exception(reason); }
    static void Capture(Window window,string root,string label) {
        var content=(Grid)window.Content;content.Background=(Brush)window.Resources["Background"];
        window.Dispatcher.Invoke(System.Windows.Threading.DispatcherPriority.ApplicationIdle,new Action(delegate {}));
        window.UpdateLayout();
        foreach(double scale in new[]{1.0,1.25,1.5}) {
            var brand=(System.Windows.Controls.Image)window.FindName("Brand");
            Check(brand.Width==72 && brand.Height==50 && brand.ActualWidth>71 && brand.ActualWidth<=72 && brand.ActualHeight>49 && brand.ActualHeight<=50,
                "Brand dimensions differ: "+brand.ActualWidth+" x "+brand.ActualHeight);
            var image=new RenderTargetBitmap((int)Math.Ceiling(content.ActualWidth*scale),(int)Math.Ceiling(content.ActualHeight*scale),96*scale,96*scale,PixelFormats.Pbgra32);image.Render(content);
            var encoder=new PngBitmapEncoder();encoder.Frames.Add(BitmapFrame.Create(image));
            using(var output=File.Create(Path.Combine(root,label+"-"+(int)(scale*100)+".png"))) encoder.Save(output);
        }
    }
    [STAThread]
    static void Main(string[] args)
    {
        try { Run(args); }
        catch (Exception error) {
            for (int n=0;error!=null && n<8;n++,error=error.InnerException) Console.Error.WriteLine(error.GetType().Name+": "+error.Message);
            Environment.Exit(1);
        }
    }
    static void Run(string[] args)
    {
        string root=Path.GetFullPath(args[0]);Directory.CreateDirectory(root);
        var assembly=Assembly.LoadFrom(args[1]);var type=assembly.GetType("Wizard");
        var flags=BindingFlags.Instance|BindingFlags.NonPublic;
        var validate=type.GetMethod("ValidateTarget",BindingFlags.Static|BindingFlags.NonPublic);
        string target=Path.Combine(root,"中文 空格 安装");
        Check((string)validate.Invoke(null,new object[]{target,100L,false})==target+"\\","Chinese path changed");
        Check(!Directory.Exists(target),"Validation created an installation directory");
        foreach(string path in new[]{"",Path.GetPathRoot(root),Path.Combine(root,"CON")}) {
            bool failed=false;try{validate.Invoke(null,new object[]{path,100L,false});}catch(TargetInvocationException){failed=true;}
            Check(failed,"Invalid path accepted: "+path);
        }
        bool insufficient=false;try{validate.Invoke(null,new object[]{target,Int64.MaxValue/4,false});}catch(TargetInvocationException){insufficient=true;}
        Check(insufficient,"Space check failed");
        foreach(bool dark in new[]{false,true}) {
            object wizard=Activator.CreateInstance(type,flags,null,new object[]{new ManualResetEvent(false),target,false,false,"component-check",351L*1048576,""},null);
            var window=(Window)type.GetField("Window",flags).GetValue(wizard);
            window.ShowActivated=false;window.ShowInTaskbar=false;window.WindowStartupLocation=WindowStartupLocation.Manual;
            window.Left=-10000;window.Top=-10000;window.Show();window.UpdateLayout();
            type.GetMethod("ApplyTheme",flags,null,new[]{typeof(bool)},null).Invoke(wizard,new object[]{dark});
            Capture(window,root,(dark?"dark":"light")+"-welcome");
            var advance=type.GetMethod("Advance",flags);advance.Invoke(wizard,null);
            var next=(Button)window.FindName("Next");Check(!next.IsEnabled,"Agreement preselected");
            var consent=(CheckBox)type.GetField("consent",flags).GetValue(wizard);consent.IsChecked=true;
            window.Width=640;window.Height=480;window.UpdateLayout();
            var scroll=(ScrollViewer)window.FindName("ContentScroll");
            Check(consent.TranslatePoint(new Point(0,consent.ActualHeight),scroll).Y<=scroll.ActualHeight+1,"Small window clips agreement consent");
            Capture(window,root,(dark?"dark":"light")+"-small-agreement");
            window.Width=900;window.Height=660;window.UpdateLayout();
            Check(next.IsEnabled,"Agreement did not enable Next");advance.Invoke(wizard,null);
            Capture(window,root,(dark?"dark":"light")+"-directory");
            ((Button)window.FindName("Cancel")).RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
            Check(((System.Windows.Controls.Border)window.FindName("CancelPanel")).Visibility==Visibility.Visible,"Cancel confirmation missing");
            ((Button)window.FindName("Keep")).RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
            Check(((System.Windows.Controls.Border)window.FindName("CancelPanel")).Visibility==Visibility.Collapsed,"Continue did not dismiss confirmation");
            ((Button)window.FindName("Cancel")).RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
            ((Button)window.FindName("ConfirmCancel")).RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
            Check(!window.IsVisible,"Pre-install cancellation did not close the wizard");
        }
        Console.WriteLine("Installer component checks passed; real engine checks remain separate.");
    }
}
