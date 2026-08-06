param(
    [string]$Python
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $Python) {
    $Python = Join-Path $projectRoot ".venv\Scripts\python.exe"
}
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "找不到用于打包的 Python: $Python"
}

function Invoke-PyInstaller {
    param(
        [Parameter(Mandatory)]
        [string[]]$Arguments
    )

    & $Python -m PyInstaller @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller 打包失败，退出码 $LASTEXITCODE"
    }
}

Push-Location $projectRoot
try {
    Invoke-PyInstaller -Arguments @(
        "--noconfirm", "--clean", "--onefile", "--console",
        "--name", "TenoDXConfigurationTool",
        "--add-data", "images;images",
        "--add-data", "DFU\vendor;DFU\vendor",
        "--add-data", "DFU\licenses;DFU\licenses",
        "--add-data", "THIRD_PARTY_NOTICES.md;.",
        "--hidden-import", "tenodx_config.controller_test_ui",
        "--hidden-import", "tenodx_config.device_config_ui",
        "main.py"
    )

    Invoke-PyInstaller -Arguments @(
        "--noconfirm", "--clean", "--onefile", "--windowed",
        "--name", "TenoDXControllerTest",
        "--add-data", "images;images",
        "--exclude-module", "DFU",
        "--exclude-module", "tenodx_config.cli",
        "--exclude-module", "tenodx_config.device_config",
        "--exclude-module", "tenodx_config.device_config_ui",
        "--exclude-module", "tenodx_config.dfu_devices",
        "--exclude-module", "tenodx_config.firmware",
        "--exclude-module", "tenodx_config.magic",
        "--exclude-module", "tenodx_config.usb_reenumeration",
        "packaging\test_entrypoint.py"
    )

    Invoke-PyInstaller -Arguments @(
        "--noconfirm", "--clean", "--onefile", "--console",
        "--name", "TenoDXDFU",
        "--add-data", "DFU\vendor;DFU\vendor",
        "--add-data", "DFU\licenses;DFU\licenses",
        "--add-data", "THIRD_PARTY_NOTICES.md;.",
        "--exclude-module", "tenodx_config.controller_test_ui",
        "--exclude-module", "tenodx_config.controller_renderer",
        "--exclude-module", "tenodx_config.device_config",
        "--exclude-module", "tenodx_config.device_config_ui",
        "--exclude-module", "tenodx_config.aime_protocol",
        "--exclude-module", "tenodx_config.aime_reader",
        "--exclude-module", "tenodx_config.mai2led",
        "--exclude-module", "tenodx_config.raw_keyboard",
        "--exclude-module", "tenodx_config.touch_protocol",
        "packaging\dfu_entrypoint.py"
    )

    Invoke-PyInstaller -Arguments @(
        "--noconfirm", "--clean", "--onefile", "--windowed",
        "--name", "TenoDXConfig",
        "--exclude-module", "DFU",
        "--exclude-module", "tenodx_config.cli",
        "--exclude-module", "tenodx_config.controller_test_ui",
        "--exclude-module", "tenodx_config.controller_renderer",
        "--exclude-module", "tenodx_config.dfu_devices",
        "--exclude-module", "tenodx_config.firmware",
        "--exclude-module", "tenodx_config.usb_reenumeration",
        "packaging\config_entrypoint.py"
    )

    $distRoot = Join-Path $projectRoot "dist"
    $distFirmware = Join-Path $distRoot "firmware"
    New-Item -ItemType Directory -Path $distFirmware -Force | Out-Null
    Get-ChildItem -LiteralPath $distFirmware -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match '^maimai_controller_H503_\d{8}_\d{6}\.bin$' } |
        Remove-Item -Force
    Get-ChildItem -LiteralPath (Join-Path $projectRoot "firmware") -File |
        Where-Object { $_.Name -match '^maimai_controller_H503_\d{8}_\d{6}\.bin$' } |
        Copy-Item -Destination $distFirmware -Force

    $releaseRoot = Join-Path $projectRoot "release"
    if ((Split-Path -Parent $releaseRoot) -ne $projectRoot) {
        throw "发布目录不在项目根目录内: $releaseRoot"
    }
    if (Test-Path -LiteralPath $releaseRoot) {
        Remove-Item -LiteralPath $releaseRoot -Recurse -Force
    }
    New-Item -ItemType Directory -Path $releaseRoot | Out-Null
    foreach ($name in @(
        "TenoDXConfigurationTool.exe",
        "TenoDXControllerTest.exe",
        "TenoDXDFU.exe",
        "TenoDXConfig.exe"
    )) {
        Copy-Item -LiteralPath (Join-Path $distRoot $name) -Destination $releaseRoot
    }
    Copy-Item -LiteralPath $distFirmware -Destination $releaseRoot -Recurse
    Copy-Item -LiteralPath (Join-Path $projectRoot "THIRD_PARTY_NOTICES.md") -Destination $releaseRoot
    Copy-Item -LiteralPath (Join-Path $projectRoot "DFU\licenses") `
        -Destination (Join-Path $releaseRoot "DFU-licenses") -Recurse
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot "RELEASE_README.txt") `
        -Destination (Join-Path $releaseRoot "使用说明.txt")
} finally {
    Pop-Location
}

Write-Host "打包完成，构建目录: $(Join-Path $projectRoot 'dist')"
Write-Host "可发布目录: $(Join-Path $projectRoot 'release')"
