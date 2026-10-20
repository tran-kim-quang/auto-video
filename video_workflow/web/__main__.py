from __future__ import annotations

import threading
from collections.abc import Sequence

from ..json_store import JsonStore
from ..ui import application_data_root
from ..voicestudio_supervisor import VoiceStudioSupervisor
from .config import WebLayout, build_status, init_settings, parse_args
from .gpu import GpuArbiter
from .runner import RunnerContext, run_web_job
from .scheduler import JobScheduler
from .state import WebStore
from .upload_service import UploadService
from .voices import VoiceService


def main(argv: Sequence[str] | None = None) -> int:
    import uvicorn
    from .app import WebServices, create_app

    args = parse_args(argv)
    layout = WebLayout(args.root).ensure()
    initial = init_settings(layout, logo=args.logo, outro=args.outro, legacy_root=application_data_root())
    settings_store = JsonStore(layout.state)
    settings = settings_store.load_settings
    supervisor = VoiceStudioSupervisor(
        initial.voice_config(), backend_override=initial.backend_override, log_directory=layout.logs
    )
    arbiter = GpuArbiter(args.gpu_slots)
    store = WebStore(layout.state)
    context = RunnerContext(
        uploads_dir=layout.uploads, work_dir=layout.work, cache_dir=layout.tts_cache,
        logs_dir=layout.logs, settings=settings, client=lambda: supervisor.ensure_ready(),
        arbiter=arbiter, export_lock=threading.Lock(),
    )
    scheduler = JobScheduler(
        store, lambda job, event, update: run_web_job(job, context, event, update),
        max_active=args.max_active,
    )
    voices = VoiceService(
        client=lambda: supervisor.ensure_ready(), arbiter=arbiter, store=store,
        config=lambda: settings().voice_config(), active_jobs=scheduler.jobs,
        max_reference_seconds=args.max_reference_seconds,
    )
    uploads = UploadService(
        store,
        layout,
        repo=lambda: args.upload_repo,
        jobs=scheduler.jobs,
    )

    def voice_ok() -> bool:
        try:
            supervisor.ensure_ready()
            return True
        except Exception:
            return False

    services = WebServices(
        layout=layout, store=store, scheduler=scheduler, voices=voices,
        uploads=uploads,
        settings=settings,
        status=lambda: build_status(
            settings(),
            voice_ok,
            args.port,
            max_reference_seconds=args.max_reference_seconds,
            upload_repo=args.upload_repo,
        ),
    )
    app = create_app(services)
    scheduler.start()
    print(f"Mở trên máy này: http://localhost:{args.port}")
    for url in build_status(
        settings(),
        voice_ok,
        args.port,
        max_reference_seconds=args.max_reference_seconds,
        upload_repo=args.upload_repo,
    )["lan_urls"]:
        print(f"Mở trong mạng LAN: {url}")
    try:
        uvicorn.run(app, host=args.host, port=args.port)
    finally:
        uploads.stop()
        scheduler.stop()
        supervisor.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
