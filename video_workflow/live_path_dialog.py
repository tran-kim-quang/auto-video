from __future__ import annotations

import fnmatch
import os
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import ttk


class LivePathDialog:
    """A small file/folder chooser that keeps its directory listing fresh."""

    REFRESH_MS = 750

    def __init__(
        self,
        parent: tk.Misc,
        *,
        title: str,
        initial_directory: Path,
        filetypes: list[tuple[str, str]] | None = None,
        select_directory: bool = False,
    ) -> None:
        self.parent = parent
        self.select_directory = select_directory
        self.filetypes = filetypes or [("All files", "*")]
        self.result: str | None = None
        self.current_directory = self._existing_directory(initial_directory)
        self._refresh_id: str | None = None

        self.window = tk.Toplevel(parent)
        self.window.title(title)
        self.window.geometry("900x600")
        self.window.minsize(680, 420)
        self.window.transient(parent)
        self.window.protocol("WM_DELETE_WINDOW", self._cancel)

        self.directory_var = tk.StringVar(value=str(self.current_directory))
        self.filter_var = tk.StringVar(value=self.filetypes[0][0])
        self.message_var = tk.StringVar()
        self._build_layout()
        self._refresh_listing()

    @staticmethod
    def _existing_directory(path: Path) -> Path:
        candidate = Path(path).expanduser()
        if candidate.is_file():
            candidate = candidate.parent
        while not candidate.is_dir() and candidate != candidate.parent:
            candidate = candidate.parent
        if candidate.is_dir():
            return candidate.resolve()
        return Path.cwd().resolve()

    def _build_layout(self) -> None:
        outer = ttk.Frame(self.window, padding=10)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(1, weight=1)

        ttk.Button(outer, text="Up", command=self._go_up).grid(
            row=0, column=0, padx=(0, 6), sticky="w"
        )
        path_entry = ttk.Entry(outer, textvariable=self.directory_var)
        path_entry.grid(row=0, column=1, sticky="ew")
        path_entry.bind("<Return>", lambda _event: self._go_to_path())
        ttk.Button(outer, text="Go", command=self._go_to_path).grid(
            row=0, column=2, padx=(6, 0)
        )

        columns = ("name", "kind", "size", "modified")
        self.tree = ttk.Treeview(
            outer, columns=columns, show="headings", selectmode="browse"
        )
        for column, heading, width in (
            ("name", "Name", 430),
            ("kind", "Type", 90),
            ("size", "Size", 100),
            ("modified", "Modified", 160),
        ):
            self.tree.heading(column, text=heading)
            self.tree.column(column, width=width, minwidth=70)
        self.tree.grid(row=1, column=0, columnspan=3, sticky="nsew", pady=(8, 0))
        self.tree.bind("<Double-1>", self._activate_selection)
        self.tree.bind("<Return>", self._activate_selection)

        scroll = ttk.Scrollbar(outer, orient="vertical", command=self.tree.yview)
        scroll.grid(row=1, column=3, sticky="ns", pady=(8, 0))
        self.tree.configure(yscrollcommand=scroll.set)

        controls = ttk.Frame(outer)
        controls.grid(row=2, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        controls.columnconfigure(1, weight=1)
        ttk.Label(controls, text="Filter").grid(row=0, column=0, padx=(0, 6))
        filter_box = ttk.Combobox(
            controls,
            textvariable=self.filter_var,
            values=[label for label, _patterns in self.filetypes],
            state="readonly",
            width=24,
        )
        filter_box.grid(row=0, column=1, sticky="w")
        filter_box.bind("<<ComboboxSelected>>", lambda _event: self._refresh_listing())
        ttk.Label(controls, textvariable=self.message_var).grid(
            row=0, column=1, sticky="e", padx=8
        )
        ttk.Button(controls, text="Cancel", command=self._cancel).grid(
            row=0, column=2, padx=(0, 6)
        )
        action = "Select folder" if self.select_directory else "Open"
        ttk.Button(controls, text=action, command=self._accept).grid(row=0, column=3)

    def _selected_patterns(self) -> tuple[str, ...]:
        selected = self.filter_var.get()
        pattern_text = next(
            (patterns for label, patterns in self.filetypes if label == selected), "*"
        )
        return tuple(
            "*" if pattern in {"*", "*.*"} else pattern.lower()
            for pattern in pattern_text.split()
        )

    def _matches_filter(self, path: Path) -> bool:
        if path.is_dir() or self.select_directory:
            return path.is_dir()
        name = path.name.lower()
        return any(
            fnmatch.fnmatch(name, pattern) for pattern in self._selected_patterns()
        )

    def _scan(self) -> list[tuple[Path, os.stat_result]]:
        entries: list[tuple[Path, os.stat_result]] = []
        try:
            for entry in self.current_directory.iterdir():
                if self._matches_filter(entry):
                    try:
                        entries.append((entry, entry.stat()))
                    except OSError:
                        continue
        except OSError as exc:
            self.message_var.set(str(exc))
            return []
        self.message_var.set(
            f"{len(entries)} folder(s)"
            if self.select_directory
            else f"{len(entries)} item(s)"
        )
        return sorted(
            entries, key=lambda item: (not item[0].is_dir(), item[0].name.lower())
        )

    def _refresh_listing(self) -> None:
        if not self.window.winfo_exists():
            return
        if self._refresh_id is not None:
            try:
                self.window.after_cancel(self._refresh_id)
            except tk.TclError:
                pass
            self._refresh_id = None
        selected = self.tree.selection()
        selected_path = selected[0] if selected else None
        entries = self._scan()
        present: set[str] = set()
        for path, stat in entries:
            item_id = str(path.resolve())
            present.add(item_id)
            is_directory = path.is_dir()
            values = (
                path.name,
                "Folder" if is_directory else (path.suffix[1:].upper() or "File"),
                "" if is_directory else self._format_size(stat.st_size),
                datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            )
            if self.tree.exists(item_id):
                self.tree.item(item_id, values=values)
            else:
                self.tree.insert("", "end", iid=item_id, values=values)
        for item_id in self.tree.get_children():
            if item_id not in present:
                self.tree.delete(item_id)
        ordered = [str(path.resolve()) for path, _stat in entries]
        for position, item_id in enumerate(ordered):
            self.tree.move(item_id, "", position)
        if selected_path and self.tree.exists(selected_path):
            self.tree.selection_set(selected_path)
            self.tree.focus(selected_path)
        self._schedule_refresh()

    @staticmethod
    def _format_size(size: int) -> str:
        value = float(size)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if value < 1024 or unit == "TB":
                return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
            value /= 1024
        return f"{size} B"

    def _schedule_refresh(self) -> None:
        self._refresh_id = self.window.after(self.REFRESH_MS, self._refresh_listing)

    def _navigate(self, path: Path) -> None:
        if not path.is_dir():
            self.message_var.set(f"Folder does not exist: {path}")
            return
        self.current_directory = path.resolve()
        self.directory_var.set(str(self.current_directory))
        self.tree.selection_remove(*self.tree.selection())
        self._refresh_listing()

    def _go_up(self) -> None:
        self._navigate(self.current_directory.parent)

    def _go_to_path(self) -> None:
        self._navigate(Path(self.directory_var.get()).expanduser())

    def _selected_path(self) -> Path | None:
        selected = self.tree.selection()
        return Path(selected[0]) if selected else None

    def _activate_selection(self, _event=None) -> None:
        selected = self._selected_path()
        if selected is None:
            return
        if selected.is_dir():
            self._navigate(selected)
        elif not self.select_directory:
            self.result = str(selected)
            self._close()

    def _accept(self) -> None:
        if self.select_directory:
            selected = self._selected_path()
            self.result = str(
                selected if selected and selected.is_dir() else self.current_directory
            )
            self._close()
            return
        selected = self._selected_path()
        if selected is None or not selected.is_file():
            self.message_var.set("Select a file first")
            return
        self.result = str(selected)
        self._close()

    def _cancel(self) -> None:
        self.result = None
        self._close()

    def _close(self) -> None:
        if self._refresh_id is not None:
            self.window.after_cancel(self._refresh_id)
            self._refresh_id = None
        self.window.destroy()

    def show(self) -> str | None:
        self.window.grab_set()
        self.tree.focus_set()
        self.window.wait_window()
        return self.result
