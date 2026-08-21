param(
    [string]$Distro = "Ubuntu",
    [string]$WslUser = "archer",
    [int]$WindowsProxyPort = 7897,
    [int]$WslProxyPort = 7898
)

$ErrorActionPreference = "Stop"

function Run($FilePath, [string[]]$Arguments) {
    Write-Host ">> $FilePath $($Arguments -join ' ')"
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code $LASTEXITCODE"
    }
}

Write-Host "Installing bubblewrap in WSL distro '$Distro'..."
Run "wsl.exe" @("-d", $Distro, "-u", "root", "-e", "sh", "-lc", "apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y bubblewrap")

Write-Host "Configuring Windows portproxy 0.0.0.0:$WslProxyPort -> 127.0.0.1:$WindowsProxyPort..."
& netsh interface portproxy delete v4tov4 listenaddress=0.0.0.0 listenport=$WslProxyPort 2>$null | Out-Null
Run "netsh.exe" @("interface", "portproxy", "add", "v4tov4", "listenaddress=0.0.0.0", "listenport=$WslProxyPort", "connectaddress=127.0.0.1", "connectport=$WindowsProxyPort")

$ruleName = "Codex WSL proxy $WslProxyPort"
$existingRule = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
if ($existingRule) {
    Remove-NetFirewallRule -DisplayName $ruleName
}
New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Action Allow -Protocol TCP -LocalPort $WslProxyPort -Profile Any | Out-Null

$markerStart = "# >>> codex wsl proxy >>>"
$markerEnd = "# <<< codex wsl proxy <<<"
$block = @"
$markerStart
# Route Codex/OpenAI traffic from WSL through the Windows proxy.
if command -v ip >/dev/null 2>&1; then
    _wsl_windows_host="`$(ip route | awk '/default/ {print `$3; exit}')"
    if [ -n "`$_wsl_windows_host" ]; then
        export HTTP_PROXY="http://`${_wsl_windows_host}:$WslProxyPort"
        export HTTPS_PROXY="`$HTTP_PROXY"
        export ALL_PROXY="`$HTTP_PROXY"
        export NO_PROXY="localhost,127.0.0.1,::1"
    fi
    unset _wsl_windows_host
fi
$markerEnd
"@

Write-Host "Updating /home/$WslUser/.bashrc idempotently..."
$encodedBlock = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($block))
$script = @"
set -eu
home_dir="/home/$WslUser"
bashrc="`$home_dir/.bashrc"
tmp="`$(mktemp)"
touch "`$bashrc"
awk '
  /^# >>> codex wsl proxy >>>$/ {skip=1; next}
  /^# <<< codex wsl proxy <<<$/ {skip=0; next}
  !skip {print}
' "`$bashrc" > "`$tmp"
cat "`$tmp" > "`$bashrc"
printf '\n' >> "`$bashrc"
printf '%s' '$encodedBlock' | base64 -d >> "`$bashrc"
chown ${WslUser}:${WslUser} "`$bashrc"
"@
Run "wsl.exe" @("-d", $Distro, "-u", "root", "-e", "bash", "-lc", $script)

Write-Host "Verification:"
Run "wsl.exe" @("-d", $Distro, "-u", $WslUser, "-e", "bash", "-ic", "command -v bwrap && bwrap --version && printf 'HTTP_PROXY=%s\n' ""`$HTTP_PROXY"" && curl -sS --max-time 20 https://chatgpt.com/cdn-cgi/trace | sed -n '1,20p'")

Write-Host ""
Write-Host "Done. In a new WSL shell, run:"
Write-Host "  source ~/.bashrc"
Write-Host "  codex doctor"
Write-Host "  codex login"
