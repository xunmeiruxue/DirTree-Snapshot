#!/usr/bin/env python3
"""DirTree Snapshot GUI — tkinter interface for snapshot operations.

A graphical front-end for the DirTree Snapshot CLI tools.  All scanning,
comparison, verification, and cache operations reuse the existing CLI
functions; the GUI only adds interaction, threading, and output display.
"""

from __future__ import annotations

import os
import queue
import sys
import threading
import traceback
import webbrowser
import tkinter as tk
from tkinter import (
    BOTH, END, LEFT, RIGHT, X, Y, BOTTOM, NORMAL, DISABLED,
    StringVar, BooleanVar,
    filedialog, messagebox, scrolledtext,
)
from tkinter import ttk

# -- version sync with dirtree.py -------------------------------------------
__version__ = "0.10.0"

# -- core imports (deferred to allow --help before dependency errors) --------
_dirtree = None
_dirtree_compare = None
_dirtree_verify = None
_dirtree_cache = None


def _import_core():
    global _dirtree, _dirtree_compare, _dirtree_verify, _dirtree_cache
    if _dirtree is not None:
        return
    import dirtree
    import dirtree_compare
    import dirtree_verify
    import dirtree_cache
    _dirtree = dirtree
    _dirtree_compare = dirtree_compare
    _dirtree_verify = dirtree_verify
    _dirtree_cache = dirtree_cache


class StdoutCapture:
    """Capture stdout/stderr from CLI functions into a queue."""

    def __init__(self, msg_queue: queue.Queue):
        self._queue = msg_queue

    def write(self, text: str):
        if text.strip():
            self._queue.put(("output", text))

    def flush(self):
        pass


class WorkerThread(threading.Thread):
    """Run a CLI function in a background thread."""

    def __init__(self, func, args, msg_queue: queue.Queue):
        super().__init__(daemon=True)
        self._func = func
        self._args = args
        self._queue = msg_queue

    def run(self):
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        capture = StdoutCapture(self._queue)
        sys.stdout = capture
        sys.stderr = capture
        try:
            exit_code = self._func(self._args)
            self._queue.put(("done", exit_code))
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
            self._queue.put(("done", code))
        except Exception as exc:
            self._queue.put(("error", str(exc)))
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr


class DirTreeGUI:
    PADDING = 8
    LABEL_WIDTH = 12

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("DirTree Snapshot")
        self.root.geometry("780x640")
        self.root.minsize(680, 560)

        self._msg_queue: queue.Queue = queue.Queue()
        self._worker: WorkerThread | None = None
        self._output_path: str | None = None

        self._build_styles()
        self._build_ui()

    # -- styles -------------------------------------------------------------
    def _build_styles(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass

    # -- UI construction ----------------------------------------------------
    def _build_ui(self):
        self._build_header()
        self._build_notebook()
        self._build_log_area()
        self._build_status_bar()
        self.root.after(100, self._poll_queue)

    def _build_header(self):
        header = ttk.Frame(self.root)
        header.pack(fill=X, padx=self.PADDING, pady=(self.PADDING, 4))
        ttk.Label(
            header,
            text="DirTree Snapshot",
            font=("", 16, "bold"),
        ).pack(side=LEFT)
        ttk.Label(
            header,
            text=f"  v{__version__}",
            font=("", 10),
            foreground="gray",
        ).pack(side=LEFT, padx=(0, 0), pady=(4, 0))

    def _build_notebook(self):
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=BOTH, expand=True, padx=self.PADDING, pady=4)
        self._build_snapshot_tab()
        self._build_compare_tab()
        self._build_verify_tab()
        self._build_cache_tab()

    # -- snapshot tab -------------------------------------------------------
    def _build_snapshot_tab(self):
        frame = ttk.Frame(self.notebook, padding=self.PADDING)
        self.notebook.add(frame, text=" Scan ")

        self.snap_dir = StringVar()
        self.snap_output = StringVar()
        self.snap_format = StringVar(value="html")
        self.snap_hash = BooleanVar()
        self.snap_metadata = BooleanVar()
        self.snap_fast = BooleanVar()
        self.snap_resume = BooleanVar()
        self.snap_no_cache = BooleanVar()
        self.snap_exclude = StringVar()
        self.snap_include = StringVar()

        self._add_dir_entry(frame, "Directory:", self.snap_dir,
                            self._browse_snapshot_dir, 0)
        self._add_dir_entry(frame, "Output:", self.snap_output,
                            self._browse_snapshot_output, 1, required=False)

        fmt_frame = ttk.Frame(frame)
        fmt_frame.grid(row=2, column=0, columnspan=3, sticky="ew", pady=2)
        ttk.Label(fmt_frame, text="Format:", width=self.LABEL_WIDTH).pack(side=LEFT)
        for fmt, label in [("html", "HTML"), ("text", "Text"), ("json", "JSON")]:
            ttk.Radiobutton(fmt_frame, text=label, variable=self.snap_format,
                            value=fmt).pack(side=LEFT, padx=(0, 16))

        opts_frame = ttk.Frame(frame)
        opts_frame.grid(row=3, column=0, columnspan=3, sticky="ew", pady=2)
        ttk.Label(opts_frame, text="Options:", width=self.LABEL_WIDTH).pack(side=LEFT)
        for var, label in [
            (self.snap_hash, "SHA-256"),
            (self.snap_metadata, "Metadata"),
            (self.snap_fast, "Fast scan"),
            (self.snap_resume, "Resume"),
            (self.snap_no_cache, "No cache"),
        ]:
            ttk.Checkbutton(opts_frame, text=label, variable=var).pack(side=LEFT, padx=(0, 12))

        self._add_text_entry(frame, "Exclude:", self.snap_exclude,
                             ".git, node_modules, *.tmp", 4)
        self._add_text_entry(frame, "Include:", self.snap_include,
                             "*.py, *.json", 5)

        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(12, 0))
        self.snap_run_btn = ttk.Button(btn_frame, text="Start Scan",
                                      command=self._run_snapshot)
        self.snap_run_btn.pack(side=LEFT)
        self.snap_open_btn = ttk.Button(btn_frame, text="Open Report",
                                       command=self._open_snapshot_report,
                                       state=DISABLED)
        self.snap_open_btn.pack(side=LEFT, padx=8)

    # -- compare tab --------------------------------------------------------
    def _build_compare_tab(self):
        frame = ttk.Frame(self.notebook, padding=self.PADDING)
        self.notebook.add(frame, text=" Compare ")

        self.cmp_left = StringVar()
        self.cmp_right = StringVar()
        self.cmp_output = StringVar()
        self.cmp_format = StringVar(value="html")
        self.cmp_unchanged = BooleanVar()
        self.cmp_case = BooleanVar()

        self._add_dir_entry(frame, "Left:", self.cmp_left,
                            lambda: self._browse_file(self.cmp_left), 0)
        self._add_dir_entry(frame, "Right:", self.cmp_right,
                            lambda: self._browse_file(self.cmp_right), 1)
        self._add_dir_entry(frame, "Output:", self.cmp_output,
                            self._browse_compare_output, 2, required=False)

        fmt_frame = ttk.Frame(frame)
        fmt_frame.grid(row=3, column=0, columnspan=3, sticky="ew", pady=2)
        ttk.Label(fmt_frame, text="Format:", width=self.LABEL_WIDTH).pack(side=LEFT)
        for fmt, label in [("html", "HTML"), ("text", "Text"), ("json", "JSON")]:
            ttk.Radiobutton(fmt_frame, text=label, variable=self.cmp_format,
                            value=fmt).pack(side=LEFT, padx=(0, 16))

        opts_frame = ttk.Frame(frame)
        opts_frame.grid(row=4, column=0, columnspan=3, sticky="ew", pady=2)
        ttk.Label(opts_frame, text="Options:", width=self.LABEL_WIDTH).pack(side=LEFT)
        ttk.Checkbutton(opts_frame, text="Include unchanged",
                       variable=self.cmp_unchanged).pack(side=LEFT, padx=(0, 12))
        ttk.Checkbutton(opts_frame, text="Case-sensitive",
                       variable=self.cmp_case).pack(side=LEFT)

        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(12, 0))
        self.cmp_run_btn = ttk.Button(btn_frame, text="Start Compare",
                                     command=self._run_compare)
        self.cmp_run_btn.pack(side=LEFT)
        self.cmp_open_btn = ttk.Button(btn_frame, text="Open Report",
                                      command=self._open_compare_report,
                                      state=DISABLED)
        self.cmp_open_btn.pack(side=LEFT, padx=8)

    # -- verify tab ---------------------------------------------------------
    def _build_verify_tab(self):
        frame = ttk.Frame(self.notebook, padding=self.PADDING)
        self.notebook.add(frame, text=" Verify ")

        self.ver_snapshot = StringVar()
        self.ver_dir = StringVar()
        self.ver_output = StringVar()
        self.ver_hash_mode = StringVar(value="auto")
        self.ver_no_cache = BooleanVar()
        self.ver_unchanged = BooleanVar()
        self.ver_case = BooleanVar()

        self._add_dir_entry(frame, "Snapshot:", self.ver_snapshot,
                            lambda: self._browse_file(self.ver_snapshot), 0)
        self._add_dir_entry(frame, "Directory:", self.ver_dir,
                            lambda: self._browse_directory(self.ver_dir), 1)
        self._add_dir_entry(frame, "Output:", self.ver_output,
                            self._browse_verify_output, 2, required=False)

        mode_frame = ttk.Frame(frame)
        mode_frame.grid(row=3, column=0, columnspan=3, sticky="ew", pady=2)
        ttk.Label(mode_frame, text="Hash mode:", width=self.LABEL_WIDTH).pack(side=LEFT)
        for mode, label in [("auto", "Auto"), ("hash", "Force hash"), ("no-hash", "No hash")]:
            ttk.Radiobutton(mode_frame, text=label, variable=self.ver_hash_mode,
                            value=mode).pack(side=LEFT, padx=(0, 16))

        opts_frame = ttk.Frame(frame)
        opts_frame.grid(row=4, column=0, columnspan=3, sticky="ew", pady=2)
        ttk.Label(opts_frame, text="Options:", width=self.LABEL_WIDTH).pack(side=LEFT)
        ttk.Checkbutton(opts_frame, text="No cache",
                       variable=self.ver_no_cache).pack(side=LEFT, padx=(0, 12))
        ttk.Checkbutton(opts_frame, text="Include unchanged",
                       variable=self.ver_unchanged).pack(side=LEFT, padx=(0, 12))
        ttk.Checkbutton(opts_frame, text="Case-sensitive",
                       variable=self.ver_case).pack(side=LEFT)

        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(12, 0))
        self.ver_run_btn = ttk.Button(btn_frame, text="Start Verify",
                                     command=self._run_verify)
        self.ver_run_btn.pack(side=LEFT)
        self.ver_open_btn = ttk.Button(btn_frame, text="Open Report",
                                      command=self._open_verify_report,
                                      state=DISABLED)
        self.ver_open_btn.pack(side=LEFT, padx=8)

    # -- cache tab ----------------------------------------------------------
    def _build_cache_tab(self):
        frame = ttk.Frame(self.notebook, padding=self.PADDING)
        self.notebook.add(frame, text=" Cache ")

        info_frame = ttk.LabelFrame(frame, text="Cache Info", padding=self.PADDING)
        info_frame.pack(fill=X, pady=(0, 8))
        self.cache_info_text = scrolledtext.ScrolledText(
            info_frame, height=8, wrap="word", state=DISABLED,
            font=("Consolas", 10))
        self.cache_info_text.pack(fill=X)

        self.cache_days = StringVar(value="30")

        action_frame = ttk.Frame(frame)
        action_frame.pack(fill=X, pady=4)
        ttk.Button(action_frame, text="Refresh Info",
                  command=lambda: self._run_cache(["info"])).pack(side=LEFT)
        ttk.Button(action_frame, text="Clear All",
                  command=lambda: self._run_cache_clear()).pack(side=LEFT, padx=8)
        ttk.Label(action_frame, text="Prune days:").pack(side=LEFT, padx=(16, 4))
        ttk.Entry(action_frame, textvariable=self.cache_days, width=8).pack(side=LEFT)
        ttk.Button(action_frame, text="Prune",
                  command=self._run_cache_prune).pack(side=LEFT, padx=8)

    # -- log area -----------------------------------------------------------
    def _build_log_area(self):
        log_frame = ttk.LabelFrame(self.root, text="Log", padding=4)
        log_frame.pack(fill=BOTH, expand=True, padx=self.PADDING, pady=4)
        self.log_text = scrolledtext.ScrolledText(
            log_frame, height=10, wrap="word", state=DISABLED,
            font=("Consolas", 10))
        self.log_text.pack(fill=BOTH, expand=True)

    # -- status bar ---------------------------------------------------------
    def _build_status_bar(self):
        self.status_var = StringVar(value="Ready")
        status_bar = ttk.Label(self.root, textvariable=self.status_var,
                               relief="sunken", padding=4)
        status_bar.pack(fill=X, side=BOTTOM)

    # -- helper widgets -----------------------------------------------------
    def _add_dir_entry(self, parent, label, var, browse_cmd, row, required=True):
        ttk.Label(parent, text=label, width=self.LABEL_WIDTH).grid(
            row=row, column=0, sticky="w", pady=2)
        entry = ttk.Entry(parent, textvariable=var)
        entry.grid(row=row, column=1, sticky="ew", pady=2, padx=(4, 4))
        ttk.Button(parent, text="...", width=3,
                   command=browse_cmd).grid(row=row, column=2, pady=2)
        parent.columnconfigure(1, weight=1)

    def _add_text_entry(self, parent, label, var, placeholder, row):
        ttk.Label(parent, text=label, width=self.LABEL_WIDTH).grid(
            row=row, column=0, sticky="w", pady=2)
        entry = ttk.Entry(parent, textvariable=var)
        entry.grid(row=row, column=1, columnspan=2, sticky="ew", pady=2, padx=(4, 0))
        parent.columnconfigure(1, weight=1)

    # -- browse helpers -----------------------------------------------------
    def _browse_directory(self, var: StringVar):
        path = filedialog.askdirectory(title="Select directory")
        if path:
            var.set(path)

    def _browse_file(self, var: StringVar):
        path = filedialog.askopenfilename(
            title="Select file",
            filetypes=[
                ("Snapshot files", "*.html *.txt *.json"),
                ("All files", "*.*"),
            ])
        if path:
            var.set(path)

    def _browse_snapshot_dir(self):
        self._browse_directory(self.snap_dir)

    def _browse_snapshot_output(self):
        path = self._save_as(defaultext=".html")
        if path:
            self.snap_output.set(path)

    def _browse_compare_output(self):
        path = self._save_as(defaultext=".html")
        if path:
            self.cmp_output.set(path)

    def _browse_verify_output(self):
        path = self._save_as(defaultext=".html")
        if path:
            self.ver_output.set(path)

    def _save_as(self, defaultext: str = ".html") -> str | None:
        return filedialog.asksaveasfilename(
            title="Save as",
            defaultextension=defaultext,
            filetypes=[
                ("HTML", "*.html"),
                ("Text", "*.txt"),
                ("JSON", "*.json"),
                ("All files", "*.*"),
            ])

    # -- run helpers --------------------------------------------------------
    def _set_running(self, running: bool):
        state = DISABLED if running else NORMAL
        for btn in [self.snap_run_btn, self.cmp_run_btn, self.ver_run_btn]:
            btn.config(state=state)
        if not running:
            self.status_var.set("Ready")
        else:
            self.status_var.set("Working...")

    def _parse_patterns(self, text: str) -> list[str]:
        result: list[str] = []
        for part in text.replace("\n", ",").split(","):
            part = part.strip()
            if part:
                result.append(part)
        return result

    # -- snapshot action ----------------------------------------------------
    def _run_snapshot(self):
        directory = self.snap_dir.get().strip().strip('"')
        if not directory:
            messagebox.showwarning("Missing input", "Please select a directory to scan.")
            return
        if not os.path.isdir(directory):
            messagebox.showerror("Invalid path", f"Not a valid directory:\n{directory}")
            return

        args: list[str] = [directory]
        output = self.snap_output.get().strip().strip('"')
        if output:
            args.extend(["-o", output])
        fmt = self.snap_format.get()
        if fmt:
            args.extend(["--format", fmt])
        if self.snap_hash.get():
            args.append("--hash")
        if self.snap_metadata.get():
            args.append("--metadata")
        if self.snap_fast.get():
            args.append("--fast")
        if self.snap_resume.get():
            args.append("--resume")
        if self.snap_no_cache.get():
            args.append("--no-cache")
        for p in self._parse_patterns(self.snap_exclude.get()):
            args.extend(["--exclude", p])
        for p in self._parse_patterns(self.snap_include.get()):
            args.extend(["--include", p])

        self._output_path = output if output else None
        self._start_worker(self._dirtree._run_snapshot, args, "Snapshot")

    # -- compare action -----------------------------------------------------
    def _run_compare(self):
        left = self.cmp_left.get().strip().strip('"')
        right = self.cmp_right.get().strip().strip('"')
        if not left or not right:
            messagebox.showwarning("Missing input", "Both snapshot files are required.")
            return

        args = [left, right]
        output = self.cmp_output.get().strip().strip('"')
        if output:
            args.extend(["-o", output])
        fmt = self.cmp_format.get()
        if fmt:
            args.extend(["--format", fmt])
        if self.cmp_unchanged.get():
            args.append("--include-unchanged")
        if self.cmp_case.get():
            args.append("--case-sensitive")

        self._output_path = output if output else None
        self._start_worker(self._dirtree_compare.run_compare, args, "Compare")

    # -- verify action ------------------------------------------------------
    def _run_verify(self):
        snapshot = self.ver_snapshot.get().strip().strip('"')
        directory = self.ver_dir.get().strip().strip('"')
        if not snapshot or not directory:
            messagebox.showwarning("Missing input", "Snapshot file and directory are required.")
            return
        if not os.path.isdir(directory):
            messagebox.showerror("Invalid path", f"Not a valid directory:\n{directory}")
            return

        args = [snapshot, directory]
        output = self.ver_output.get().strip().strip('"')
        if output:
            args.extend(["-o", output])
        mode = self.ver_hash_mode.get()
        if mode == "hash":
            args.append("--hash")
        elif mode == "no-hash":
            args.append("--no-hash")
        if self.ver_no_cache.get():
            args.append("--no-cache")
        if self.ver_unchanged.get():
            args.append("--include-unchanged")
        if self.ver_case.get():
            args.append("--case-sensitive")

        self._output_path = output if output else None
        self._start_worker(self._dirtree_verify.run_verify, args, "Verify")

    # -- cache actions ------------------------------------------------------
    def _run_cache(self, sub_args: list[str]):
        self._start_worker(self._dirtree_cache.run_cache, sub_args, "Cache")

    def _run_cache_clear(self):
        if not messagebox.askyesno("Confirm", "Clear all hash cache entries?"):
            return
        self._start_worker(self._dirtree_cache.run_cache, ["clear"], "Cache")

    def _run_cache_prune(self):
        days_str = self.cache_days.get().strip()
        try:
            days = int(days_str)
        except ValueError:
            messagebox.showwarning("Invalid input", "Prune days must be a number.")
            return
        self._start_worker(self._dirtree_cache.run_cache, ["prune", "--days", str(days)], "Cache")

    # -- open report actions ------------------------------------------------
    def _open_report(self, path: str | None):
        if not path or not os.path.isfile(path):
            messagebox.showinfo("No report", "No report file was generated or the path is unknown.\n"
                               "Check the log for the actual output path.")
            return
        webbrowser.open(f"file:///{path.replace(os.sep, '/')}")
        self._log(f"Opened: {path}")

    def _open_snapshot_report(self):
        self._open_report(self._output_path)

    def _open_compare_report(self):
        self._open_report(self._output_path)

    def _open_verify_report(self):
        self._open_report(self._output_path)

    # -- worker management --------------------------------------------------
    def _start_worker(self, func, args: list[str], label: str):
        if self._worker is not None and self._worker.is_alive():
            messagebox.showwarning("Busy", "A task is already running. Wait for it to finish.")
            return
        self._import_core()
        self._set_running(True)
        self.snap_open_btn.config(state=DISABLED)
        self.cmp_open_btn.config(state=DISABLED)
        self.ver_open_btn.config(state=DISABLED)
        self._log(f"--- {label} started ---")
        self._worker = WorkerThread(func, args, self._msg_queue)
        self._worker.start()

    def _poll_queue(self):
        try:
            while True:
                kind, data = self._msg_queue.get_nowait()
                if kind == "output":
                    self._log(data.rstrip())
                elif kind == "done":
                    self._on_worker_done(data)
                elif kind == "error":
                    self._log(f"[ERROR] {data}")
                    self._on_worker_done(1)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)

    def _on_worker_done(self, exit_code: int):
        self._worker = None
        self._set_running(False)
        self._log(f"--- completed (exit code {exit_code}) ---\n")
        if exit_code == 0:
            self.status_var.set("Completed successfully")
            if self._output_path:
                self.snap_open_btn.config(state=NORMAL)
                self.cmp_open_btn.config(state=NORMAL)
                self.ver_open_btn.config(state=NORMAL)
        elif exit_code == 1:
            self.status_var.set("Completed with warnings/differences")
        else:
            self.status_var.set(f"Failed (exit code {exit_code})")

    # -- logging ------------------------------------------------------------
    def _log(self, text: str):
        self.log_text.config(state=NORMAL)
        self.log_text.insert(END, text + "\n")
        self.log_text.see(END)
        self.log_text.config(state=DISABLED)


def main():
    try:
        _import_core()
        app = DirTreeGUI()
        app.root.mainloop()
    except Exception:
        detail = traceback.format_exc()
        try:
            sys.stderr.write(detail)
        except Exception:
            pass
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("DirTree Snapshot — startup error", detail)
        root.destroy()
        raise SystemExit(1)


if __name__ == "__main__":
    main()
