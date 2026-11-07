# -*- coding: utf-8 -*-
"""In-app audio library.

Explorer cannot always be started from an unsigned/low-integrity process
(0xc0000142 / WinError 5), so the primary way to reach generated WAVs is this
small Tk browser.  System Explorer is only an optional button.
"""
import os
import threading
import time
import tkinter as tk
import wave
from tkinter import ttk

from .engine import play_wav
from .widgets import C, PillButton, _font


class AudioLibrary(tk.Toplevel):
    def __init__(self, parent, folder, on_play=None, on_open=None, on_close=None):
        super().__init__(parent)
        self.folder = folder
        self._on_play = on_play
        self._on_open = on_open
        self._on_close = on_close
        self._paths = {}
        self.title("RemVoice · 音频库")
        self.configure(bg=C["bg"])
        self.transient(parent)
        self.minsize(640, 420)
        self._build()
        self._center(parent)
        self.refresh()
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.bind("<Escape>", lambda _e: self._close())

    def _build(self):
        head = tk.Frame(self, bg=C["surface"])
        head.pack(fill="x", padx=18, pady=(16, 10))
        text = tk.Frame(head, bg=C["surface"])
        text.pack(side="left", fill="x", expand=True)
        tk.Label(
            text,
            text="音频库",
            bg=C["surface"],
            fg=C["ink"],
            font=_font(16, bold=True),
        ).pack(anchor="w")
        tk.Label(
            text,
            text=self.folder,
            bg=C["surface"],
            fg=C["muted"],
            font=_font(8),
        ).pack(anchor="w", pady=(2, 0))
        PillButton(
            head,
            text="刷新",
            command=self.refresh,
            kind="secondary",
            width=76,
            height=34,
            font_size=8,
            bold=False,
        ).pack(side="right", padx=(6, 0))
        PillButton(
            head,
            text="资源管理器",
            command=self._open_explorer,
            kind="ghost",
            width=110,
            height=34,
            font_size=8,
            bold=False,
        ).pack(side="right", padx=(6, 0))

        body = tk.Frame(self, bg=C["surface"])
        body.pack(fill="both", expand=True, padx=18, pady=(0, 8))
        columns = ("name", "duration", "modified", "size")
        self.tree = ttk.Treeview(
            body,
            columns=columns,
            show="headings",
            selectmode="browse",
        )
        self.tree.heading("name", text="文件")
        self.tree.heading("duration", text="时长")
        self.tree.heading("modified", text="修改时间")
        self.tree.heading("size", text="大小")
        self.tree.column("name", width=420, minwidth=240, anchor="w")
        self.tree.column("duration", width=72, anchor="center", stretch=False)
        self.tree.column("modified", width=150, anchor="center", stretch=False)
        self.tree.column("size", width=90, anchor="e", stretch=False)
        scroll = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", lambda _e: self._play_selected())
        self.tree.bind("<Return>", lambda _e: self._play_selected())

        footer = tk.Frame(self, bg=C["surface"])
        footer.pack(fill="x", padx=18, pady=(0, 16))
        self.status_var = tk.StringVar(value="")
        tk.Label(
            footer,
            textvariable=self.status_var,
            bg=C["surface"],
            fg=C["muted"],
            font=_font(8),
            anchor="w",
        ).pack(side="left", fill="x", expand=True)
        PillButton(
            footer,
            text="复制路径",
            command=self._copy_selected,
            kind="ghost",
            width=96,
            height=34,
            font_size=8,
            bold=False,
        ).pack(side="right", padx=(6, 0))
        PillButton(
            footer,
            text="播放选中",
            command=self._play_selected,
            kind="primary",
            width=108,
            height=34,
            font_size=8,
        ).pack(side="right", padx=(6, 0))

    def _center(self, parent):
        self.update_idletasks()
        width, height = 820, 560
        x = parent.winfo_rootx() + max(20, (parent.winfo_width() - width) // 2)
        y = parent.winfo_rooty() + max(20, (parent.winfo_height() - height) // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")

    def refresh(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        self._paths = {}
        entries = []
        try:
            with os.scandir(self.folder) as scan:
                for entry in scan:
                    if entry.is_file() and entry.name.lower().endswith(".wav"):
                        entries.append(entry)
        except OSError as exc:
            self.status_var.set(f"无法读取文件夹：{exc}")
            return
        entries.sort(key=lambda e: e.stat().st_mtime, reverse=True)
        for entry in entries:
            duration = ""
            size = ""
            try:
                with wave.open(entry.path, "rb") as w:
                    rate = w.getframerate() or 1
                    duration = f"{w.getnframes() / rate:.2f}s"
            except (OSError, EOFError, wave.Error):
                duration = "-"
            try:
                size = f"{entry.stat().st_size / 1024:.0f} KB"
            except OSError:
                pass
            stamp = time.strftime("%m-%d %H:%M", time.localtime(entry.stat().st_mtime))
            item = self.tree.insert(
                "", "end", values=(entry.name, duration, stamp, size)
            )
            self._paths[item] = entry.path
        self.status_var.set(f"{len(entries)} 个 WAV · 双击或选中后播放")

    def _selected_path(self):
        selection = self.tree.selection()
        if not selection:
            self.status_var.set("先选中一个文件")
            return ""
        return self._paths.get(selection[0], "")

    def _play_selected(self):
        path = self._selected_path()
        if not path:
            return
        self.status_var.set("正在播放：" + os.path.basename(path))
        if self._on_play:
            self._on_play(path)
        else:
            threading.Thread(target=play_wav, args=(path,), daemon=True).start()

    def _copy_selected(self):
        path = self._selected_path()
        if not path:
            return
        self.clipboard_clear()
        self.clipboard_append(path)
        self.status_var.set("路径已复制")

    def _open_explorer(self):
        if self._on_open:
            try:
                self._on_open(self.folder)
                self.status_var.set("已请求资源管理器")
            except OSError as exc:
                self.status_var.set(f"资源管理器无法打开：{exc}")
        else:
            self.status_var.set("资源管理器不可用")

    def _close(self):
        if self._on_close:
            try:
                self._on_close()
            except Exception:
                pass
        self.destroy()
