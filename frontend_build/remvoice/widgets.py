# -*- coding: utf-8 -*-
"""Custom Tk widgets used by the RemVoice shell."""
import os
import re
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

from .theme import (
    C,
    FONT_DISPLAY,
    FONT_UI,
    _font,
    _round_rect,
    _set_bg_tree,
    install_rounded_corners,
)

class Card(tk.Frame):
    """A soft iOS-style surface with rounded corners."""

    def __init__(self, parent, bg=None, border=None, **kwargs):
        super().__init__(
            parent,
            bg=bg or C["surface"],
            bd=0,
            highlightthickness=0,
            **kwargs,
        )
        self._card_bg = bg or C["surface"]
        self.after_idle(
            lambda: install_rounded_corners(self, 16, self._card_bg)
            if self.winfo_exists()
            else None
        )


class Badge(tk.Canvas):
    def __init__(self, parent, text, width=None, height=28, fg=None, fill=None, bg=None):
        self.font = tkfont.Font(family=FONT_UI, size=8, weight="bold")
        width = width or self.font.measure(text) + 28
        parent_bg = bg or parent.cget("bg")
        super().__init__(
            parent,
            width=width,
            height=height,
            bg=parent_bg,
            highlightthickness=0,
            bd=0,
        )
        _round_rect(
            self,
            1,
            1,
            width - 1,
            height - 1,
            height / 2,
            fill=fill or C["ice"],
            outline="",
        )
        self.create_text(
            width / 2,
            height / 2,
            text=text,
            fill=fg or C["blue_dark"],
            font=self.font,
        )


