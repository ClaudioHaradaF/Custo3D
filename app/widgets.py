import tkinter as tk
from tkinter import font as tkfont
from app.constants import (
    PRIMARY, PRIMARY_LIGHT, PRIMARY_DARK, BG_LIGHT, CARD_BG,
    TEXT_PRIMARY, TEXT_SECONDARY, BTN_RADIUS, BTN_PADX, BTN_PADY,
)


class RoundedButton(tk.Canvas):
    def __init__(self, parent, text='', command=None, bg=PRIMARY, fg='white',
                 hover_bg='#1565C0', width=None, height=34, font=None, tooltip=None, **kwargs):
        self._bg = bg
        self._hover_bg = hover_bg
        self._fg = fg
        self._command = command
        self._text = text
        self._font = font or ('Segoe UI', 10, 'bold')

        try:
            fnt = tkfont.Font(family=self._font[0], size=self._font[1], weight=self._font[2])
            tw = fnt.measure(text)
            th = fnt.metrics('linespace')
        except Exception:
            tw = len(text) * 8
            th = 20
        btn_w = width or (tw + BTN_PADX * 2)
        btn_h = max(height, th + BTN_PADY * 2)

        try:
            p_bg = parent.cget('bg')
        except Exception:
            p_bg = BG_LIGHT

        super().__init__(parent, width=btn_w, height=btn_h,
                         highlightthickness=0, bd=0, bg=p_bg, **kwargs)
        self._btn_w = btn_w
        self._btn_h = btn_h
        self._radius = BTN_RADIUS

        self._draw(self._bg)
        self.create_text(btn_w//2, btn_h//2, text=text, fill=fg,
                        font=self._font, anchor='center', tags='text')

        self.bind('<Enter>', self._on_enter)
        self.bind('<Leave>', self._on_leave)
        self.bind('<Button-1>', self._on_click)
        self.bind('<ButtonRelease-1>', self._on_release)

        self._tooltip_window = None
        if tooltip:
            self.bind('<Enter>', lambda e: self._show_tooltip(tooltip), add='+')
            self.bind('<Leave>', lambda e: self._hide_tooltip(), add='+')

    def _draw(self, color):
        self.delete('bg')
        r = self._radius
        w, h = self._btn_w, self._btn_h
        self.create_arc((0, 0, r*2, r*2), start=90, extent=90, fill=color, outline=color, tags='bg')
        self.create_arc((w-r*2, 0, w, r*2), start=0, extent=90, fill=color, outline=color, tags='bg')
        self.create_arc((0, h-r*2, r*2, h), start=180, extent=90, fill=color, outline=color, tags='bg')
        self.create_arc((w-r*2, h-r*2, w, h), start=270, extent=90, fill=color, outline=color, tags='bg')
        self.create_rectangle((r, 0, w-r, h), fill=color, outline=color, tags='bg')
        self.create_rectangle((0, r, w, h-r), fill=color, outline=color, tags='bg')

    def _on_enter(self, event):
        self._draw(self._hover_bg)
        self.tag_raise('text')
        self.configure(cursor='hand2')

    def _on_leave(self, event):
        self._draw(self._bg)
        self.tag_raise('text')
        self.configure(cursor='')

    def _on_click(self, event):
        self._draw('#0A3D91')
        self.tag_raise('text')
        if self._command:
            self.after(80, self._command)

    def _on_release(self, event):
        self._draw(self._hover_bg)
        self.tag_raise('text')

    def _show_tooltip(self, text):
        self._hide_tooltip()
        x = self.winfo_rootx() + self.winfo_width() // 2
        y = self.winfo_rooty() + self.winfo_height() + 4
        self._tooltip_window = tw = tk.Toplevel(self)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f'+{x}+{y}')
        label = tk.Label(tw, text=text, background='#333333', foreground='white',
                         font=('Segoe UI', 8), padx=8, pady=3)
        label.pack()

    def _hide_tooltip(self):
        if self._tooltip_window:
            self._tooltip_window.destroy()
            self._tooltip_window = None

    def config(self, **kwargs):
        if 'text' in kwargs:
            self._text = kwargs['text']
            self.itemconfigure('text', text=self._text)
        if 'command' in kwargs:
            self._command = kwargs['command']
        if 'bg' in kwargs:
            self._bg = kwargs['bg']
            self._draw(self._bg)
            self.tag_raise('text')
        if 'fg' in kwargs:
            self._fg = kwargs['fg']
            self.itemconfigure('text', fill=self._fg)
        if 'hover_bg' in kwargs:
            self._hover_bg = kwargs['hover_bg']

    def configure(self, **kwargs):
        self.config(**kwargs)


class LoadingDialog:
    def __init__(self, parent, message='Processando...'):
        self.parent = parent
        self.dialog = tk.Toplevel(parent)
        self.dialog.title('')
        self.dialog.geometry('280x100')
        self.dialog.resizable(False, False)
        self.dialog.transient(parent)
        self.dialog.overrideredirect(True)
        self.dialog.configure(bg=CARD_BG, highlightbackground=PRIMARY_LIGHT, highlightthickness=2)

        cx = parent.winfo_rootx() + parent.winfo_width() // 2 - 140
        cy = parent.winfo_rooty() + parent.winfo_height() // 2 - 50
        self.dialog.geometry(f'+{cx}+{cy}')

        frame = tk.Frame(self.dialog, bg=CARD_BG, padx=20, pady=20)
        frame.pack(fill='both', expand=True)

        self._dots_label = tk.Label(frame, text='⏳', font=('Segoe UI', 16),
                                   bg=CARD_BG, fg=PRIMARY)
        self._dots_label.pack(pady=(0, 8))

        self._msg_label = tk.Label(frame, text=message, font=('Segoe UI', 10),
                                  fg=TEXT_PRIMARY, bg=CARD_BG)
        self._msg_label.pack()

        self._running = True
        self.dialog.update()
        self._animate()

    def _animate(self):
        if not self._running:
            return
        symbols = ['⏳', '🔄', '⏳', '🔄']
        current = self._dots_label.cget('text')
        idx = (symbols.index(current) + 1) % len(symbols) if current in symbols else 0
        self._dots_label.config(text=symbols[idx])
        self.dialog.after(400, self._animate)

    def close(self):
        self._running = False
        try:
            self.dialog.grab_release()
            self.dialog.destroy()
        except:
            pass
