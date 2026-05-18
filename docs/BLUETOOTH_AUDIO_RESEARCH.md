# Bluetooth Audio Streaming on Linux: Technical Research Report

**Date:** 2026-05-14  
**Context:** Dual Audio Player — Python/GTK/GStreamer desktop application on Ubuntu 24.04 with PipeWire 1.0.5  
**System:** PulseAudio-on-PipeWire bridge (`pipewire-pulse`), BlueZ 5.72, WirePlumber 0.4.17

---

## 1. BlueZ — Linux Bluetooth Stack

BlueZ is the canonical Linux Bluetooth stack. It runs as `bluetoothd` and exposes everything via D-Bus.

### Key D-Bus Interfaces

| Interface | Path | Purpose |
|---|---|---|
| `org.bluez.Adapter1` | `/org/bluez/hci0` | Controller power, discovery, pairable |
| `org.bluez.Device1` | `/org/bluez/hci0/dev_XX_XX_XX_XX_XX_XX` | Device properties, pairing, trusted, connected, UUIDs |
| `org.bluez.Media1` | `/org/bluez/hci0` | Register A2DP endpoints, register media players |
| `org.bluez.MediaTransport1` | per-transport path | A2DP transport state, volume, delay |
| `org.bluez.ProfileManager1` | `/org/bluez` | Register custom Bluetooth profiles |
| `org.bluez.GattManager1` | `/org/bluez/hci0` | BLE GATT services |
| `org.bluez.AgentManager1` | `/org/bluez` | Pairing agent registration (PIN, passkey, display) |
| `org.bluez.BatteryProviderManager1` | `/org/bluez/hci0` | Battery level reporting |

### Discovery/Connection Flow (from Python)

```python
import dbus

bus = dbus.SystemBus()
manager = dbus.Interface(bus.get_object("org.bluez", "/"), "org.freedesktop.DBus.ObjectManager")
objects = manager.GetManagedObjects()

for path, ifaces in objects.items():
    if "org.bluez.Device1" in ifaces:
        props = ifaces["org.bluez.Device1"]
        print(props.get("Address"), props.get("Name"), props.get("UUIDs", []))

# Pair: set Trusted=True, call Pair(), monitor Connected property
# Connect A2DP: register endpoint via Media1.RegisterEndpoint()
```

### Pros
- Complete control of all Bluetooth profiles (A2DP, HFP, HSP, AVRCP, GATT)
- Stable D-Bus API — same across all Linux distros
- Can register custom endpoints for A2DP source/sink roles
- Direct access to codec negotiation data via `MediaTransport1.Configuration`

### Cons
- Raw BlueZ D-Bus programming is verbose — hundreds of lines for a functioning A2DP setup
- You **cannot** use BlueZ D-Bus for audio while PipeWire also manages Bluetooth — they conflict
- `Media1.RegisterPlayer` was deprecated in BlueZ 5 (controller-only now)
- Must implement a pairing agent (`org.bluez.Agent1`) for PIN/passkey workflows

---

## 2. GStreamer BlueZ Plugin (`gst-plugins-bad`)

The `bluez` plugin in `gst-plugins-bad` provides three elements:

| Element | Role | Notes |
|---|---|---|
| `a2dpsink` | Bluetooth A2DP Audio Sink | Receives PCM, encodes, streams to BT speaker/headphones |
| `avdtpsink` | AVDTP Sink (transport-level) | Lower-level transport sink |
| `avdtpsrc` | AVDTP Source (transport-level) | Lower-level transport source |

### How It Works

```text
... ! audioconvert ! audioresample ! sbcenc ! a2dpsink
```

The `a2dpsink` uses BlueZ D-Bus internally (or direct kernel socket) to:
1. Discover and connect to a remote A2DP sink (headphones/speaker)
2. Negotiate AVDTP transport with BlueZ
3. Encode audio (you must supply an encoder: `sbcenc`, etc.)
4. Stream over L2CAP/AVDTP

### Practical Limitation

The BlueZ GStreamer plugin is **not widely used** in production desktop applications. It competes with PipeWire/PulseAudio for control of the BlueZ Media1 interface. Only one process can register as the A2DP endpoint at a time. On a system running PipeWire or PulseAudio with Bluetooth modules, the GStreamer `bluez` plugin **will fail** to acquire the transport because those audio servers already own the D-Bus interfaces.

**Verdict:** Not recommended for this project unless running a custom embedded Linux image without PulseAudio/PipeWire.

---

## 3. PulseAudio Bluetooth Module (`module-bluetooth-discover`)

### Architecture