class PillButton(tk.Canvas):
    """Canvas button with a rounded background and hover/disabled states."""

    _STYLES = {
        "primary": {
            "fill": C["blue_deep"],
            "hover": "#216684",
            "press": "#1A5874",
            "fg": "#FFFFFF",
            "border": C["blue_deep"],
            "disabled": "#C8DFEB",
            "disabled_fg": "#F8FCFE",
        },
        "secondary": {
            "fill": "#F1F8FC",
            "hover": "#E6F3FA",
            "press": "#DBEDF7",
            "fg": C["blue_dark"],
            "border": "#F1F8FC",
            "disabled": "#F5F9FC",
            "disabled_fg": C["faint"],
        },
        "ghost": {
            "fill": None,
            "hover": "#EDF7FC",
            "press": "#E1F1F9",
            "fg": C["ink_soft"],
            "border": None,
            "disabled": None,
            "disabled_fg": C["faint"],
        },
        "danger": {
            "fill": "#FCEFF0",
            "hover": "#F9E4E5",
            "press": "#F4D8DA",
            "fg": C["red"],
            "border": "#FCEFF0",
            "disabled": "#F8F3F3",
            "disabled_fg": C["faint"],
        },
    }

    def __init__(
        self,
        parent,
        text="",
        command=None,
        kind="primary",
        width=140,
        height=40,
        font_size=10,
        bold=True,
        icon=None,
        bg=None,
        press_command=None,
        release_command=None,
    ):
        parent_bg = bg or parent.cget("bg")
        super().__init__(
            parent,
            width=width,
            height=height,
            bg=parent_bg,
            highlightthickness=0,
            bd=0,
            cursor="hand2",
            takefocus=1,
        )
        self._text = text
        self._command = command
        self._press_command = press_command
        self._release_command = release_command
        self._kind = kind
        self._cw = width
        self._ch = height
        self._font = tkfont.Font(
            family=FONT_UI, size=font_size, weight="bold" if bold else "normal"
        )
        self._icon = icon
        self._enabled = True
        self._hover = False
        self._pressed = False
        self._bg = parent_bg
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Return>", lambda _e: self._activate())
        self.bind("<space>", lambda _e: self._activate())
        self.bind("<FocusIn>", lambda _e: self._draw(focus=True))
        self.bind("<FocusOut>", lambda _e: self._draw())
        self._draw()

    def _colors(self, focus=False):
        style = self._STYLES[self._kind]
        if not self._enabled:
            fill = style["disabled"] if style["disabled"] is not None else self._bg
            border = style["border"] if style["border"] is not None else fill
            if self._kind == "secondary":
                border = fill
            elif self._kind == "ghost":
                border = fill
            return fill or self._bg, style["disabled_fg"], border, self._bg
        fill = style["fill"] if style["fill"] is not None else self._bg
        border = style["border"] if style["border"] is not None else fill
        if self._pressed:
            fill = style["press"]
            border = style["press"]
        elif self._hover:
            fill = style["hover"]
            border = style["hover"]
        if focus and self._kind != "primary":
            border = C["blue"]
        return fill, style["fg"], border, self._bg

    def _draw(self, focus=False):
        self.delete("all")
        fill, fg, border, _ = self._colors(focus)
        _round_rect(
            self,
            1,
            1,
            self._cw - 1,
            self._ch - 1,
            (self._ch - 2) / 2,
            fill=fill,
            outline=border,
            width=1,
        )
        text_w = self._font.measure(self._text)
        offset = 9 if self._icon else 0
        cx = self._cw / 2 + offset
        cy = self._ch / 2
        if self._icon == "play":
            ix = cx - text_w / 2 - 18
            self.create_polygon(
                ix - 4,
                cy - 5,
                ix - 4,
                cy + 5,
                ix + 5,
                cy,
                fill=fg,
                outline="",
            )
        elif self._icon == "open":
            ix = cx - text_w / 2 - 19
            self.create_rectangle(
                ix - 6, cy - 1, ix + 5, cy + 6, outline=fg, width=1
            )
            self.create_line(ix - 6, cy - 1, ix - 3, cy - 5, ix + 2, cy - 5,
                             ix + 3, cy - 1, fill=fg, width=1)
        elif self._icon == "replay":
            ix = cx - text_w / 2 - 17
            self.create_arc(
                ix - 5,
                cy - 5,
                ix + 5,
                cy + 5,
                start=35,
                extent=285,
                style="arc",
                outline=fg,
                width=1,
            )
            self.create_polygon(
                ix + 1,
                cy - 7,
                ix + 7,
                cy - 4,
                ix + 1,
                cy - 1,
                fill=fg,
                outline="",
            )
        self.create_text(
            cx,
            cy,
            text=self._text,
            fill=fg,
            font=self._font,
            anchor="center",
        )

    def _on_enter(self, _event):
        if self._enabled:
            self._hover = True
            self._draw()

    def _on_leave(self, _event):
        if self._pressed and self._release_command is not None:
            try:
                self._release_command()
            except Exception:
                pass
        self._hover = False
        self._pressed = False
        self._draw()

    def _on_press(self, _event):
        if not self._enabled:
            return "break"
        self.focus_set()
        self._pressed = True
        if self._press_command is not None:
            try:
                self._press_command()
            except Exception:
                pass
        self._draw()
        return "break"

    def _on_release(self, event):
        if not self._enabled:
            return "break"
        was_pressed = self._pressed
        self._pressed = False
        if was_pressed and self._release_command is not None:
            try:
                self._release_command()
            except Exception:
                pass
        inside = 0 <= event.x <= self._cw and 0 <= event.y <= self._ch
        self._draw()
        if was_pressed and inside:
            self._activate()
        return "break"

    def _activate(self):
        if self._enabled and self._command:
            self._command()

    def set_state(self, state):
        enabled = state not in ("disabled", "disable", "off")
        if enabled == self._enabled:
            return
        self._enabled = enabled
        self._hover = False
        self._pressed = False
        self.configure(cursor="hand2" if enabled else "arrow")
        self._draw()

    def set_text(self, text):
        self._text = text
        self._draw()

    def set_enabled(self, enabled):
        self.set_state("normal" if enabled else "disabled")


