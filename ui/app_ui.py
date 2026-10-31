# -*- coding: utf-8 -*-
"""硬盘健康检测 —— tkinter 图形界面。

零第三方依赖，只用 Python 自带的 tkinter，双击即用。
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

# --------------------------------------------------------------------------
# 主题
# --------------------------------------------------------------------------
BG = "#0f1115"
CARD = "#181b22"
CARD_HI = "#20242e"
HEAD = "#151821"
BORDER = "#272c38"
TEXT = "#e6e9ef"
MUTED = "#8b93a5"
ACCENT = "#4aa8ff"
SEV_COLORS = {
    "ok": "#3ddc84",
    "info": "#8b93a5",
    "warn": "#ffb020",
    "crit": "#ff4d4f",
}
SEV_DOT = {"ok": "●", "info": "●", "warn": "▲", "crit": "■"}

FONT = "Microsoft YaHei UI"
F_TITLE = (FONT, 17, "bold")
F_SUB = (FONT, 10)
F_CARD = (FONT, 12, "bold")
F_BODY = (FONT, 10)
F_SMALL = (FONT, 9)
F_BIG = (FONT, 26, "bold")


def apply_style(root: tk.Tk):
    root.configure(bg=BG)
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    style.configure("Dark.Treeview", background=CARD, fieldbackground=CARD,
                    foreground=TEXT, rowheight=26, borderwidth=0, font=F_BODY)
    style.configure("Dark.Treeview.Heading", background=CARD_HI, foreground=MUTED,
                    borderwidth=0, font=F_SMALL, relief="flat")
    style.map("Dark.Treeview",
              background=[("selected", ACCENT)],
              foreground=[("selected", "#0f1115")])
    style.map("Dark.Treeview.Heading", background=[("active", CARD_HI)])
    style.configure("Dark.Vertical.TScrollbar", background=CARD_HI, troughcolor=BG,
                    bordercolor=BG, arrowcolor=MUTED, borderwidth=0)


def flat_button(parent, text, command, primary=False, width=None):
    bg = ACCENT if primary else CARD_HI
    fg = "#0f1115" if primary else TEXT
    btn = tk.Label(parent, text=text, bg=bg, fg=fg, font=F_BODY, padx=14, pady=6,
                   cursor="hand2")
    if width:
        btn.configure(width=width)
    btn.bind("<Button-1>", lambda _e: command())
    btn.bind("<Enter>", lambda _e: btn.configure(bg=ACCENT if not primary else "#6cbcff"))
    btn.bind("<Leave>", lambda _e: btn.configure(bg=bg))
    return btn


# --------------------------------------------------------------------------
# 环形仪表盘
# --------------------------------------------------------------------------
class RingGauge(tk.Canvas):
    SIZE = 118

    def __init__(self, parent, score, color):
        super().__init__(parent, width=self.SIZE, height=self.SIZE,
                         bg=CARD, highlightthickness=0)
        s, pad = self.SIZE, 11
        box = (pad, pad, s - pad, s - pad)
        self.create_arc(*box, start=225, extent=-270, style="arc",
                        outline=BORDER, width=9)
        if score is None:
            txt, sub = "—", ""
        else:
            txt, sub = str(score), ""
            self.create_arc(*box, start=225, extent=-270 * max(0, min(100, score)) / 100,
                            style="arc", outline=color, width=9)
        self.create_text(s / 2, s / 2 - (6 if sub else 0), text=txt, fill=color,
                         font=F_BIG)
        if sub:
            self.create_text(s / 2, s / 2 + 20, text=sub, fill=MUTED, font=F_SMALL)


# --------------------------------------------------------------------------
# 磁盘卡片
# --------------------------------------------------------------------------
class DiskCard(tk.Frame):
    def __init__(self, parent, info, volumes, on_detail):
        super().__init__(parent, bg=CARD, highlightthickness=1,
                         highlightbackground=BORDER)
        self.info = info

        top = tk.Frame(self, bg=CARD)
        top.pack(fill="x", padx=16, pady=(14, 0))

        RingGauge(top, info["score"], info["color"]).pack(side="left")

        right = tk.Frame(top, bg=CARD)
        right.pack(side="left", fill="both", expand=True, padx=(14, 0))

        tk.Label(right, text=info["model"], bg=CARD, fg=TEXT, font=F_CARD,
                 anchor="w", wraplength=330, justify="left").pack(fill="x")

        badges = tk.Frame(right, bg=CARD)
        badges.pack(fill="x", pady=(6, 0))
        self._badge(badges, info["bus"], ACCENT)
        self._badge(badges, info["level"], info["color"])
        if info.get("predict_failure") == 1:
            self._badge(badges, "固件预警", SEV_COLORS["crit"])
        disks = [v["disks"] for v in volumes.values() if info["index"] in v.get("disks", [])]
        letters = [v for v, i in volumes.items() if info["index"] in i.get("disks", [])]
        if letters:
            self._badge(badges, " ".join(sorted(letters)), MUTED)

        if info["serial"]:
            tk.Label(right, text="序列号 " + info["serial"], bg=CARD, fg=MUTED,
                     font=F_SMALL, anchor="w").pack(fill="x", pady=(6, 0))

        # 关键指标 3x2
        grid = tk.Frame(self, bg=CARD)
        grid.pack(fill="x", padx=16, pady=(12, 0))
        for i, (label, value, unit) in enumerate(info["metrics"][:6]):
            cell = tk.Frame(grid, bg=CARD)
            cell.grid(row=i // 3, column=i % 3, sticky="w", padx=(0, 22), pady=3)
            tk.Label(cell, text=label, bg=CARD, fg=MUTED, font=F_SMALL,
                     anchor="w").pack(anchor="w")
            shown = value if value not in (None, "") else "—"
            tk.Label(cell, text=f"{shown} {unit}".strip(), bg=CARD, fg=TEXT,
                     font=F_BODY, anchor="w").pack(anchor="w")

        # 结论摘要
        findings = info["findings"]
        if findings:
            worst = min(findings, key=lambda f: ("ok", "info", "warn", "crit").index(f["sev"]))
            sev = "crit" if info["score"] is not None and info["score"] < 50 else worst["sev"]
            line = tk.Frame(self, bg=CARD)
            line.pack(fill="x", padx=16, pady=(12, 0))
            tk.Label(line, text=SEV_DOT[worst["sev"]], bg=CARD,
                     fg=SEV_COLORS[worst["sev"]], font=F_SMALL).pack(side="left")
            tk.Label(line, text=worst["text"], bg=CARD, fg=SEV_COLORS[worst["sev"]],
                     font=F_SMALL, anchor="w", wraplength=430,
                     justify="left").pack(side="left", padx=(6, 0))

        bar = tk.Frame(self, bg=CARD)
        bar.pack(fill="x", padx=16, pady=(12, 14))
        flat_button(bar, "查看详细数据", lambda: on_detail(info)).pack(side="left")
        readable = info.get("readable") or []
        tk.Label(bar, text="通道: " + (", ".join(readable) if readable else "无"),
                 bg=CARD, fg=MUTED, font=F_SMALL).pack(side="right")

    def _badge(self, parent, text, color):
        tk.Label(parent, text=" " + text + " ", bg=CARD_HI, fg=color,
                 font=F_SMALL, padx=4, pady=1).pack(side="left", padx=(0, 6))


# --------------------------------------------------------------------------
# 详情窗口
# --------------------------------------------------------------------------
class DetailWindow(tk.Toplevel):
    def __init__(self, parent, info, volumes):
        super().__init__(parent, bg=BG)
        self.title(f"详细数据 — {info['model']}")
        self.geometry("880x660")
        self.minsize(720, 520)

        head = tk.Frame(self, bg=HEAD)
        head.pack(fill="x")
        left = tk.Frame(head, bg=HEAD)
        left.pack(side="left", padx=18, pady=(14, 12))
        tk.Label(left, text=info["model"], bg=HEAD, fg=TEXT, font=F_TITLE,
                 anchor="w").pack(anchor="w")
        sub = " | ".join(x for x in [
            info.get("bus", ""),
            ("序列号 " + info["serial"]) if info.get("serial") else "",
            " ".join(sorted(v for v, i in volumes.items()
                            if info["index"] in i.get("disks", []))),
        ] if x)
        tk.Label(left, text=sub, bg=HEAD, fg=MUTED, font=F_SMALL,
                 anchor="w").pack(anchor="w", pady=(3, 0))

        right = tk.Frame(head, bg=HEAD)
        right.pack(side="right", padx=18)
        score_text = info["score"] if info["score"] is not None else "—"
        tk.Label(right, text=str(score_text), bg=HEAD, fg=info["color"],
                 font=(FONT, 30, "bold")).pack(side="left")
        meta = tk.Frame(right, bg=HEAD)
        meta.pack(side="left", padx=(10, 0))
        tk.Label(meta, text=info["level"], bg=HEAD, fg=info["color"],
                 font=F_CARD, anchor="w").pack(anchor="w")
        tk.Label(meta, text="健康分", bg=HEAD, fg=MUTED, font=F_SMALL,
                 anchor="w").pack(anchor="w")

        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=18, pady=14)

        tk.Label(body, text="健康结论", bg=BG, fg=TEXT, font=F_CARD,
                 anchor="w").pack(fill="x")
        box = tk.Frame(body, bg=CARD, highlightthickness=1, highlightbackground=BORDER)
        box.pack(fill="x", pady=(8, 16))
        for f in info["findings"]:
            row = tk.Frame(box, bg=CARD)
            row.pack(fill="x", padx=14, pady=4)
            tk.Label(row, text=SEV_DOT[f["sev"]], bg=CARD, fg=SEV_COLORS[f["sev"]],
                     font=F_SMALL).pack(side="left")
            tk.Label(row, text=f["text"], bg=CARD, fg=SEV_COLORS[f["sev"]],
                     font=F_BODY, anchor="w", wraplength=760,
                     justify="left").pack(side="left", padx=(8, 0))
        if not info["findings"]:
            tk.Label(box, text="无", bg=CARD, fg=MUTED, font=F_BODY).pack(padx=14, pady=8)

        label = "S.M.A.R.T. 属性表" if info["kind"] == "ata" else "NVMe 健康日志"
        tk.Label(body, text=label, bg=BG, fg=TEXT, font=F_CARD,
                 anchor="w").pack(fill="x")

        wrap = tk.Frame(body, bg=BG)
        wrap.pack(fill="both", expand=True, pady=(8, 0))
        cols = ("id", "name", "value", "worst", "raw")
        tree = ttk.Treeview(wrap, columns=cols, show="headings",
                            style="Dark.Treeview")
        for c, t, w, a in (("id", "ID", 60, "center"), ("name", "属性名称", 260, "w"),
                           ("value", "当前值", 90, "center"),
                           ("worst", "最差值", 90, "center"),
                           ("raw", "原始值", 220, "w")):
            tree.heading(c, text=t)
            tree.column(c, width=w, anchor=a)
        vs = ttk.Scrollbar(wrap, orient="vertical", command=tree.yview,
                           style="Dark.Vertical.TScrollbar")
        tree.configure(yscrollcommand=vs.set)
        tree.pack(side="left", fill="both", expand=True)
        vs.pack(side="right", fill="y")

        for a in info["attributes"]:
            tree.insert("", "end", values=(a.get("id", ""), a.get("name", ""),
                                           a.get("value", ""), a.get("worst", ""),
                                           a.get("raw", "")))

        self.transient(parent)
        self.focus_set()


# --------------------------------------------------------------------------
# 主窗口
# --------------------------------------------------------------------------
class MainWindow:
    def __init__(self, root: tk.Tk, scan_func, is_admin: bool, on_elevate=None,
                 demo: bool = False):
        self.root = root
        self.scan_func = scan_func
        self.is_admin = is_admin
        self.on_elevate = on_elevate
        self.demo = demo
        self.infos = []
        self.volumes = {}

        root.title("硬盘健康检测（演示数据）" if demo else "硬盘健康检测")
        root.geometry("760x780")
        root.minsize(660, 520)

        self._build_header()
        if demo:
            self._build_demo_bar()
        elif not is_admin:
            self._build_warning()
        self._build_scroll()
        self._build_status()

        self.root.after(120, self.refresh)

    # ---------------- 布局 ----------------
    def _build_header(self):
        head = tk.Frame(self.root, bg=HEAD)
        head.pack(fill="x")
        left = tk.Frame(head, bg=HEAD)
        left.pack(side="left", padx=20, pady=14)
        tk.Label(left, text="硬盘健康检测", bg=HEAD, fg=TEXT, font=F_TITLE,
                 anchor="w").pack(anchor="w")
        tk.Label(left, text="基于 S.M.A.R.T. 的固态/机械硬盘状态诊断",
                 bg=HEAD, fg=MUTED, font=F_SMALL, anchor="w").pack(anchor="w")

        right = tk.Frame(head, bg=HEAD)
        right.pack(side="right", padx=20)
        self.btn_refresh = flat_button(right, "重新扫描", self.refresh, primary=True)
        self.btn_refresh.pack(side="right")

    def _build_demo_bar(self):
        """演示模式的提示条。

        演示数据是编出来的，本来就不需要管理员权限。如果沿用「没有管理员
        权限」那条警告，看截图的人会以为软件坏了，而不是以为这是演示。
        """
        bar = tk.Frame(self.root, bg="#10263a", highlightthickness=0)
        bar.pack(fill="x")
        tk.Label(bar, text="ℹ", bg="#10263a", fg="#4ea1ff",
                 font=(FONT, 13, "bold")).pack(side="left", padx=(18, 8), pady=10)
        tk.Label(bar, text="演示模式：以下为虚构数据，不读取真实硬盘，无需管理员权限。",
                 bg="#10263a", fg="#bcd9ff", font=F_BODY,
                 anchor="w").pack(side="left", pady=10)

    def _build_warning(self):
        bar = tk.Frame(self.root, bg="#3a2a10", highlightthickness=0)
        bar.pack(fill="x")
        tk.Label(bar, text="⚠", bg="#3a2a10", fg="#ffb020",
                 font=(FONT, 13, "bold")).pack(side="left", padx=(18, 8), pady=10)
        tk.Label(bar, text="当前不是管理员权限，无法读取 S.M.A.R.T. 数据。",
                 bg="#3a2a10", fg="#ffd591", font=F_BODY,
                 anchor="w").pack(side="left", pady=10)
        if self.on_elevate:
            flat_button(bar, "以管理员身份重启", self.on_elevate).pack(
                side="right", padx=18)

    def _build_scroll(self):
        outer = tk.Frame(self.root, bg=BG)
        outer.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(outer, bg=BG, highlightthickness=0)
        vs = ttk.Scrollbar(outer, orient="vertical", command=self.canvas.yview,
                           style="Dark.Vertical.TScrollbar")
        self.canvas.configure(yscrollcommand=vs.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        vs.pack(side="right", fill="y")

        self.inner = tk.Frame(self.canvas, bg=BG)
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda _e: self.canvas.configure(
            scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(
            self._win, width=e.width))
        for widget in (self.canvas, self.inner):
            widget.bind("<MouseWheel>", self._wheel)

    def _wheel(self, event):
        self.canvas.yview_scroll(int(-event.delta / 120), "units")

    def _build_status(self):
        bar = tk.Frame(self.root, bg=HEAD)
        bar.pack(fill="x", side="bottom")
        self.status = tk.Label(bar, text="就绪", bg=HEAD, fg=MUTED, font=F_SMALL,
                               anchor="w")
        self.status.pack(side="left", padx=18, pady=8)
        if self.demo:
            perm, color = "演示模式", MUTED
        elif self.is_admin:
            perm, color = "管理员权限", SEV_COLORS["ok"]
        else:
            perm, color = "普通权限", SEV_COLORS["warn"]
        tk.Label(bar, text=perm, bg=HEAD, fg=color,
                 font=F_SMALL).pack(side="right", padx=18)

    # ---------------- 行为 ----------------
    def refresh(self):
        self.status.configure(text="正在读取 S.M.A.R.T. 数据……")
        self.root.update_idletasks()
        try:
            results, volumes, elapsed = self.scan_func()
        except Exception as exc:  # noqa: BLE001
            self.status.configure(text=f"扫描失败：{exc}")
            return

        from core import health
        self.infos = health.evaluate_all(results)
        self.volumes = volumes
        self._render(elapsed)

    def _render(self, elapsed):
        for w in self.inner.winfo_children():
            w.destroy()

        found = [i for i in self.infos if i["score"] is not None]
        if not self.infos:
            tk.Label(self.inner, text="未检测到任何物理磁盘", bg=BG, fg=MUTED,
                     font=F_BODY).pack(pady=40)
        for n, info in enumerate(self.infos):
            card = DiskCard(self.inner, info, self.volumes, self.open_detail)
            card.pack(fill="x", padx=18, pady=(16 if n == 0 else 12, 0))
        tk.Frame(self.inner, bg=BG, height=16).pack(fill="x")

        worst = min((i["score"] for i in found), default=None)
        if worst is None:
            summary = "未能读取到任何 S.M.A.R.T. 数据"
        elif worst >= 90:
            summary = f"{len(found)} 块磁盘状态优秀"
        elif worst >= 75:
            summary = f"{len(found)} 块磁盘状态良好"
        elif worst >= 50:
            summary = f"有磁盘需要注意"
        else:
            summary = f"有磁盘存在严重风险"
        self.status.configure(
            text=f"{summary} · 共发现 {len(self.infos)} 块物理磁盘 · "
                 f"耗时 {elapsed:.2f} 秒")

    def open_detail(self, info):
        DetailWindow(self.root, info, self.volumes)
