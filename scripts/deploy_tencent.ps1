param(
  [string]$ReleaseId = (Get-Date -Format "yyyyMMdd-HHmmss"),
  [string]$RemoteHost = "tencent",
  [string]$RemoteRoot = "/root/wangyutang_platform",
  [string]$SshKey = "$HOME\.ssh\codex_tencent_lighthouse",
  [switch]$SkipBuild,
  [switch]$SkipUpload,
  [switch]$SkipRemoteApply,
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

function Copy-IfExists([string]$Source, [string]$Destination) {
  if (Test-Path -LiteralPath $Source) {
    Copy-Item -LiteralPath $Source -Destination $Destination -Force
  }
}

function Copy-DirIfExists([string]$Source, [string]$Destination) {
  if (Test-Path -LiteralPath $Source) {
    Copy-Item -LiteralPath $Source -Destination $Destination -Recurse -Force
  }
}

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RepoRoot

$ReleaseRoot = Join-Path $RepoRoot "dist\tencent_releases"
$ReleaseDir = Join-Path $ReleaseRoot $ReleaseId
$ImagesDir = Join-Path $ReleaseDir "images"
$InfraDir = Join-Path $ReleaseDir "infra"
$GatewayDir = Join-Path $ReleaseDir "robot_gateway"
$ScriptsDir = Join-Path $ReleaseDir "scripts"
$DocsDir = Join-Path $ReleaseDir "docs"

$Images = @(
  @{ Name = "camera-snapshot:local"; File = "camera-snapshot_local.tar" },
  @{ Name = "action-move:local"; File = "action-move_local.tar" },
  @{ Name = "audio-recognition:local"; File = "audio-recognition_local.tar" },
  @{ Name = "audio-interact:local"; File = "audio-interact_local.tar" },
  @{ Name = "pi5-robot:local"; File = "pi5-robot_local.tar" },
  @{ Name = "slam-mapping:local"; File = "slam-mapping_local.tar" }
)

Write-Step "Checking git state"
$Commit = (git rev-parse --short HEAD).Trim()
$Branch = (git rev-parse --abbrev-ref HEAD).Trim()
$GitStatus = git status --short
if ($GitStatus -and -not $AllowDirty) {
  Write-Host $GitStatus
  throw "Working tree is dirty. Commit/stash changes or rerun with -AllowDirty for an intentional emergency release."
}

Write-Step "Running local checks"
Invoke-Checked "python" @("-m", "compileall", "-q", "camera_snapshot", "action_move", "audio_recognition", "audio_interact", "pi5_robot", "slam_mapping")
Invoke-Checked "docker" @("compose", "config", "--quiet")

if (-not $SkipBuild) {
  Write-Step "Building Docker images"
  Invoke-Checked "docker" @("compose", "build")
} else {
  Write-Step "Skipping Docker build"
}

Write-Step "Creating release directory $ReleaseDir"
if (Test-Path -LiteralPath $ReleaseDir) {
  Remove-Item -LiteralPath $ReleaseDir -Recurse -Force
}
New-Item -ItemType Directory -Path $ImagesDir, $InfraDir, $GatewayDir, $ScriptsDir, $DocsDir -Force | Out-Null

Copy-Item -LiteralPath "docker-compose.yml" -Destination (Join-Path $ReleaseDir "docker-compose.yml") -Force
Copy-Item -LiteralPath "robot_gateway\Caddyfile" -Destination (Join-Path $GatewayDir "Caddyfile") -Force
Copy-Item -LiteralPath "robot_gateway\site" -Destination (Join-Path $GatewayDir "site") -Recurse -Force
Copy-IfExists ".env.example" (Join-Path $ReleaseDir ".env.example")
Copy-IfExists ".env" (Join-Path $ReleaseDir ".env.local-copy")
Copy-Item -LiteralPath "scripts\tencent_apply_release.sh" -Destination (Join-Path $ScriptsDir "tencent_apply_release.sh") -Force
Copy-Item -LiteralPath "DEPLOY.md" -Destination (Join-Path $DocsDir "DEPLOY.md") -Force
Copy-DirIfExists "docs" (Join-Path $DocsDir "repo-docs")

Set-Content -LiteralPath (Join-Path $ReleaseDir "RELEASE_INFO.txt") -Encoding UTF8 -Value @(
  "release_id=$ReleaseId"
  "branch=$Branch"
  "commit=$Commit"
  "created_at=$(Get-Date -Format o)"
  "allow_dirty=$([bool]$AllowDirty)"
)

Write-Step "Exporting images"
$HashLines = @()
foreach ($Image in $Images) {
  $archive = Join-Path $ImagesDir $Image.File
  Invoke-Checked "docker" @("image", "inspect", $Image.Name)
  Invoke-Checked "docker" @("save", "-o", $archive, $Image.Name)
  $hash = Get-FileHash -Algorithm SHA256 -LiteralPath $archive
  $HashLines += "$($hash.Hash)  images/$($Image.File)"
}
Set-Content -LiteralPath (Join-Path $ReleaseDir "SHA256SUMS.txt") -Encoding UTF8 -Value $HashLines

$RemoteRelease = "$RemoteRoot/releases/$ReleaseId"

if (-not $SkipUpload) {
  Write-Step "Uploading release to ${RemoteHost}:$RemoteRelease"
  $sshArgs = @()
  $scpArgs = @()
  if (Test-Path -LiteralPath $SshKey) {
    $sshArgs += @("-i", $SshKey)
    $scpArgs += @("-i", $SshKey)
  }
  Invoke-Checked "ssh" ($sshArgs + @($RemoteHost, "mkdir -p '$RemoteRelease'"))
  Invoke-Checked "scp" ($scpArgs + @("-r", "$ReleaseDir\*", "${RemoteHost}:$RemoteRelease/"))
} else {
  Write-Step "Skipping upload"
}

if (-not $SkipRemoteApply) {
  Write-Step "Applying release on server"
  $sshArgs = @()
  if (Test-Path -LiteralPath $SshKey) {
    $sshArgs += @("-i", $SshKey)
  }
  Invoke-Checked "ssh" ($sshArgs + @($RemoteHost, "bash '$RemoteRelease/scripts/tencent_apply_release.sh' '$RemoteRelease' '$RemoteRoot'"))
} else {
  Write-Step "Skipping remote apply"
}

$VerifyResults = @()
if (-not $SkipVerify) {
  Write-Step "Verifying service health URLs"
  $Checks = @(
    "https://www.wangyutang.cn/api/health",
    "https://www.wangyutang.cn/",
    "http://110.40.154.41:8099/api/health",
    "http://110.40.154.41:8094/api/health",
    "http://110.40.154.41:8095/api/health",
    "http://110.40.154.41:8097/api/health",
    "http://110.40.154.41:8093/api/health",
    "http://110.40.154.41:8301/api/health",
    "http://110.40.154.41:8096/api/health"
  )
  foreach ($url in $Checks) {
    try {
      $response = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 20
      $line = "OK   $([int]$response.StatusCode) $url"
      Write-Host $line
      $VerifyResults += $line
    } catch {
      $line = "FAIL $url $($_.Exception.Message)"
      Write-Host $line
      $VerifyResults += $line
      throw "Verification failed: $url"
    }
  }
} else {
  $VerifyResults += "verification skipped"
}

Write-Step "Writing deployment log"
$LogDir = Join-Path $RepoRoot "docs\logs"
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
$LogPath = Join-Path $LogDir ("{0}-tencent-deploy-{1}.md" -f (Get-Date -Format "yyyy-MM-dd"), $ReleaseId)
Set-Content -LiteralPath $LogPath -Encoding UTF8 -Value @(
  "# Tencent Deploy $ReleaseId"
  ""
  "- Time: $(Get-Date -Format o)"
  "- Branch: $Branch"
  "- Commit: $Commit"
  "- Release dir: $ReleaseDir"
  "- Remote release: $RemoteRelease"
  "- SkipBuild: $([bool]$SkipBuild)"
  "- AllowDirty: $([bool]$AllowDirty)"
  ""
  "## Images"
  ""
  ($Images | ForEach-Object { "- $($_.Name) -> images/$($_.File)" })
  ""
  "## Verification"
  ""
  ($VerifyResults | ForEach-Object { "- $_" })
)

Write-Host ""
Write-Host "DEPLOY_RELEASE=$ReleaseId"
Write-Host "DEPLOY_LOG=$LogPath"
