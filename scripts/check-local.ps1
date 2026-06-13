$ErrorActionPreference = "Continue"

$checks = @(
  @{ Name = "robot-gateway"; Url = "http://127.0.0.1/api/health" },
  @{ Name = "remote-api"; Url = "http://127.0.0.1:8000/api/health" },
  @{ Name = "camera-snapshot"; Url = "http://127.0.0.1:8099/api/health" },
  @{ Name = "action-move"; Url = "http://127.0.0.1:8094/api/health" },
  @{ Name = "audio-recognition"; Url = "http://127.0.0.1:8095/api/health" },
  @{ Name = "pi5-robot"; Url = "http://127.0.0.1:8093/api/health" },
  @{ Name = "smile-face"; Url = "http://127.0.0.1:8096/api/health" }
)

$failed = 0
foreach ($check in $checks) {
  try {
    $response = Invoke-WebRequest -Uri $check.Url -UseBasicParsing -TimeoutSec 5
    Write-Output ("OK   {0,-16} {1} {2}" -f $check.Name, [int]$response.StatusCode, $check.Url)
  } catch {
    $failed += 1
    Write-Output ("FAIL {0,-16} {1} ({2})" -f $check.Name, $check.Url, $_.Exception.Message)
  }
}

if ($failed -gt 0) {
  exit 1
}
