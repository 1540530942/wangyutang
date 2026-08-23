# Windows device bridge for WSL Codex

Codex running inside WSL may fail to call `powershell.exe` from its sandbox with
errors like:

```text
WSL ERROR: UtilBindVsockAnyPort:308: socket failed 1
```

Use this bridge when Codex needs to know what Windows peripherals exist, without
depending on WSL interop from inside the sandbox.

## Files

- `export-windows-devices.ps1`: run on Windows to export device snapshots.
- `read-windows-devices.sh`: run from WSL/Codex to summarize exported JSON.
- `windows-devices.example.json`: shape of the generated JSON file.
- `windows-devices.json`: generated local snapshot, ignored by git.

## Refresh from Windows

Run from the repository root in Windows PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\docs\codex-wsl\windows-device-bridge\export-windows-devices.ps1
```

The script writes:

```text
docs\codex-wsl\windows-device-bridge\windows-devices.json
```

## Read from WSL

Run from the repository root inside WSL:

```bash
bash docs/codex-wsl/windows-device-bridge/read-windows-devices.sh
```

Filter by class or friendly name:

```bash
bash docs/codex-wsl/windows-device-bridge/read-windows-devices.sh --class Ports
bash docs/codex-wsl/windows-device-bridge/read-windows-devices.sh --grep camera
```

## Actual device access

This bridge only gives Codex an inventory. To use a device from Linux, expose the
device to WSL first:

- USB serial / ESP32 / Arduino: use `usbipd-win`, then check `/dev/ttyUSB*` or
  `/dev/ttyACM*`.
- Disks/files: use `/mnt/c`, `/mnt/d`, or explicit Windows shares.
- GUI/display: use WSLg or Windows-side tools.
- Bluetooth and many built-in laptop devices usually remain Windows-managed.

## usbipd quick path

On Windows PowerShell as Administrator:

```powershell
winget install dorssel.usbipd-win
usbipd list
usbipd bind --busid <BUSID>
usbipd attach --wsl --busid <BUSID>
```

Inside WSL:

```bash
lsusb
ls /dev/ttyUSB* /dev/ttyACM* 2>/dev/null
```
