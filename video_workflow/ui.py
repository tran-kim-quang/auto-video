from __future__ import annotations

import os
import queue
import subprocess
import sys
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .json_store import JsonStore
from .queue_controller import QueueController, QueueStateError
from .worker import QueueWorker


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
        cls, source_media: str, pptx: str, timeline: str, output_name: str, output_directory: str
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


class WorkflowApp:
    def __init__(self, root: tk.Tk, controller: QueueController, worker: QueueWorker) -> None:
        self.root = root
        self.controller = controller
        self.worker = worker
        self._closing = False
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
        self.status_var = tk.StringVar(value="Ready")

        self._build_layout()
        self.refresh_jobs()
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(200, self._poll_worker_events)

    def _path_row(self, parent, row: int, label: str, variable: tk.StringVar, command, button="Browse") -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=3)
        ttk.Entry(parent, textvariable=variable).grid(row=row, column=1, sticky="ew", pady=3)
        ttk.Button(parent, text=button, command=command).grid(row=row, column=2, padx=(8, 0), pady=3)

    def _build_layout(self) -> None:
        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill="both", expand=True)

        global_box = ttk.LabelFrame(outer, text="Global assets", padding=10)
        global_box.pack(fill="x")
        global_box.columnconfigure(1, weight=1)
        self._path_row(global_box, 0, "Logo (optional)", self.logo_var, self.choose_logo)
        self._path_row(global_box, 1, "Outro (optional)", self.outro_var, self.choose_outro)
        ttk.Button(global_box, text="Save assets", command=self.save_global_assets).grid(
            row=2, column=2, sticky="e", pady=(6, 0)
        )

        form = ttk.LabelFrame(outer, text="Add job", padding=10)
        form.pack(fill="x", pady=10)
        form.columnconfigure(1, weight=1)
        self._path_row(form, 0, "Video or audio", self.source_var, self.choose_source)
        self._path_row(form, 1, "PPTX", self.pptx_var, self.choose_pptx)
        self._path_row(form, 2, "Timeline TXT", self.timeline_var, self.choose_timeline)
        ttk.Label(form, text="Output name").grid(row=3, column=0, sticky="w", pady=3)
        ttk.Entry(form, textvariable=self.output_name_var).grid(row=3, column=1, sticky="ew", pady=3)
        self._path_row(form, 4, "Output directory", self.output_directory_var, self.choose_output_directory)
        self.add_button = ttk.Button(form, text="Add to queue", command=self.submit_job)
        self.add_button.grid(row=5, column=2, sticky="e", pady=(8, 0))

        queue_box = ttk.LabelFrame(outer, text="Queue", padding=8)
        queue_box.pack(fill="both", expand=True)
        columns = ("output", "source", "status", "stage", "result")
        self.tree = ttk.Treeview(queue_box, columns=columns, show="headings", selectmode="browse")
        headings = {"output": "Output", "source": "Source", "status": "Status", "stage": "Stage", "result": "Result / error"}
        widths = {"output": 150, "source": 240, "status": 90, "stage": 130, "result": 360}
        for name in columns:
            self.tree.heading(name, text=headings[name])
            self.tree.column(name, width=widths[name], minwidth=70)
        scroll_y = ttk.Scrollbar(queue_box, orient="vertical", command=self.tree.yview)
        scroll_x = ttk.Scrollbar(queue_box, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set)
        self.tree.grid(row=0, column=0, columnspan=4, sticky="nsew")
        scroll_y.grid(row=0, column=4, sticky="ns")
        scroll_x.grid(row=1, column=0, columnspan=4, sticky="ew")
        queue_box.rowconfigure(0, weight=1)
        queue_box.columnconfigure(0, weight=1)
        ttk.Button(queue_box, text="Retry", command=self.retry_selected).grid(row=2, column=0, sticky="w", pady=(8, 0))
        ttk.Button(queue_box, text="Remove waiting", command=self.remove_selected).grid(row=2, column=1, sticky="w", pady=(8, 0))
        ttk.Button(queue_box, text="Open output folder", command=self.open_selected_folder).grid(row=2, column=2, sticky="w", pady=(8, 0))
        ttk.Label(outer, textvariable=self.status_var).pack(fill="x", pady=(8, 0))

    def show_error(self, message: str) -> None:
        messagebox.showerror("Video workflow", message, parent=self.root)

    def _choose_file(self, variable: tk.StringVar, filetypes) -> None:
        path = filedialog.askopenfilename(parent=self.root, filetypes=filetypes)
        if path:
            variable.set(path)

    def choose_logo(self) -> None:
        self._choose_file(self.logo_var, [("Images", "*.png *.jpg *.jpeg *.webp"), ("All files", "*.*")])

    def choose_outro(self) -> None:
        self._choose_file(self.outro_var, [("Videos", "*.mp4 *.mov *.mkv"), ("All files", "*.*")])

    def choose_source(self) -> None:
        self._choose_file(self.source_var, [("Video and audio", "*.mp4 *.mov *.mkv *.mp3 *.wav *.m4a *.aac"), ("All files", "*.*")])

    def choose_pptx(self) -> None:
        self._choose_file(self.pptx_var, [("PowerPoint", "*.pptx")])

    def choose_timeline(self) -> None:
        self._choose_file(self.timeline_var, [("Timeline", "*.txt")])

    def choose_output_directory(self) -> None:
        path = filedialog.askdirectory(parent=self.root)
        if path:
            self.output_directory_var.set(path)

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
                self.source_var.get(), self.pptx_var.get(), self.timeline_var.get(),
                self.output_name_var.get(), self.output_directory_var.get(),
            )
            self.controller.enqueue(
                source_media=data.source_media, pptx=data.pptx, timeline=data.timeline,
                output_name=data.output_name, output_directory=data.output_directory,
            )
        except (OSError, ValueError) as exc:
            self.show_error(str(exc))
            return
        self.status_var.set("Job added")
        self.output_name_var.set("")
        self.refresh_jobs()
        self.worker.wake()

    def refresh_jobs(self) -> None:
        if not hasattr(self, "tree"):
            return
        selected = self.tree.selection()
        for item in self.tree.get_children():
            self.tree.delete(item)
        for job in self.controller.jobs():
            result = job.error or (str(job.output_path) if job.status.value == "completed" else "")
            self.tree.insert("", "end", iid=job.id, values=(
                job.output_name, str(job.source_media), job.status.value,
                job.stage.value if job.stage else "", result,
            ))
        if selected and self.tree.exists(selected[0]):
            self.tree.selection_set(selected[0])

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
    data_root = Path(__file__).resolve().parents[1] / ".workflow_data"
    store = JsonStore(data_root)
    controller = QueueController(store)
    controller.recover_startup()
    worker = QueueWorker(controller)
    app = WorkflowApp(root, controller, worker)
    if store.warnings:
        messagebox.showwarning("Recovered workflow data", "\n".join(store.warnings), parent=root)
    worker.start()
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
