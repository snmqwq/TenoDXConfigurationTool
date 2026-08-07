# Third-party notices

## dfu-util 0.11

This product includes dfu-util, licensed under GPL-2.0-or-later.

- Project: https://dfu-util.sourceforge.net/
- Source release: https://dfu-util.sourceforge.net/releases/dfu-util-0.11.tar.gz
- Binary release: https://dfu-util.sourceforge.net/releases/dfu-util-0.11-binaries.tar.xz

The upstream license and binary-release notes are stored in `DFU/licenses/`.

## libusb

The bundled `libusb-1.0.dll` was built from libusb commit `1a90627`, as recorded
by the upstream dfu-util binary-release README. libusb is licensed under
LGPL-2.1-or-later.

- Project: https://libusb.info/
- Source: https://github.com/libusb/libusb/tree/1a90627

The upstream LGPL license is stored as `DFU/licenses/libusb-COPYING.txt`.

## STM32 Bootloader WinUSB driver 1.3.0.0

This distribution includes the STMicroelectronics-signed INF and catalog for
`USB\\VID_0483&PID_DF11`. The package binds the STM32 system-memory bootloader
to the Microsoft WinUSB service and contains no third-party kernel driver
binary.

The STMicroelectronics software-package license is retained verbatim at the
start of `DFU/driver/STM32Bootloader.inf`. Redistribution is permitted subject
to those terms, including use solely on or in combination with devices
manufactured by or for STMicroelectronics.