class NavItem(tk.Canvas):
    def __init__(self, parent, label, icon, command, index="01", width=200, height=46):
        parent_bg = parent.cget("bg")
        super().__init__(
            parent,
            width=width,
            height=height,
            bg=parent_bg,
            highlightthickness=0,
            bd=0,
            cursor="hand2",
        )
        self._cw = width
        self._ch = height
        self._label = label
        self._icon = icon
        self._command = command
        self._index = index
        self._active = False
        self._hover = False
        self._bg = parent_bg
        self.bind("<ButtonRelease-1>", lambda _e: self._command())
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self._draw()

    def _on_enter(self, _event):
        self._hover = True
        self._draw()

    def _on_leave(self, _event):
        self._hover = False
        self._draw()

    def set_active(self, active):
        self._active = active
        self._draw()

    def _draw(self):
        self.delete("all")
        fill = C["nav_active"] if self._active else (C["nav_soft"] if self._hover else self._bg)
        _round_rect(
            self, 1, 1, self._cw - 1, self._ch - 1, 13, fill=fill, outline=fill
        )

        fg = C["blue_deep"] if self._active else C["nav_text"]
        icon_color = C["blue_dark"] if self._active else C["nav_muted"]
        cx, cy = 24, self._ch / 2
        if self._icon == "wave":
            for i, height in enumerate((9, 16, 23, 15, 7)):
                x = cx - 8 + i * 4
                self.create_line(
                    x,
                    cy - height / 2,
                    x,
                    cy + height / 2,
                    fill=icon_color,
                    width=2,
                    capstyle="round",
                )
        else:
            _round_rect(
                self,
                cx - 9,
                cy - 8,
                cx + 9,
                cy + 5,
                5,
                fill="",
                outline=icon_color,
                width=2,
            )
            self.create_line(
                cx - 4,
                cy + 5,
                cx - 7,
                cy + 10,
                cx,
                cy + 5,
                fill=icon_color,
                width=2,
            )
        self.create_text(
            44,
            cy,
            text=self._label,
            anchor="w",
            fill=fg,
            font=_font(10, bold=self._active),
        )
        self.create_text(
            self._cw - 15,
            cy,
            text=self._index,
            anchor="e",
            fill=C["blue_dark"] if self._active else "#B0C2CC",
            font=_font(8, bold=True, family=FONT_DISPLAY),
        )


class ToneChip(tk.Canvas):
    def __init__(self, parent, key, label, variable):
        self._font = tkfont.Font(family=FONT_UI, size=9)
        width = max(74, self._font.measure(label) + 34)
        super().__init__(
            parent,
            width=width,
            height=34,
            bg=parent.cget("bg"),
            highlightthickness=0,
            bd=0,
            cursor="hand2",
        )
        self._key = key
        self._label = label
        self._var = variable
        self._cw = width
        self._ch = 34
        self._hover = False
        self._bg = parent.cget("bg")
        self.bind("<ButtonRelease-1>", self._choose)
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))
        self._var.trace_add("write", lambda *_args: self._draw())
        self._draw()

    def _set_hover(self, hover):
        self._hover = hover
        self._draw()

    def _choose(self, _event):
        self._var.set(self._key)

    def _draw(self):
        self.delete("all")
        selected = self._var.get() == self._key
        fill = C["blue_deep"] if selected else ("#F1F9FD" if self._hover else C["surface"])
        outline = C["blue_deep"] if selected else C["line"]
        fg = "#FFFFFF" if selected else C["ink_soft"]
        _round_rect(
            self, 1, 1, self._cw - 1, self._ch - 1, 17, fill=fill, outline=outline
        )
        if selected:
            self.create_oval(14, 15, 20, 21, fill="#BFE7F8", outline="")
            self.create_text(
                27,
                self._ch / 2,
                text=self._label,
                anchor="w",
                fill=fg,
                font=_font(9, bold=True),
            )
        else:
            self.create_text(
                self._cw / 2,
                self._ch / 2,
                text=self._label,
                fill=fg,
                font=self._font,
            )


class ToneMenu(tk.Canvas):
    """Compact tone picker for the chat header."""

    def __init__(self, parent, variable, tones, width=156, height=34, title="语气"):
        super().__init__(
            parent,
            width=width,
            height=height,
            bg=parent.cget("bg"),
            highlightthickness=0,
            bd=0,
            cursor="hand2",
        )
        self._var = variable
        self._tones = tones
        self._title = title
        self._cw = width
        self._ch = height
        self._bg = parent.cget("bg")
        self._hover = False
        self._font = tkfont.Font(family=FONT_UI, size=9)
        self.bind("<ButtonRelease-1>", self._open_menu)
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))
        self._var.trace_add("write", lambda *_args: self._draw())
        self._draw()

    def _set_hover(self, hover):
        self._hover = hover
        self._draw()

    def _current_label(self):
        meta = self._tones.get(self._var.get(), {})
        return _tone_short(meta.get("label", self._var.get()))

    def _draw(self):
        self.delete("all")
        fill = C["ice_soft"] if self._hover else C["surface"]
        _round_rect(
            self, 1, 1, self._cw - 1, self._ch - 1, (self._ch - 2) / 2,
            fill=fill, outline=C["line"]
        )
        self.create_text(
            12,
            self._ch / 2,
            text=self._title,
            anchor="w",
            fill=C["muted"],
            font=_font(8),
        )
        self.create_text(
            41,
            self._ch / 2,
            text=self._current_label(),
            anchor="w",
            fill=C["ink_soft"],
            font=self._font,
        )
        self.create_line(
            self._cw - 22,
            self._ch / 2 - 2,
            self._cw - 17,
            self._ch / 2 + 3,
            self._cw - 12,
            self._ch / 2 - 2,
            fill=C["muted"],
            width=1,
        )

    def _open_menu(self, _event):
        menu = tk.Menu(
            self,
            tearoff=0,
            bg=C["surface"],
            fg=C["ink"],
            activebackground=C["ice"],
            activeforeground=C["ink"],
            bd=0,
            relief="flat",
            font=_font(10),
        )
        for key, meta in self._tones.items():
            menu.add_radiobutton(
                label=_tone_short(meta.get("label", key)),
                variable=self._var,
                value=key,
            )
        try:
            menu.tk_popup(self.winfo_rootx(), self.winfo_rooty() + self._ch + 2)
        finally:
            menu.grab_release()


