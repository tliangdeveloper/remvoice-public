# -*- coding: utf-8 -*-
"""RemVoice desktop client.

This file owns the Tk window and page layout only.  Text handling, storage,
the local TTS service, the LLM client and chat state live in ``remvoice``.
"""
import array
import base64
import ctypes
import ctypes.wintypes
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import traceback
import urllib.error
import urllib.parse
import urllib.request
import wave
from tkinter import messagebox, simpledialog, ttk

from remvoice import __version__
from remvoice.audio_library import AudioLibrary
from remvoice.chat import ChatService, ChatSession
from remvoice.engine import (
    apply_weights,
    ensure_service,
    kill_port_owner,
    load_tones,
    play_wav,
    port_open,
    save_audio,
    stop_playback,
    stop_service,
    synth,
    warmup_tts,
)
from remvoice.llm import (
    ModelsError,
    fetch_models,
    is_local_endpoint,
    llm_chat,
    translate_to_japanese,
)
from remvoice.asr import AsrService
from remvoice.memory import CLARITY_LABEL, MemoryKeeper, MemoryStore
from remvoice.player import AudioPlayer, StreamingPlayer
from remvoice.persona import (
    DEFAULT_CHAT_CONFIG,
    OLD_PERSONA_PREFIX,
    OUTPUT_FORMAT,
    PERSONA,
    load_persona_from_card,
)
from remvoice.settings import (
    APP_DIR,
    APP_LOG,
    ASR_BEAM,
    ASR_COMPUTE,
    ASR_DEVICE,
    ASR_ENGINE,
    ASR_LANGUAGE,
    ASR_MODEL,
    ASR_MODEL_DIR,
    ASR_PRELOAD,
    ASR_STDERR,
    ASR_WORKER,
    AVATAR_ASSET,
    CHAT_ARCHIVE,
    CHAT_CONFIG,
    CHAT_RECORD_DIR,
    KEEP_ENGINE_RUNNING,
    MAX_HISTORY_TURNS,
    PERSONA_CARD,
    PORTRAIT_ASSET,
    PYTHON,
    SAVE_DIR,
    SPEED,
    STREAM_TTS_MODE,
    TOP_K,
    WARMUP,
)
from remvoice.storage import (
    append_chat_archive,
    append_chat_transcript,
    append_log,
    load_chat_config,
    load_chat_history,
    read_chat_archive,
    save_chat_config,
)
from remvoice.text import apply_names, clean_reply, parse_reply, smart_punct
from remvoice.theme import (
    APP_ICON_B64,
    C,
    FONT_DISPLAY,
    FONT_UI,
    _ellipsize,
    _font,
    _mix,
    _round_rect,
    install_rounded_corners,
)
from remvoice.widgets import (
    Badge,
    Card,
    EngineCard,
    NavItem,
    PillButton,
    PortraitCard,
    QuoteCard,
    RecentRow,
    ScrollArea,
    Switch,
    ToneMenu,
    VoiceCard,
)
from remvoice.chat_ui import ChatView

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("RemVoice · 助手语音")
        self.configure(bg=C["bg"])
        self.minsize(1020, 680)
        self._place_main_window(1160, 750)
        self._apply_window_icon()

        self._spawned_pid = None
        self._service_ready = False
        self._engine_state = "loading"
        self._engine_text = "正在唤醒引擎"
        self._gen_fraction = 0.0
        self.active_page = "synth"
        self.tones, loaded_default = load_tones()
        # Both languages now use the "greeting" reference.  Keep the other
        # references on disk for rollback, but do not expose them in the UI.
        self.default_tone = "greeting" if "greeting" in self.tones else loaded_default
        self._tone_label = (
            "日常轻语" if self.default_tone == "greeting" else "日常"
        )
        if self.default_tone in self.tones:
            self.tones[self.default_tone]["label"] = self._tone_label
        self.worker = None
        self.chat_worker = None
        self.chat_stop = threading.Event()
        self._chat_epoch = 0
        self.audio_player = None
        self.chat_audio = {}
        self.last_reply_audio = None
        self.pending_msg = None
        self.asr = None
        self._voice_active = False
        self._voice_busy = False
        self._voice_timer = None
        self.chat_cfg = load_chat_config()
        self.auto_import_persona()
        self.ensure_output_format()
        self.memory_store = MemoryStore(cfg=self.chat_cfg)
        self.memory_keeper = MemoryKeeper(
            self.memory_store,
            self.chat_cfg,
            log=lambda message: append_log(APP_LOG, "记忆 · " + str(message)),
            keep_messages=MAX_HISTORY_TURNS * 2,
        )
        self.chat_session = ChatSession(
            self.chat_cfg, load_chat_history(), memory=self.memory_store
        )
        self.chat_service = ChatService(
            self.chat_cfg,
            self.tones,
            self.default_tone,
            speed=SPEED,
            memory=self.memory_keeper,
        )
        self.recent_paths = []
        self._recent_rows = []
        self._synth_placeholder = False
        self._chat_placeholder = False
        self._chat_tone_key = self.default_tone
        self._brand_font = tkfont.Font(family=FONT_DISPLAY, size=22, weight="bold")
        self._pill_font = tkfont.Font(family=FONT_UI, size=8, weight="bold")
        self._avatar_full = None
        self._avatar_img = None
        self._portrait_img = None
        try:
            self._avatar_full = tk.PhotoImage(file=AVATAR_ASSET)
            self._avatar_img = self._avatar_full.subsample(2)
        except tk.TclError:
            pass
        try:
            self._portrait_img = tk.PhotoImage(file=PORTRAIT_ASSET)
        except tk.TclError:
            pass

        self.status = tk.StringVar(value="正在加载模型…")
        self._audio_library = None
        self.chat_model_var = tk.StringVar()
        self.chat_meta_var = tk.StringVar()
        self.chat_activity_var = tk.StringVar(value="正在准备本地引擎…")
        self._voice_langs = {"zh": {"label": "中文"}, "ja": {"label": "日本語"}}
        lang = str(self.chat_cfg.get("voice_lang", "zh")).lower()
        if lang not in self._voice_langs:
            lang = "zh"
        self.voice_lang_var = tk.StringVar(value=lang)
        self.voice_lang_var.trace_add("write", self._on_voice_lang_changed)

        self._init_styles()
        self._build_shell()
        self._build_synth_page()
        self._build_chat_page()
        self._load_recent()
        self.render_history()
        self._show_page("synth")
        self.after(120, self._raise_active_page)
        self._refresh_chat_labels()
        self._set_generation_status(
            "正在唤醒引擎", "本地服务启动后，就可以开始合成了。", 0.0, "loading"
        )

        self.play_btn.set_state("disabled")
        self.send_btn.set_state("disabled")
        self.stop_btn.set_state("disabled")
        self.bind_all("<MouseWheel>", self._route_wheel, add="+")
        self.bind_all("<KeyPress-F2>", self._voice_hotkey_start, add="+")
        self.bind_all("<KeyRelease-F2>", self._voice_hotkey_stop, add="+")
        self.bind("<Control-Key-1>", lambda _e: self._show_page("synth"))
        self.bind("<Control-Key-2>", lambda _e: self._show_page("chat"))
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        # Start after the Tk event loop is alive: worker callbacks use after().
        self.after(
            60,
            lambda: threading.Thread(target=self.init_service, daemon=True).start(),
        )
        if ASR_PRELOAD:
            self.after(1500, self._warm_asr)

    # ------------------------------------------------------------------
    # Window, fonts and styles
    # ------------------------------------------------------------------

    def _place_main_window(self, width, height):
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        work_left, work_top = 0, 0
        work_right, work_bottom = screen_w, screen_h
        try:
            rect = ctypes.wintypes.RECT()
            if ctypes.windll.user32.SystemParametersInfoW(
                0x0030, 0, ctypes.byref(rect), 0
            ):
                work_left, work_top = rect.left, rect.top
                work_right, work_bottom = rect.right, rect.bottom
        except Exception:
            pass
        work_w = max(800, work_right - work_left)
        work_h = max(600, work_bottom - work_top)
        width = min(width, work_w - 28)
        # The Tk size is the client area; leave room for the native title bar.
        height = min(height, max(660, work_h - 58))
        x = work_left + max(0, (work_w - width) // 2)
        y = work_top + max(6, (work_h - height - 40) // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")

    def _apply_window_icon(self):
        if not APP_ICON_B64:
            return
        try:
            self._icon_photo = tk.PhotoImage(data=base64.b64decode(APP_ICON_B64))
            self.iconphoto(True, self._icon_photo)
        except Exception:
            pass

    def _init_styles(self):
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
            try:
                tkfont.nametofont(name).configure(family=FONT_UI, size=10)
            except tk.TclError:
                pass
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "Soft.Vertical.TScrollbar",
            gripcount=0,
            background="#C7D9E0",
            darkcolor="#C7D9E0",
            lightcolor="#C7D9E0",
            troughcolor=C["surface_soft"],
            bordercolor=C["surface_soft"],
            arrowcolor=C["muted"],
            arrowsize=10,
            width=7,
            relief="flat",
        )
        style.map(
            "Soft.Vertical.TScrollbar",
            background=[("active", "#A9C6D2"), ("pressed", "#91B5C4")],
        )
        style.configure(
            "TCombobox",
            fieldbackground=C["surface_soft"],
            background=C["surface_soft"],
            foreground=C["ink"],
            arrowcolor=C["blue_dark"],
            bordercolor=C["line"],
            lightcolor=C["line"],
            darkcolor=C["line"],
            padding=(8, 5),
            relief="flat",
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", C["surface_soft"])],
            foreground=[("readonly", C["ink"])],
            selectbackground=[("readonly", C["ice"])],
            selectforeground=[("readonly", C["ink"])],
            bordercolor=[("focus", C["blue"])],
        )
        self.option_add("*TCombobox*Listbox.background", C["surface"])
        self.option_add("*TCombobox*Listbox.foreground", C["ink"])
        self.option_add("*TCombobox*Listbox.selectBackground", C["ice"])
        self.option_add("*TCombobox*Listbox.selectForeground", C["ink"])
        self.option_add("*TCombobox*Listbox.font", f"{{{FONT_UI}}} 9")

    def _ui(self, fn, *args, **kwargs):
        """Call a UI function safely from a worker thread."""
        try:
            self.after(0, lambda: fn(*args, **kwargs))
        except (tk.TclError, RuntimeError):
            pass

    def report_callback_exception(self, exc, value, tb):
        """Tk's default handler writes to stderr; the windowed exe has none."""
        detail = "".join(traceback.format_exception(exc, value, tb))
        append_log(APP_LOG, detail)
        try:
            messagebox.showerror("RemVoice 出错", str(value), parent=self)
        except tk.TclError:
            pass

    # ------------------------------------------------------------------
    # Shell: header, sidebar, page host and footer
    # ------------------------------------------------------------------

    def _build_shell(self):
        self.header = tk.Canvas(
            self, height=86, bg=C["nav"], highlightthickness=0, bd=0
        )
        self.header.pack(fill="x")
        self.header.bind("<Configure>", self._draw_header)

        self.body = tk.Frame(self, bg=C["bg"])
        self.body.pack(fill="both", expand=True)

        self.sidebar = tk.Frame(self.body, width=232, bg=C["nav"])
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)
        tk.Frame(self.body, bg=C["line"], width=1).pack(side="left", fill="y")

        tk.Label(
            self.sidebar,
            text="W O R K S P A C E",
            bg=C["nav"],
            fg=C["nav_muted"],
            font=_font(7, bold=True, family="Segoe UI"),
        ).pack(anchor="w", padx=20, pady=(20, 10))

        self.nav_synth = NavItem(
            self.sidebar,
            "声音工坊",
            "wave",
            lambda: self._show_page("synth"),
            index="01",
        )
        self.nav_synth.pack(fill="x", padx=16, pady=(0, 6))
        self.nav_chat = NavItem(
            self.sidebar,
            "与助手聊天",
            "chat",
            lambda: self._show_page("chat"),
            index="02",
        )
        self.nav_chat.pack(fill="x", padx=16)

        spacer = tk.Frame(self.sidebar, bg=C["nav"])
        spacer.pack(fill="both", expand=True)

        if self._portrait_img is not None:
            self.portrait = PortraitCard(self.sidebar, self._portrait_img)
            self.portrait.pack(padx=16, pady=(0, 12))
        self.engine_card = EngineCard(self.sidebar)
        self.engine_card.pack(fill="x", padx=16, pady=(0, 8))
        tk.Label(
            self.sidebar,
            text="RemVoice 2.1  ·  PRIVATE",
            bg=C["nav"],
            fg="#A4B7C1",
            font=_font(7, family="Segoe UI"),
        ).pack(anchor="w", padx=18, pady=(0, 12))

        self.main = tk.Frame(self.body, bg=C["bg"])
        self.main.pack(side="left", fill="both", expand=True)
        self._build_footer()
        self.page_host = tk.Frame(self.main, bg=C["bg"])
        self.page_host.pack(fill="both", expand=True)
        self.page_host.grid_rowconfigure(0, weight=1)
        self.page_host.grid_columnconfigure(0, weight=1)
        self.synth_tab = None
        self.chat_tab = None

    def _build_footer(self):
        footer = tk.Frame(self.main, bg=C["surface"], height=30)
        footer.pack(side="bottom", fill="x")
        footer.pack_propagate(False)
        tk.Frame(footer, bg=C["line_soft"], height=1).pack(fill="x", side="top")
        self.footer_dot = tk.Canvas(
            footer,
            width=12,
            height=12,
            bg=C["surface"],
            highlightthickness=0,
            bd=0,
        )
        self.footer_dot.pack(side="left", padx=(22, 5))
        self.footer_dot.create_oval(3, 3, 9, 9, fill=C["blue"], outline="")
        tk.Label(
            footer,
            textvariable=self.status,
            bg=C["surface"],
            fg=C["muted"],
            font=_font(8),
            anchor="w",
        ).pack(side="left", fill="x", expand=True)
        tk.Label(
            footer,
            text="本地推理  ·  音频只保存在这台电脑",
            bg=C["surface"],
            fg=C["faint"],
            font=_font(8),
        ).pack(side="right", padx=(8, 22))

    def _draw_header(self, _event=None):
        c = self.header
        c.delete("all")
        width = max(900, c.winfo_width() or 1160)
        height = 86
        for y in range(0, height, 2):
            color = _mix("#FBFEFF", "#DFF0FA", y / height)
            c.create_rectangle(0, y, width, y + 2, fill=color, outline="")

        # Soft, barely-there circles keep the header airy.
        c.create_oval(
            width - 420, -220, width - 10, 190, outline="#D3EAF7", width=1
        )
        c.create_oval(
            width - 330, -140, width + 40, 240, outline="#DFF0F8", width=1
        )
        c.create_oval(
            width - 245, -70, width - 30, 166, outline="#E4F3FA", width=1
        )
        for x, y, r in (
            (width - 470, 24, 2),
            (width - 392, 68, 1),
            (width - 292, 18, 1),
            (width - 80, 72, 1),
        ):
            c.create_oval(x - r, y - r, x + r, y + r, fill="#B8DCEB", outline="")

        # Round Rem portrait acts as the app's brand mark.
        c.create_oval(27, 20, 75, 68, fill="#FFFFFF", outline="#CFE6F2")
        if self._avatar_img is not None:
            c.create_image(51, 44, image=self._avatar_img)
        else:
            c.create_text(
                51,
                44,
                text="R",
                fill=C["blue_deep"],
                font=(FONT_DISPLAY, 18, "bold"),
            )
        c.create_oval(
            66, 58, 76, 68, fill=C["green"], outline="#FFFFFF", width=2
        )

        c.create_text(
            88,
            35,
            text="RemVoice",
            anchor="w",
            fill=C["ink"],
            font=self._brand_font,
        )
        brand_w = self._brand_font.measure("RemVoice")
        _round_rect(
            c,
            88 + brand_w + 12,
            25,
            88 + brand_w + 57,
            43,
            9,
            fill="#E2F3FC",
            outline="",
        )
        c.create_text(
            88 + brand_w + 34,
            34,
            text="V" + __version__,
            fill=C["blue_dark"],
            font=(FONT_DISPLAY, 7, "bold"),
        )
        c.create_text(
            88,
            62,
            text="助手语音  ·  从零开始的每一次对话",
            anchor="w",
            fill=C["muted"],
            font=_font(9),
        )

        pill_w = 188
        x2 = width - 28
        x1 = x2 - pill_w
        y1, y2 = 21, 56
        _round_rect(
            c,
            x1,
            y1,
            x2,
            y2,
            18,
            fill="#FFFFFF",
            outline="#D5E8F3",
        )
        c.create_oval(
            x1 + 16,
            y1 + 10,
            x1 + 24,
            y1 + 18,
            fill=C["gold"],
            outline="",
            tags=("status_dot",),
        )
        c.create_text(
            x1 + 32,
            y1 + 17,
            text=self._engine_text,
            anchor="w",
            fill=C["ink_soft"],
            font=self._pill_font,
            tags=("status_text",),
        )
        c.create_text(
            x2,
            72,
            text="LOCAL INFERENCE  ·  127.0.0.1:9880",
            anchor="e",
            fill="#9CB3BF",
            font=(FONT_DISPLAY, 7, "bold"),
        )
        c.create_line(0, height - 1, width, height - 1, fill="#DCEBF4")
        self._paint_header_status()

    def _paint_header_status(self):
        if not hasattr(self, "header"):
            return
        colors = {
            "loading": C["gold"],
            "ready": C["green"],
            "thinking": C["blue"],
            "speaking": C["blue"],
            "error": C["red"],
        }
        try:
            self.header.itemconfigure(
                "status_dot", fill=colors.get(self._engine_state, C["blue"])
            )
            self.header.itemconfigure("status_text", text=self._engine_text)
        except tk.TclError:
            pass

    def set_engine_state(self, state, text=None):
        self._engine_state = state
        self._engine_text = text or {
            "loading": "正在唤醒引擎",
            "ready": "本地引擎就绪",
            "thinking": "助手正在想…",
            "speaking": "助手正在说话…",
            "error": "引擎未连接",
        }.get(state, state)
        self._paint_header_status()
        if hasattr(self, "engine_card"):
            self.engine_card.set_state(state, text)

    def _show_page(self, name):
        if not self.synth_tab or not self.chat_tab:
            return
        self.active_page = name
        if name == "chat":
            self.chat_tab.tkraise()
            # The transcript changes height after the page becomes visible.
            self.after(60, self.chat_view.scroll.to_bottom)
            self.after(240, self.chat_view.scroll.to_bottom)
            if self._service_ready:
                self.log("和助手说说话吧 · 回复会自动朗读")
        else:
            name = "synth"
            self.synth_tab.tkraise()
            if self._service_ready:
                self.log("写下一句台词，让助手念给你听")
        self.nav_synth.set_active(name == "synth")
        self.nav_chat.set_active(name == "chat")

    def _raise_active_page(self):
        if self.active_page == "chat":
            self.chat_tab.tkraise()
            self.chat_view.scroll.to_bottom()
        else:
            self.synth_tab.tkraise()

    def _route_wheel(self, event):
        try:
            widget = self.winfo_containing(event.x_root, event.y_root)
        except (tk.TclError, KeyError):
            return None
        while widget is not None:
            if isinstance(widget, (tk.Text, tk.Listbox, ttk.Treeview)):
                return None
            if isinstance(widget, ScrollArea):
                return widget.wheel(event)
            widget = getattr(widget, "master", None)
        return None

    # ------------------------------------------------------------------
    # Page heading
    # ------------------------------------------------------------------

    def _page_heading(self, parent, eyebrow, title, subtitle, right_builder):
        head = tk.Frame(parent, bg=C["bg"])
        head.pack(fill="x", padx=28, pady=(22, 16))
        left = tk.Frame(head, bg=C["bg"])
        left.pack(side="left", fill="x", expand=True)
        tk.Label(
            left,
            text=eyebrow,
            bg=C["bg"],
            fg=C["blue_dark"],
            font=(FONT_DISPLAY, 7, "bold"),
        ).pack(anchor="w")
        tk.Label(
            left,
            text=title,
            bg=C["bg"],
            fg=C["ink"],
            font=_font(23, bold=True),
        ).pack(anchor="w", pady=(2, 2))
        tk.Label(
            left,
            text=subtitle,
            bg=C["bg"],
            fg=C["muted"],
            font=_font(9),
        ).pack(anchor="w")
        right = tk.Frame(head, bg=C["bg"])
        right.pack(side="right", anchor="e")
        right_builder(right)
        return head

    # ------------------------------------------------------------------
    # Synthesis page
    # ------------------------------------------------------------------

    def _build_synth_page(self):
        page = tk.Frame(self.page_host, bg=C["bg"])
        page.grid(row=0, column=0, sticky="nsew")
        self.synth_tab = page

        def right_badges(parent):
            Badge(parent, "WAV · 48 kHz").pack(side="right", padx=(8, 0))
            Badge(
                parent,
                "LOCAL",
                fg="#397E63",
                fill="#E2F3EC",
            ).pack(side="right")

        self._page_heading(
            page,
            "VOICE STUDIO  /  01",
            "声音工坊",
            "写下台词，让助手用熟悉的声音念给你听。",
            right_badges,
        )

        body = tk.Frame(page, bg=C["bg"])
        body.pack(fill="both", expand=True, padx=28, pady=(0, 18))
        body.grid_rowconfigure(0, weight=1)
        body.grid_columnconfigure(0, weight=62, minsize=360)
        body.grid_columnconfigure(1, weight=38, minsize=280)

        editor = Card(body)
        editor.grid(row=0, column=0, sticky="nsew", padx=(0, 16))
        self._build_editor_card(editor)

        side = tk.Frame(body, bg=C["bg"])
        side.grid(row=0, column=1, sticky="nsew")
        side.grid_columnconfigure(0, weight=1)
        side.grid_rowconfigure(1, weight=1)
        self._build_status_card(side)
        self._build_recent_card(side)

    def _build_editor_card(self, card):
        head = tk.Frame(card, bg=C["surface"])
        head.pack(fill="x", padx=18, pady=(15, 10))
        title_box = tk.Frame(head, bg=C["surface"])
        title_box.pack(side="left")
        tk.Label(
            title_box,
            text="台词脚本",
            bg=C["surface"],
            fg=C["ink"],
            font=_font(11, bold=True),
        ).pack(side="left")
        tk.Label(
            title_box,
            text="  每行一句 · 按顺序合成",
            bg=C["surface"],
            fg=C["muted"],
            font=_font(8),
        ).pack(side="left", pady=(2, 0))
        tk.Label(
            head,
            text="SCENE  /  01",
            bg=C["surface"],
            fg=C["faint"],
            font=(FONT_DISPLAY, 7, "bold"),
        ).pack(side="right")

        shell = tk.Frame(
            card,
            bg=C["surface_soft"],
            highlightthickness=0,
            bd=0,
        )
        shell.pack(fill="both", expand=True, padx=18)
        self.text = tk.Text(
            shell,
            height=8,
            width=1,
            wrap="word",
            font=_font(10),
            bg=C["surface_soft"],
            fg=C["ink"],
            insertbackground=C["blue_deep"],
            selectbackground=C["ice"],
            selectforeground=C["ink"],
            relief="flat",
            bd=0,
            highlightthickness=0,
            padx=15,
            pady=12,
            spacing1=3,
            spacing3=5,
            undo=True,
        )
        self.text.pack(side="left", fill="both", expand=True)
        text_scroll = ttk.Scrollbar(
            shell,
            orient="vertical",
            command=self.text.yview,
            style="Soft.Vertical.TScrollbar",
        )
        text_scroll.pack(side="right", fill="y", padx=(0, 3), pady=3)
        self.text.configure(yscrollcommand=text_scroll.set)
        install_rounded_corners(shell, 12, C["surface_soft"], bg=C["surface"])
        self.text.tag_configure(
            "placeholder",
            foreground=C["faint"],
            font=_font(10, italic=True),
            spacing1=4,
            spacing3=6,
        )
        self.text.bind("<FocusIn>", self._synth_focus_in)
        self.text.bind("<FocusOut>", self._synth_focus_out)
        self.text.bind("<Control-Return>", self._synth_shortcut)
        self.text.bind("<Control-KP_Enter>", self._synth_shortcut)
        self.text.edit_modified(False)
        self.text.bind("<<Modified>>", self._synth_modified)

        count_row = tk.Frame(card, bg=C["surface"])
        count_row.pack(fill="x", padx=20, pady=(6, 0))
        self.synth_count_var = tk.StringVar(value="0 行 · 0 字")
        tk.Label(
            count_row,
            textvariable=self.synth_count_var,
            bg=C["surface"],
            fg=C["muted"],
            font=_font(8),
        ).pack(side="left")
        tk.Label(
            count_row,
            text="Ctrl + Enter 快速生成",
            bg=C["surface"],
            fg=C["faint"],
            font=_font(8),
        ).pack(side="right")
        self._show_synth_placeholder()

        tk.Frame(card, bg=C["line_soft"], height=1).pack(
            fill="x", padx=18, pady=(11, 12)
        )

        tone_row = tk.Frame(card, bg=C["surface"])
        tone_row.pack(fill="x", padx=18)
        tk.Label(
            tone_row,
            text="语气",
            bg=C["surface"],
            fg=C["ink"],
            font=_font(10, bold=True),
        ).pack(side="left")
        tk.Label(
            tone_row,
            text=self._tone_label,
            bg=C["surface"],
            fg=C["ink_soft"],
            font=_font(9),
        ).pack(side="left", padx=(8, 0), pady=(1, 0))
        tk.Label(
            tone_row,
            text="固定，保持声音一致",
            bg=C["surface"],
            fg=C["muted"],
            font=_font(8),
        ).pack(side="left", padx=(8, 0), pady=(1, 0))
        tk.Label(
            tone_row,
            text="日本語会先翻译台词",
            bg=C["surface"],
            fg=C["faint"],
            font=_font(7),
        ).pack(side="right", padx=(0, 8))
        self.synth_voice_menu = ToneMenu(
            tone_row,
            self.voice_lang_var,
            self._voice_langs,
            width=132,
            height=34,
            title="语音",
        )
        self.synth_voice_menu.pack(side="right")

        # The old four-chip tone row is intentionally gone.  `tone_var` stays
        # for run_batch/backward compatibility, but it is fixed to the one
        # language-neutral daily reference.
        self.tone_var = tk.StringVar(value=self.default_tone)

        actions = tk.Frame(card, bg=C["surface"])
        actions.pack(fill="x", padx=18, pady=(14, 16))
        tk.Label(
            actions,
            text="WAV · 自动播放",
            bg=C["surface"],
            fg=C["faint"],
            font=_font(8),
        ).pack(side="left")
        self.play_btn = PillButton(
            actions,
            text="生成并播放",
            command=self.on_play,
            kind="primary",
            width=150,
            height=42,
            font_size=10,
            icon="play",
        )
        self.play_btn.pack(side="right")
        self.open_btn = PillButton(
            actions,
            text="音频库",
            command=self.open_audio_library,
            kind="secondary",
            width=128,
            height=42,
            font_size=9,
            icon="open",
        )
        self.open_btn.pack(side="right", padx=(0, 8))

    def _build_status_card(self, parent):
        card = Card(parent)
        card.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        head = tk.Frame(card, bg=C["surface"])
        head.pack(fill="x", padx=18, pady=(13, 0))
        self.gen_dot = tk.Canvas(
            head,
            width=14,
            height=14,
            bg=C["surface"],
            highlightthickness=0,
            bd=0,
        )
        self.gen_dot.pack(side="left", pady=(3, 0))
        self.gen_dot.create_oval(3, 3, 11, 11, fill=C["gold"], outline="", tags=("dot",))
        tk.Label(
            head,
            text="生成状态",
            bg=C["surface"],
            fg=C["ink"],
            font=_font(10, bold=True),
        ).pack(side="left", padx=(7, 0))
        tk.Label(
            head,
            text="LOCAL TTS",
            bg=C["surface"],
            fg=C["faint"],
            font=(FONT_DISPLAY, 7, "bold"),
        ).pack(side="right")
        self.gen_title_var = tk.StringVar(value="正在唤醒引擎")
        self.gen_detail_var = tk.StringVar(value="准备本地推理服务…")
        tk.Label(
            card,
            textvariable=self.gen_title_var,
            bg=C["surface"],
            fg=C["ink"],
            font=_font(13, bold=True),
            anchor="w",
        ).pack(fill="x", padx=18, pady=(9, 0))
        self.gen_detail = tk.Label(
            card,
            textvariable=self.gen_detail_var,
            bg=C["surface"],
            fg=C["muted"],
            font=_font(8),
            anchor="w",
            justify="left",
            wraplength=270,
        )
        self.gen_detail.pack(fill="x", padx=18, pady=(2, 0))
        self.gen_progress = tk.Canvas(
            card,
            height=8,
            bg=C["surface"],
            highlightthickness=0,
            bd=0,
        )
        self.gen_progress.pack(fill="x", padx=18, pady=(9, 0))
        self.gen_progress.bind("<Configure>", lambda _e: self._draw_gen_progress())
        foot = tk.Frame(card, bg=C["surface"])
        foot.pack(fill="x", padx=18, pady=(5, 12))
        tk.Label(
            foot,
            text=f"语速 {SPEED:.2f}  ·  top-k {TOP_K}  ·  温柔参考",
            bg=C["surface"],
            fg=C["faint"],
            font=_font(8),
        ).pack(side="left")
        tk.Label(
            foot,
            text="WAV",
            bg=C["surface"],
            fg=C["faint"],
            font=(FONT_DISPLAY, 7, "bold"),
        ).pack(side="right")
        card.bind(
            "<Configure>",
            lambda e: self.gen_detail.configure(wraplength=max(210, e.width - 36)),
        )

    def _set_generation_status(self, title, detail, fraction, state="busy"):
        self.gen_title_var.set(title)
        self.gen_detail_var.set(detail)
        self._gen_fraction = max(0.0, min(1.0, float(fraction)))
        color = {
            "loading": C["gold"],
            "ready": C["green"],
            "busy": C["blue"],
            "success": C["green"],
            "error": C["red"],
        }.get(state, C["blue"])
        try:
            self.gen_dot.itemconfigure("dot", fill=color)
        except tk.TclError:
            pass
        self._draw_gen_progress()

    def _draw_gen_progress(self):
        if not hasattr(self, "gen_progress"):
            return
        c = self.gen_progress
        c.delete("all")
        width = c.winfo_width() or 270
        c.create_line(0, 4, width, 4, fill="#E8F0F3", width=5, capstyle="round")
        if self._gen_fraction > 0:
            c.create_line(
                0,
                4,
                max(4, int(width * self._gen_fraction)),
                4,
                fill=C["blue"],
                width=5,
                capstyle="round",
            )

    def _build_recent_card(self, parent):
        card = Card(parent)
        card.grid(row=1, column=0, sticky="nsew")
        head = tk.Frame(card, bg=C["surface"])
        head.pack(fill="x", padx=18, pady=(13, 8))
        tk.Label(
            head,
            text="最近生成",
            bg=C["surface"],
            fg=C["ink"],
            font=_font(10, bold=True),
        ).pack(side="left")
        self.recent_count_var = tk.StringVar(value="0 条")
        tk.Label(
            head,
            textvariable=self.recent_count_var,
            bg=C["surface"],
            fg=C["muted"],
            font=_font(8),
        ).pack(side="right", pady=(2, 0))
        self.recent_scroll = ScrollArea(card, bg=C["surface"])
        self.recent_scroll.pack(fill="both", expand=True, padx=8, pady=(0, 4))
        tk.Label(
            card,
            text="单击记录打开所在文件夹",
            bg=C["surface"],
            fg=C["faint"],
            font=_font(7),
            anchor="w",
        ).pack(fill="x", padx=18, pady=(1, 10))

        self.recent_empty = tk.Frame(self.recent_scroll.body, bg=C["surface"])
        self.recent_empty.pack(fill="both", expand=True, pady=(22, 18))
        icon = tk.Canvas(
            self.recent_empty,
            width=48,
            height=48,
            bg=C["surface"],
            highlightthickness=0,
            bd=0,
        )
        icon.pack()
        icon.create_oval(2, 2, 46, 46, fill=C["ice_soft"], outline="")
        icon.create_text(
            24,
            24,
            text="♪",
            fill=C["blue"],
            font=(FONT_DISPLAY, 18, "bold"),
        )
        tk.Label(
            self.recent_empty,
            text="还没有生成记录",
            bg=C["surface"],
            fg=C["ink_soft"],
            font=_font(9, bold=True),
        ).pack(pady=(8, 2))
        tk.Label(
            self.recent_empty,
            text="完成的语音会按时间排在这里",
            bg=C["surface"],
            fg=C["muted"],
            font=_font(8),
        ).pack()

    def _show_synth_placeholder(self):
        if self.text.get("1.0", "end-1c").strip():
            return
        self.text.delete("1.0", "end")
        self.text.insert(
            "1.0",
            "把想听助手说的台词写在这里……\n\n"
            "每行一句，会按顺序合成并播放。\n"
            "例如：旅行者君，欢迎回来。",
        )
        self.text.tag_add("placeholder", "1.0", "end")
        self._synth_placeholder = True
        self.synth_count_var.set("0 行 · 0 字")

    def _synth_focus_in(self, _event):
        if self._synth_placeholder:
            self.text.delete("1.0", "end")
            self._synth_placeholder = False
            self._refresh_synth_count()

    def _synth_focus_out(self, _event):
        if not self.text.get("1.0", "end-1c").strip():
            self._show_synth_placeholder()

    def _synth_shortcut(self, _event):
        self.on_play()
        return "break"

    def _synth_modified(self, _event):
        if not self.text.edit_modified():
            return
        self.text.edit_modified(False)
        if not self._synth_placeholder:
            self._refresh_synth_count()

    def _refresh_synth_count(self):
        text = self._synth_text_get()
        lines = [line for line in text.splitlines() if line.strip()]
        chars = len("".join(lines))
        self.synth_count_var.set(f"{len(lines)} 行 · {chars} 字")

    def _synth_text_get(self):
        if self._synth_placeholder:
            return ""
        return self.text.get("1.0", "end-1c").strip()

    def _synth_lines(self):
        return [line.strip() for line in self._synth_text_get().splitlines() if line.strip()]


    def _load_recent(self):
        """Restore the recent list from WAV files generated earlier."""
        try:
            entries = [
                entry
                for entry in os.scandir(SAVE_DIR)
                if entry.is_file()
                and entry.name.lower().endswith(".wav")
                and not entry.name.startswith("_")
            ]
        except OSError:
            return
        entries.sort(key=lambda entry: entry.stat().st_mtime)
        for entry in entries[-30:]:
            self._add_history(entry.path)

    def _chat_error_text(self, result):
        kind = getattr(result, "error_kind", "")
        if kind == "auth":
            return "API Key 或接口有问题，点「设置」检查一下吧。"
        if kind == "http":
            status = getattr(result, "status", None)
            if status:
                return f"接口返回错误（{status}），点「设置」检查一下吧。"
            return "接口返回错误，点「设置」检查一下吧。"
        if kind == "network":
            return "网络没连上，检查网络或 API 地址后再试。"
        if kind == "format":
            return "模型返回格式异常，再试一次吧。"
        if kind == "empty":
            return "模型这次没有返回内容，再试一次吧。"
        return "助手刚才没听清……稍后再试一次吧。"

    def _add_history(self, path):
        if self.recent_empty is not None:
            self.recent_empty.destroy()
            self.recent_empty = None
        row = RecentRow(self.recent_scroll.body, path, self._open_folder)
        if self._recent_rows:
            row.pack(fill="x", before=self._recent_rows[0])
        else:
            row.pack(fill="x")
        self._recent_rows.insert(0, row)
        self.recent_paths.insert(0, path)
        while len(self._recent_rows) > 30:
            old = self._recent_rows.pop()
            self.recent_paths.pop()
            old.destroy()
        self.recent_count_var.set(f"{len(self.recent_paths)} 条")
        self.recent_scroll.to_top()

    def open_audio_library(self):
        if self._audio_library is not None:
            try:
                if self._audio_library.winfo_exists():
                    self._audio_library.deiconify()
                    self._audio_library.lift()
                    self._audio_library.focus_force()
                    return
            except tk.TclError:
                pass
            self._audio_library = None
        self._audio_library = AudioLibrary(
            self,
            SAVE_DIR,
            on_play=self._play_chat_audio,
            on_open=self._open_folder,
            on_close=self._audio_library_closed,
        )

    def _audio_library_closed(self):
        self._audio_library = None

    def _open_folder(self, path):
        folder = path if os.path.isdir(path) else os.path.dirname(path)
        folder = os.path.normpath(folder)
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError:
            pass
        # ShellExecute can fail with WinError 5 from a lower-integrity process.
        # Do not spawn a second explorer.exe here: in a restricted token it can
        # fail with 0xc0000142 and shows a second, unrelated error dialog.
        try:
            os.startfile(folder)
            return
        except OSError as exc:
            try:
                self.clipboard_clear()
                self.clipboard_append(folder)
            except tk.TclError:
                pass
            messagebox.showinfo(
                "文件夹路径",
                f"系统没有允许自动打开资源管理器，路径已复制：\n\n{folder}\n\n"
                f"可以直接在程序里的「音频库」中播放文件。\n{exc}",
                parent=self,
            )

    def on_play(self):
        lines = self._synth_lines()
        if not lines:
            messagebox.showinfo("提示", "先写下想让助手说的台词吧。", parent=self)
            self.text.focus_set()
            return
        if self.worker and self.worker.is_alive():
            return
        if not self._service_ready:
            messagebox.showinfo("请稍候", "本地引擎还在加载，请稍等片刻。", parent=self)
            return
        tone = self.tones.get(self.tone_var.get()) or self.tones[self.default_tone]
        voice_lang = self.voice_lang_var.get()
        self.play_btn.set_state("disabled")
        self._set_generation_status(
            "准备生成",
            f"共 {len(lines)} 条台词，正在排队（{'日本語' if voice_lang == 'ja' else '中文'}）。",
            0.0,
            "busy",
        )
        self.log("开始生成语音…")
        self.worker = threading.Thread(
            target=self.run_batch,
            args=(lines, tone, SPEED, voice_lang),
            daemon=True,
        )
        self.worker.start()

    def run_batch(self, lines, tone, speed, voice_lang="zh"):
        total = len(lines)
        try:
            for index, line in enumerate(lines, 1):
                self._ui(
                    self._set_generation_status,
                    f"正在生成 {index} / {total}",
                    f"“{_ellipsize(line, 34)}”",
                    (index - 1) / max(1, total),
                    "busy",
                )
                self._ui(self.log, f"生成中 {index}/{total}：{_ellipsize(line, 26)}")
                tts_line = line
                text_lang = "auto"
                if voice_lang == "ja":
                    self._ui(
                        self._set_generation_status,
                        f"正在翻译 {index} / {total}",
                        "中文台词 → 日语朗读",
                        (index - 1) / max(1, total),
                        "busy",
                    )
                    tts_line = translate_to_japanese(line, self.chat_cfg)
                    text_lang = "ja"
                data = synth(
                    tts_line,
                    tone,
                    speed,
                    streaming_mode=STREAM_TTS_MODE,
                    text_lang=text_lang,
                )
                path = save_audio(data)
                self._ui(self._add_history, path)
                self._ui(self.log, f"已保存：{os.path.basename(path)}")
                play_wav(path)
            self._ui(
                self._set_generation_status,
                f"全部完成 · {total} 条",
                "音频已保存到「生成音频」文件夹。",
                1.0,
                "success",
            )
            self._ui(self.log, f"全部完成，共 {total} 条")
        except Exception as exc:
            self._ui(
                self._set_generation_status,
                "生成失败",
                f"请检查本地服务后重试。（{_ellipsize(exc, 38)}）",
                0.0,
                "error",
            )
            self._ui(self.log, f"生成失败：{exc}")
        finally:
            self._ui(self.play_btn.set_state, "normal")

    # ------------------------------------------------------------------
    # Chat page
    # ------------------------------------------------------------------

    def _build_chat_page(self):
        page = tk.Frame(self.page_host, bg=C["bg"])
        page.grid(row=0, column=0, sticky="nsew")
        self.chat_tab = page

        def right_actions(parent):
            self.settings_btn = PillButton(
                parent,
                text="设置",
                command=self.open_settings,
                kind="secondary",
                width=86,
                height=36,
                font_size=9,
                bold=False,
            )
            self.settings_btn.pack(side="right")
            self.chat_replay_btn = PillButton(
                parent,
                text="重播语音",
                command=self.replay_last_audio,
                kind="ghost",
                width=104,
                height=36,
                font_size=9,
                bold=False,
                icon="replay",
            )
            self.chat_replay_btn.pack(side="right", padx=(0, 4))
            self.chat_console_btn = PillButton(
                parent,
                text="控制台",
                command=self.open_chat_console,
                kind="ghost",
                width=86,
                height=36,
                font_size=9,
                bold=False,
            )
            self.chat_console_btn.pack(side="right", padx=(0, 4))
            self.chat_memory_btn = PillButton(
                parent,
                text="记忆",
                command=self.open_memory_panel,
                kind="ghost",
                width=78,
                height=36,
                font_size=9,
                bold=False,
            )
            self.chat_memory_btn.pack(side="right", padx=(0, 4))

        self._page_heading(
            page,
            "CHAT ROOM  /  02",
            "与助手的对话",
            "她记得你们说过的话，也会用自己的声音回应你。",
            right_actions,
        )

        outer = tk.Frame(page, bg=C["bg"])
        outer.pack(fill="both", expand=True, padx=28, pady=(0, 18))
        card = Card(outer)
        card.pack(fill="both", expand=True)

        top = tk.Frame(card, bg=C["surface"])
        top.pack(fill="x", padx=18, pady=(12, 11))
        avatar = tk.Canvas(
            top,
            width=44,
            height=44,
            bg=C["surface"],
            highlightthickness=0,
            bd=0,
        )
        avatar.pack(side="left")
        avatar.create_oval(0, 0, 44, 44, fill="#F7FCFF", outline="#D3E8F3")
        if self._avatar_img is not None:
            avatar.create_image(22, 22, image=self._avatar_img)
        else:
            avatar.create_text(
                22, 22, text="R", fill=C["blue_deep"], font=(FONT_DISPLAY, 13, "bold")
            )
        avatar.create_oval(33, 31, 42, 40, fill=C["green"], outline="#FFFFFF", width=2)
        info = tk.Frame(top, bg=C["surface"])
        info.pack(side="left", padx=(11, 0))
        name_row = tk.Frame(info, bg=C["surface"])
        name_row.pack(anchor="w")
        tk.Label(
            name_row,
            text="助手",
            bg=C["surface"],
            fg=C["ink"],
            font=_font(11, bold=True),
        ).pack(side="left")
        tk.Label(
            name_row,
            text="  ONLINE",
            bg=C["surface"],
            fg=C["green"],
            font=_font(7, bold=True, family="Segoe UI"),
        ).pack(side="left", pady=(2, 0))
        tk.Label(
            info,
            textvariable=self.chat_model_var,
            bg=C["surface"],
            fg=C["muted"],
            font=_font(8),
        ).pack(anchor="w", pady=(1, 0))
        tk.Label(
            info,
            textvariable=self.chat_meta_var,
            bg=C["surface"],
            fg=C["faint"],
            font=_font(7),
        ).pack(anchor="w")

        tk.Label(
            top,
            text=self._tone_label,
            bg=C["surface"],
            fg=C["ink_soft"],
            font=_font(9),
        ).pack(side="right", padx=(0, 10))
        self.voice_lang_menu = ToneMenu(
            top,
            self.voice_lang_var,
            self._voice_langs,
            width=132,
            height=34,
            title="语音",
        )
        self.voice_lang_menu.pack(side="right", padx=(0, 8))

        tk.Frame(card, bg=C["line_soft"], height=1).pack(fill="x")
        self.chat_view = ChatView(
            card,
            self.chat_cfg.get("user_name", "旅行者君"),
            self._play_chat_audio,
            display_turns=int(self.chat_cfg.get("display_turns", 8) or 0),
        )
        self.chat_view.pack(fill="both", expand=True)

        tk.Frame(card, bg=C["line_soft"], height=1).pack(fill="x")
        composer = tk.Frame(card, bg=C["surface"])
        composer.pack(fill="x", padx=16, pady=(10, 10))
        shell = tk.Frame(
            composer,
            bg=C["surface_soft"],
            highlightthickness=0,
            bd=0,
        )
        shell.pack(fill="x")
        self.chat_input = tk.Text(
            shell,
            height=2,
            width=1,
            wrap="word",
            font=_font(10),
            bg=C["surface_soft"],
            fg=C["ink"],
            insertbackground=C["blue_deep"],
            selectbackground=C["ice"],
            selectforeground=C["ink"],
            relief="flat",
            bd=0,
            highlightthickness=0,
            padx=15,
            pady=10,
            spacing1=2,
            spacing3=2,
        )
        self.chat_input.pack(fill="x", padx=(0, 3), pady=(0, 3))
        install_rounded_corners(shell, 14, C["surface_soft"], bg=C["surface"])
        self.chat_input.tag_configure("placeholder", foreground=C["faint"])
        self.chat_input.bind("<FocusIn>", self._chat_focus_in)
        self.chat_input.bind("<FocusOut>", self._chat_focus_out)
        self.chat_input.bind("<Return>", self._chat_return)
        self._show_chat_placeholder()

        bottom = tk.Frame(composer, bg=C["surface"])
        bottom.pack(fill="x", pady=(7, 0))
        tk.Label(
            bottom,
            textvariable=self.chat_activity_var,
            bg=C["surface"],
            fg=C["muted"],
            font=_font(8),
        ).pack(side="left")
        self.opening_btn = PillButton(
            bottom,
            text="让助手开口",
            command=self.on_opening,
            kind="ghost",
            width=104,
            height=32,
            font_size=8,
            bold=False,
        )
        self.opening_btn.pack(side="left", padx=(10, 0))
        self.voice_btn = PillButton(
            bottom,
            text="按住说话",
            command=None,
            kind="secondary",
            width=104,
            height=32,
            font_size=8,
            bold=False,
            press_command=self.on_voice_start,
            release_command=self.on_voice_stop,
        )
        self.voice_btn.pack(side="left", padx=(8, 0))
        self.send_btn = PillButton(
            bottom,
            text="发送",
            command=self.on_send,
            kind="primary",
            width=90,
            height=38,
            font_size=9,
        )
        self.send_btn.pack(side="right")
        self.stop_btn = PillButton(
            bottom,
            text="停止",
            command=self.on_stop,
            kind="ghost",
            width=62,
            height=38,
            font_size=9,
            bold=False,
        )
        self.stop_btn.pack(side="right", padx=(0, 4))
        tk.Label(
            bottom,
            text="Enter 发送  ·  Shift+Enter 换行  ·  F2 按住说话",
            bg=C["surface"],
            fg=C["faint"],
            font=_font(8),
        ).pack(side="right", padx=(0, 14))

    def _refresh_chat_labels(self):
        model = self.chat_cfg.get("model", DEFAULT_CHAT_CONFIG["model"])
        self.chat_model_var.set("LOCAL VOICE  ·  " + model)
        lang = "日本語" if self.chat_cfg.get("voice_lang", "zh") == "ja" else "中文"
        speak = "开" if self.chat_cfg.get("auto_speak", True) else "关"
        self.chat_meta_var.set(
            f"{self._tone_label}  ·  语音 {lang}  ·  语速 {SPEED:.2f}  ·  朗读 {speak}"
        )

    def _on_voice_lang_changed(self, *_args):
        key = self.voice_lang_var.get()
        if key not in self._voice_langs:
            return
        self.chat_cfg["voice_lang"] = key
        try:
            save_chat_config(self.chat_cfg)
        except Exception:
            pass
        if hasattr(self, "chat_model_var"):
            self._refresh_chat_labels()

    def _show_chat_placeholder(self):
        if self.chat_input.get("1.0", "end-1c").strip():
            return
        self.chat_input.delete("1.0", "end")
        self.chat_input.insert("1.0", "和助手说点什么……")
        self.chat_input.tag_add("placeholder", "1.0", "end")
        self._chat_placeholder = True

    def _chat_focus_in(self, _event):
        if self._chat_placeholder:
            self.chat_input.delete("1.0", "end")
            self._chat_placeholder = False

    def _chat_focus_out(self, _event):
        if not self.chat_input.get("1.0", "end-1c").strip():
            self._show_chat_placeholder()

    def _chat_return(self, event):
        if event.state & 0x0001:
            return None
        self.on_send()
        return "break"

    def _chat_text_get(self):
        if self._chat_placeholder:
            return ""
        return self.chat_input.get("1.0", "end-1c").replace("\n", " ").strip()

    def render_history(self):
        self.chat_view.clear()
        if not self.chat_session.messages:
            self.chat_view.add_assistant(
                [],
                "旅行者君，欢迎回来。今天想和助手聊些什么呢？",
            )
            self.chat_view.add_note("对话只保存在这台电脑上")
            return
        for message in self.chat_session.messages:
            role = message.get("role")
            content = message.get("content", "")
            if role == "user":
                self.chat_view.add_user(content)
            elif role == "assistant":
                actions, speech = parse_reply(content, add_punct=False)
                speech = clean_reply(speech)
                widget = self.chat_view.add_assistant(actions, speech)
                audio = message.get("audio", "")
                if audio and os.path.exists(audio):
                    widget.set_path(audio)
        self.chat_view.after(80, self.chat_view.scroll.to_bottom)


    def _play_chat_audio(self, path):
        if not path or not os.path.exists(path):
            return
        threading.Thread(target=play_wav, args=(path,), daemon=True).start()

    def _voice_hotkey_start(self, _event=None):
        self.on_voice_start()
        return "break"

    def _voice_hotkey_stop(self, _event=None):
        self.on_voice_stop()
        return "break"

    def _ensure_asr(self, silent=False):
        if self.asr is not None:
            return self.asr
        if not os.path.exists(PYTHON):
            message = "找不到 a-gen 的 Python：%s" % PYTHON
            if silent:
                append_log(APP_LOG, "ASR 预热跳过：" + message)
                return None
            self._voice_error(message)
            return None
        if not os.path.exists(ASR_WORKER):
            message = "找不到 asr_worker.py：%s" % ASR_WORKER
            if silent:
                append_log(APP_LOG, "ASR 预热跳过：" + message)
                return None
            self._voice_error(message)
            return None
        service = AsrService(
            PYTHON,
            ASR_WORKER,
            ASR_MODEL,
            device=ASR_DEVICE,
            compute_type=ASR_COMPUTE,
            language=ASR_LANGUAGE,
            beam_size=ASR_BEAM,
            download_root=ASR_MODEL_DIR,
            stderr_path=ASR_STDERR,
            on_event=self._on_asr_event,
            log=lambda message: append_log(APP_LOG, "ASR · " + str(message)),
            engine=ASR_ENGINE,
        )
        try:
            service.start()
        except Exception as exc:
            service.close()
            if silent:
                append_log(APP_LOG, "ASR 预热失败：" + str(exc))
                return None
            self._voice_error(str(exc))
            return None
        self.asr = service
        return service

    def _warm_asr(self):
        if self.asr is not None:
            return
        try:
            self._ensure_asr(silent=True)
        except Exception as exc:
            append_log(APP_LOG, "ASR 预热失败：" + str(exc))

    def _on_asr_event(self, event):
        self._ui(self._handle_asr_event, event)

    def _voice_error(self, message):
        self._voice_busy = False
        self._voice_active = False
        self._cancel_voice_timer()
        if hasattr(self, "voice_btn"):
            self.voice_btn.set_text("按住说话")
        self.chat_activity_var.set("语音识别不可用：" + _ellipsize(message, 52))
        self.set_engine_state("ready")
        append_log(APP_LOG, "ASR 错误：" + str(message))

    def _cancel_voice_timer(self):
        timer = getattr(self, "_voice_timer", None)
        if timer is None:
            return
        self._voice_timer = None
        try:
            self.after_cancel(timer)
        except Exception:
            pass

    def _handle_asr_event(self, event):
        kind = str(event.get("event") or "")
        if kind in ("starting", "loading"):
            if self._voice_busy or self._voice_active:
                self.chat_activity_var.set("正在准备语音识别…")
            return
        if kind == "ready":
            if self._voice_busy and self._voice_active:
                self.chat_activity_var.set("正在听…松开结束")
            return
        if kind == "recording":
            self.chat_activity_var.set("正在听…松开结束")
            self.set_engine_state("thinking", "正在听…")
            return
        if kind == "transcribing":
            self.chat_activity_var.set("正在识别…")
            self.set_engine_state("thinking", "正在识别…")
            return
        if kind == "cancelled":
            self._voice_busy = False
            self._voice_active = False
            self._cancel_voice_timer()
            self.voice_btn.set_text("按住说话")
            self.chat_activity_var.set("已取消语音")
            self.set_engine_state("ready")
            return
        if kind == "result":
            self._voice_busy = False
            self._voice_active = False
            self._cancel_voice_timer()
            self.voice_btn.set_text("按住说话")
            text = str(event.get("text") or "").strip()
            if not re.search(
                r"[\u4e00-\u9fff\u3040-\u30ffA-Za-z0-9]", text
            ):
                text = ""
            if not text:
                self.chat_activity_var.set("没听清，再试一次")
                self.set_engine_state("ready")
                return
            self.chat_input.delete("1.0", "end")
            self.chat_input.insert("1.0", text)
            self.chat_input.tag_remove("placeholder", "1.0", "end")
            self._chat_placeholder = False
            self.chat_input.focus_set()
            self.chat_activity_var.set("识别完成 · 按 Enter 发送")
            self.set_engine_state("ready")
            if self.chat_cfg.get("voice_auto_send", False):
                self.on_send()
            return
        if kind == "error":
            message = str(event.get("message") or "未知错误")
            if not (self._voice_busy or self._voice_active):
                append_log(APP_LOG, "ASR 预热失败：" + message)
                if self.asr is not None:
                    try:
                        self.asr.close()
                    except Exception:
                        pass
                    self.asr = None
                return
            self._voice_error(message)

    def on_voice_start(self, _event=None):
        if self._voice_busy or self._voice_active:
            return
        if not self._service_ready:
            self.chat_activity_var.set("本地引擎还在准备，稍后再用语音")
            return
        # Barge-in: stop Rem's speech before opening the microphone.
        self.on_stop()
        service = self._ensure_asr()
        if service is None:
            return
        self._voice_busy = True
        self._voice_active = True
        self.voice_btn.set_text("松开发送")
        self.chat_activity_var.set("正在准备语音识别…")
        self.set_engine_state("thinking", "正在准备语音…")
        self._voice_timer = self.after(30000, self.on_voice_stop)
        try:
            if not service.begin():
                self._voice_error("语音识别进程没有启动")
        except Exception as exc:
            self._voice_error(str(exc))

    def on_voice_stop(self, _event=None):
        if not self._voice_active:
            return
        self._voice_active = False
        self._cancel_voice_timer()
        self.voice_btn.set_text("按住说话")
        self.chat_activity_var.set("正在识别…")
        self.set_engine_state("thinking", "正在识别…")
        if self.asr is not None:
            self.asr.end()

    def on_send(self):
        text = self._chat_text_get()
        if not text:
            return
        self._send_text(text)

    def _chat_blocked_text(self):
        user_name = self.chat_cfg.get("user_name", "旅行者君")
        if (
            not str(self.chat_cfg.get("api_key", "")).strip()
            and not is_local_endpoint(self.chat_cfg.get("api_base", ""))
        ):
            return (
                f"{user_name}，助手还没拿到 API Key 呢。"
                "点右上角「设置」填写一下吧。"
            )
        if not self._service_ready:
            return "本地语音引擎还在准备中，请稍等一下再和助手说话。"
        return ""

    def _record_user(self, text):
        user_name = self.chat_cfg.get("user_name", "旅行者君")
        append_chat_transcript(user_name, text)
        append_chat_archive("user", text)

    def _record_assistant(self, reply, audio="", voice_lang="", tone=""):
        if not reply:
            return
        append_chat_transcript("助手", reply)
        append_chat_archive(
            "assistant", reply, audio=audio, voice_lang=voice_lang, tone=tone
        )

    def _send_text(self, text):
        if self.chat_worker and self.chat_worker.is_alive():
            self.chat_activity_var.set("上一轮还在收尾，稍等一下")
            return
        blocked = self._chat_blocked_text()
        if blocked:
            self.chat_view.add_assistant([], blocked)
            return
        self.chat_input.delete("1.0", "end")
        self._chat_placeholder = False
        self.chat_view.add_user(text)
        self._record_user(text)
        self.chat_stop.clear()
        self.send_btn.set_state("disabled")
        self.stop_btn.set_state("normal")
        self.chat_activity_var.set("助手正在想…")
        self.set_engine_state("thinking")
        self.pending_msg = self.chat_view.add_assistant(
            [], "助手正在想……", muted=True
        )
        self.chat_worker = threading.Thread(
            target=self.run_chat, args=(text,), daemon=True
        )
        self.chat_worker.start()

    def run_chat(self, user_text):
        self._run_turn(user_text)

    def run_opening(self):
        self._run_turn(None)

    def on_opening(self):
        if self.chat_worker and self.chat_worker.is_alive():
            self.chat_activity_var.set("上一轮还在收尾，稍等一下")
            return
        blocked = self._chat_blocked_text()
        if blocked:
            self.chat_view.add_assistant([], blocked)
            return
        self.chat_stop.clear()
        self.send_btn.set_state("disabled")
        self.stop_btn.set_state("normal")
        self.chat_activity_var.set("助手正在找话题…")
        self.set_engine_state("thinking")
        self.pending_msg = self.chat_view.add_assistant(
            [], "助手正在想……", muted=True
        )
        self.chat_worker = threading.Thread(target=self.run_opening, daemon=True)
        self.chat_worker.start()

    def _run_turn(self, user_text):
        message = self.pending_msg
        epoch = self._chat_epoch
        tone = self.tones.get(self._chat_tone_key) or self.tones[self.default_tone]
        voice_lang = self.chat_cfg.get("voice_lang", "zh")
        speak = bool(self.chat_cfg.get("auto_speak", True))
        stream_player = StreamingPlayer()
        use_streaming_audio = (
            speak
            and bool(self.chat_service.stream_voice)
            and STREAM_TTS_MODE > 0
            and stream_player.available
        )
        if use_streaming_audio:
            player = stream_player.start()
        else:
            player = AudioPlayer().start()
        self.audio_player = player

        def on_text(actions, speech):
            if epoch != self._chat_epoch:
                return
            self._ui(self._show_chat_stream, message, actions, speech)

        def on_audio(path):
            if epoch != self._chat_epoch:
                return
            self._ui(self.set_engine_state, "speaking")
            if not use_streaming_audio:
                player.put(path)

        def on_audio_chunk(chunk):
            if epoch != self._chat_epoch:
                return
            player.write(chunk)
            self._ui(self.set_engine_state, "speaking")

        try:
            if user_text is None:
                result = self.chat_service.opening(
                    self.chat_session,
                    tone,
                    cancel=self.chat_stop,
                    on_text=on_text,
                    on_audio=on_audio,
                    on_audio_chunk=on_audio_chunk if use_streaming_audio else None,
                )
            else:
                result = self.chat_service.run(
                    user_text,
                    self.chat_session,
                    tone,
                    cancel=self.chat_stop,
                    on_text=on_text,
                    on_audio=on_audio,
                    on_audio_chunk=on_audio_chunk if use_streaming_audio else None,
                )
        finally:
            player.finish()

        if epoch != self._chat_epoch:
            player.stop()
            return
        if result.cancelled:
            player.stop()
            self._ui(self._finish_chat_stopped)
            return
        if result.error_kind:
            player.stop()
            self._ui(self.log, f"聊天失败：{result.error}")
            self._ui(self._show_chat_error, message, self._chat_error_text(result))
            return
        timings = result.timings or {}
        if timings:
            if "first_token" in timings:
                detail = "首 token {}s · 首音 {}s · TTS {}s · 总 {}s".format(
                    timings.get("first_token") or "-",
                    timings.get("first_audio") or "-",
                    timings.get("tts", "-"),
                    timings.get("total", "-"),
                )
            else:
                detail = "LLM {}s · TTS {}s · 生成 {}s".format(
                    timings.get("llm", "-"),
                    timings.get("tts", "-"),
                    timings.get("total", "-"),
                )
            translate = timings.get("translate") or 0
            if translate:
                detail += " · 翻译 {}s".format(translate)
            self._ui(self.log, "本轮耗时：" + detail)
        if result.tts_error:
            self._ui(self.log, f"语音生成失败：{result.tts_error}")

        self._record_assistant(
            result.reply, result.audio_path, voice_lang, self._chat_tone_key
        )
        if result.audio_paths:
            self._ui(self._attach_chat_audio, message, result.audio_path)
            self.chat_session.set_last_audio(
                result.audio_path, voice_lang, self._chat_tone_key
            )
            player.wait(300)
            if self.chat_stop.is_set() or epoch != self._chat_epoch:
                return
            self._ui(
                self._finish_chat_turn,
                ("主动开口" if user_text is None else "回复已播放")
                + " · 按 Enter 继续",
            )
        elif result.speech and not speak:
            self._ui(self._finish_chat_turn, "文字回复 · 未朗读 · 按 Enter 继续")
        elif result.speech:
            self._ui(self._finish_chat_turn, "语音生成失败 · 按 Enter 继续")
        else:
            self._ui(self.log, "这条回复只有动作，没有说话")
            self._ui(self._finish_chat_turn, "这条回复只有动作 · 按 Enter 继续")

    def _show_chat_stream(self, message, actions, speech):
        if message is None:
            return
        message.set_content(actions, speech)
        self.chat_view.scroll.to_bottom()


    def _show_chat_reply(self, message, actions, speech):
        if message is not None:
            message.set_content(actions, speech)
            self.chat_view.scroll.to_bottom()
        if speech:
            self.chat_activity_var.set("语音生成中…")
        else:
            self.chat_activity_var.set("这条回复只有动作")

    def _attach_chat_audio(self, message, path):
        if message is not None:
            message.set_path(path)
            self.chat_audio[id(message)] = path
        self.last_reply_audio = path
        self.chat_view.scroll.to_bottom()

    def _finish_chat_turn(self, activity):
        self.pending_msg = None
        self.chat_activity_var.set(activity)
        self.send_btn.set_state("normal")
        self.stop_btn.set_state("disabled")
        self.set_engine_state("ready")
        self.log("本轮聊天结束")

    def _finish_chat_stopped(self):
        if self.pending_msg is not None:
            self.pending_msg.set_content([], "好，助手先不说了。", muted=True)
            self.chat_view.scroll.to_bottom()
        self.pending_msg = None
        self.chat_activity_var.set("已停下 · 按 Enter 重新开始")
        self.send_btn.set_state("normal")
        self.stop_btn.set_state("disabled")
        self.set_engine_state("ready")
        self.log("已停止本轮聊天")

    def _show_chat_error(self, message, detail):
        if message is None:
            self.chat_view.add_assistant([], detail)
        else:
            message.set_content([], detail, error=True)
        self.pending_msg = None
        self.chat_view.scroll.to_bottom()
        self.chat_activity_var.set("回复失败 · 可以再试一次")
        self.send_btn.set_state("normal")
        self.stop_btn.set_state("disabled")
        self.set_engine_state("ready")

    def on_stop(self):
        # Playback can outlive the chat worker (audio queue drains later), so
        # always stop it; voice input uses this for barge-in.
        if self.audio_player is not None:
            self.audio_player.stop()
        else:
            stop_playback()
        if not (self.chat_worker and self.chat_worker.is_alive()):
            return
        self.chat_stop.set()
        self._chat_epoch += 1
        if self.pending_msg is not None:
            self.pending_msg.set_content([], "好，助手先不说了。", muted=True)
        self.pending_msg = None
        self.chat_activity_var.set("已停下 · 按 Enter 重新开始")
        self.send_btn.set_state("normal")
        self.stop_btn.set_state("disabled")
        self.set_engine_state("ready")
        self.chat_view.scroll.to_bottom()
        self.log("已停止本轮聊天")


    def replay_last_audio(self):
        if not self.last_reply_audio or not os.path.exists(self.last_reply_audio):
            messagebox.showinfo("提示", "还没有可以重播的聊天语音。", parent=self)
            return
        self._play_chat_audio(self.last_reply_audio)

    def _reload_chat_view(self):
        self.chat_view.clear()
        self.render_history()
        self._refresh_chat_labels()

    def open_chat_console(self):
        win = tk.Toplevel(self)
        win.title("RemVoice · 聊天控制台")
        win.configure(bg=C["bg"])
        win.transient(self)
        win.grab_set()
        self._center_toplevel(win, 560, 430)

        body = tk.Frame(win, bg=C["surface"])
        body.pack(fill="both", expand=True, padx=18, pady=18)

        tk.Label(
            body,
            text="聊天控制台",
            bg=C["surface"],
            fg=C["ink"],
            font=_font(16, bold=True),
        ).pack(anchor="w")
        tk.Label(
            body,
            text="只影响当前聊天，不改变人设和模型",
            bg=C["surface"],
            fg=C["muted"],
            font=_font(9),
        ).pack(anchor="w", pady=(2, 14))

        display_row = tk.Frame(body, bg=C["surface"])
        display_row.pack(fill="x", pady=(0, 10))
        tk.Label(
            display_row,
            text="聊天窗口最多显示最近",
            bg=C["surface"],
            fg=C["ink"],
            font=_font(10),
        ).pack(side="left")
        display_var = tk.StringVar(
            value=str(self.chat_cfg.get("display_turns", 8))
        )
        ttk.Spinbox(
            display_row,
            from_=0,
            to=50,
            width=5,
            textvariable=display_var,
            justify="center",
        ).pack(side="left", padx=8)
        tk.Label(
            display_row,
            text="轮（0 = 全部）",
            bg=C["surface"],
            fg=C["muted"],
            font=_font(9),
        ).pack(side="left")

        speak_row = tk.Frame(body, bg=C["surface"])
        speak_row.pack(fill="x", pady=(0, 14))
        tk.Label(
            speak_row,
            text="自动朗读回复",
            bg=C["surface"],
            fg=C["ink"],
            font=_font(10),
        ).pack(side="left")
        speak_var = tk.BooleanVar(
            value=bool(self.chat_cfg.get("auto_speak", True))
        )
        Switch(speak_row, speak_var).pack(side="right")

        status_var = tk.StringVar(value="")
        status = tk.Label(
            body,
            textvariable=status_var,
            bg=C["surface"],
            fg=C["muted"],
            font=_font(8),
            anchor="w",
        )
        status.pack(fill="x", pady=(0, 10))

        buttons = tk.Frame(body, bg=C["surface"])
        buttons.pack(fill="x")

        def apply_settings():
            try:
                turns = int(display_var.get().strip())
            except ValueError:
                status_var.set("显示轮次必须是 0–50 的整数")
                return
            turns = max(0, min(50, turns))
            self.chat_cfg["display_turns"] = turns
            self.chat_cfg["auto_speak"] = bool(speak_var.get())
            try:
                save_chat_config(self.chat_cfg)
            except Exception as exc:
                status_var.set(f"保存失败：{exc}")
                return
            self.chat_view.set_display_turns(turns)
            self._reload_chat_view()
            status_var.set("已应用")

        def regenerate():
            if self.chat_worker and self.chat_worker.is_alive():
                status_var.set("上一轮还在生成，稍等")
                return
            text = self.chat_session.pop_last_turn()
            if not text:
                status_var.set("没有可重新生成的回合")
                return
            self.chat_view.remove_last_turn()
            self.last_reply_audio = None
            self._send_text(text)
            win.destroy()

        def delete_last():
            text = self.chat_session.pop_last_turn()
            if not text:
                status_var.set("没有可删除的回合")
                return
            self.chat_view.remove_last_turn()
            self.last_reply_audio = None
            status_var.set("已删除上一轮")
            self._refresh_chat_labels()

        def clear_all():
            if not messagebox.askyesno(
                "清空对话", "确定清空当前聊天记录吗？", parent=win
            ):
                return
            self.chat_session.clear()
            self.last_reply_audio = None
            self._reload_chat_view()
            status_var.set("已清空")

        def export_markdown():
            os.makedirs(CHAT_RECORD_DIR, exist_ok=True)
            path = os.path.join(
                CHAT_RECORD_DIR,
                "聊天导出_" + time.strftime("%Y%m%d_%H%M%S") + ".md",
            )
            try:
                self.chat_session.export_markdown(path)
            except Exception as exc:
                status_var.set(f"导出失败：{exc}")
                return
            status_var.set("已导出：" + os.path.basename(path))
            try:
                os.startfile(CHAT_RECORD_DIR)
            except OSError:
                pass

        def open_archive():
            win.destroy()
            self.open_chat_archive()

        PillButton(
            buttons,
            text="应用设置",
            command=apply_settings,
            kind="primary",
            width=110,
            height=36,
            font_size=9,
        ).pack(side="left")
        PillButton(
            buttons,
            text="重新生成上一轮",
            command=regenerate,
            kind="secondary",
            width=128,
            height=36,
            font_size=8,
            bold=False,
        ).pack(side="left", padx=(8, 0))
        PillButton(
            buttons,
            text="删除上一轮",
            command=delete_last,
            kind="ghost",
            width=104,
            height=36,
            font_size=8,
            bold=False,
        ).pack(side="left", padx=(8, 0))

        bottom = tk.Frame(body, bg=C["surface"])
        bottom.pack(fill="x", pady=(14, 0))
        PillButton(
            bottom,
            text="清空对话",
            command=clear_all,
            kind="ghost",
            width=96,
            height=34,
            font_size=8,
            bold=False,
        ).pack(side="left")
        PillButton(
            bottom,
            text="导出 Markdown",
            command=export_markdown,
            kind="ghost",
            width=126,
            height=34,
            font_size=8,
            bold=False,
        ).pack(side="left", padx=(8, 0))
        PillButton(
            bottom,
            text="聊天档案",
            command=open_archive,
            kind="ghost",
            width=96,
            height=34,
            font_size=8,
            bold=False,
        ).pack(side="left", padx=(8, 0))
        PillButton(
            bottom,
            text="关闭",
            command=win.destroy,
            kind="secondary",
            width=76,
            height=34,
            font_size=8,
            bold=False,
        ).pack(side="right")

        win.bind("<Escape>", lambda _e: win.destroy())
        win.after(30, lambda: win.focus_force())

    # ------------------------------------------------------------------
    # Chat archive viewer
    # ------------------------------------------------------------------
    def open_chat_archive(self):
        win = tk.Toplevel(self)
        win.title("RemVoice · 聊天档案")
        win.configure(bg=C["bg"])
        win.transient(self)
        self._center_toplevel(win, 820, 640)

        body = tk.Frame(win, bg=C["bg"])
        body.pack(fill="both", expand=True, padx=20, pady=18)
        footer = tk.Frame(body, bg=C["surface"])
        footer.pack(side="bottom", fill="x", pady=(10, 0))

        tk.Label(
            body,
            text="聊天档案",
            bg=C["bg"],
            fg=C["ink"],
            font=_font(16, bold=True),
        ).pack(anchor="w")
        tk.Label(
            body,
            text="append-only 原文，不受最近 200 条限制；只保存在本机。",
            bg=C["bg"],
            fg=C["muted"],
            font=_font(9),
        ).pack(anchor="w", pady=(2, 10))

        search_row = tk.Frame(body, bg=C["surface"])
        search_row.pack(fill="x", pady=(0, 8))
        tk.Label(
            search_row,
            text="关键词",
            bg=C["surface"],
            fg=C["ink"],
            font=_font(9),
        ).pack(side="left", padx=(12, 8))
        query_var = tk.StringVar()
        query_entry = tk.Entry(
            search_row,
            textvariable=query_var,
            font=_font(9),
            bg=C["surface_soft"],
            fg=C["ink"],
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=C["line"],
            highlightcolor=C["blue"],
            insertbackground=C["blue_deep"],
        )
        query_entry.pack(side="left", fill="x", expand=True, ipady=7, padx=(0, 8))
        status_var = tk.StringVar(value="")
        PillButton(
            search_row,
            text="搜索",
            command=lambda: refresh(),
            kind="secondary",
            width=76,
            height=32,
            font_size=8,
            bold=False,
        ).pack(side="right", padx=(0, 12))
        PillButton(
            search_row,
            text="刷新",
            command=lambda: refresh(),
            kind="ghost",
            width=76,
            height=32,
            font_size=8,
            bold=False,
        ).pack(side="right", padx=(0, 6))

        style = ttk.Style(win)
        style.configure(
            "Archive.Treeview",
            background=C["surface"],
            fieldbackground=C["surface"],
            foreground=C["ink"],
            rowheight=26,
            font=(FONT_UI, 9),
            borderwidth=0,
        )
        style.configure(
            "Archive.Treeview.Heading",
            font=(FONT_UI, 8, "bold"),
            background=C["surface_soft"],
            foreground=C["muted"],
            relief="flat",
        )
        style.map(
            "Archive.Treeview",
            background=[("selected", C["ice"])],
            foreground=[("selected", C["ink"])],
        )

        tree_frame = tk.Frame(body, bg=C["surface"])
        tree_frame.pack(fill="both", expand=True)
        tree = ttk.Treeview(
            tree_frame,
            columns=("when", "role", "text"),
            show="headings",
            style="Archive.Treeview",
            selectmode="browse",
        )
        tree.heading("when", text="时间")
        tree.heading("role", text="角色")
        tree.heading("text", text="原文")
        tree.column("when", width=138, anchor="center", stretch=False)
        tree.column("role", width=58, anchor="center", stretch=False)
        tree.column("text", width=560, anchor="w")
        scroll = ttk.Scrollbar(
            tree_frame, orient="vertical", command=tree.yview
        )
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side="left", fill="both", expand=True, padx=(12, 0), pady=(0, 8))
        scroll.pack(side="right", fill="y", padx=(0, 12), pady=(0, 8))

        full_rows = {}

        def refresh(*_args):
            rows = read_chat_archive(query_var.get(), limit=500)
            tree.delete(*tree.get_children())
            full_rows.clear()
            for index, row in enumerate(rows):
                iid = "row_%d" % index
                full_rows[iid] = str(row.get("content", ""))
                stamp = time.strftime(
                    "%Y-%m-%d %H:%M:%S",
                    time.localtime(row.get("ts") or 0),
                )
                role = "助手" if row.get("role") == "assistant" else "你"
                text = " ".join(str(row.get("content", "")).split())
                if len(text) > 180:
                    text = text[:179] + "…"
                tree.insert("", "end", iid=iid, values=(stamp, role, text))
            if rows:
                status_var.set(
                    "显示最近 %d 条 · %s"
                    % (len(rows), os.path.basename(CHAT_ARCHIVE))
                )
            else:
                status_var.set(
                    "还没有聊天档案；新对话会写入 %s"
                    % os.path.basename(CHAT_ARCHIVE)
                )

        def copy_selected():
            selection = tree.selection()
            if not selection:
                status_var.set("先在列表里选一条")
                return
            text = full_rows.get(selection[0], "")
            try:
                self.clipboard_clear()
                self.clipboard_append(text)
            except tk.TclError:
                status_var.set("复制失败")
                return
            status_var.set("原文已复制")

        tk.Label(
            footer,
            textvariable=status_var,
            bg=C["surface"],
            fg=C["muted"],
            font=_font(8),
            anchor="w",
        ).pack(side="left", fill="x", expand=True, padx=(12, 8), pady=10)
        PillButton(
            footer,
            text="复制原文",
            command=copy_selected,
            kind="ghost",
            width=96,
            height=34,
            font_size=8,
            bold=False,
        ).pack(side="right", pady=8)
        PillButton(
            footer,
            text="关闭",
            command=win.destroy,
            kind="secondary",
            width=76,
            height=34,
            font_size=8,
            bold=False,
        ).pack(side="right", padx=(0, 6), pady=8)

        query_entry.bind("<Return>", lambda _e: refresh())
        tree.bind("<Double-1>", lambda _e: copy_selected())
        refresh()
        win.bind("<Escape>", lambda _e: win.destroy())
        win.after(30, lambda: win.focus_force())

    # ------------------------------------------------------------------
    # Memory panel
    # ------------------------------------------------------------------
    def open_memory_panel(self):
        win = tk.Toplevel(self)
        win.title("RemVoice · 记忆")
        win.configure(bg=C["bg"])
        win.transient(self)
        win.grab_set()
        self._center_toplevel(win, 820, 680)

        body = tk.Frame(win, bg=C["bg"])
        body.pack(fill="both", expand=True, padx=20, pady=18)

        # The footer is packed before the notebook: Tk hands the space to the
        # widgets packed first, so packing it later would clip it away.
        footer = tk.Frame(body, bg=C["surface"])
        footer.pack(side="bottom", fill="x", pady=(10, 0))

        tk.Label(
            body,
            text="记忆",
            bg=C["bg"],
            fg=C["ink"],
            font=_font(16, bold=True),
        ).pack(anchor="w")
        tk.Label(
            body,
            text="只有这张「记忆卡」会进提示词；全量档案只在本机存档，可以搜回已经遗忘的内容。",
            bg=C["bg"],
            fg=C["muted"],
            font=_font(9),
        ).pack(anchor="w", pady=(2, 10))

        cap_row = tk.Frame(body, bg=C["surface"], height=48)
        cap_row.pack(fill="x", pady=(0, 10))
        cap_row.pack_propagate(False)
        tk.Label(
            cap_row,
            text="提示词占用",
            bg=C["surface"],
            fg=C["ink"],
            font=_font(9, bold=True),
        ).pack(side="left", padx=(14, 8), pady=14)
        bar = tk.Canvas(
            cap_row, height=8, bg=C["surface"], highlightthickness=0, bd=0
        )
        bar.pack(side="left", fill="x", expand=True, padx=(0, 12), pady=20)
        capacity_var = tk.StringVar(value="")
        tk.Label(
            cap_row,
            textvariable=capacity_var,
            bg=C["surface"],
            fg=C["muted"],
            font=_font(8),
        ).pack(side="right", padx=(0, 14))

        style = ttk.Style(win)
        style.configure(
            "Memory.Treeview",
            background=C["surface"],
            fieldbackground=C["surface"],
            foreground=C["ink"],
            rowheight=26,
            font=(FONT_UI, 9),
            borderwidth=0,
        )
        style.configure(
            "Memory.Treeview.Heading",
            font=(FONT_UI, 8, "bold"),
            background=C["surface_soft"],
            foreground=C["muted"],
            relief="flat",
        )
        style.map(
            "Memory.Treeview",
            background=[("selected", C["ice"])],
            foreground=[("selected", C["ink"])],
        )
        style.configure("Memory.TNotebook", background=C["bg"], borderwidth=0)
        style.configure("Memory.TNotebook.Tab", font=(FONT_UI, 9), padding=(16, 6))

        notebook = ttk.Notebook(body, style="Memory.TNotebook")
        notebook.pack(fill="both", expand=True)

        # -- tab 1: the card ------------------------------------------------
        card_tab = tk.Frame(notebook, bg=C["surface"])
        notebook.add(card_tab, text="记忆卡")

        actions = tk.Frame(card_tab, bg=C["surface"])
        actions.pack(fill="x", side="bottom", padx=10, pady=(0, 8))

        tree = ttk.Treeview(
            card_tab,
            columns=("when", "clarity", "text"),
            show="headings",
            style="Memory.Treeview",
            selectmode="browse",
            height=11,
        )
        tree.heading("when", text="日期")
        tree.heading("clarity", text="清晰度")
        tree.heading("text", text="记忆（★ = 置顶）")
        tree.column("when", width=96, anchor="center", stretch=False)
        tree.column("clarity", width=66, anchor="center", stretch=False)
        tree.column("text", width=540, anchor="w")
        tree_scroll = ttk.Scrollbar(card_tab, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=tree_scroll.set)
        tree.pack(side="left", fill="both", expand=True, padx=(10, 0), pady=8)
        tree_scroll.pack(side="right", fill="y", padx=(0, 8), pady=8)

        # -- tab 2: prompt preview -----------------------------------------
        preview_tab = tk.Frame(notebook, bg=C["surface"])
        notebook.add(preview_tab, text="提示词预览")
        preview = tk.Text(
            preview_tab,
            wrap="word",
            font=_font(9),
            bg=C["surface_soft"],
            fg=C["ink_soft"],
            relief="flat",
            bd=0,
            padx=14,
            pady=12,
            spacing1=2,
            spacing3=2,
        )
        preview_scroll = ttk.Scrollbar(
            preview_tab, orient="vertical", command=preview.yview
        )
        preview.configure(yscrollcommand=preview_scroll.set, state="disabled")
        preview.pack(side="left", fill="both", expand=True, padx=(10, 0), pady=8)
        preview_scroll.pack(side="right", fill="y", padx=(0, 8), pady=8)

        # -- tab 3: full archive -------------------------------------------
        archive_tab = tk.Frame(notebook, bg=C["surface"])
        notebook.add(archive_tab, text="全量档案")

        archive_actions = tk.Frame(archive_tab, bg=C["surface"])
        archive_actions.pack(fill="x", side="bottom", padx=10, pady=(0, 8))

        search_row = tk.Frame(archive_tab, bg=C["surface"])
        search_row.pack(fill="x", padx=10, pady=(10, 4))
        tk.Label(
            search_row,
            text="关键词",
            bg=C["surface"],
            fg=C["ink"],
            font=_font(9),
        ).pack(side="left", padx=(0, 8))
        query_var = tk.StringVar()
        query_entry = tk.Entry(
            search_row,
            textvariable=query_var,
            font=_font(9),
            bg=C["surface_soft"],
            fg=C["ink"],
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=C["line"],
            highlightcolor=C["blue"],
            insertbackground=C["blue_deep"],
        )
        query_entry.pack(side="left", fill="x", expand=True, ipady=7, padx=(0, 8))
        PillButton(
            search_row,
            text="搜索",
            command=lambda: refresh_archive(),
            kind="secondary",
            width=76,
            height=32,
            font_size=8,
            bold=False,
        ).pack(side="right")

        archive_tree = ttk.Treeview(
            archive_tab,
            columns=("when", "text"),
            show="headings",
            style="Memory.Treeview",
            height=12,
        )
        archive_tree.heading("when", text="归档时间")
        archive_tree.heading("text", text="原文")
        archive_tree.column("when", width=140, anchor="center", stretch=False)
        archive_tree.column("text", width=560, anchor="w")
        archive_scroll = ttk.Scrollbar(
            archive_tab, orient="vertical", command=archive_tree.yview
        )
        archive_tree.configure(yscrollcommand=archive_scroll.set)
        archive_tree.pack(
            side="left", fill="both", expand=True, padx=(10, 0), pady=(0, 8)
        )
        archive_scroll.pack(side="right", fill="y", padx=(0, 8), pady=(0, 8))

        # -- shared state ----------------------------------------------------
        enabled_var = tk.BooleanVar(
            value=bool(self.chat_cfg.get("memory_enabled", True))
        )
        llm_var = tk.BooleanVar(value=bool(self.chat_cfg.get("memory_llm", True)))
        status_var = tk.StringVar(value="")
        ratio_holder = {"value": 0.0}

        def draw_bar(*_args):
            bar.delete("all")
            width = max(10, bar.winfo_width())
            bar.create_rectangle(0, 0, width, 8, fill=C["line_soft"], outline="")
            ratio = max(0.0, min(1.0, ratio_holder["value"]))
            if ratio > 0:
                color = (
                    C["green"]
                    if ratio < 0.8
                    else (C["gold"] if ratio < 1.0 else C["red"])
                )
                bar.create_rectangle(
                    0, 0, int(width * ratio), 8, fill=color, outline=""
                )

        bar.bind("<Configure>", draw_bar)

        def refresh_archive(*_args):
            rows = self.memory_store.archive_rows(query_var.get().strip(), limit=300)
            archive_tree.delete(*archive_tree.get_children())
            for row in rows:
                stamp = time.strftime(
                    "%Y-%m-%d %H:%M",
                    time.localtime(
                        row.get("archived_at") or row.get("created_at") or 0
                    ),
                )
                archive_tree.insert(
                    "", "end", values=(stamp, str(row.get("text", "")))
                )

        def refresh():
            items = sorted(
                self.memory_store.items,
                key=lambda i: (
                    0 if i.get("pinned") else 1,
                    -float(i.get("importance", 0)),
                    -float(i.get("last_seen_at") or 0),
                ),
            )
            tree.delete(*tree.get_children())
            for item in items:
                stamp = time.strftime(
                    "%m-%d %H:%M", time.localtime(item.get("created_at") or 0)
                )
                star = "★ " if item.get("pinned") else ""
                tree.insert(
                    "",
                    "end",
                    iid=item.get("id"),
                    values=(
                        stamp,
                        CLARITY_LABEL.get(item.get("clarity"), "?"),
                        star + str(item.get("text", "")),
                    ),
                )
            capacity = self.memory_store.capacity(self.chat_cfg)
            ratio_holder["value"] = capacity["ratio"]
            draw_bar()
            capacity_var.set(
                "%d / %d 字（%d%%） · 记忆 %d 条 · 摘要 %d 字"
                % (
                    capacity["chars"],
                    capacity["budget"],
                    int(capacity["ratio"] * 100),
                    capacity["items"],
                    capacity["summary"],
                )
            )
            block = self.memory_store.prompt_block(self.chat_cfg)
            preview.configure(state="normal")
            preview.delete("1.0", "end")
            preview.insert(
                "1.0",
                block or "（还没有可以注入的记忆）\n\n聊过几轮、旧对话掉出最近 20 轮窗口之后，"
                "这里就会慢慢长出内容。也可以点「手动记一条」。",
            )
            preview.configure(state="disabled")
            stats = self.memory_store.stats()
            extra = ""
            if stats.get("merged"):
                extra += " · 合并 %d" % stats["merged"]
            if stats.get("forgotten"):
                extra += " · 遗忘 %d" % stats["forgotten"]
            if not status_var.get():
                status_var.set(
                    "共 %d 条%s · 全量档案可在「全量档案」页搜回"
                    % (len(items), extra)
                )
            refresh_archive()

        def selected_id():
            selection = tree.selection()
            return selection[0] if selection else ""

        def forget_selected():
            item_id = selected_id()
            if not item_id:
                status_var.set("先在列表里选一条记忆")
                return
            item = self.memory_store.get(item_id)
            if item.get("pinned"):
                status_var.set("置顶的记忆不能直接忘记，先取消置顶")
                return
            if not messagebox.askyesno(
                "忘记这条",
                "让助手忘记「%s」？\n（原文仍留在全量档案里，可以搜回来）"
                % item.get("text", ""),
                parent=win,
            ):
                return
            self.memory_store.forget(item_id)
            status_var.set("已忘记一条")
            refresh()

        def toggle_pin():
            item_id = selected_id()
            if not item_id:
                status_var.set("先在列表里选一条记忆")
                return
            item = self.memory_store.get(item_id)
            self.memory_store.pin(item_id, not item.get("pinned"))
            status_var.set("已置顶" if not item.get("pinned") else "已取消置顶")
            refresh()

        def add_manual():
            text = simpledialog.askstring("记一条", "要助手记住什么？", parent=win)
            if not text or not text.strip():
                return
            self.memory_store.add(text.strip(), "manual", 90)
            status_var.set("已记下")
            refresh()

        def clear_card():
            if not messagebox.askyesno(
                "清空记忆卡",
                "清空当前所有记忆和前情摘要？\n全量档案会保留，但不会再进提示词。",
                parent=win,
            ):
                return
            self.memory_store.clear()
            status_var.set("记忆卡已清空")
            refresh()

        def clear_archive():
            if not messagebox.askyesno(
                "清空全量档案",
                "全量档案是找回已遗忘记忆的唯一备份，确定删除吗？",
                parent=win,
            ):
                return
            try:
                os.remove(self.memory_store.archive_path)
            except FileNotFoundError:
                pass
            except OSError as exc:
                status_var.set("删除失败：%s" % exc)
                return
            status_var.set("全量档案已删除")
            refresh_archive()

        def maintain():
            if self.memory_keeper.busy:
                status_var.set("已经在整理了，稍等")
                return
            status_var.set("正在整理记忆…")
            messages = list(self.chat_session.messages)

            def finish(message):
                if not win.winfo_exists():
                    return
                refresh()
                status_var.set(message)

            def work():
                try:
                    report = self.memory_keeper.maintain(messages, force=True)
                except Exception as exc:
                    message = "整理失败：%s" % exc
                else:
                    mode = {
                        "llm": "模型整理",
                        "fallback": "模型失败，本地兜底",
                        "local": "本地规则",
                        "empty": "没有新内容",
                        "busy": "正忙",
                        "disabled": "记忆已关闭",
                    }.get(report.get("mode"), str(report.get("mode")))
                    message = "整理完成（%s）：新增 %d 条，合并 %d 条" % (
                        mode,
                        report.get("added", 0),
                        report.get("updated", 0),
                    )
                    if report.get("messages"):
                        message += "，读了 %d 条旧消息" % report["messages"]
                self._ui(finish, message)

            threading.Thread(target=work, daemon=True).start()

        def save_flags(*_args):
            self.chat_cfg["memory_enabled"] = bool(enabled_var.get())
            self.chat_cfg["memory_llm"] = bool(llm_var.get())
            try:
                save_chat_config(self.chat_cfg)
            except Exception as exc:
                status_var.set("设置保存失败：%s" % exc)
                return
            status_var.set("记忆已开启" if enabled_var.get() else "记忆已关闭")

        enabled_var.trace_add("write", save_flags)
        llm_var.trace_add("write", save_flags)

        # -- footer switches --------------------------------------------------
        tk.Label(
            footer,
            text="写入记忆",
            bg=C["surface"],
            fg=C["ink"],
            font=_font(9),
        ).pack(side="left", padx=(14, 6), pady=10)
        Switch(footer, enabled_var).pack(side="left", pady=10)
        tk.Label(
            footer,
            text="模型提炼",
            bg=C["surface"],
            fg=C["ink"],
            font=_font(9),
        ).pack(side="left", padx=(14, 6), pady=10)
        Switch(footer, llm_var).pack(side="left", pady=10)
        tk.Label(
            footer,
            textvariable=status_var,
            bg=C["surface"],
            fg=C["muted"],
            font=_font(8),
            anchor="w",
        ).pack(side="left", fill="x", expand=True, padx=(16, 8))
        PillButton(
            footer,
            text="关闭",
            command=win.destroy,
            kind="secondary",
            width=76,
            height=34,
            font_size=8,
            bold=False,
        ).pack(side="right", padx=(0, 14))

        # -- card actions -----------------------------------------------------
        PillButton(
            actions,
            text="立即整理",
            command=maintain,
            kind="primary",
            width=96,
            height=34,
            font_size=8,
        ).pack(side="left")
        PillButton(
            actions,
            text="手动记一条",
            command=add_manual,
            kind="secondary",
            width=108,
            height=34,
            font_size=8,
            bold=False,
        ).pack(side="left", padx=(8, 0))
        PillButton(
            actions,
            text="置顶 / 取消",
            command=toggle_pin,
            kind="ghost",
            width=96,
            height=34,
            font_size=8,
            bold=False,
        ).pack(side="left", padx=(8, 0))
        PillButton(
            actions,
            text="忘记选中",
            command=forget_selected,
            kind="ghost",
            width=96,
            height=34,
            font_size=8,
            bold=False,
        ).pack(side="left", padx=(8, 0))
        PillButton(
            actions,
            text="清空记忆卡",
            command=clear_card,
            kind="danger",
            width=100,
            height=34,
            font_size=8,
            bold=False,
        ).pack(side="right")

        # -- archive actions --------------------------------------------------
        PillButton(
            archive_actions,
            text="清空全量档案",
            command=clear_archive,
            kind="ghost",
            width=112,
            height=32,
            font_size=8,
            bold=False,
        ).pack(side="left")

        refresh()
        query_entry.bind("<Return>", refresh_archive)
        win.bind("<Escape>", lambda _e: win.destroy())
        win.after(30, lambda: win.focus_force())

    # ------------------------------------------------------------------
    # Service state
    # ------------------------------------------------------------------

    def log(self, message):
        append_log(APP_LOG, message)
        text = _ellipsize(message, 68)
        self.status.set(text)

    def init_service(self):
        def worker_log(message):
            self._ui(self.log, message)

        try:
            proc = ensure_service(worker_log)
            if proc is not None:
                self._spawned_pid = proc.pid
            apply_weights()
            # Only the process that started the service needs the first-call
            # warm-up; a reused service already paid it in an earlier run.
            if (
                WARMUP
                and proc is not None
                and "--ui-preview" not in sys.argv
            ):
                tone = self.tones.get(self.default_tone) or next(
                    iter(self.tones.values()), None
                )
                warmup_tts(tone, worker_log)
        except Exception as exc:
            append_log(APP_LOG, traceback.format_exc())
            self._ui(self._on_service_error, str(exc))
            return
        self._ui(self._on_service_ready)


    def _on_service_ready(self):
        self._service_ready = True
        self.set_engine_state("ready")
        self.log("模型加载完成，可以开始合成")
        self.play_btn.set_state("normal")
        self.send_btn.set_state("normal")
        self._set_generation_status(
            "准备就绪", "写下台词，让助手开口吧。", 0.0, "ready"
        )
        self.chat_activity_var.set("本地引擎就绪 · 按 Enter 发送")

    def _on_service_error(self, detail):
        self._service_ready = False
        self.set_engine_state("error")
        self.log(f"服务启动失败：{detail}")
        message = _ellipsize(detail, 150)
        self._set_generation_status(
            "引擎未连接",
            f"{message}\n详细日志：REM sound\\logs\\app.log",
            0.0,
            "error",
        )
        self.chat_activity_var.set("引擎未连接 · 聊天暂不可用")
        self.footer_dot.delete("all")
        self.footer_dot.create_oval(3, 3, 9, 9, fill=C["red"], outline="")

    # ------------------------------------------------------------------
    # Persona housekeeping
    # ------------------------------------------------------------------

    def auto_import_persona(self):
        persona = self.chat_cfg.get("persona", "")
        if not persona.startswith(OLD_PERSONA_PREFIX) or "主人大人" not in persona:
            return
        if not os.path.exists(PERSONA_CARD):
            return
        imported = load_persona_from_card(PERSONA_CARD)
        if imported is None:
            return
        self.chat_cfg["persona"] = apply_names(
            imported, self.chat_cfg["user_name"], self.chat_cfg["user_short"]
        )
        try:
            save_chat_config(self.chat_cfg)
        except OSError:
            pass
        print("已从人设卡导入 persona")

    def ensure_output_format(self):
        persona = self.chat_cfg.get("persona", "")
        if persona and "【输出格式（最高优先级" not in persona:
            self.chat_cfg["persona"] = persona.rstrip() + "\n\n" + OUTPUT_FORMAT
            try:
                save_chat_config(self.chat_cfg)
            except OSError:
                pass

    # ------------------------------------------------------------------
    # Settings dialog
    # ------------------------------------------------------------------

    def open_settings(self):
        win = tk.Toplevel(self)
        win.title("RemVoice · 聊天设置")
        win.configure(bg=C["bg"])
        win.minsize(680, 560)
        win.transient(self)
        self._center_toplevel(win, 760, 710)
        win.grab_set()

        header = tk.Canvas(win, height=86, bg=C["nav"], highlightthickness=0, bd=0)
        header.pack(fill="x")

        def draw_settings_header(event=None):
            c = header
            c.delete("all")
            width = max(600, c.winfo_width() or 760)
            for y in range(0, 86, 2):
                c.create_rectangle(
                    0,
                    y,
                    width,
                    y + 2,
                    fill=_mix("#FBFEFF", "#DFF0FA", y / 86),
                    outline="",
                )
            c.create_oval(
                width - 260, -110, width - 40, 130, outline="#D3EAF7"
            )
            c.create_oval(
                width - 190, -60, width + 30, 160, outline="#E0F0F8"
            )
            c.create_oval(28, 20, 72, 64, fill="#FFFFFF", outline="#CFE6F2")
            if self._avatar_img is not None:
                c.create_image(50, 42, image=self._avatar_img)
            c.create_text(
                86,
                30,
                text="聊天设置",
                anchor="w",
                fill=C["ink"],
                font=_font(18, bold=True),
            )
            c.create_text(
                86,
                57,
                text="连接、称呼与助手的说话方式",
                anchor="w",
                fill=C["muted"],
                font=_font(9),
            )
            c.create_text(
                width - 28,
                43,
                text="SETTINGS  /  CONNECTION",
                anchor="e",
                fill="#9AB2BE",
                font=(FONT_DISPLAY, 7, "bold"),
            )
            c.create_line(0, 85, width, 85, fill="#DCEBF4")

        header.bind("<Configure>", draw_settings_header)

        footer = tk.Frame(win, bg=C["surface"], height=66)
        footer.pack(side="bottom", fill="x")
        footer.pack_propagate(False)
        tk.Frame(footer, bg=C["line_soft"], height=1).pack(fill="x", side="top")
        tk.Label(
            footer,
            text="API Key 只保存在本机，不会上传到别处",
            bg=C["surface"],
            fg=C["muted"],
            font=_font(8),
        ).pack(side="left", padx=(24, 0))

        content = ScrollArea(win, bg=C["bg"])
        content.pack(fill="both", expand=True)
        body = content.body

        def group(title, desc):
            card = Card(body)
            card.pack(fill="x", padx=20, pady=(14, 0))
            head = tk.Frame(card, bg=C["surface"])
            head.pack(fill="x", padx=18, pady=(13, 7))
            tk.Label(
                head,
                text=title,
                bg=C["surface"],
                fg=C["ink"],
                font=_font(10, bold=True),
            ).pack(side="left")
            tk.Label(
                head,
                text=desc,
                bg=C["surface"],
                fg=C["muted"],
                font=_font(8),
            ).pack(side="right", pady=(1, 0))
            inner = tk.Frame(card, bg=C["surface"])
            inner.pack(fill="x", padx=18, pady=(0, 15))
            return inner

        def field_label(parent, text, hint=None):
            row = tk.Frame(parent, bg=C["surface"])
            row.pack(fill="x", pady=(0, 3))
            tk.Label(
                row,
                text=text,
                bg=C["surface"],
                fg=C["ink_soft"],
                font=_font(8, bold=True),
            ).pack(side="left")
            if hint:
                tk.Label(
                    row,
                    text=hint,
                    bg=C["surface"],
                    fg=C["faint"],
                    font=_font(7),
                ).pack(side="right")
            return row

        def entry(parent, variable, show=None, width=None):
            widget = tk.Entry(
                parent,
                textvariable=variable,
                show=show,
                width=width,
                bg=C["surface_soft"],
                fg=C["ink"],
                insertbackground=C["blue_deep"],
                relief="flat",
                bd=0,
                highlightthickness=1,
                highlightbackground=C["line"],
                highlightcolor=C["blue"],
                font=_font(9),
            )
            widget.pack(fill="x" if width is None else None, ipady=6, pady=(0, 9))
            return widget

        conn = group("连接设置", "DEEPSEEK API")
        field_label(conn, "API Base")
        base_var = tk.StringVar(value=self.chat_cfg["api_base"])
        entry(conn, base_var)
        field_label(conn, "API Key", "本地保存")
        key_var = tk.StringVar(value=self.chat_cfg["api_key"])
        entry(conn, key_var, show="*")
        field_label(conn, "模型", "支持手动填写")
        model_row = tk.Frame(conn, bg=C["surface"])
        model_row.pack(fill="x", pady=(2, 0))
        model_var = tk.StringVar(value=self.chat_cfg["model"])
        cache = self.chat_cfg.get("models_cache") or []
        model_box = ttk.Combobox(
            model_row,
            textvariable=model_var,
            values=cache if cache else [model_var.get()],
            font=_font(9),
        )
        model_box.pack(side="left", fill="x", expand=True, ipady=1)

        def do_fetch():
            fetch_btn.set_state("disabled")
            fetch_btn.set_text("获取中…")
            win.update_idletasks()
            try:
                ids = fetch_models(base_var.get(), key_var.get())
            except ModelsError as exc:
                messagebox.showerror("拉取失败", str(exc), parent=win)
                return
            finally:
                fetch_btn.set_state("normal")
                fetch_btn.set_text("拉取列表")
            model_box.configure(values=ids)
            current = model_var.get()
            model_var.set(current if current in ids else ids[0])
            self.chat_cfg["models_cache"] = ids
            self.chat_cfg["models_fetched_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            try:
                save_chat_config(self.chat_cfg)
            except OSError as exc:
                messagebox.showerror(
                    "保存设置失败",
                    f"模型列表已获取，但配置没有写入：\n{exc}\n\n"
                    f"目标：{CHAT_CONFIG}",
                    parent=win,
                )
                return
            messagebox.showinfo("成功", f"已获取 {len(ids)} 个模型", parent=win)

        fetch_btn = PillButton(
            model_row,
            text="拉取列表",
            command=do_fetch,
            kind="secondary",
            width=96,
            height=34,
            font_size=8,
            bold=False,
        )
        fetch_btn.pack(side="left", padx=(8, 0))

        trans = group("翻译服务（可选）", "LOCAL / OPENAI-COMPATIBLE")
        field_label(trans, "翻译 API Base", "留空使用上面的 DeepSeek")
        trans_base_var = tk.StringVar(
            value=self.chat_cfg.get("translate_api_base", "")
        )
        entry(trans, trans_base_var)
        field_label(trans, "翻译模型", "例如 qwen2.5:7b")
        trans_model_var = tk.StringVar(
            value=self.chat_cfg.get("translate_model", "")
        )
        entry(trans, trans_model_var)
        field_label(trans, "翻译 API Key", "本地服务可留空")
        trans_key_var = tk.StringVar(
            value=self.chat_cfg.get("translate_api_key", "")
        )
        entry(trans, trans_key_var, show="*")
        tk.Label(
            trans,
            text="本地示例：Ollama http://127.0.0.1:11434/v1 或 LM Studio http://127.0.0.1:1234/v1",
            bg=C["surface"],
            fg=C["faint"],
            font=_font(7),
            wraplength=640,
            justify="left",
        ).pack(anchor="w", pady=(0, 4))

        roles = group("角色称呼", "PERSONA NAMES")
        role_row = tk.Frame(roles, bg=C["surface"])
        role_row.pack(fill="x")
        role_row.grid_columnconfigure(0, weight=1)
        role_row.grid_columnconfigure(1, weight=1)
        left_role = tk.Frame(role_row, bg=C["surface"])
        left_role.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        right_role = tk.Frame(role_row, bg=C["surface"])
        right_role.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        field_label(left_role, "称呼")
        name_var = tk.StringVar(value=self.chat_cfg.get("user_name", "旅行者君"))
        entry(left_role, name_var)
        field_label(right_role, "短称呼")
        short_var = tk.StringVar(value=self.chat_cfg.get("user_short", "旅行者君"))
        entry(right_role, short_var)

        common = group("常用选项", "VOICE & REPLY")
        punct_var = tk.BooleanVar(value=self.chat_cfg.get("auto_punct", True))
        punct_row = tk.Frame(common, bg=C["surface"])
        punct_row.pack(fill="x", pady=(0, 10))
        tk.Label(
            punct_row,
            text="自动断句标点",
            bg=C["surface"],
            fg=C["ink_soft"],
            font=_font(9, bold=True),
        ).pack(side="left")
        tk.Label(
            punct_row,
            text="只影响朗读，不改聊天文字",
            bg=C["surface"],
            fg=C["faint"],
            font=_font(8),
        ).pack(side="left", padx=(8, 0), pady=(1, 0))
        Switch(punct_row, punct_var).pack(side="right")
        temp_row = tk.Frame(common, bg=C["surface"])
        temp_row.pack(fill="x")
        tk.Label(
            temp_row,
            text="温度（temperature）",
            bg=C["surface"],
            fg=C["ink_soft"],
            font=_font(8, bold=True),
        ).pack(side="left")
        temp_var = tk.StringVar(value=str(self.chat_cfg.get("temperature", 0.8)))
        temp_entry = tk.Entry(
            temp_row,
            textvariable=temp_var,
            width=8,
            bg=C["surface_soft"],
            fg=C["ink"],
            insertbackground=C["blue_deep"],
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=C["line"],
            highlightcolor=C["blue"],
            font=_font(9),
            justify="center",
        )
        temp_entry.pack(side="left", padx=(10, 0), ipady=4)
        tk.Label(
            temp_row,
            text="越高越有变化，0.7 – 1.0 较适合日常对话",
            bg=C["surface"],
            fg=C["faint"],
            font=_font(7),
        ).pack(side="left", padx=(10, 0))

        advanced_box = group("高级设置", "PERSONA PROMPT")
        advanced = tk.Frame(advanced_box, bg=C["surface"])
        toggle_btn = PillButton(
            advanced_box,
            text="展开人设文本 ▾",
            kind="ghost",
            width=144,
            height=34,
            font_size=8,
            bold=False,
        )
        toggle_btn.pack(anchor="w")
        persona_shell = tk.Frame(advanced, bg=C["surface"])
        persona_shell.pack(fill="x")
        persona_text = tk.Text(
            persona_shell,
            height=12,
            width=1,
            wrap="word",
            font=_font(8),
            bg=C["surface_soft"],
            fg=C["ink_soft"],
            insertbackground=C["blue_deep"],
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=C["line"],
            highlightcolor=C["blue"],
            padx=10,
            pady=8,
        )
        persona_text.pack(side="left", fill="both", expand=True)
        persona_scroll = ttk.Scrollbar(
            persona_shell,
            orient="vertical",
            command=persona_text.yview,
            style="Soft.Vertical.TScrollbar",
        )
        persona_scroll.pack(side="right", fill="y")
        persona_text.configure(yscrollcommand=persona_scroll.set)
        persona_text.insert("1.0", self.chat_cfg["persona"])
        persona_tools = tk.Frame(advanced, bg=C["surface"])
        persona_tools.pack(fill="x", pady=(6, 0))

        def do_import():
            imported = load_persona_from_card(PERSONA_CARD)
            if imported is None:
                messagebox.showerror(
                    "导入失败", "人设卡文件不存在或格式错误", parent=win
                )
                return
            persona_text.delete("1.0", "end")
            persona_text.insert(
                "1.0", apply_names(imported, name_var.get(), short_var.get())
            )
            messagebox.showinfo("导入成功", "人设卡已导入", parent=win)

        PillButton(
            persona_tools,
            text="从人设卡导入",
            command=do_import,
            kind="secondary",
            width=112,
            height=32,
            font_size=8,
            bold=False,
        ).pack(side="left")
        tk.Label(
            advanced,
            text="点「保存设置」后生效；建议保留末尾「输出格式」段落，否则动作与台词可能混在一起。",
            bg=C["surface"],
            fg=C["faint"],
            font=_font(7),
            wraplength=640,
            justify="left",
        ).pack(anchor="w", pady=(6, 0))

        def toggle_advanced():
            if advanced.winfo_ismapped():
                advanced.pack_forget()
                toggle_btn.set_text("展开人设文本 ▾")
            else:
                advanced.pack(fill="x", pady=(8, 0), after=toggle_btn)
                toggle_btn.set_text("收起人设文本 ▴")
                win.after(60, content._on_body_configure)

        toggle_btn._command = toggle_advanced

        def do_save():
            self.chat_cfg["api_base"] = (
                base_var.get().strip() or DEFAULT_CHAT_CONFIG["api_base"]
            )
            self.chat_cfg["api_key"] = key_var.get().strip()
            self.chat_cfg["model"] = (
                model_var.get().strip() or DEFAULT_CHAT_CONFIG["model"]
            )
            self.chat_cfg["auto_punct"] = bool(punct_var.get())
            self.chat_cfg["voice_lang"] = self.voice_lang_var.get()
            self.chat_cfg["translate_api_base"] = trans_base_var.get().strip()
            self.chat_cfg["translate_model"] = trans_model_var.get().strip()
            self.chat_cfg["translate_api_key"] = trans_key_var.get().strip()
            try:
                self.chat_cfg["temperature"] = float(temp_var.get())
            except ValueError:
                self.chat_cfg["temperature"] = 0.8
            old_name = self.chat_cfg.get("user_name", "旅行者君")
            old_short = self.chat_cfg.get("user_short", "旅行者君")
            new_name = name_var.get().strip() or "旅行者君"
            new_short = short_var.get().strip() or "旅行者君"
            persona = persona_text.get("1.0", "end").strip()
            if persona:
                persona = persona.replace(old_name, new_name).replace(
                    old_short, new_short
                )
            else:
                persona = apply_names(PERSONA, new_name, new_short)
            self.chat_cfg["persona"] = persona
            self.chat_cfg["user_name"] = new_name
            self.chat_cfg["user_short"] = new_short
            try:
                save_chat_config(self.chat_cfg)
            except OSError as exc:
                messagebox.showerror(
                    "保存设置失败",
                    f"配置没有写入：\n{exc}\n\n目标：{CHAT_CONFIG}",
                    parent=win,
                )
                return
            self._refresh_chat_labels()
            self.chat_view.user_label = new_name
            self.chat_activity_var.set("设置已保存 · 按 Enter 发送")
            self.log("聊天设置已保存")
            win.destroy()

        cancel_btn = PillButton(
            footer,
            text="取消",
            command=win.destroy,
            kind="ghost",
            width=76,
            height=36,
            font_size=9,
            bold=False,
        )
        cancel_btn.pack(side="right", padx=(0, 20), pady=14)
        save_btn = PillButton(
            footer,
            text="保存设置",
            command=do_save,
            kind="primary",
            width=112,
            height=36,
            font_size=9,
        )
        save_btn.pack(side="right", padx=(0, 6), pady=14)

        win.bind("<Escape>", lambda _e: win.destroy())
        win.after(30, lambda: win.focus_force())

    def _center_toplevel(self, win, width, height):
        win.update_idletasks()
        x = self.winfo_rootx() + max(0, (self.winfo_width() - width) // 2)
        y = self.winfo_rooty() + max(0, (self.winfo_height() - height) // 2)
        win.geometry(f"{width}x{height}+{x}+{y}")

    def on_close(self):
        self._cancel_voice_timer()
        if self.audio_player is not None:
            self.audio_player.stop()
        self.chat_stop.set()
        if self.asr is not None:
            try:
                self.asr.close()
            except Exception:
                pass
            self.asr = None
        if not KEEP_ENGINE_RUNNING and self._spawned_pid and port_open():
            stop_service(self._spawned_pid)
        self.destroy()



def selftest():
    log_path = os.path.join(APP_DIR, "selftest_log.txt")
    messages = []

    def log(msg):
        messages.append(time.strftime("%H:%M:%S") + " " + str(msg))

    os.makedirs(SAVE_DIR, exist_ok=True)
    ensure_service(log)
    apply_weights()
    tones, default = load_tones()
    for lang, text in [("zh", "你好，很高兴认识你。"), ("ja", "今日も一日、頑張りましょうね。")]:
        data = synth(text, tones[default], SPEED)
        path = save_audio(data)
        log(f"OK {lang}: {path}")
        try:
            play_wav(path)
            log("playback attempted")
        except Exception as e:
            log(f"playback skipped: {e}")

    cfg = load_chat_config()
    persona_from_card = load_persona_from_card(PERSONA_CARD)
    if persona_from_card is None:
        log("人设卡检查失败：文件缺失或格式错误")
    else:
        ok = (
            "罗兹瓦尔宅邸" in persona_from_card
            and "旅行者君" in persona_from_card
            and "鬼族" in persona_from_card
            and "主人大人" not in persona_from_card
        )
        log("人设卡检查：" + ("通过" if ok else "不通过"))

    parse_cases = [
        (
            "动作行+台词行",
            "（红着脸低下头，攥着围裙衣角）\n旅行者君，助手只是做了分内的事。",
            1,
            "旅行者君，助手只是做了分内的事。",
        ),
        (
            "行内括号片段移除",
            "（笑）旅行者君早。",
            0,
            "旅行者君早。",
        ),
        (
            "纯台词兼容",
            "早安，旅行者君。",
            0,
            "早安，旅行者君。",
        ),
        (
            "纯动作",
            "（只是安静地看着他）",
            1,
            "",
        ),
    ]
    for name, sample, exp_actions, exp_speech in parse_cases:
        actions, speech = parse_reply(sample)
        passed = len(actions) == exp_actions and speech == exp_speech
        log(f"parse 自测 [{name}]：{'PASS' if passed else 'FAIL'} actions={actions} speech={speech!r}")

    punct_cases = [
        ("累不累", "旅行者君今天累不累", "旅行者君今天累不累？"),
        ("连词逗号", "今天天气很好但是有点冷", "今天天气很好，但是有点冷。"),
        ("句号补全", "旅行者君早安", "旅行者君早安。"),
        ("原样返回", "旅行者君，助手永远相信你！", "旅行者君，助手永远相信你！"),
        ("名字保护", "爱蜜莉雅大人和拉姆姐姐都在厨房", "爱蜜莉雅大人和拉姆姐姐都在厨房。"),
        ("空串", "", ""),
        ("纯空格", "   ", "   "),
    ]
    for name, sample, expected in punct_cases:
        out = smart_punct(sample)
        passed = out == expected
        log(f"smart_punct 自测 [{name}]：{'PASS' if passed else 'FAIL'} out={out!r} expected={expected!r}")

    if not cfg["api_key"].strip():
        log("聊天自测跳过：未配置 API Key")
    else:
        try:
            messages_req = [{"role": "system", "content": cfg["persona"]}, {"role": "user", "content": "你好"}]
            reply = llm_chat(messages_req, cfg)
            actions, speech = parse_reply(reply)
            speech = clean_reply(speech)
            if cfg.get("auto_punct", True):
                speech = smart_punct(speech)
            log(f"LLM 回复 actions={actions}")
            log(f"LLM 回复 speech={speech}")
            if speech:
                data = synth(speech, tones[default], SPEED)
                path = save_audio(data, prefix="聊天_")
                log(f"聊天链路 OK：{path}")
                play_wav(path)
            else:
                log("聊天链路：本条只有动作，跳过 TTS")
        except Exception as e:
            log(f"聊天链路失败：{e}")

    if not cfg["api_key"].strip():
        log("fetch_models 自测跳过（无 key）")
    else:
        try:
            ids = fetch_models(cfg["api_base"], cfg["api_key"])
            log(f"fetch_models OK，共 {len(ids)} 个，前 3 个：{ids[:3]}")
        except ModelsError as e:
            log(f"fetch_models 失败：{e}")

    if not os.path.exists(CHAT_CONFIG):
        save_chat_config(DEFAULT_CHAT_CONFIG)
        log("已生成 chat_config.json 模板")
    with open(log_path, "w", encoding="utf-8") as f:
        f.write("\n".join(messages))
    return 0


def _acquire_single_instance():
    """Return a mutex handle, or None when another instance is running.

    Two windows would race on chat history/config and could both try to own
    port 9880, so the packaged app is single-instance by default.  Pass
    ``--allow-multiple`` (or ``--ui-preview``) while developing.
    """
    if "--allow-multiple" in sys.argv or "--ui-preview" in sys.argv:
        return None
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    handle = kernel32.CreateMutexW(None, False, "Local\\RemVoice.SingleInstance")
    if not handle:
        return None
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        kernel32.CloseHandle(handle)
        ctypes.windll.user32.MessageBoxW(
            0, "RemVoice 已经在运行了。", "RemVoice", 0x40
        )
        raise SystemExit(0)
    return handle


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    if "--ui-preview" in sys.argv:
        ensure_service = lambda log=print: log("UI 预览模式：未连接本地推理服务")
        apply_weights = lambda: None
    _MUTEX_HANDLE = _acquire_single_instance()
    app = App()
    app.mainloop()
