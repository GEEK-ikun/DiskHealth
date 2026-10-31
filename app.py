# -*- coding: utf-8 -*-
"""硬盘健康检测 —— 程序入口。

用法：
    双击「启动硬盘健康检测.bat」  -> 自动提权并打开界面
    python app.py --demo          -> 用演示数据看界面（不需要管理员权限）
    python app.py --no-elevate    -> 禁止自动提权，界面里手动点按钮提权
"""
from __future__ import annotations

import argparse
import ctypes
import os
import sys
import time
import traceback

def _is_frozen() -> bool:
    """是否运行在 PyInstaller 打包出的 exe 里。"""
    return bool(getattr(sys, "frozen", False))


def _app_dir() -> str:
    """程序所在目录。

    打包成 exe 后 __file__ 指向 PyInstaller 的临时解压目录（%TEMP%\\_MEIxxxx），
    那个目录随进程退出就消失，不能用来定位自身或写日志；此时用 exe 自身的位置。
    """
    if _is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


BASE = _app_dir()
if not _is_frozen() and BASE not in sys.path:
    sys.path.insert(0, BASE)


def _log_file() -> str:
    """日志位置。

    源码运行时写在项目目录（和原来一致）；打包后程序目录可能在 Program Files
    这类只读位置，改写到用户数据目录，避免写入失败。
    """
    if not _is_frozen():
        return os.path.join(BASE, "launch.log")
    root = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    d = os.path.join(root, "DiskHealth")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        return os.path.join(BASE, "launch.log")
    return os.path.join(d, "launch.log")


LOG_FILE = _log_file()


def _log(msg: str) -> None:
    """界面用 pythonw 启动时没有控制台，任何错误都看不见，所以全部落盘。

    提权后的实例是**另一个进程**，它也会往同一个日志追加，因此日志里会同时
    出现原始进程和提权进程的记录（用 pid 区分），这正是排查问题的关键。
    """
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] "
                     f"[app pid={os.getpid()} elevated={_is_admin()}] {msg}\n")
    except Exception:
        pass


def _is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _pythonw() -> str:
    """GUI 程序不该带控制台窗口，优先用 pythonw.exe。"""
    exe = sys.executable or "python.exe"
    if exe.lower().endswith("python.exe"):
        cand = exe[: -len("python.exe")] + "pythonw.exe"
        if os.path.exists(cand):
            return cand
    return exe


def try_elevate() -> bool:
    try:
        # 打包成 exe：直接以管理员身份重启 exe 自己，不带脚本参数
        # （__file__ 在打包后指向临时解压目录，父进程退出即失效，不能用）
        if _is_frozen():
            target = sys.executable
            params = " ".join(f'"{a}"' for a in sys.argv[1:])
            workdir = BASE
        else:
            target = _pythonw()
            params = f'"{os.path.abspath(__file__)}"'
            workdir = BASE
        _log(f"请求提权: exe={target!r} params={params!r} cwd={workdir!r}")
        rc = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", target, params, workdir, 1,
        )
        _log(f"ShellExecuteW 返回 {rc} ({'成功' if rc > 32 else '失败/被取消'})")
        return rc > 32
    except Exception:
        _log("提权异常:\n" + traceback.format_exc())
        return False


