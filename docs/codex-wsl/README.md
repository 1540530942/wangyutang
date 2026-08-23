# Codex WSL quick setup

This note records the working setup used on this machine so it can be restored
quickly after a reboot, a WSL reinstall, or a new Windows environment.

## What this fixes

- `Codex could not find bubblewrap on PATH`
- WSL cannot reach `chatgpt.com` or `api.openai.com` directly
- Codex falls back from WebSocket to HTTPS and waits for network
- WSL needs to use the Windows Clash Verge proxy without sharing Codex state

Windows Codex and WSL Codex may use the same ChatGPT account, but their local
state is separate:

- Windows: `C:\Users\Administrator\.codex`
- WSL user `archer`: `/home/archer/.codex`

Do not copy `auth.json` between them.

## Assumptions

- WSL distro: Ubuntu
- WSL user: `archer`
- Windows proxy app: Clash Verge / `verge-mihomo.exe`
- Windows local proxy listens on `127.0.0.1:7897`
- WSL-facing forwarded proxy port: `7898`

## Fast restore

Run PowerShell as Administrator on Windows:

```powershell
powershell -ExecutionPolicy Bypass -File .\docs\codex-wsl\setup-wsl-codex.ps1
```

Then open a new WSL shell and run:

```bash
source ~/.bashrc
codex doctor
codex login
codex exec -s read-only --skip-git-repo-check 'Please only answer: Codex OK'
```

If `codex login` reports `unsupported_country_region_territory`, check the
actual proxy exit region and switch Clash Verge to an OpenAI-supported exit.
The local WSL setup is working when `codex doctor` shows HTTP reachability as
reachable.

## Windows device awareness

WSL Codex may fail to call Windows programs from its sandbox with
`UtilBindVsockAnyPort` errors. For Windows peripheral awareness, use the bridge
under `docs/codex-wsl/windows-device-bridge/`:

```powershell
powershell -ExecutionPolicy Bypass -File .\docs\codex-wsl\windows-device-bridge\export-windows-devices.ps1
```

Then WSL/Codex can read the generated snapshot:

```bash
bash docs/codex-wsl/windows-device-bridge/read-windows-devices.sh
```

This is an inventory bridge only. Use `usbipd-win` or another explicit device
sharing mechanism when Linux needs direct access to USB, serial, or camera
devices.

## Manual commands

Install `bubblewrap` inside WSL:

```powershell
wsl -u root -e sh -lc "apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y bubblewrap"
```

Expose the Windows-only proxy to WSL:

```powershell
netsh interface portproxy add v4tov4 listenaddress=0.0.0.0 listenport=7898 connectaddress=127.0.0.1 connectport=7897
New-NetFirewallRule -DisplayName "Codex WSL proxy 7898" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 7898 -Profile Any
```

Append WSL proxy exports to `~/.bashrc`:

```bash
if command -v ip >/dev/null 2>&1; then
    _wsl_windows_host="$(ip route | awk '/default/ {print $3; exit}')"
    if [ -n "$_wsl_windows_host" ]; then
        export HTTP_PROXY="http://${_wsl_windows_host}:7898"
        export HTTPS_PROXY="$HTTP_PROXY"
        export ALL_PROXY="$HTTP_PROXY"
        export NO_PROXY="localhost,127.0.0.1,::1"
    fi
    unset _wsl_windows_host
fi
```

Verify from WSL:

```bash
command -v bwrap
bwrap --version
env | grep -i proxy
curl -sS --max-time 20 https://chatgpt.com/cdn-cgi/trace | sed -n '1,30p'
codex doctor
```

## Known outcomes from this machine

- `bubblewrap 0.9.0` installed at `/usr/bin/bwrap`
- `codex doctor` reached `https://chatgpt.com/backend-api/` over HTTP with the
  proxy environment variables present
- WebSocket may still warn behind the proxy, but HTTPS fallback is usable
- A stale WSL token can produce `refresh_token_reused`; fix with `codex logout`
  then `codex login` inside WSL
- `unsupported_country_region_territory` means the OpenAI service rejected the
  current exit region; change the proxy route/exit and log in again
