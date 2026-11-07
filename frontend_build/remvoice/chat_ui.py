# -*- coding: utf-8 -*-
"""Chat transcript widgets.

Kept separate from the window so chat presentation (streaming, message
actions, timestamps) can evolve without touching the rest of the app.
"""
import os
import tkinter as tk
import tkinter.font as tkfont

from .settings import AVATAR_SMALL_ASSET
from .theme import C, FONT_DISPLAY, _font, _wrap_cjk, install_rounded_corners
from .widgets import ScrollArea

class ChatMessage(tk.Frame):
    """One chat row: avatar (Rem) + message block, or a right-aligned user block."""

    def __init__(
        self,
        parent,
        role,
        actions=None,
        speech="",
        user_label="旅行者君",
        on_replay=None,
        wrap=520,
        avatar_image=None,
    ):
        super().__init__(parent, bg=C["chat_bg"])
        self.role = role
        self._on_replay = on_replay
        self._wrap = wrap
        self.path = None
        self._labels = []
        self._replay_label = None
        self._avatar_image = avatar_image
        self._is_user = role == "user"

        if self._is_user:
            self._bubble_bg = "#3D93C5"
            self._border = self._bubble_bg
            self._name_fg = "#D6EEF9"
            self._speech_fg = "#FFFFFF"
            self._action_fg = "#D7EDF7"
            self.columnconfigure(0, weight=1)
            bubble_col = 1
            sticky = "e"
            padx = (110, 18)
        else:
            self._bubble_bg = C["surface"]
            self._border = C["surface"]
            self._name_fg = C["blue_dark"]
            self._speech_fg = C["ink"]
            self._action_fg = C["muted"]
            self.columnconfigure(2, weight=1)
            bubble_col = 1
            sticky = "w"
            padx = (0, 90)
            self.avatar = tk.Canvas(
                self,
                width=36,
                height=36,
                bg=C["chat_bg"],
                highlightthickness=0,
                bd=0,
            )
            self.avatar.grid(row=0, column=0, sticky="n", padx=(18, 10), pady=(10, 0))
            self.avatar.create_oval(0, 0, 36, 36, fill="#F8FDFF", outline="#D5EAF4")
            if self._avatar_image is not None:
                self.avatar.create_image(18, 18, image=self._avatar_image)
            else:
                self.avatar.create_text(
                    18,
                    18,
                    text="R",
                    fill=C["blue_deep"],
                    font=(FONT_DISPLAY, 11, "bold"),
                )

        self.bubble = tk.Frame(
            self,
            bg=self._bubble_bg,
            bd=0,
            highlightthickness=0,
        )
        self.bubble.grid(
            row=0, column=bubble_col, sticky=sticky, padx=padx, pady=(10, 0)
        )
        self._inner = tk.Frame(self.bubble, bg=self._bubble_bg)
        self._inner.pack(padx=15, pady=10)
        self._content = tk.Frame(self._inner, bg=self._bubble_bg)
        self._content.pack(fill="x")
        self._render(actions or [], speech, muted=False)
        install_rounded_corners(self.bubble, 14, self._bubble_bg)

        if not self._is_user and self._on_replay:
            self._replay_label = tk.Label(
                self._inner,
                text="▶  重播",
                bg=self._bubble_bg,
                fg=C["blue_dark"],
                font=_font(8, bold=True),
                cursor="hand2",
                anchor="e",
            )
            self._replay_label.bind("<ButtonRelease-1>", self._play)
            self._replay_label.bind(
                "<Enter>", lambda _e: self._replay_label.configure(fg=C["blue_deep"])
            )
            self._replay_label.bind(
                "<Leave>", lambda _e: self._replay_label.configure(fg=C["blue_dark"])
            )

    def _render(self, actions, speech, muted=False, error=False):
        for child in self._content.winfo_children():
            child.destroy()
        self._labels = []
        for action in actions:
            self._add_wrapped_label(
                "（" + action + "）",
                C["red"] if error else self._action_fg,
                _font(8, italic=True),
                pady=(0, 3),
            )
        if speech:
            self._add_wrapped_label(
                speech,
                C["red"] if error else (C["muted"] if muted else self._speech_fg),
                _font(10, italic=muted),
            )
        if not actions and not speech:
            label = tk.Label(
                self._content,
                text=" ",
                bg=self._bubble_bg,
                font=_font(10),
                wraplength=self._wrap,
            )
            label.pack(fill="x")
            self._labels.append((label, " ", _font(10)))

    def _add_wrapped_label(self, text, fg, font_spec, pady=(0, 0)):
        font = tkfont.Font(font=font_spec)
        wrapped = _wrap_cjk(text, font, self._wrap)
        label = tk.Label(
            self._content,
            text=wrapped,
            bg=self._bubble_bg,
            fg=fg,
            font=font_spec,
            justify="left",
            anchor="w",
            wraplength=self._wrap + 2,
        )
        label.pack(fill="x", pady=pady)
        self._labels.append((label, text, font_spec))

    def set_content(self, actions, speech, error=False, muted=False):
        self._render(actions, speech, muted=muted, error=error)
        if self._replay_label is not None:
            self._replay_label.pack_forget()
        self.path = None
        self.update_idletasks()

    def set_path(self, path):
        self.path = path
        if self._replay_label is not None:
            self._replay_label.pack(anchor="e", pady=(5, 0))
            self._bind_replay_tree(self.bubble)
        self.update_idletasks()

    def _bind_replay_tree(self, widget):
        widget.bind("<Double-Button-1>", self._play)
        for child in widget.winfo_children():
            self._bind_replay_tree(child)

    def set_wrap(self, wrap):
        self._wrap = wrap
        for label, raw, font_spec in self._labels:
            try:
                font = tkfont.Font(font=font_spec)
                label.configure(
                    text=_wrap_cjk(raw, font, wrap), wraplength=wrap + 2
                )
            except tk.TclError:
                pass

    def _play(self, _event=None):
        if self.path and os.path.exists(self.path) and self._on_replay:
            self._on_replay(self.path)
        return "break"


