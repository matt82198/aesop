// run-hidden.cs -- hidden-console launcher for Aesop Windows Scheduled Tasks.
//
// Usage: run-hidden.exe <program> [args...]
//
// Starts <program> with CREATE_NO_WINDOW, waits for it to finish, and exits with its exit
// code. CREATE_NO_WINDOW means the child console app gets no visible window, which a plain
// wscript WScript.Shell.Run(cmd, 0, True) does not guarantee (on Windows 11 the default
// terminal can still show a window for a console created with SW_HIDE). The waiting behaviour
// keeps Task Scheduler's MultipleInstances and ExecutionTimeLimit semantics intact.
//
// Built as a GUI-subsystem executable (/target:winexe) so this launcher never owns a console.
// daemons/install-tasks.ps1 compiles it into daemons/bin/run-hidden.exe with csc.exe.
using System;
using System.Diagnostics;
using System.Text;

internal static class RunHidden
{
    private static int Main(string[] args)
    {
        if (args.Length == 0)
        {
            return 2;
        }

        try
        {
            var argLine = new StringBuilder();
            for (int i = 1; i < args.Length; i++)
            {
                if (i > 1)
                {
                    argLine.Append(' ');
                }
                argLine.Append(QuoteArg(args[i]));
            }

            var psi = new ProcessStartInfo
            {
                FileName = args[0],
                Arguments = argLine.ToString(),
                UseShellExecute = false,
                CreateNoWindow = true,
            };

            using (var proc = Process.Start(psi))
            {
                proc.WaitForExit();
                return proc.ExitCode;
            }
        }
        catch (Exception)
        {
            // Fail closed: nothing ran, and the task reports a non-zero result.
            return 1;
        }
    }

    // MSVCRT command-line quoting, so bash receives each argument exactly as given.
    private static string QuoteArg(string arg)
    {
        if (arg.Length > 0 && arg.IndexOfAny(new[] { ' ', '\t', '"' }) < 0)
        {
            return arg;
        }

        var sb = new StringBuilder("\"");
        int backslashes = 0;
        foreach (char c in arg)
        {
            if (c == '\\')
            {
                backslashes++;
                continue;
            }

            if (c == '"')
            {
                sb.Append('\\', backslashes * 2 + 1);
                sb.Append('"');
            }
            else
            {
                sb.Append('\\', backslashes);
                sb.Append(c);
            }
            backslashes = 0;
        }

        sb.Append('\\', backslashes * 2);
        sb.Append('"');
        return sb.ToString();
    }
}
