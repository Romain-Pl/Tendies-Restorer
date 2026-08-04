#!/usr/bin/env python3
"""
Graphical interface tying together the project's five tools:
  1. Combine several .tendies into one, with swipeable variants (family)
  2. Enable/disable appearanceAware (Auto/Light/Dark selector)
  3. Inject a .tendies into an iPhone backup (manual backup selection)
  4. Inject a .tendies into an Xcode simulator (manual simulator selection)
  5. Dump an already-installed poster back into a .tendies

Usage:
    python3 gui.py

Doesn't replace the command-line scripts (combine.py, appearance.py,
deploy.py, sim_deploy.py, dump.py): this interface calls them directly,
every action is logged to logs/ just like any CLI run.

Available in French and English (switch at the top of the window, the
starting language is auto-detected from the system locale) — see i18n.py
for the translation catalog.
"""
import logging
import plistlib
import queue
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, simpledialog, ttk

import combine
import appearance
import deploy
import dump
import sim_deploy
from i18n import Translator
from logsetup import LOG_DIR, get_logger, setup_logging

logger = get_logger()


# ---------------------------------------------------------------------------
# Logging -> UI bridge: the existing modules (combine/appearance/deploy/
# sim_deploy/dump) already log via logsetup; this just plugs in an extra
# handler that pushes each line into a queue read by the Tkinter loop, so it
# streams into the UI without blocking it.
# ---------------------------------------------------------------------------

class QueueHandler(logging.Handler):
    def __init__(self, log_queue):
        super().__init__()
        self.log_queue = log_queue

    def emit(self, record):
        self.log_queue.put(self.format(record))


def describe_backup(path: Path) -> str:
    info_plist = path / "Info.plist"
    if info_plist.exists():
        try:
            with open(info_plist, "rb") as f:
                info = plistlib.load(f)
            device = info.get("Device Name", "?")
            product = info.get("Product Name", "?")
            version = info.get("Product Version", "?")
            last = info.get("Last Backup Date", "?")
            return f"{device} ({product}, iOS {version}) — {last} — {path.name}"
        except Exception:
            pass
    return path.name


def describe_simulator(sim: dict) -> str:
    runtime = sim["runtime"].rsplit("SimRuntime.", 1)[-1]
    parts = runtime.split("-")
    pretty_runtime = f"{parts[0]} {'.'.join(parts[1:])}" if len(parts) > 1 else runtime
    return f"{sim['name']} — {pretty_runtime} — {sim['state']}  ({sim['udid']})"


