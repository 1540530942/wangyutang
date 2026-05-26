$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$FallbackKeyScript = "C:\Users\Administrator\AppData\Roaming\Tencent\xwechat\xwechat_files\wangjinnan1st_ee8a\msg\file\2026-05\test_qwen.py"

if (-not $env:DASHSCOPE_API_KEY -and (Test-Path -LiteralPath $FallbackKeyScript)) {
    $content = Get-Content -LiteralPath $FallbackKeyScript -Raw -Encoding UTF8
    $match = [regex]::Match($content, 'api_key\s*=\s*"([^"]+)"')
    if ($match.Success) {
        $env:DASHSCOPE_API_KEY = $match.Groups[1].Value
    }
}

Set-Location -LiteralPath $ProjectDir
python -m uvicorn app:app --host 0.0.0.0 --port 8100
