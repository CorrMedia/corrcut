# Sign one file. Inno Setup's compiler is 32-bit, so it must launch this script
# with 64-bit PowerShell (C:\Windows\Sysnative\...). signtool prints
# "Successfully signed" and can still exit with an error when the CorrMedia
# certificate is self-signed and not yet trusted.
param(
    [Parameter(Mandatory = $true)][string]$Thumbprint,
    [Parameter(Mandatory = $true)][string]$Path
)

$ErrorActionPreference = "Continue"
$tool = Get-Command signtool.exe -ErrorAction SilentlyContinue
if (-not $tool) {
    Write-Error "signtool.exe was not found."
    exit 1
}
$log = & $tool.Source sign /fd SHA256 /sha1 $Thumbprint /tr http://timestamp.digicert.com /td SHA256 $Path 2>&1 | Out-String
Write-Host $log
if ($log -match "Successfully signed") {
    exit 0
}
Write-Error "Signing did not attach certificate $Thumbprint to $Path."
exit 1
