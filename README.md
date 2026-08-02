# TenoDX Configuration Tool

TenoDX 主控的命令行配置程序。当前版本实现 STM32H503 固件更新，后续配置功能可在
应用层继续扩展。

## DFU 更新流程

```text
选择严格时间戳命名的固件
  -> 验证 TenoDX Aime/Magic 串口
  -> 发送 Magic ENTER_DFU 命令
  -> 等待新的 0483:DF11，并取得 USB 序列号
  -> 把 VID:PID、USB 序列号、固件路径传给 DFU 组件
  -> dfu-util 写入 0x08000000 并 leave
  -> 卸载所选 DFU 设备节点并扫描 USB 设备变化
  -> 重新验证应用设备的 Magic 协议
  -> 关闭并释放串口
```

应用层与 `DFU/` 刷写组件严格分离。`DFU/` 只负责刷写，不选择固件、不进入 DFU、
不发现设备，也不检查应用重新枚举。

## 环境

- Windows x64
- Python 3.10 或更高版本
- STM32 DFU 已绑定 WinUSB、libusbK 或兼容驱动
- 以管理员权限运行，以便使用 Windows PnPUtil 卸载 DFU 设备节点

安装 Python 依赖：

```powershell
python -m pip install -r requirements.txt
```

项目随附 `dfu-util 0.11` 和 `libusb-1.0.dll`，不依赖 STM32CubeProgrammer 或
STM32CubeIDE。DFU 刷写后，应用端使用 Windows 自带的 `pnputil.exe` 精确卸载所选
DFU 节点并同步扫描设备变化；随后仍以应用设备重新枚举且通过 Magic 验证作为成功
条件。

## 固件目录

把固件直接放入 `firmware/`。只识别以下严格格式的非空文件：

```text
maimai_controller_H503_YYYYMMDD_HHMMSS.bin
```

无时间戳的 `maimai_controller_H503.bin` 会被排除。只有一个有效固件时自动选择；
存在多个时按时间从新到旧列出，并要求用户明确选择。

## 使用

自动探测兼容的 Aime/Magic 串口：

```powershell
python main.py dfu
```

指定应用串口：

```powershell
python main.py dfu --port COM7
```

指定 `firmware/` 中的固件：

```powershell
python main.py dfu --firmware firmware\maimai_controller_H503_20260802_220000.bin
```

查看参数：

```powershell
python main.py dfu --help
```

`DFU/` 组件也可以独立调用，详见 `DFU/README.md`。

## 目录结构

```text
TenoDXConfigurationTool/
├─ main.py
├─ tenodx_config/          应用端：固件选择、Magic、DFU 发现与流程编排
├─ DFU/                    独立刷写组件及 Windows x64 dfu-util
├─ firmware/               时间戳 BIN 固件目录（初始为空）
├─ tests/
└─ THIRD_PARTY_NOTICES.md
```

## 第三方软件

dfu-util 使用 GPL-2.0-or-later。上游许可证、发布说明及来源记录保存在
`DFU/licenses/`，汇总见 `THIRD_PARTY_NOTICES.md`。
