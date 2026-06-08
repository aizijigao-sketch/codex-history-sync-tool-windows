from __future__ import annotations

import json
import os
from pathlib import Path


DEFAULT_APPDATA = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
DEFAULT_STATE_DIR = DEFAULT_APPDATA / "Codex History Sync Tool"
DEFAULT_SETTINGS = {
    "auto_detect": True,
    "auto_fix_chats": True,
    "auto_fix_projects": False,
    "dual_home": True,
    "detect_only": False,
}


def settings_path(state_dir: str | Path | None = None) -> Path:
    base = Path(state_dir).expanduser() if state_dir else DEFAULT_STATE_DIR
    return base / "autosync-settings.json"


def normalize_settings(raw: object) -> dict[str, bool]:
    data = raw if isinstance(raw, dict) else {}
    return {
        key: bool(data.get(key, default))
        for key, default in DEFAULT_SETTINGS.items()
    }


def load_settings(state_dir: str | Path | None = None) -> dict[str, bool]:
    path = settings_path(state_dir)
    if not path.exists():
        return dict(DEFAULT_SETTINGS)
    try:
        return normalize_settings(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return dict(DEFAULT_SETTINGS)


def save_settings(settings: dict[str, object], state_dir: str | Path | None = None) -> Path:
    path = settings_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = normalize_settings(settings)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path
