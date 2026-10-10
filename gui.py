import os
import re
import sys
import time
import glob
import threading
import subprocess
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, messagebox, filedialog

from fitgirl_parser import FitGirlParser
from idm_exporter import IDMExporter
from extractor import ArchiveExtractor, find_recent_repack_folders
from idm_proxy import LocalRelay

# If running as a frozen PyInstaller executable, set working dir to the exe location
if getattr(sys, 'frozen', False):
    os.chdir(os.path.dirname(sys.executable))

# Ensure UTF-8 output on Windows
if sys.platform == "win32":
    try:
        if sys.stdout is not None:
            sys.stdout.reconfigure(encoding='utf-8')
        if sys.stderr is not None:
            sys.stderr.reconfigure(encoding='utf-8')
    except (AttributeError, Exception):
        pass

DEFAULT_DL = r"D:\Downloads"
POLL_SECONDS = 2.0
ASSUMED_PART_BYTES = 500 * 1024 * 1024  # ETA fallback before any part has finished
IDM_TEMP_SUFFIXES = ('.tmp', '.log', '.temp')
ARCHIVE_SUFFIXES = ('.rar', '.r00', '.r01')


def resource_path(rel):
    """Resolve a bundled asset both from source and from a PyInstaller exe."""
    base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


def format_bytes(num_bytes):
    if num_bytes < 1024:
        return f"{num_bytes} B"
    elif num_bytes < 1024 ** 2:
        return f"{num_bytes / 1024:.1f} KB"
    elif num_bytes < 1024 ** 3:
        return f"{num_bytes / (1024 ** 2):.1f} MB"
    else:
        return f"{num_bytes / (1024 ** 3):.2f} GB"


def format_time(seconds):
    if seconds <= 0 or seconds > 3600 * 24 * 7:
        return "--:--"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}h {m:02d}m {s:02d}s"
    return f"{m:02d}m {s:02d}s"


def safe_name(name):
    """Strip characters Windows refuses in file and folder names."""
    return re.sub(r'[<>:"/\\|?*]', '', name).strip().rstrip('.') or 'fitgirl_downloads'


class Palette:
    """One graphite base, one emerald accent. Amber and red are reserved for real warnings and errors."""
    BG = "#0b0d10"
    RAISED = "#14181e"      # hover wash and zebra rows
    LINE = "#222730"        # hairlines and idle underlines
    LINE_STRONG = "#343a45"
    TEXT = "#e8eaed"
    MUTED = "#8a919c"
    FAINT = "#5c636e"       # disabled only
    ACCENT = "#3ddc97"
    ACCENT_HOVER = "#62e8ae"
    ACCENT_PRESS = "#2fb97d"
    ACCENT_DIM = "#17493a"
    ON_ACCENT = "#06231a"
    AMBER = "#e0a43a"
    RED = "#ef6b63"
    RED_DARK = "#8f2f2a"
    RED_PRESS = "#702420"


P = Palette
UI_FONT = "Segoe UI"
MONO_FONT = "Consolas"
SCALE = 1.0


def px(n):
    """Pixel sizes follow the display scale; point sizes already do."""
    return max(1, int(round(n * SCALE)))


def _pick_fonts(root):
    global UI_FONT, MONO_FONT
    have = set(tkfont.families(root))
    UI_FONT = "Segoe UI Variable Text" if "Segoe UI Variable Text" in have else "Segoe UI"
    MONO_FONT = "Cascadia Mono" if "Cascadia Mono" in have else "Consolas"


# ------------------------------------------------------------------ widgets
class FlatButton(tk.Label):
    """Flat rectangular button. Primary is solid; secondary is outlined; ghost is text only."""
    VARIANTS = {
        'primary': dict(bg=P.ACCENT, hover=P.ACCENT_HOVER, press=P.ACCENT_PRESS, fg=P.ON_ACCENT,
                        fg_hover=P.ON_ACCENT, border=P.ACCENT),
        'secondary': dict(bg=P.BG, hover=P.RAISED, press=P.LINE, fg=P.TEXT,
                          fg_hover=P.TEXT, border=P.LINE_STRONG),
        'ghost': dict(bg=P.BG, hover=P.RAISED, press=P.LINE, fg=P.MUTED,
                      fg_hover=P.TEXT, border=P.BG),
        'danger': dict(bg=P.BG, hover=P.RED_DARK, press=P.RED_PRESS, fg=P.RED,
                       fg_hover="#ffffff", border=P.RED_DARK),
    }

    def __init__(self, parent, text, command=None, variant='secondary', padx=18, pady=8):
        self._cmd, self._variant = command, variant
        self._state, self._hover, self._down = 'normal', False, False
        super().__init__(parent, text=text, font=(UI_FONT, 9, 'bold'), padx=px(padx), pady=px(pady),
                         bd=0, highlightthickness=px(1), takefocus=1, cursor='hand2')
        self.bind('<Enter>', lambda e: self._set(hover=True))
        self.bind('<Leave>', lambda e: self._set(hover=False, down=False))
        self.bind('<ButtonPress-1>', lambda e: (self.focus_set(), self._set(down=True)))
        self.bind('<ButtonRelease-1>', self._release)
        self.bind('<space>', lambda e: self._invoke())
        self.bind('<Return>', lambda e: self._invoke())
        self._render()

    def _set(self, **kw):
        for k, v in kw.items():
            setattr(self, '_' + k, v)
        self._render()

    def _release(self, e):
        was_down = self._down
        self._set(down=False)
        if was_down and 0 <= e.x < self.winfo_width() and 0 <= e.y < self.winfo_height():
            self._invoke()

    def _invoke(self):
        if self._state == 'normal' and self._cmd:
            self._cmd()

    def _render(self):
        v = self.VARIANTS[self._variant]
        if self._state == 'disabled':
            bg, fg, border, cursor = P.BG, P.FAINT, P.LINE, 'arrow'
        else:
            bg = v['press'] if self._down else v['hover'] if self._hover else v['bg']
            fg = v['fg_hover'] if self._hover else v['fg']
            border, cursor = v['border'], 'hand2'
        tk.Label.configure(self, bg=bg, fg=fg, highlightbackground=border,
                           highlightcolor=P.TEXT, cursor=cursor)

    def configure(self, cnf=None, **kw):
        if isinstance(cnf, dict):
            kw = {**cnf, **kw}
        for key in ('variant', 'state', 'command'):
            if key in kw:
                attr = {'variant': '_variant', 'state': '_state', 'command': '_cmd'}[key]
                setattr(self, attr, kw.pop(key))
        if kw:
            tk.Label.configure(self, **kw)
        self._render()

    config = configure


class Underlined(tk.Frame):
    """Holds one control and draws a 2px underline beneath it. The underline carries focus state."""

    def __init__(self, parent):
        super().__init__(parent, bg=P.BG)
        self.body = tk.Frame(self, bg=P.BG)
        self.body.pack(fill='x')
        self.line = tk.Frame(self, bg=P.LINE, height=px(2))
        self.line.pack(fill='x')
        self._focused = False

    def track(self, widget):
        def focus(on):
            self._focused = on
            self.line.configure(bg=P.ACCENT if on else P.LINE)
        widget.bind('<FocusIn>', lambda e: focus(True), add='+')
        widget.bind('<FocusOut>', lambda e: focus(False), add='+')
        widget.bind('<Enter>', lambda e: None if self._focused else self.line.configure(bg=P.LINE_STRONG), add='+')
        widget.bind('<Leave>', lambda e: None if self._focused else self.line.configure(bg=P.LINE), add='+')


