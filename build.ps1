<#
.SYNOPSIS
  Builds dist\MyfuelaiConnector\ (PyInstaller onedir), optionally Authenticode-signs every
  executable file in it, and writes SHA-256 hashes for application-control whitelisting.

.EXAMPLE
  .\build.ps1                                             # unsigned build + hashes
  .\build.ps1 -CertThumbprint 0123ABCD...                 # sign with a cert in the CurrentUser\My store
  .\build.ps1 -PfxPath .\codesign.pfx -PfxPassword (Read-Host -AsSecureString)

.NOTES
  Python: the QuickBooks SDK request processor (QBXMLRP2) may only be registered 32-bit. If the
  proof of concept shows that, build with a 32-bit Python: .\build.ps1 -Python 'py -3.12-32'
#>
param(
    [string]$Python = 'py -3.12',
    [string]$CertThumbprint,
    [string]$PfxPath,
    [SecureString]$PfxPassword,
    [string]$TimestampUrl = 'http://timestamp.digicert.com'
)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$venv = Join-Path $PSScriptRoot '.build-venv'
if (-not (Test-Path $venv)) { Invoke-Expression "$Python -m venv `"$venv`"" }
$py = Join-Path $venv 'Scripts\python.exe'
& $py -m pip install --quiet --upgrade pip
& $py -m pip install --quiet -r requirements.txt
& $py -c "import struct; print('Building with', struct.calcsize('P') * 8, 'bit Python')"

& $py -m PyInstaller --clean --noconfirm MyfuelaiConnector.spec
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed' }
$out = Join-Path $PSScriptRoot 'dist\MyfuelaiConnector'
$binaries = Get-ChildItem $out -Recurse -Include *.exe, *.dll, *.pyd

if ($CertThumbprint -or $PfxPath) {
    $signtool = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin\*\x64\signtool.exe" -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending | Select-Object -First 1
    if (-not $signtool) { throw 'signtool.exe not found - install the Windows SDK signing tools' }
    $signArgs = @('sign', '/fd', 'SHA256', '/tr', $TimestampUrl, '/td', 'SHA256')
    if ($CertThumbprint) { $signArgs += @('/sha1', $CertThumbprint) }
    else {
        $plain = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($PfxPassword))
        $signArgs += @('/f', $PfxPath, '/p', $plain)
    }
    # Only sign what isn't already signed by its vendor (e.g. python312.dll is signed by the PSF).
    $unsigned = $binaries | Where-Object { (Get-AuthenticodeSignature $_.FullName).Status -ne 'Valid' }
    foreach ($batch in ($unsigned | ForEach-Object FullName)) {
        & $signtool.FullName @signArgs $batch
        if ($LASTEXITCODE -ne 0) { throw "signing failed: $batch" }
    }
    Write-Host "Signed $(@($unsigned).Count) file(s)."
}

# Hashes of every executable file, for a hash-based ThreatLocker policy. Written outside the
# output folder so the list itself isn't part of the install.
$hashFile = Join-Path $PSScriptRoot 'dist\MyfuelaiConnector-hashes.txt'
$binaries | Get-FileHash -Algorithm SHA256 |
    ForEach-Object { '{0}  {1}' -f $_.Hash, $_.Path.Substring($out.Length + 1) } |
    Set-Content -Encoding utf8 $hashFile
Write-Host "Build: $out"
Write-Host "Hashes: $hashFile ($(@($binaries).Count) files)"
Get-AuthenticodeSignature (Join-Path $out 'MyfuelaiConnector.exe') | Format-List Status, SignerCertificate