class TendiesGUI(tk.Tk):
    def __init__(self, log_path: Path = None):
        super().__init__()
        self.log_path = log_path
        self.tr = Translator()
        self._retranslate_callbacks = []

        self.title("Tendies Toolbox")
        self.geometry("900x780")
        self.minsize(760, 620)

        self.backups = []      # list[Path], parallel to the Backup tab's Listbox
        self.simulators = []   # list[dict], parallel to the Simulator tab's Listbox

        self._build_language_switcher()

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=8, pady=(4, 4))

        self.tab_combine = ttk.Frame(self.notebook)
        self.tab_appearance = ttk.Frame(self.notebook)
        self.tab_backup = ttk.Frame(self.notebook)
        self.tab_simulator = ttk.Frame(self.notebook)
        self.tab_dump = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_combine)
        self.notebook.add(self.tab_appearance)
        self.notebook.add(self.tab_backup)
        self.notebook.add(self.tab_simulator)
        self.notebook.add(self.tab_dump)
        self._register_retranslate(lambda: self.notebook.tab(self.tab_combine, text=self.tr.t("tab.combine")))
        self._register_retranslate(lambda: self.notebook.tab(self.tab_appearance, text=self.tr.t("tab.appearance")))
        self._register_retranslate(lambda: self.notebook.tab(self.tab_backup, text=self.tr.t("tab.backup")))
        self._register_retranslate(lambda: self.notebook.tab(self.tab_simulator, text=self.tr.t("tab.simulator")))
        self._register_retranslate(lambda: self.notebook.tab(self.tab_dump, text=self.tr.t("tab.dump")))

        self._build_combine_tab(self.tab_combine)
        self._build_appearance_tab(self.tab_appearance)
        self._build_backup_tab(self.tab_backup)
        self._build_simulator_tab(self.tab_simulator)
        self._build_dump_tab(self.tab_dump)

        self.log_frame = ttk.LabelFrame(self)
        self.log_frame.pack(fill="both", expand=False, padx=8, pady=(4, 8))
        self._register_retranslate(lambda: self.log_frame.configure(text=self.tr.t("log.title")))
        log_btn_row = ttk.Frame(self.log_frame)
        log_btn_row.pack(fill="x", padx=4, pady=(4, 0))
        self._button(log_btn_row, "log.open_folder", self._open_log_dir).pack(side="left")
        self.log_text = scrolledtext.ScrolledText(self.log_frame, height=10, state="disabled", wrap="word")
        self.log_text.pack(fill="both", expand=True, padx=4, pady=4)

        self.log_queue = queue.Queue()
        handler = QueueHandler(self.log_queue)
        handler.setLevel(logging.INFO)
        handler.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%H:%M:%S"))
        logger.addHandler(handler)
        self.after(150, self._poll_log_queue)

        if self.log_path:
            logger.info(self.tr.t("log.session_path", path=self.log_path))

        self._refresh_backups()
        self._refresh_simulators()
        self._refresh_dump_backups()
        self._refresh_dump_simulators()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def report_callback_exception(self, exc, val, tb):
        # Tkinter silently swallows exceptions raised in callbacks (button
        # clicks, etc.) by default and just prints them to stderr, invisible
        # if the app is launched without a terminal. Route them through the
        # same log as everything else, so a UI bug is just as easy to find
        # as a conversion error.
        logger.error(self.tr.t("log.unhandled_error"), exc_info=(exc, val, tb))
        messagebox.showerror(self.tr.t("log.unexpected_title"), str(val))

    def _open_log_dir(self):
        subprocess.run(["open", str(LOG_DIR)])

    def _on_close(self):
        for staging_root in self.combine_all_staging_roots:
            shutil.rmtree(staging_root, ignore_errors=True)
        self.destroy()

    # -- i18n ----------------------------------------------------------------

    def _register_retranslate(self, fn):
        """Registers a no-argument function that applies the current
        translation to a piece of UI; called once immediately, then again
        on every language change."""
        self._retranslate_callbacks.append(fn)
        fn()

    def _set_language(self, lang):
        self.tr.lang = lang
        for fn in self._retranslate_callbacks:
            fn()

    def _build_language_switcher(self):
        row = ttk.Frame(self)
        row.pack(fill="x", padx=8, pady=(8, 0))
        self.lang_var = tk.StringVar(value=self.tr.lang)
        fr_btn = ttk.Radiobutton(row, variable=self.lang_var, value="fr",
                                  command=lambda: self._set_language("fr"))
        en_btn = ttk.Radiobutton(row, variable=self.lang_var, value="en",
                                  command=lambda: self._set_language("en"))
        fr_btn.pack(side="right", padx=(4, 8))
        en_btn.pack(side="right")
        self._register_retranslate(lambda: fr_btn.configure(text=self.tr.t("lang.french")))
        self._register_retranslate(lambda: en_btn.configure(text=self.tr.t("lang.english")))

    def _label(self, parent, key, **kw):
        w = ttk.Label(parent, **kw)
        self._register_retranslate(lambda: w.configure(text=self.tr.t(key)))
        return w

    def _button(self, parent, key, command, **kw):
        w = ttk.Button(parent, command=command, **kw)
        self._register_retranslate(lambda: w.configure(text=self.tr.t(key)))
        return w

    def _checkbutton(self, parent, key, variable, **kw):
        w = ttk.Checkbutton(parent, variable=variable, **kw)
        self._register_retranslate(lambda: w.configure(text=self.tr.t(key)))
        return w

    def _radiobutton(self, parent, key, value, variable, **kw):
        w = ttk.Radiobutton(parent, value=value, variable=variable, **kw)
        self._register_retranslate(lambda: w.configure(text=self.tr.t(key)))
        return w

    def _labelframe(self, parent, key, **kw):
        w = ttk.LabelFrame(parent, **kw)
        self._register_retranslate(lambda: w.configure(text=self.tr.t(key)))
        return w

    # -- infrastructure ----------------------------------------------------

    def _poll_log_queue(self):
        while True:
            try:
                line = self.log_queue.get_nowait()
            except queue.Empty:
                break
            self.log_text.configure(state="normal")
            self.log_text.insert("end", line + "\n")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
        self.after(150, self._poll_log_queue)

    def _run_async(self, fn, args=(), kwargs=None, buttons=(), on_success=None):
        kwargs = kwargs or {}
        for b in buttons:
            b.configure(state="disabled")

        def worker():
            error = None
            result = None
            try:
                result = fn(*args, **kwargs)
            except Exception as e:
                logger.exception(f"{getattr(fn, '__name__', fn)} failed")
                error = e

            def finish():
                for b in buttons:
                    b.configure(state="normal")
                if error is not None:
                    messagebox.showerror(self.tr.t("log.failed_title"), str(error))
                elif on_success:
                    on_success(result)

            self.after(0, finish)

        threading.Thread(target=worker, daemon=True).start()

    def _add_files_dialog(self, listbox: tk.Listbox, paths: list):
        chosen = filedialog.askopenfilenames(
            title=self.tr.t("common.dialog.pick_tendies"),
            filetypes=[(self.tr.t("common.filetype_tendies"), "*.tendies"),
                       (self.tr.t("common.filetype_all"), "*.*")],
        )
        for c in chosen:
            p = Path(c)
            if p not in paths:
                paths.append(p)
                listbox.insert("end", p.name)

    @staticmethod
    def _remove_selection(listbox: tk.Listbox, paths: list):
        for i in reversed(listbox.curselection()):
            listbox.delete(i)
            del paths[i]

    @staticmethod
    def _clear_list(listbox: tk.Listbox, paths: list):
        listbox.delete(0, "end")
        paths.clear()

    # -- Tab 1: combine -------------------------------------------------------

    def _build_combine_tab(self, parent):
        self.combine_entries = []           # ordered list of dicts (see combine.extract_descriptors)
        self.combine_all_staging_roots = set()

        self._label(parent, "combine.label.variants").pack(anchor="w", padx=8, pady=(8, 0))

        tree_frame = ttk.Frame(parent)
        tree_frame.pack(fill="both", expand=True, padx=8)
        columns = ("num", "source", "descriptor", "name")
        self.combine_tree = ttk.Treeview(tree_frame, columns=columns, show="headings", height=8, selectmode="browse")
        for col, key, width in (("num", "combine.col.num", 30), ("source", "combine.col.source", 200),
                                 ("descriptor", "combine.col.descriptor", 190), ("name", "combine.col.name", 150)):
            self.combine_tree.column(col, width=width, anchor="w")
            self._register_retranslate(lambda c=col, k=key: self.combine_tree.heading(c, text=self.tr.t(k)))
        self.combine_tree.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(tree_frame, command=self.combine_tree.yview)
        scroll.pack(side="right", fill="y")
        self.combine_tree.configure(yscrollcommand=scroll.set)
        self.combine_tree.bind("<Double-1>", lambda e: self._combine_rename())

        btn_row = ttk.Frame(parent)
        btn_row.pack(fill="x", padx=8, pady=4)
        self._button(btn_row, "common.add", self._combine_add_files).pack(side="left")
        self._button(btn_row, "combine.btn.up", lambda: self._combine_move(-1)).pack(side="left", padx=4)
        self._button(btn_row, "combine.btn.down", lambda: self._combine_move(1)).pack(side="left")
        self._button(btn_row, "combine.btn.rename", self._combine_rename).pack(side="left", padx=4)
        self._button(btn_row, "combine.btn.remove", self._combine_remove).pack(side="left")
        self._button(btn_row, "common.clear", self._combine_clear).pack(side="left", padx=4)

        form = ttk.Frame(parent)
        form.pack(fill="x", padx=8, pady=8)
        self._label(form, "combine.label.family").grid(row=0, column=0, sticky="w")
        self.combine_family_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.combine_family_var, width=40).grid(row=0, column=1, sticky="w", padx=6)

        self._label(form, "combine.label.output").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.combine_output_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.combine_output_var, width=50).grid(row=1, column=1, sticky="w", padx=6, pady=(6, 0))
        self._button(form, "common.choose", self._choose_combine_output).grid(row=1, column=2, padx=4, pady=(6, 0))

        self.combine_button = self._button(parent, "combine.btn.combine", self._do_combine)
        self.combine_button.pack(pady=10)

    def _refresh_combine_tree(self):
        self.combine_tree.delete(*self.combine_tree.get_children())
        for i, entry in enumerate(self.combine_entries):
            self.combine_tree.insert(
                "", "end", iid=str(i),
                values=(i + 1, entry["source_name"], entry["descriptor_name"], entry["display_name"]),
            )

    def _prune_combine_staging(self):
        referenced = {e["staging_root"] for e in self.combine_entries}
        for stale in self.combine_all_staging_roots - referenced:
            shutil.rmtree(stale, ignore_errors=True)
        self.combine_all_staging_roots = referenced

    def _combine_selected_index(self):
        sel = self.combine_tree.selection()
        return int(sel[0]) if sel else None

    def _combine_add_files(self):
        chosen = filedialog.askopenfilenames(
            title=self.tr.t("common.dialog.pick_tendies"),
            filetypes=[(self.tr.t("common.filetype_tendies"), "*.tendies"),
                       (self.tr.t("common.filetype_all"), "*.*")],
        )
        for c in chosen:
            try:
                found = combine.extract_descriptors(Path(c))
            except Exception as e:
                messagebox.showerror(self.tr.t("combine.err.add_title"), f"{Path(c).name} : {e}")
                continue
            self.combine_all_staging_roots.add(found[0]["staging_root"])
            self.combine_entries.extend(found)
        self._refresh_combine_tree()

    def _combine_move(self, delta: int):
        i = self._combine_selected_index()
        if i is None:
            return
        j = i + delta
        if not (0 <= j < len(self.combine_entries)):
            return
        self.combine_entries[i], self.combine_entries[j] = self.combine_entries[j], self.combine_entries[i]
        self._refresh_combine_tree()
        self.combine_tree.selection_set(str(j))

    def _combine_rename(self):
        i = self._combine_selected_index()
        if i is None:
            messagebox.showinfo(self.tr.t("combine.btn.rename"), self.tr.t("combine.rename.select_first"))
            return
        current = self.combine_entries[i]["display_name"]
        new_name = simpledialog.askstring(
            self.tr.t("combine.rename.title"), self.tr.t("combine.rename.prompt"),
            initialvalue=current, parent=self,
        )
        if new_name and new_name.strip():
            self.combine_entries[i]["display_name"] = new_name.strip()
            self._refresh_combine_tree()
            self.combine_tree.selection_set(str(i))

    def _combine_remove(self):
        i = self._combine_selected_index()
        if i is None:
            return
        del self.combine_entries[i]
        self._refresh_combine_tree()
        self._prune_combine_staging()

    def _combine_clear(self):
        self.combine_entries.clear()
        self._refresh_combine_tree()
        self._prune_combine_staging()

    def _choose_combine_output(self):
        default_name = f"{self.combine_family_var.get().strip() or 'combo'}.tendies"
        path = filedialog.asksaveasfilename(
            title=self.tr.t("combine.dialog.save_title"),
            defaultextension=".tendies",
            initialfile=default_name,
            filetypes=[(self.tr.t("common.filetype_tendies"), "*.tendies")],
        )
        if path:
            self.combine_output_var.set(path)

    def _do_combine(self):
        if not self.combine_entries:
            messagebox.showerror(self.tr.t("combine.btn.combine"), self.tr.t("combine.err.no_variant"))
            return
        family = self.combine_family_var.get().strip()
        if not family:
            messagebox.showerror(self.tr.t("combine.btn.combine"), self.tr.t("combine.err.empty_family"))
            return
        output = self.combine_output_var.get().strip()
        if not output:
            messagebox.showerror(self.tr.t("combine.btn.combine"), self.tr.t("combine.err.no_output"))
            return

        entries_snapshot = [dict(e) for e in self.combine_entries]

        def on_success(summary):
            messagebox.showinfo(
                self.tr.t("combine.btn.combine"),
                self.tr.t("combine.success", n=len(summary), family=family, output=output),
            )
            self.combine_entries.clear()
            self._refresh_combine_tree()
            self._prune_combine_staging()

        self._run_async(
            combine.combine_variants,
            args=(entries_snapshot, Path(output), family),
            buttons=(self.combine_button,),
            on_success=on_success,
        )

    # -- Tab 2: appearanceAware ----------------------------------------------

    def _build_appearance_tab(self, parent):
        form = ttk.Frame(parent)
        form.pack(fill="x", padx=8, pady=8)

        self._label(form, "appearance.label.input").grid(row=0, column=0, sticky="w")
        self.appearance_input_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.appearance_input_var, width=50).grid(row=0, column=1, sticky="w", padx=6)
        self._button(form, "common.choose", self._choose_appearance_input).grid(row=0, column=2, padx=4)

        self.appearance_mode_var = tk.StringVar(value="on")
        mode_row = ttk.Frame(parent)
        mode_row.pack(fill="x", padx=8, pady=4)
        self._radiobutton(mode_row, "appearance.radio.on", "on", self.appearance_mode_var).pack(side="left")
        self._radiobutton(mode_row, "appearance.radio.off", "off", self.appearance_mode_var).pack(side="left", padx=10)

        self._label(form, "appearance.label.output").grid(row=1, column=0, sticky="w", pady=(10, 0))
        self.appearance_output_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.appearance_output_var, width=50).grid(row=1, column=1, sticky="w", padx=6, pady=(10, 0))
        self._button(form, "common.choose", self._choose_appearance_output).grid(row=1, column=2, padx=4, pady=(10, 0))

        self.appearance_button = self._button(parent, "appearance.btn.apply", self._do_appearance)
        self.appearance_button.pack(pady=10)

    def _choose_appearance_input(self):
        path = filedialog.askopenfilename(
            title=self.tr.t("appearance.dialog.input_title"),
            filetypes=[(self.tr.t("common.filetype_tendies"), "*.tendies"),
                       (self.tr.t("common.filetype_all"), "*.*")],
        )
        if path:
            self.appearance_input_var.set(path)
            if not self.appearance_output_var.get():
                p = Path(path)
                self.appearance_output_var.set(str(p.with_name(p.stem + ".appearance.tendies")))

    def _choose_appearance_output(self):
        path = filedialog.asksaveasfilename(
            title=self.tr.t("appearance.dialog.save_title"),
            defaultextension=".tendies",
            filetypes=[(self.tr.t("common.filetype_tendies"), "*.tendies")],
        )
        if path:
            self.appearance_output_var.set(path)

    def _do_appearance(self):
        input_path = self.appearance_input_var.get().strip()
        if not input_path:
            messagebox.showerror(self.tr.t("tab.appearance"), self.tr.t("appearance.err.no_input"))
            return
        output_path = self.appearance_output_var.get().strip()
        if not output_path:
            messagebox.showerror(self.tr.t("tab.appearance"), self.tr.t("appearance.err.no_output"))
            return
        enabled = self.appearance_mode_var.get() == "on"

        def on_success(changes):
            detail = "\n".join(f"  {name}/{wp}: {before} -> {after}" for name, wp, before, after in changes)
            messagebox.showinfo(self.tr.t("tab.appearance"), self.tr.t("appearance.success", output=output_path, detail=detail))

        self._run_async(
            appearance.set_appearance_aware,
            args=(Path(input_path), Path(output_path)),
            kwargs={"enabled": enabled},
            buttons=(self.appearance_button,),
            on_success=on_success,
        )

    # -- Tab 3: inject into a backup ------------------------------------------

    def _build_backup_tab(self, parent):
        self.backup_paths = []

        self._label(parent, "backup.label.files").pack(anchor="w", padx=8, pady=(8, 0))
        list_frame = ttk.Frame(parent)
        list_frame.pack(fill="both", expand=True, padx=8)
        self.backup_tendies_listbox = tk.Listbox(list_frame, selectmode="extended", height=6)
        self.backup_tendies_listbox.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(list_frame, command=self.backup_tendies_listbox.yview)
        scroll.pack(side="right", fill="y")
        self.backup_tendies_listbox.configure(yscrollcommand=scroll.set)

        btn_row = ttk.Frame(parent)
        btn_row.pack(fill="x", padx=8, pady=4)
        self._button(btn_row, "common.add",
                     lambda: self._add_files_dialog(self.backup_tendies_listbox, self.backup_paths)
                     ).pack(side="left")
        self._button(btn_row, "common.remove_selection",
                     lambda: self._remove_selection(self.backup_tendies_listbox, self.backup_paths)
                     ).pack(side="left", padx=4)
        self._button(btn_row, "common.clear",
                     lambda: self._clear_list(self.backup_tendies_listbox, self.backup_paths)
                     ).pack(side="left")

        self._label(parent, "backup.label.target").pack(anchor="w", padx=8, pady=(10, 0))
        backup_list_frame = ttk.Frame(parent)
        backup_list_frame.pack(fill="both", expand=True, padx=8)
        self.backup_listbox = tk.Listbox(backup_list_frame, selectmode="browse", height=5)
        self.backup_listbox.pack(side="left", fill="both", expand=True)
        scroll2 = ttk.Scrollbar(backup_list_frame, command=self.backup_listbox.yview)
        scroll2.pack(side="right", fill="y")
        self.backup_listbox.configure(yscrollcommand=scroll2.set)

        btn_row2 = ttk.Frame(parent)
        btn_row2.pack(fill="x", padx=8, pady=4)
        self._button(btn_row2, "common.refresh", self._refresh_backups).pack(side="left")
        self._button(btn_row2, "common.browse", self._browse_backup).pack(side="left", padx=4)

        self.backup_select_var = tk.BooleanVar(value=False)
        self._checkbutton(parent, "backup.checkbox.select", self.backup_select_var).pack(anchor="w", padx=8, pady=6)

        self.backup_button = self._button(parent, "backup.btn.deploy", self._do_backup_deploy)
        self.backup_button.pack(pady=10)

    def _refresh_backups(self):
        self.backup_listbox.delete(0, "end")
        try:
            self.backups = deploy.list_available_backups()
        except Exception as e:
            logger.exception("list_available_backups() failed")
            messagebox.showerror(self.tr.t("backup.dialog.backups_title"), str(e))
            self.backups = []
        for b in self.backups:
            self.backup_listbox.insert("end", describe_backup(b))

    def _browse_backup(self):
        chosen = filedialog.askdirectory(title=self.tr.t("backup.dialog.browse_title"))
        if not chosen:
            return
        path = Path(chosen)
        if not (path / "Manifest.db").exists():
            messagebox.showerror(self.tr.t("backup.dialog.backups_title"), self.tr.t("backup.err.not_valid", path=path))
            return
        if path not in self.backups:
            self.backups.append(path)
            self.backup_listbox.insert("end", describe_backup(path))
        self.backup_listbox.selection_clear(0, "end")
        self.backup_listbox.selection_set(self.backups.index(path))

    def _do_backup_deploy(self):
        if not self.backup_paths:
            messagebox.showerror(self.tr.t("backup.dialog.title"), self.tr.t("backup.err.no_files"))
            return
        sel = self.backup_listbox.curselection()
        if not sel:
            messagebox.showerror(self.tr.t("backup.dialog.title"), self.tr.t("backup.err.no_target"))
            return
        backup_dir = self.backups[sel[0]]

        if not messagebox.askyesno(
            self.tr.t("backup.confirm.title"),
            self.tr.t("backup.confirm.message", n=len(self.backup_paths), dir=backup_dir),
        ):
            return

        select = self.backup_select_var.get()

        def on_success(_):
            messagebox.showinfo(self.tr.t("backup.dialog.title"), self.tr.t("backup.success", dir=backup_dir))

        self._run_async(
            deploy.deploy_to_dir,
            args=(backup_dir, list(self.backup_paths), select),
            buttons=(self.backup_button,),
            on_success=on_success,
        )

    # -- Tab 4: inject into a simulator ---------------------------------------

    def _build_simulator_tab(self, parent):
        self.simulator_paths = []

        self._label(parent, "simulator.label.files").pack(anchor="w", padx=8, pady=(8, 0))
        list_frame = ttk.Frame(parent)
        list_frame.pack(fill="both", expand=True, padx=8)
        self.sim_tendies_listbox = tk.Listbox(list_frame, selectmode="extended", height=6)
        self.sim_tendies_listbox.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(list_frame, command=self.sim_tendies_listbox.yview)
        scroll.pack(side="right", fill="y")
        self.sim_tendies_listbox.configure(yscrollcommand=scroll.set)

        btn_row = ttk.Frame(parent)
        btn_row.pack(fill="x", padx=8, pady=4)
        self._button(btn_row, "common.add",
                     lambda: self._add_files_dialog(self.sim_tendies_listbox, self.simulator_paths)
                     ).pack(side="left")
        self._button(btn_row, "common.remove_selection",
                     lambda: self._remove_selection(self.sim_tendies_listbox, self.simulator_paths)
                     ).pack(side="left", padx=4)
        self._button(btn_row, "common.clear",
                     lambda: self._clear_list(self.sim_tendies_listbox, self.simulator_paths)
                     ).pack(side="left")

        self._label(parent, "simulator.label.target").pack(anchor="w", padx=8, pady=(10, 0))
        sim_list_frame = ttk.Frame(parent)
        sim_list_frame.pack(fill="both", expand=True, padx=8)
        self.sim_listbox = tk.Listbox(sim_list_frame, selectmode="browse", height=5)
        self.sim_listbox.pack(side="left", fill="both", expand=True)
        scroll2 = ttk.Scrollbar(sim_list_frame, command=self.sim_listbox.yview)
        scroll2.pack(side="right", fill="y")
        self.sim_listbox.configure(yscrollcommand=scroll2.set)

        self._button(parent, "simulator.btn.refresh_list", self._refresh_simulators).pack(anchor="w", padx=8, pady=4)

        options_row = ttk.Frame(parent)
        options_row.pack(fill="x", padx=8, pady=6)
        self.sim_select_var = tk.BooleanVar(value=True)
        self._checkbutton(options_row, "simulator.checkbox.select", self.sim_select_var).pack(side="left")
        self.sim_respring_var = tk.BooleanVar(value=True)
        self._checkbutton(options_row, "simulator.checkbox.respring", self.sim_respring_var).pack(side="left", padx=10)

        self.sim_inject_button = self._button(parent, "simulator.btn.inject", self._do_sim_deploy)
        self.sim_inject_button.pack(pady=8)

        tools_frame = self._labelframe(parent, "simulator.tools.title")
        tools_frame.pack(fill="x", padx=8, pady=8)
        self._button(tools_frame, "simulator.tools.respring", self._sim_respring).pack(side="left", padx=6, pady=6)
        self._button(tools_frame, "simulator.tools.light", lambda: self._sim_appearance("light")).pack(side="left", padx=6)
        self._button(tools_frame, "simulator.tools.dark", lambda: self._sim_appearance("dark")).pack(side="left", padx=6)
        self._button(tools_frame, "simulator.tools.screenshot", self._sim_screenshot).pack(side="left", padx=6)

    def _refresh_simulators(self):
        self.sim_listbox.delete(0, "end")

        def on_success(sims):
            self.simulators = sims
            for s in sims:
                self.sim_listbox.insert("end", describe_simulator(s))

        self._run_async(sim_deploy.list_simulators, on_success=on_success)

    def _selected_simulator_udid(self):
        sel = self.sim_listbox.curselection()
        if not sel:
            messagebox.showerror(self.tr.t("simulator.dialog.title"), self.tr.t("simulator.err.no_target"))
            return None
        return self.simulators[sel[0]]["udid"]

    def _do_sim_deploy(self):
        if not self.simulator_paths:
            messagebox.showerror(self.tr.t("simulator.dialog.title"), self.tr.t("simulator.err.no_files"))
            return
        udid = self._selected_simulator_udid()
        if not udid:
            return
        select = self.sim_select_var.get()
        do_respring = self.sim_respring_var.get()

        def work():
            for tendies_path in self.simulator_paths:
                sim_deploy.inject_into_simulator(tendies_path, udid, select=select)
            if do_respring:
                sim_deploy.respring(udid)

        def on_success(_):
            msg = self.tr.t("simulator.success")
            if do_respring:
                msg += self.tr.t("simulator.success.respring_suffix")
            messagebox.showinfo(self.tr.t("simulator.dialog.title"), msg)

        self._run_async(work, buttons=(self.sim_inject_button,), on_success=on_success)

    def _sim_respring(self):
        udid = self._selected_simulator_udid()
        if not udid:
            return
        self._run_async(sim_deploy.respring, args=(udid,))

    def _sim_appearance(self, mode):
        udid = self._selected_simulator_udid()
        if not udid:
            return
        self._run_async(sim_deploy.set_appearance, args=(udid, mode))

    def _sim_screenshot(self):
        udid = self._selected_simulator_udid()
        if not udid:
            return
        path = filedialog.asksaveasfilename(
            title=self.tr.t("simulator.dialog.screenshot_save_title"),
            defaultextension=".png",
            initialfile="simulator.png",
            filetypes=[("PNG", "*.png")],
        )
        if not path:
            return

        def on_success(_):
            messagebox.showinfo(self.tr.t("simulator.dialog.screenshot_done_title"),
                                 self.tr.t("simulator.dialog.screenshot_done", path=path))

        self._run_async(sim_deploy.take_screenshot, args=(udid, Path(path)), on_success=on_success)

    # -- Tab 5: dump an installed poster -> .tendies ---------------------------

    def _build_dump_tab(self, parent):
        self.dump_backups = []
        self.dump_simulators = []
        self.dump_posters = []

        self._label(parent, "dump.label.backup").pack(anchor="w", padx=8, pady=(8, 0))
        backup_frame = ttk.Frame(parent)
        backup_frame.pack(fill="x", padx=8)
        self.dump_backup_listbox = tk.Listbox(backup_frame, selectmode="browse", height=3, exportselection=False)
        self.dump_backup_listbox.pack(side="left", fill="x", expand=True)
        scroll1 = ttk.Scrollbar(backup_frame, command=self.dump_backup_listbox.yview)
        scroll1.pack(side="right", fill="y")
        self.dump_backup_listbox.configure(yscrollcommand=scroll1.set)
        backup_btn_row = ttk.Frame(parent)
        backup_btn_row.pack(fill="x", padx=8, pady=4)
        self._button(backup_btn_row, "common.refresh", self._refresh_dump_backups).pack(side="left")
        self._button(backup_btn_row, "common.browse", self._browse_dump_backup).pack(side="left", padx=4)

        self._label(parent, "dump.label.simulator").pack(anchor="w", padx=8, pady=(8, 0))
        sim_frame = ttk.Frame(parent)
        sim_frame.pack(fill="x", padx=8)
        self.dump_sim_listbox = tk.Listbox(sim_frame, selectmode="browse", height=3, exportselection=False)
        self.dump_sim_listbox.pack(side="left", fill="x", expand=True)
        scroll2 = ttk.Scrollbar(sim_frame, command=self.dump_sim_listbox.yview)
        scroll2.pack(side="right", fill="y")
        self.dump_sim_listbox.configure(yscrollcommand=scroll2.set)
        sim_btn_row = ttk.Frame(parent)
        sim_btn_row.pack(fill="x", padx=8, pady=4)
        self._button(sim_btn_row, "common.refresh", self._refresh_dump_simulators).pack(side="left")

        self.dump_list_button = self._button(parent, "dump.btn.list_posters", self._do_list_posters)
        self.dump_list_button.pack(pady=6)

        self._label(parent, "dump.label.posters").pack(anchor="w", padx=8, pady=(4, 0))
        tree_frame = ttk.Frame(parent)
        tree_frame.pack(fill="both", expand=True, padx=8)
        columns = ("uuid", "provider", "descriptor", "role", "selected")
        self.dump_tree = ttk.Treeview(tree_frame, columns=columns, show="headings", height=6, selectmode="browse")
        widths = {"uuid": 210, "provider": 240, "descriptor": 140, "role": 140, "selected": 60}
        for col in columns:
            self.dump_tree.column(col, width=widths[col], anchor="w")
        for col, key in (("uuid", "dump.col.uuid"), ("provider", "dump.col.provider"),
                         ("descriptor", "dump.col.descriptor"), ("role", "dump.col.role"),
                         ("selected", "dump.col.selected")):
            self._register_retranslate(lambda c=col, k=key: self.dump_tree.heading(c, text=self.tr.t(k)))
        self.dump_tree.pack(side="left", fill="both", expand=True)
        scroll3 = ttk.Scrollbar(tree_frame, command=self.dump_tree.yview)
        scroll3.pack(side="right", fill="y")
        self.dump_tree.configure(yscrollcommand=scroll3.set)

        form = ttk.Frame(parent)
        form.pack(fill="x", padx=8, pady=8)
        self._label(form, "dump.label.output").grid(row=0, column=0, sticky="w")
        self.dump_output_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.dump_output_var, width=45).grid(row=0, column=1, sticky="w", padx=6)
        self._button(form, "common.choose", self._choose_dump_output).grid(row=0, column=2, padx=4)

        self.dump_button = self._button(parent, "dump.btn.dump", self._do_dump)
        self.dump_button.pack(pady=8)

    def _refresh_dump_backups(self):
        self.dump_backup_listbox.delete(0, "end")
        try:
            self.dump_backups = deploy.list_available_backups()
        except Exception as e:
            logger.exception("list_available_backups() failed")
            messagebox.showerror(self.tr.t("backup.dialog.backups_title"), str(e))
            self.dump_backups = []
        for b in self.dump_backups:
            self.dump_backup_listbox.insert("end", describe_backup(b))

    def _browse_dump_backup(self):
        chosen = filedialog.askdirectory(title=self.tr.t("backup.dialog.browse_title"))
        if not chosen:
            return
        path = Path(chosen)
        if not (path / "Manifest.db").exists():
            messagebox.showerror(self.tr.t("backup.dialog.backups_title"), self.tr.t("backup.err.not_valid", path=path))
            return
        if path not in self.dump_backups:
            self.dump_backups.append(path)
            self.dump_backup_listbox.insert("end", describe_backup(path))
        self.dump_backup_listbox.selection_clear(0, "end")
        self.dump_backup_listbox.selection_set(self.dump_backups.index(path))

    def _refresh_dump_simulators(self):
        self.dump_sim_listbox.delete(0, "end")

        def on_success(sims):
            self.dump_simulators = sims
            for s in sims:
                self.dump_sim_listbox.insert("end", describe_simulator(s))

        self._run_async(sim_deploy.list_simulators, on_success=on_success)

    def _refresh_dump_tree(self, posters):
        self.dump_posters = posters
        self.dump_tree.delete(*self.dump_tree.get_children())
        yes, no = self.tr.t("dump.yes"), self.tr.t("dump.no")
        for i, p in enumerate(posters):
            self.dump_tree.insert(
                "", "end", iid=str(i),
                values=(p["uuid"], p["provider"], p["descriptor_identifier"] or "", p["role"],
                        yes if p["selected"] else no),
            )

    def _do_list_posters(self):
        backup_sel = self.dump_backup_listbox.curselection()
        sim_sel = self.dump_sim_listbox.curselection()
        if backup_sel:
            source_fn, source_arg = dump.list_posters_in_backup, self.dump_backups[backup_sel[0]]
        elif sim_sel:
            source_fn, source_arg = dump.list_posters_in_simulator, self.dump_simulators[sim_sel[0]]["udid"]
        else:
            messagebox.showerror(self.tr.t("dump.dialog.title"), self.tr.t("dump.err.no_source"))
            return

        self._run_async(source_fn, args=(source_arg,), buttons=(self.dump_list_button,),
                         on_success=self._refresh_dump_tree)

    def _choose_dump_output(self):
        i = self.dump_tree.selection()
        default_name = "poster.tendies"
        if i:
            p = self.dump_posters[int(i[0])]
            suggestion = p["descriptor_identifier"] or p["uuid"]
            default_name = f"{suggestion.replace('.', '_')}.tendies"
        path = filedialog.asksaveasfilename(
            title=self.tr.t("dump.dialog.save_title"),
            defaultextension=".tendies",
            initialfile=default_name,
            filetypes=[(self.tr.t("common.filetype_tendies"), "*.tendies")],
        )
        if path:
            self.dump_output_var.set(path)

    def _do_dump(self):
        sel = self.dump_tree.selection()
        if not sel:
            messagebox.showerror(self.tr.t("dump.dialog.title"), self.tr.t("dump.err.no_selection"))
            return
        poster = self.dump_posters[int(sel[0])]
        output = self.dump_output_var.get().strip()
        if not output:
            messagebox.showerror(self.tr.t("dump.dialog.title"), self.tr.t("dump.err.no_output"))
            return

        backup_sel = self.dump_backup_listbox.curselection()
        sim_sel = self.dump_sim_listbox.curselection()
        if backup_sel:
            fn = dump.dump_from_backup
            args = (self.dump_backups[backup_sel[0]], poster["provider"], poster["uuid"], Path(output))
        elif sim_sel:
            fn = dump.dump_from_simulator
            args = (self.dump_simulators[sim_sel[0]]["udid"], poster["provider"], poster["uuid"], Path(output))
        else:
            messagebox.showerror(self.tr.t("dump.dialog.title"), self.tr.t("dump.err.no_source"))
            return

        def on_success(_):
            messagebox.showinfo(self.tr.t("dump.dialog.title"), self.tr.t("dump.success", output=output))

        self._run_async(fn, args=args, buttons=(self.dump_button,), on_success=on_success)


def main():
    log_path = setup_logging("gui", sys.argv)
    app = TendiesGUI(log_path=log_path)
    app.mainloop()


if __name__ == "__main__":
    main()
