param(
    [string]$Version,
    [string]$InstallRoot,
    [switch]$Yes
)
$ErrorActionPreference = 'Stop'
$python = $null
$prefix = @()
foreach ($candidate in @(@{ Name = 'python'; Prefix = @() }, @{ Name = 'py'; Prefix = @('-3') })) {
    $found = Get-Command $candidate.Name -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $found) { continue }
    $candidatePrefix = $candidate.Prefix
    & $found.Source @candidatePrefix -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>$null
    if ($LASTEXITCODE -eq 0) { $python = $found; $prefix = $candidatePrefix; break }
}
if (-not $python) { throw 'Please install Python 3.10+ and make it available on PATH.' }
$tempFile = Join-Path ([IO.Path]::GetTempPath()) ('kemo-bootstrap-' + [guid]::NewGuid().ToString('N') + '.py')
try {
    Invoke-WebRequest -Uri 'https://raw.githubusercontent.com/kesepain-KE/kemo-agent/main/deploy/bootstrap.py' -OutFile $tempFile
    $arguments = @($tempFile, '--platform', 'windows')
    if ($Version) { $arguments += @('--version', $Version) }
    if ($InstallRoot) { $arguments += @('--install-root', $InstallRoot) }
    if ($Yes) { $arguments += '--yes' }
    & $python.Source @prefix @arguments
    if ($LASTEXITCODE -ne 0) { throw 'Deployment failed. Read the error above.' }
} finally {
    if (Test-Path -LiteralPath $tempFile) { Remove-Item -LiteralPath $tempFile -Force }
}