class Switch(tk.Canvas):
    """Small iOS-style toggle bound to a BooleanVar."""

    def __init__(self, parent, variable, width=48, height=28):
        super().__init__(
            parent,
            width=width,
            height=height,
            bg=parent.cget("bg"),
            highlightthickness=0,
            bd=0,
            cursor="hand2",
        )
        self._cw = width
        self._ch = height
        self._var = variable
        self._var.trace_add("write", lambda *_args: self._draw())
        self.bind("<ButtonRelease-1>", lambda _e: self._var.set(not self._var.get()))
        self._draw()

    def _draw(self):
        self.delete("all")
        on = bool(self._var.get())
        _round_rect(
            self,
            1,
            1,
            self._cw - 1,
            self._ch - 1,
            (self._ch - 2) / 2,
            fill=C["blue_deep"] if on else "#D7E3EB",
            outline="",
        )
        knob_r = self._ch / 2 - 3
        cy = self._ch / 2
        cx = self._cw - knob_r - 4 if on else knob_r + 4
        self.create_oval(
            cx - knob_r,
            cy - knob_r,
            cx + knob_r,
            cy + knob_r,
            fill="#FFFFFF",
            outline="",
        )


def _tone_short(label):
    return str(label).split("（")[0].strip()


class EngineCard(tk.Canvas):
    def __init__(self, parent, width=200, height=94):
        super().__init__(
            parent,
            width=width,
            height=height,
            bg=parent.cget("bg"),
            highlightthickness=0,
            bd=0,
        )
        self._cw = width
        self._ch = height
        self._state = "loading"
        self._text = "正在唤醒引擎"
        self._draw()

    def set_state(self, state, text=None):
        self._state = state
        self._text = text or {
            "loading": "正在唤醒引擎",
            "ready": "本地引擎就绪",
            "thinking": "正在思考",
            "speaking": "正在说话",
            "error": "引擎未连接",
        }.get(state, state)
        self._draw()

    def _draw(self):
        self.delete("all")
        _round_rect(
            self,
            1,
            1,
            self._cw - 1,
            self._ch - 1,
            16,
            fill=C["surface"],
            outline="",
        )
        color = {
            "loading": C["gold"],
            "ready": C["green"],
            "thinking": C["blue"],
            "speaking": C["blue"],
            "error": C["red"],
        }.get(self._state, C["blue"])
        self.create_text(
            16,
            16,
            text="LOCAL ENGINE",
            anchor="w",
            fill=C["faint"],
            font=_font(7, bold=True, family="Segoe UI"),
        )
        self.create_oval(16, 29, 24, 37, fill=color, outline="")
        self.create_text(
            32,
            33,
            text=self._text,
            anchor="w",
            fill=C["ink_soft"],
            font=_font(9, bold=True),
        )
        self.create_line(16, 49, self._cw - 16, 49, fill=C["line_soft"], width=1)
        self.create_text(
            16,
            64,
            text="SPEED",
            anchor="w",
            fill=C["faint"],
            font=_font(7, bold=True, family="Segoe UI"),
        )
        self.create_text(
            56,
            64,
            text="0.85",
            anchor="w",
            fill=C["ink_soft"],
            font=_font(8, family="Segoe UI"),
        )
        self.create_text(
            104,
            64,
            text="TOP-K",
            anchor="w",
            fill=C["faint"],
            font=_font(7, bold=True, family="Segoe UI"),
        )
        self.create_text(
            144,
            64,
            text="15",
            anchor="w",
            fill=C["ink_soft"],
            font=_font(8, family="Segoe UI"),
        )
        self.create_text(
            16,
            80,
            text="127.0.0.1:9880",
            anchor="w",
            fill=C["faint"],
            font=_font(7, family="Consolas"),
        )


