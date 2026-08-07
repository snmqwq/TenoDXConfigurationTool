[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

function Test-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-Administrator)) {
    $arguments = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", ('"{0}"' -f $PSCommandPath)
    )
    $process = Start-Process -FilePath "powershell.exe" -Verb RunAs `
        -ArgumentList $arguments -Wait -PassThru
    exit $process.ExitCode
}

$infPath = Join-Path $PSScriptRoot "STM32Bootloader.inf"
if (-not (Test-Path -LiteralPath $infPath -PathType Leaf)) {
    throw "Missing driver INF: $infPath"
}

$pnputil = Join-Path $env:SystemRoot "System32\pnputil.exe"
Write-Host "Installing the signed STM32 Bootloader WinUSB driver..."
& $pnputil /add-driver $infPath /install
if ($LASTEXITCODE -ne 0) {
    throw "pnputil /add-driver failed with exit code $LASTEXITCODE"
}

Write-Host "Scanning for hardware changes..."
& $pnputil /scan-devices
if ($LASTEXITCODE -ne 0) {
    throw "pnputil /scan-devices failed with exit code $LASTEXITCODE"
}

Start-Sleep -Milliseconds 1000
$prefix = "USB\VID_0483&PID_DF11\"
$devices = @(
    Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue |
        Where-Object { $_.InstanceId.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase) }
)
if (-not $devices) {
    Write-Host "Driver installed. Connect a device in STM32 DFU mode to verify it."
    exit 0
}

$incompatible = @()
foreach ($device in $devices) {
    $property = Get-PnpDeviceProperty -InstanceId $device.InstanceId `
        -KeyName "DEVPKEY_Device_Service" -ErrorAction SilentlyContinue
    $service = if ($property) { [string]$property.Data } else { "UNKNOWN" }
    Write-Host ("{0}  service={1}  {2}" -f $device.FriendlyName, $service, $device.InstanceId)
    if ($service -ine "WINUSB") {
        $incompatible += $device
    }
}

if ($incompatible) {
    Write-Host "The connected DFU device is not using WINUSB. See README.txt for the manual Device Manager steps." -ForegroundColor Red
    exit 2
}

Write-Host "Success: every connected 0483:DF11 device is using WINUSB."
