# TenoDX Configuration Tool

TenoDX 主控的配置程序。当前提供 STM32H503 固件更新，以及 Touch、主按键、
Aime 和 Mai2LED 的协议测试。

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

打开实时输入界面：

```powershell
python main.py test
```

所有测试位于同一个窗口，并且每个设备都必须由用户明确选择；即使只发现一个候选设备，
也不会自动选中。设备标签保留系统报告的设备描述、VID、PID 等身份信息。Touch、LED、
Aime 三路串口不能重复选择，各模块独立连接和断开，一个模块通信失败不会主动断开其他模块。

- Touch / BTN：只显示当前触发的 34 个触摸区域与 `BTN1`-`BTN8`；1P、2P 键位映射到
  同一组主按键。连接 Touch 后只发送 `{RSET}` 和 `{STAT}`。
- Aime：验证 Aime 协议，显示固件/硬件版本、当前有无卡、20 位 Aime 协议卡号和
  Block 2 原始 16 字节。Block 2 不是合法 BCD 时仍保留原始数据，但将卡号标为无法解析。
  此功能只读卡，不写卡，也不执行 Magic 命令或 PN532 底层诊断。
- Mai2LED：验证 `15070-04` 协议，提供自定义 RGB 常亮、RGBW 循环、`BTN1`-`BTN8`
  逐灯和目标色淡入淡出。循环持续到手动停止；停止、断开或关闭窗口时会请求全灭。

界面不包含测试引导、历史记录、通过/失败判定，也不会发送触摸灵敏度调整命令。
Mai2LED 返回 ACK 只代表协议通信成功，不能代替对灯珠是否实际点亮的观察。

打开独立设备配置窗口：

```powershell
python main.py config
```

配置窗口通过 Aime/Magic 使用的同一路 CDC2 串口通信，因此不能与综合测试界面的 Aime
读卡功能同时连接。窗口包含三个配置页：

- Touch：为物理通道 `0`-`33` 分别选择一个逻辑区块（`A1`-`A8`、`B1`-`B8`、
  `C1`-`C2`、`D1`-`D8`、`E1`-`E8`）。同一区块允许被多个通道复用，扫描 Block
  由所选区块自动确定并只读显示。
- LED：设置每个逻辑灯对应的灯珠数量 `1`-`4`，显示物理灯珠总数 `8 × N`，并设置
  空闲彩虹效果开关。
- 按键：整体选择主按键 `1P`/`2P` 布局，设置 `EK_1`-`EK_4` 的已知 HID 键值或禁用；
  `BTN1`-`BTN8` 仅供查看。

每页都可以重新读取设备配置、临时应用到 RAM，或应用并保存到 Flash。控件变化不会自动
写入设备或擦写 Flash。简易配置窗口不包含 Touch raw 模式切换、恢复默认配置，也不接受
任意 HID 原始字节。

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
├─ tenodx_config/          应用端：配置流程、设备协议与综合测试界面
├─ images/                 触摸区域与 BTN1-BTN8 实时显示素材
├─ DFU/                    独立刷写组件及 Windows x64 dfu-util
├─ firmware/               时间戳 BIN 固件目录（初始为空）
├─ tests/
└─ THIRD_PARTY_NOTICES.md
```

## 第三方软件

dfu-util 使用 GPL-2.0-or-later。上游许可证、发布说明及来源记录保存在
`DFU/licenses/`，汇总见 `THIRD_PARTY_NOTICES.md`。
