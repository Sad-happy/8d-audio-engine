# Switch the default audio output device on Windows (no admin needed).
# Backed by the undocumented IPolicyConfig COM interface.
#
# usage:
#   switch_audio.ps1 -To list              # list active render endpoints
#   switch_audio.ps1 -To cable             # default output -> virtual cable
#                                          # (remembers the previous device)
#   switch_audio.ps1 -To restore           # restore the remembered device
#   switch_audio.ps1 -To name -Name "Spea" # switch by name substring
#
# The engine reads the loopback of VB-Cable, so before starting it run
#   switch_audio.ps1 -To cable
# and after stopping it run
#   switch_audio.ps1 -To restore
param(
  [string]$To = "list",
  [string]$Name = "",
  [string]$CableName = "CABLE Input",
  [string]$StateFile = ""
)
$ErrorActionPreference = "Stop"

Add-Type @"
using System;
using System.Runtime.InteropServices;
public class ADev {
    [ComImport, Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IMMDeviceEnumerator {
        [PreserveSig] int EnumAudioEndpoints(int dataFlow, int state, out IntPtr devices);
        [PreserveSig] int GetDefaultAudioEndpoint(int dataFlow, int role, out IntPtr device);
    }
    [ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")] public class MMDeviceEnumeratorCom {}

    [ComImport, Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IMMDevice {
        [PreserveSig] int Activate(ref Guid iid, int clsCtx, IntPtr activationParams, [MarshalAs(UnmanagedType.IUnknown)] out object iface);
        [PreserveSig] int OpenPropertyStore(int access, out IntPtr store);
        [PreserveSig] int GetId([MarshalAs(UnmanagedType.LPWStr)] out string id);
    }
    [ComImport, Guid("0BD7A1BE-7A1A-44DB-8397-CC5392387B5E"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IMMDeviceCollection {
        [PreserveSig] int GetCount(out int count);
        [PreserveSig] int Item(int index, out IntPtr device);
    }
    [ComImport, Guid("886d8eeb-8cf2-4446-8d02-cdba1dbdcf99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IPropertyStore {
        [PreserveSig] int GetCount(out int count);
        [PreserveSig] int GetAt(int i, out PropertyKey key);
        [PreserveSig] int GetValue(ref PropertyKey key, out PropVariant value);
    }
    [StructLayout(LayoutKind.Sequential)] public struct PropertyKey { public Guid fmtid; public int pid; }
    [StructLayout(LayoutKind.Explicit)] public struct PropVariant {
        [FieldOffset(0)] public short vt;
        [FieldOffset(8)] public IntPtr pointerValue;
    }

    public static string[] List() {
        var en = (IMMDeviceEnumerator)(new MMDeviceEnumeratorCom());
        IntPtr col; en.EnumAudioEndpoints(0, 1, out col);        // eRender, DEVICE_STATE_ACTIVE
        var dc = (IMMDeviceCollection)Marshal.GetObjectForIUnknown(col);
        int n; dc.GetCount(out n);
        var pkey = new PropertyKey { fmtid = new Guid("a45c254e-df1c-4efd-8020-67d146a850e0"), pid = 14 };
        var res = new string[n];
        for (int i = 0; i < n; i++) {
            IntPtr dv; dc.Item(i, out dv);
            var dev = (IMMDevice)Marshal.GetObjectForIUnknown(dv);
            string id; dev.GetId(out id);
            IntPtr st; dev.OpenPropertyStore(0, out st);          // STGM_READ
            var store = (IPropertyStore)Marshal.GetObjectForIUnknown(st);
            PropVariant pv; store.GetValue(ref pkey, out pv);
            string name = (pv.vt == 31) ? Marshal.PtrToStringUni(pv.pointerValue) : "?";
            res[i] = id + "|" + name;
        }
        return res;
    }

    public static string DefaultId() {
        var en = (IMMDeviceEnumerator)(new MMDeviceEnumeratorCom());
        IntPtr dv; en.GetDefaultAudioEndpoint(0, 1, out dv);     // eRender, eMultimedia
        var dev = (IMMDevice)Marshal.GetObjectForIUnknown(dv);
        string id; dev.GetId(out id);
        return id;
    }

    [ComImport, Guid("F8679F50-850A-41CF-9C72-430F290290C8"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IPolicyConfig {
        [PreserveSig] int GetMixFormat(IntPtr a, IntPtr b);
        [PreserveSig] int GetDeviceFormat(IntPtr a, int b, IntPtr c);
        [PreserveSig] int ResetDeviceFormat(IntPtr a);
        [PreserveSig] int SetDeviceFormat(IntPtr a, IntPtr b, IntPtr c);
        [PreserveSig] int GetProcessingPeriod(IntPtr a, int b, IntPtr c, IntPtr d);
        [PreserveSig] int SetProcessingPeriod(IntPtr a, int b, IntPtr c);
        [PreserveSig] int GetShareMode(IntPtr a, IntPtr b);
        [PreserveSig] int SetShareMode(IntPtr a, IntPtr b);
        [PreserveSig] int GetPropertyValue(IntPtr a, int b, IntPtr c, IntPtr d);
        [PreserveSig] int SetPropertyValue(IntPtr a, int b, IntPtr c, IntPtr d);
        [PreserveSig] int SetDefaultEndpoint([MarshalAs(UnmanagedType.LPWStr)] string a, int b);
        [PreserveSig] int SetEndpointVisibility(IntPtr a, int b);
    }
    [ComImport, Guid("870af99c-171d-4f9e-af0d-e63df40c2bc9")] public class PolicyConfigClient {}

    public static int SetDefault(string id, int role) {
        var pc = (IPolicyConfig)(new PolicyConfigClient());
        return pc.SetDefaultEndpoint(id, role);
    }
}
"@

$prev = if ($StateFile) { $StateFile } else { Join-Path $PSScriptRoot "prev-output.txt" }
$all = [ADev]::List()

function Find-ByName([string]$sub) {
    foreach ($line in $all) {
        $id, $name = $line -split "\|", 2
        if ($name -like "*$sub*") { return @($id, $name) }
    }
    return $null
}

switch ($To) {
  "list" { $all | ForEach-Object { $_ }; exit 0 }
  "cable" {
    $pair = Find-ByName $CableName
    if (-not $pair) { throw "device '$CableName' not found (run -To list)" }
    [ADev]::DefaultId() | Set-Content -Path $prev -Encoding ASCII
    $rc = [ADev]::SetDefault($pair[0], 1)     # 1 = eMultimedia
    Write-Host "default output -> $($pair[1]) (hr=0x$('{0:X}' -f $rc)); previous saved to $prev"
  }
  "restore" {
    if (-not (Test-Path $prev)) { throw "no saved device in $prev (run -To cable first)" }
    $id = (Get-Content $prev -Raw).Trim()
    $rc = [ADev]::SetDefault($id, 1)
    Write-Host "default output restored (hr=0x$('{0:X}' -f $rc))"
  }
  "name" {
    if (-not $Name) { throw "-To name requires -Name <substring>" }
    $pair = Find-ByName $Name
    if (-not $pair) { throw "device '$Name' not found (run -To list)" }
    $rc = [ADev]::SetDefault($pair[0], 1)
    Write-Host "default output -> $($pair[1]) (hr=0x$('{0:X}' -f $rc))"
  }
  default { throw "unknown -To '$To' (list|cable|restore|name)" }
}
