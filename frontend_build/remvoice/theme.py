# -*- coding: utf-8 -*-
"""Colors, fonts and small Tk drawing helpers."""
import tkinter as tk
import tkinter.font as tkfont

# ---------------------------------------------------------------------------
# Visual system
# ---------------------------------------------------------------------------

FONT_UI = "Microsoft YaHei UI"
FONT_DISPLAY = "Segoe UI Variable Display"

C = {
    "bg": "#ECF4F9",
    "surface": "#FFFFFF",
    "surface_soft": "#F4F9FC",
    "chat_bg": "#F2F7FB",
    "ink": "#16374A",
    "ink_soft": "#3D5B6B",
    "muted": "#708995",
    "faint": "#9AAFB9",
    "line": "#DDEAF2",
    "line_soft": "#ECF3F7",
    "nav": "#F8FBFE",
    "nav_soft": "#EFF7FC",
    "nav_active": "#DDF0FB",
    "nav_line": "#E1EDF5",
    "nav_text": "#456272",
    "nav_muted": "#8AA2AF",
    "blue": "#4C9FD0",
    "blue_dark": "#2E7FA9",
    "blue_deep": "#266F93",
    "ice": "#DFF2FB",
    "ice_soft": "#F0F8FD",
    "rose": "#D78FA5",
    "gold": "#DDB264",
    "green": "#4FB28F",
    "red": "#D77A7A",
}

APP_ICON_B64 = ""


def _font(size=10, bold=False, italic=False, family=FONT_UI):
    style = "bold" if bold else ""
    if italic:
        style = (style + " italic").strip()
    return (family, size, style) if style else (family, size)


def _mix(color_a, color_b, ratio):
    a = tuple(int(color_a[i : i + 2], 16) for i in (1, 3, 5))
    b = tuple(int(color_b[i : i + 2], 16) for i in (1, 3, 5))
    out = tuple(int(a[i] + (b[i] - a[i]) * ratio) for i in range(3))
    return "#%02x%02x%02x" % out


def _round_rect(canvas, x1, y1, x2, y2, radius, **kwargs):
    radius = max(0, min(radius, (x2 - x1) / 2, (y2 - y1) / 2))
    points = [
        x1 + radius,
        y1,
        x2 - radius,
        y1,
        x2,
        y1,
        x2,
        y1 + radius,
        x2,
        y2 - radius,
        x2,
        y2,
        x2 - radius,
        y2,
        x1 + radius,
        y2,
        x1,
        y2,
        x1,
        y2 - radius,
        x1,
        y1 + radius,
        x1,
        y1,
    ]
    return canvas.create_polygon(points, smooth=True, splinesteps=24, **kwargs)


def _ellipsize(text, limit=46):
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def install_rounded_corners(widget, radius=16, fill=None, bg=None):
    """Mask a normal Tk frame into a rounded card.

    Tk has no rounded Frame. Four tiny canvases cover the square corners with
    the parent background and draw a quarter-circle of the card colour.
    """
    if getattr(widget, "_rounded_corners", None):
        return
    fill = fill or widget.cget("bg")
    bg = bg or widget.master.cget("bg")
    radius = int(radius)
    corners = []
    for key in ("tl", "tr", "bl", "br"):
        canvas = tk.Canvas(
            widget,
            width=radius,
            height=radius,
            bg=bg,
            highlightthickness=0,
            bd=0,
            takefocus=0,
        )
        canvas.place(x=0, y=0, width=radius, height=radius)
        corners.append((key, canvas))
    widget._rounded_corners = corners
    widget._rounded_radius = radius
    widget._rounded_fill = fill
    widget._rounded_bg = bg

    def paint(_event=None):
        if not widget.winfo_exists():
            return
        w = widget.winfo_width()
        h = widget.winfo_height()
        r = min(widget._rounded_radius, max(2, w // 2), max(2, h // 2))
        positions = {
            "tl": (0, 0),
            "tr": (max(0, w - r), 0),
            "bl": (0, max(0, h - r)),
            "br": (max(0, w - r), max(0, h - r)),
        }
        boxes = {
            "tl": (0, 0, 2 * r, 2 * r),
            "tr": (-r, 0, r, 2 * r),
            "bl": (0, -r, 2 * r, r),
            "br": (-r, -r, r, r),
        }
        for key, canvas in corners:
            x, y = positions[key]
            canvas.place_configure(x=x, y=y, width=r, height=r)
            canvas.delete("all")
            canvas.create_oval(
                *boxes[key],
                fill=widget._rounded_fill,
                outline="",
            )
            tk.Misc.lift(canvas)

    widget.bind("<Configure>", paint, add="+")
    widget.after_idle(paint)


def _wrap_cjk(text, font, width):
    """Wrap Chinese text by pixels; Tk labels only wrap at word boundaries."""
    return _wrap_cjk_once(text, font, width, allow_retry=True)


_WIDTH_CACHE = {}


def _char_width(font, font_key, char):
    """Memoised ``font.measure``; a long chat otherwise measures every glyph
    again on each resize."""
    key = (font_key, char)
    try:
        return _WIDTH_CACHE[key]
    except KeyError:
        value = font.measure(char)
        _WIDTH_CACHE[key] = value
        return value


def _wrap_cjk_once(text, font, width, allow_retry=False):
    if not text:
        return ""
    width = max(80, int(width))
    actual = font.actual()
    font_key = (
        actual.get("family"),
        actual.get("size"),
        actual.get("weight"),
        actual.get("slant"),
    )
    no_break_start = "，。！？、；：…—～）】」』,.!?;:"
    out = []
    for paragraph in str(text).splitlines():
        if not paragraph:
            out.append("")
            continue
        current = ""
        current_width = 0
        for char in paragraph:
            char_width = _char_width(font, font_key, char)
            if current and current_width + char_width > width:
                if char in no_break_start:
                    current += char
                    current_width += char_width
                    continue
                out.append(current.rstrip())
                current = "" if char == " " else char
                current_width = 0 if char == " " else char_width
            else:
                current += char
                current_width += char_width
        out.append(current.rstrip())
    if (
        allow_retry
        and len(out) > 1
        and 0 < len(out[-1].strip()) <= 2
        and width > 220
    ):
        return _wrap_cjk_once(text, font, int(width * 0.9), allow_retry=False)
    return "\n".join(out)


def _set_bg_tree(widget, color):
    try:
        widget.configure(bg=color)
    except tk.TclError:
        pass
    for child in widget.winfo_children():
        _set_bg_tree(child, color)