class PortraitCard(tk.Canvas):
    """Rem artwork in the sidebar; the PNG already has rounded corners."""

    def __init__(self, parent, image, width=200, height=280):
        super().__init__(
            parent,
            width=width,
            height=height,
            bg=parent.cget("bg"),
            highlightthickness=0,
            bd=0,
        )
        self.create_image(0, 0, anchor="nw", image=image)


class VoiceCard(tk.Canvas):
    """Small, quiet identity card in the sidebar."""

    def __init__(self, parent, width=178, height=128):
        super().__init__(
            parent,
            width=width,
            height=height,
            bg=parent.cget("bg"),
            highlightthickness=0,
            bd=0,
        )
        self._cw = width
        self._ch = height
        self.bind("<Configure>", self._on_configure)
        self._draw()

    def _on_configure(self, event):
        self._cw = max(130, event.width)
        self._ch = max(110, event.height)
        self._draw()

    def _draw(self):
        self.delete("all")
        _round_rect(
            self,
            1,
            1,
            self._cw - 1,
            self._ch - 1,
            14,
            fill="#152F3B",
            outline="#244654",
        )
        self.create_text(
            14,
            15,
            text="VOICE PROFILE",
            anchor="w",
            fill="#587986",
            font=_font(7, bold=True, family="Segoe UI"),
        )
        self.create_text(
            14,
            28,
            text="REM",
            anchor="nw",
            fill="#E4F5FA",
            font=(FONT_DISPLAY, 22, "bold"),
        )
        self.create_oval(74, 38, 79, 43, fill=C["rose"], outline="")
        self.create_line(14, 66, self._cw - 14, 66, fill="#244654")
        rows = [
            ("语气库", "4 TONES"),
            ("参考集", "951 CLIPS"),
            ("推理", "LOCAL ONLY"),
        ]
        for index, (label, value) in enumerate(rows):
            y = 80 + index * 16
            self.create_text(
                14,
                y,
                text=label,
                anchor="w",
                fill="#587986",
                font=_font(8),
            )
            self.create_text(
                self._cw - 14,
                y,
                text=value,
                anchor="e",
                fill="#A9C9D5",
                font=_font(7, bold=True, family="Segoe UI"),
            )


class QuoteCard(tk.Canvas):
    def __init__(self, parent, width=178, height=108):
        super().__init__(
            parent,
            width=width,
            height=height,
            bg=parent.cget("bg"),
            highlightthickness=0,
            bd=0,
        )
        self._cw = width
        self._ch = height
        self.bind("<Configure>", self._on_configure)
        self._draw()

    def _on_configure(self, event):
        self._cw = max(120, event.width)
        self._ch = max(90, event.height)
        self._draw()

    def _draw(self):
        self.delete("all")
        _round_rect(
            self,
            1,
            1,
            self._cw - 1,
            self._ch - 1,
            14,
            fill="#16313E",
            outline="#244654",
        )
        self.create_text(
            16,
            15,
            text="“",
            anchor="nw",
            fill="#4F7C8F",
            font=(FONT_DISPLAY, 25, "bold"),
        )
        self.create_text(
            18,
            45,
            text="从零开始，也没关系。",
            anchor="w",
            fill="#D5E8EF",
            font=_font(9),
        )
        self.create_text(
            18,
            65,
            text="助手会一直在这里。",
            anchor="w",
            fill="#B7D0DA",
            font=_font(9),
        )
        self.create_text(
            self._cw - 16,
            self._ch - 14,
            text="— REM",
            anchor="e",
            fill="#6E929F",
            font=_font(7, bold=True, family=FONT_DISPLAY),
        )


