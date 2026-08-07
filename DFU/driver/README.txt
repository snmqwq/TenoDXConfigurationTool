TenoDX STM32 DFU WinUSB 驱动
===========================

适用系统：Windows 10/11 x64
适用设备：USB\VID_0483&PID_DF11

安装方法：
  1. 关闭正在使用 DFU 设备的程序。
  2. 双击 install_dfu_driver.cmd。
  3. 在 Windows 用户账户控制窗口中允许管理员权限。
  4. 脚本会安装 STMicroelectronics 签名驱动、扫描硬件，并验证当前 DFU 设备使用 WINUSB。

如果脚本报告设备仍未使用 WINUSB：
  1. 打开设备管理器，找到“STM Device in DFU Mode”。
  2. 选择“更新驱动程序”→“浏览我的电脑以查找驱动程序”。
  3. 指定本目录，保持“包括子文件夹”选中并完成安装。
  4. 安装成功后，设备名称应为“STM32 Bootloader”，驱动服务应为 WINUSB。

注意：
  切换到 WinUSB 后，旧版 STM DfuSe 工具可能无法再访问该设备。
  本驱动使用 Windows 自带 WinUSB.sys，不需要安装 STM32CubeProgrammer。

驱动信息：
  INF：STM32Bootloader.inf
  版本：1.3.0.0（2025-11-28）
  提供商与签名者：STMicroelectronics
  硬件 ID：USB\VID_0483&PID_DF11

许可：
  STMicroelectronics 软件包许可原文保留在 STM32Bootloader.inf 文件开头。
  该驱动仅可用于或结合 STMicroelectronics 制造的微控制器/微处理器设备。
