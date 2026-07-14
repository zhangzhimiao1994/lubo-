from __future__ import annotations

from pathlib import Path


PACKAGE_NAME = "org.lubo.recorder"
SERVICE_CLASS = f"{PACKAGE_NAME}.ServiceRecorder"
STOP_REQUEST_FILE = "stop.request"


def app_storage_root() -> Path:
    try:
        from android.storage import app_storage_path
    except ImportError:
        return Path.home() / ".douyin-live-recorder-android"
    return Path(app_storage_path())


def request_runtime_permissions() -> None:
    try:
        from android.permissions import Permission, request_permissions
    except ImportError:
        return

    permissions = []
    notification_permission = getattr(Permission, "POST_NOTIFICATIONS", None)
    if notification_permission:
        permissions.append(notification_permission)
    if permissions:
        request_permissions(permissions)


def start_recorder_service(root: Path) -> None:
    stop_request = root / STOP_REQUEST_FILE
    stop_request.unlink(missing_ok=True)

    from jnius import autoclass

    service = autoclass(SERVICE_CLASS)
    activity = autoclass("org.kivy.android.PythonActivity").mActivity
    service.start(
        activity,
        "",
        "Lubo",
        "Monitoring live rooms; tap Stop to finish recordings",
        "",
    )


def request_service_stop(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / STOP_REQUEST_FILE).write_text("stop\n", encoding="ascii")
