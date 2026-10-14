from __future__ import annotations

import os
import queue
import subprocess
import sys
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import messagebox, ttk

from .folder_batch import queue_folder_jobs
from .job_models import JobRecord, JobStatus
from .json_store import JsonStore
from .live_path_dialog import LivePathDialog
from .queue_controller import QueueController, QueueStateError
from .worker import QueueWorker


FIXED_PROFILE_LABEL = "Teacher_male — 599fc048 (32 steps, GPU)"
PROFILE_ENTRY_STATE = "readonly"
BATCH_FOLDER_GUIDE = """Quy ước đầu vào (quét đệ quy):
• Mỗi bài gồm hai file cùng tên: lesson.pptx + lesson.txt.
• TXT dùng marker Slide N:; phần trước Slide 2 là nội dung slide 1.
• Logo và outro phía trên được áp dụng cho mọi bài.
• Output: <thư mục bài>/output/lesson/lesson.mp4 cùng audio, timeline và report.
• Chỉ một bài chạy tại một thời điểm; job mới có thể tái sử dụng WAV hợp lệ."""


def application_data_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / ".workflow_data"
    return Path(__file__).resolve().parents[1] / ".workflow_data"


def _open_directory(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(path)
    elif sys.platform.startswith("linux"):
        subprocess.Popen(["xdg-open", str(path)])
    else:
        raise OSError(f"opening folders is unsupported on {sys.platform}")


def job_row(job: JobRecord) -> tuple[str, ...]:
    progress = (
        f"{job.progress_current}/{job.progress_total}"
        if job.progress_total
        else ""
    )
    result = job.error or (
        str(job.output_path) if job.status is JobStatus.COMPLETED else ""
    )
    return (
        job.pptx.stem,
        str(job.pptx),
        str(job.script),
        job.status.value,
        job.stage.value if job.stage is not None else "",
        progress,
        str(job.artifact_directory),
        result,
    )


@dataclass(slots=True)
class _PathBinding:
    variable: tk.StringVar
    kind: str
    indicator: ttk.Label


class WorkflowApp:
    def __init__(
        self, root: tk.Tk, controller: QueueController, worker: QueueWorker
    ) -> None:
        self.root = root
        self.controller = controller
        self.worker = worker
        self._closing = False
        self._path_bindings: list[_PathBinding] = []
        self._last_directory = Path.cwd()
        root.title("Pyramid Batch TTS Slide Video")
        root.geometry("1180x760")
        root.minsize(920, 620)

        settings = controller.settings
        self.logo_var = tk.StringVar(value=str(settings.logo or ""))
        self.outro_var = tk.StringVar(value=str(settings.outro or ""))
        self.batch_root_var = tk.StringVar()
        self.profile_var = tk.StringVar(value=FIXED_PROFILE_LABEL)
        self.status_var = tk.StringVar(value="Ready")

        self._build_layout()
        self.refresh_jobs()
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(200, self._poll_worker_events)
        root.after(1000, self._poll_filesystem)

    def _path_row(
        self,
        parent,
        row: int,
        label: str,
        variable: tk.StringVar,
        command,
        *,
        kind: str = "file",
    ) -> None:
        ttk.Label(parent, text=label).grid(
            row=row, column=0, sticky="w", padx=(0, 8), pady=3
        )
        ttk.Entry(parent, textvariable=variable).grid(
            row=row, column=1, sticky="ew", pady=3
        )
        indicator = ttk.Label(parent, width=12, anchor="e")
        indicator.grid(row=row, column=2, padx=(8, 0), pady=3, sticky="e")
        self._path_bindings.append(_PathBinding(variable, kind, indicator))
        ttk.Button(parent, text="Browse", command=command).grid(
            row=row, column=3, padx=(8, 0), pady=3
        )

    def _build_layout(self) -> None:
        style = ttk.Style(self.root)
        style.configure("Path.Available.TLabel", foreground="#188038")
        style.configure("Path.Missing.TLabel", foreground="#c5221f")
        style.configure("Path.Empty.TLabel", foreground="#6b7280")

        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill="both", expand=True)

        assets = ttk.LabelFrame(outer, text="Global assets (required)", padding=10)
        assets.pack(fill="x")
        assets.columnconfigure(1, weight=1)
        self._path_row(assets, 0, "Logo", self.logo_var, self.choose_logo)
        self._path_row(assets, 1, "Outro", self.outro_var, self.choose_outro)
        ttk.Label(assets, text="Fixed voice profile").grid(
            row=2, column=0, sticky="w", pady=3
        )
        ttk.Entry(
            assets,
            textvariable=self.profile_var,
            state=PROFILE_ENTRY_STATE,
        ).grid(row=2, column=1, sticky="ew", pady=3)
        ttk.Button(assets, text="Save assets", command=self.save_global_assets).grid(
            row=3, column=3, sticky="e", pady=(6, 0)
        )

        batch = ttk.LabelFrame(outer, text="Batch Folder", padding=10)
        batch.pack(fill="x", pady=10)
        batch.columnconfigure(1, weight=1)
        self._path_row(
            batch,
            0,
            "Input root",
            self.batch_root_var,
            self.choose_batch_root,
            kind="directory",
        )
        ttk.Label(
            batch,
            text=BATCH_FOLDER_GUIDE,
            justify="left",
            anchor="w",
            font="TkFixedFont",
        ).grid(row=1, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        self.batch_button = ttk.Button(
            batch, text="Scan & Add All", command=self.submit_batch_folder
        )
        self.batch_button.grid(row=2, column=3, sticky="e", pady=(8, 0))

        queue_box = ttk.LabelFrame(outer, text="Queue", padding=8)
        queue_box.pack(fill="both", expand=True)
        columns = (
            "lesson",
            "pptx",
            "script",
            "status",
            "stage",
            "progress",
            "artifact",
            "result",
        )
        headings = {
            "lesson": "Lesson",
            "pptx": "PPTX",
            "script": "TXT",
            "status": "Status",
            "stage": "Stage",
            "progress": "Slide",
            "artifact": "Artifact folder",
            "result": "Result / error",
        }
        self.tree = ttk.Treeview(
            queue_box, columns=columns, show="headings", selectmode="browse"
        )
        for name in columns:
            self.tree.heading(name, text=headings[name])
            self.tree.column(name, width=100 if name != "result" else 260, minwidth=70)
        scroll_y = ttk.Scrollbar(queue_box, orient="vertical", command=self.tree.yview)
        scroll_x = ttk.Scrollbar(queue_box, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set)
        self.tree.grid(row=0, column=0, columnspan=5, sticky="nsew")
        scroll_y.grid(row=0, column=5, sticky="ns")
        scroll_x.grid(row=1, column=0, columnspan=5, sticky="ew")
        queue_box.rowconfigure(0, weight=1)
        queue_box.columnconfigure(0, weight=1)
        ttk.Button(queue_box, text="Retry", command=self.retry_selected).grid(
            row=2, column=0, sticky="w", pady=(8, 0)
        )
        ttk.Button(queue_box, text="Cancel active", command=self.cancel_selected).grid(
            row=2, column=1, sticky="w", pady=(8, 0)
        )
        ttk.Button(queue_box, text="Remove waiting", command=self.remove_selected).grid(
            row=2, column=2, sticky="w", pady=(8, 0)
        )
        ttk.Button(
            queue_box, text="Open output folder", command=self.open_selected_folder
        ).grid(row=2, column=3, sticky="w", pady=(8, 0))
        ttk.Label(outer, textvariable=self.status_var).pack(fill="x", pady=(8, 0))

    def show_error(self, message: str) -> None:
        messagebox.showerror("Video workflow", message, parent=self.root)

    def show_warning(self, message: str) -> None:
        messagebox.showwarning("Batch folder", message, parent=self.root)

    def _initial_directory(self, variable: tk.StringVar) -> Path:
        raw = variable.get().strip()
        if raw:
            candidate = Path(raw).expanduser()
            if candidate.is_dir():
                return candidate
            if candidate.parent.is_dir():
                return candidate.parent
        return self._last_directory

    def _choose_file(
        self, variable: tk.StringVar, filetypes: list[tuple[str, str]]
    ) -> None:
        path = LivePathDialog(
            self.root,
            title="Select file",
            initial_directory=self._initial_directory(variable),
            filetypes=filetypes,
        ).show()
        if path:
            variable.set(path)
            self._last_directory = Path(path).parent

    def _choose_directory(self, variable: tk.StringVar) -> None:
        path = LivePathDialog(
            self.root,
            title="Select folder",
            initial_directory=self._initial_directory(variable),
            select_directory=True,
        ).show()
        if path:
            variable.set(path)
            self._last_directory = Path(path)

    def choose_logo(self) -> None:
        self._choose_file(
            self.logo_var,
            [("Images", "*.png *.jpg *.jpeg *.webp"), ("All files", "*.*")],
        )

    def choose_outro(self) -> None:
        self._choose_file(
            self.outro_var,
            [("Videos", "*.mp4 *.mov *.mkv"), ("All files", "*.*")],
        )

    def choose_batch_root(self) -> None:
        self._choose_directory(self.batch_root_var)

    def save_global_assets(self) -> None:
        logo = self.logo_var.get().strip()
        outro = self.outro_var.get().strip()
        if not logo or not outro:
            self.show_error("logo and outro are required")
            return
        try:
            self.controller.set_global_assets(Path(logo), Path(outro))
        except (OSError, ValueError) as exc:
            self.show_error(str(exc))
            return
        self.status_var.set("Required assets saved")
        self.worker.wake()

    def submit_batch_folder(self) -> None:
        settings = self.controller.settings
        if (
            settings.logo is None
            or settings.outro is None
            or not settings.logo.is_file()
            or not settings.outro.is_file()
        ):
            self.show_error("Save a valid logo and outro before scanning")
            return
        raw_root = self.batch_root_var.get().strip()
        if not raw_root:
            self.show_error("input root is required")
            return
        try:
            result = queue_folder_jobs(self.controller, Path(raw_root))
        except OSError as exc:
            self.show_error(str(exc))
            return
        if result.issues:
            visible = result.issues[:20]
            lines = [f"{issue.folder}: {issue.message}" for issue in visible]
            if len(result.issues) > len(visible):
                lines.append(f"... and {len(result.issues) - len(visible)} more issue(s)")
            self.show_warning("\n".join(lines))
        self.status_var.set(
            f"Batch: {len(result.queued)} added, {len(result.issues)} issue(s)"
        )
        self.refresh_jobs()
        if result.queued:
            self.worker.wake()

    def refresh_jobs(self) -> None:
        if not hasattr(self, "tree"):
            return
        selected = self.tree.selection()
        present: set[str] = set()
        for position, job in enumerate(self.controller.jobs()):
            present.add(job.id)
            values = job_row(job)
            if self.tree.exists(job.id):
                self.tree.item(job.id, values=values)
            else:
                self.tree.insert("", "end", iid=job.id, values=values)
            self.tree.move(job.id, "", position)
        for item in self.tree.get_children():
            if item not in present:
                self.tree.delete(item)
        if selected and self.tree.exists(selected[0]):
            self.tree.selection_set(selected[0])

    def _refresh_path_indicators(self) -> None:
        for binding in self._path_bindings:
            raw = binding.variable.get().strip()
            if not raw:
                binding.indicator.configure(text="Required", style="Path.Empty.TLabel")
                continue
            path = Path(raw).expanduser()
            available = path.is_dir() if binding.kind == "directory" else path.is_file()
            binding.indicator.configure(
                text="Available" if available else "Missing",
                style="Path.Available.TLabel" if available else "Path.Missing.TLabel",
            )

    def _poll_filesystem(self) -> None:
        if self._closing:
            return
        self._refresh_path_indicators()
        restored = self.controller.restore_available_path_jobs()
        if restored:
            self.status_var.set(f"Restored {len(restored)} job(s)")
            self.worker.wake()
        self.refresh_jobs()
        self.root.after(1000, self._poll_filesystem)

    def _selected_id(self) -> str:
        if not hasattr(self, "tree") or not self.tree.selection():
            raise ValueError("select a job first")
        return self.tree.selection()[0]

    def retry_selected(self) -> None:
        try:
            self.controller.retry(self._selected_id())
        except (QueueStateError, ValueError) as exc:
            self.show_error(str(exc))
            return
        self.refresh_jobs()
        self.worker.wake()

    def cancel_selected(self) -> None:
        try:
            job_id = self._selected_id()
            if self.worker.current_job_id != job_id:
                raise ValueError("selected job is not currently running")
            self.worker.cancel_current()
        except ValueError as exc:
            self.show_error(str(exc))

    def remove_selected(self) -> None:
        try:
            self.controller.remove(self._selected_id())
        except (QueueStateError, ValueError) as exc:
            self.show_error(str(exc))
            return
        self.refresh_jobs()

    def open_selected_folder(self) -> None:
        try:
            job_id = self._selected_id()
            job = next(job for job in self.controller.jobs() if job.id == job_id)
            target = (
                job.artifact_directory
                if job.artifact_directory.is_dir()
                else job.artifact_directory.parent
            )
            _open_directory(target)
        except (OSError, StopIteration, ValueError) as exc:
            self.show_error(str(exc))

    def process_worker_events(self) -> None:
        refreshed = False
        while True:
            try:
                event = self.worker.events.get_nowait()
            except queue.Empty:
                break
            self.status_var.set(event.message or event.kind)
            refreshed = True
        if refreshed:
            self.refresh_jobs()

    def _poll_worker_events(self) -> None:
        self.process_worker_events()
        if not self._closing:
            self.root.after(200, self._poll_worker_events)

    def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        if hasattr(self, "batch_button"):
            self.batch_button.configure(state="disabled")
        self.worker.stop(timeout=10)
        self._finish_close()

    def _finish_close(self) -> None:
        if self.worker.is_alive():
            self.status_var.set("Waiting for the active job to stop safely...")
            self.root.after(200, self._finish_close)
            return
        self.process_worker_events()
        self.root.destroy()


def main() -> int:
    root = tk.Tk()
    store = JsonStore(application_data_root())
    controller = QueueController(store)
    controller.recover_startup()
    worker = QueueWorker(controller)
    _app = WorkflowApp(root, controller, worker)
    if store.warnings:
        messagebox.showwarning(
            "Recovered workflow data", "\n".join(store.warnings), parent=root
        )
    worker.start()
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
