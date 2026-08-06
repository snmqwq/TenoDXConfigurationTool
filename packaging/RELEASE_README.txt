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
  DFU 功能需要管理员权限。
  Test 和 Config 独立版不包含 DFU 刷写工具。