class ScrollArea(tk.Frame):
    """Scrollable frame with a slim scrollbar that hides when unnecessary."""

    def __init__(self, parent, bg=None, **kwargs):
        super().__init__(parent, bg=bg or C["surface"], **kwargs)
        self._bg = bg or C["surface"]
        self.canvas = tk.Canvas(
            self, bg=self._bg, highlightthickness=0, bd=0, yscrollincrement=22
        )
        self.vsb = ttk.Scrollbar(
            self,
            orient="vertical",
            command=self.canvas.yview,
            style="Soft.Vertical.TScrollbar",
        )
        self.canvas.configure(yscrollcommand=self._on_scroll)
        self.body = tk.Frame(self.canvas, bg=self._bg)
        self._window = self.canvas.create_window(
            (0, 0), window=self.body, anchor="nw"
        )
        self.canvas.pack(side="left", fill="both", expand=True)
        self._vsb_visible = False
        self.body.bind("<Configure>", self._on_body_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)

    def _on_body_configure(self, _event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        self.canvas.itemconfigure(self._window, width=event.width)

    def _on_scroll(self, first, last):
        self.vsb.set(first, last)
        needed = not (float(first) <= 0.0 and float(last) >= 1.0)
        if needed and not self._vsb_visible:
            self.vsb.pack(side="right", fill="y", padx=(2, 0))
            self._vsb_visible = True
        elif not needed and self._vsb_visible:
            self.vsb.pack_forget()
            self._vsb_visible = False

    def to_top(self):
        self.canvas.yview_moveto(0)

    def to_bottom(self):
        self.update_idletasks()
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        self.canvas.yview_moveto(1.0)

    def wheel(self, event):
        if not self._vsb_visible:
            return "break"
        steps = int(-event.delta / 120) if event.delta else 0
        if steps:
            self.canvas.yview_scroll(steps, "units")
        return "break"


class RecentRow(tk.Frame):
    def __init__(self, parent, path, on_open):
        super().__init__(parent, bg=C["surface"], cursor="hand2")
        self.path = path
        self._on_open = on_open
        self._hover = False
        self._inner = tk.Frame(self, bg=C["surface"])
        self._inner.pack(fill="x", padx=4, pady=6)

        self._dot = tk.Canvas(
            self._inner,
            width=26,
            height=26,
            bg=C["surface"],
            highlightthickness=0,
            bd=0,
        )
        self._dot.pack(side="left", padx=(2, 9))
        self._dot.create_oval(2, 2, 24, 24, fill=C["ice_soft"], outline="")
        self._dot.create_line(
            8, 11, 12, 11, 14, 13, 19, 13, 19, 19, 7, 19, 7, 11, 8, 11,
            fill=C["blue_dark"], width=1,
        )
        self._dot.create_line(7, 13, 19, 13, fill=C["blue_dark"], width=1)

        right = tk.Frame(self._inner, bg=C["surface"])
        right.pack(side="right", padx=(6, 2))
        self._arrow = tk.Label(
            right,
            text="↗",
            bg=C["surface"],
            fg=C["faint"],
            font=_font(9),
        )
        self._arrow.pack()

        text_box = tk.Frame(self._inner, bg=C["surface"])
        text_box.pack(side="left", fill="x", expand=True)
        name = os.path.basename(path)
        match = re.search(r"(\d{8})_(\d{6})", name)
        kind = "聊天语音" if name.startswith("聊天_") else "合成语音"
        if match:
            date = match.group(1)
            clock = match.group(2)
            detail = f"{moon_day(date)}  {clock[:2]}:{clock[2:4]}"
        else:
            detail = "刚刚生成"
        self._title = tk.Label(
            text_box,
            text=kind,
            bg=C["surface"],
            fg=C["ink"],
            font=_font(9, bold=True),
            anchor="w",
        )
        self._title.pack(anchor="w")
        self._meta = tk.Label(
            text_box,
            text=detail,
            bg=C["surface"],
            fg=C["muted"],
            font=_font(8),
            anchor="w",
        )
        self._meta.pack(anchor="w")

        for widget in (self, self._inner, self._dot, right, text_box, self._title, self._meta, self._arrow):
            widget.bind("<ButtonRelease-1>", self._open)
            widget.bind("<Enter>", self._enter)
            widget.bind("<Leave>", self._leave)

    def _enter(self, _event):
        self._set_hover(True)

    def _leave(self, _event):
        # Ignore synthetic leaves while moving between children.
        pointer = self.winfo_containing(self.winfo_pointerx(), self.winfo_pointery())
        widget = pointer
        while widget is not None:
            if widget is self:
                return
            widget = getattr(widget, "master", None)
        self._set_hover(False)

    def _set_hover(self, hover):
        if hover == self._hover:
            return
        self._hover = hover
        color = C["ice_soft"] if hover else C["surface"]
        _set_bg_tree(self, color)
        try:
            self._dot.itemconfigure(1, fill=C["ice"] if hover else C["ice_soft"])
        except tk.TclError:
            pass
        self._arrow.configure(fg=C["blue_dark"] if hover else C["faint"])

    def _open(self, _event=None):
        self._on_open(self.path)


def moon_day(compact):
    if len(compact) == 8:
        return compact[4:6] + "-" + compact[6:8]
    return compact
