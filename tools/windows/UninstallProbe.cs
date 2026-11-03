using System;
using System.IO;
internal static class UninstallProbe
{
    static int Main(string[] args)
    {
        Console.OutputEncoding = new System.Text.UTF8Encoding(false);
        string guid = "{01234567-0123-0123-0123-0123456789AB}";
        if (Uninstall.ProductCode("msiexec.exe /x " + guid) != guid) return 1;
        foreach (string value in new[] { "cmd.exe /c anything", "msiexec.exe /x ../other", "msiexec.exe /x " + guid + " & echo bad" })
        {
            try { Uninstall.ProductCode(value); return 2; } catch (InvalidDataException) { }
        }
        if (!Uninstall.SamePath(Path.Combine(args[0], "."), args[0])) return 3;
        var backend = Uninstall.Backend(args[0], true);
        Console.WriteLine(backend.FileName); Console.WriteLine(backend.Arguments); return 0;
    }
}