```text
module-bluetooth-discover
  ├── module-bluez5-discover (BlueZ 5 device detection)
  │   ├── module-bluez5-device (per-device)
  │   └── Creates: bluez_sink.<addr>.a2dp_sink / bluez_source.<addr>.a2dp_source
  └── Register BlueZ MediaEndpoint (captures A2DP transports)
```

### How It Integrates

- Loads via `/etc/pulse/default.pa` or `pactl load-module`
- On this system: **not loaded** (system uses PipeWire-Pulse)
- When present, Bluetooth devices appear as standard PulseAudio sinks/sources
- Codec negotiation: PulseAudio's module-bluez5-device negotiates SBC by default; AAC/aptX/LDAC require patched modules or vendor forks

### Pros
- Bluetooth sinks appear in `pactl list sinks` — seamless for GStreamer `pulsesink`
- No special GStreamer handling needed
- Device name convention: `bluez_sink.XX_XX_XX_XX_XX_XX.a2dp_sink`

### Cons
- PulseAudio is legacy; Ubuntu 24.04 defaults to PipeWire
- SBC-only codec support without third-party patches
- No LDAC, aptX, AAC negotiation in mainline PA
- Module conflicts with PipeWire's `libspa-bluez5`

---

## 4. PipeWire Bluetooth — The Modern Stack

### Components

| Component | Package | Role |
|---|---|---|
| `libspa-bluez5` | `libspa-0.2-bluetooth` | SPA plugin — BlueZ D-Bus integration, A2DP/HFP transport |
| WirePlumber Bluetooth policy | `wireplumber` | Policy engine — auto-connect, profile switching, codec selection |
| `pipewire-pulse` | `pipewire-pulse` | PulseAudio compatibility layer |

### Architecture

```text
WirePlumber (session manager)
  ├── bluetooth.lua.d/30-bluez-monitor.lua  (device detection)
  ├── bluetooth.lua.d/50-bluez-config.lua   (codec priorities)
  ├── scripts/policy-bluetooth.lua           (auto-switch profiles)
  └── Creates PipeWire nodes:
      bluez_output.<addr>.1 (A2DP sink to play audio TO headphones)
      bluez_input.<addr>.1  (A2DP source FROM headset mic)
```

### Codec Support (PipeWire 1.0+)

| Codec | PipeWire Native | Notes |
|---|---|---|
| SBC | Built-in | Always available, baseline A2DP codec |
| AAC | Built-in (via fdk-aac or ffmpeg) | Requires `libfdk-aac2` package |
| aptX | Built-in (via `libopenaptx`) | `libopenaptx0` on this system |
| aptX HD | Built-in (via `libopenaptx`) | Same as above |
| LDAC | Built-in (encoder only) | Requires `libldac` |
| LC3 | Via `lc3plus` or separate plugin | For LE Audio |
| mSBC | Built-in | Wideband speech for HFP |
| CVSD | Built-in | Narrowband speech for HFP |

**Configured on this system:** `libopenaptx0` (aptX/aptX-HD) and `libspa-0.2-bluetooth` present.

### Auto-Connect and Profile Switching

WirePlumber's `policy-bluetooth.lua` handles:
- Auto-connect to trusted devices when they appear
- Switch between A2DP (high-quality music) and HFP/HSP (headset mode) based on stream type
- Fallback logic when multiple devices are available

### Codec Negotiation

`50-bluez-config.lua` controls codec priority. Example custom configuration:

```lua
bluez_monitor.properties = {
  ["bluez5.codecs"] = "[ ldac aac aptx_hd aptx sbc ]",
  ["bluez5.a2dp.ldac.quality"] = "hq",  -- hq, sq, mq
  ["bluez5.default.rate"] = 48000,
  ["bluez5.default.channels"] = 2,
}
```

### How to Integrate With This Project

**Current state:** The project uses `pulsesink` as the GstElement. On PipeWire-Pulse, Bluetooth sinks appear as regular PulseAudio sinks:

```bash
$ pactl list short sinks | grep bluez
bluez_output.XX_XX_XX_XX_XX_XX.1	PipeWire	...
```

This means **no code changes are required** to support Bluetooth sinks. The sink discovery code in `player/devices.py:45` (`get_audio_sinks()`) already discovers Bluetooth sinks via:
1. `GstDeviceMonitor` with `Audio/Sink` filter (finds all PipeWire sinks)
2. Fallback `pactl list short sinks` (also lists bluetooth sinks)

The `player/engine.py:134` pipeline already uses `pulsesink` with `device=<sink_name>`, which works transparently for Bluetooth sinks.

### Pros
- **Zero application changes required** — Bluetooth sinks appear as standard sinks
- PipeWire IS the modern/future stack — default on Ubuntu 24.04+, Fedora 34+, Arch
- Best-in-class codec support (LDAC, aptX, aptX HD, AAC, SBC)
- Automatic profile switching handled by WirePlumber
- Hardware offloading support for codecs on some adapters
- Battery level reporting via AT+XAPL
- FastStream and aptX Low Latency modes for gaming
- LE Audio / LC3 support (emerging, better in PipeWire 1.2+)

