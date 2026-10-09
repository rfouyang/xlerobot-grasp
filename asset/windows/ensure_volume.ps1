# Windows' default playback device (the Bluetooth speaker): unmuted, and at least MinPercent loud (louder stays).
# Called by util/speech_helper.py before an alert, so a voice nobody turned down is heard.
param([int]$MinPercent = 65)
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

[Guid("5CDF2C82-841E-4546-9722-0CF74078229A"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IAudioEndpointVolume {
  int RegisterControlChangeNotify(IntPtr p); int UnregisterControlChangeNotify(IntPtr p);
  int GetChannelCount(out int c);
  int SetMasterVolumeLevel(float db, ref Guid ctx); int SetMasterVolumeLevelScalar(float level, ref Guid ctx);
  int GetMasterVolumeLevel(out float db); int GetMasterVolumeLevelScalar(out float level);
  int SetChannelVolumeLevel(uint ch, float db, ref Guid ctx); int SetChannelVolumeLevelScalar(uint ch, float level, ref Guid ctx);
  int GetChannelVolumeLevel(uint ch, out float db); int GetChannelVolumeLevelScalar(uint ch, out float level);
  int SetMute([MarshalAs(UnmanagedType.Bool)] bool mute, ref Guid ctx); int GetMute(out bool mute);
}
[Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDevice { int Activate(ref Guid iid, int ctx, IntPtr p, [MarshalAs(UnmanagedType.IUnknown)] out object o); }
[Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDeviceEnumerator { int EnumAudioEndpoints(int flow, int mask, out IntPtr devices); int GetDefaultAudioEndpoint(int flow, int role, out IMMDevice device); }
[ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")] class MMDeviceEnumerator { }

public static class Volume {
  static IAudioEndpointVolume Endpoint() {
    var enumerator = (IMMDeviceEnumerator)new MMDeviceEnumerator();
    IMMDevice device; enumerator.GetDefaultAudioEndpoint(0, 1, out device);
    var iid = typeof(IAudioEndpointVolume).GUID; object o;
    device.Activate(ref iid, 23, IntPtr.Zero, out o);
    return (IAudioEndpointVolume)o;
  }
  public static string AtLeast(float level) {
    var v = Endpoint(); var ctx = Guid.Empty; float before; bool mute;
    v.GetMasterVolumeLevelScalar(out before); v.GetMute(out mute);
    v.SetMute(false, ref ctx);
    if (before < level) v.SetMasterVolumeLevelScalar(level, ref ctx);
    float after; v.GetMasterVolumeLevelScalar(out after);
    return String.Format("volume {0:0} -> {1:0} (was muted: {2})", before * 100, after * 100, mute);
  }
}
"@
[Volume]::AtLeast($MinPercent / 100.0)
