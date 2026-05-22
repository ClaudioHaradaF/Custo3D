import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import os
import sys
import io
import base64
import tempfile
import shutil
import threading
from datetime import datetime

from app.database import init_database, get_setting, update_setting, get_connection, DB_PATH
from app.filament import Filament
from app.printer import Printer
from app.gcode_parser import GCodeParser, extract_thumbnail_from_gcode
from app.cost_calculator import CostCalculator
from app.slicer_importer import scan_filaments as slicer_scan_filaments, scan_printers as slicer_scan_printers, find_available_slicers as find_importable_slicers
from app.mesh_reader import estimate_from_3mf as mesh_estimate_3mf, extract_thumbnail_from_3mf
from app.exporter import export_quotes_csv, export_quote_detail_csv
from app.constants import (
    PRIMARY, PRIMARY_LIGHT, PRIMARY_DARK, SECONDARY, BG_LIGHT, CARD_BG,
    TEXT_PRIMARY, TEXT_SECONDARY, SUCCESS, WARNING, ERROR,
    DARK_BG, APP_TITLE,
)
from app.widgets import RoundedButton, LoadingDialog

class Cost3DApp:
    def __init__(self, root):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("1200x780")
        self.root.minsize(1000, 650)

        init_database()

        self._check_slicer_available()
        self._setup_styles()
        self._build_main_layout()

        self.quote_thumbnail_tk = None
        self.quote_thumbnail_pil = None
        self.current_quote_data = None
        self._generated_gcode_path = None

        self._setup_keyboard_shortcuts()
        self._update_status_bar()

    def _switch_tab(self, index):
        if index == self._active_tab:
            return
        self.tab_frames[self._active_tab].pack_forget()
        self._update_tab_style(self._active_tab, active=False)
        self.tab_frames[index].pack(fill='both', expand=True)
        self._update_tab_style(index, active=True)
        self._active_tab = index

        frame = self.tab_frames[index]
        if frame == self.tab_quote:
            self._refresh_quote_combos()
        elif frame == self.tab_dashboard:
            self._refresh_dashboard()
            self._load_filaments()
            self._load_printers()
            self._load_history()

    def _update_tab_style(self, index, active):
        btn = self.tab_buttons[index]
        if active:
            btn.config(bg=PRIMARY, fg='white', hover_bg=PRIMARY_DARK)
        else:
            btn.config(bg='#E8EAF6', fg=TEXT_PRIMARY, hover_bg='#C5CAE9')

    def _setup_styles(self):
        style = ttk.Style()
        style.theme_use('clam')

        style.configure('.', font=('Segoe UI', 10))

        style.configure('Card.TFrame', background=CARD_BG, relief='solid', borderwidth=1)
        style.configure('CardTitle.TLabel', font=('Segoe UI', 11, 'bold'), foreground=PRIMARY_DARK, background=CARD_BG)
        style.configure('CardValue.TLabel', font=('Segoe UI', 22, 'bold'), foreground=TEXT_PRIMARY, background=CARD_BG)
        style.configure('CardLabel.TLabel', font=('Segoe UI', 9), foreground=TEXT_SECONDARY, background=CARD_BG)

        style.configure('Header.TLabel', font=('Segoe UI', 16, 'bold'), foreground=PRIMARY_DARK)
        style.configure('SubHeader.TLabel', font=('Segoe UI', 11), foreground=TEXT_SECONDARY)

        style.configure('Treeview', font=('Segoe UI', 9), rowheight=28)
        style.configure('Treeview.Heading', font=('Segoe UI', 9, 'bold'))
        style.map('Treeview', background=[('selected', PRIMARY_LIGHT)], foreground=[('selected', TEXT_PRIMARY)])

        style.configure('TFrame', background=BG_LIGHT)
        style.configure('TLabelframe', background=BG_LIGHT)
        style.configure('TLabelframe.Label', background=BG_LIGHT)

        style.configure('Total.TLabel', font=('Segoe UI', 14, 'bold'), foreground=PRIMARY_DARK)

    def _build_main_layout(self):
        header = tk.Frame(self.root, bg=PRIMARY, height=50)
        header.pack(fill='x')
        header.pack_propagate(False)

        tk.Label(header, text='Cost3D', font=('Segoe UI', 17, 'bold'),
                bg=PRIMARY, fg='white').pack(side='left', padx=20)
        tk.Label(header, text='Controle de Custos',
                font=('Segoe UI', 10), bg=PRIMARY, fg='#BBDEFB').pack(side='left', padx=(0, 20))

        self.theme_var = tk.StringVar(value=get_setting('theme') or 'light')
        theme_btn = RoundedButton(header, text='🌙' if self.theme_var.get() == 'dark' else '☀️',
                                 width=40, height=30, bg='#1565C0', hover_bg='#0D47A1',
                                 command=self._toggle_theme)
        self.theme_btn = theme_btn
        theme_btn.pack(side='right', padx=10)

        self.root.configure(bg=BG_LIGHT)

        # ─── Tab bar ─────────────────────────────────────────────────
        self.tab_bar = tk.Frame(self.root, bg=BG_LIGHT)
        self.tab_bar.pack(fill='x', padx=12, pady=(12, 0))

        # ─── Content container ────────────────────────────────────────
        self.tab_content = tk.Frame(self.root, bg=BG_LIGHT)
        self.tab_content.pack(fill='both', expand=True, padx=12, pady=12)

        # ─── Tab content frames ──────────────────────────────────────
        self.tab_dashboard = ttk.Frame(self.tab_content, style='TFrame')
        self.tab_filaments = ttk.Frame(self.tab_content, style='TFrame')
        self.tab_printers = ttk.Frame(self.tab_content, style='TFrame')
        self.tab_quote = ttk.Frame(self.tab_content, style='TFrame')
        self.tab_slicing = ttk.Frame(self.tab_content, style='TFrame')
        self.tab_history = ttk.Frame(self.tab_content, style='TFrame')
        self.tab_settings = ttk.Frame(self.tab_content, style='TFrame')

        self.tab_frames = [
            self.tab_dashboard, self.tab_filaments, self.tab_printers,
            self.tab_quote, self.tab_slicing, self.tab_history, self.tab_settings,
        ]
        self.tab_labels = [
            '  Painel  ', '  Filamentos  ', '  Impressoras  ',
            '  Novo Orçamento  ', '  Fatiar  ', '  Histórico  ', '  Configurações  ',
        ]
        self.tab_buttons = []
        self._active_tab = 0

        for i, (frame, label) in enumerate(zip(self.tab_frames, self.tab_labels)):
            is_active = (i == 0)
            btn = RoundedButton(
                self.tab_bar, text=label,
                bg=PRIMARY if is_active else '#E8EAF6',
                fg='white' if is_active else TEXT_PRIMARY,
                hover_bg=PRIMARY_DARK if is_active else '#C5CAE9',
                command=lambda idx=i: self._switch_tab(idx),
            )
            btn.pack(side='left', padx=(0, 4))
            self.tab_buttons.append(btn)
            frame.pack_forget()

        for frame in self.tab_frames:
            frame.pack_forget()
        self.tab_frames[0].pack(fill='both', expand=True)

        self._build_dashboard()
        self._build_filaments_tab()
        self._build_printers_tab()
        self._build_quote_tab()
        self._build_slicing_tab()
        self._build_history_tab()
        self._build_settings_tab()

        # ─── Status Bar ──────────────────────────────────────────────────
        self.status_bar = tk.Frame(self.root, bg=PRIMARY_DARK, height=26)
        self.status_bar.pack(fill='x', side='bottom')
        self.status_bar.pack_propagate(False)
        self.status_label = tk.Label(self.status_bar, text='', font=('Segoe UI', 8),
                                     fg='white', bg=PRIMARY_DARK, anchor='w', padx=10)
        self.status_label.pack(side='left', fill='x', expand=True)
        self.db_info_label = tk.Label(self.status_bar, text='', font=('Segoe UI', 8),
                                      fg='#BBDEFB', bg=PRIMARY_DARK, anchor='e', padx=10)
        self.db_info_label.pack(side='right')

        current_theme = self.theme_var.get()
        if current_theme == 'dark':
            self._apply_theme('dark')

    def _toggle_theme(self):
        new = 'dark' if self.theme_var.get() == 'light' else 'light'
        self.theme_var.set(new)
        update_setting('theme', new)
        self._apply_theme(new)

    def _apply_theme(self, theme):
        if theme == 'dark':
            bg = '#1E1E1E'
            fg = '#FFFFFF'
            card = '#2D2D2D'
            self.root.configure(bg=bg)
            style = ttk.Style()
            style.configure('.', background=bg, foreground=fg)
            style.configure('TFrame', background=bg)
            style.configure('TLabelframe', background=bg)
            style.configure('TLabelframe.Label', background=bg, foreground=fg)
            style.configure('Card.TFrame', background=card)
            style.configure('CardTitle.TLabel', background=card, foreground=fg)
            style.configure('CardValue.TLabel', background=card, foreground=fg)
            style.configure('CardLabel.TLabel', background=card, foreground='#BBBBBB')
            style.configure('Header.TLabel', foreground=fg)
            style.configure('SubHeader.TLabel', foreground='#BBBBBB')
            style.configure('Total.TLabel', foreground=fg)
            style.configure('Treeview', background=card, foreground=fg, fieldbackground=card)
            style.configure('Treeview.Heading', background='#3A3A3A', foreground=fg)
            style.map('Treeview', background=[('selected', PRIMARY)])
            self.tab_bar.configure(bg=bg)
            self.theme_btn.config(text='☀️', bg='#333', fg='white', hover_bg='#555')
        else:
            bg = BG_LIGHT
            fg = TEXT_PRIMARY
            card = CARD_BG
            self.root.configure(bg=bg)
            style = ttk.Style()
            style.configure('.', background=bg, foreground=fg)
            style.configure('TFrame', background=bg)
            style.configure('TLabelframe', background=bg)
            style.configure('TLabelframe.Label', background=bg, foreground=fg)
            style.configure('Card.TFrame', background=card)
            style.configure('CardTitle.TLabel', background=card, foreground=PRIMARY_DARK)
            style.configure('CardValue.TLabel', background=card, foreground=TEXT_PRIMARY)
            style.configure('CardLabel.TLabel', background=card, foreground=TEXT_SECONDARY)
            style.configure('Header.TLabel', foreground=PRIMARY_DARK)
            style.configure('SubHeader.TLabel', foreground=TEXT_SECONDARY)
            style.configure('Total.TLabel', foreground=PRIMARY_DARK)
            style.configure('Treeview', background='white', foreground=TEXT_PRIMARY, fieldbackground='white')
            style.configure('Treeview.Heading', background=BG_LIGHT, foreground=TEXT_PRIMARY)
            style.map('Treeview', background=[('selected', PRIMARY_LIGHT)])
            self.tab_bar.configure(bg=BG_LIGHT)
            self.theme_btn.config(text='🌙', bg='#1565C0', fg='white', hover_bg='#0D47A1')
        if hasattr(self, 'status_bar'):
            sb_bg = DARK_BG if theme == 'dark' else PRIMARY_DARK
            self.status_bar.configure(bg=sb_bg)
            self.status_label.configure(bg=sb_bg)
            self.db_info_label.configure(bg=sb_bg)

    # ─── KEYBOARD SHORTCUTS ────────────────────────────────────────

    def _setup_keyboard_shortcuts(self):
        self.root.bind('<Control-n>', lambda e: self._switch_tab(3))
        self.root.bind('<Control-N>', lambda e: self._switch_tab(3))
        self.root.bind('<Control-s>', lambda e: self._save_quote())
        self.root.bind('<Control-S>', lambda e: self._save_quote())
        self.root.bind('<Control-f>', lambda e: self._switch_tab(4))
        self.root.bind('<Control-F>', lambda e: self._switch_tab(4))
        self.root.bind('<Control-h>', lambda e: self._switch_tab(5))
        self.root.bind('<Control-H>', lambda e: self._switch_tab(5))
        self.root.bind('<Control-d>', lambda e: self._switch_tab(0))
        self.root.bind('<Control-D>', lambda e: self._switch_tab(0))
        self.root.bind('<F5>', lambda e: self._refresh_dashboard())
        self.root.bind('<Escape>', self._close_active_dialog)

    def _close_active_dialog(self, event=None):
        for w in self.root.winfo_children():
            if isinstance(w, tk.Toplevel):
                w.destroy()
                break

    def _update_status_bar(self, message=None):
        if not hasattr(self, 'status_label'):
            return
        if message:
            self.status_label.config(text=message)
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM quotes')
        q_count = cursor.fetchone()[0]
        cursor.execute('SELECT COUNT(*) FROM quotes WHERE status IS NULL OR status NOT IN (\'concluído\',\'cancelado\')')
        p_count = cursor.fetchone()[0]
        conn.close()
        self.db_info_label.config(text=f'{q_count} orçamentos | {p_count} pendentes')

    # ─── DASHBOARD ─────────────────────────────────────────────────

    def _check_slicer_available(self):
        from app.slicer_cli import find_available_slicers as find_slicers
        self._slicer_list = find_slicers()
        self._cli_slicers = [s for s in self._slicer_list if s['has_cli']]
        self._gui_slicers = [s for s in self._slicer_list if not s['has_cli']]
        self._slicer_available = len(self._slicer_list) > 0
        print(f'[SLICE DEBUG] Slicers found: {[s["name"] for s in self._slicer_list]}')
        print(f'[SLICE DEBUG] CLI slicers: {[s["name"] for s in self._cli_slicers]}')

    def _build_dashboard(self):
        main = ttk.Frame(self.tab_dashboard)
        main.pack(fill='both', expand=True, padx=15, pady=15)

        ttk.Label(main, text='Painel de Controle', style='Header.TLabel').pack(anchor='w')
        ttk.Label(main, text='Resumo do seu negócio de impressão 3D', style='SubHeader.TLabel').pack(anchor='w', pady=(0, 15))

        cards_frame = ttk.Frame(main)
        cards_frame.pack(fill='x')

        self.dash_cards = {}
        card_data = [
            ('filaments', 'Filamentos', '0', PRIMARY),
            ('printers', 'Impressoras', '0', '#1565C0'),
            ('quotes', 'Orçamentos', '0', SECONDARY),
            ('total_revenue', 'Faturamento Total', 'R$ 0,00', SUCCESS),
        ]

        for key, title, value, color in card_data:
            card = tk.Frame(cards_frame, bg=CARD_BG, highlightbackground='#E0E0E0', highlightthickness=1)
            card.pack(side='left', fill='both', expand=True, padx=(0, 12))

            tk.Frame(card, bg=color, height=4).pack(fill='x')
            tk.Label(card, text=title, font=('Segoe UI', 10), fg=TEXT_SECONDARY,
                    bg=CARD_BG, anchor='w').pack(fill='x', padx=15, pady=(10, 0))
            tk.Label(card, text=value, font=('Segoe UI', 24, 'bold'), fg=TEXT_PRIMARY,
                    bg=CARD_BG, anchor='w').pack(fill='x', padx=15, pady=(0, 10))
            self.dash_cards[key] = card.winfo_children()[-1]

        buttons_frame = ttk.Frame(main)
        buttons_frame.pack(fill='x', pady=20)

        RoundedButton(buttons_frame, text='+ Novo Orçamento',
                     command=lambda: self._switch_tab(self.tab_frames.index(self.tab_quote)),
                     tooltip='Ctrl+N | Criar novo orçamento').pack(side='left', padx=(0, 8))
        RoundedButton(buttons_frame, text='Importar dos Slicers', bg='#546E7A', hover_bg='#455A64',
                     command=self._import_from_all_slicers,
                     tooltip='Importar filamentos e impressoras dos slicers instalados').pack(side='left', padx=8)
        RoundedButton(buttons_frame, text='Ver Pendentes', bg=WARNING, hover_bg='#D97706',
                     command=self._show_pending_window,
                     tooltip='Mostrar orçamentos não concluídos').pack(side='left', padx=8)

        recent_frame = ttk.LabelFrame(main, text='Últimos Orçamentos', padding=10)
        recent_frame.pack(fill='both', expand=True, pady=(0, 10))

        self.recent_canvas = tk.Canvas(recent_frame, bg=CARD_BG, highlightthickness=0, height=140)
        self.recent_canvas.pack(fill='x')

        self._refresh_dashboard()

    def _show_quote_card(self, qid):
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute('''SELECT q.name, q.total_cost, q.suggested_price, q.gcode_file,
                          pr.name AS printer_name, f.name AS filament_name
                          FROM quotes q
                          LEFT JOIN printers pr ON q.printer_id = pr.id
                          LEFT JOIN filaments f ON q.filament_id = f.id
                          WHERE q.id=?''', (qid,))
        row = cursor.fetchone()
        conn.close()
        if row:
            msg = (f"Nome: {row[0]}\n"
                   f"Custo: R$ {row[1]:.2f}\n"
                   f"Sugerido: R$ {row[2]:.2f}\n"
                   f"G-Code: {row[3] or 'N/A'}")
            messagebox.showinfo(f'Orçamento #{qid}', msg)

    def _refresh_dashboard(self):
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute('SELECT COUNT(*) FROM filaments')
        fil_count = cursor.fetchone()[0]
        self.dash_cards['filaments'].config(text=str(fil_count))

        cursor.execute('SELECT COUNT(*) FROM printers')
        pr_count = cursor.fetchone()[0]
        self.dash_cards['printers'].config(text=str(pr_count))

        cursor.execute('SELECT COUNT(*) FROM quotes')
        qt_count = cursor.fetchone()[0]
        self.dash_cards['quotes'].config(text=str(qt_count))

        cursor.execute('SELECT COALESCE(SUM(COALESCE(sale_price, suggested_price, 0)), 0) FROM quotes WHERE status=?', ('concluído',))
        total = cursor.fetchone()[0]
        self.dash_cards['total_revenue'].config(text=f'R$ {total:.2f}')

        # Recent quotes with thumbnails
        cursor.execute('''
            SELECT id, name, total_cost, suggested_price, gcode_file, thumbnail_data
            FROM quotes ORDER BY id DESC LIMIT 6
        ''')
        recent = cursor.fetchall()
        conn.close()

        self.recent_canvas.delete('all')
        if not recent:
            self.recent_canvas.create_text(10, 20, anchor='nw', text='Nenhum orçamento ainda.',
                                          font=('Segoe UI', 10), fill=TEXT_SECONDARY)
            return

        x = 10
        y = 10
        card_w = 155
        card_h = 118
        for q in recent:
            qid, name, cost, suggested, gcode_file, thumb_blob = q
            self._draw_quote_card(self.recent_canvas, x, y, card_w, card_h,
                                  qid, name, cost, gcode_file, thumb_blob)
            x += card_w + 10

        self.recent_canvas.configure(scrollregion=(0, 0, x + 10, card_h + 20))

    def _draw_quote_card(self, canvas, x, y, w, h, qid, name, cost, gcode_file, thumb_blob=None):
        # Card background
        canvas.create_rectangle(x, y, x+w, y+h, fill=CARD_BG, outline='#E0E0E0', width=1, tags=f'card_{qid}')
        # Thumbnail — try DB blob first, then gcode file
        thumb = None
        if thumb_blob:
            try:
                from PIL import Image as PILImage
                import io
                thumb = PILImage.open(io.BytesIO(thumb_blob))
            except Exception:
                pass
        if not thumb and gcode_file and os.path.isfile(gcode_file):
            try:
                thumb = extract_thumbnail_from_gcode(gcode_file)
            except Exception:
                pass
        if thumb:
            try:
                from PIL import ImageTk
                img = thumb.copy()
                img.thumbnail((w-20, 60))
                tk_img = ImageTk.PhotoImage(img)
                canvas.create_image(x+w//2, y+35, image=tk_img, tags=f'thumb_{qid}')
                if not hasattr(self, '_recent_imgs'):
                    self._recent_imgs = []
                self._recent_imgs.append(tk_img)
            except Exception:
                pass
        # Name
        display_name = name or f'Orçamento #{qid}'
        if len(display_name) > 22:
            display_name = display_name[:20] + '...'
        canvas.create_text(x+w//2, y+75, text=display_name, font=('Segoe UI', 9, 'bold'),
                          fill=TEXT_PRIMARY, anchor='center', tags=f'name_{qid}')
        # Cost
        canvas.create_text(x+w//2, y+92, text=f'R$ {cost:.2f}', font=('Segoe UI', 10, 'bold'),
                          fill=PRIMARY, anchor='center', tags=f'cost_{qid}')
        # Click binding
        canvas.tag_bind(f'card_{qid}', '<Button-1>', lambda e, i=qid: self._show_quote_card(i))

    def _show_pending_window(self):
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT q.id, q.name, q.total_cost, q.suggested_price, q.sale_price, q.status, q.created_at,
                   pr.name AS printer_name, f.name AS filament_name
            FROM quotes q
            LEFT JOIN printers pr ON q.printer_id = pr.id
            LEFT JOIN filaments f ON q.filament_id = f.id
            WHERE q.status IS NULL OR q.status NOT IN ('concluído','cancelado')
            ORDER BY q.created_at DESC''')
        rows = cursor.fetchall()
        conn.close()

        win = tk.Toplevel(self.root)
        win.title('Orçamentos Pendentes')
        win.geometry('750x400')
        win.transient(self.root)
        win.grab_set()
        win.configure(bg=CARD_BG)

        tk.Label(win, text='Orçamentos Pendentes', font=('Segoe UI', 14, 'bold'),
                fg=PRIMARY_DARK, bg=CARD_BG).pack(anchor='w', padx=20, pady=(15, 5))

        tree = ttk.Treeview(win, columns=('id','data','nome','impressora','filamento','custo','sugerido','venda','status'),
                           show='headings', height=15)
        tree.heading('id', text='#')
        tree.heading('data', text='Data')
        tree.heading('nome', text='Nome')
        tree.heading('impressora', text='Impressora')
        tree.heading('filamento', text='Filamento')
        tree.heading('custo', text='Custo')
        tree.heading('sugerido', text='Sugerido')
        tree.heading('venda', text='Venda')
        tree.heading('status', text='Status')
        cols = ('id','data','nome','impressora','filamento','custo','sugerido','venda','status')
        widths = (30, 130, 140, 100, 100, 70, 70, 70, 80)
        for c, w in zip(cols, widths):
            tree.column(c, width=w, anchor='center')

        style = ttk.Style()
        style.configure('Pending.Treeview', rowheight=28, font=('Segoe UI', 9), background=CARD_BG)
        tree.configure(style='Pending.Treeview')

        status_tags = {
            'em espera': '#FEF3C7', 'em andamento': '#DBEAFE',
            'cancelado': '#FEE2E2', 'concluído': '#DCFCE7',
        }
        for row in rows:
            status = (row['status'] or 'orçamento').capitalize()
            sale = f'R$ {row["sale_price"]:.2f}' if row['sale_price'] else '-'
            tag = (row['status'] or 'orçamento').lower()
            tree.insert('', 'end', values=(
                row['id'], (row['created_at'] or '')[:10], row['name'],
                row['printer_name'] or '-', row['filament_name'] or '-',
                f'R$ {row["total_cost"]:.2f}' if row['total_cost'] else '-',
                f'R$ {row["suggested_price"]:.2f}' if row['suggested_price'] else '-',
                sale, status,
            ), tags=(tag,))
            bg = status_tags.get(tag, 'white')
            tree.tag_configure(tag, background=bg)

        tree.pack(fill='both', expand=True, padx=20, pady=10)

        RoundedButton(win, text='Fechar', bg=PRIMARY, command=win.destroy).pack(pady=10)

    # ─── FILAMENTS TAB ─────────────────────────────────────────────

    def _build_filaments_tab(self):
        main = ttk.Frame(self.tab_filaments)
        main.pack(fill='both', expand=True, padx=15, pady=15)

        ttk.Label(main, text='Gerenciar Filamentos', style='Header.TLabel').pack(anchor='w')
        ttk.Label(main, text='Cadastre ou importe filamentos dos slicers', style='SubHeader.TLabel').pack(anchor='w', pady=(0, 15))

        self.fil_import_user_var = tk.BooleanVar(value=False)
        toolbar = ttk.Frame(main)
        toolbar.pack(fill='x', pady=(0, 10))
        RoundedButton(toolbar, text='Importar dos Slicers', bg='#546E7A', hover_bg='#455A64',
                     command=self._import_filaments_from_slicers,
                     tooltip='Importar filamentos dos slicers instalados').pack(side='left', padx=(0, 5))
        ttk.Checkbutton(toolbar, text='Só perfis de usuário', variable=self.fil_import_user_var).pack(side='left', padx=5)
        RoundedButton(toolbar, text='+ Novo', bg='#78909C', hover_bg='#607D8B',
                     command=self._clear_filament_form,
                     tooltip='Limpar formulário para novo cadastro').pack(side='left', padx=5)
        content = ttk.Frame(main)
        content.pack(fill='both', expand=True)

        left = ttk.LabelFrame(content, text='Dados do Filamento', padding=12)
        left.pack(side='left', fill='y', padx=(0, 10))

        fields = [
            ('Nome:', 'fil_name'),
            ('Marca:', 'fil_brand'),
            ('Material:', ('fil_material', ['PLA', 'PLA+', 'PETG', 'ABS', 'TPU', 'Nylon', 'PC', 'ASA', 'PP', 'Outro'])),
            ('Cor:', 'fil_color'),
            ('Diâmetro (mm):', ('fil_diameter', ['1.75', '2.85', '3.00'])),
            ('Densidade (g/cm³):', ('fil_density', ['1.24', '1.27', '1.23', '1.04', '1.10', '1.20', '1.40'])),
            ('Preço por Kg (R$):', 'fil_price'),
        ]

        self.fil_widgets = {}
        self.fil_id_var = tk.StringVar()

        for i, (label, key) in enumerate(fields):
            ttk.Label(left, text=label).grid(row=i, column=0, sticky='w', pady=4)
            if isinstance(key, tuple):
                name, values = key
                w = ttk.Combobox(left, values=values, width=30, state='readonly')
                w.set(values[0])
                self.fil_widgets[name] = w
                w.grid(row=i, column=1, padx=5, pady=4)
            else:
                w = ttk.Entry(left, width=33)
                self.fil_widgets[key] = w
                w.grid(row=i, column=1, padx=5, pady=4)
                w.bind('<Return>', lambda e: self._save_filament())

        btn_frame = ttk.Frame(left)
        btn_frame.grid(row=len(fields), column=0, columnspan=2, pady=15)
        RoundedButton(btn_frame, text='Salvar', bg=SUCCESS, hover_bg='#15803D',
                     command=self._save_filament).pack(side='left', padx=3)
        RoundedButton(btn_frame, text='Limpar', bg='#78909C', hover_bg='#607D8B', fg='white',
                     command=self._clear_filament_form).pack(side='left', padx=3)
        RoundedButton(btn_frame, text='Excluir', bg=ERROR, hover_bg='#B91C1C',
                     command=self._delete_filament).pack(side='left', padx=3)

        right = ttk.LabelFrame(content, text='Filamentos Cadastrados', padding=8)
        right.pack(side='right', fill='both', expand=True)

        columns = ('id', 'name', 'brand', 'material', 'price')
        self.fil_tree = ttk.Treeview(right, columns=columns, show='headings', height=18, selectmode='extended')
        self.fil_tree.heading('id', text='#')
        self.fil_tree.heading('name', text='Nome')
        self.fil_tree.heading('brand', text='Marca')
        self.fil_tree.heading('material', text='Material')
        self.fil_tree.heading('price', text='R$/Kg')
        self.fil_tree.column('id', width=40, anchor='center')
        self.fil_tree.column('name', width=180)
        self.fil_tree.column('brand', width=130)
        self.fil_tree.column('material', width=90)
        self.fil_tree.column('price', width=80, anchor='e')

        scroll = ttk.Scrollbar(right, orient='vertical', command=self.fil_tree.yview)
        self.fil_tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        self.fil_tree.pack(fill='both', expand=True)
        self.fil_tree.bind('<<TreeviewSelect>>', self._on_filament_select)

        self._load_filaments()

    def _load_filaments(self):
        for item in self.fil_tree.get_children():
            self.fil_tree.delete(item)
        for f in Filament.get_all():
            self.fil_tree.insert('', 'end', values=(f.id, f.name, f.brand, f.material, f'R$ {f.price_per_kg:.2f}'))

    def _on_filament_select(self, event):
        sel = self.fil_tree.selection()
        if not sel:
            return
        vals = self.fil_tree.item(sel[0], 'values')
        if not vals:
            return
        f = Filament.get_by_id(vals[0])
        if not f:
            return
        self.fil_id_var.set(str(f.id))
        self.fil_widgets['fil_name'].delete(0, 'end')
        self.fil_widgets['fil_name'].insert(0, f.name)
        self.fil_widgets['fil_brand'].delete(0, 'end')
        self.fil_widgets['fil_brand'].insert(0, f.brand)
        self.fil_widgets['fil_material'].set(f.material)
        self.fil_widgets['fil_color'].delete(0, 'end')
        self.fil_widgets['fil_color'].insert(0, f.color)
        self.fil_widgets['fil_diameter'].set(str(f.diameter))
        self.fil_widgets['fil_price'].delete(0, 'end')
        self.fil_widgets['fil_price'].insert(0, str(f.price_per_kg))
        if 'fil_density' in self.fil_widgets:
            self.fil_widgets['fil_density'].set(str(f.density))

    def _save_filament(self):
        name = self.fil_widgets['fil_name'].get().strip()
        if not name:
            messagebox.showwarning('Aviso', 'Nome obrigatório.')
            return
        try:
            price = float(self.fil_widgets['fil_price'].get().strip())
        except ValueError:
            messagebox.showwarning('Aviso', 'Preço inválido.')
            return

        f = Filament()
        if self.fil_id_var.get():
            f.id = int(self.fil_id_var.get())
        f.name = name
        f.brand = self.fil_widgets['fil_brand'].get().strip()
        f.material = self.fil_widgets['fil_material'].get()
        f.color = self.fil_widgets['fil_color'].get().strip()
        try:
            f.diameter = float(self.fil_widgets['fil_diameter'].get())
        except:
            f.diameter = 1.75
        try:
            f.density = float(self.fil_widgets['fil_density'].get())
        except:
            f.density = 1.24
        f.price_per_kg = price
        f.save()
        self._clear_filament_form()
        self._load_filaments()
        self._refresh_dashboard()
        messagebox.showinfo('Sucesso', 'Filamento salvo!')

    def _clear_filament_form(self):
        self.fil_id_var.set('')
        for key, w in self.fil_widgets.items():
            if isinstance(w, ttk.Combobox):
                w.set(w['values'][0] if w['values'] else '')
            else:
                w.delete(0, 'end')

    def _delete_filament(self):
        selected = self.fil_tree.selection()
        if not selected:
            messagebox.showwarning('Aviso', 'Selecione um ou mais filamentos.')
            return
        count = len(selected)
        msg = f'Excluir {count} filamento(s)?' if count > 1 else 'Excluir este filamento?'
        if messagebox.askyesno('Confirmar', msg):
            for item in selected:
                values = self.fil_tree.item(item, 'values')
                if values:
                    f = Filament.get_by_id(int(values[0]))
                    if f:
                        f.delete()
            self._clear_filament_form()
            self._load_filaments()
            self._refresh_dashboard()

    def _import_filaments_orca(self):
        self._import_filaments_from_slicers()

    # ─── PRINTERS TAB ──────────────────────────────────────────────

    def _build_printers_tab(self):
        main = ttk.Frame(self.tab_printers)
        main.pack(fill='both', expand=True, padx=15, pady=15)

        ttk.Label(main, text='Gerenciar Impressoras', style='Header.TLabel').pack(anchor='w')
        ttk.Label(main, text='Cadastre ou importe impressoras dos slicers', style='SubHeader.TLabel').pack(anchor='w', pady=(0, 15))

        self.pr_import_user_var = tk.BooleanVar(value=False)
        toolbar = ttk.Frame(main)
        toolbar.pack(fill='x', pady=(0, 10))
        RoundedButton(toolbar, text='Importar dos Slicers', bg='#546E7A', hover_bg='#455A64',
                     command=self._import_printers_from_slicers,
                     tooltip='Importar impressoras dos slicers instalados').pack(side='left', padx=(0, 5))
        ttk.Checkbutton(toolbar, text='Só perfis de usuário', variable=self.pr_import_user_var).pack(side='left', padx=5)
        RoundedButton(toolbar, text='+ Novo', bg='#78909C', hover_bg='#607D8B',
                     command=self._clear_printer_form,
                     tooltip='Limpar formulário para novo cadastro').pack(side='left', padx=5)

        content = ttk.Frame(main)
        content.pack(fill='both', expand=True)

        left = ttk.LabelFrame(content, text='Dados da Impressora', padding=12)
        left.pack(side='left', fill='y', padx=(0, 10))

        fields = [
            ('Nome:', 'pr_name'),
            ('Modelo:', 'pr_model'),
            ('Fabricante:', 'pr_manufacturer'),
            ('Preço Aquisição (R$):', 'pr_price'),
            ('Potência (Watts):', 'pr_power'),
            ('Vida útil (horas):', 'pr_lifespan'),
            ('Manutenção (R$/h):', 'pr_maintenance'),
        ]

        self.pr_widgets = {}
        self.pr_id_var = tk.StringVar()

        for i, (label, key) in enumerate(fields):
            ttk.Label(left, text=label).grid(row=i, column=0, sticky='w', pady=4)
            w = ttk.Entry(left, width=33)
            self.pr_widgets[key] = w
            w.grid(row=i, column=1, padx=5, pady=4)
            w.bind('<Return>', lambda e: self._save_printer())
            if key == 'pr_lifespan':
                w.insert(0, '10000')
            elif key == 'pr_maintenance':
                w.insert(0, '0')

        btn_frame = ttk.Frame(left)
        btn_frame.grid(row=len(fields), column=0, columnspan=2, pady=15)
        RoundedButton(btn_frame, text='Salvar', bg=SUCCESS, hover_bg='#15803D',
                     command=self._save_printer).pack(side='left', padx=3)
        RoundedButton(btn_frame, text='Limpar', bg='#78909C', hover_bg='#607D8B', fg='white',
                     command=self._clear_printer_form).pack(side='left', padx=3)
        RoundedButton(btn_frame, text='Excluir', bg=ERROR, hover_bg='#B91C1C',
                     command=self._delete_printer).pack(side='left', padx=3)

        right = ttk.LabelFrame(content, text='Impressoras Cadastradas', padding=8)
        right.pack(side='right', fill='both', expand=True)

        columns = ('id', 'name', 'model', 'price', 'power')
        self.pr_tree = ttk.Treeview(right, columns=columns, show='headings', height=18, selectmode='extended')
        self.pr_tree.heading('id', text='#')
        self.pr_tree.heading('name', text='Nome')
        self.pr_tree.heading('model', text='Modelo')
        self.pr_tree.heading('price', text='Preço')
        self.pr_tree.heading('power', text='Watts')
        self.pr_tree.column('id', width=40, anchor='center')
        self.pr_tree.column('name', width=180)
        self.pr_tree.column('model', width=160)
        self.pr_tree.column('price', width=100, anchor='e')
        self.pr_tree.column('power', width=70, anchor='center')

        scroll = ttk.Scrollbar(right, orient='vertical', command=self.pr_tree.yview)
        self.pr_tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        self.pr_tree.pack(fill='both', expand=True)
        self.pr_tree.bind('<<TreeviewSelect>>', self._on_printer_select)

        self._load_printers()

    def _load_printers(self):
        for item in self.pr_tree.get_children():
            self.pr_tree.delete(item)
        for p in Printer.get_all():
            self.pr_tree.insert('', 'end', values=(p.id, p.name, p.model, f'R$ {p.purchase_price:.2f}', p.power_watts))

    def _on_printer_select(self, event):
        sel = self.pr_tree.selection()
        if not sel:
            return
        vals = self.pr_tree.item(sel[0], 'values')
        if not vals:
            return
        p = Printer.get_by_id(vals[0])
        if not p:
            return
        self.pr_id_var.set(str(p.id))
        self.pr_widgets['pr_name'].delete(0, 'end')
        self.pr_widgets['pr_name'].insert(0, p.name)
        self.pr_widgets['pr_model'].delete(0, 'end')
        self.pr_widgets['pr_model'].insert(0, p.model)
        if 'pr_manufacturer' in self.pr_widgets:
            self.pr_widgets['pr_manufacturer'].delete(0, 'end')
            self.pr_widgets['pr_manufacturer'].insert(0, getattr(p, 'manufacturer', ''))
        self.pr_widgets['pr_price'].delete(0, 'end')
        self.pr_widgets['pr_price'].insert(0, str(p.purchase_price))
        self.pr_widgets['pr_power'].delete(0, 'end')
        self.pr_widgets['pr_power'].insert(0, str(p.power_watts))
        self.pr_widgets['pr_lifespan'].delete(0, 'end')
        self.pr_widgets['pr_lifespan'].insert(0, str(p.lifespan_hours))
        self.pr_widgets['pr_maintenance'].delete(0, 'end')
        self.pr_widgets['pr_maintenance'].insert(0, str(p.maintenance_cost_per_hour))

    def _save_printer(self):
        name = self.pr_widgets['pr_name'].get().strip()
        if not name:
            messagebox.showwarning('Aviso', 'Nome obrigatório.')
            return
        try:
            price = float(self.pr_widgets['pr_price'].get().strip())
            power = float(self.pr_widgets['pr_power'].get().strip())
            lifespan = int(self.pr_widgets['pr_lifespan'].get().strip())
            maint = float(self.pr_widgets['pr_maintenance'].get().strip() or '0')
        except ValueError:
            messagebox.showwarning('Aviso', 'Valores inválidos.')
            return

        p = Printer()
        if self.pr_id_var.get():
            p.id = int(self.pr_id_var.get())
        p.name = name
        p.model = self.pr_widgets['pr_model'].get().strip()
        p.purchase_price = price
        p.power_watts = power
        p.lifespan_hours = lifespan
        p.maintenance_cost_per_hour = maint
        p.save()
        self._clear_printer_form()
        self._load_printers()
        self._refresh_dashboard()
        messagebox.showinfo('Sucesso', 'Impressora salva!')

    def _clear_printer_form(self):
        self.pr_id_var.set('')
        defaults = {'pr_lifespan': '10000', 'pr_maintenance': '0'}
        for key, w in self.pr_widgets.items():
            w.delete(0, 'end')
            if key in defaults:
                w.insert(0, defaults[key])

    def _delete_printer(self):
        selected = self.pr_tree.selection()
        if not selected:
            messagebox.showwarning('Aviso', 'Selecione uma ou mais impressoras.')
            return
        count = len(selected)
        msg = f'Excluir {count} impressora(s)?' if count > 1 else 'Excluir esta impressora?'
        if messagebox.askyesno('Confirmar', msg):
            for item in selected:
                values = self.pr_tree.item(item, 'values')
                if values:
                    p = Printer.get_by_id(int(values[0]))
                    if p:
                        p.delete()
            self._clear_printer_form()
            self._load_printers()
            self._refresh_dashboard()

    def _import_printers_from_slicers(self):
        available = find_importable_slicers()
        if not available:
            messagebox.showinfo('Importar', 'Nenhum slicer compatível encontrado.\n\nVerifique se o OrcaSlicer, Bambu Studio ou Anycubic Slicer Next estão instalados.')
            return

        user_only = self.pr_import_user_var.get() if hasattr(self, 'pr_import_user_var') else False
        loading = LoadingDialog(self.root, 'Escaneando impressoras...')
        try:
            printers = slicer_scan_printers(user_only=user_only)
        finally:
            loading.close()

        if not printers:
            messagebox.showinfo('Importar', 'Nenhuma impressora encontrada nos slicers.')
            return

        imported = 0
        existing_names = {p.name for p in Printer.get_all()}
        for pd in printers:
            if pd['name'] in existing_names:
                continue
            p = Printer()
            p.name = pd['name']
            p.model = pd.get('model', '')
            p.purchase_price = pd.get('purchase_price', 0)
            p.power_watts = pd.get('power_watts', 350)
            p.lifespan_hours = pd.get('lifespan_hours', 10000)
            p.maintenance_cost_per_hour = pd.get('maintenance_cost_per_hour', 0)
            p.save()
            imported += 1
        self._load_printers()
        self._refresh_dashboard()
        self._update_status_bar(f'{imported} impressoras importadas' if imported else 'Nenhuma impressora nova')
        if imported > 0:
            slicers_str = ', '.join(available)
            messagebox.showinfo('Sucesso', f'{imported} impressoras importadas de: {slicers_str}')
        else:
            messagebox.showinfo('Importar', 'Nenhuma impressora nova encontrada (já existem no sistema).')

    # ─── QUOTE TAB ─────────────────────────────────────────────────

    def _build_quote_tab(self):
        main = ttk.Frame(self.tab_quote)
        main.pack(fill='both', expand=True, padx=15, pady=15)

        ttk.Label(main, text='Novo Orçamento', style='Header.TLabel').pack(anchor='w')
        ttk.Label(main, text='Importe um G-Code e calcule automaticamente o custo de impressão',
                 style='SubHeader.TLabel').pack(anchor='w', pady=(0, 15))

        cols = ttk.Frame(main)
        cols.pack(fill='both', expand=True)

        # ---- LEFT COLUMN: G-code + thumbnail ----
        left_col = ttk.Frame(cols)
        left_col.pack(side='left', fill='both', expand=True, padx=(0, 10))

        gcode_frame = ttk.LabelFrame(left_col, text='Arquivo G-Code', padding=10)
        gcode_frame.pack(fill='x')

        path_frame = ttk.Frame(gcode_frame)
        path_frame.pack(fill='x')
        self.quote_gcode_path = ttk.Entry(path_frame, width=45)
        self.quote_gcode_path.pack(side='left', fill='x', expand=True)
        self.quote_select_btn = RoundedButton(path_frame, text='Selecionar', width=90,
                     command=self._select_gcode,
                     tooltip='Selecionar arquivo .gcode ou .3mf')
        self.quote_select_btn.pack(side='right', padx=(5, 0))
        self.quote_slice_btn = RoundedButton(path_frame, text='Fatiar .3mf', width=90,
                     bg='#546E7A', hover_bg='#455A64', fg='white',
                     command=self._slice_3mf_file,
                     tooltip='Estimar custo pela geometria do .3mf (sem fatiar)')
        self.quote_slice_btn.pack(side='right', padx=(5, 0))

        self.slicer_status = tk.Label(gcode_frame, text='', font=('Segoe UI', 8),
                                      fg=TEXT_SECONDARY, bg=BG_LIGHT)
        self.slicer_status.pack(anchor='w', pady=(2, 0))

        self.thumb_frame = tk.Frame(gcode_frame, bg='#EEEEEE', width=300, height=300,
                                    highlightbackground='#E0E0E0', highlightthickness=1)
        self.thumb_frame.pack(pady=10, anchor='center')
        self.thumb_frame.pack_propagate(False)
        self.thumb_label = tk.Label(self.thumb_frame, text='Miniatura não disponível',
                                   bg='#EEEEEE', fg='#999', font=('Segoe UI', 10))
        self.thumb_label.pack(expand=True)

        # Extracted data
        data_frame = ttk.LabelFrame(left_col, text='Dados Extraídos do G-Code', padding=10)
        data_frame.pack(fill='x', pady=(10, 0))

        self.quote_info = {}
        info_fields = [
            ('Peso estimado:', 'weight', '0 g'),
            ('Comprimento filamento:', 'length', '0 mm'),
            ('Tempo de impressão:', 'time', '0 min'),
            ('Camadas:', 'layers', '0'),
            ('Cores:', 'colors', '-'),
            ('Resíduos:', 'waste', '0 g'),
            ('Impressora:', 'printer', '-'),
        ]
        for i, (label, key, default) in enumerate(info_fields):
            ttk.Label(data_frame, text=label).grid(row=i, column=0, sticky='w', pady=3)
            lbl = ttk.Label(data_frame, text=default, foreground=PRIMARY, font=('Segoe UI', 10, 'bold'))
            lbl.grid(row=i, column=1, sticky='w', padx=(10, 0), pady=3)
            self.quote_info[key] = lbl

        # Manual override
        manual_frame = ttk.LabelFrame(left_col, text='Ajuste Manual', padding=8)
        manual_frame.pack(fill='x', pady=(10, 0))

        ttk.Label(manual_frame, text='Peso (g):').grid(row=0, column=0, sticky='w', padx=5)
        self.manual_weight = ttk.Entry(manual_frame, width=12)
        self.manual_weight.grid(row=0, column=1, padx=5)
        self.manual_weight.bind('<Return>', lambda e: self._calculate_quote())

        ttk.Label(manual_frame, text='Tempo (min):').grid(row=0, column=2, sticky='w', padx=(10, 5))
        self.manual_time = ttk.Entry(manual_frame, width=12)
        self.manual_time.grid(row=0, column=3, padx=5)
        self.manual_time.bind('<Return>', lambda e: self._calculate_quote())

        # ---- RIGHT COLUMN: Selection + Costs ----
        right_col = ttk.Frame(cols)
        right_col.pack(side='right', fill='both', expand=True, padx=(10, 0))

        sel_frame = ttk.LabelFrame(right_col, text='Seleção', padding=10)
        sel_frame.pack(fill='x')

        ttk.Label(sel_frame, text='Impressora:').grid(row=0, column=0, sticky='w', pady=4)
        self.quote_printer = ttk.Combobox(sel_frame, state='readonly', width=35)
        self.quote_printer.grid(row=0, column=1, padx=5, pady=4)

        ttk.Label(sel_frame, text='Filamento:').grid(row=1, column=0, sticky='w', pady=4)
        self.quote_filament = ttk.Combobox(sel_frame, state='readonly', width=35)
        self.quote_filament.grid(row=1, column=1, padx=5, pady=4)

        calc_frame = tk.Frame(sel_frame, bg=BG_LIGHT)
        calc_frame.grid(row=2, column=0, columnspan=2, pady=10)
        RoundedButton(calc_frame, text='Calcular Custo',
                     command=self._calculate_quote).pack()

        # Cost breakdown
        cost_frame = ttk.LabelFrame(right_col, text='Custos Calculados', padding=12)
        cost_frame.pack(fill='x', pady=(10, 0))

        self.quote_costs = {}
        cost_items = [
            ('filament', 'Filamento', 'R$ 0,00'),
            ('energy', 'Energia elétrica', 'R$ 0,00'),
            ('depreciation', 'Depreciação', 'R$ 0,00'),
            ('maintenance', 'Manutenção', 'R$ 0,00'),
        ]
        for i, (key, label, default) in enumerate(cost_items):
            ttk.Label(cost_frame, text=label).grid(row=i, column=0, sticky='w', pady=3)
            lbl = ttk.Label(cost_frame, text=default, foreground=TEXT_SECONDARY)
            lbl.grid(row=i, column=1, sticky='e', padx=(10, 0), pady=3)
            self.quote_costs[key] = lbl

        ttk.Separator(cost_frame, orient='horizontal').grid(row=4, column=0, columnspan=2, sticky='ew', pady=8)
        self.quote_total_label = ttk.Label(cost_frame, text='Custo Total:', style='Total.TLabel')
        self.quote_total_label.grid(row=5, column=0, sticky='w')
        self.quote_total_value = ttk.Label(cost_frame, text='R$ 0,00', style='Total.TLabel')
        self.quote_total_value.grid(row=5, column=1, sticky='e')

        # Suggested price
        price_frame = ttk.LabelFrame(right_col, text='Preço Sugerido', padding=12)
        price_frame.pack(fill='x', pady=(10, 0))

        margin_frame = ttk.Frame(price_frame)
        margin_frame.pack(fill='x')
        ttk.Label(margin_frame, text='Margem de lucro:').pack(side='left')
        self.profit_margin_var = tk.StringVar(value='30')
        self.profit_margin_entry = ttk.Entry(margin_frame, textvariable=self.profit_margin_var, width=6, justify='center')
        self.profit_margin_entry.pack(side='left', padx=5)
        self.profit_margin_entry.bind('<Return>', lambda e: self._calculate_quote())
        ttk.Label(margin_frame, text='%').pack(side='left')

        self.suggested_price_label = ttk.Label(price_frame, text='R$ 0,00',
                                              font=('Segoe UI', 20, 'bold'), foreground=SUCCESS)
        self.suggested_price_label.pack(pady=5)

        save_frame = ttk.Frame(right_col)
        save_frame.pack(fill='x', pady=10)
        self.quote_name_var = tk.StringVar(value='')
        ttk.Label(save_frame, text='Nome do orçamento:').pack(side='left')
        name_ent = ttk.Entry(save_frame, textvariable=self.quote_name_var, width=25)
        name_ent.pack(side='left', padx=5)
        name_ent.bind('<Return>', lambda e: self._save_quote())
        RoundedButton(save_frame, text='Salvar Orçamento', bg=SUCCESS, hover_bg='#15803D',
                     command=self._save_quote).pack(side='left', padx=5)

        self._refresh_quote_combos()

    def _refresh_quote_combos(self):
        printers = Printer.get_all()
        self.quote_printer['values'] = [f'{p.id} - {p.name} ({p.model})' for p in printers]
        if printers:
            self.quote_printer.current(0)

        filaments = Filament.get_all()
        self.quote_filament['values'] = [f'{f.id} - {f.name} ({f.material})' for f in filaments]
        if filaments:
            self.quote_filament.current(0)

    def _select_gcode(self):
        path = filedialog.askopenfilename(
            title='Selecionar Arquivo',
            filetypes=[('Arquivos 3D', '*.gcode *.gc *.gco *.nc *.3mf'), ('Todos', '*.*')]
        )
        if path:
            self.quote_gcode_path.delete(0, 'end')
            self.quote_gcode_path.insert(0, path)
            self._generated_gcode_path = None
            ext = os.path.splitext(path)[1].lower()
            if ext == '.3mf':
                self.quote_slice_btn.config(bg=PRIMARY, hover_bg=PRIMARY_DARK)
                self.slicer_status.config(text='Use o botão "Fatiar .3mf" para estimar custo pela geometria',
                                          fg=TEXT_SECONDARY)
            else:
                self.quote_slice_btn.config(bg='#BDBDBD', hover_bg='#BDBDBD')
                self.slicer_status.config(text='')
                loading = LoadingDialog(self.root, 'Analisando G-Code...')
                try:
                    self._parse_gcode(path)
                finally:
                    loading.close()

    def _parse_gcode(self, path):
        try:
            parser = GCodeParser(path)
            result = parser.parse()

            if 'error' in result:
                for k in self.quote_info:
                    self.quote_info[k].config(text='Erro')
                messagebox.showerror('Erro', f'Falha ao processar G-Code:\n{result["error"]}')
                return

            weight = result.get('estimated_weight_grams', 0)
            length = result.get('filament_length_mm', 0)
            time_sec = result.get('print_time_seconds', 0)
            layers = result.get('layer_count', 0)
            thumb_data = result.get('thumbnail_data')
            thumb_size = result.get('thumbnail_size')

            self.quote_info['weight'].config(text=f'{weight:.2f} g')
            self.quote_info['length'].config(text=f'{length:.2f} mm')
            time_min = time_sec // 60
            if time_sec >= 3600:
                time_str = f'{time_sec//3600}h {(time_sec%3600)//60:02d}min'
            else:
                time_str = f'{time_min}min'
            self.quote_info['time'].config(text=time_str)
            self.quote_info['layers'].config(text=str(layers))
            self.quote_info['colors'].config(text='-')
            self.quote_info['waste'].config(text='0 g')
            self.quote_info['printer'].config(text='-')

            self.manual_weight.delete(0, 'end')
            self.manual_weight.insert(0, f'{weight:.1f}')
            self.manual_time.delete(0, 'end')
            self.manual_time.insert(0, str(time_min))

            self._display_thumbnail(thumb_data, thumb_size)

            basename = os.path.basename(path)
            name = os.path.splitext(basename)[0]
            self.quote_name_var.set(name)
        except Exception as e:
            messagebox.showerror('Erro', f'Falha ao processar G-Code:\n{str(e)}')

    def _slice_3mf_file(self):
        path = self.quote_gcode_path.get().strip()
        if not path or not os.path.isfile(path):
            messagebox.showwarning('Aviso', 'Selecione um arquivo .3mf primeiro.')
            return

        filament_text = self.quote_filament.get()
        material = 'PLA'
        density = 1.24
        try:
            fid = filament_text.split(' - ')[0] if ' - ' in filament_text else '0'
            from app.filament import Filament
            f = Filament.get_by_id(int(fid))
            if f:
                material = f.material
        except:
            pass

        loading = LoadingDialog(self.root, 'Estimando a partir do .3mf...')
        try:
            result = mesh_estimate_3mf(path, density, material)
        finally:
            loading.close()

        if 'error' in result:
            messagebox.showerror('Erro', f'Falha ao processar .3mf:\n{result["error"]}')
            return

        self._display_mesh_result(result, path)

    def _display_mesh_result(self, result, path):
        weight = result.get('estimated_weight_grams', 0)
        length = result.get('filament_length_mm', 0)
        time_sec = result.get('print_time_seconds', 0)
        layers = result.get('layer_count', 0)

        self.quote_info['weight'].config(text=f'{weight:.2f} g')
        self.quote_info['length'].config(text=f'{length:.2f} mm')
        time_min = time_sec // 60
        if time_sec >= 3600:
            time_str = f'{time_sec//3600}h {(time_sec%3600)//60:02d}min'
        else:
            time_str = f'{time_min}min'
        self.quote_info['time'].config(text=time_str)
        self.quote_info['layers'].config(text=str(layers))

        color_count = result.get('color_count', 1)
        purge_waste = result.get('purge_waste', {})
        filaments = result.get('filaments', [])
        printer = result.get('printer', {})

        if color_count > 1:
            color_labels = []
            for f in filaments:
                c = f.get('colour', '')
                label = c
                if f.get('vendor'):
                    label = '%s %s' % (f['vendor'], f.get('type', '?'))
                color_labels.append(label)
            colors_text = '%d cores: %s' % (color_count, ', '.join(color_labels[:4]))
            if color_count > 4:
                colors_text += '...'
        else:
            colors_text = '1 filamento'
        self.quote_info['colors'].config(text=colors_text)

        waste_g = purge_waste.get('waste_grams', 0)
        if waste_g > 0 and color_count > 1:
            self.quote_info['waste'].config(text='%.2f g (purge)' % waste_g)
        else:
            self.quote_info['waste'].config(text='0 g')

        pmodel = printer.get('model', printer.get('machine_name', ''))
        pnozzle = printer.get('nozzle_diameter', '')
        if pmodel:
            printer_text = pmodel
            if pnozzle:
                printer_text += ' (%smm)' % str(pnozzle)
        else:
            printer_text = '-'
        self.quote_info['printer'].config(text=printer_text)

        self.manual_weight.delete(0, 'end')
        self.manual_weight.insert(0, f'{weight:.1f}')
        self.manual_time.delete(0, 'end')
        self.manual_time.insert(0, str(time_min))

        thumb = extract_thumbnail_from_3mf(path)
        if thumb:
            import io
            buf = io.BytesIO()
            thumb.save(buf, format='PNG')
            self._display_thumbnail(buf.getvalue(), None)
        else:
            for w in self.thumb_frame.winfo_children():
                w.destroy()
            self.thumb_label = None
            self.quote_thumbnail_tk = None
            self.quote_thumbnail_pil = None

        basename = os.path.basename(path)
        name = os.path.splitext(basename)[0]
        self.quote_name_var.set(name)
        self.slicer_status.config(text='Estimativa baseada na geometria da malha 3D', fg=TEXT_SECONDARY)

    def _display_thumbnail(self, thumb_data, thumb_size):
        for w in self.thumb_frame.winfo_children():
            w.destroy()
        self.thumb_label = None
        self.quote_thumbnail_tk = None
        self.quote_thumbnail_pil = None

        if not thumb_data:
            self.thumb_label = tk.Label(self.thumb_frame, text='Sem miniatura\ndisponível',
                                       bg='#EEEEEE', fg='#999', font=('Segoe UI', 10))
            self.thumb_label.pack(expand=True)
            return

        try:
            from PIL import Image, ImageTk
            img = Image.open(io.BytesIO(thumb_data))

            max_size = 280
            w, h = img.size
            if w > max_size or h > max_size:
                ratio = min(max_size/w, max_size/h)
                img = img.resize((int(w*ratio), int(h*ratio)), Image.LANCZOS)

            self.quote_thumbnail_pil = img
            self.quote_thumbnail_tk = ImageTk.PhotoImage(img)

            lbl = tk.Label(self.thumb_frame, image=self.quote_thumbnail_tk, bg='#EEEEEE')
            lbl.pack(expand=True)
        except ImportError:
            tk.Label(self.thumb_frame, text='Miniatura disponível\n(instale Pillow para exibir)',
                    bg='#EEEEEE', fg='#666').pack(expand=True)
        except Exception:
            tk.Label(self.thumb_frame, text='Erro ao carregar\nminiatura',
                    bg='#EEEEEE', fg='#999').pack(expand=True)

    def _calculate_quote(self):
        try:
            printer_text = self.quote_printer.get()
            filament_text = self.quote_filament.get()

            if not printer_text or not filament_text:
                messagebox.showwarning('Aviso', 'Selecione impressora e filamento.')
                return

            try:
                printer_id = int(printer_text.split(' - ')[0])
                filament_id = int(filament_text.split(' - ')[0])
            except (IndexError, ValueError):
                messagebox.showwarning('Aviso', 'Seleção inválida.')
                return

            printer = Printer.get_by_id(printer_id)
            filament = Filament.get_by_id(filament_id)
            if not printer or not filament:
                messagebox.showwarning('Aviso', 'Item não encontrado.')
                return

            try:
                weight = float(self.manual_weight.get().strip() or '0')
                time_min = int(self.manual_time.get().strip() or '0')
            except ValueError:
                messagebox.showwarning('Aviso', 'Peso ou tempo inválidos.')
                return

            if weight <= 0 or time_min <= 0:
                messagebox.showwarning('Aviso', 'Peso e tempo devem ser maiores que zero.')
                return

            calc = CostCalculator(printer, filament)
            costs = calc.calculate(weight, time_min)

            self.quote_costs['filament'].config(text=f'R$ {costs["filament_cost"]:.2f}')
            self.quote_costs['energy'].config(text=f'R$ {costs["energy_cost"]:.2f}')
            self.quote_costs['depreciation'].config(text=f'R$ {costs["depreciation_cost"]:.2f}')
            self.quote_costs['maintenance'].config(text=f'R$ {costs["maintenance_cost"]:.2f}')
            self.quote_total_value.config(text=f'R$ {costs["total_cost"]:.2f}')

            try:
                margin = float(self.profit_margin_var.get()) / 100
            except ValueError:
                margin = 0.3
            suggested = costs['total_cost'] * (1 + margin)
            self.suggested_price_label.config(text=f'R$ {suggested:.2f}')

            self.current_quote_data = {
                'name': self.quote_name_var.get() or f'Orçamento {datetime.now().strftime("%d/%m/%Y %H:%M")}',
                'printer_id': printer_id,
                'filament_id': filament_id,
                'gcode_file': os.path.basename(self._generated_gcode_path or self.quote_gcode_path.get() or ''),
                'filament_used_grams': weight,
                'print_time_minutes': time_min,
                'filament_cost': costs['filament_cost'],
                'energy_cost': costs['energy_cost'],
                'depreciation_cost': costs['depreciation_cost'],
                'maintenance_cost': costs['maintenance_cost'],
                'total_cost': costs['total_cost'],
                'suggested_price': suggested,
                'profit_margin': margin * 100,
            }
        except Exception as e:
            messagebox.showerror('Erro', f'Falha ao calcular custo:\n{str(e)}')

    def _clear_quote_form(self):
        self.current_quote_data = None
        self._generated_gcode_path = None
        self.quote_gcode_path.delete(0, 'end')
        self.quote_name_var.set('')
        self.manual_weight.delete(0, 'end')
        self.manual_time.delete(0, 'end')
        for key in self.quote_info:
            self.quote_info[key].config(text='0')
        for key in self.quote_costs:
            self.quote_costs[key].config(text='R$ 0,00')
        self.quote_total_value.config(text='R$ 0,00')
        self.suggested_price_label.config(text='R$ 0,00')
        self._display_thumbnail(None, None)
        self.quote_slice_btn.config(bg='#BDBDBD', hover_bg='#BDBDBD')
        self.slicer_status.config(text='')

    def _save_quote(self):
        try:
            if not self.current_quote_data:
                messagebox.showwarning('Aviso', 'Calcule o custo primeiro.')
                return
            name = self.quote_name_var.get().strip()
            if name:
                self.current_quote_data['name'] = name
            conn = get_connection()
            cursor = conn.cursor()

            thumb_blob = None
            if self.quote_thumbnail_pil:
                buf = io.BytesIO()
                try:
                    self.quote_thumbnail_pil.save(buf, format='PNG')
                    thumb_blob = buf.getvalue()
                except:
                    thumb_blob = None

            cursor.execute('''
                INSERT INTO quotes (name, printer_id, filament_id, gcode_file,
                    thumbnail_data, filament_used_grams, print_time_minutes,
                    filament_cost, energy_cost, depreciation_cost, maintenance_cost,
                    total_cost, suggested_price, profit_margin)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                self.current_quote_data['name'],
                self.current_quote_data['printer_id'],
                self.current_quote_data['filament_id'],
                self.current_quote_data['gcode_file'],
                thumb_blob,
                self.current_quote_data['filament_used_grams'],
                self.current_quote_data['print_time_minutes'],
                self.current_quote_data['filament_cost'],
                self.current_quote_data['energy_cost'],
                self.current_quote_data['depreciation_cost'],
                self.current_quote_data['maintenance_cost'],
                self.current_quote_data['total_cost'],
                self.current_quote_data['suggested_price'],
                self.current_quote_data['profit_margin'],
            ))

            conn.commit()
            conn.close()

            self._clear_quote_form()
            self._load_history()
            self._refresh_dashboard()
            self._update_status_bar('Orçamento salvo com sucesso!')
            messagebox.showinfo('Sucesso', 'Orçamento salvo!')
        except Exception as e:
            messagebox.showerror('Erro', f'Falha ao salvar orçamento:\n{str(e)}')

    # ─── SLICING TAB ───────────────────────────────────────────────

    def _update_slicer_info(self):
        info = self.slice_slicer_info
        slicer = self._get_cli_slicer()
        if slicer:
            info.config(text=f'Fatiador: {slicer["name"]} (CLI)', fg=SUCCESS)
        else:
            info.config(text='Fatiador: Motor Interno (nenhum CLI encontrado)', fg=WARNING)

    def _get_cli_slicer(self):
        if not hasattr(self, '_slicer_list'):
            return None
        for s in self._slicer_list:
            if s['name'] == 'OrcaSlicer' and s.get('has_cli'):
                return s
        for s in self._slicer_list:
            if s.get('has_cli'):
                return s
        return None

    def _build_slicing_tab(self):
        main = ttk.Frame(self.tab_slicing)
        main.pack(fill='both', expand=True, padx=15, pady=15)

        ttk.Label(main, text='Fatiar Arquivo .3mf', style='Header.TLabel').pack(anchor='w')
        ttk.Label(main, text='Importe, fatie e exporte G-code',
                 style='SubHeader.TLabel').pack(anchor='w', pady=(0, 12))

        # ─── File selection ─────────────────────────────────────────
        file_frame = ttk.LabelFrame(main, text='Arquivo', padding=10)
        file_frame.pack(fill='x')

        row1 = ttk.Frame(file_frame)
        row1.pack(fill='x')
        self.slice_file_entry = ttk.Entry(row1, width=60)
        self.slice_file_entry.pack(side='left', fill='x', expand=True)
        RoundedButton(row1, text='Selecionar', width=85,
                     command=self._select_slice_file).pack(side='right', padx=(5, 0))

        self.slice_slicer_info = tk.Label(file_frame, text='', font=('Segoe UI', 8),
                                          fg=SUCCESS, bg=BG_LIGHT, anchor='w')
        self.slice_slicer_info.pack(fill='x', pady=(4, 0))
        self._update_slicer_info()

        # ─── Info + Thumbnail ──────────────────────────────────────
        info_frame = ttk.LabelFrame(main, text='Informações', padding=12)
        info_frame.pack(fill='x', pady=10)

        top = ttk.Frame(info_frame)
        top.pack(fill='x')

        info_grid = ttk.Frame(top)
        info_grid.pack(side='left', fill='x', expand=True)

        self.slice_info = {}
        fields = [
            ('file_name', 'Arquivo:'),
            ('volume', 'Volume:'),
            ('height', 'Camadas:'),
            ('weight', 'Peso est.:'),
        ]
        for i, (key, label) in enumerate(fields):
            ttk.Label(info_grid, text=label, font=('Segoe UI', 9)).grid(
                row=i, column=0, sticky='w', padx=(0, 8), pady=3)
            lbl = ttk.Label(info_grid, text='-', foreground=PRIMARY,
                            font=('Segoe UI', 9, 'bold'))
            lbl.grid(row=i, column=1, sticky='w', pady=3)
            self.slice_info[key] = lbl

        self.slice_thumb_frame = tk.Frame(top, bg='#EEEEEE', width=140, height=140,
                                          highlightbackground='#E0E0E0', highlightthickness=1)
        self.slice_thumb_frame.pack(side='right', padx=(10, 0))
        self.slice_thumb_frame.pack_propagate(False)
        self.slice_thumb_label = tk.Label(self.slice_thumb_frame, text='Sem\nminiatura',
                                          bg='#EEEEEE', fg='#999', font=('Segoe UI', 9))
        self.slice_thumb_label.pack(expand=True)
        self._slice_thumbnail_tk = None

        self.slice_colors_frame = ttk.Frame(info_frame)
        self.slice_colors_frame.pack(fill='x', pady=(5, 0))
        self.slice_color_labels = []

        # ─── Actions ────────────────────────────────────────────────
        action_frame = ttk.Frame(main)
        action_frame.pack(fill='x', pady=(10, 0))

        self.slice_fatiar_btn = RoundedButton(
            action_frame, text='FATIAR', bg=PRIMARY, hover_bg=PRIMARY_DARK,
            width=120, height=38, command=self._run_slice_unified,
            tooltip='Fatia o .3mf (CLI se disponível, senão motor interno)')
        self.slice_fatiar_btn.pack(side='left', padx=(0, 6))

        self.slice_save_btn = RoundedButton(
            action_frame, text='Salvar G-code', bg=SUCCESS, hover_bg='#15803D',
            width=140, height=38, command=self._save_gcode_to_file,
            tooltip='Salva o G-code gerado no disco')
        self.slice_save_btn.pack(side='left', padx=6)

        self.slice_price_btn = RoundedButton(
            action_frame, text='Precificar', bg=SECONDARY, hover_bg='#E65100',
            width=120, height=38, command=self._go_to_pricing,
            tooltip='Leva os dados para o orçamento')
        self.slice_price_btn.pack(side='left', padx=6)

        self.slice_clear_btn = RoundedButton(
            action_frame, text='Limpar', bg='#78909C', hover_bg='#607D8B',
            width=100, height=38, command=self._clear_slice_form,
            tooltip='Limpa tudo')
        self.slice_clear_btn.pack(side='left', padx=6)

        # ─── Result ──────────────────────────────────────────────────
        self.slice_result_frame = ttk.LabelFrame(main, text='Resultado', padding=12)
        self.slice_result_frame.pack(fill='x', pady=(10, 0))

        self.slice_result_status = ttk.Label(
            self.slice_result_frame, text='', font=('Segoe UI', 10, 'bold'))
        self.slice_result_status.pack(fill='x')

        self.slice_result_detail = ttk.Label(
            self.slice_result_frame, text='', font=('Segoe UI', 9))
        self.slice_result_detail.pack(fill='x', pady=(4, 0))

        # ─── Internal state ─────────────────────────────────────────
        self._slice_mesh_result = None
        self._slice_gcode_result = None
        self._generated_gcode_path = None
        self._slice_lh_from_3mf = 0
        self._slice_3mf_thumbnail_pil = None
        self._slice_auto_printer = ''
        self._slice_auto_filament_name = ''
        self._slice_auto_filament_density = 0
        self._cli_fallback_tried = False

    def _select_slice_file(self):
        path = filedialog.askopenfilename(
            title='Selecionar Arquivo .3mf',
            filetypes=[('Arquivo 3MF', '*.3mf'), ('Todos', '*.*')]
        )
        if not path:
            return
        print(f'[SLICE DEBUG] File selected: {path}')
        print(f'[SLICE DEBUG] File size: {os.path.getsize(path)} bytes')
        self.slice_file_entry.delete(0, 'end')
        self.slice_file_entry.insert(0, path)
        self._slice_mesh_result = None
        self._slice_gcode_result = None
        self._generated_gcode_path = None
        self.slice_save_btn.config(bg=SUCCESS)
        self.slice_price_btn.config(bg=SECONDARY)
        self._analyze_slice_file(path)

    def _analyze_slice_file(self, path):
        import traceback
        print(f'[SLICE DEBUG] Analyzing .3mf...')
        self._set_result('Analisando arquivo .3mf...', TEXT_SECONDARY)
        try:
            result = mesh_estimate_3mf(path)
            if 'error' in result:
                print(f'[SLICE DEBUG] Analysis error: {result["error"]}')
                self._set_result(f'Erro: {result["error"]}', ERROR)
                return
            self._slice_mesh_result = result
            print(f'[SLICE DEBUG] Analysis OK: vol={result.get("volume_mm3",0):.0f}mm3  '
                  f'weight={result.get("estimated_weight_grams",0):.2f}g  '
                  f'layers={result.get("layer_count",0)}  '
                  f'lh={result.get("layer_height",0)}  '
                  f'printer={result.get("printer",{}).get("model","?")}')
            self._display_slice_info(result, path)
            self._set_result('Arquivo analisado. Clique FATIAR para gerar G-code.', SUCCESS)
        except Exception as e:
            print(f'[SLICE DEBUG] Analysis exception: {traceback.format_exc()}')
            self._set_result(f'Erro ao analisar: {str(e)}', ERROR)

    def _display_slice_info(self, result, path):
        self.slice_info['file_name'].config(text=os.path.basename(path))
        thumb = extract_thumbnail_from_3mf(path)
        self._show_slice_thumbnail(thumb)
        self._slice_3mf_thumbnail_pil = thumb

        self._slice_lh_from_3mf = max(result.get('layer_height', 0), 0)
        height = result.get('height_mm', 0)
        layers = result.get('layer_count', 0)
        self.slice_info['height'].config(text=f'{height:.2f}mm · {layers} camadas')
        # Use result data directly (already parsed from the .3mf)
        sli_vol = result.get('volume_mm3', 0)
        flist = result.get('filaments', [])
        fd = flist[0].get('density', 1.24) if flist else 1.24
        fill_pct = result.get('fill_density', 0.2)
        self._slice_auto_filament_density = fd
        solid_weight = sli_vol * fd / 1000.0
        # Estimate real weight: walls (~35% of solid) + infill of remaining 65%
        wall_frac = 0.35
        infill_frac = 1.0 - wall_frac
        est_weight = solid_weight * (wall_frac + infill_frac * fill_pct)
        self._slice_preview_solid_weight = solid_weight
        self._slice_preview_fill_pct = fill_pct
        fill_str = result.get('fill_density_pct', f'{fill_pct*100:.0f}%')
        self.slice_info['volume'].config(text=f'{sli_vol:.0f} mm³')
        self.slice_info['weight'].config(text=f'{est_weight:.1f}g ({fill_str})')
        self.slice_info['weight'].config(foreground=PRIMARY)

        printer = result.get('printer', {})
        self._slice_auto_printer = printer.get('model', printer.get('machine_name', ''))
        flist = result.get('filaments', [])
        self._slice_auto_filament_name = flist[0].get('name', '') if flist else ''

        for lbl in self.slice_color_labels:
            lbl.destroy()
        self.slice_color_labels = []
        for f in flist[:6]:
            col = f.get('colour', '#888')
            lbl = tk.Label(self.slice_colors_frame, text=f'● {col}', font=('Segoe UI', 9),
                          fg=col, bg=BG_LIGHT)
            lbl.pack(side='left', padx=(0, 8))
            self.slice_color_labels.append(lbl)

    def _show_slice_thumbnail(self, thumb_pil):
        for w in self.slice_thumb_frame.winfo_children():
            w.destroy()
        self._slice_thumbnail_tk = None

        if not thumb_pil:
            self.slice_thumb_label = tk.Label(self.slice_thumb_frame, text='Sem\nminiatura',
                                             bg='#EEEEEE', fg='#999', font=('Segoe UI', 9))
            self.slice_thumb_label.pack(expand=True)
            return

        try:
            from PIL import Image, ImageTk
            max_size = 130
            w, h = thumb_pil.size
            if w > max_size or h > max_size:
                ratio = min(max_size / w, max_size / h)
                thumb_pil = thumb_pil.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
            self._slice_thumbnail_tk = ImageTk.PhotoImage(thumb_pil)
            lbl = tk.Label(self.slice_thumb_frame, image=self._slice_thumbnail_tk, bg='#EEEEEE')
            lbl.pack(expand=True)
        except ImportError:
            tk.Label(self.slice_thumb_frame, text='Pillow\nausente',
                    bg='#EEEEEE', fg='#666').pack(expand=True)
        except Exception:
            tk.Label(self.slice_thumb_frame, text='Erro na\nminiatura',
                    bg='#EEEEEE', fg='#999').pack(expand=True)

    def _set_result(self, text, color=None):
        self.slice_result_status.config(text=text)
        if color:
            self.slice_result_status.config(foreground=color)
        self.root.update_idletasks()

    def _run_slice_unified(self):
        import traceback
        path = self.slice_file_entry.get().strip()
        print(f'[SLICE DEBUG] === FATIAR clicked ===')
        print(f'[SLICE DEBUG] Path: {path}')
        if not path or not os.path.isfile(path):
            print(f'[SLICE DEBUG] ERROR: file not found')
            messagebox.showwarning('Aviso', 'Selecione um arquivo .3mf primeiro.')
            return
        print(f'[SLICE DEBUG] File exists: {os.path.getsize(path)} bytes')

        self.slice_result_detail.config(text='')
        self._set_result('Fatiando...', PRIMARY)
        self.slice_fatiar_btn.config(text='FATIANDO...', bg=WARNING, command=lambda: None)
        self.root.update_idletasks()

        slicer = self._get_cli_slicer()
        if slicer:
            print(f'[SLICE DEBUG] Using CLI: {slicer["name"]} at {slicer["exe"]}')
            self._run_slice_cli(path, slicer)
        else:
            print(f'[SLICE DEBUG] No CLI found, using internal slicer')
            self._run_slice_internal(path)

    def _run_slice_cli(self, path, slicer):
        import traceback
        from app.slicer_cli import slice_3mf as cli_slice
        output_dir = tempfile.mkdtemp(prefix='custo3d_slice_')
        pn = getattr(self, '_slice_auto_printer', '')
        fn = getattr(self, '_slice_auto_filament_name', '')
        print(f'[SLICE DEBUG] CLI slicer="{slicer["name"]}" printer="{pn}" filament="{fn}"')
        print(f'[SLICE DEBUG] Output dir: {output_dir}')

        def do_slice():
            try:
                print(f'[SLICE DEBUG] Starting CLI slice thread...')
                result = cli_slice(slicer['name'], path, output_dir,
                                  printer=pn, filament=fn)
                print(f'[SLICE DEBUG] CLI slice done.')
                if 'error' in result:
                    print(f'[SLICE DEBUG] CLI error: {result["error"]}')
                    if result.get('stderr'):
                        print(f'[SLICE DEBUG] CLI stderr: {result["stderr"][:1000]}')
                    if result.get('stdout'):
                        print(f'[SLICE DEBUG] CLI stdout: {result["stdout"][:500]}')
                else:
                    gcodes = result.get('gcode_files', [])
                    print(f'[SLICE DEBUG] CLI G-code files: {gcodes}')
                self.root.after(0, lambda: self._on_slice_done(result, 'cli'))
            except Exception as e:
                print(f'[SLICE DEBUG] CLI thread exception: {traceback.format_exc()}')
                self.root.after(0, lambda: self._on_slice_done({'error': str(e)}, 'cli'))

        threading.Thread(target=do_slice, daemon=True).start()

    def _run_slice_internal(self, path):
        import traceback
        output_dir = tempfile.mkdtemp(prefix='custo3d_builtin_')
        base = os.path.splitext(os.path.basename(path))[0]
        output_path = os.path.join(output_dir, base + '.gcode')
        lh = getattr(self, '_slice_lh_from_3mf', 0)
        fn = getattr(self, '_slice_auto_filament_name', '')
        pn = getattr(self, '_slice_auto_printer', '')
        print(f'[SLICE DEBUG] Internal slice: lh={lh} printer="{pn}" filament="{fn}"')
        print(f'[SLICE DEBUG] Output: {output_path}')

        def do_slice():
            try:
                from app.slicer_engine import builtin_slice_3mf
                print(f'[SLICE DEBUG] Starting internal slice...')
                # Do NOT pass filament_density override — let the engine
                # read it from the .3mf config (same source as _scan_3mf_volumes)
                result = builtin_slice_3mf(path, output_path=output_path,
                                          layer_height=lh,
                                          filament_name=fn,
                                          printer_name=pn,
                                          plate_id=1)
                if 'error' in result:
                    print(f'[SLICE DEBUG] Internal slice error: {result["error"]}')
                else:
                    print(f'[SLICE DEBUG] Internal slice OK: '
                          f'weight={result.get("estimated_weight_grams",0):.2f}g '
                          f'density={result.get("filament_density",0)} '
                          f'layers={result.get("total_layers",0)} '
                          f'time={result.get("print_time_seconds",0)}s '
                          f'elapsed={result.get("elapsed_seconds",0):.1f}s')
                self.root.after(0, lambda: self._on_slice_done(result, 'internal', output_path))
            except Exception as e:
                print(f'[SLICE DEBUG] Internal thread exception: {traceback.format_exc()}')
                self.root.after(0, lambda: self._on_slice_done({'error': str(e)}, 'internal', output_path))

        threading.Thread(target=do_slice, daemon=True).start()

    def _on_slice_done(self, result, mode='cli', output_path=None):
        self.slice_fatiar_btn.config(text='FATIAR', bg=PRIMARY, command=self._run_slice_unified)
        weight = 0
        time_sec = 0
        layers = 0
        gcode_path = None

        if 'error' in result:
            error_msg = result['error']
            stderr = result.get('stderr', '')
            stdout = result.get('stdout', '')
            print(f'[SLICE DEBUG] === ERROR ({mode}) ===')
            print(f'[SLICE DEBUG] Error: {error_msg}')
            if stderr:
                print(f'[SLICE DEBUG] stderr (first 1000): {stderr[:1000]}')
            if stdout:
                print(f'[SLICE DEBUG] stdout (first 500): {stdout[:500]}')

            # Auto-fallback: CLI param errors → try internal slicer
            is_param_error = 'not in range' in error_msg.lower() or 'param values' in error_msg.lower()
            if mode == 'cli' and is_param_error and not self._cli_fallback_tried:
                self._cli_fallback_tried = True
                print(f'[SLICE DEBUG] CLI param error detected, falling back to internal slicer')
                self._set_result('CLI incompatível com este .3mf. Usando motor interno...', WARNING)
                path = self.slice_file_entry.get().strip()
                if path:
                    self._run_slice_internal(path)
                return

            full_detail = str(error_msg)
            if stderr:
                full_detail += f'\nstderr: {stderr[:300]}'
            if stdout:
                full_detail += f'\nstdout: {stdout[:300]}'
            self.slice_result_detail.config(text=full_detail)
            self._set_result(f'Erro: {error_msg}', ERROR)
            self._cli_fallback_tried = False
            return
        self._cli_fallback_tried = False

        if mode == 'cli':
            gcode_files = result.get('gcode_files', [])
            if not gcode_files:
                self._set_result('Nenhum G-code gerado.', ERROR)
                return
            gcode_path = gcode_files[0]
            self._generated_gcode_path = gcode_path
            slicer_label = result.get('slicer', 'CLI')
            try:
                parser = GCodeParser(gcode_path)
                parse_result = parser.parse()
                if 'error' not in parse_result:
                    self._slice_gcode_result = parse_result
                    weight = parse_result.get('estimated_weight_grams', 0)
                    time_sec = parse_result.get('print_time_seconds', 0)
                    layers = parse_result.get('layer_count', 0)
                    self._show_slice_thumbnail(
                        self._try_load_gcode_thumb(gcode_path))
            except Exception as e:
                self.slice_result_detail.config(text=f'Aviso ao ler G-code: {e}')
            self._set_result(f'Fatiado com {slicer_label}', SUCCESS)
        else:
            gcode_path = output_path
            self._generated_gcode_path = gcode_path
            weight = result.get('estimated_weight_grams', 0)
            time_sec = result.get('print_time_seconds', 0)
            layers = result.get('total_layers', 0)
            fd = result.get('filament_density', 0)
            length = result.get('filament_length_mm', 0)
            elapsed = result.get('elapsed_seconds', 0)
            thumb = self._try_load_gcode_thumb(gcode_path)
            thumb_data = None
            thumb_size = None
            if thumb:
                buf = io.BytesIO()
                thumb.save(buf, format='PNG')
                thumb_data = buf.getvalue()
                thumb_size = thumb.size
            self._slice_gcode_result = {
                'estimated_weight_grams': weight,
                'filament_length_mm': length,
                'print_time_seconds': time_sec,
                'layer_count': layers,
                'thumbnail_data': thumb_data,
                'thumbnail_size': thumb_size,
            }
            self._show_slice_thumbnail(thumb)
            self._set_result(f'Fatiado (motor interno)  {elapsed:.1f}s', SUCCESS)

        time_str = f'{time_sec//3600}h {(time_sec%3600)//60:02d}min' if time_sec >= 3600 else f'{time_sec//60}min'
        weight_color = SUCCESS
        if hasattr(self, '_slice_preview_solid_weight') and self._slice_preview_solid_weight > 0:
            ratio = weight / self._slice_preview_solid_weight
            self.slice_info['weight'].config(
                text=f'{weight:.1f}g ({ratio*100:.0f}% do sólido {self._slice_preview_solid_weight:.1f}g)')
        else:
            self.slice_info['weight'].config(text=f'{weight:.1f}g')
        self.slice_info['weight'].config(foreground=weight_color)
        detail = f'Peso: {weight:.1f}g | Tempo: {time_str} | Camadas: {layers}'
        if mode == 'internal':
            length = result.get('filament_length_mm', 0)
            detail += f'\nFilamento: {length:.0f}mm | Densidade: {result.get("filament_density", 0):.2f}g/cm³'
        self.slice_result_detail.config(text=detail)

    def _try_load_gcode_thumb(self, gcode_path):
        try:
            from app.gcode_parser import extract_thumbnail_from_gcode
            thumb = extract_thumbnail_from_gcode(gcode_path)
            if thumb:
                return thumb
        except Exception:
            pass
        return getattr(self, '_slice_3mf_thumbnail_pil', None)

    def _save_gcode_to_file(self):
        if not self._generated_gcode_path or not os.path.isfile(self._generated_gcode_path):
            messagebox.showwarning('Aviso', 'Nenhum G-code gerado. Fatia o arquivo primeiro.')
            return
        src = self._generated_gcode_path
        default_name = os.path.basename(src)
        dest = filedialog.asksaveasfilename(
            title='Salvar G-code',
            defaultextension='.gcode',
            filetypes=[('G-code', '*.gcode'), ('Todos', '*.*')],
            initialfile=default_name
        )
        if not dest:
            return
        try:
            shutil.copy2(src, dest)
            self._set_result(f'G-code salvo em: {dest}', SUCCESS)
        except Exception as e:
            self._set_result(f'Erro ao salvar: {e}', ERROR)

    def _go_to_pricing(self):
        path = self.slice_file_entry.get().strip()
        if not path:
            messagebox.showwarning('Aviso', 'Nenhum arquivo selecionado.')
            return

        if self._slice_gcode_result:
            self.quote_gcode_path.delete(0, 'end')
            self.quote_gcode_path.insert(0, self._generated_gcode_path or path)
            self.quote_gcode_path.xview_moveto(1)
            self._display_gcode_result_in_quote(self._slice_gcode_result, self._generated_gcode_path or path)
        elif self._slice_mesh_result:
            self.quote_gcode_path.delete(0, 'end')
            self.quote_gcode_path.insert(0, path)
            self._display_mesh_result(self._slice_mesh_result, path)
        else:
            self.quote_gcode_path.delete(0, 'end')
            self.quote_gcode_path.insert(0, path)
            self._analyze_slice_file(path)
            if self._slice_mesh_result:
                self._display_mesh_result(self._slice_mesh_result, path)

        self._switch_tab(self.tab_frames.index(self.tab_quote))
        self._calculate_quote()

    def _display_gcode_result_in_quote(self, parse_result, gcode_path):
        weight = parse_result.get('estimated_weight_grams', 0)
        length = parse_result.get('filament_length_mm', 0)
        time_sec = parse_result.get('print_time_seconds', 0)
        layers = parse_result.get('layer_count', 0)
        thumb_data = parse_result.get('thumbnail_data')
        thumb_size = parse_result.get('thumbnail_size')

        self.quote_info['weight'].config(text=f'{weight:.2f} g')
        self.quote_info['length'].config(text=f'{length:.2f} mm')
        time_min = time_sec // 60
        if time_sec >= 3600:
            time_str = f'{time_sec//3600}h {(time_sec%3600)//60:02d}min'
        else:
            time_str = f'{time_min}min'
        self.quote_info['time'].config(text=time_str)
        self.quote_info['layers'].config(text=str(layers))
        self.quote_info['colors'].config(text='-')
        self.quote_info['waste'].config(text='0 g')
        self.quote_info['printer'].config(text='-')

        self.manual_weight.delete(0, 'end')
        self.manual_weight.insert(0, f'{weight:.1f}')
        self.manual_time.delete(0, 'end')
        self.manual_time.insert(0, str(time_min))

        self._display_thumbnail(thumb_data, thumb_size)
        basename = os.path.basename(gcode_path)
        name = os.path.splitext(basename)[0]
        self.quote_name_var.set(name)

    def _clear_slice_form(self):
        self.slice_file_entry.delete(0, 'end')
        self._slice_mesh_result = None
        self._slice_gcode_result = None
        self._generated_gcode_path = None
        self._slice_3mf_thumbnail_pil = None
        self.slice_fatiar_btn.config(text='FATIAR', bg=PRIMARY, command=self._run_slice_unified)
        self.slice_save_btn.config(bg=SUCCESS)
        self.slice_price_btn.config(bg=SECONDARY)
        for key in self.slice_info:
            self.slice_info[key].config(text='-')
        self.slice_info['file_name'].config(text='-')
        self.slice_info['volume'].config(text='-')
        self.slice_info['height'].config(text='-')
        self.slice_info['weight'].config(text='-')
        for lbl in self.slice_color_labels:
            lbl.destroy()
        self.slice_color_labels = []
        self._show_slice_thumbnail(None)
        self._set_result('')

    # ─── HISTORY TAB ───────────────────────────────────────────────

    def _build_history_tab(self):
        main = ttk.Frame(self.tab_history)
        main.pack(fill='both', expand=True, padx=15, pady=15)

        ttk.Label(main, text='Histórico de Orçamentos', style='Header.TLabel').pack(anchor='w')
        ttk.Label(main, text='Gerencie orçamentos, defina status e valor de venda',
                 style='SubHeader.TLabel').pack(anchor='w', pady=(0, 10))

        # Status toolbar
        status_bar = ttk.Frame(main)
        status_bar.pack(fill='x', pady=(0, 8))
        ttk.Label(status_bar, text='Status:', font=('Segoe UI', 10)).pack(side='left', padx=(0, 8))
        status_actions = [
            ('Em Espera', '#F59E0B', '#D97706'),
            ('Em Andamento', '#3B82F6', '#2563EB'),
            ('Concluído', '#16A34A', '#15803D'),
            ('Cancelado', '#DC2626', '#B91C1C'),
        ]
        for stext, sbg, shover in status_actions:
            RoundedButton(status_bar, text=stext, bg=sbg, hover_bg=shover, fg='white',
                         command=lambda s=stext.lower(): self._update_quote_status(s),
                         tooltip=f'Marca como "{stext}"').pack(side='left', padx=2)

        # Toolbar
        toolbar = ttk.Frame(main)
        toolbar.pack(fill='x', pady=(0, 10))

        ttk.Label(toolbar, text='Buscar:', font=('Segoe UI', 9)).pack(side='left', padx=(0, 4))
        self.history_search_var = tk.StringVar()
        self.history_search_var.trace_add('write', lambda *a: self._load_history())
        self.history_search_entry = ttk.Entry(toolbar, textvariable=self.history_search_var, width=18)
        self.history_search_entry.pack(side='left', padx=(0, 10))

        self.history_sale_var = tk.StringVar()
        ttk.Label(toolbar, text='Valor Venda (R$):').pack(side='left', padx=(0, 5))
        self.history_sale_entry = ttk.Entry(toolbar, textvariable=self.history_sale_var, width=10)
        self.history_sale_entry.pack(side='left', padx=(0, 5))
        self.history_sale_entry.bind('<Return>', lambda e: self._set_sale_price())
        RoundedButton(toolbar, text='Definir', bg=SUCCESS, hover_bg='#15803D',
                     command=self._set_sale_price, tooltip='Define valor de venda do orçamento selecionado').pack(side='left', padx=(0, 5))
        RoundedButton(toolbar, text='Exportar CSV', bg='#78909C', hover_bg='#607D8B', fg='white',
                     command=self._export_history_csv, tooltip='Exporta orçamentos visíveis para CSV').pack(side='left', padx=(0, 5))
        RoundedButton(toolbar, text='Atualizar', bg='#78909C', hover_bg='#607D8B', fg='white',
                     command=self._load_history, tooltip='Atualiza a lista').pack(side='left', padx=(0, 5))
        RoundedButton(toolbar, text='Excluir', bg=ERROR, hover_bg='#B91C1C',
                     command=self._delete_quote, tooltip='Exclui o orçamento selecionado').pack(side='left', padx=5)

        columns = ('id', 'date', 'name', 'printer', 'filament', 'weight', 'time',
                   'cost', 'suggested', 'sale', 'status')
        self.history_tree = ttk.Treeview(main, columns=columns, show='headings', height=18)
        self.history_tree.heading('id', text='#')
        self.history_tree.heading('date', text='Data')
        self.history_tree.heading('name', text='Nome')
        self.history_tree.heading('printer', text='Impressora')
        self.history_tree.heading('filament', text='Filamento')
        self.history_tree.heading('weight', text='Peso (g)')
        self.history_tree.heading('time', text='Tempo')
        self.history_tree.heading('cost', text='Custo')
        self.history_tree.heading('suggested', text='Sugerido')
        self.history_tree.heading('sale', text='Venda')
        self.history_tree.heading('status', text='Status')

        self.history_tree.column('id', width=36, anchor='center')
        self.history_tree.column('date', width=120)
        self.history_tree.column('name', width=160)
        self.history_tree.column('printer', width=130)
        self.history_tree.column('filament', width=100)
        self.history_tree.column('weight', width=65, anchor='center')
        self.history_tree.column('time', width=60, anchor='center')
        self.history_tree.column('cost', width=80, anchor='e')
        self.history_tree.column('suggested', width=80, anchor='e')
        self.history_tree.column('sale', width=80, anchor='e')
        self.history_tree.column('status', width=80, anchor='center')

        scroll = ttk.Scrollbar(self.tab_history, orient='vertical', command=self.history_tree.yview)
        self.history_tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        self.history_tree.pack(fill='both', expand=True)

        self.history_tree.bind('<Double-1>', self._show_quote_detail)
        self._load_history()

    def _export_history_csv(self):
        path = filedialog.asksaveasfilename(
            title='Exportar Orçamentos',
            defaultextension='.csv',
            filetypes=[('CSV', '*.csv'), ('Todos', '*.*')]
        )
        if not path:
            return
        success, msg = export_quotes_csv(path)
        if success:
            messagebox.showinfo('Exportado', msg)
        else:
            messagebox.showwarning('Exportar', msg)

    def _load_history(self):
        search_term = self.history_search_var.get().strip().lower() if hasattr(self, 'history_search_var') else ''
        for item in self.history_tree.get_children():
            self.history_tree.delete(item)
        conn = get_connection()
        cursor = conn.cursor()
        if search_term:
            cursor.execute('''
                SELECT q.id, q.created_at, q.name, pr.name as printer_name,
                       f.name as filament_name, q.filament_used_grams,
                       q.print_time_minutes, q.total_cost, q.suggested_price,
                       q.sale_price, q.status
                FROM quotes q
                LEFT JOIN printers pr ON q.printer_id = pr.id
                LEFT JOIN filaments f ON q.filament_id = f.id
                WHERE LOWER(q.name) LIKE ? OR LOWER(pr.name) LIKE ? OR LOWER(f.name) LIKE ? OR LOWER(q.status) LIKE ?
                ORDER BY q.created_at DESC
            ''', (f'%{search_term}%', f'%{search_term}%', f'%{search_term}%', f'%{search_term}%'))
        else:
            cursor.execute('''
                SELECT q.id, q.created_at, q.name, pr.name as printer_name,
                       f.name as filament_name, q.filament_used_grams,
                       q.print_time_minutes, q.total_cost, q.suggested_price,
                       q.sale_price, q.status
                FROM quotes q
                LEFT JOIN printers pr ON q.printer_id = pr.id
                LEFT JOIN filaments f ON q.filament_id = f.id
                ORDER BY q.created_at DESC
            ''')
        status_tags = {
            'concluído': '#DCFCE7', 'em espera': '#FEF3C7',
            'em andamento': '#DBEAFE', 'cancelado': '#FEE2E2',
        }
        for row in cursor.fetchall():
            hours = (row['print_time_minutes'] or 0) // 60
            mins = (row['print_time_minutes'] or 0) % 60
            time_str = f'{hours}h{mins:02d}min' if hours > 0 else f'{mins}min'
            date_str = row['created_at'][:16].replace('T', ' ') if row['created_at'] else ''
            status = (row['status'] or 'orçamento').capitalize()
            sale_str = f'R$ {row["sale_price"]:.2f}' if row['sale_price'] else '-'
            tag = status.lower()
            item_id = self.history_tree.insert('', 'end', values=(
                row['id'], date_str, row['name'],
                row['printer_name'] or '-', row['filament_name'] or '-',
                f'{row["filament_used_grams"]:.1f}' if row['filament_used_grams'] else '-', time_str,
                f'R$ {row["total_cost"]:.2f}' if row['total_cost'] else '-',
                f'R$ {row["suggested_price"]:.2f}' if row['suggested_price'] else '-',
                sale_str, status,
            ), tags=(tag,))
            bg = status_tags.get(tag, 'white')
            self.history_tree.tag_configure(tag, background=bg)
        conn.close()

    def _update_quote_status(self, new_status):
        sel = self.history_tree.selection()
        if not sel:
            messagebox.showwarning('Aviso', 'Selecione um orçamento.')
            return
        vals = self.history_tree.item(sel[0], 'values')
        if not vals:
            return
        qid = vals[0]
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute('UPDATE quotes SET status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?',
                      (new_status, qid))
        conn.commit()
        conn.close()
        self._load_history()
        self._refresh_dashboard()

    def _set_sale_price(self):
        sel = self.history_tree.selection()
        if not sel:
            messagebox.showwarning('Aviso', 'Selecione um orçamento.')
            return
        vals = self.history_tree.item(sel[0], 'values')
        if not vals:
            return
        try:
            price = float(self.history_sale_var.get().strip().replace(',', '.'))
            if price < 0:
                raise ValueError
        except ValueError:
            messagebox.showwarning('Aviso', 'Valor de venda inválido.')
            return
        qid = vals[0]
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute('UPDATE quotes SET sale_price=?, updated_at=CURRENT_TIMESTAMP WHERE id=?',
                      (price, qid))
        conn.commit()
        conn.close()
        self.history_sale_var.set('')
        self._load_history()
        self._refresh_dashboard()

    def _show_quote_detail(self, event):
        sel = self.history_tree.selection()
        if not sel:
            return
        vals = self.history_tree.item(sel[0], 'values')
        if not vals:
            return
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM quotes WHERE id = ?', (vals[0],))
        row = cursor.fetchone()
        conn.close()
        if not row:
            return

        detail = tk.Toplevel(self.root)
        detail.title(f'Orçamento: {row["name"]}')
        detail.geometry('500x550')
        detail.transient(self.root)
        detail.grab_set()
        detail.configure(bg=CARD_BG)

        main_frame = tk.Frame(detail, bg=CARD_BG, padx=25, pady=20)
        main_frame.pack(fill='both', expand=True)

        tk.Label(main_frame, text=row['name'], font=('Segoe UI', 16, 'bold'),
                fg=PRIMARY_DARK, bg=CARD_BG).pack(anchor='w')

        tk.Frame(main_frame, bg=PRIMARY_LIGHT, height=2).pack(fill='x', pady=8)

        info = [
            ('Data', row['created_at'][:19] if row['created_at'] else '-'),
            ('Arquivo G-Code', row['gcode_file'] or '-'),
            ('Peso', f'{row["filament_used_grams"]:.2f} g' if row['filament_used_grams'] else '-'),
            ('Tempo', f'{row["print_time_minutes"]} min' if row['print_time_minutes'] else '-'),
            ('Margem de lucro', f'{row["profit_margin"]:.0f}%' if row['profit_margin'] else '-'),
            ('Status', (row['status'] or 'orçamento').capitalize()),
            ('Valor Venda', f'R$ {row["sale_price"]:.2f}' if row['sale_price'] else 'N/D'),
        ]
        for label, value in info:
            f = tk.Frame(main_frame, bg=CARD_BG)
            f.pack(fill='x', pady=2)
            tk.Label(f, text=label, font=('Segoe UI', 10), fg=TEXT_SECONDARY,
                    bg=CARD_BG, width=18, anchor='w').pack(side='left')
            tk.Label(f, text=value, font=('Segoe UI', 10, 'bold'),
                    fg=TEXT_PRIMARY, bg=CARD_BG).pack(side='left')

        tk.Frame(main_frame, bg='#E0E0E0', height=1).pack(fill='x', pady=10)

        costs = [
            ('Filamento', f'R$ {row["filament_cost"]:.2f}'),
            ('Energia', f'R$ {row["energy_cost"]:.2f}'),
            ('Depreciação', f'R$ {row["depreciation_cost"]:.2f}'),
            ('Manutenção', f'R$ {row["maintenance_cost"]:.2f}'),
        ]
        for label, value in costs:
            f = tk.Frame(main_frame, bg=CARD_BG)
            f.pack(fill='x', pady=2)
            tk.Label(f, text=label, font=('Segoe UI', 10), fg=TEXT_SECONDARY,
                    bg=CARD_BG, width=18, anchor='w').pack(side='left')
            tk.Label(f, text=value, font=('Segoe UI', 10), fg=ERROR if '0' in value else TEXT_PRIMARY,
                    bg=CARD_BG).pack(side='left')

        tk.Frame(main_frame, bg=PRIMARY, height=1).pack(fill='x', pady=8)
        total_f = tk.Frame(main_frame, bg=CARD_BG)
        total_f.pack(fill='x', pady=3)
        tk.Label(total_f, text='CUSTO TOTAL', font=('Segoe UI', 12, 'bold'),
                fg=PRIMARY_DARK, bg=CARD_BG).pack(side='left')
        tk.Label(total_f, text=f'R$ {row["total_cost"]:.2f}',
                font=('Segoe UI', 12, 'bold'), fg=PRIMARY, bg=CARD_BG).pack(side='right')

        sug_f = tk.Frame(main_frame, bg=CARD_BG)
        sug_f.pack(fill='x', pady=3)
        tk.Label(sug_f, text='PREÇO SUGERIDO', font=('Segoe UI', 14, 'bold'),
                fg=SUCCESS, bg=CARD_BG).pack(side='left')
        tk.Label(sug_f, text=f'R$ {row["suggested_price"]:.2f}',
                font=('Segoe UI', 14, 'bold'), fg=SUCCESS, bg=CARD_BG).pack(side='right')

        # Thumbnail
        if row['thumbnail_data']:
            try:
                from PIL import Image, ImageTk
                import io as io_module
                img = Image.open(io_module.BytesIO(row['thumbnail_data']))
                max_s = 200
                w, h = img.size
                ratio = min(max_s/w, max_s/h)
                img = img.resize((int(w*ratio), int(h*ratio)), Image.LANCZOS)
                tk_img = ImageTk.PhotoImage(img)
                lbl = tk.Label(main_frame, image=tk_img, bg=CARD_BG)
                lbl.image = tk_img
                lbl.pack(pady=10)
            except:
                pass

        tk.Button(main_frame, text='Fechar', command=detail.destroy,
                 bg=PRIMARY, fg='white', font=('Segoe UI', 10)).pack(pady=10)

    def _delete_quote(self):
        sel = self.history_tree.selection()
        if not sel:
            messagebox.showwarning('Aviso', 'Selecione um orçamento.')
            return
        vals = self.history_tree.item(sel[0], 'values')
        if not vals:
            return
        if messagebox.askyesno('Confirmar', 'Excluir este orçamento?'):
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute('DELETE FROM quotes WHERE id = ?', (vals[0],))
            conn.commit()
            conn.close()
            self._load_history()
            self._refresh_dashboard()

    # ─── SETTINGS TAB ──────────────────────────────────────────────

    def _build_settings_tab(self):
        main = ttk.Frame(self.tab_settings)
        main.pack(fill='both', expand=True, padx=15, pady=15)

        ttk.Label(main, text='Configurações', style='Header.TLabel').pack(anchor='w')
        ttk.Label(main, text='Ajuste os parâmetros gerais do sistema',
                 style='SubHeader.TLabel').pack(anchor='w', pady=(0, 20))

        energy_frame = ttk.LabelFrame(main, text='Energia Elétrica', padding=15)
        energy_frame.pack(fill='x', pady=10)

        ttk.Label(energy_frame, text='Preço do kWh (R$):').grid(row=0, column=0, sticky='w', pady=5)
        self.settings_energy = ttk.Entry(energy_frame, width=20)
        self.settings_energy.grid(row=0, column=1, padx=10, pady=5)
        self.settings_energy.insert(0, str(get_setting('energy_price') or '0.85'))
        self.settings_energy.bind('<Return>', lambda e: self._save_settings())

        profit_frame = ttk.LabelFrame(main, text='Margem de Lucro Padrão', padding=15)
        profit_frame.pack(fill='x', pady=10)

        ttk.Label(profit_frame, text='Margem padrão (%):').grid(row=0, column=0, sticky='w', pady=5)
        self.settings_margin = ttk.Entry(profit_frame, width=20)
        self.settings_margin.grid(row=0, column=1, padx=10, pady=5)
        self.settings_margin.insert(0, str(get_setting('profit_margin_default') or '30'))
        self.settings_margin.bind('<Return>', lambda e: self._save_settings())

        btn_frame = tk.Frame(profit_frame, bg=BG_LIGHT)
        btn_frame.grid(row=1, column=0, columnspan=2, pady=15)
        RoundedButton(btn_frame, text='Salvar Configurações',
                     command=self._save_settings).pack()

        # ─── Database Management ─────────────────────────────────────
        db_frame = ttk.LabelFrame(main, text='Banco de Dados', padding=15)
        db_frame.pack(fill='x', pady=10)

        db_row = ttk.Frame(db_frame)
        db_row.pack(fill='x')
        ttk.Label(db_row, text=f'Localização:', font=('Segoe UI', 9)).pack(side='left')
        db_path_label = ttk.Label(db_row, text=DB_PATH, font=('Segoe UI', 8), foreground=TEXT_SECONDARY)
        db_path_label.pack(side='left', padx=10)

        db_btn_row = ttk.Frame(db_frame)
        db_btn_row.pack(fill='x', pady=(8, 0))
        RoundedButton(db_btn_row, text='Fazer Backup', bg='#78909C', hover_bg='#607D8B', fg='white',
                     command=self._backup_database, tooltip='Copia o banco de dados atual').pack(side='left', padx=(0, 5))
        RoundedButton(db_btn_row, text='Info do BD', bg='#78909C', hover_bg='#607D8B', fg='white',
                     command=self._show_db_info, tooltip='Mostra estatísticas do banco').pack(side='left', padx=5)

        about_frame = ttk.LabelFrame(main, text='Sobre', padding=15)
        about_frame.pack(fill='x', pady=10)
        ttk.Label(about_frame, text=f'{APP_TITLE}').pack(anchor='w')
        ttk.Label(about_frame, text='Importa dados do OrcaSlicer, Bambu Studio e Anycubic Slicer Next.').pack(anchor='w', pady=(5, 0))
        ttk.Label(about_frame, text='Compatível com arquivos G-Code do OrcaSlicer, Bambu Studio, PrusaSlicer, Cura e outros.').pack(anchor='w')

    def _backup_database(self):
        if not os.path.isfile(DB_PATH):
            messagebox.showerror('Erro', 'Banco de dados não encontrado.')
            return
        from datetime import datetime
        default_name = f'custo3d_backup_{datetime.now().strftime("%Y%m%d_%H%M%S")}.db'
        path = filedialog.asksaveasfilename(
            title='Salvar Backup do Banco de Dados',
            initialfile=default_name,
            defaultextension='.db',
            filetypes=[('SQLite DB', '*.db'), ('Todos', '*.*')]
        )
        if not path:
            return
        try:
            shutil.copy2(DB_PATH, path)
            messagebox.showinfo('Backup', f'Backup salvo em:\n{path}')
        except Exception as e:
            messagebox.showerror('Erro', f'Falha ao fazer backup:\n{str(e)}')

    def _show_db_info(self):
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM filaments')
        fc = cursor.fetchone()[0]
        cursor.execute('SELECT COUNT(*) FROM printers')
        pc = cursor.fetchone()[0]
        cursor.execute('SELECT COUNT(*) FROM quotes')
        qc = cursor.fetchone()[0]
        cursor.execute('SELECT COUNT(*) FROM quotes WHERE status="concluído"')
        done = cursor.fetchone()[0]
        cursor.execute('SELECT COALESCE(SUM(sale_price), 0) FROM quotes WHERE status="concluído"')
        revenue = cursor.fetchone()[0]
        conn.close()
        size = os.path.getsize(DB_PATH) if os.path.isfile(DB_PATH) else 0
        size_kb = size / 1024
        msg = (
            f'Tamanho: {size_kb:.1f} KB\n'
            f'Filamentos: {fc}\n'
            f'Impressoras: {pc}\n'
            f'Orçamentos: {qc} ({done} concluídos)\n'
            f'Faturamento: R$ {revenue:.2f}'
        )
        messagebox.showinfo('Informações do Banco de Dados', msg)

    def _save_settings(self):
        try:
            energy = float(self.settings_energy.get().strip())
            margin = float(self.settings_margin.get().strip())
            if energy <= 0:
                raise ValueError
            if margin < 0:
                raise ValueError
            update_setting('energy_price', str(energy))
            update_setting('profit_margin_default', str(margin))
            messagebox.showinfo('Sucesso', 'Configurações salvas!')
        except ValueError:
            messagebox.showwarning('Aviso', 'Valores inválidos.')

    # ─── ORCA IMPORT (GLOBAL) ──────────────────────────────────────

    def _import_from_all_slicers(self):
        self._import_filaments_from_slicers()
        self._import_printers_from_slicers()

    def _import_filaments_from_slicers(self):
        available = find_importable_slicers()
        if not available:
            messagebox.showinfo('Importar', 'Nenhum slicer compatível encontrado.\n\nVerifique se o OrcaSlicer, Bambu Studio ou Anycubic Slicer Next estão instalados.')
            return

        user_only = self.fil_import_user_var.get() if hasattr(self, 'fil_import_user_var') else False
        loading = LoadingDialog(self.root, 'Escaneando filamentos...')
        try:
            filaments = slicer_scan_filaments(user_only=user_only)
        finally:
            loading.close()

        if not filaments:
            messagebox.showinfo('Importar', 'Nenhum filamento encontrado nos slicers.')
            return

        existing = {f.name for f in Filament.get_all()}
        imported = 0
        for fd in filaments:
            if fd['name'] in existing:
                continue
            f = Filament()
            f.name = fd['name']
            f.brand = fd.get('brand', '')
            f.material = fd.get('material', 'PLA')
            f.color = fd.get('color', '')
            try:
                f.diameter = float(fd.get('diameter', 1.75))
            except:
                f.diameter = 1.75
            try:
                f.density = float(fd.get('density', 1.24))
            except:
                f.density = 1.24
            f.price_per_kg = fd.get('price_per_kg', 0)
            f.save()
            imported += 1
        self._load_filaments()
        self._refresh_dashboard()
        self._update_status_bar(f'{imported} filamentos importados' if imported else 'Nenhum filamento novo')
        if imported > 0:
            slicers_str = ', '.join(available)
            messagebox.showinfo('Sucesso', f'{imported} filamentos importados de: {slicers_str}\n\nLembre-se de preencher o preço por kg!')
        else:
            messagebox.showinfo('Importar', 'Nenhum filamento novo encontrado (já existem no sistema).')

if __name__ == '__main__':
    root = tk.Tk()
    app = Cost3DApp(root)
    root.mainloop()