### Cons
- PipeWire/WirePlumber configuration can be complex
- Some USB Bluetooth adapters have firmware bugs (Broadcom, Realtek)
- LE Audio (Bluetooth 5.2+) still maturing in 2025-2026
- Debugging PipeWire Bluetooth issues requires `PIPEWIRE_DEBUG=4 wireplumber` logs

---

## 5. oFono (HSP/HFP Telephony Profiles)

oFono provides telephony stack integration — phone calls, modem management, SIM access. It includes a BlueZ plugin that registers the **HSP** (Headset Profile) and **HFP** (Hands-Free Profile) Bluetooth profiles.

### Relevance to This Project

**Low.** oFono is targeted at:
- Mobile Linux (Sailfish OS, postmarketOS, Tizen, etc.)
- Automotive IVI systems
- Cellular modem management

On desktop Linux, HSP/HFP for headsets is handled by:
- PipeWire with `libspa-bluez5` (includes HFP/HSP support with mSBC/CVSD codecs)
- PulseAudio with `module-bluetooth-discover` + `module-bluez5-device`

oFono does not add value for an A2DP audio playback use case and adds significant complexity (modem integration, telephony stack). Not recommended for this project.

---

## 6. A2DP Sink/Source Roles

### Roles Explained

| Role | Description | Use Case |
|---|---|---|
| **A2DP Sink** (SNK) | Receives audio, drives DAC | Bluetooth headphones, speakers |
| **A2DP Source** (SRC) | Sends encoded audio | Phone/laptop playing TO headphones |
| **A2DP Source** (as local device) | Receives audio from remote | Laptop acting as Bluetooth speaker |

### On Linux

Your laptop is an **A2DP Source** when playing to BT headphones:
```text
GStreamer → pulsesink → PipeWire → libspa-bluez5 (encodes SBC/AAC/aptX/LDAC) → BlueZ → L2CAP → headphones
```

Your laptop could act as an **A2DP Sink** if you want phone audio to play through laptop speakers:
```text
Phone → BlueZ → libspa-bluez5 (decodes) → PipeWire → ALSA → speakers
```

WirePlumber handles both roles automatically via `policy-bluetooth.lua`.

---

## 7. Common Pitfalls

### Connection Stability

| Issue | Cause | Mitigation |
|---|---|---|
| BT adapter crashes | USB suspend/autosuspend | `echo 'options btusb enable_autosuspend=n' > /etc/modprobe.d/btusb.conf` |
| A2DP drops to HFP | PipeWire polling failure | Update BlueZ to 5.70+, kernel to 6.5+ |
| Device not auto-connecting | WirePlumber policy | Check `50-bluez-config.lua`, trust the device |
| Paired but no audio | Codec mismatch | Verify `bluez5.codecs` in WirePlumber config |
| PulseAudio/PipeWire fight for BlueZ | Both have BT modules loaded | On PipeWire systems, ensure PA's `module-bluetooth-discover` is NOT loaded |

### Audio Latency Over Bluetooth

| Codec | Typical Latency | Notes |
|---|---|---|
| SBC (standard) | 150-250 ms | Default codec, high latency |
| SBC (low-latency) | 50-80 ms | PipeWire can tune bitpool |
| aptX | 80-120 ms | Better than SBC, not low-latency |
| aptX Low Latency | 32-40 ms | Requires hardware support (Qualcomm CSR) |
| aptX Adaptive | 50-80 ms | Dynamically adjusts quality/latency |
| LDAC (HQ) | 150-200 ms | Best quality, worst latency |
| LDAC (SQ) | 100-150 ms | Standard quality |
| LDAC (MQ) | 80-100 ms | Mobile quality |
| AAC | 120-200 ms | Apple-focused, high latency |
| LC3 (LE Audio) | 20-30 ms | Next-gen, requires BT 5.2+ |
| FastStream | 30-40 ms | Proprietary, rare |

**Key insight for dual audio:** If playing video with synchronized dual output, Bluetooth latency (100-250ms) will always cause lipsync issues. The project's `delayMs` system in `player/config.py` can compensate by delaying the wired listener branch, but:
- Delaying video is not supported currently
- Minimal achievable delay is 0 ms (no negative delay)
- A Bluetooth sink with 200ms latency requires 200ms video delay or 200ms advance on the wired audio — neither is currently supported

**Recommendation:** Add negative delay support (via `GstQueue` with min-threshold or `audiofirfilter` for time-stretching) or add a video delay pipeline option.

### Codec Negotiation Issues

