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
        "--add-data", "firmware;firmware",
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
        "--add-data", "firmware;firmware",
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
} finally {
    Pop-Location
}

Write-Host "打包完成，输出目录: $(Join-Path $projectRoot 'dist')"
