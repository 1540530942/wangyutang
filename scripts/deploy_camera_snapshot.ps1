param(
  [string]$RemoteHost = "tencent",
  [string]$RemoteRoot = "/root/camera_snapshot",
  [string]$RemotePlatformRoot = "/root/control_platform",
  [string]$RemoteComposeFile = "/root/control_platform/infra/docker-compose.platform.yml",
  [string]$PiHost = "raspberrypi-via-tencent",
  [string]$PiSenderPath = "/home/pi/pi_camera_sender.py",
  [string]$PiServicePath = "/etc/systemd/system/camera-snapshot-sender.service",
  [string]$SshKey = "$HOME\.ssh\codex_tencent_lighthouse",
  [switch]$SkipPi,
  [switch]$SkipVerify,
  [switch]$AllowDirty
)

$ErrorActionPreference = "Stop"

function Write-Step([string]$Message) {
  Write-Host ""
  Write-Host "==> $Message"
}

function Invoke-Checked([string]$Command, [string[]]$Arguments) {
  Write-Host ("+ {0} {1}" -f $Command, ($Arguments -join " "))
  & $Command @Arguments
  if ($LASTEXITCODE -ne 0) {
    throw "Command failed: $Command"
  }
}

function Wait-ForTaskResult([string]$Mode, [string]$ExpectedSource, [int]$TimeoutSec = 35) {
  $body = @{ mode = $Mode; query_gpio = 26 } | ConvertTo-Json -Compress
  $task = Invoke-RestMethod -Uri "https://www.wangyutang.cn/camera/api/capture" -Method Post -ContentType "application/json" -Body $body -TimeoutSec 15
  $deadline = (Get-Date).AddSeconds($TimeoutSec)
  do {
    Start-Sleep -Seconds 2
    $latest = Invoke-RestMethod -Uri "https://www.wangyutang.cn/camera/api/latest" -TimeoutSec 15
    if ($latest.task_id -eq $task.task.id -and $latest.capture_source -eq $ExpectedSource) {
      Write-Host ("OK   mode={0} task={1} source={2}" -f $Mode, $latest.task_id, $latest.capture_source)
      return
    }
  } while ((Get-Date) -lt $deadline)

  $seen = ""
  if ($latest) {
    $seen = "last task=$($latest.task_id) source=$($latest.capture_source) error=$($latest.capture_error)"
  }
  throw "Timed out waiting for mode=$Mode expected source=$ExpectedSource. $seen"
}

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RepoRoot

Write-Step "Checking source state"
$GitStatus = git status --short
if ($GitStatus -and -not $AllowDirty) {
  Write-Host $GitStatus
  throw "Working tree is dirty. Commit/stash changes or rerun with -AllowDirty for an intentional emergency release."
}

Write-Step "Running camera syntax checks"
Invoke-Checked "python" @("-m", "py_compile", "camera_snapshot\server.py", "camera_snapshot\pi_camera_sender.py")

$sshArgs = @()
$scpArgs = @()
if (Test-Path -LiteralPath $SshKey) {
  $sshArgs += @("-i", $SshKey)
  $scpArgs += @("-i", $SshKey)
}

Write-Step "Uploading camera source to Tencent Cloud"
Invoke-Checked "ssh" ($sshArgs + @($RemoteHost, "mkdir -p '$RemoteRoot/static'"))
Invoke-Checked "scp" ($scpArgs + @(
  "camera_snapshot\server.py",
  "camera_snapshot\pi_camera_sender.py",
  "camera_snapshot\README.md",
  "camera_snapshot\requirements.txt",
  "camera_snapshot\Dockerfile",
  "${RemoteHost}:$RemoteRoot/"
))
Invoke-Checked "scp" ($scpArgs + @(
  "camera_snapshot\static\app.js",
  "camera_snapshot\static\index.html",
  "camera_snapshot\static\styles.css",
  "${RemoteHost}:$RemoteRoot/static/"
))

Write-Step "Building and restarting cloud camera container"
$remoteCommand = @"
cd '$RemoteRoot' &&
python3 -m py_compile server.py pi_camera_sender.py &&
docker build --build-arg PYTHON_IMAGE=docker.m.daocloud.io/library/python:3.12-slim -t camera-snapshot:local . &&
cd '$RemotePlatformRoot' &&
docker compose -f '$RemoteComposeFile' up -d --no-deps --force-recreate camera-snapshot &&
docker ps --filter name=camera-snapshot --format '{{.Names}} {{.Status}}'
"@
Invoke-Checked "ssh" ($sshArgs + @($RemoteHost, $remoteCommand))

if (-not $SkipPi) {
  Write-Step "Installing Raspberry Pi sender and systemd service"
  Invoke-Checked "scp" @("camera_snapshot\pi_camera_sender.py", "${PiHost}:$PiSenderPath")
  Invoke-Checked "scp" @("camera_snapshot\systemd\camera-snapshot-sender.service", "${PiHost}:/tmp/camera-snapshot-sender.service")
  $piCommand = @"
sudo install -m 0644 /tmp/camera-snapshot-sender.service '$PiServicePath' &&
sudo rm -f /etc/systemd/system/camera-snapshot-sender.service.d/override.conf &&
sudo rmdir /etc/systemd/system/camera-snapshot-sender.service.d 2>/dev/null || true &&
sudo systemctl daemon-reload &&
sudo systemctl restart camera-snapshot-sender.service &&
systemctl status camera-snapshot-sender.service --no-pager -l | head -40 &&
ps -ef | grep -E 'pi_camera_sender.py' | grep -v grep
"@
  Invoke-Checked "ssh" @($PiHost, $piCommand)
} else {
  Write-Step "Skipping Raspberry Pi sender deployment"
}

if (-not $SkipVerify) {
  Write-Step "Verifying public camera page and capture modes"
  $html = Invoke-WebRequest -Uri "https://www.wangyutang.cn/camera/" -UseBasicParsing -TimeoutSec 20
  $labels = @(
    (-join @([char]0x6444, [char]0x50cf, [char]0x673a, [char]0x622a, [char]0x56fe)),
    (-join @([char]0x5c4f, [char]0x5e55, [char]0x622a, [char]0x56fe)),
    (-join @([char]0x8868, [char]0x60c5, [char]0x622a, [char]0x56fe))
  )
  foreach ($label in $labels) {
    if ($html.Content -notlike "*$label*") {
      throw "Camera page missing button label: $label"
    }
  }
  Invoke-RestMethod -Uri "https://www.wangyutang.cn/camera/api/health" -TimeoutSec 15 | Out-Null
  Wait-ForTaskResult "single" "opencv"
  Wait-ForTaskResult "screenshot" "screenshot"
  Wait-ForTaskResult "face" "face-screenshot"
} else {
  Write-Step "Skipping public verification"
}

Write-Host ""
Write-Host "CAMERA_DEPLOY_OK"
