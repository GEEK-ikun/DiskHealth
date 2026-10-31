"""S.M.A.R.T. 采集引擎（纯 Windows IOCTL + ctypes，无第三方依赖）。

由 P0 探针 smart_probe.py 提炼而来，剥掉命令行外壳，作为 GUI 的数据层。
对外主要接口：
    is_admin()                      -> bool
    relaunch_elevated(extra)        -> bool    通过 UAC 提权重启
    scan(drive_indices=None)        -> (results, volumes, elapsed)
    BUS_TYPES / KEY_ATA_ATTRS / CRITICAL_ATA_ATTRS
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import tempfile
import time
from ctypes import wintypes

if os.name != "nt":
    sys.exit("本探针仅支持 Windows。")

# Windows 控制台默认 GBK，中文与表格会乱码；统一强制 UTF-8
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# --------------------------------------------------------------------------
# 常量
# --------------------------------------------------------------------------
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

IOCTL_STORAGE_QUERY_PROPERTY = 0x0002D1400
IOCTL_STORAGE_GET_DEVICE_NUMBER = 0x0002D1080
IOCTL_STORAGE_PREDICT_FAILURE = 0x0007409C
IOCTL_ATA_PASS_THROUGH = 0x0004D02C
IOCTL_SCSI_PASS_THROUGH_DIRECT = 0x0004D014
SMART_RCV_DRIVE_DATA = 0x0007C088
IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS = 0x00560000

STORAGE_DEVICE_PROPERTY = 0
STORAGE_DEVICE_PROTOCOL_SPECIFIC_PROPERTY = 49
PROPERTY_STANDARD_QUERY = 0
PROTOCOL_TYPE_NVME = 3
NVME_DATA_TYPE_LOG_PAGE = 2
NVME_LOG_PAGE_HEALTH_INFO = 0x02

ATA_FLAGS_DRDY_REQUIRED = 0x01
ATA_FLAGS_DATA_IN = 0x02
ATA_FLAGS_USE_DMA = 0x10

BUS_TYPES = {
    0: "unknown", 1: "SCSI", 2: "ATAPI", 3: "ATA", 4: "1394", 5: "SSA",
    6: "Fibre", 7: "USB", 8: "RAID", 9: "iSCSI", 10: "SAS", 11: "SATA",
    12: "SD", 13: "MMC", 14: "Virtual", 15: "FileBackedVirtual",
    16: "Spaces", 17: "NVMe", 18: "SCM", 19: "UFS",
}

# 需要重点关注/展示的 ATA 属性
KEY_ATA_ATTRS = {
    1: "Read_Error_Rate", 3: "Spin_Up_Time", 4: "Start_Stop_Count",
    5: "Reallocated_Sector_Ct", 7: "Seek_Error_Rate", 9: "Power_On_Hours",
    10: "Spin_Retry_Count", 12: "Power_Cycle_Count", 177: "Wear_Leveling_Count",
    179: "Used_Reserved_Blk_Ct", 181: "Program_Fail_Cnt", 182: "Erase_Fail_Cnt",
    183: "Runtime_Bad_Block", 184: "End-to-End_Error", 187: "Reported_Uncorrect",
    188: "Command_Timeout", 189: "High_Fly_Writes", 190: "Airflow_Temp",
    191: "G-Sense_Error_Rate", 192: "Power-Off_Retract_Count",
    193: "Load_Cycle_Count", 194: "Temperature_Celsius",
    195: "Hardware_ECC_Recovered", 196: "Reallocated_Event_Count",
    197: "Current_Pending_Sector", 198: "Offline_Uncorrectable",
    199: "UDMA_CRC_Error_Count", 200: "Multi_Zone_Error_Rate",
    231: "SSD_Life_Left", 233: "Media_Wearout_Ind", 241: "Total_LBAs_Written",
    242: "Total_LBAs_Read",
}
# 值为「越低越健康」且非零即可疑的属性
CRITICAL_ATA_ATTRS = {5, 187, 188, 196, 197, 198, 199}


# --------------------------------------------------------------------------
# 结构体
# --------------------------------------------------------------------------
class STORAGE_PROPERTY_QUERY(ctypes.Structure):
    _fields_ = [
        ("PropertyId", ctypes.c_int),
        ("QueryType", ctypes.c_int),
        ("AdditionalParameters", ctypes.c_ubyte * 8),
    ]


class STORAGE_DEVICE_DESCRIPTOR(ctypes.Structure):
    _fields_ = [
        ("Version", ctypes.c_ulong),
        ("Size", ctypes.c_ulong),
        ("DeviceType", ctypes.c_ubyte),
        ("DeviceTypeModifier", ctypes.c_ubyte),
        ("RemovableMedia", ctypes.c_ubyte),
        ("CommandQueueing", ctypes.c_ubyte),
        ("VendorIdOffset", ctypes.c_ulong),
        ("ProductIdOffset", ctypes.c_ulong),
        ("ProductRevisionOffset", ctypes.c_ulong),
        ("SerialNumberOffset", ctypes.c_ulong),
        ("BusType", ctypes.c_int),
        ("RawPropertiesLength", ctypes.c_ulong),
        ("RawDeviceProperties", ctypes.c_ubyte * 1024),
    ]


class STORAGE_DEVICE_NUMBER(ctypes.Structure):
    _fields_ = [
        ("DeviceType", ctypes.c_ulong),
        ("DeviceNumber", ctypes.c_ulong),
        ("PartitionNumber", ctypes.c_ulong),
    ]


class STORAGE_PREDICT_FAILURE(ctypes.Structure):
    _fields_ = [
        ("PredictFailure", ctypes.c_ulong),
        ("VendorSpecific", ctypes.c_ubyte * 512),
    ]


class ATA_PASS_THROUGH_EX(ctypes.Structure):
    _fields_ = [
        ("Length", ctypes.c_ushort),
        ("AtaFlags", ctypes.c_ushort),
        ("PathId", ctypes.c_ubyte),
        ("TargetId", ctypes.c_ubyte),
        ("Lun", ctypes.c_ubyte),
        ("ReservedAsUchar", ctypes.c_ubyte),
        ("DataTransferLength", ctypes.c_ulong),
        ("TimeOutValue", ctypes.c_ulong),
        ("ReservedAsUlong", ctypes.c_ulong),
        ("DataBufferOffset", ctypes.c_size_t),
        ("PreviousTaskFile", ctypes.c_ubyte * 8),
        ("CurrentTaskFile", ctypes.c_ubyte * 8),
    ]


class ATA_PASS_THROUGH_EX_BUF(ctypes.Structure):
    """把 512 字节数据缓冲区紧跟在结构体之后，保证 8 字节对齐。"""
    _fields_ = [
        ("apt", ATA_PASS_THROUGH_EX),
        ("data", ctypes.c_ubyte * 512),
    ]


class IDEREGS(ctypes.Structure):
    _fields_ = [
        ("bFeaturesReg", ctypes.c_ubyte),
        ("bSectorCountReg", ctypes.c_ubyte),
        ("bSectorNumberReg", ctypes.c_ubyte),
        ("bCylLowReg", ctypes.c_ubyte),
        ("bCylHighReg", ctypes.c_ubyte),
        ("bDriveHeadReg", ctypes.c_ubyte),
        ("bCommandReg", ctypes.c_ubyte),
        ("bReserved", ctypes.c_ubyte),
    ]


class SENDCMDINPARAMS(ctypes.Structure):
    _fields_ = [
        ("cBufferSize", ctypes.c_ulong),
        ("irDriveRegs", IDEREGS),
        ("bDriveNumber", ctypes.c_ubyte),
        ("bReserved", ctypes.c_ubyte * 3),
        ("dwReserved", ctypes.c_ulong * 4),
        ("bBuffer", ctypes.c_ubyte * 16),
    ]


class DRIVERSTATUS(ctypes.Structure):
    _fields_ = [
        ("bDriverError", ctypes.c_ubyte),
        ("bIDEError", ctypes.c_ubyte),
        ("bReserved", ctypes.c_ubyte * 2),
        ("dwReserved", ctypes.c_ulong * 2),
    ]


class SENDCMDOUTPARAMS(ctypes.Structure):
    _fields_ = [
        ("cBufferSize", ctypes.c_ulong),
        ("DriverStatus", DRIVERSTATUS),
        ("bBuffer", ctypes.c_ubyte * 512),
    ]


class SCSI_PASS_THROUGH_DIRECT(ctypes.Structure):
    _fields_ = [
        ("Length", ctypes.c_ushort),
        ("ScsiStatus", ctypes.c_ubyte),
        ("PathId", ctypes.c_ubyte),
        ("TargetId", ctypes.c_ubyte),
        ("Lun", ctypes.c_ubyte),
        ("CdbLength", ctypes.c_ubyte),
        ("SenseInfoLength", ctypes.c_ubyte),
        ("DataIn", ctypes.c_ubyte),
        ("DataTransferLength", ctypes.c_ulong),
        ("TimeOutValue", ctypes.c_ulong),
        ("DataBuffer", ctypes.c_void_p),
        ("SenseInfoOffset", ctypes.c_ulong),
        ("Cdb", ctypes.c_ubyte * 16),
    ]


class SCSI_PASS_THROUGH_DIRECT_BUF(ctypes.Structure):
    _fields_ = [
        ("sptd", SCSI_PASS_THROUGH_DIRECT),
        ("sense", ctypes.c_ubyte * 32),
        ("data", ctypes.c_ubyte * 512),
    ]


class STORAGE_PROTOCOL_SPECIFIC_DATA(ctypes.Structure):
    _fields_ = [
        ("ProtocolType", ctypes.c_ulong),
        ("DataType", ctypes.c_ulong),
        ("ProtocolDataRequestValue", ctypes.c_ulong),
        ("ProtocolDataRequestSubValue", ctypes.c_ulong),
        ("ProtocolDataOffset", ctypes.c_ulong),
        ("ProtocolDataLength", ctypes.c_ulong),
        ("FixedProtocolReturnData", ctypes.c_ulong),
        ("ProtocolDataRequestSubValue2", ctypes.c_ulong),
        ("ProtocolDataRequestSubValue3", ctypes.c_ulong),
        ("ProtocolDataRequestSubValue4", ctypes.c_ulong),
    ]


class DISK_EXTENT(ctypes.Structure):
    _fields_ = [
        ("DiskNumber", ctypes.c_ulong),
        ("StartingOffset", ctypes.c_longlong),
        ("ExtentLength", ctypes.c_longlong),
    ]


class VOLUME_DISK_EXTENTS(ctypes.Structure):
    _fields_ = [
        ("NumberOfDiskExtents", ctypes.c_ulong),
        ("Extents", DISK_EXTENT * 8),
    ]


# --------------------------------------------------------------------------
# Win32 原型
# --------------------------------------------------------------------------
kernel32.CreateFileW.restype = wintypes.HANDLE
kernel32.CreateFileW.argtypes = [
    wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
    wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.DeviceIoControl.restype = wintypes.BOOL
kernel32.DeviceIoControl.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
    wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID,
]


def _err() -> str:
    e = ctypes.get_last_error()
    return f"[{e}] {ctypes.FormatError(e).strip()}"


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _python_console_exe() -> str:
    """IDLE 里 sys.executable 是 pythonw.exe，没有控制台，需要换回 python.exe。"""
    exe = sys.executable or "python.exe"
    if exe.lower().endswith("pythonw.exe"):
        cand = exe[: -len("pythonw.exe")] + "python.exe"
        if os.path.exists(cand):
            return cand
    return exe


def relaunch_elevated(extra_args: str) -> bool:
    """通过 UAC 以管理员身份重新启动自己。

    直接调用 ShellExecuteW（Unicode API）而不是写 .bat：
    批处理文件会被 cmd 按当前代码页解析，脚本名含中文时（例如「电池.py」）
    会被写成问号导致提权后找不到文件。
    """
    try:
        script = os.path.abspath(__file__)
        exe = _python_console_exe()
        params = f'"{script}"{extra_args}'
        rc = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", exe, params, os.path.dirname(script) or None, 1,
        )
        if rc > 32:
            return True
        print(f"ShellExecuteW 返回 {rc}（1223 = 你在 UAC 窗口点了『否』）")
        return False
    except Exception as exc:  # noqa: BLE001
        print(f"提权失败: {exc}")
        return False


def open_drive(idx: int, write: bool = True):
    """打开 \\\\.\\PhysicalDriveN。返回 (handle, error_str)。"""
    path = f"\\\\.\\PhysicalDrive{idx}"
    access = GENERIC_READ | (GENERIC_WRITE if write else 0)
    h = kernel32.CreateFileW(
        path, access, FILE_SHARE_READ | FILE_SHARE_WRITE,
        None, OPEN_EXISTING, 0, None,
    )
    if h == INVALID_HANDLE_VALUE:
        return None, _err()
    return h, None


def ioctl(handle, code, in_buf, out_size, in_size=None):
    """执行 DeviceIoControl。返回 (ok, out_bytes, err)。"""
    if in_size is None:
        in_size = ctypes.sizeof(in_buf) if in_buf is not None else 0
    out = ctypes.create_string_buffer(out_size)
    returned = wintypes.DWORD(0)
    ok = kernel32.DeviceIoControl(
        handle, code,
        ctypes.byref(in_buf) if in_buf is not None else None, in_size,
        out, out_size, ctypes.byref(returned), None,
    )
    if not ok:
        return False, b"", _err()
    return True, out.raw[: returned.value], None


def _cstr(base: bytes, offset: int) -> str:
    if not offset:
        return ""
    chunk = base[offset:]
    end = chunk.find(b"\x00")
    if end >= 0:
        chunk = chunk[:end]
    return chunk.decode("ascii", "replace").strip()


# --------------------------------------------------------------------------
# 1) 设备标识
# --------------------------------------------------------------------------
def query_device_descriptor(handle):
    q = STORAGE_PROPERTY_QUERY()
    q.PropertyId = STORAGE_DEVICE_PROPERTY
    q.QueryType = PROPERTY_STANDARD_QUERY
    ok, raw, err = ioctl(handle, IOCTL_STORAGE_QUERY_PROPERTY, q, 2048)
    if not ok:
        return None, err
    d = STORAGE_DEVICE_DESCRIPTOR.from_buffer_copy(raw.ljust(ctypes.sizeof(STORAGE_DEVICE_DESCRIPTOR), b"\x00"))
    return {
        "vendor": _cstr(raw, d.VendorIdOffset),
        "product": _cstr(raw, d.ProductIdOffset),
        "revision": _cstr(raw, d.ProductRevisionOffset),
        "serial": _cstr(raw, d.SerialNumberOffset),
        "bus_type": BUS_TYPES.get(d.BusType, f"unknown({d.BusType})"),
        "removable": bool(d.RemovableMedia),
    }, None


def query_device_number(handle):
    try:
        ok, raw, _ = ioctl(handle, IOCTL_STORAGE_GET_DEVICE_NUMBER, None, 16, 0)
        if not ok:
            return None
        n = STORAGE_DEVICE_NUMBER.from_buffer_copy(raw.ljust(12, b"\x00"))
        return {"device_type": n.DeviceType, "device_number": n.DeviceNumber,
                "partition_number": n.PartitionNumber}
    except Exception:
        return None


def map_drive_letters():
    """卷 -> 物理盘号 映射，用于告诉用户 C: 是哪块盘。"""
    mapping = {}
    try:
        mask = kernel32.GetLogicalDrives()
    except Exception:
        return mapping
    for i in range(26):
        if not (mask >> i) & 1:
            continue
        letter = f"{chr(65 + i)}:"
        h = kernel32.CreateFileW(
            f"\\\\.\\{letter}", GENERIC_READ,
            FILE_SHARE_READ | FILE_SHARE_WRITE, None, OPEN_EXISTING, 0, None,
        )
        if h == INVALID_HANDLE_VALUE:
            continue
        try:
            ok, raw, _ = ioctl(h, IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS, None, 1024, 0)
            if ok and len(raw) >= 4:
                vde = VOLUME_DISK_EXTENTS.from_buffer_copy(
                    raw.ljust(ctypes.sizeof(VOLUME_DISK_EXTENTS), b"\x00"))
                disks = sorted({vde.Extents[j].DiskNumber
                                for j in range(min(vde.NumberOfDiskExtents, 8))})
                if disks:
                    try:
                        total = ctypes.c_ulonglong(0)
                        free = ctypes.c_ulonglong(0)
                        kernel32.GetDiskFreeSpaceExW(
                            ctypes.c_wchar_p(letter + "\\"),
                            ctypes.byref(free), ctypes.byref(total), None)
                        mapping[letter] = {
                            "disks": disks,
                            "total_gb": round(total.value / 1024 ** 3, 1),
                            "free_gb": round(free.value / 1024 ** 3, 1),
                        }
                    except Exception:
                        mapping[letter] = {"disks": disks}
        finally:
            kernel32.CloseHandle(h)
    return mapping


# --------------------------------------------------------------------------
# 2) NVMe：Health Information Log (Log Page 0x02)
# --------------------------------------------------------------------------
def read_nvme_health(handle):
    """返回 (data512, strategy_name, err)。"""
    errors = []
    for header_off in (12, 8):  # STORAGE_PROPERTY_QUERY 的 sizeof 在 SDK 中为 12
        for sub in (0, 0xFFFFFFFF):
            total = header_off + ctypes.sizeof(STORAGE_PROTOCOL_SPECIFIC_DATA) + 512
            buf = ctypes.create_string_buffer(total)
            ctypes.memset(buf, 0, total)
            ctypes.c_int.from_buffer(buf, 0).value = STORAGE_DEVICE_PROTOCOL_SPECIFIC_PROPERTY
            ctypes.c_int.from_buffer(buf, 4).value = PROPERTY_STANDARD_QUERY
            psd = STORAGE_PROTOCOL_SPECIFIC_DATA.from_buffer(buf, header_off)
            psd.ProtocolType = PROTOCOL_TYPE_NVME
            psd.DataType = NVME_DATA_TYPE_LOG_PAGE
            psd.ProtocolDataRequestValue = NVME_LOG_PAGE_HEALTH_INFO
            psd.ProtocolDataRequestSubValue = sub
            psd.ProtocolDataOffset = ctypes.sizeof(STORAGE_PROTOCOL_SPECIFIC_DATA)
            psd.ProtocolDataLength = 512
            ok, raw, err = ioctl(handle, IOCTL_STORAGE_QUERY_PROPERTY, buf, total, total)
            tag = f"nvme(logpage0x02,hdr={header_off},sub=0x{sub:x})"
            if ok:
                data = raw[header_off + ctypes.sizeof(STORAGE_PROTOCOL_SPECIFIC_DATA):][:512]
                if len(data) >= 512 and any(data):
                    return data, tag, None
                errors.append(f"{tag}: 返回成功但数据为空")
            else:
                errors.append(f"{tag}: {err}")
    return None, None, " | ".join(errors)


def _u128(b: bytes, off: int) -> int:
    lo = int.from_bytes(b[off:off + 8], "little")
    hi = int.from_bytes(b[off + 8:off + 16], "little")
    return lo | (hi << 64)


def parse_nvme_health(b: bytes) -> dict:
    crit = b[0]
    crit_bits = [n for bit, n in enumerate([
        "可用备用空间低于阈值", "温度超阈值", "NVM 子系统可靠性降级", "只读模式",
        "易失性内存备份失败", "易失性内存未就绪", "控制器内存缓冲区错误", "保留",
    ]) if crit >> bit & 1]
    return {
        "critical_warning": crit,
        "critical_warning_text": crit_bits or ["正常"],
        "temperature_c": round(int.from_bytes(b[1:3], "little") - 273.15, 1),
        "available_spare_pct": b[3],
        "available_spare_threshold_pct": b[4],
        "percentage_used_pct": b[5],
        "data_units_read_gb": round(_u128(b, 32) * 512000 / 1e9, 1),
        "data_units_written_gb": round(_u128(b, 48) * 512000 / 1e9, 1),
        "host_read_commands": _u128(b, 64),
        "host_write_commands": _u128(b, 80),
        "controller_busy_minutes": _u128(b, 96),
        "power_cycles": _u128(b, 112),
        "power_on_hours": _u128(b, 128),
        "unsafe_shutdowns": _u128(b, 144),
        "media_errors": _u128(b, 160),
        "error_log_entries": _u128(b, 176),
        "warning_temp_minutes": int.from_bytes(b[192:196], "little"),
        "critical_temp_minutes": int.from_bytes(b[196:200], "little"),
    }


# --------------------------------------------------------------------------
# 3) ATA / SATA：三条通道
# --------------------------------------------------------------------------
def _smart_read_cmd(sector_number: int) -> list:
    """CurrentTaskFile / IDEREGS 的 8 字节寄存器序列。"""
    return [0xD0, 0x01, sector_number, 0x4F, 0xC2, 0xA0, 0xB0, 0x00]


def read_ata_smart_pass_through(handle, drive_index: int):
    errors = []
    variants = [
        ("apt:flags=DATA_IN,sec=0", ATA_FLAGS_DATA_IN, 0),
        ("apt:flags=DATA_IN|DRDY,sec=0", ATA_FLAGS_DATA_IN | ATA_FLAGS_DRDY_REQUIRED, 0),
        ("apt:flags=DATA_IN,sec=1", ATA_FLAGS_DATA_IN, 1),
        ("apt:flags=DATA_IN|DRDY|DMA,sec=0",
         ATA_FLAGS_DATA_IN | ATA_FLAGS_DRDY_REQUIRED | ATA_FLAGS_USE_DMA, 0),
    ]
    for tag, flags, sec in variants:
        try:
            holder = ATA_PASS_THROUGH_EX_BUF()
            ctypes.memset(ctypes.byref(holder), 0, ctypes.sizeof(holder))
            apt = holder.apt
            apt.Length = ctypes.sizeof(ATA_PASS_THROUGH_EX)
            apt.AtaFlags = flags
            apt.PathId = 0
            apt.TargetId = 0
            apt.Lun = 0
            apt.DataTransferLength = 512
            apt.TimeOutValue = 10
            apt.DataBufferOffset = ctypes.sizeof(ATA_PASS_THROUGH_EX)
            for i, v in enumerate(_smart_read_cmd(sec)):
                apt.CurrentTaskFile[i] = v
            apt.CurrentTaskFile[5] = 0xA0 | ((drive_index & 0x0F) << 4)
            for i, v in enumerate(_smart_read_cmd(sec)):
                apt.PreviousTaskFile[i] = v
            ok, raw, err = ioctl(handle, IOCTL_ATA_PASS_THROUGH, holder,
                                ctypes.sizeof(holder))
            if ok:
                data = bytes(holder.data)
                if any(data):
                    return data, tag, None
                errors.append(f"{tag}: 成功但全零")
            else:
                errors.append(f"{tag}: {err}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{tag}: 异常 {exc}")
    return None, None, " | ".join(errors)


def read_ata_smart_rcv(handle, drive_index: int):
    errors = []
    for sec in (1, 0):
        tag = f"SMART_RCV_DRIVE_DATA:sec={sec}"
        try:
            inp = SENDCMDINPARAMS()
            ctypes.memset(ctypes.byref(inp), 0, ctypes.sizeof(inp))
            inp.cBufferSize = 0
            inp.irDriveRegs.bFeaturesReg = 0xD0
            inp.irDriveRegs.bSectorCountReg = 0x01
            inp.irDriveRegs.bSectorNumberReg = sec
            inp.irDriveRegs.bCylLowReg = 0x4F
            inp.irDriveRegs.bCylHighReg = 0xC2
            inp.irDriveRegs.bDriveHeadReg = 0xA0 | ((drive_index & 0x0F) << 4)
            inp.irDriveRegs.bCommandReg = 0xB0
            inp.bDriveNumber = drive_index
            out = SENDCMDOUTPARAMS()
            ctypes.memset(ctypes.byref(out), 0, ctypes.sizeof(out))
            returned = wintypes.DWORD(0)
            ok = kernel32.DeviceIoControl(
                handle, SMART_RCV_DRIVE_DATA,
                ctypes.byref(inp), ctypes.sizeof(inp),
                ctypes.byref(out), ctypes.sizeof(out),
                ctypes.byref(returned), None,
            )
            if ok:
                data = bytes(out.bBuffer)
                if any(data):
                    return data, tag, None
                errors.append(f"{tag}: 成功但全零 (driver_err={out.DriverStatus.bDriverError})")
            else:
                errors.append(f"{tag}: {_err()}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{tag}: 异常 {exc}")
    return None, None, " | ".join(errors)


def read_ata_smart_scsi(handle, drive_index: int):
    """SCSI ATA PASS-THROUGH (16) —— USB 硬盘盒通常走这条。"""
    tag = "scsi(ata16)"
    try:
        holder = SCSI_PASS_THROUGH_DIRECT_BUF()
        ctypes.memset(ctypes.byref(holder), 0, ctypes.sizeof(holder))
        s = holder.sptd
        s.Length = ctypes.sizeof(SCSI_PASS_THROUGH_DIRECT)
        s.CdbLength = 16
        s.SenseInfoLength = 32
        s.DataIn = 1
        s.DataTransferLength = 512
        s.TimeOutValue = 10
        s.DataBuffer = ctypes.addressof(holder.data)
        s.SenseInfoOffset = ctypes.sizeof(SCSI_PASS_THROUGH_DIRECT)
        cdb = s.Cdb
        cdb[0] = 0x85                       # ATA PASS-THROUGH (16)
        cdb[1] = (3 << 6) | (1 << 2)        # PIO Data-In, T_DIR=1
        cdb[2] = 0x00                       # features[15:8]
        cdb[3] = 0xD0                       # features[7:0] = SMART READ DATA
        cdb[4] = 0x00                       # count[15:8]
        cdb[5] = 0x01                       # count[7:0]
        cdb[6] = 0x00                       # lba[23:16]
        cdb[7] = 0x00                       # lba[7:0]  -> sector number
        cdb[8] = 0x00                       # lba[31:24]
        cdb[9] = 0x4F                       # lba[15:8] -> cylinder low
        cdb[10] = 0x00                      # lba[39:32]
        cdb[11] = 0xC2                      # lba[23:16] -> cylinder high
        cdb[12] = 0xA0 | ((drive_index & 0x0F) << 4)  # device
        cdb[13] = 0xB0                      # command = SMART
        cdb[14] = 0x00
        cdb[15] = 0x00
        ok, raw, err = ioctl(handle, IOCTL_SCSI_PASS_THROUGH_DIRECT, holder,
                            ctypes.sizeof(holder))
        if not ok:
            return None, None, f"{tag}: {err}"
        if holder.sptd.ScsiStatus != 0:
            return None, None, f"{tag}: ScsiStatus=0x{holder.sptd.ScsiStatus:02X}"
        data = bytes(holder.data)
        if not any(data):
            return None, None, f"{tag}: 成功但全零"
        return data, tag, None
    except Exception as exc:  # noqa: BLE001
        return None, None, f"{tag}: 异常 {exc}"


def read_predict_failure(handle):
    try:
        out = STORAGE_PREDICT_FAILURE()
        ctypes.memset(ctypes.byref(out), 0, ctypes.sizeof(out))
        returned = wintypes.DWORD(0)
        ok = kernel32.DeviceIoControl(
            handle, IOCTL_STORAGE_PREDICT_FAILURE, None, 0,
            ctypes.byref(out), ctypes.sizeof(out), ctypes.byref(returned), None,
        )
        if not ok:
            return None, _err()
        return {"predict_failure": out.PredictFailure,
                "has_data": any(out.VendorSpecific),
                "data": bytes(out.VendorSpecific)}, None
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)


# --------------------------------------------------------------------------
# 4) ATA SMART 数据解析
# --------------------------------------------------------------------------
def parse_ata_smart(b: bytes) -> dict:
    revision = int.from_bytes(b[0:2], "little")
    checksum_ok = (sum(b[:511]) & 0xFF) == b[511]
    attrs = []
    for i in range(30):
        off = 2 + i * 12
        aid = b[off]
        if aid == 0:
            continue
        flags = int.from_bytes(b[off + 1:off + 3], "little")
        raw6 = b[off + 5:off + 11]
        attrs.append({
            "id": aid,
            "name": KEY_ATA_ATTRS.get(aid, "Vendor_Specific"),
            "value": b[off + 3],
            "worst": b[off + 4],
            "threshold": None,       # 需另一条 READ THRESHOLDS 命令，P1 再取
            "raw": int.from_bytes(raw6, "little"),
            "raw_first_byte": raw6[0],   # 部分厂商温度/寿命存在首字节
            "flags": flags,
            "prefailure": bool(flags & 0x0001),
            "online": bool(flags & 0x0002),
        })
    return {
        "revision": revision,
        "checksum_ok": checksum_ok,
        "offline_data_collection_status": b[362],
        "self_test_execution_status": b[363],
        "smart_capability": int.from_bytes(b[368:370], "little"),
        "attributes": attrs,
    }


def ata_verdict(parsed: dict) -> list:
    warn = []
    for a in parsed["attributes"]:
        if a["id"] in CRITICAL_ATA_ATTRS and a["raw"] > 0:
            warn.append(f"{a['name']}(ID{a['id']}) 原始值={a['raw']} 不为 0")
        if a["id"] in (5, 197, 198, 187, 196) and a["value"] <= 100 and a["value"] < a["worst"]:
            warn.append(f"{a['name']}(ID{a['id']}) 当前值 {a['value']} 低于历史最差 {a['worst']}")
        if a["id"] in (194, 190) and a["raw_first_byte"] not in (0, 255):
            if a["raw_first_byte"] > 55:
                warn.append(f"温度偏高：{a['raw_first_byte']}°C")
    if not parsed["checksum_ok"]:
        warn.append("SMART 校验和错误（数据可能不可信）")
    return warn


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
def probe_drive(idx: int) -> dict:
    result = {"index": idx, "path": f"\\\\.\\PhysicalDrive{idx}", "channels": {}}
    handle, err = open_drive(idx, write=True)
    if handle is None:
        handle, err2 = open_drive(idx, write=False)
        if handle is None:
            result["open_error"] = err
            result["open_error_ro"] = err2
            result["exists"] = not ("找不到" in err or "cannot find" in err.lower()
                                    or "系统找不到" in err)
            return result
        result["opened_readonly"] = True

    try:
        desc, derr = query_device_descriptor(handle)
        if desc:
            result["device"] = desc
        else:
            result["device_error"] = derr
        num = query_device_number(handle)
        if num:
            result["device_number"] = num

        bus = (result.get("device") or {}).get("bus_type", "")

        # NVMe 通道
        if bus == "NVMe" or not bus:
            data, tag, e = read_nvme_health(handle)
            if data:
                result["channels"]["nvme"] = {"ok": True, "strategy": tag,
                                              "parsed": parse_nvme_health(data)}
            else:
                result["channels"]["nvme"] = {"ok": False, "error": e}

        # ATA 三通道
        for name, fn in (("ata_pass_through", lambda h: read_ata_smart_pass_through(h, idx)),
                         ("smart_rcv_drive_data", lambda h: read_ata_smart_rcv(h, idx)),
                         ("scsi_ata16", lambda h: read_ata_smart_scsi(h, idx))):
            data, tag, e = fn(handle)
            if data:
                parsed = parse_ata_smart(data)
                result["channels"][name] = {
                    "ok": True, "strategy": tag, "parsed": parsed,
                    "warnings": ata_verdict(parsed),
                }
            else:
                result["channels"][name] = {"ok": False, "error": e}

        pf, pferr = read_predict_failure(handle)
        if pf:
            pf.pop("data", None)
            result["predict_failure"] = pf
        else:
            result["predict_failure"] = {"error": pferr}
    finally:
        kernel32.CloseHandle(handle)

    result["readable"] = [k for k, v in result.get("channels", {}).items() if v.get("ok")]
    return result




# --------------------------------------------------------------------------
# 对外聚合接口
# --------------------------------------------------------------------------
def scan(drive_indices=None, max_drives=16):
    """扫描物理磁盘，返回 (results, volumes, elapsed_seconds)。"""
    if drive_indices is None:
        drive_indices = list(range(max_drives))
    t0 = time.time()
    try:
        volumes = map_drive_letters()
    except Exception:
        volumes = {}
    results = []
    for i in drive_indices:
        r = probe_drive(i)
        err = r.get("open_error") or ""
        # "[2]" = ERROR_FILE_NOT_FOUND：该物理磁盘根本不存在，不该出现在界面上
        if not r.get("readable") and err.startswith("[2]"):
            continue
        results.append(r)
    return results, volumes, time.time() - t0