# --------------------------------------------------------------------------
# 演示数据：让用户在没有管理员权限时也能看到完整界面
# --------------------------------------------------------------------------
def demo_scan():
    from core import smart_engine as e

    def ata_block(entries, good_checksum=True):
        b = bytearray(512)
        b[0:2] = (0x0010).to_bytes(2, "little")
        for i, (aid, val, worst, raw, b0) in enumerate(entries):
            off = 2 + i * 12
            b[off] = aid
            b[off + 1:off + 3] = (0x0033).to_bytes(2, "little")
            b[off + 3] = val
            b[off + 4] = worst
            raw6 = bytearray(int(raw).to_bytes(6, "little"))
            if b0 is not None:
                raw6[0] = b0
            b[off + 5:off + 11] = raw6
        if good_checksum:
            b[511] = sum(b[:511]) & 0xFF
        return bytes(b)

    def nvme_block(temp=41.0, used=7, spare=100, thr=10, hours=1234,
                   media=0, unsafe=2, cw=0):
        b = bytearray(512)
        b[0] = cw
        b[1:3] = int(temp + 273.15).to_bytes(2, "little")
        b[3], b[4], b[5] = spare, thr, used
        b[32:40] = (900_000).to_bytes(8, "little")
        b[48:56] = (500_000).to_bytes(8, "little")
        b[112:120] = (300).to_bytes(8, "little")
        b[128:136] = hours.to_bytes(8, "little")
        b[144:152] = unsafe.to_bytes(8, "little")
        b[160:168] = media.to_bytes(8, "little")
        return bytes(b)

    def nvme_result(idx, vendor, product, serial, block):
        return {
            "index": idx,
            "device": {"vendor": vendor, "product": product, "revision": "1.0",
                       "serial": serial, "bus_type": "NVMe", "removable": False},
            "channels": {"nvme": {"ok": True,
                                  "strategy": "nvme(logpage0x02,hdr=12,sub=0x0)",
                                  "parsed": e.parse_nvme_health(block)}},
            "predict_failure": {"predict_failure": 0, "has_data": True},
            "readable": ["nvme"],
        }

    def ata_result(idx, vendor, product, serial, block, extra_ok=(),
                   predict_fail=False):
        parsed = e.parse_ata_smart(block)
        chans = {"ata_pass_through": {"ok": True,
                                      "strategy": "apt:flags=DATA_IN,sec=0",
                                      "parsed": parsed,
                                      "warnings": e.ata_verdict(parsed)}}
        for name in extra_ok:
            chans[name] = {"ok": True, "strategy": name, "parsed": parsed, "warnings": []}
        return {
            "index": idx,
            "device": {"vendor": vendor, "product": product, "revision": "01.01A01",
                       "serial": serial, "bus_type": "SATA", "removable": False},
            "channels": chans,
            "predict_failure": {"predict_failure": 1 if predict_fail else 0,
                                "has_data": True},
            "readable": ["ata_pass_through"] + list(extra_ok),
        }

    results = [
        # ① 健康 NVMe：优秀
        nvme_result(0, "Samsung", "MZVLB256HAHQ-000L2", "S3K9NX0M123456",
                    nvme_block(temp=41.0, used=7, spare=100, hours=1234,
                               media=0, unsafe=2)),
        # ② 轻度磨损的机械盘：注意
        ata_result(1, "WDC", "WD10EZEX-08WN4A0", "WD-WCC6Y1234567",
                   ata_block([(5, 100, 100, 6, None), (9, 82, 82, 15321, None),
                              (12, 97, 97, 902, None), (187, 100, 100, 2, None),
                              (194, 55, 40, 0, 52), (197, 100, 100, 1, None),
                              (198, 100, 100, 0, None), (199, 200, 200, 27, None)]),
                   extra_ok=("scsi_ata16",)),
        # ③ 濒临报废且固件已预警：危险
        ata_result(2, "Seagate", "ST2000DM008-2FR102", "ZFL0ABCD",
                   ata_block([(5, 42, 42, 148, None), (9, 55, 55, 41022, None),
                              (12, 91, 91, 2210, None), (187, 60, 60, 3, None),
                              (194, 40, 30, 0, 58), (197, 30, 30, 12, None),
                              (198, 30, 30, 4, None), (199, 200, 200, 0, None)]),
                   predict_fail=True),
        {
            "index": 9,
            "open_error": "[2] 系统找不到指定的文件。",
            "open_error_ro": "[2] 系统找不到指定的文件。",
            "channels": {},
            "readable": [],
        },
    ]
    volumes = {
        "C:": {"disks": [0], "total_gb": 228.9, "free_gb": 96.4},
        "D:": {"disks": [1], "total_gb": 931.5, "free_gb": 210.7},
        "E:": {"disks": [2], "total_gb": 1863.0, "free_gb": 88.2},
    }
    # 与 engine.scan() 保持一致：不存在的物理磁盘不进界面
    results = [r for r in results
               if r.get("readable") or not (r.get("open_error") or "").startswith("[2]")]
    return results, volumes, 0.42


# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-elevate", action="store_true", help="禁止自动提权")
    ap.add_argument("--demo", action="store_true", help="使用演示数据（无需管理员）")
    ap.add_argument("--selftest", action="store_true", help="构建界面后立即退出")
    args = ap.parse_args()

    _log(f"app 启动 argv={sys.argv[1:]!r} pythonw={_pythonw()!r}")

    from core import smart_engine

    admin = smart_engine.is_admin()
    _log(f"管理员权限 = {admin}")

    if not admin and not args.demo and not args.selftest and not args.no_elevate:
        if try_elevate():
            _log("提权成功，本进程退出，交给提权实例显示界面")
            return 0
        _log("提权失败或被取消，将以普通权限继续（界面会显示警告条）")

    import tkinter as tk
    from ui import app_ui

    _log("正在创建 Tk 主窗口……")
    root = tk.Tk()
    app_ui.apply_style(root)

    def scan():
        return demo_scan() if args.demo else smart_engine.scan()

    def elevate():
        if try_elevate():
            root.destroy()

    win = app_ui.MainWindow(root, scan_func=scan, is_admin=admin,
                            on_elevate=elevate, demo=args.demo)
    _log("主窗口已创建，进入事件循环")

    if args.selftest:
        root.withdraw()
        win.refresh()
        root.update()
        root.update_idletasks()
        win.open_detail(win.infos[0])
        root.update()
        for child in root.winfo_children():
            if isinstance(child, tk.Toplevel):
                child.destroy()
        cards = sum(1 for w in win.inner.winfo_children()
                    if isinstance(w, app_ui.DiskCard))
        print(f"selftest OK: 磁盘卡片 {cards} 张, 状态栏 = {win.status.cget('text')}")
        print("健康结论: " + " | ".join(
            f"#{i['index']} {i['model'][:22]} 分数={i['score']} 等级={i['level']}"
            for i in win.infos))
        root.destroy()
        return 0

    root.mainloop()
    _log("事件循环结束，正常退出")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        err = traceback.format_exc()
        _log("app 未捕获异常:\n" + err)
        try:
            import tkinter.messagebox as mb
            mb.showerror("硬盘健康检测 启动失败", err[-1500:])
        except Exception:
            pass
        sys.exit(1)
