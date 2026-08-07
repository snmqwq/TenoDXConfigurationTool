TenoDX Configuration Tool 使用说明
===================================

综合版：
  TenoDXConfigurationTool.exe test
  TenoDXConfigurationTool.exe config
  TenoDXConfigurationTool.exe dfu

独立版：
  TenoDXControllerTest.exe  双击打开综合测试界面
  TenoDXConfig.exe          双击打开设备配置界面
  TenoDXDFU.exe             在管理员命令行中运行完整 DFU 流程

配置导入导出：
  先连接 Aime / Magic 串口并读取设备配置。
  “导出配置”保存当前界面中的 Touch、LED 和按键草稿。
  “导入配置”只载入界面草稿，不会自动写入设备。
  每页“恢复默认”只临时应用到 RAM；需要永久保存时再点击“应用并保存到 Flash”。

DFU 示例：
  TenoDXDFU.exe
  TenoDXDFU.exe --port COM7
  TenoDXDFU.exe --firmware maimai_controller_H503_YYYYMMDD_HHMMSS.bin

固件放置：
  把固件直接放入本目录的 firmware 文件夹。
  文件名必须为 maimai_controller_H503_YYYYMMDD_HHMMSS.bin。
  只有一个有效固件时自动选择；存在多个时会要求选择。
  添加、删除或替换固件不需要重新打包 EXE。

注意：
  支持 Windows 10 22H2（19045）和 Windows 11 x64。
  DFU 功能需要管理员权限。
  首次在新电脑刷写前，建议先运行 DFU-driver\install_dfu_driver.cmd。
  该脚本会请求管理员权限，安装 ST 签名的 STM32 Bootloader WinUSB 驱动并验证设备。
  如果设备管理器显示“STM Device in DFU Mode”而程序无法识别，必须安装此驱动。
  切换到 WinUSB 后，旧版 STM DfuSe 工具可能无法再访问该设备。
  Test 和 Config 独立版不包含 DFU 刷写工具。