- **SBC bitpool:** Low bitpool = low latency but bad quality. High bitpool = good quality but high latency and potential L2CAP MTU issues.
- **LDAC bitrate:** 990kbps (HQ) causes dropouts on crowded RF environments. Adaptive mode helps.
- **AAC on Linux:** Software encoding via `fdk-aac` can introduce additional latency vs hardware AAC on macOS/iOS.
- **aptX licensing:** `libopenaptx` is a reverse-engineered implementation; may not match Qualcomm's performance in edge cases.

---

## 8. Comparison Table

| Axis | BlueZ D-Bus (Raw) | PulseAudio `module-bluetooth-discover` | PipeWire `libspa-bluez5` (+WirePlumber) |
|---|---|---|---|
| **Integration effort** | Very high — manage Device1, Media1, MediaTransport1, Agent1, plus L2CAP/A2DP/AVDTP | Low — load module, sinks appear as PA sinks | Zero — PipeWire-Pulse compatibility layer |
| **Codec support** | Manual — you handle encoding | SBC only (mainline) | SBC, AAC, aptX, aptX HD, LDAC, LC3, FastStream |
| **Auto-connect** | You implement it | Yes, via module-bluetooth-discover | Yes, via WirePlumber policy |
| **Profile switching** | Manual | A2DP ↔ HFP automatic | A2DP ↔ HFP automatic |
| **HFP/HSP** | Complex via oFono or custom AG | Yes, via module-bluez5-device | Yes, native mSBC/CVSD |
| **LE Audio support** | Possible, high effort | No | Emerging (1.2+) |
| **Latency control** | Full control | Minimal | Configurable via WirePlumber |
| **GStreamer integration** | `a2dpsink`/`avdtpsink` (conflicts with PA/PW) | `pulsesink` — seamless | `pulsesink` — seamless (via pipewire-pulse) |
| **Python bindings** | `dbus-python` | `pulsectl` / `pactl` | `pulsectl` / `wireplumber-client` / D-Bus |
| **Desktop distro default** | N/A (raw) | Ubuntu 22.04 and earlier | Ubuntu 24.04+, Fedora 34+, Arch |
| **Future-proof?** | No — raw D-Bus is fragile | No — PulseAudio is legacy | Yes — PipeWire is the present and future |
| **Status on this system** | Available but unused for audio | NOT loaded | Running and managing all audio |

---

## 9. Recommendation for Dual Audio Player

### Immediate (No Changes Needed)

The project already supports Bluetooth sinks. On PipeWire-Pulse (current system), Bluetooth headphones appear as `bluez_output.<addr>.1` sinks in `get_audio_sinks()` and can be used immediately via `pulsesink` with `device=<sink_name>`.

### Path Forward

1. **Stay on PipeWire-Pulse path.** Continue using `pulsesink`. This is the most future-proof approach. Do NOT attempt to use GStreamer's `bluez` plugin or raw BlueZ D-Bus — they conflict with the audio server.

2. **Address latency for dual-audio sync.** The biggest Bluetooth-specific gap is the lack of video delay or negative audio delay. Consider:
   - Adding a `video_delay_ms` config option with a `GstQueue` insert before the video sink
   - Or using `audiorate` + queue tricks to handle the case where the wired output needs to be delayed relative to Bluetooth

3. **Optionally enhance sink discovery** to tag sinks as Bluetooth:

   ```python
   def _is_bluetooth_sink(sink_name: str) -> bool:
       return sink_name.startswith("bluez_") or "bluez" in sink_name.lower()
   ```

   This would allow the UI to show a Bluetooth icon, warn about latency, or auto-suggest delay values.

4. **Power-user feature — codec visibility:** Parse `pactl list sinks` or PipeWire metadata to show which Bluetooth codec is active (visible in `pw-dump` output as `api.bluez5.codec`). Could display in a tooltip.

5. **Do NOT use oFono or raw BlueZ D-Bus.** These are architecturally wrong for this application's scope. The PipeWire-Pulse abstraction handles everything correctly.

### Summary Table

| Approach | Recommended? | Effort | Reason |
|---|---|---|---|
| Continue with `pulsesink` + PipeWire-Pulse | **Yes** | Zero | Already works; Bluetooth sinks are transparent |
| Add video delay for lip-sync | **Yes** | Medium | Enables proper A/V sync with BT latency |
| Tag sinks as Bluetooth in UI | Optional | Low | UX polish |
| Switch to raw BlueZ D-Bus | **No** | Very high | Conflicts with audio server; unnecessary |
| Switch to GStreamer `bluez` plugin | **No** | High | Conflicts with PipeWire/Pulse; not needed |
| Integrate oFono | **No** | Very high | Telephony stack; irrelevant to media playback |