class ChatView(tk.Frame):
    def __init__(self, parent, user_label, on_replay, display_turns=8):
        super().__init__(parent, bg=C["chat_bg"])
        self.user_label = user_label
        self.on_replay = on_replay
        self.messages = []
        self._display_turns = max(0, int(display_turns))
        self._avatar_image = None
        try:
            self._avatar_image = tk.PhotoImage(file=AVATAR_SMALL_ASSET)
        except tk.TclError:
            pass
        self.scroll = ScrollArea(self, bg=C["chat_bg"])
        self.scroll.pack(fill="both", expand=True)
        self.scroll.canvas.bind("<Configure>", self._on_resize)
        self._wrap = 520
        self._pending_wrap = self._wrap
        self._resize_job = None

    def set_display_turns(self, turns):
        self._display_turns = max(0, int(turns or 0))
        self._trim()

    def _trim(self):
        if self._display_turns <= 0:
            return
        limit = self._display_turns * 2
        while len(self.messages) > limit:
            widget = self.messages.pop(0)
            try:
                widget.destroy()
            except tk.TclError:
                pass

    def clear(self):
        for child in self.scroll.body.winfo_children():
            try:
                child.destroy()
            except tk.TclError:
                pass
        self.messages = []

    def remove_last_turn(self):
        removed = False
        if self.messages and getattr(self.messages[-1], "role", "") == "assistant":
            self.messages.pop().destroy()
            removed = True
        if self.messages and getattr(self.messages[-1], "role", "") == "user":
            self.messages.pop().destroy()
            removed = True
        return removed

    def _on_resize(self, event):
        wrap = max(240, min(470, int(event.width * 0.53)))
        if wrap == self._wrap and self._resize_job is None:
            return
        self._pending_wrap = wrap
        if self._resize_job is not None:
            try:
                self.after_cancel(self._resize_job)
            except tk.TclError:
                pass
        self._resize_job = self.after(120, self._apply_wrap)

    def _apply_wrap(self):
        """Rewrap after the user stops dragging; avoids per-pixel relayout."""
        self._resize_job = None
        wrap = self._pending_wrap
        if wrap == self._wrap:
            return
        self._wrap = wrap
        for message in self.messages:
            message.set_wrap(wrap)

    def add_user(self, text):
        message = ChatMessage(
            self.scroll.body,
            "user",
            speech=text,
            user_label=self.user_label,
            on_replay=self.on_replay,
            wrap=self._wrap,
        )
        message.pack(fill="x")
        self.messages.append(message)
        self._trim()
        self._scroll_bottom()
        return message

    def add_assistant(self, actions, speech, muted=False):
        message = ChatMessage(
            self.scroll.body,
            "assistant",
            actions=actions,
            speech=speech,
            on_replay=self.on_replay,
            wrap=self._wrap,
            avatar_image=self._avatar_image,
        )
        if muted:
            message.set_content(actions, speech, muted=True)
        message.pack(fill="x")
        self.messages.append(message)
        self._trim()
        self._scroll_bottom()
        return message

    def add_note(self, text):
        row = tk.Frame(self.scroll.body, bg=C["chat_bg"])
        row.pack(fill="x", pady=(10, 4))
        label = tk.Label(
            row,
            text=text,
            bg=C["chat_bg"],
            fg=C["muted"],
            font=_font(8),
        )
        label.pack()
        self._scroll_bottom()
        return row

    def _scroll_bottom(self):
        self.after(30, self.scroll.to_bottom)