class Field(Underlined):
    def __init__(self, parent, textvariable=None, width=None, font=None):
        super().__init__(parent)
        self.entry = tk.Entry(self.body, textvariable=textvariable, bd=0, relief='flat', bg=P.BG,
                              fg=P.TEXT, insertbackground=P.TEXT, disabledforeground=P.MUTED,
                              selectbackground=P.ACCENT_DIM, selectforeground=P.TEXT,
                              highlightthickness=0, font=font or (UI_FONT, 10), width=width or 20)
        self.entry.pack(fill='x', ipady=px(5))
        self.track(self.entry)


class ProgressLine(tk.Canvas):
    """4px line with an eased fill. Two rectangles, moved with coords(), never redrawn."""

    def __init__(self, parent, height=4):
        self._h = px(height)
        super().__init__(parent, height=self._h, bg=P.BG, highlightthickness=0, bd=0)
        self._track = self.create_rectangle(0, 0, 0, self._h, fill=P.LINE, width=0)
        self._fill = self.create_rectangle(0, 0, 0, self._h, fill=P.ACCENT, width=0)
        self._target = self._shown = 0.0
        self._job = None
        self.bind('<Configure>', lambda e: self._layout())

    def _layout(self):
        w = self.winfo_width()
        self.coords(self._track, 0, 0, w, self._h)
        self.coords(self._fill, 0, 0, w * self._shown / 100, self._h)

    def set(self, value):
        self._target = max(0.0, min(100.0, float(value)))
        if self._target < self._shown:      # jumps backwards (a new phase) snap instead of sliding
            self._shown = self._target
            self._layout()
            return
        if self._job is None:
            self._step()

    def _step(self):
        self._job = None
        diff = self._target - self._shown
        self._shown = self._target if abs(diff) < 0.2 else self._shown + diff * 0.25
        self._layout()
        if self._shown != self._target:
            self._job = self.after(16, self._step)


class NavItem(tk.Frame):
    """Rail destination: a 3px accent bar marks the active one."""

    def __init__(self, parent, text, command):
        super().__init__(parent, bg=P.BG, cursor='hand2', takefocus=1, highlightthickness=px(1),
                         highlightbackground=P.BG, highlightcolor=P.TEXT)
        self._cmd = command
        self.bar = tk.Frame(self, width=px(3), bg=P.BG)
        self.bar.pack(side='left', fill='y')
        self.label = tk.Label(self, text=text, font=(UI_FONT, 11), fg=P.MUTED, bg=P.BG, anchor='w',
                              padx=px(17), pady=px(10), cursor='hand2')
        self.label.pack(side='left', fill='x', expand=True)
        self._active = False
        for w in (self, self.label):
            w.bind('<Button-1>', lambda e: command())
            w.bind('<Enter>', lambda e: self._paint(hover=True))
            w.bind('<Leave>', lambda e: self._paint(hover=False))
        self.bind('<space>', lambda e: command())
        self.bind('<Return>', lambda e: command())

    def _paint(self, hover=False):
        bg = P.RAISED if hover and not self._active else P.BG
        self.configure(bg=bg)
        self.label.configure(bg=bg, fg=P.TEXT if (self._active or hover) else P.MUTED)
        self.bar.configure(bg=P.ACCENT if self._active else bg)

    def set_active(self, active):
        self._active = active
        self._paint()


