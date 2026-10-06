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
from .job_models import JobKind, JobRecord, JobStatus
from .json_store import JsonStore
from .live_path_dialog import LivePathDialog
from .queue_controller import QueueController, QueueStateError
from .worker import QueueWorker


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


@dataclass(frozen=True, slots=True)
class JobFormData:
    source_media: Path
    pptx: Path
    timeline: Path
    output_name: str
    output_directory: Path

    @classmethod
    def from_strings(
        cls,
        source_media: str,
        pptx: str,
        timeline: str,
        output_name: str,
        output_directory: str,
    ) -> JobFormData:
        values = {
            "source media": source_media.strip(),
            "PPTX": pptx.strip(),
            "timeline": timeline.strip(),
            "output name": output_name.strip(),
            "output directory": output_directory.strip(),
        }
        missing = next((label for label, value in values.items() if not value), None)
        if missing:
            raise ValueError(f"{missing} is required")
        return cls(
            source_media=Path(values["source media"]),
            pptx=Path(values["PPTX"]),
            timeline=Path(values["timeline"]),
            output_name=values["output name"],
            output_directory=Path(values["output directory"]),
        )


@dataclass(frozen=True, slots=True)
class MergeFormData:
    first_video: Path
    second_video: Path
    output_name: str
    output_directory: Path

    @classmethod
    def from_strings(
        cls,
        first_video: str,
        second_video: str,
        output_name: str,
        output_directory: str,
    ) -> MergeFormData:
        values = {
            "first video": first_video.strip(),
            "second video": second_video.strip(),
            "output name": output_name.strip(),
            "output directory": output_directory.strip(),
        }
        missing = next((label for label, value in values.items() if not value), None)
        if missing:
            raise ValueError(f"{missing} is required")
        return cls(
            first_video=Path(values["first video"]),
            second_video=Path(values["second video"]),
            output_name=values["output name"],
            output_directory=Path(values["output directory"]),
        )


