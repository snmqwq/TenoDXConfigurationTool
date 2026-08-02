# DFU 刷写组件

该目录是独立的“仅刷写”组件，不负责进入 DFU、发现设备、选择固件或等待应用重新枚举。

Python API：

```python
from DFU import flash_firmware

flash_firmware(
    device_id="0483:DF11",
    serial_number="目标 USB 序列号",
    firmware_path="firmware/maimai_controller_H503_20260802_220000.bin",
    on_output=print,
)
```

也可以独立从命令行调用：

```powershell
python -m DFU `
  --device-id 0483:DF11 `
  --serial <USB序列号> `
  --firmware <固件路径>
```

实际执行的核心命令为：

```text
dfu-util -d 0483:DF11 -S <USB序列号> -a 0 -s 0x08000000:leave -D <固件>
```

`vendor/` 中提供 Windows x64 的 `dfu-util 0.11` 和 `libusb-1.0.dll`，无需安装
STM32CubeProgrammer。Windows 仍需为 STM32 DFU 设备安装兼容的 WinUSB/libusbK
驱动。