# ---------------------------------------------------------------------- app
class FitGirlIDMApp:
    def __init__(self, root):
        global SCALE
        self.root = root
        SCALE = max(1.0, root.winfo_fpixels('1i') / 96.0)
        _pick_fonts(root)

        self.root.title("FitGirl to IDM")
        sh = self.root.winfo_screenheight()
        self.root.geometry(f"{px(1040)}x{min(px(760), sh - 90)}")
        self.root.minsize(px(940), px(660))
        self.root.configure(bg=P.BG)
        self._load_logo()

        self.style = ttk.Style()
        if 'clam' in self.style.theme_names():
            self.style.theme_use('clam')
        self._configure_styles()

        self.parser = FitGirlParser()
        self.exporter = IDMExporter()
        self.extractor = ArchiveExtractor()
        self.relay = LocalRelay(refresh=self._refresh_direct, on_log=self.log)
        self.parsed_data = None

        # Monitoring: one stop event per session, so a stale worker can never act on a newer session
        self.is_monitoring = False
        self._stop = None
        self._extracting = False
        self._folder_scan_id = 0
        self._filter_job = None
        self._ef2_cache = {}   # path -> (mtime, filenames)

        self._build_shell()
        self.root.bind_all('<Control-Key-1>', lambda e: self.show_page('export'))
        self.root.bind_all('<Control-Key-2>', lambda e: self.show_page('monitor'))
        self.root.protocol('WM_DELETE_WINDOW', self._on_close)

    def _load_logo(self):
        """Window and taskbar icon plus a small copy for the rail. Fails soft if assets are missing."""
        self.logo_small = None
        try:
            icon = tk.PhotoImage(file=resource_path(os.path.join("assets", "logo.png")))
            self.root.iconphoto(True, icon)
            factor = max(1, icon.width() // px(30))
            self.logo_small = icon.subsample(factor, factor)
            self._logo_ref = icon
        except Exception:
            pass
        try:
            self.root.iconbitmap(resource_path(os.path.join("assets", "icon.ico")))
        except Exception:
            pass

    # ------------------------------------------------------------------ styling
    def _configure_styles(self):
        """ttk is only used for Treeview, Combobox and Scrollbar; everything else is plain Tk."""
        s = self.style
        self.root.option_add('*Font', (UI_FONT, 10))
        self.root.option_add('*TCombobox*Listbox.background', P.RAISED)
        self.root.option_add('*TCombobox*Listbox.foreground', P.TEXT)
        self.root.option_add('*TCombobox*Listbox.selectBackground', P.ACCENT_DIM)
        self.root.option_add('*TCombobox*Listbox.selectForeground', P.TEXT)
        self.root.option_add('*TCombobox*Listbox.font', (UI_FONT, 10))

        s.configure('.', background=P.BG, foreground=P.TEXT, fieldbackground=P.BG,
                    bordercolor=P.BG, lightcolor=P.BG, darkcolor=P.BG,
                    troughcolor=P.BG, focuscolor=P.BG, font=(UI_FONT, 10))

        s.configure('TCombobox', fieldbackground=P.BG, background=P.BG, foreground=P.TEXT,
                    arrowcolor=P.MUTED, padding=[0, 5], bordercolor=P.BG, lightcolor=P.BG,
                    darkcolor=P.BG, borderwidth=0)
        s.map('TCombobox', fieldbackground=[('readonly', P.BG)], foreground=[('readonly', P.TEXT)],
              background=[('active', P.BG)], arrowcolor=[('active', P.TEXT)])

        s.layout('Treeview', [('Treeview.treearea', {'sticky': 'nswe'})])
        s.configure('Treeview', background=P.BG, fieldbackground=P.BG, foreground=P.TEXT,
                    rowheight=px(30), borderwidth=0, font=(UI_FONT, 9))
        s.configure('Treeview.Heading', background=P.BG, foreground=P.MUTED,
                    font=(UI_FONT, 9), padding=[px(8), px(8)], relief='flat', borderwidth=0)
        s.map('Treeview', background=[('selected', P.ACCENT_DIM)], foreground=[('selected', P.TEXT)])
        s.map('Treeview.Heading', background=[('active', P.BG)], foreground=[('active', P.TEXT)])

        s.layout('Vertical.TScrollbar', [('Vertical.Scrollbar.trough', {
            'children': [('Vertical.Scrollbar.thumb', {'expand': '1', 'sticky': 'nswe'})],
            'sticky': 'ns'})])
        s.configure('Vertical.TScrollbar', background=P.LINE, troughcolor=P.BG, bordercolor=P.BG,
                    lightcolor=P.LINE, darkcolor=P.LINE, width=px(8), gripcount=0)
        s.map('Vertical.TScrollbar', background=[('active', P.MUTED)])

    # ------------------------------------------------------------------ layout helpers
    def _label(self, parent, text, muted=False, **kw):
        return tk.Label(parent, text=text, bg=P.BG, fg=P.MUTED if muted else P.TEXT,
                        font=kw.pop('font', (UI_FONT, 10)), **kw)

    def _rule(self, parent, pady=(0, 0), side='top'):
        tk.Frame(parent, bg=P.LINE, height=1).pack(fill='x', pady=pady, side=side)

    def _page_header(self, parent, title, sub):
        tk.Label(parent, text=title, font=(UI_FONT, 20, 'bold'), bg=P.BG, fg=P.TEXT).pack(anchor='w')
        tk.Label(parent, text=sub, font=(UI_FONT, 10), bg=P.BG, fg=P.MUTED).pack(anchor='w', pady=(2, px(20)))

    def _form(self, parent):
        f = tk.Frame(parent, bg=P.BG)
        f.columnconfigure(0, minsize=px(132))
        f.columnconfigure(1, weight=1)
        f._rows = 0
        f.pack(fill='x')
        return f

    def _form_row(self, form, label):
        r = form._rows
        form._rows += 1
        tk.Label(form, text=label, font=(UI_FONT, 10), bg=P.BG, fg=P.MUTED,
                 anchor='w').grid(row=r, column=0, sticky='w', pady=px(7))
        cell = tk.Frame(form, bg=P.BG)
        cell.grid(row=r, column=1, sticky='ew', pady=px(7))
        return cell

    # ----------------------------------------------------------------- shell
    def _build_shell(self):
        # Status line first so it stays pinned to the bottom edge
        status = tk.Frame(self.root, bg=P.BG)
        status.pack(fill='x', side='bottom')
        self._rule(status)
        self.status_var = tk.StringVar(value="Ready. Paste a FitGirl link or monitor an existing download.")
        tk.Label(status, textvariable=self.status_var, font=(UI_FONT, 9), bg=P.BG, fg=P.MUTED,
                 anchor='w', padx=px(24), pady=px(8)).pack(fill='x')

        rail = tk.Frame(self.root, bg=P.BG, width=px(216))
        rail.pack(side='left', fill='y')
        rail.pack_propagate(False)
        tk.Frame(self.root, bg=P.LINE, width=1).pack(side='left', fill='y')

        brand = tk.Frame(rail, bg=P.BG)
        brand.pack(fill='x', padx=px(20), pady=(px(26), px(28)))
        if self.logo_small is not None:
            tk.Label(brand, image=self.logo_small, bg=P.BG, bd=0).pack(side='left', padx=(0, px(10)))
        tk.Label(brand, text="FitGirl\nto IDM", font=(UI_FONT, 12, 'bold'), bg=P.BG, fg=P.TEXT,
                 justify='left').pack(side='left')

        self._nav = {}
        for key, text in (('export', "Links"), ('monitor', "Monitor")):
            item = NavItem(rail, text, lambda k=key: self.show_page(k))
            item.pack(fill='x')
            self._nav[key] = item

        idm_ok = bool(self.exporter.idm_path)
        idm = tk.Frame(rail, bg=P.BG)
        idm.pack(side='bottom', fill='x', padx=px(20), pady=px(22))
        row = tk.Frame(idm, bg=P.BG)
        row.pack(anchor='w')
        tk.Label(row, text="\u25CF", font=(UI_FONT, 8), bg=P.BG,
                 fg=P.ACCENT if idm_ok else P.AMBER).pack(side='left', padx=(0, px(6)))
        tk.Label(row, text="IDM detected" if idm_ok else "IDM not found", font=(UI_FONT, 9, 'bold'),
                 bg=P.BG, fg=P.TEXT).pack(side='left')
        tk.Label(idm, text=os.path.basename(self.exporter.idm_path) if idm_ok else "Exports still work as files",
                 font=(UI_FONT, 9), bg=P.BG, fg=P.MUTED).pack(anchor='w', pady=(2, 0))

        main = tk.Frame(self.root, bg=P.BG)
        main.pack(side='left', fill='both', expand=True)
        self._pages = {}
        for key in ('export', 'monitor'):
            self._pages[key] = tk.Frame(main, bg=P.BG)
        self._build_export_page(self._pages['export'])
        self._build_monitor_page(self._pages['monitor'])
        self.show_page('export')

    def show_page(self, key):
        for k, page in self._pages.items():
            if k == key:
                page.pack(fill='both', expand=True, padx=px(40), pady=(px(28), px(14)))
            else:
                page.pack_forget()
            self._nav[k].set_active(k == key)
        if key == 'export':
            self.url_entry.focus_set()

    # ================= PAGE 1: LINKS =================
    def _build_export_page(self, page):
        self._page_header(page, "Links", "Paste a repack page, pick a mirror, send the parts to IDM.")

        form = self._form(page)
        cell = self._form_row(form, "Repack URL")
        self.url_var = tk.StringVar()
        self.url_field = Field(cell, textvariable=self.url_var)
        self.url_field.pack(side='left', fill='x', expand=True, padx=(0, px(10)))
        self.url_entry = self.url_field.entry
        self.url_entry.bind('<Return>', lambda e: self.on_extract_clicked())
        FlatButton(cell, "Paste", command=self.on_paste_url).pack(side='left', padx=(0, px(8)))
        self.btn_extract = FlatButton(cell, "Extract links", variant='primary', command=self.on_extract_clicked)
        self.btn_extract.pack(side='left')

        cell = self._form_row(form, "Save to")
        self.dest_var = tk.StringVar(value=DEFAULT_DL)
        Field(cell, textvariable=self.dest_var).pack(side='left', fill='x', expand=True, padx=(0, px(10)))
        FlatButton(cell, "Browse", command=self.on_browse_dest).pack(side='left')

        # Bottom-anchored items are packed before the expanding parts list
        actions = tk.Frame(page, bg=P.BG)
        actions.pack(fill='x', side='bottom', pady=(px(14), 0))
        self.btn_send = FlatButton(actions, "Send to IDM", variant='primary', command=self.on_send_to_idm)
        self.btn_send.pack(side='left')
        self.btn_export_ef2 = FlatButton(actions, "Export .ef2", command=self.on_export_ef2)
        self.btn_export_ef2.pack(side='left', padx=(px(8), 0))
        self.btn_export_txt = FlatButton(actions, "Export .txt", command=lambda: self._export_file('txt'))
        self.btn_export_txt.pack(side='left', padx=(px(8), 0))
        self.btn_export_bat = FlatButton(actions, "Export .bat", command=lambda: self._export_file('bat'))
        self.btn_export_bat.pack(side='left', padx=(px(8), 0))
        self._rule(page, side='bottom')

        self._rule(page, pady=(px(8), px(16)))
        head = tk.Frame(page, bg=P.BG)
        head.pack(fill='x', pady=(0, px(8)))
        self.lbl_game_title = tk.Label(head, text="No game loaded", font=(UI_FONT, 13, 'bold'),
                                       bg=P.BG, fg=P.MUTED, anchor='w')
        self.lbl_game_title.pack(side='left')
        self.lbl_count = self._label(head, "0 parts", muted=True, font=(UI_FONT, 9))
        self.lbl_count.pack(side='right')

        selector = tk.Frame(page, bg=P.BG)
        selector.pack(fill='x', pady=(0, px(10)))
        self._label(selector, "Hoster", muted=True).pack(side='left', padx=(0, px(10)))
        combo_wrap = Underlined(selector)
        combo_wrap.pack(side='left')
        self.combo_hosters = ttk.Combobox(combo_wrap.body, state='readonly', width=22)
        self.combo_hosters.pack(fill='x')
        combo_wrap.track(self.combo_hosters)
        self.combo_hosters.bind("<<ComboboxSelected>>", self.on_hoster_selected)

        self._label(selector, "Filter", muted=True).pack(side='left', padx=(px(28), px(10)))
        self.filter_var = tk.StringVar()
        self.filter_var.trace_add('write', self._on_filter_changed)
        Field(selector, textvariable=self.filter_var, width=18).pack(side='left')

        tree_frame = tk.Frame(page, bg=P.BG)
        tree_frame.pack(fill='both', expand=True)
        self.tree = ttk.Treeview(tree_frame, columns=('filename', 'url', 'hoster'), show='headings',
                                 selectmode='extended')
        for col, text, w, grow in (('filename', 'Part', 220, False), ('url', 'Download URL', 360, True),
                                   ('hoster', 'Hoster', 120, False)):
            self.tree.heading(col, text=text, anchor='w')
            self.tree.column(col, width=px(w), minwidth=px(90), anchor='w', stretch=grow)
        self.tree.tag_configure('alt', background=P.RAISED)

        scrollbar = ttk.Scrollbar(tree_frame, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side='left', fill='both', expand=True)
        scrollbar.pack(side='right', fill='y', padx=(px(6), 0))

        self.lbl_empty = tk.Label(tree_frame, text="", justify='center', font=(UI_FONT, 10),
                                  bg=P.BG, fg=P.MUTED)

        self.tree_menu = tk.Menu(self.root, tearoff=0, bg=P.RAISED, fg=P.TEXT, bd=0,
                                 activebackground=P.ACCENT_DIM, activeforeground=P.TEXT)
        self.tree_menu.add_command(label="Copy URL", command=lambda: self._copy_tree_col(1))
        self.tree_menu.add_command(label="Copy filename", command=lambda: self._copy_tree_col(0))
        self.tree.bind('<Button-3>', self._show_tree_menu)

        self.populate_tree()

    def on_paste_url(self):
        try:
            text = self.root.clipboard_get().strip()
        except tk.TclError:
            return
        if text:
            self.url_var.set(text)

    def _show_tree_menu(self, event):
        row = self.tree.identify_row(event.y)
        if row:
            if row not in self.tree.selection():
                self.tree.selection_set(row)
            self.tree_menu.tk_popup(event.x_root, event.y_root)

    def _copy_tree_col(self, idx):
        values = [self.tree.item(i, 'values')[idx] for i in self.tree.selection()]
        if values:
            self.root.clipboard_clear()
            self.root.clipboard_append("\n".join(values))
            self.status_var.set(f"Copied {len(values)} item(s) to clipboard.")

    # ================= PAGE 2: MONITOR =================
    def _build_monitor_page(self, page):
        self._page_header(page, "Monitor", "Watch IDM finish the parts, then unpack them in place.")

        form = self._form(page)
        cell = self._form_row(form, "Download folder")
        self.mon_folder_var = tk.StringVar(value=DEFAULT_DL)
        folder_field = Field(cell, textvariable=self.mon_folder_var)
        folder_field.pack(side='left', fill='x', expand=True, padx=(0, px(10)))
        folder_field.entry.bind('<Return>', lambda e: self.refresh_folder_info())
        folder_field.entry.bind('<FocusOut>', lambda e: self.refresh_folder_info(), add='+')
        FlatButton(cell, "Browse", command=self.on_browse_mon_folder).pack(side='left', padx=(0, px(8)))
        FlatButton(cell, "Auto-detect", command=self.on_auto_detect_folder).pack(side='left')

        cell = self._form_row(form, "Expected parts")
        self.total_parts_var = tk.StringVar(value="0")
        Field(cell, textvariable=self.total_parts_var, width=6).pack(side='left', padx=(0, px(16)))
        self.lbl_folder_rars_found = tk.Label(cell, text="", bg=P.BG, fg=P.MUTED, font=(UI_FONT, 9), anchor='w')
        self.lbl_folder_rars_found.pack(side='left', fill='x', expand=True)

        cell = self._form_row(form, "When done")
        self.auto_extract_var = tk.BooleanVar(value=True)
        self.auto_delete_rars_var = tk.BooleanVar(value=True)
        for var, text in ((self.auto_extract_var, "Extract all RAR parts automatically"),
                          (self.auto_delete_rars_var, "Permanently delete the .rar parts after a successful extraction")):
            tk.Checkbutton(cell, text=text, variable=var, bg=P.BG, fg=P.TEXT, selectcolor=P.BG,
                           activebackground=P.BG, activeforeground=P.TEXT, bd=0, highlightthickness=0,
                           font=(UI_FONT, 10), anchor='w', cursor='hand2').pack(anchor='w', pady=1)

        self._rule(page, pady=(px(10), px(18)))

        # Progress: one large readout and plain stats on a single baseline, then a thin line
        top = tk.Frame(page, bg=P.BG)
        top.pack(fill='x', pady=(0, px(12)))
        self.val_pct = tk.Label(top, text="0%", font=(MONO_FONT, 40, 'bold'), bg=P.BG, fg=P.TEXT)
        self.val_pct.pack(side='left', anchor='s')

        stats = tk.Frame(top, bg=P.BG)
        stats.pack(side='right', anchor='s', pady=(0, px(8)))
        self.val_downloaded = self._stat(stats, "Parts", "0 / 0")
        self.val_speed = self._stat(stats, "Speed", "--")
        self.val_eta = self._stat(stats, "ETA", "--:--")
        self.val_size = self._stat(stats, "Downloaded", "0 B")
        self.val_elapsed = self._stat(stats, "Elapsed", "--:--")

        self.progress_bar = ProgressLine(page)
        self.progress_bar.pack(fill='x', pady=(0, px(10)))

        self.mon_status_var = tk.StringVar(value="Idle. Press Start monitoring to watch IDM progress.")
        self.lbl_mon_status = tk.Label(page, textvariable=self.mon_status_var, font=(UI_FONT, 10),
                                       bg=P.BG, fg=P.MUTED, anchor='w')
        self.lbl_mon_status.pack(fill='x')

        controls = tk.Frame(page, bg=P.BG)
        controls.pack(fill='x', pady=(px(14), px(16)))
        self.btn_toggle_monitor = FlatButton(controls, "Start monitoring", variant='primary',
                                             command=self.on_toggle_monitor)
        self.btn_toggle_monitor.pack(side='left')
        self.btn_extract_now = FlatButton(controls, "Extract now", command=self.on_manual_extract)
        self.btn_extract_now.pack(side='left', padx=(px(8), 0))
        FlatButton(controls, "Open folder", variant='ghost', command=self.on_open_folder).pack(side='left', padx=(px(8), 0))

        self._rule(page, pady=(0, px(12)))
        self._label(page, "Activity", muted=True, font=(UI_FONT, 9)).pack(anchor='w', pady=(0, px(6)))
        log_frame = tk.Frame(page, bg=P.BG)
        log_frame.pack(fill='both', expand=True)
        self.log_text = tk.Text(log_frame, height=4, font=(MONO_FONT, 9), wrap='word', bg=P.BG, fg=P.TEXT,
                                relief='flat', bd=0, padx=0, pady=0, insertbackground=P.TEXT,
                                highlightthickness=0, state='disabled', selectbackground=P.ACCENT_DIM,
                                spacing1=2)
        log_scroll = ttk.Scrollbar(log_frame, orient='vertical', command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.pack(side='left', fill='both', expand=True)
        log_scroll.pack(side='right', fill='y', padx=(px(6), 0))
        for tag, color in (('ts', P.FAINT), ('info', P.TEXT), ('ok', P.ACCENT), ('warn', P.AMBER), ('err', P.RED)):
            self.log_text.tag_configure(tag, foreground=color)

        self.on_auto_detect_folder()

    def _stat(self, parent, label, initial):
        col = tk.Frame(parent, bg=P.BG)
        col.pack(side='left', padx=(px(28), 0))
        tk.Label(col, text=label, font=(UI_FONT, 9), bg=P.BG, fg=P.MUTED).pack(anchor='w')
        val = tk.Label(col, text=initial, font=(MONO_FONT, 11, 'bold'), bg=P.BG, fg=P.TEXT)
        val.pack(anchor='w')
        return val

    def log(self, message, level='info'):
        """Thread-safe append to the activity log."""
        def _append():
            self.log_text.configure(state='normal')
            self.log_text.insert('end', f"{time.strftime('%H:%M:%S')}  ", 'ts')
            self.log_text.insert('end', message + "\n", level)
            self.log_text.see('end')
            self.log_text.configure(state='disabled')
        try:
            self.root.after(0, _append)
        except RuntimeError:
            pass

    def _ui(self, fn, *args):
        try:
            self.root.after(0, fn, *args)
        except RuntimeError:
            pass

    # ================= LINKS PAGE HANDLERS =================
    def on_browse_dest(self):
        folder = filedialog.askdirectory(initialdir=self.dest_var.get())
        if folder:
            self.dest_var.set(os.path.normpath(folder))

    def on_extract_clicked(self):
        url = self.url_var.get().strip()
        if not url:
            messagebox.showwarning("Input required", "Please enter a FitGirl URL.")
            return
        self.btn_extract.config(state='disabled', text='Resolving links')
        self.status_var.set("Scraping page and resolving direct links...")
        threading.Thread(target=self._worker_parse, args=(url,), daemon=True).start()

    def _worker_parse(self, url):
        res = self.parser.parse_url(url)
        self._ui(self._on_parse_complete, res)

    def _on_parse_complete(self, res):
        self.btn_extract.config(state='normal', text='Extract links')
        if not res['success']:
            messagebox.showerror("Error", res['error'])
            self.status_var.set("Failed to parse page.")
            return

        self.parsed_data = res
        title = res['title']
        self.lbl_game_title.config(text=title, fg=P.TEXT)

        target_path = os.path.join(DEFAULT_DL, safe_name(title))
        self.dest_var.set(target_path)
        self.mon_folder_var.set(target_path)

        hosters = list(res['categorized_links'].keys())
        if not hosters:
            messagebox.showinfo("No links", "No supported file host links found on this page.")
            self.status_var.set("No download links found.")
            return

        # Pre-select a recommended hoster
        rec_idx = 0
        for i, h in enumerate(hosters):
            if any(good in h.lower() for good in ('filekeeper', 'datanodes', 'pixeldrain')):
                rec_idx = i + 1
                break
        self.combo_hosters['values'] = ["All Hosters"] + hosters
        self.combo_hosters.current(rec_idx)

        self.populate_tree()
        links = self.get_selected_links()
        if links:
            self.total_parts_var.set(str(len(links)))
        self.status_var.set(f"Found {res['total_found']} links across {len(hosters)} mirrors.")

    def on_hoster_selected(self, event=None):
        self.populate_tree()
        links = self.get_selected_links()
        if links:
            self.total_parts_var.set(str(len(links)))

    def _on_filter_changed(self, *_):
        """Debounce: rebuild once typing pauses instead of on every keystroke."""
        if self._filter_job:
            self.root.after_cancel(self._filter_job)
        self._filter_job = self.root.after(120, self.populate_tree)

    def _current_items(self):
        if not self.parsed_data:
            return []
        categorized = self.parsed_data['categorized_links']
        selected = self.combo_hosters.get()
        if selected == "All Hosters" or not selected:
            return [(item, h) for h, items in categorized.items() for item in items]
        return [(item, selected) for item in categorized.get(selected, [])]

    def get_selected_links(self):
        return [item for item, _ in self._current_items()]

    def populate_tree(self):
        self._filter_job = None
        self.tree.delete(*self.tree.get_children())
        items = self._current_items()
        has_links = bool(items)
        for btn in (self.btn_send, self.btn_export_ef2, self.btn_export_txt, self.btn_export_bat):
            btn.config(state='normal' if has_links else 'disabled')

        if not self.parsed_data:
            self.lbl_count.config(text="0 parts")
            self._show_empty("No links yet\nPaste a FitGirl URL above and press Enter.")
            return

        needle = self.filter_var.get().strip().lower()
        shown = 0
        for item, host in items:
            fn = item.get('filename') or ''
            if needle and needle not in fn.lower():
                continue
            self.tree.insert('', 'end', values=(fn, item.get('direct') or item.get('original'), host),
                             tags=('alt',) if shown % 2 else ())
            shown += 1
        self.lbl_count.config(text=f"{shown} of {len(items)} parts")
        self._show_empty("" if shown else "No parts match this filter.")

    def _show_empty(self, text):
        if text:
            self.lbl_empty.config(text=text)
            self.lbl_empty.place(relx=0.5, rely=0.5, anchor='center')
        else:
            self.lbl_empty.place_forget()

    def _export_target(self, suffix):
        """Shared export preamble. Returns (links, dest_dir, filepath) or None after telling the user."""
        links = self.get_selected_links()
        if not links:
            messagebox.showwarning("No links", "Extract links from a FitGirl page first.")
            return None
        dest_dir = self.dest_var.get().strip()
        try:
            os.makedirs(dest_dir, exist_ok=True)
        except OSError as exc:
            messagebox.showerror("Cannot create folder", f"{dest_dir}\n\n{exc}")
            return None
        title = safe_name(self.parsed_data.get('title', 'fitgirl_downloads'))
        return links, dest_dir, os.path.join(dest_dir, f"{title}{suffix}")

    def _export_file(self, kind):
        target = self._export_target('.txt' if kind == 'txt' else '_idm.bat')
        if not target:
            return
        links, dest_dir, filepath = target
        if kind == 'txt':
            self.exporter.export_txt(links, filepath)
            messagebox.showinfo("Export complete", f"Saved link list:\n{filepath}")
        else:
            self.exporter.export_bat(links, filepath, download_dir=dest_dir)
            messagebox.showinfo("Export complete", f"Saved batch script:\n{filepath}")

    def _refresh_direct(self, item):
        """Relay callback: resolve a fresh direct URL when a queued link has expired."""
        if item.get('original') and 'filekeeper' in item['original']:
            return self.parser.resolve_filekeeper(item['original'])
        return None

    def on_send_to_idm(self):
        """Queue every part in IDM directly. No .ef2 import, no browser."""
        if not self.exporter.idm_path:
            messagebox.showwarning("IDM not found", "IDMan.exe was not found. Use Export .ef2 or Export .bat instead.")
            return
        target = self._export_target('.ef2')
        if not target:
            return
        links, dest_dir, manifest = target
        # The manifest is how Monitor knows the exact part list, so keep writing it
        self.exporter.export_ef2(links, manifest, download_dir=dest_dir)
        self.btn_send.config(state='disabled', text='Sending to IDM')
        self.status_var.set(f"Queuing {len(links)} parts in IDM...")
        threading.Thread(target=self._worker_send, args=(links, dest_dir), daemon=True).start()

    def _worker_send(self, links, dest_dir):
        ok, msg, count = self.exporter.send_to_idm(links, dest_dir, relay=self.relay)
        self._ui(self._on_sent, ok, msg, dest_dir, len(links))

    def _on_sent(self, ok, msg, dest_dir, total):
        self.btn_send.config(state='normal', text='Send to IDM')
        if not ok:
            self.status_var.set("Could not queue links in IDM.")
            messagebox.showerror("IDM", msg)
            return
        self.status_var.set(msg)
        self.log(msg, 'ok')
        if self.relay.has_items:
            self.log(f"Relay listening on 127.0.0.1:{self.relay.port}.")
        if self.is_monitoring:
            self.on_stop_monitor()
        self.mon_folder_var.set(dest_dir)
        self.total_parts_var.set(str(total))
        self.show_page('monitor')
        self.on_start_monitor()

    def _on_close(self):
        if self.relay.active or (self.relay.has_items and self.is_monitoring):
            if not messagebox.askyesno("Downloads in progress",
                                       "IDM is still pulling parts through this app. Closing it will make "
                                       "those downloads fail.\n\nQuit anyway?"):
                return
        self.relay.stop()
        self.root.destroy()

    def on_export_ef2(self):
        target = self._export_target('.ef2')
        if not target:
            return
        links, dest_dir, filepath = target
        self.exporter.export_ef2(links, filepath, download_dir=dest_dir)

        # Highlight the file in Explorer so it is easy to pick, then launch IDM if installed
        try:
            subprocess.Popen(f'explorer /select,"{os.path.abspath(filepath)}"')
        except Exception:
            pass
        if self.exporter.idm_path and os.path.exists(self.exporter.idm_path):
            try:
                subprocess.Popen([self.exporter.idm_path])
            except Exception:
                pass

        instructions = (
            f"Saved the batch file in your download folder:\n{filepath}\n\n"
            "In IDM: Tasks > Import > From IDM export file, pick this .ef2, then Start Queue.\n\n"
            "Switch to Monitor to watch progress, speed and ETA now?"
        )
        if messagebox.askyesno("Ready for IDM", instructions):
            self.mon_folder_var.set(dest_dir)
            self.total_parts_var.set(str(len(links)))
            self.show_page('monitor')
            self.on_start_monitor()

    # ================= FOLDER INSPECTION (off the UI thread) =================
    def _read_ef2_files(self, ef2_path):
        """Filenames listed in an .ef2, cached by modification time."""
        try:
            mtime = os.path.getmtime(ef2_path)
        except OSError:
            return []
        cached = self._ef2_cache.get(ef2_path)
        if cached and cached[0] == mtime:
            return cached[1]
        files = []
        try:
            with open(ef2_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    low = line.strip().lower()
                    if low.startswith(("file name:", "filename:")):
                        fn = line.strip().split(":", 1)[1].strip()
                        if fn:
                            files.append(fn)
        except OSError:
            pass
        self._ef2_cache[ef2_path] = (mtime, files)
        return files

    def detect_ef2_in_folder(self, folder):
        """
        Finds the .ef2 manifest for `folder`. An .ef2 inside the folder wins outright. Otherwise the
        candidate (parent dir, sibling folders, ./exports) with the most files present in the folder is
        used, and only if at least one of its files is actually there.
        """
        if not folder or not os.path.isdir(folder):
            return 0, [], None
        norm_folder = os.path.normpath(folder).lower()

        direct = glob.glob(os.path.join(folder, "*.ef2"))
        if direct:
            direct.sort(key=os.path.getmtime, reverse=True)
            files = self._read_ef2_files(direct[0])
            return len(files), files, direct[0]

        candidates = []
        parent = os.path.dirname(folder)
        if parent and parent != folder and os.path.isdir(parent):
            candidates += glob.glob(os.path.join(parent, "*.ef2"))
            candidates += glob.glob(os.path.join(parent, "*", "*.ef2"))
        candidates += glob.glob(os.path.join(os.getcwd(), "exports", "*.ef2"))

        best, best_files, best_matches = None, [], 0
        seen = set()
        for c in candidates:
            key = os.path.normpath(c).lower()
            if key in seen or os.path.dirname(key) == norm_folder:
                continue
            seen.add(key)
            c_files = self._read_ef2_files(c)
            matches = sum(1 for f in c_files if os.path.exists(os.path.join(folder, f)))
            if matches > best_matches:
                best, best_files, best_matches = c, c_files, matches
        return len(best_files), best_files, best

    def _inspect_folder(self, folder):
        info = dict(exists=os.path.isdir(folder), renamed=0, count=0, files=[], ef2=None,
                    ready=0, total=0, extracted=False)
        if not info['exists']:
            return info
        info['renamed'] = self.extractor.normalize_idm_filenames(folder)
        info['count'], info['files'], info['ef2'] = self.detect_ef2_in_folder(folder)
        info['extracted'] = os.path.exists(os.path.join(folder, "setup.exe"))
        if info['files']:
            info['total'] = info['count']
            info['ready'] = sum(1 for f in info['files']
                                if self.extractor.is_file_ready(os.path.join(folder, f)))
        else:
            rars = self.extractor.get_all_rar_parts(folder)
            info['total'] = len(rars)
            info['ready'] = sum(1 for r in rars if self.extractor.is_file_ready(r))
        return info

    def refresh_folder_info(self):
        folder = self.mon_folder_var.get().strip()
        self._folder_scan_id += 1
        scan_id = self._folder_scan_id

        def work():
            self._ui(self._apply_folder_info, scan_id, folder, self._inspect_folder(folder))
        threading.Thread(target=work, daemon=True).start()

    def _apply_folder_info(self, scan_id, folder, info):
        if scan_id != self._folder_scan_id:     # a newer scan superseded this one
            return
        name = os.path.basename(folder)
        if info['renamed']:
            self.log(f"Normalized {info['renamed']} IDM duplicate filename(s) (_2 to clean names).")

        if not info['exists']:
            self.lbl_folder_rars_found.config(text="Folder does not exist yet. It will be watched once it appears.",
                                              fg=P.MUTED)
            return

        if info['files']:
            self.total_parts_var.set(str(info['count']))

        already_done = info['extracted'] and (not info['files'] or info['ready'] == 0)
        if already_done:
            self.lbl_folder_rars_found.config(text="Already extracted. setup.exe is ready to install.", fg=P.ACCENT)
            if not self.is_monitoring and not self._extracting:
                self.mon_status_var.set("Already extracted. setup.exe is ready.")
                self.lbl_mon_status.config(fg=P.ACCENT)
                self.progress_bar.set(100)
                self.val_pct.config(text="100%")
                self.val_downloaded.config(text="Extracted")
                self.val_eta.config(text="Ready")
                self.log(f"Repack already extracted in {name}.")
            return

        if info['files']:
            ef2_name = os.path.basename(info['ef2']) if info['ef2'] else ".ef2"
            if len(ef2_name) > 38:
                ef2_name = ef2_name[:35] + "..."
            self.lbl_folder_rars_found.config(
                text=f"From {ef2_name}: {info['ready']} of {info['count']} parts ready", fg=P.MUTED)
        else:
            self.lbl_folder_rars_found.config(
                text=f"In folder: {info['ready']} of {info['total']} RAR parts ready", fg=P.MUTED)

    def on_browse_mon_folder(self):
        folder = filedialog.askdirectory(initialdir=self.mon_folder_var.get())
        if folder:
            self.mon_folder_var.set(os.path.normpath(folder))
            self.refresh_folder_info()

    def on_auto_detect_folder(self):
        self._folder_scan_id += 1
        scan_id = self._folder_scan_id

        def work():
            recent = find_recent_repack_folders(DEFAULT_DL)
            folder = recent[0][0] if recent else self.mon_folder_var.get().strip()
            self._ui(self._apply_auto_detect, scan_id, folder, bool(recent), self._inspect_folder(folder))
        threading.Thread(target=work, daemon=True).start()

    def _apply_auto_detect(self, scan_id, folder, found, info):
        if scan_id != self._folder_scan_id:
            return
        if found:
            self.mon_folder_var.set(folder)
            self.log(f"Auto-detected repack folder: {os.path.basename(folder)}")
        self._apply_folder_info(scan_id, folder, info)

    # ================= MONITORING =================
    def _get_idm_temp_bytes(self):
        """Bytes IDM has staged for in-flight downloads (all of them, not just this repack)."""
        appdata = os.getenv('APPDATA')
        if not appdata:
            return 0
        stack, total = [os.path.join(appdata, 'IDM', 'DwnlData')], 0
        while stack:
            try:
                with os.scandir(stack.pop()) as it:
                    for e in it:
                        if e.is_dir():
                            stack.append(e.path)
                        elif not e.name.endswith('.log'):
                            total += e.stat().st_size
            except OSError:
                pass
        return total

    def _snapshot(self, folder, targets, ready_cache):
        """
        One directory scan per tick. A part counts as ready when it has bytes, no IDM temp sibling, and
        has been opened once; after that it is trusted until its size changes.
        Returns (completed_bytes, idm_temp_bytes, total_bytes, ready_count).
        """
        sizes = {}
        try:
            self.extractor.normalize_idm_filenames(folder)
            with os.scandir(folder) as it:
                for e in it:
                    if e.is_file():
                        sizes[e.name.lower()] = (e.name, e.stat().st_size)
        except OSError:
            pass
        names = [n for n in sizes if n.endswith(ARCHIVE_SUFFIXES)] if targets is None \
            else [n for n in targets if n in sizes]

        completed = ready = 0
        for n in names:
            actual, size = sizes[n]
            completed += size
            if size == 0 or any(n + s in sizes for s in IDM_TEMP_SUFFIXES):
                continue
            if ready_cache.get(n) != size:
                try:
                    with open(os.path.join(folder, actual), 'rb') as f:
                        f.read(64)
                except OSError:
                    continue
                ready_cache[n] = size
            ready += 1
        temp = self._get_idm_temp_bytes()
        return completed, temp, completed + temp, ready

    def on_toggle_monitor(self):
        if self.is_monitoring:
            self.on_stop_monitor()
        else:
            self.on_start_monitor()

    def on_start_monitor(self):
        folder = self.mon_folder_var.get().strip()
        if not folder:
            messagebox.showwarning("Folder required", "Choose a download folder to monitor.")
            return
        try:
            total_parts = int(self.total_parts_var.get())
        except ValueError:
            total_parts = 0

        self._stop = stop = threading.Event()
        self.is_monitoring = True
        self.btn_toggle_monitor.config(text="Stop monitoring", variant='danger')
        self.mon_status_var.set(f"Watching {os.path.basename(folder)}...")
        self.lbl_mon_status.config(fg=P.ACCENT)
        self.log(f"Started monitoring {os.path.basename(folder)} ({total_parts} parts expected).")
        self.refresh_folder_info()

        threading.Thread(target=self._monitor_worker, args=(folder, total_parts, stop), daemon=True).start()

    def on_stop_monitor(self):
        if self._stop:
            self._stop.set()
        self.is_monitoring = False
        self.btn_toggle_monitor.config(text="Start monitoring", variant='primary')
        self.mon_status_var.set("Monitoring stopped.")
        self.lbl_mon_status.config(fg=P.AMBER)
        self.log("Monitoring stopped.", "warn")

    def _monitor_worker(self, folder, total_parts, stop):
        """Runs off the UI thread and touches no Tk variables. `stop.wait` makes Stop immediate."""
        try:
            count, ef2_files, _ = self.detect_ef2_in_folder(folder)   # once per session, not per tick
            if count:
                total_parts = count
                self._ui(self.total_parts_var.set, str(count))
            targets = [f.lower() for f in ef2_files] or None
            ready_cache = {}
            expected_bytes = total_parts * ASSUMED_PART_BYTES

            start = prev_time = time.time()
            _, _, prev_bytes, _ = self._snapshot(folder, targets, ready_cache)
            initial_bytes, smoothed = prev_bytes, 0.0

            while not stop.wait(POLL_SECONDS):
                now = time.time()
                comp, _, active, ready = self._snapshot(folder, targets, ready_cache)

                dt = max(now - prev_time, 0.001)
                inst = max(0, active - prev_bytes) / dt
                smoothed = inst if smoothed == 0.0 else 0.75 * smoothed + 0.25 * inst   # EMA
                elapsed = now - start
                speed = smoothed
                if elapsed > 15:
                    cumulative = max(0, active - initial_bytes) / elapsed
                    if cumulative > 100 * 1024:
                        speed = 0.6 * smoothed + 0.4 * cumulative
                prev_bytes, prev_time = active, now

                if ready > 0 and comp > 0:
                    remaining = max(0, (total_parts - ready) * (comp / ready))
                else:
                    remaining = max(0, expected_bytes - active)
                eta = remaining / speed if speed > 100 * 1024 else 0

                self._ui(self._update_monitor_ui, stop, ready, total_parts, comp, speed, eta, elapsed)

                if total_parts > 0 and ready >= total_parts:
                    self._ui(self._on_downloads_all_complete, stop, folder, ready)
                    return
        except Exception as exc:
            self.log(f"Monitor stopped unexpectedly: {exc}", "err")
            self._ui(self._monitor_crashed, stop)

    def _monitor_crashed(self, stop):
        if stop is self._stop:
            self.on_stop_monitor()
            self.lbl_mon_status.config(fg=P.RED)

    def _update_monitor_ui(self, stop, completed, total, comp_bytes, speed_bps, eta_seconds, elapsed_seconds):
        if stop is not self._stop or stop.is_set():
            return
        pct = (completed / total * 100) if total > 0 else 0
        self.progress_bar.set(pct)
        self.val_pct.config(text=f"{pct:.0f}%")
        self.val_downloaded.config(text=f"{completed} / {total}")
        self.val_speed.config(text=f"{format_bytes(speed_bps)}/s" if speed_bps > 0 else "--")
        self.val_eta.config(text=format_time(eta_seconds) if eta_seconds > 0 else "Estimating")
        self.val_size.config(text=format_bytes(comp_bytes))
        self.val_elapsed.config(text=format_time(elapsed_seconds))
        self.mon_status_var.set(f"{completed} of {total} parts finished in IDM")

    def _on_downloads_all_complete(self, stop, folder, total_count):
        if stop is not self._stop or stop.is_set():
            return
        self.is_monitoring = False
        self.btn_toggle_monitor.config(text="Start monitoring", variant='primary')
        self.progress_bar.set(100)
        self.val_pct.config(text="100%")
        self.val_downloaded.config(text=f"{total_count} / {total_count}")
        self.val_eta.config(text="Done")
        self.mon_status_var.set(f"All {total_count} parts downloaded.")
        self.lbl_mon_status.config(fg=P.ACCENT)
        self.log(f"All {total_count} parts downloaded in {folder}.", "ok")

        if self.auto_extract_var.get():
            self.log("Starting extraction.")
            self._start_extraction(folder)
        else:
            messagebox.showinfo("Downloads complete",
                                f"All {total_count} parts finished in:\n{folder}\n\nUse Extract now to unpack them.")

    # ================= EXTRACTION =================
    def on_manual_extract(self):
        folder = self.mon_folder_var.get().strip()
        if self._extracting:
            return
        if not os.path.exists(folder):
            messagebox.showerror("Folder not found", f"Cannot find directory:\n{folder}")
            return
        rars = self.extractor.get_all_rar_parts(folder)
        if not rars:
            messagebox.showwarning("No RAR files", f"No .rar parts found in:\n{folder}")
            return
        delete_msg = ("The .rar parts will be PERMANENTLY deleted afterwards."
                      if self.auto_delete_rars_var.get() else "The .rar parts will be kept.")
        if messagebox.askyesno("Extract", f"Unpack {len(rars)} RAR parts into:\n{folder}\n\n{delete_msg}\n\nProceed?"):
            self._start_extraction(folder)

    def _start_extraction(self, folder):
        if self._extracting:
            return
        self._extracting = True
        self.btn_extract_now.config(state='disabled')
        self.mon_status_var.set("Extracting with 7-Zip. This can take a while.")
        self.lbl_mon_status.config(fg=P.AMBER)
        self.progress_bar.set(0)
        self.val_pct.config(text="0%")
        self.val_downloaded.config(text="Extracting")
        self.val_speed.config(text="--")
        self.val_eta.config(text="--:--")
        self.log(f"Extracting {os.path.basename(folder)}.")
        threading.Thread(target=self._run_extraction_task, args=(folder,), daemon=True).start()

    def _run_extraction_task(self, folder):
        def on_progress(line):
            clean = line.strip()
            m = re.search(r'(\d+)%', clean)
            self._ui(self._extraction_progress, int(m.group(1)) if m else None, clean)

        success = self.extractor.extract_archive(
            folder, extract_to=folder, delete_rars_after=self.auto_delete_rars_var.get(),
            progress_callback=on_progress)
        self._ui(self._extraction_finished, folder, success)

    def _extraction_progress(self, pct, line):
        if pct is not None:
            self.progress_bar.set(pct)
            self.val_pct.config(text=f"{pct}%")
        self.mon_status_var.set(f"Extracting: {line[:75]}")

    def _extraction_finished(self, folder, success):
        self._extracting = False
        self.btn_extract_now.config(state='normal')
        if success:
            self.progress_bar.set(100)
            self.val_pct.config(text="100%")
            self.val_downloaded.config(text="Extracted")
            self.mon_status_var.set("Extracted. setup.exe is ready.")
            self.lbl_mon_status.config(fg=P.ACCENT)
            self.log(f"Extraction finished in {folder}.", "ok")
            if self.auto_delete_rars_var.get():
                self.log("Deleted the .rar parts to free disk space.")
            messagebox.showinfo("Extraction done", f"Game files are in:\n{folder}\n\nRun setup.exe to install.")
        else:
            self.mon_status_var.set("Extraction failed. See the activity log.")
            self.lbl_mon_status.config(fg=P.RED)
            self.log("Extraction failed.", "err")
            messagebox.showerror("Extraction failed", "Could not extract the archives. Make sure 7-Zip or WinRAR is installed.")

    def on_open_folder(self):
        folder = self.mon_folder_var.get().strip()
        target = folder if os.path.exists(folder) else DEFAULT_DL
        if os.path.exists(target):
            os.startfile(target)


def main():
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("FitGirlToIDM.Downloader")
        except Exception:
            pass
    root = tk.Tk()
    FitGirlIDMApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