@dataclass(slots=True)
class _PathBinding:
    variable: tk.StringVar
    kind: str
    optional: bool
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
        self._waiting_readiness: dict[str, bool] = {}
        self._last_directory = Path.cwd()
        root.title("Pyramid Slide Video Queue")
        root.geometry("1120x720")
        root.minsize(900, 600)

        settings = controller.settings
        self.logo_var = tk.StringVar(value=str(settings.logo or ""))
        self.outro_var = tk.StringVar(value=str(settings.outro or ""))
        self.source_var = tk.StringVar()
        self.pptx_var = tk.StringVar()
        self.timeline_var = tk.StringVar()
        self.output_name_var = tk.StringVar()
        self.output_directory_var = tk.StringVar()
        self.batch_root_var = tk.StringVar()
        self.first_video_var = tk.StringVar()
        self.second_video_var = tk.StringVar()
        self.merge_output_name_var = tk.StringVar()
        self.merge_output_directory_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Ready")

        self._build_layout()
        self.refresh_jobs()
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(200, self._poll_worker_events)
        root.after(250, self._poll_filesystem)

    def _path_row(
        self,
        parent,
        row: int,
        label: str,
        variable: tk.StringVar,
        command,
        button="Browse",
        *,
        kind: str = "file",
        optional: bool = False,
    ) -> None:
        ttk.Label(parent, text=label).grid(
            row=row, column=0, sticky="w", padx=(0, 8), pady=3
        )
        ttk.Entry(parent, textvariable=variable).grid(
            row=row, column=1, sticky="ew", pady=3
        )
        indicator = ttk.Label(parent, width=12, anchor="e")
        indicator.grid(row=row, column=2, padx=(8, 0), pady=3, sticky="e")
        self._path_bindings.append(_PathBinding(variable, kind, optional, indicator))
        ttk.Button(parent, text=button, command=command).grid(
            row=row, column=3, padx=(8, 0), pady=3
        )

    def _build_layout(self) -> None:
        style = ttk.Style(self.root)
        style.configure("Path.Available.TLabel", foreground="#188038")
        style.configure("Path.Missing.TLabel", foreground="#c5221f")
        style.configure("Path.Empty.TLabel", foreground="#6b7280")

        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill="both", expand=True)

        global_box = ttk.LabelFrame(outer, text="Global assets", padding=10)
        global_box.pack(fill="x")
        global_box.columnconfigure(1, weight=1)
        self._path_row(
            global_box,
            0,
            "Logo (optional)",
            self.logo_var,
            self.choose_logo,
            optional=True,
        )
        self._path_row(
            global_box,
            1,
            "Outro (optional)",
            self.outro_var,
            self.choose_outro,
            optional=True,
        )
        ttk.Button(
            global_box, text="Save assets", command=self.save_global_assets
        ).grid(row=2, column=3, sticky="e", pady=(6, 0))

        job_tabs = ttk.Notebook(outer)
        job_tabs.pack(fill="x", pady=10)

        form = ttk.Frame(job_tabs, padding=10)
        form.columnconfigure(1, weight=1)
        job_tabs.add(form, text="Build slide video")
        self._path_row(form, 0, "Video or audio", self.source_var, self.choose_source)
        self._path_row(form, 1, "PPTX", self.pptx_var, self.choose_pptx)
        self._path_row(
            form, 2, "Timeline TXT/JSON", self.timeline_var, self.choose_timeline
        )
        ttk.Label(form, text="Output name").grid(row=3, column=0, sticky="w", pady=3)
        ttk.Entry(form, textvariable=self.output_name_var).grid(
            row=3, column=1, sticky="ew", pady=3
        )
        self._path_row(
            form,
            4,
            "Output directory",
            self.output_directory_var,
            self.choose_output_directory,
            kind="directory",
        )
        self.add_button = ttk.Button(form, text="Add to queue", command=self.submit_job)
        self.add_button.grid(row=5, column=3, sticky="e", pady=(8, 0))

        batch_form = ttk.Frame(job_tabs, padding=10)
        batch_form.columnconfigure(1, weight=1)
        job_tabs.add(batch_form, text="Batch folder")
        self._path_row(
            batch_form,
            0,
            "Input root",
            self.batch_root_var,
            self.choose_batch_root,
            kind="directory",
        )
        ttk.Label(
            batch_form,
            text=(
                "Recursively scans leaf folders for BASE.pptx, BASE_1/2 media, "
                "and timeline_slide_BASE_1/2.txt/json. Outro is added to part 2 only."
            ),
        ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(5, 0))
        self.batch_button = ttk.Button(
            batch_form, text="Scan and add to queue", command=self.submit_batch_folder
        )
        self.batch_button.grid(row=2, column=3, sticky="e", pady=(8, 0))

        merge_form = ttk.Frame(job_tabs, padding=10)
        merge_form.columnconfigure(1, weight=1)
        job_tabs.add(merge_form, text="Merge 2 videos")
        self._path_row(
            merge_form, 0, "Video 1", self.first_video_var, self.choose_first_video
        )
        self._path_row(
            merge_form, 1, "Video 2", self.second_video_var, self.choose_second_video
        )
        ttk.Label(merge_form, text="Output name").grid(
            row=2, column=0, sticky="w", pady=3
        )
        ttk.Entry(merge_form, textvariable=self.merge_output_name_var).grid(
            row=2, column=1, sticky="ew", pady=3
        )
        self._path_row(
            merge_form,
            3,
            "Output directory",
            self.merge_output_directory_var,
            self.choose_merge_output_directory,
            kind="directory",
        )
        self.merge_button = ttk.Button(
            merge_form, text="Add to queue", command=self.submit_merge_job
        )
        self.merge_button.grid(row=4, column=3, sticky="e", pady=(8, 0))

        queue_box = ttk.LabelFrame(outer, text="Queue", padding=8)
        queue_box.pack(fill="both", expand=True)
        columns = (
            "type",
            "output",
            "source",
            "files",
            "status",
            "stage",
            "result",
        )
        self.tree = ttk.Treeview(
            queue_box, columns=columns, show="headings", selectmode="browse"
        )
        headings = {
            "type": "Type",
            "output": "Output",
            "source": "Source",
            "files": "Files",
            "status": "Status",
            "stage": "Stage",
            "result": "Result / error",
        }
        widths = {
            "type": 70,
            "output": 150,
            "source": 240,
            "files": 150,
            "status": 90,
            "stage": 130,
            "result": 320,
        }
        for name in columns:
            self.tree.heading(name, text=headings[name])
            self.tree.column(name, width=widths[name], minwidth=70)
        scroll_y = ttk.Scrollbar(queue_box, orient="vertical", command=self.tree.yview)
        scroll_x = ttk.Scrollbar(
            queue_box, orient="horizontal", command=self.tree.xview
        )
        self.tree.configure(yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set)
        self.tree.grid(row=0, column=0, columnspan=4, sticky="nsew")
        scroll_y.grid(row=0, column=4, sticky="ns")
        scroll_x.grid(row=1, column=0, columnspan=4, sticky="ew")
        queue_box.rowconfigure(0, weight=1)
        queue_box.columnconfigure(0, weight=1)
        ttk.Button(queue_box, text="Retry", command=self.retry_selected).grid(
            row=2, column=0, sticky="w", pady=(8, 0)
        )
        ttk.Button(queue_box, text="Remove waiting", command=self.remove_selected).grid(
            row=2, column=1, sticky="w", pady=(8, 0)
        )
        ttk.Button(
            queue_box, text="Open output folder", command=self.open_selected_folder
        ).grid(row=2, column=2, sticky="w", pady=(8, 0))
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
            self.outro_var, [("Videos", "*.mp4 *.mov *.mkv"), ("All files", "*.*")]
        )

    def choose_source(self) -> None:
        self._choose_file(
            self.source_var,
            [
                ("Video and audio", "*.mp4 *.mov *.mkv *.mp3 *.wav *.m4a *.aac"),
                ("All files", "*.*"),
            ],
        )

    def choose_pptx(self) -> None:
        self._choose_file(self.pptx_var, [("PowerPoint", "*.pptx")])

    def choose_timeline(self) -> None:
        self._choose_file(self.timeline_var, [("Timeline", "*.txt *.json")])

    def choose_output_directory(self) -> None:
        self._choose_directory(self.output_directory_var)

    def choose_batch_root(self) -> None:
        self._choose_directory(self.batch_root_var)

    def choose_first_video(self) -> None:
        self._choose_file(
            self.first_video_var,
            [("Videos", "*.mp4 *.mov *.mkv"), ("All files", "*.*")],
        )

    def choose_second_video(self) -> None:
        self._choose_file(
            self.second_video_var,
            [("Videos", "*.mp4 *.mov *.mkv"), ("All files", "*.*")],
        )

    def choose_merge_output_directory(self) -> None:
        self._choose_directory(self.merge_output_directory_var)

    def save_global_assets(self) -> None:
        try:
            logo = self.logo_var.get().strip()
            outro = self.outro_var.get().strip()
            self.controller.set_global_assets(
                Path(logo) if logo else None, Path(outro) if outro else None
            )
        except (OSError, ValueError) as exc:
            self.show_error(str(exc))
            return
        self.status_var.set("Optional assets saved")
        self.worker.wake()

    def submit_job(self) -> None:
        try:
            data = JobFormData.from_strings(
                self.source_var.get(),
                self.pptx_var.get(),
                self.timeline_var.get(),
                self.output_name_var.get(),
                self.output_directory_var.get(),
            )
            self.controller.enqueue(
                source_media=data.source_media,
                pptx=data.pptx,
                timeline=data.timeline,
                output_name=data.output_name,
                output_directory=data.output_directory,
            )
        except (OSError, ValueError) as exc:
            self.show_error(str(exc))
            return
        self.status_var.set("Job added")
        self.output_name_var.set("")
        self.refresh_jobs()
        self.worker.wake()

    def submit_merge_job(self) -> None:
        try:
            data = MergeFormData.from_strings(
                self.first_video_var.get(),
                self.second_video_var.get(),
                self.merge_output_name_var.get(),
                self.merge_output_directory_var.get(),
            )
            self.controller.enqueue_merge(
                first_video=data.first_video,
                second_video=data.second_video,
                output_name=data.output_name,
                output_directory=data.output_directory,
            )
        except (OSError, ValueError) as exc:
            self.show_error(str(exc))
            return
        self.status_var.set("Merge job added")
        self.merge_output_name_var.set("")
        self.refresh_jobs()
        self.worker.wake()

    def submit_batch_folder(self) -> None:
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
            f"Batch: {len(result.queued)} added, {len(result.skipped)} skipped, "
            f"{len(result.issues)} issue(s)"
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
            result = job.error or (
                str(job.output_path) if job.status.value == "completed" else ""
            )
            source = str(job.source_media)
            if job.kind is JobKind.MERGE and job.secondary_media is not None:
                source = f"{source}  →  {job.secondary_media}"
            values = (
                job.kind.value,
                job.output_name,
                source,
                self._job_files_summary(job),
                job.status.value,
                job.stage.value if job.stage else "",
                result,
            )
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

    def _missing_job_paths(self, job: JobRecord) -> list[str]:
        required: list[tuple[str, Path | None]] = [("source", job.source_media)]
        if job.kind is JobKind.MERGE:
            required.append(("video 2", job.secondary_media))
        else:
            required.extend((("PPTX", job.pptx), ("timeline", job.timeline)))
            if job.status in {JobStatus.WAITING, JobStatus.RUNNING}:
                settings = self.controller.settings
                required.append(("logo", settings.logo))
                if job.use_outro:
                    required.append(("outro", settings.outro))
        missing = [
            label
            for label, path in required
            if path is not None and not Path(path).is_file()
        ]
        if not job.output_directory.is_dir():
            missing.append("output folder")
        return missing

    def _job_files_summary(self, job: JobRecord) -> str:
        parts: list[str] = []
        missing = self._missing_job_paths(job)
        if missing:
            parts.append(f"Missing: {', '.join(missing)}")
        if job.status is JobStatus.COMPLETED:
            parts.append(
                "Output ready" if job.output_path.is_file() else "Output missing"
            )
        elif job.output_path.exists():
            parts.append("Output exists")
        return "; ".join(parts) or "Ready"

    def _waiting_job_ready(self, job: JobRecord) -> bool:
        return (
            job.status is JobStatus.WAITING
            and not self._missing_job_paths(job)
            and not job.output_path.exists()
        )

    def _refresh_path_indicators(self) -> None:
        for binding in self._path_bindings:
            raw = binding.variable.get().strip()
            if not raw:
                binding.indicator.configure(
                    text="Optional" if binding.optional else "Required",
                    style="Path.Empty.TLabel",
                )
                continue
            path = Path(raw).expanduser()
            available = path.is_dir() if binding.kind == "directory" else path.is_file()
            binding.indicator.configure(
                text="Available" if available else "Missing",
                style=("Path.Available.TLabel" if available else "Path.Missing.TLabel"),
            )

    def _poll_filesystem(self) -> None:
        if self._closing:
            return
        self._refresh_path_indicators()
        restored = self.controller.restore_available_path_jobs()
        jobs = self.controller.jobs()
        current_readiness = {
            job.id: self._waiting_job_ready(job)
            for job in jobs
            if job.status is JobStatus.WAITING
        }
        became_ready = any(
            ready and self._waiting_readiness.get(job_id) is False
            for job_id, ready in current_readiness.items()
        )
        self._waiting_readiness = current_readiness
        if restored:
            self.status_var.set(
                f"Restored {len(restored)} job(s) after files became available"
            )
        if restored or became_ready:
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
            _open_directory(job.output_directory)
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
        if hasattr(self, "add_button"):
            self.add_button.configure(state="disabled")
        if hasattr(self, "merge_button"):
            self.merge_button.configure(state="disabled")
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
