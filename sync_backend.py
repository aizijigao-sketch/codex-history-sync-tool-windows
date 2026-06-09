from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import socket
import subprocess
import time
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SESSION_FILENAME_PATTERN = re.compile(
    r"rollout-.*-(?P<id>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$"
)
UTC = timezone.utc
TOOL_VERSION = "0.3.5-autosync-health"
UPSTREAM_VERSION = "v0.2.5"
DEFAULT_DB_TIMEOUT_SECONDS = 30.0
WRITE_OPERATION_TIMEOUT_SECONDS = 0.5
WRITE_LOCK_RETRY_LIMIT = 40
WRITE_LOCK_RETRY_DELAY_SECONDS = 0.25
FILE_REPLACE_RETRY_LIMIT = 20
FILE_REPLACE_RETRY_DELAY_SECONDS = 0.1
SYNC_CHECKPOINT_MODE = "PASSIVE"
CODEX_HOME_SPECS = {
    "official": {
        "name": "official",
        "display": "官方目录",
        "home_name": ".codex-official",
        "login_mode": "OpenAI Official / ChatGPT OAuth",
    },
    "thirdparty": {
        "name": "thirdparty",
        "display": "第三方目录",
        "home_name": ".codex",
        "login_mode": "CCSwitch local route / custom provider",
    },
}
ONE_CLICK_MODES = ("auto", "official", "thirdparty")


def default_codex_home() -> Path:
    return Path.home() / ".codex"


def named_codex_home(home_name: str) -> Path:
    return Path.home() / home_name


@dataclass
class Paths:
    codex_home: Path
    config_path: Path
    db_path: Path
    backup_dir: Path
    session_index_path: Path
    sessions_dir: Path
    archived_sessions_dir: Path
    global_state_path: Path


@dataclass
class SessionRecord:
    thread_id: str
    path: Path
    model_provider: str
    model: str | None


@dataclass
class SessionMetaStats:
    provider_counts: OrderedDict[str, int]
    model_counts: OrderedDict[str, int]
    mismatched_files: int
    mismatched_entries: int
    mismatched_thread_ids: set[str]
    provider_mismatched_entries: int
    provider_mismatched_thread_ids: set[str]


def resolve_paths(codex_home: str | None) -> Paths:
    home = Path(codex_home).expanduser() if codex_home else default_codex_home()
    return Paths(
        codex_home=home,
        config_path=home / "config.toml",
        db_path=home / "state_5.sqlite",
        backup_dir=home / "history_sync_backups",
        session_index_path=home / "session_index.jsonl",
        sessions_dir=home / "sessions",
        archived_sessions_dir=home / "archived_sessions",
        global_state_path=home / ".codex-global-state.json",
    )


def resolve_one_click_homes(mode: str) -> list[tuple[str, Paths]]:
    if mode not in ONE_CLICK_MODES:
        raise RuntimeError(f"Unsupported one-click mode: {mode}")
    names = ["official", "thirdparty"] if mode == "auto" else [mode]
    return [
        (name, resolve_paths(str(named_codex_home(str(CODEX_HOME_SPECS[name]["home_name"])))))
        for name in names
    ]


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def read_text_exact(path: Path) -> str:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return handle.read()


def replace_file_with_retry(source_path: Path, target_path: Path) -> None:
    last_error: OSError | None = None
    for attempt in range(FILE_REPLACE_RETRY_LIMIT):
        try:
            # 用原子替换避免写到一半被 Codex 读到半成品文件。
            source_path.replace(target_path)
            return
        except PermissionError as exc:
            last_error = exc
        except OSError as exc:
            if getattr(exc, "winerror", None) not in (5, 32):
                raise
            last_error = exc

        if attempt < FILE_REPLACE_RETRY_LIMIT - 1:
            time.sleep(FILE_REPLACE_RETRY_DELAY_SECONDS)

    raise RuntimeError(f"File is busy and could not be replaced: {target_path}") from last_error


def write_text_exact(path: Path, text: str) -> None:
    temp_path = path.with_name(f".{path.name}.codex-sync-{time.time_ns()}.tmp")
    try:
        with temp_path.open("w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        replace_file_with_retry(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def parse_current_provider(config_text: str) -> str:
    match = re.search(r'(?m)^\s*model_provider\s*=\s*"([^"]+)"', config_text)
    return match.group(1) if match else ""


def configured_model_provider_names(config_text: str) -> set[str]:
    names = {"openai"}
    for match in re.finditer(r'(?m)^\s*\[model_providers\.([A-Za-z0-9_.-]+)\]\s*$', config_text):
        names.add(match.group(1))
    return names


def is_model_provider_available(provider: str, config_text: str) -> bool:
    return provider in configured_model_provider_names(config_text)


def parse_current_model(config_text: str) -> str | None:
    match = re.search(r'(?m)^\s*model\s*=\s*"([^"]+)"', config_text)
    return match.group(1) if match else None


def read_auth_mode(paths: Paths) -> str | None:
    auth_path = paths.codex_home / "auth.json"
    if not auth_path.exists():
        return None
    try:
        text = read_text(auth_path)
    except OSError:
        return None
    match = re.search(r'"auth_mode"\s*:\s*"([^"]+)"', text)
    return match.group(1) if match else None


def read_json_file(path: Path, default: object | None = None) -> object:
    if not path.exists():
        return default
    try:
        return json.loads(read_text(path))
    except (json.JSONDecodeError, OSError):
        return default


def parse_config_value(config_text: str, key: str) -> str | None:
    match = re.search(rf'(?m)^\s*{re.escape(key)}\s*=\s*"([^"]*)"', config_text)
    return match.group(1) if match else None


def detect_login_mode(paths: Paths, config_text: str | None = None) -> dict[str, object]:
    if config_text is None and paths.config_path.exists():
        config_text = read_text(paths.config_path)
    config_text = config_text or ""

    auth_mode = read_auth_mode(paths) or ""
    provider = parse_current_provider(config_text).strip()
    base_url = parse_config_value(config_text, "base_url") or ""

    if "127.0.0.1:15721" in base_url or "localhost:15721" in base_url:
        mode = "cc-switch-local-route"
    elif auth_mode == "chatgpt":
        mode = "chatgpt-oauth"
    elif auth_mode:
        mode = f"auth-json-{auth_mode}"
    elif provider and provider != "openai":
        mode = "custom-provider"
    elif provider == "openai":
        mode = "openai-compatible-api"
    else:
        mode = "unknown"

    return {
        "mode": mode,
        "auth_mode_present": bool(auth_mode),
        "config_provider": provider,
        "has_base_url": bool(base_url),
        "uses_local_cc_switch_route": "127.0.0.1:15721" in base_url or "localhost:15721" in base_url,
        "credential_migration": "disabled",
    }


@contextmanager
def connect_db(
    path: Path,
    readonly: bool = False,
    timeout_seconds: float = DEFAULT_DB_TIMEOUT_SECONDS,
    busy_timeout_ms: int | None = None,
) -> Iterator[sqlite3.Connection]:
    if busy_timeout_ms is None:
        busy_timeout_ms = max(1, int(timeout_seconds * 1000))

    if readonly:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=timeout_seconds)
    else:
        conn = sqlite3.connect(str(path), timeout=timeout_seconds)

    try:
        conn.execute(f"PRAGMA busy_timeout = {busy_timeout_ms}")
        conn.row_factory = sqlite3.Row
        yield conn
    finally:
        conn.close()


def ensure_environment(paths: Paths) -> None:
    if not paths.config_path.exists():
        raise RuntimeError(f"Missing config file: {paths.config_path}")
    if not paths.db_path.exists():
        raise RuntimeError(f"Missing database file: {paths.db_path}")


def get_thread_columns(conn: sqlite3.Connection) -> set[str]:
    return {str(row["name"]) for row in conn.execute("PRAGMA table_info(threads)")}


def counts_to_rows(counts: OrderedDict[str, int]) -> list[dict[str, object]]:
    return [{"provider": key, "count": value} for key, value in counts.items()]


def model_counts_to_rows(counts: OrderedDict[str, int]) -> list[dict[str, object]]:
    return [{"model": key, "count": value} for key, value in counts.items()]


def ordered_counts(values: list[str]) -> OrderedDict[str, int]:
    raw_counts: dict[str, int] = {}
    for value in values:
        key = value or "(empty)"
        raw_counts[key] = raw_counts.get(key, 0) + 1

    counts = OrderedDict()
    for key, value in sorted(raw_counts.items(), key=lambda item: (-item[1], item[0])):
        counts[key] = value
    return counts


def elapsed_ms(started_at: float) -> int:
    return int((time.monotonic() - started_at) * 1000)


def query_provider_counts(conn: sqlite3.Connection) -> OrderedDict[str, int]:
    counts = OrderedDict()
    for provider, count in conn.execute(
        """
        SELECT model_provider, COUNT(*)
        FROM threads
        GROUP BY model_provider
        ORDER BY COUNT(*) DESC, model_provider ASC
        """
    ):
        counts[str(provider or "(empty)")] = int(count)
    return counts


def query_model_counts(conn: sqlite3.Connection) -> OrderedDict[str, int]:
    counts = OrderedDict()
    for model, count in conn.execute(
        """
        SELECT model, COUNT(*)
        FROM threads
        GROUP BY model
        ORDER BY COUNT(*) DESC, model ASC
        """
    ):
        counts[str(model or "(empty)")] = int(count)
    return counts


def query_provider_model_counts(conn: sqlite3.Connection) -> list[dict[str, object]]:
    rows = []
    for provider, model, count in conn.execute(
        """
        SELECT model_provider, model, COUNT(*)
        FROM threads
        GROUP BY model_provider, model
        ORDER BY COUNT(*) DESC, model_provider ASC, model ASC
        """
    ):
        rows.append(
            {
                "provider": str(provider or "(empty)"),
                "model": str(model or "(empty)"),
                "count": int(count),
            }
        )
    return rows


def query_cwd_counts(conn: sqlite3.Connection, limit: int = 20) -> list[dict[str, object]]:
    rows = []
    for cwd, count in conn.execute(
        """
        SELECT cwd, COUNT(*)
        FROM threads
        GROUP BY cwd
        ORDER BY COUNT(*) DESC, cwd ASC
        LIMIT ?
        """,
        (limit,),
    ):
        rows.append({"cwd": str(cwd or "(empty)"), "count": int(count)})
    return rows


def query_thread_cwd_counts(conn: sqlite3.Connection) -> OrderedDict[str, int]:
    columns = get_thread_columns(conn)
    if "cwd" not in columns:
        return OrderedDict()
    counts = OrderedDict()
    raw_counts: dict[str, int] = {}
    for cwd, count in conn.execute(
        """
        SELECT cwd, COUNT(*)
        FROM threads
        WHERE cwd IS NOT NULL AND cwd <> ''
        GROUP BY cwd
        ORDER BY COUNT(*) DESC, cwd ASC
        """
    ):
        path = normalize_project_path(cwd)
        if not path:
            continue
        raw_counts[path] = raw_counts.get(path, 0) + int(count)
    for path, count in sorted(raw_counts.items(), key=lambda item: (-item[1], item[0].casefold())):
        counts[path] = count
    return counts


def query_thread_totals(conn: sqlite3.Connection) -> tuple[int, int]:
    columns = get_thread_columns(conn)
    total = int(conn.execute("SELECT COUNT(*) FROM threads").fetchone()[0])
    archived = 0
    if "archived" in columns:
        archived = int(conn.execute("SELECT COUNT(*) FROM threads WHERE archived = 1").fetchone()[0])
    return total, archived


def query_recent_thread_cwds(conn: sqlite3.Connection, limit: int = 50) -> list[str]:
    columns = get_thread_columns(conn)
    if "cwd" not in columns:
        return []
    order_parts = []
    if "updated_at_ms" in columns:
        order_parts.append("updated_at_ms DESC")
    if "updated_at" in columns:
        order_parts.append("updated_at DESC")
    if not order_parts:
        order_parts.append("id DESC")
    rows = conn.execute(
        f"""
        SELECT cwd
        FROM threads
        WHERE cwd IS NOT NULL AND cwd <> ''
        ORDER BY {", ".join(order_parts)}
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [normalize_project_path(row["cwd"]) for row in rows]


def normalize_project_path(value: object) -> str:
    path = str(value or "").strip().rstrip("\\/")
    if path.startswith("\\\\?\\"):
        path = path[4:]
    return path


def is_transient_codex_workspace_root(value: object) -> bool:
    path = normalize_project_path(value).replace("/", "\\")
    if not path:
        return False
    documents_codex = str(Path.home() / "Documents" / "Codex").replace("/", "\\").rstrip("\\")
    relative = ""
    if path.casefold().startswith((documents_codex + "\\").casefold()):
        relative = path[len(documents_codex) + 1 :]
    else:
        match = re.match(r"^[A-Za-z]:\\Users\\[^\\]+\\Documents\\Codex\\(.+)$", path)
        if match:
            relative = match.group(1)
    if not relative:
        return False
    first_part = relative.split("\\", 1)[0]
    return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", first_part))


def filter_project_roots(values: list[object], include_transient: bool = False) -> list[str]:
    roots = dedupe_paths(values)
    if include_transient:
        return roots
    return [root for root in roots if not is_transient_codex_workspace_root(root)]


def project_writable_root_paths(value: object) -> list[object]:
    if isinstance(value, list):
        return list(value)
    if isinstance(value, dict):
        return list(value.keys())
    return []


def build_project_writable_roots(existing: object, roots: list[str]) -> object:
    if isinstance(existing, dict):
        output = {
            normalize_project_path(key): value
            for key, value in existing.items()
            if normalize_project_path(key) and not is_transient_codex_workspace_root(key)
        }
        for root in roots:
            output.setdefault(root, [{"kind": "local", "path": root}])
        return output
    return roots


def dedupe_paths(values: list[object]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        path = normalize_project_path(value)
        if not path:
            continue
        key = path.casefold()
        if key in seen:
            continue
        seen.add(key)
        output.append(path)
    return output


def read_global_state(paths: Paths) -> dict[str, object]:
    payload = read_json_file(paths.global_state_path, {}) or {}
    return payload if isinstance(payload, dict) else {}


def global_state_backup_path(backup_path: Path) -> Path:
    if backup_path.is_dir():
        return backup_path / "codex-global-state.json"
    return backup_path.with_name(f"{backup_path.name}.codex-global-state.json")


def manifest_path(backup_path: Path) -> Path:
    if backup_path.is_dir():
        return backup_path / "manifest.json"
    return backup_path.with_name(f"{backup_path.name}.manifest.json")


def read_backup_manifest(backup_path: Path) -> dict[str, object]:
    payload = read_json_file(manifest_path(backup_path), {}) or {}
    return payload if isinstance(payload, dict) else {}


def db_backup_path(backup_path: Path) -> Path:
    return backup_path / "state_5.sqlite.bak" if backup_path.is_dir() else backup_path


def safe_slug(value: str, fallback: str = "unknown") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip())
    cleaned = cleaned.strip("-._")
    return cleaned[:80] if cleaned else fallback


def safe_filename_part(value: str, fallback: str = "未知") -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "-", value.strip())
    cleaned = re.sub(r"\s+", "-", cleaned).strip("-._ ")
    return cleaned[:80] if cleaned else fallback


def backup_display_dir_name(
    current_provider: str,
    session_count: int,
    project_root_count: int,
) -> str:
    timestamp = datetime.now(tz=UTC).strftime("%Y-%m-%d_%H%M%S")
    hostname = safe_filename_part(socket.gethostname(), "未知电脑")
    provider = safe_filename_part(current_provider or "未知通道", "未知通道")
    return f"{timestamp}-电脑_{hostname}-通道_{provider}-历史{session_count}-项目{project_root_count}"


def unique_backup_dir_path(paths: Paths, base_name: str) -> Path:
    candidate = paths.backup_dir / base_name
    if not candidate.exists():
        return candidate
    for suffix in range(2, 1000):
        candidate = paths.backup_dir / f"{base_name}-{suffix}"
        if not candidate.exists():
            return candidate
    raise RuntimeError("Unable to create a unique backup directory name")


def create_manifest(
    paths: Paths,
    label: str,
    current_provider: str,
    current_model: str | None,
    session_count: int,
    archived_session_count: int,
    changed_session_files: int,
    project_root_count: int,
    notes: str = "",
) -> dict[str, object]:
    login_mode = detect_login_mode(paths)
    return {
        "toolVersion": TOOL_VERSION,
        "upstreamVersion": UPSTREAM_VERSION,
        "createdAt": datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
        "hostname": socket.gethostname(),
        "codexHome": str(paths.codex_home),
        "label": label,
        "displayName": "",
        "loginModeDetected": login_mode["mode"],
        "loginModeDetails": login_mode,
        "targetProvider": current_provider,
        "targetModel": current_model,
        "sessionCount": session_count,
        "archivedSessionCount": archived_session_count,
        "changedSessionFiles": changed_session_files,
        "projectRootCount": project_root_count,
        "notes": notes,
        "credentialPolicy": {
            "authJsonCopied": False,
            "oauthTokensCopied": False,
            "apiKeysCopied": False,
            "refreshTokensCopied": False,
            "ccSwitchDatabaseCopied": False,
        },
    }


def project_roots_from_global_state(state: dict[str, object]) -> list[str]:
    roots: list[object] = []
    for key in (
        "electron-saved-workspace-roots",
        "project-order",
        "active-workspace-roots",
    ):
        value = state.get(key)
        if isinstance(value, list):
            roots.extend(value)
    roots.extend(project_writable_root_paths(state.get("project-writable-roots")))
    return filter_project_roots(roots)


def collect_project_roots(
    paths: Paths,
    conn: sqlite3.Connection | None = None,
    include_transient: bool = False,
) -> list[str]:
    roots: list[object] = project_roots_from_global_state(read_global_state(paths))
    if conn is not None:
        roots.extend(query_thread_cwd_counts(conn).keys())
    return filter_project_roots(roots, include_transient=include_transient)


def diagnose_projects(paths: Paths) -> dict[str, object]:
    state = read_global_state(paths)
    with connect_db(paths.db_path, readonly=True) as conn:
        cwd_counts = query_thread_cwd_counts(conn)
        recent_cwds = query_recent_thread_cwds(conn, limit=50)

    saved_roots = filter_project_roots(state.get("electron-saved-workspace-roots", []) if isinstance(state.get("electron-saved-workspace-roots"), list) else [])
    order_roots = filter_project_roots(state.get("project-order", []) if isinstance(state.get("project-order"), list) else [])
    active_roots = filter_project_roots(state.get("active-workspace-roots", []) if isinstance(state.get("active-workspace-roots"), list) else [])
    writable_roots = filter_project_roots(project_writable_root_paths(state.get("project-writable-roots")))

    local_projects = state.get("local-projects")
    local_project_paths: list[str] = []
    if isinstance(local_projects, list):
        for item in local_projects:
            if isinstance(item, str):
                local_project_paths.append(item)
            elif isinstance(item, dict):
                local_project_paths.append(str(item.get("path") or item.get("root") or item.get("cwd") or ""))
    local_project_paths = dedupe_paths(local_project_paths)
    saved_keys = {root.casefold() for root in saved_roots}
    duplicate_local = [path for path in local_project_paths if path.casefold() in saved_keys]

    all_roots = filter_project_roots([*saved_roots, *order_roots, *active_roots, *writable_roots, *cwd_counts.keys()])
    recent_set = {normalize_project_path(path).casefold() for path in recent_cwds}
    projects = []
    for root in all_roots:
        thread_count = int(cwd_counts.get(root, 0))
        projects.append(
            {
                "path": root,
                "exists": Path(root).exists(),
                "thread_count": thread_count,
                "recent_thread_count": 1 if root.casefold() in recent_set else 0,
                "message": "项目存在但未找到项目归属线程" if Path(root).exists() and thread_count == 0 else "",
            }
        )

    return {
        "global_state_path": str(paths.global_state_path),
        "global_state_exists": paths.global_state_path.exists(),
        "project_root_count": len(all_roots),
        "saved_workspace_root_count": len(saved_roots),
        "project_order_count": len(order_roots),
        "active_workspace_root_count": len(active_roots),
        "project_writable_root_count": len(writable_roots),
        "local_project_count": len(local_project_paths),
        "duplicate_local_project_paths": duplicate_local,
        "recent_50_project_thread_count": len([path for path in recent_cwds if normalize_project_path(path)]),
        "projects": projects,
    }


def remove_duplicate_local_projects(local_projects: object, path_keys: set[str]) -> tuple[object, int]:
    if not isinstance(local_projects, list):
        return local_projects, 0
    cleaned = []
    removed = 0
    for item in local_projects:
        path = ""
        if isinstance(item, str):
            path = item
        elif isinstance(item, dict):
            path = str(item.get("path") or item.get("root") or item.get("cwd") or "")
        if normalize_project_path(path).casefold() in path_keys:
            removed += 1
            continue
        cleaned.append(item)
    return cleaned, removed


def repair_projects(paths: Paths, extra_roots: list[object] | None = None) -> dict[str, object]:
    ensure_environment(paths)
    state = read_global_state(paths)
    if not state:
        state = {}
    with connect_db(paths.db_path, readonly=True) as conn:
        thread_roots = list(query_thread_cwd_counts(conn).keys())
    all_thread_roots = filter_project_roots([*thread_roots, *(extra_roots or [])])

    before = diagnose_projects(paths)
    existing_saved = state.get("electron-saved-workspace-roots", [])
    existing_order = state.get("project-order", [])
    existing_active = state.get("active-workspace-roots", [])
    existing_writable = state.get("project-writable-roots", [])

    saved_roots = filter_project_roots([*(existing_saved if isinstance(existing_saved, list) else []), *all_thread_roots])
    order_roots = filter_project_roots([*(existing_order if isinstance(existing_order, list) else []), *saved_roots])
    active_roots = filter_project_roots(existing_active if isinstance(existing_active, list) else [])
    writable_roots = filter_project_roots([*project_writable_root_paths(existing_writable), *saved_roots])

    saved_keys = {path.casefold() for path in saved_roots}
    cleaned_local_projects, removed_local_projects = remove_duplicate_local_projects(state.get("local-projects"), saved_keys)

    state["electron-saved-workspace-roots"] = saved_roots
    state["project-order"] = order_roots
    if active_roots:
        state["active-workspace-roots"] = active_roots
    state["project-writable-roots"] = build_project_writable_roots(existing_writable, writable_roots)
    if "local-projects" in state:
        state["local-projects"] = cleaned_local_projects

    paths.global_state_path.parent.mkdir(parents=True, exist_ok=True)
    write_text_exact(paths.global_state_path, json.dumps(state, ensure_ascii=False, indent=2) + "\n")
    after = diagnose_projects(paths)
    return {
        "action": "repair-projects",
        "global_state_path": str(paths.global_state_path),
        "added_saved_workspace_roots": max(0, after["saved_workspace_root_count"] - before["saved_workspace_root_count"]),
        "added_project_order_roots": max(0, after["project_order_count"] - before["project_order_count"]),
        "added_project_writable_roots": max(0, after["project_writable_root_count"] - before["project_writable_root_count"]),
        "removed_local_project_duplicates": removed_local_projects,
        "before": before,
        "after": after,
    }


def merge_global_state_from_backup(paths: Paths, backup_path: Path) -> dict[str, object]:
    source_path = global_state_backup_path(backup_path)
    if not source_path.exists():
        return {"global_state_merged": False, "reason": "no global state backup"}

    current = read_global_state(paths)
    source = read_json_file(source_path, {}) or {}
    if not isinstance(source, dict):
        return {"global_state_merged": False, "reason": "invalid global state backup"}

    before = diagnose_projects(paths)
    roots = dedupe_paths(
        [
            *project_roots_from_global_state(source),
            *project_roots_from_global_state(current),
        ]
    )
    for key in ("electron-saved-workspace-roots", "project-order", "project-writable-roots"):
        existing = current.get(key)
        if key == "project-writable-roots":
            writable_roots = filter_project_roots([*project_writable_root_paths(existing), *roots])
            current[key] = build_project_writable_roots(existing, writable_roots)
        else:
            current[key] = filter_project_roots([*(existing if isinstance(existing, list) else []), *roots])

    source_active = source.get("active-workspace-roots")
    current_active = current.get("active-workspace-roots")
    active_roots = filter_project_roots(
        [
            *(current_active if isinstance(current_active, list) else []),
            *(source_active if isinstance(source_active, list) else []),
        ]
    )
    if active_roots:
        current["active-workspace-roots"] = active_roots

    path_keys = {path.casefold() for path in current.get("electron-saved-workspace-roots", []) if isinstance(path, str)}
    if "local-projects" in current:
        current["local-projects"], removed = remove_duplicate_local_projects(current.get("local-projects"), path_keys)
    else:
        removed = 0

    paths.global_state_path.parent.mkdir(parents=True, exist_ok=True)
    write_text_exact(paths.global_state_path, json.dumps(current, ensure_ascii=False, indent=2) + "\n")
    repair_summary = repair_projects(paths)
    after = diagnose_projects(paths)
    return {
        "global_state_merged": True,
        "source": str(source_path),
        "merged_project_roots": len(roots),
        "removed_local_project_duplicates": removed + int(repair_summary["removed_local_project_duplicates"]),
        "before": before,
        "after": after,
    }


def query_recent_provider(conn: sqlite3.Connection, current_model: str | None) -> str | None:
    columns = get_thread_columns(conn)
    order_parts = []
    if "updated_at_ms" in columns:
        order_parts.append("updated_at_ms DESC")
    if "updated_at" in columns:
        order_parts.append("updated_at DESC")
    if not order_parts:
        order_parts.append("id DESC")

    where_parts = ["model_provider IS NOT NULL", "model_provider <> ''"]
    params: list[str] = []
    if current_model and "model" in columns:
        where_parts.append("model = ?")
        params.append(current_model)

    row = conn.execute(
        f"""
        SELECT model_provider
        FROM threads
        WHERE {" AND ".join(where_parts)}
        ORDER BY {", ".join(order_parts)}
        LIMIT 1
        """,
        params,
    ).fetchone()
    return str(row["model_provider"]) if row and row["model_provider"] else None


def resolve_current_provider(
    paths: Paths,
    config_text: str,
    conn: sqlite3.Connection,
    current_model: str | None,
) -> tuple[str, str]:
    config_provider = parse_current_provider(config_text).strip()
    if config_provider:
        if is_model_provider_available(config_provider, config_text):
            return config_provider, "config.toml"
        auth_mode = read_auth_mode(paths)
        if auth_mode == "chatgpt":
            return "openai", f"auth.json-invalid-config-provider-{config_provider}"

    auth_mode = read_auth_mode(paths)
    counts = query_provider_counts(conn)
    if auth_mode == "chatgpt":
        # Codex Desktop's ChatGPT/Plus account login stores local threads under
        # the openai provider bucket, while API-key profiles usually write
        # model_provider explicitly in config.toml.
        if not counts or "openai" in counts:
            return "openai", "auth.json"

    recent_provider = query_recent_provider(conn, current_model)
    if recent_provider:
        if is_model_provider_available(recent_provider, config_text):
            return recent_provider, "recent_thread"
        if auth_mode == "chatgpt":
            return "openai", f"auth.json-invalid-recent-provider-{recent_provider}"

    if len(counts) == 1:
        only_provider = next(iter(counts))
        if is_model_provider_available(only_provider, config_text):
            return only_provider, "only_database_provider"
        if auth_mode == "chatgpt":
            return "openai", f"auth.json-invalid-database-provider-{only_provider}"

    if config_provider:
        raise RuntimeError(
            f"config.toml 里写了 model_provider = \"{config_provider}\"，"
            f"但没有找到 [model_providers.{config_provider}] 配置。"
            "为避免把历史写成 Codex 无法加载的 provider，本次同步已停止。"
            "请先在 Codex/CC Switch 中恢复这个 provider 配置，或切回 OpenAI Official 后再同步。"
        )

    raise RuntimeError(
        "Could not determine current model_provider. config.toml has no model_provider, "
        "auth.json is not a ChatGPT/Plus login, and no provider could be inferred from the database."
    )


def provider_unresolved_message(error: object | None = None) -> str:
    detail = str(error or "").strip()
    message = (
        "当前无法判断 Codex 正在使用哪个 provider。"
        "常见原因是 CC Switch 仍在运行时又通过启动器切换通道，"
        "导致 config.toml 没有 model_provider，auth.json 也不是 ChatGPT 登录，"
        "本地数据库暂时推不出 provider。"
        "请先关闭 Codex Desktop 和 CC Switch，再用启动器重新选择通道启动 Codex；"
        "如果只是想保留现场，可以先手动备份。"
    )
    return f"{message} 原始错误：{detail}" if detail else message


def count_mismatched(conn: sqlite3.Connection, column: str, expected: str | None) -> int | None:
    if expected is None:
        return None
    return int(
        conn.execute(
            f"SELECT COUNT(*) FROM threads WHERE {column} IS NULL OR {column} <> ?",
            (expected,),
        ).fetchone()[0]
    )


def query_id_set(conn: sqlite3.Connection, query: str, params: tuple[object, ...] = ()) -> set[str]:
    return {str(row["id"]) for row in conn.execute(query, params)}


def archived_visibility_repair_condition(columns: set[str]) -> str:
    conditions: list[str] = []
    if "cwd" in columns:
        conditions.append("cwd LIKE '\\\\?\\%'")
    if {"has_user_event", "first_user_message"}.issubset(columns):
        conditions.append(
            """
            (
              COALESCE(has_user_event, 0) = 0
              AND COALESCE(TRIM(first_user_message), '') <> ''
            )
            """
        )
    if not conditions:
        return "0"
    return f"COALESCE(archived, 0) <> 0 AND ({' OR '.join(conditions)})"


def query_visibility_candidates(
    conn: sqlite3.Connection,
    columns: set[str],
) -> tuple[set[str], dict[str, int]]:
    ids: set[str] = set()
    counts = {
        "cwd_prefix_threads": 0,
        "missing_user_event_threads": 0,
        "archived_threads": 0,
    }

    if "cwd" in columns:
        cwd_ids = query_id_set(conn, "SELECT id FROM threads WHERE cwd LIKE '\\\\?\\%'")
        counts["cwd_prefix_threads"] = len(cwd_ids)
        ids |= cwd_ids

    if {"has_user_event", "first_user_message"}.issubset(columns):
        user_event_ids = query_id_set(
            conn,
            """
            SELECT id
            FROM threads
            WHERE COALESCE(has_user_event, 0) = 0
              AND COALESCE(TRIM(first_user_message), '') <> ''
            """,
        )
        counts["missing_user_event_threads"] = len(user_event_ids)
        ids |= user_event_ids

    if "archived" in columns:
        archived_ids = query_id_set(
            conn,
            f"SELECT id FROM threads WHERE {archived_visibility_repair_condition(columns)}",
        )
        counts["archived_threads"] = len(archived_ids)
        ids |= archived_ids

    return ids, counts


def list_backups(paths: Paths, limit: int = 20) -> list[dict[str, str]]:
    if not paths.backup_dir.exists():
        return []
    candidates = [
        *[item for item in paths.backup_dir.iterdir() if item.is_dir() and (item / "manifest.json").exists()],
        *paths.backup_dir.glob("state_5.sqlite.*.bak"),
    ]
    files = sorted(candidates, key=lambda item: item.stat().st_mtime, reverse=True)
    output = []
    for item in files[:limit]:
        manifest = read_backup_manifest(item)
        output.append(
            {
                "name": item.name,
                "display_name": str(manifest.get("displayName") or item.name),
                "path": str(item),
                "modified_at": datetime.fromtimestamp(item.stat().st_mtime).isoformat(timespec="seconds"),
                "notes": str(manifest.get("notes") or ""),
                "target_provider": str(manifest.get("targetProvider") or ""),
                "login_mode_detected": str(manifest.get("loginModeDetected") or ""),
                "session_count": str(manifest.get("sessionCount") or ""),
                "project_root_count": str(manifest.get("projectRootCount") or ""),
            }
        )
    return output


def close_codex_desktop() -> dict[str, object]:
    commands = [
        ["taskkill", "/IM", "Codex.exe", "/T", "/F"],
        ["taskkill", "/IM", "OpenAI.Codex.exe", "/T", "/F"],
    ]
    attempts = []
    closed_any = False
    for command in commands:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=15)
        output = (completed.stdout or completed.stderr or "").strip()
        if completed.returncode == 0:
            closed_any = True
        attempts.append(
            {
                "command": " ".join(command),
                "returncode": completed.returncode,
                "output": output,
            }
        )
    return {"attempted": True, "closed_any": closed_any, "attempts": attempts}


def existing_one_click_homes(mode: str) -> list[tuple[str, Paths]]:
    homes = []
    for name, paths in resolve_one_click_homes(mode):
        if paths.codex_home.exists():
            homes.append((name, paths))
    return homes


def collect_roots_from_homes(homes: list[tuple[str, Paths]]) -> list[str]:
    roots: list[object] = []
    for _, paths in homes:
        if not paths.db_path.exists():
            continue
        roots.extend(project_roots_from_global_state(read_global_state(paths)))
        with connect_db(paths.db_path, readonly=True) as conn:
            roots.extend(query_thread_cwd_counts(conn).keys())
    return dedupe_paths(roots)


def one_click_home_manifest(
    name: str,
    paths: Paths,
    status: dict[str, object] | None,
) -> dict[str, object]:
    spec = CODEX_HOME_SPECS[name]
    login_mode = status.get("login_mode") if status else None
    return {
        "name": name,
        "path": str(paths.codex_home),
        "exists": paths.codex_home.exists(),
        "login_mode": (
            login_mode.get("mode")
            if isinstance(login_mode, dict)
            else spec["login_mode"]
        ),
        "history_count": int(status.get("total_threads") or 0) if status else 0,
        "project_count": int((status.get("project_diagnostics") or {}).get("project_root_count") or 0)
        if status
        else 0,
        "credential_files_excluded": True,
    }


def add_launcher_compatible_manifest_fields(
    manifest: dict[str, object],
    mode: str,
    codex_homes: list[dict[str, object]],
) -> None:
    manifest["toolVersion"] = TOOL_VERSION
    manifest["oneClickSafeSync"] = True
    manifest["mode"] = mode
    manifest["codexHomes"] = codex_homes
    manifest["safetyPolicy"] = {
        "copyAuthJson": False,
        "copyOAuthToken": False,
        "copyApiKey": False,
        "copyRefreshToken": False,
        "copyCCSwitchDatabase": False,
        "overwriteGlobalState": False,
        "mergeGlobalStateOnly": True,
    }


def append_one_click_manifest(
    backup_path: Path,
    mode: str,
    codex_homes: list[dict[str, object]],
) -> None:
    manifest = read_backup_manifest(backup_path)
    add_launcher_compatible_manifest_fields(manifest, mode, codex_homes)
    write_text_exact(manifest_path(backup_path), json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")


def process_one_click_home(
    name: str,
    paths: Paths,
    mode: str,
    all_roots: list[str],
    codex_homes_manifest: list[dict[str, object]],
    fix_projects: bool,
    sync_history: bool,
) -> dict[str, object]:
    spec = CODEX_HOME_SPECS[name]
    if not paths.codex_home.exists():
        return {
            "name": name,
            "display": spec["display"],
            "path": str(paths.codex_home),
            "exists": False,
            "skipped": True,
            "message": f"未检测到 {paths.codex_home.name}，跳过{spec['display']}。",
        }
    if not paths.config_path.exists() or not paths.db_path.exists():
        missing = []
        if not paths.config_path.exists():
            missing.append("config.toml")
        if not paths.db_path.exists():
            missing.append("state_5.sqlite")
        return {
            "name": name,
            "display": spec["display"],
            "path": str(paths.codex_home),
            "exists": True,
            "skipped": True,
            "message": f"{paths.codex_home.name} 缺少 {'、'.join(missing)}，跳过{spec['display']}。",
        }
    ensure_environment(paths)
    status_before = safe_status_for_one_click(paths)
    backup_path = make_backup(paths, f"one-click-safe-sync-{mode}")
    append_one_click_manifest(backup_path, mode, codex_homes_manifest)

    project_summary = repair_projects(paths, all_roots) if fix_projects else {}
    sync_summary: dict[str, object] = {}
    sync_error = ""
    if sync_history:
        try:
            sync_summary = sync_to_current_provider(paths, create_backup=False)
        except RuntimeError as exc:
            sync_error = str(exc)

    status_after = safe_status_for_one_click(paths)
    return {
        "name": name,
        "display": spec["display"],
        "path": str(paths.codex_home),
        "exists": True,
        "skipped": False,
        "backup_path": str(backup_path),
        "history_before": status_before.get("total_threads"),
        "history_after": status_after.get("total_threads"),
        "projects_before": (status_before.get("project_diagnostics") or {}).get("project_root_count"),
        "projects_after": (status_after.get("project_diagnostics") or {}).get("project_root_count"),
        "project_repair": project_summary,
        "history_sync": sync_summary,
        "history_sync_error": sync_error,
        "login_mode": status_after.get("login_mode"),
    }


def safe_status_for_one_click(paths: Paths) -> dict[str, object]:
    try:
        return get_status(paths)
    except RuntimeError as exc:
        diagnostics = diagnose_projects(paths)
        with connect_db(paths.db_path, readonly=True) as conn:
            total_threads, _ = query_thread_totals(conn)
        config_text = read_text(paths.config_path) if paths.config_path.exists() else ""
        return {
            "codex_home": str(paths.codex_home),
            "current_provider": parse_current_provider(config_text) or "未知",
            "current_provider_source": "invalid-or-incomplete-config",
            "current_model": parse_current_model(config_text),
            "login_mode": detect_login_mode(paths, config_text),
            "total_threads": total_threads,
            "project_diagnostics": diagnostics,
            "status_error": str(exc),
        }


def one_click_safe_sync(
    mode: str = "auto",
    close_codex: bool = True,
    backup: bool = True,
    fix_projects: bool = True,
    no_credentials: bool = True,
    merge_global_state: bool = True,
    sync_history: bool = True,
) -> dict[str, object]:
    if not backup:
        raise RuntimeError("一键安全修复必须先备份，不能关闭 --backup。")
    if not no_credentials:
        raise RuntimeError("一键安全修复不支持复制或恢复任何凭据。")
    if not merge_global_state:
        raise RuntimeError("一键安全修复只能 merge .codex-global-state.json，不能整文件覆盖。")

    started_at = time.monotonic()
    close_summary = close_codex_desktop() if close_codex else {"attempted": False, "closed_any": False, "attempts": []}
    homes = existing_one_click_homes(mode)
    all_roots = collect_roots_from_homes(homes)

    codex_homes_manifest: list[dict[str, object]] = []
    for name, paths in resolve_one_click_homes(mode):
        status = safe_status_for_one_click(paths) if paths.config_path.exists() and paths.db_path.exists() else None
        codex_homes_manifest.append(one_click_home_manifest(name, paths, status))

    results = [
        process_one_click_home(
            name,
            paths,
            mode,
            all_roots,
            codex_homes_manifest,
            fix_projects,
            sync_history,
        )
        for name, paths in resolve_one_click_homes(mode)
    ]

    backed_up = [item for item in results if item.get("backup_path")]
    repaired = [item for item in results if item.get("project_repair")]
    synced = [item for item in results if item.get("history_sync")]
    sync_errors = [item for item in results if item.get("history_sync_error")]
    skipped = [item for item in results if item.get("skipped")]
    summary_lines = [
        "本次已完成：",
        "- 已尝试关闭 Codex Desktop。" if close_codex else "- 未请求关闭 Codex Desktop。",
        f"- 已备份 {len(backed_up)} 个 Codex Home。",
        f"- 已修复 {len(repaired)} 个目录的项目列表。",
        f"- 已整理 {len(synced)} 个目录的历史可见性。",
        "- 已合并 workspace roots / project-order / project-writable-roots。",
        "- 未复制任何 auth.json、token、API key 或 CCSwitch 数据库。",
    ]
    for item in skipped:
        summary_lines.append(f"- {item.get('message')}")
    for item in sync_errors:
        summary_lines.append(f"- {item.get('display')}历史可见性整理已跳过：{item.get('history_sync_error')}")
    for item in backed_up:
        summary_lines.append(f"- 备份位置（{item.get('display')}）：{item.get('backup_path')}")

    return {
        "action": "one-click-safe-sync",
        "mode": mode,
        "close_codex": close_summary,
        "codex_homes": codex_homes_manifest,
        "results": results,
        "backup_paths": [str(item["backup_path"]) for item in backed_up],
        "summary": "\n".join(summary_lines),
        "safety_policy": {
            "copy_auth_json": False,
            "copy_oauth_token": False,
            "copy_api_key": False,
            "copy_refresh_token": False,
            "copy_ccswitch_database": False,
            "overwrite_global_state": False,
            "merge_global_state_only": True,
        },
        "timing": {"total_ms": elapsed_ms(started_at)},
    }


def split_first_line(text: str) -> tuple[str, str, str]:
    for ending in ("\r\n", "\n", "\r"):
        index = text.find(ending)
        if index >= 0:
            return text[:index], ending, text[index + len(ending) :]
    return text, "", ""


def replace_first_line(path: Path, first_line: str) -> None:
    text = read_text_exact(path)
    _, ending, remainder = split_first_line(text)
    if ending:
        new_text = first_line + ending + remainder
    elif text:
        new_text = first_line
    else:
        new_text = first_line + "\n"
    write_text_exact(path, new_text)


def session_index_backup_path(backup_path: Path) -> Path:
    if backup_path.is_dir():
        return backup_path / "session_index.jsonl"
    return backup_path.with_name(f"{backup_path.name}.session_index.jsonl")


def session_meta_backup_path(backup_path: Path) -> Path:
    if backup_path.is_dir():
        return backup_path / "session_meta.json"
    return backup_path.with_name(f"{backup_path.name}.session_meta.json")


def iter_session_paths(paths: Paths) -> list[Path]:
    output: list[Path] = []
    for directory in (paths.sessions_dir, paths.archived_sessions_dir):
        if directory.exists():
            output.extend(directory.rglob("rollout-*.jsonl"))
    return sorted(output)


def parse_session_record(path: Path) -> SessionRecord | None:
    if not SESSION_FILENAME_PATTERN.search(path.name):
        return None

    with path.open("r", encoding="utf-8", newline="") as handle:
        first_line = handle.readline()

    if not first_line:
        return None

    item = json.loads(first_line.rstrip("\r\n"))
    if item.get("type") != "session_meta":
        return None

    payload = item.get("payload")
    if not isinstance(payload, dict):
        return None

    thread_id = str(payload.get("id") or "").strip()
    if not thread_id:
        return None

    model_provider = str(payload.get("model_provider") or "")
    raw_model = payload.get("model")
    model = str(raw_model) if raw_model else None
    return SessionRecord(thread_id=thread_id, path=path, model_provider=model_provider, model=model)


def iter_session_meta_items(path: Path) -> Iterator[dict[str, object]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        for line in handle:
            if '"session_meta"' not in line:
                continue
            try:
                item = json.loads(line.rstrip("\r\n"))
            except json.JSONDecodeError:
                continue
            if item.get("type") == "session_meta" and isinstance(item.get("payload"), dict):
                yield item


def scan_session_records(paths: Paths) -> list[SessionRecord]:
    records: list[SessionRecord] = []
    for path in iter_session_paths(paths):
        record = parse_session_record(path)
        if record:
            records.append(record)
    return records


def scan_session_meta_stats(
    paths: Paths,
    current_provider: str | None = None,
    current_model: str | None = None,
) -> SessionMetaStats:
    providers: list[str] = []
    models: list[str] = []
    mismatched_files = 0
    mismatched_entries = 0
    mismatched_thread_ids: set[str] = set()
    provider_mismatched_entries = 0
    provider_mismatched_thread_ids: set[str] = set()

    for path in iter_session_paths(paths):
        file_mismatched = False
        for item in iter_session_meta_items(path):
            payload = item["payload"]
            assert isinstance(payload, dict)
            provider = str(payload.get("model_provider") or "")
            raw_model = payload.get("model")
            model = str(raw_model) if raw_model else "(empty)"
            thread_id = str(payload.get("id") or "").strip()
            providers.append(provider)
            models.append(model)
            if current_provider is not None and provider != current_provider:
                mismatched_entries += 1
                provider_mismatched_entries += 1
                if thread_id:
                    mismatched_thread_ids.add(thread_id)
                    provider_mismatched_thread_ids.add(thread_id)
                file_mismatched = True
            elif current_model is not None and model != current_model:
                mismatched_entries += 1
                if thread_id:
                    mismatched_thread_ids.add(thread_id)
                file_mismatched = True
        if file_mismatched:
            mismatched_files += 1

    return SessionMetaStats(
        provider_counts=ordered_counts(providers),
        model_counts=ordered_counts(models),
        mismatched_files=mismatched_files,
        mismatched_entries=mismatched_entries,
        mismatched_thread_ids=mismatched_thread_ids,
        provider_mismatched_entries=provider_mismatched_entries,
        provider_mismatched_thread_ids=provider_mismatched_thread_ids,
    )


def read_session_index(paths: Paths) -> OrderedDict[str, dict[str, object]]:
    entries: OrderedDict[str, dict[str, object]] = OrderedDict()
    if not paths.session_index_path.exists():
        return entries

    for line in read_text(paths.session_index_path).splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        if not isinstance(entry, dict):
            continue
        thread_id = str(entry.get("id") or "").strip()
        if not thread_id:
            continue
        entry["id"] = thread_id
        entry["thread_name"] = str(entry.get("thread_name") or thread_id)
        entry["updated_at"] = str(entry.get("updated_at") or "")
        entries[thread_id] = entry
    return entries


def write_session_index(paths: Paths, entries: list[dict[str, object]]) -> None:
    lines = [json.dumps(entry, ensure_ascii=False, separators=(",", ":")) for entry in entries]
    content = "\n".join(lines)
    if content:
        content += "\n"
    write_text_exact(paths.session_index_path, content)


def iso_utc_from_unix(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, tz=UTC).isoformat().replace("+00:00", "Z")


def parse_index_timestamp(value: str) -> datetime:
    if not value:
        return datetime.fromtimestamp(0, tz=UTC)
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def snapshot_metadata(paths: Paths, backup_path: Path) -> None:
    if paths.session_index_path.exists():
        write_text_exact(session_index_backup_path(backup_path), read_text_exact(paths.session_index_path))

    if paths.global_state_path.exists():
        write_text_exact(global_state_backup_path(backup_path), read_text_exact(paths.global_state_path))

    items: list[dict[str, str]] = []
    for path in iter_session_paths(paths):
        try:
            relative_path = path.relative_to(paths.codex_home)
        except ValueError:
            relative_path = path

        meta_lines: list[dict[str, object]] = []
        with path.open("r", encoding="utf-8", newline="") as handle:
            for line_number, line in enumerate(handle, start=1):
                line_body = line.rstrip("\r\n")
                if '"session_meta"' not in line_body:
                    continue
                try:
                    item = json.loads(line_body)
                except json.JSONDecodeError:
                    continue
                if item.get("type") == "session_meta":
                    meta_lines.append({"line_number": line_number, "line": line_body})

        if meta_lines:
            items.append({"path": str(relative_path), "meta_lines": meta_lines})

    write_text_exact(
        session_meta_backup_path(backup_path),
        json.dumps(items, ensure_ascii=False, indent=2) + "\n",
    )


def restore_metadata(paths: Paths, backup_path: Path) -> dict[str, object]:
    started_at = time.monotonic()
    session_index_restored = False
    session_files_restored = 0
    global_state_summary = merge_global_state_from_backup(paths, backup_path)

    index_backup = session_index_backup_path(backup_path)
    if index_backup.exists():
        write_text_exact(paths.session_index_path, read_text_exact(index_backup))
        session_index_restored = True

    meta_backup = session_meta_backup_path(backup_path)
    if meta_backup.exists():
        for item in json.loads(read_text(meta_backup)):
            raw_path = Path(item["path"])
            path = raw_path if raw_path.is_absolute() else paths.codex_home / raw_path
            if not path.exists():
                continue
            if "meta_lines" not in item:
                # 兼容旧备份格式：旧版只保存首行 session_meta。
                replace_first_line(path, str(item["first_line"]))
                session_files_restored += 1
                continue

            lines = read_text_exact(path).splitlines(keepends=True)
            for meta_item in item["meta_lines"]:
                line_number = int(meta_item["line_number"])
                if line_number <= 0 or line_number > len(lines):
                    continue
                existing = lines[line_number - 1]
                existing_body = existing.rstrip("\r\n")
                existing_ending = existing[len(existing_body) :]
                lines[line_number - 1] = str(meta_item["line"]) + existing_ending
            write_text_exact(path, "".join(lines))
            session_files_restored += 1

    return {
        "session_index_restored": session_index_restored,
        "session_files_restored": session_files_restored,
        "global_state": global_state_summary,
        "duration_ms": elapsed_ms(started_at),
    }


def rebuild_session_index(paths: Paths, conn: sqlite3.Connection) -> dict[str, int]:
    started_at = time.monotonic()
    existing_entries = read_session_index(paths)
    columns = get_thread_columns(conn)
    select_parts = ["id"]
    if "title" in columns:
        select_parts.append("title")
    if "updated_at" in columns:
        select_parts.append("updated_at")
    where_sql = "WHERE archived = 0" if "archived" in columns else ""
    db_rows = conn.execute(
        f"""
        SELECT {", ".join(select_parts)}
        FROM threads
        {where_sql}
        ORDER BY id ASC
        """
    ).fetchall()
    db_ids = {str(row["id"]) for row in db_rows}
    existing_ids = set(existing_entries)

    known_thread_ids = query_id_set(conn, "SELECT id FROM threads")
    merged: list[dict[str, object]] = []
    for row in db_rows:
        thread_id = str(row["id"])
        existing_entry = existing_entries.get(thread_id)
        title = str(row["title"]) if "title" in columns and row["title"] else thread_id
        updated_at = int(row["updated_at"]) if "updated_at" in columns and row["updated_at"] else 0
        entry = dict(existing_entry or {})
        entry["id"] = thread_id
        entry["thread_name"] = str(entry.get("thread_name") or title)
        entry["updated_at"] = iso_utc_from_unix(updated_at)
        merged.append(entry)

    preserved_index_only_entries = 0
    for thread_id, entry in existing_entries.items():
        if thread_id not in db_ids and thread_id not in known_thread_ids:
            merged.append(entry)
            preserved_index_only_entries += 1

    merged.sort(key=lambda item: (parse_index_timestamp(item["updated_at"]), item["id"]))
    write_session_index(paths, merged)

    return {
        "rewritten_index_entries": len(merged),
        "missing_session_index_entries_before": len(db_ids - existing_ids),
        "preserved_index_only_entries": preserved_index_only_entries,
        "duration_ms": elapsed_ms(started_at),
    }


def sync_session_records(paths: Paths, current_provider: str, current_model: str | None) -> dict[str, object]:
    started_at = time.monotonic()
    before_records = scan_session_records(paths)
    before_meta_stats = scan_session_meta_stats(paths, current_provider, current_model)
    updated_session_files = 0
    updated_session_meta_entries = 0
    skipped_busy_session_files = 0

    for path in iter_session_paths(paths):
        text = read_text_exact(path)
        lines = text.splitlines(keepends=True)
        changed = False
        new_lines: list[str] = []

        for line in lines:
            line_body = line.rstrip("\r\n")
            line_ending = line[len(line_body) :]
            if '"session_meta"' not in line_body:
                new_lines.append(line)
                continue
            try:
                item = json.loads(line_body)
            except json.JSONDecodeError:
                new_lines.append(line)
                continue
            if item.get("type") != "session_meta":
                new_lines.append(line)
                continue
            payload = item.get("payload")
            if not isinstance(payload, dict):
                new_lines.append(line)
                continue

            model_matches = current_model is None or payload.get("model") == current_model
            if payload.get("model_provider") == current_provider and model_matches:
                new_lines.append(line)
                continue

            payload["model_provider"] = current_provider
            if current_model:
                payload["model"] = current_model
            new_lines.append(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + line_ending)
            changed = True
            updated_session_meta_entries += 1

        if changed:
            try:
                write_text_exact(path, "".join(new_lines))
                updated_session_files += 1
            except RuntimeError as exc:
                if "File is busy and could not be replaced" not in str(exc):
                    raise
                skipped_busy_session_files += 1

    after_records = scan_session_records(paths)
    after_meta_stats = scan_session_meta_stats(paths, current_provider, current_model)
    return {
        "updated_session_files": updated_session_files,
        "updated_session_meta_entries": updated_session_meta_entries,
        "skipped_busy_session_files": skipped_busy_session_files,
        "session_before_counts": counts_to_rows(
            ordered_counts([record.model_provider for record in before_records])
        ),
        "session_after_counts": counts_to_rows(
            ordered_counts([record.model_provider for record in after_records])
        ),
        "session_before_model_counts": model_counts_to_rows(
            ordered_counts([record.model or "(empty)" for record in before_records])
        ),
        "session_after_model_counts": model_counts_to_rows(
            ordered_counts([record.model or "(empty)" for record in after_records])
        ),
        "all_session_meta_before_counts": counts_to_rows(before_meta_stats.provider_counts),
        "all_session_meta_after_counts": counts_to_rows(after_meta_stats.provider_counts),
        "all_session_meta_before_model_counts": model_counts_to_rows(before_meta_stats.model_counts),
        "all_session_meta_after_model_counts": model_counts_to_rows(after_meta_stats.model_counts),
        "mismatched_session_meta_files_before": before_meta_stats.mismatched_files,
        "mismatched_session_meta_files_after": after_meta_stats.mismatched_files,
        "mismatched_session_meta_entries_before": before_meta_stats.mismatched_entries,
        "mismatched_session_meta_entries_after": after_meta_stats.mismatched_entries,
        "duration_ms": elapsed_ms(started_at),
    }


def is_locked_error(exc: sqlite3.OperationalError) -> bool:
    message = str(exc).lower()
    return (
        "database is locked" in message
        or "database table is locked" in message
        or "database is busy" in message
        or "destination database is in use" in message
    )


def checkpoint(conn: sqlite3.Connection, mode: str = SYNC_CHECKPOINT_MODE) -> tuple[int, int, int]:
    row = conn.execute(f"PRAGMA wal_checkpoint({mode})").fetchone()
    return int(row[0]), int(row[1]), int(row[2])


def update_provider_assignments(
    paths: Paths,
    current_provider: str,
    current_model: str | None,
) -> dict[str, object]:
    started_at = time.monotonic()
    last_error: sqlite3.OperationalError | None = None

    for attempt in range(1, WRITE_LOCK_RETRY_LIMIT + 1):
        try:
            with connect_db(
                paths.db_path,
                readonly=False,
                timeout_seconds=WRITE_OPERATION_TIMEOUT_SECONDS,
            ) as conn:
                # 显式拿写锁，把等待控制在我们自己的重试节奏里。
                conn.execute("BEGIN IMMEDIATE")
                columns = get_thread_columns(conn)
                before_counts = query_provider_counts(conn)
                before_model_counts = query_model_counts(conn) if "model" in columns else OrderedDict()
                visibility_updates = {
                    "normalized_cwd": 0,
                    "set_has_user_event": 0,
                    "unarchived": 0,
                }
                set_parts = ["model_provider = ?"]
                set_params = [current_provider]
                where_parts = ["model_provider IS NULL OR model_provider <> ?"]
                where_params = [current_provider]
                synced_fields = ["model_provider"]

                if "model" in columns and current_model:
                    set_parts.append("model = ?")
                    set_params.append(current_model)
                    where_parts.append("model IS NULL OR model <> ?")
                    where_params.append(current_model)
                    synced_fields.append("model")

                set_sql = ", ".join(set_parts)
                where_sql = " OR ".join(f"({part})" for part in where_parts)
                updated_rows = conn.execute(
                    f"UPDATE threads SET {set_sql} WHERE {where_sql}",
                    (*set_params, *where_params),
                ).rowcount

                if "archived" in columns:
                    archived_set_parts = ["archived = 0"]
                    if "archived_at" in columns:
                        archived_set_parts.append("archived_at = NULL")
                    visibility_updates["unarchived"] = conn.execute(
                        f"""
                        UPDATE threads
                        SET {", ".join(archived_set_parts)}
                        WHERE {archived_visibility_repair_condition(columns)}
                        """
                    ).rowcount

                if "cwd" in columns:
                    visibility_updates["normalized_cwd"] = conn.execute(
                        "UPDATE threads SET cwd = SUBSTR(cwd, 5) WHERE cwd LIKE '\\\\?\\%'"
                    ).rowcount

                if {"has_user_event", "first_user_message"}.issubset(columns):
                    visibility_updates["set_has_user_event"] = conn.execute(
                        """
                        UPDATE threads
                        SET has_user_event = 1
                        WHERE COALESCE(has_user_event, 0) = 0
                          AND COALESCE(TRIM(first_user_message), '') <> ''
                        """
                    ).rowcount
                conn.commit()
                after_counts = query_provider_counts(conn)
                after_model_counts = query_model_counts(conn) if "model" in columns else OrderedDict()
                checkpoint_result = checkpoint(conn)

            return {
                "attempts": attempt,
                "lock_wait_ms": elapsed_ms(started_at),
                "synced_fields": synced_fields,
                "updated_rows": updated_rows,
                "visibility_updates": visibility_updates,
                "before_counts": counts_to_rows(before_counts),
                "after_counts": counts_to_rows(after_counts),
                "before_model_counts": model_counts_to_rows(before_model_counts),
                "after_model_counts": model_counts_to_rows(after_model_counts),
                "checkpoint": {
                    "mode": SYNC_CHECKPOINT_MODE,
                    "busy": checkpoint_result[0],
                    "log_frames": checkpoint_result[1],
                    "checkpointed_frames": checkpoint_result[2],
                },
            }
        except sqlite3.OperationalError as exc:
            if not is_locked_error(exc):
                raise
            last_error = exc
            if attempt >= WRITE_LOCK_RETRY_LIMIT:
                waited_seconds = (time.monotonic() - started_at)
                raise RuntimeError(
                    "Codex 当前正在写入本地历史数据库，"
                    f"已等待 {waited_seconds:.1f} 秒仍未拿到写锁。"
                    "保持 Codex 开着也可以同步，但请等当前回复、工具调用或自动保存结束后再试一次。"
                ) from exc
            time.sleep(WRITE_LOCK_RETRY_DELAY_SECONDS)

    raise RuntimeError("Database write lock retry loop ended unexpectedly.") from last_error


def restore_database_with_retry(paths: Paths, chosen_backup: Path) -> dict[str, object]:
    started_at = time.monotonic()
    last_error: sqlite3.OperationalError | None = None
    chosen_db_backup = db_backup_path(chosen_backup)

    for attempt in range(1, WRITE_LOCK_RETRY_LIMIT + 1):
        try:
            with connect_db(chosen_db_backup, readonly=True) as source, connect_db(
                paths.db_path,
                readonly=False,
                timeout_seconds=WRITE_OPERATION_TIMEOUT_SECONDS,
            ) as target:
                # SQLite 在整库 backup 到目标库时会自己申请所需锁；
                # 这里直接尝试 restore，失败后统一按“数据库正忙”重试即可。
                source.backup(target)
                checkpoint_result = checkpoint(target)

            return {
                "attempts": attempt,
                "lock_wait_ms": elapsed_ms(started_at),
                "checkpoint": {
                    "mode": SYNC_CHECKPOINT_MODE,
                    "busy": checkpoint_result[0],
                    "log_frames": checkpoint_result[1],
                    "checkpointed_frames": checkpoint_result[2],
                },
            }
        except sqlite3.OperationalError as exc:
            if not is_locked_error(exc):
                raise
            last_error = exc
            if attempt >= WRITE_LOCK_RETRY_LIMIT:
                waited_seconds = (time.monotonic() - started_at)
                raise RuntimeError(
                    "Codex 当前正在写入本地历史数据库，"
                    f"已等待 {waited_seconds:.1f} 秒仍无法完成还原。"
                    "请等当前回复、工具调用或自动保存结束后再试一次。"
                ) from exc
            time.sleep(WRITE_LOCK_RETRY_DELAY_SECONDS)

    raise RuntimeError("Database restore retry loop ended unexpectedly.") from last_error


def get_status(paths: Paths) -> dict[str, object]:
    ensure_environment(paths)
    config_text = read_text(paths.config_path)
    current_model = parse_current_model(config_text)
    login_mode = detect_login_mode(paths, config_text)
    session_records = scan_session_records(paths)
    should_check_index = (
        paths.session_index_path.exists()
        or paths.sessions_dir.exists()
        or paths.archived_sessions_dir.exists()
    )
    index_entries = read_session_index(paths)

    with connect_db(paths.db_path, readonly=True) as conn:
        columns = get_thread_columns(conn)
        counts = query_provider_counts(conn)
        provider_resolution_error = ""
        try:
            current_provider, current_provider_source = resolve_current_provider(paths, config_text, conn, current_model)
        except RuntimeError as exc:
            current_provider = ""
            current_provider_source = "unresolved"
            provider_resolution_error = provider_unresolved_message(exc)
        session_provider_counts = ordered_counts([record.model_provider for record in session_records])
        session_model_counts = ordered_counts([record.model or "(empty)" for record in session_records])
        session_meta_stats = scan_session_meta_stats(paths, current_provider or None, current_model)
        session_movable_ids = {
            record.thread_id
            for record in session_records
            if current_provider and record.model_provider != current_provider
        }
        model_counts = query_model_counts(conn) if "model" in columns else OrderedDict()
        provider_model_counts = query_provider_model_counts(conn) if "model" in columns else []
        cwd_counts = query_cwd_counts(conn) if "cwd" in columns else []
        project_diagnostics = diagnose_projects(paths)
        total_threads = int(conn.execute("SELECT COUNT(*) FROM threads").fetchone()[0])
        provider_movable = count_mismatched(conn, "model_provider", current_provider) if current_provider else None
        model_movable = count_mismatched(conn, "model", current_model) if "model" in columns else None
        provider_pending_ids: set[str] = set()
        if current_provider:
            provider_where_sql = "(model_provider IS NULL OR model_provider <> ?)"
            provider_pending_ids = {
                str(row["id"])
                for row in conn.execute(f"SELECT id FROM threads WHERE {provider_where_sql}", (current_provider,))
            }
        model_pending_ids: set[str] = set()
        if "model" in columns and current_model:
            model_where_sql = "(model IS NULL OR model <> ?)"
            model_pending_ids = {
                str(row["id"])
                for row in conn.execute(f"SELECT id FROM threads WHERE {model_where_sql}", (current_model,))
            }
        visibility_pending_ids, visibility_counts = query_visibility_candidates(conn, columns)
        db_thread_query = "SELECT id FROM threads WHERE archived = 0" if "archived" in columns else "SELECT id FROM threads"
        db_thread_ids = {str(row["id"]) for row in conn.execute(db_thread_query)}
        missing_index_ids = db_thread_ids - set(index_entries) if should_check_index else set()
        db_movable_ids = provider_pending_ids | model_pending_ids | visibility_pending_ids
        db_pending_ids = db_movable_ids | missing_index_ids
        session_pending_ids = session_movable_ids | session_meta_stats.provider_mismatched_thread_ids

    return {
        "codex_home": str(paths.codex_home),
        "config_path": str(paths.config_path),
        "db_path": str(paths.db_path),
        "session_index_path": str(paths.session_index_path),
        "sessions_dir": str(paths.sessions_dir),
        "archived_sessions_dir": str(paths.archived_sessions_dir),
        "global_state_path": str(paths.global_state_path),
        "backup_dir": str(paths.backup_dir),
        "current_provider": current_provider,
        "current_provider_source": current_provider_source,
        "provider_resolution_error": provider_resolution_error,
        "current_model": current_model,
        "login_mode": login_mode,
        "total_threads": total_threads,
        "movable_threads": len(db_pending_ids),
        "provider_movable_threads": provider_movable,
        "model_movable_threads": model_movable,
        "movable_database_threads": len(db_movable_ids),
        "movable_database_thread_ids": sorted(db_pending_ids),
        "visibility_movable_threads": len(visibility_pending_ids),
        "visibility_movable_thread_ids": sorted(visibility_pending_ids),
        **visibility_counts,
        "movable_session_threads": len(session_movable_ids),
        "movable_session_thread_ids": sorted(session_pending_ids),
        "movable_session_meta_entries": session_meta_stats.mismatched_entries,
        "movable_session_meta_files": session_meta_stats.mismatched_files,
        "provider_movable_session_meta_entries": session_meta_stats.provider_mismatched_entries,
        "missing_session_index_entries": len(missing_index_ids),
        "indexed_threads": len(index_entries),
        "session_file_count": len(session_records),
        "provider_counts": counts_to_rows(counts),
        "model_counts": model_counts_to_rows(model_counts),
        "provider_model_counts": provider_model_counts,
        "cwd_counts": cwd_counts,
        "session_provider_counts": counts_to_rows(session_provider_counts),
        "session_model_counts": model_counts_to_rows(session_model_counts),
        "all_session_meta_provider_counts": counts_to_rows(session_meta_stats.provider_counts),
        "all_session_meta_model_counts": model_counts_to_rows(session_meta_stats.model_counts),
        "project_diagnostics": project_diagnostics,
        "backups": list_backups(paths),
    }


def make_backup(paths: Paths, label: str) -> Path:
    ensure_environment(paths)
    paths.backup_dir.mkdir(parents=True, exist_ok=True)
    config_text = read_text(paths.config_path)
    current_model = parse_current_model(config_text)
    with connect_db(paths.db_path, readonly=True) as conn:
        try:
            current_provider, _ = resolve_current_provider(paths, config_text, conn, current_model)
        except RuntimeError:
            current_provider = parse_current_provider(config_text) or "unknown"
        session_count, archived_session_count = query_thread_totals(conn)
        project_root_count = len(collect_project_roots(paths, conn))
    changed_session_files = scan_session_meta_stats(paths, current_provider, current_model).mismatched_files
    backup_path = unique_backup_dir_path(
        paths,
        backup_display_dir_name(current_provider, session_count, project_root_count),
    )
    backup_path.mkdir(parents=True, exist_ok=False)
    with connect_db(paths.db_path, readonly=True) as source, connect_db(db_backup_path(backup_path), readonly=False) as target:
        source.backup(target)
    snapshot_metadata(paths, backup_path)
    manifest = create_manifest(
        paths,
        label,
        current_provider,
        current_model,
        session_count,
        archived_session_count,
        changed_session_files,
        project_root_count,
    )
    manifest["displayName"] = backup_path.name
    write_text_exact(manifest_path(backup_path), json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    backup_path.touch()
    return backup_path


def sync_to_current_provider(paths: Paths, create_backup: bool = True) -> dict[str, object]:
    total_started_at = time.monotonic()
    status_before = get_status(paths)
    current_provider = str(status_before["current_provider"])
    if not current_provider.strip():
        raise RuntimeError(str(status_before.get("provider_resolution_error") or provider_unresolved_message()))
    raw_current_model = status_before.get("current_model")
    current_model = str(raw_current_model) if raw_current_model else None

    backup_path: Path | None = None
    backup_duration_ms = 0
    if create_backup:
        backup_started_at = time.monotonic()
        backup_path = make_backup(paths, "pre-sync")
        backup_duration_ms = elapsed_ms(backup_started_at)

    db_summary = update_provider_assignments(paths, current_provider, current_model)
    session_summary = sync_session_records(paths, current_provider, current_model)

    with connect_db(paths.db_path, readonly=True) as conn:
        index_summary = rebuild_session_index(paths, conn)

    status_after = get_status(paths)
    return {
        "action": "sync",
        "current_provider": current_provider,
        "current_model": current_model,
        "synced_fields": db_summary["synced_fields"],
        "updated_rows": db_summary["updated_rows"],
        "visibility_updates": db_summary["visibility_updates"],
        "updated_session_files": session_summary["updated_session_files"],
        "updated_session_meta_entries": session_summary["updated_session_meta_entries"],
        "skipped_busy_session_files": session_summary["skipped_busy_session_files"],
        "provider_movable_threads": status_before["provider_movable_threads"],
        "model_movable_threads": status_before["model_movable_threads"],
        "backup_path": str(backup_path) if backup_path else "",
        "before_counts": db_summary["before_counts"],
        "after_counts": db_summary["after_counts"],
        "before_model_counts": db_summary["before_model_counts"],
        "after_model_counts": db_summary["after_model_counts"],
        "session_before_counts": session_summary["session_before_counts"],
        "session_after_counts": session_summary["session_after_counts"],
        "session_before_model_counts": session_summary["session_before_model_counts"],
        "session_after_model_counts": session_summary["session_after_model_counts"],
        "all_session_meta_before_counts": session_summary["all_session_meta_before_counts"],
        "all_session_meta_after_counts": session_summary["all_session_meta_after_counts"],
        "all_session_meta_before_model_counts": session_summary["all_session_meta_before_model_counts"],
        "all_session_meta_after_model_counts": session_summary["all_session_meta_after_model_counts"],
        "mismatched_session_meta_files_before": session_summary["mismatched_session_meta_files_before"],
        "mismatched_session_meta_files_after": session_summary["mismatched_session_meta_files_after"],
        "mismatched_session_meta_entries_before": session_summary["mismatched_session_meta_entries_before"],
        "mismatched_session_meta_entries_after": session_summary["mismatched_session_meta_entries_after"],
        "checkpoint": db_summary["checkpoint"],
        "lock_wait_ms": db_summary["lock_wait_ms"],
        "lock_attempts": db_summary["attempts"],
        "rewritten_index_entries": index_summary["rewritten_index_entries"],
        "missing_session_index_entries_before": index_summary["missing_session_index_entries_before"],
        "preserved_index_only_entries": index_summary["preserved_index_only_entries"],
        "timing": {
            "backup_ms": backup_duration_ms,
            "database_ms": db_summary["lock_wait_ms"],
            "session_ms": session_summary["duration_ms"],
            "index_ms": index_summary["duration_ms"],
            "total_ms": elapsed_ms(total_started_at),
        },
        "status": status_after,
    }


def resolve_backup(paths: Paths, requested_path: str | None) -> Path:
    if requested_path:
        backup = Path(requested_path).expanduser()
    else:
        backups = list_backups(paths, limit=1)
        if not backups:
            raise RuntimeError("No backup files were found.")
        backup = Path(backups[0]["path"])
    if not backup.exists():
        raise RuntimeError(f"Backup file does not exist: {backup}")
    if backup.is_dir() and not db_backup_path(backup).exists():
        raise RuntimeError(f"Backup directory does not contain state_5.sqlite.bak: {backup}")
    return backup


def restore_backup(paths: Paths, backup_path: str | None) -> dict[str, object]:
    total_started_at = time.monotonic()
    ensure_environment(paths)
    chosen_backup = resolve_backup(paths, backup_path)

    backup_started_at = time.monotonic()
    restore_snapshot = make_backup(paths, "pre-restore")
    backup_duration_ms = elapsed_ms(backup_started_at)

    restore_db_started_at = time.monotonic()
    restore_db_summary = restore_database_with_retry(paths, chosen_backup)
    restore_db_duration_ms = elapsed_ms(restore_db_started_at)

    restore_summary = restore_metadata(paths, chosen_backup)
    # 恢复后统一重建索引，让数据库与侧边栏索引重新对齐。
    with connect_db(paths.db_path, readonly=True) as conn:
        index_summary = rebuild_session_index(paths, conn)

    status_after = get_status(paths)
    return {
        "action": "restore",
        "restored_from": str(chosen_backup),
        "safety_backup": str(restore_snapshot),
        "metadata_restore": restore_summary,
        "checkpoint": restore_db_summary["checkpoint"],
        "lock_wait_ms": restore_db_summary["lock_wait_ms"],
        "lock_attempts": restore_db_summary["attempts"],
        "rewritten_index_entries": index_summary["rewritten_index_entries"],
        "timing": {
            "backup_ms": backup_duration_ms,
            "database_ms": restore_db_duration_ms,
            "metadata_ms": restore_summary["duration_ms"],
            "index_ms": index_summary["duration_ms"],
            "total_ms": elapsed_ms(total_started_at),
        },
        "status": status_after,
    }


def backup_details(paths: Paths, backup_path: str | None) -> dict[str, object]:
    chosen_backup = resolve_backup(paths, backup_path)
    manifest = read_backup_manifest(chosen_backup)
    return {
        "action": "backup-details",
        "backup_path": str(chosen_backup),
        "manifest": manifest,
        "contains_database": db_backup_path(chosen_backup).exists(),
        "contains_session_index": session_index_backup_path(chosen_backup).exists(),
        "contains_session_meta": session_meta_backup_path(chosen_backup).exists(),
        "contains_global_state": global_state_backup_path(chosen_backup).exists(),
    }


def update_backup_manifest(paths: Paths, backup_path: str, display_name: str | None, notes: str | None) -> dict[str, object]:
    chosen_backup = resolve_backup(paths, backup_path)
    manifest = read_backup_manifest(chosen_backup)
    if not manifest:
        manifest = {"displayName": chosen_backup.name, "notes": ""}
    if display_name is not None:
        manifest["displayName"] = display_name.strip() or chosen_backup.name
    if notes is not None:
        manifest["notes"] = notes
    manifest["updatedAt"] = datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")
    write_text_exact(manifest_path(chosen_backup), json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return {"action": "backup-update", "backup_path": str(chosen_backup), "manifest": manifest}


def delete_backup(paths: Paths, backup_path: str) -> dict[str, object]:
    chosen_backup = resolve_backup(paths, backup_path)
    if chosen_backup.is_dir():
        shutil.rmtree(chosen_backup)
    else:
        for sidecar in (
            session_index_backup_path(chosen_backup),
            session_meta_backup_path(chosen_backup),
            global_state_backup_path(chosen_backup),
            manifest_path(chosen_backup),
        ):
            sidecar.unlink(missing_ok=True)
        chosen_backup.unlink()
    return {"action": "backup-delete", "deleted": str(chosen_backup)}


def to_json(payload: dict[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


def main() -> int:
    parser = argparse.ArgumentParser(description="Codex history sync helper")
    parser.add_argument("--codex-home", help="Override Codex home directory")
    parser.add_argument("--json", action="store_true", help="Emit JSON output")
    parser.add_argument("--one-click-safe-sync", action="store_true", help="Run launcher-compatible one-click safe backup and repair")
    parser.add_argument("--mode", choices=ONE_CLICK_MODES, default="auto", help="One-click mode")
    parser.add_argument("--close-codex", action="store_true", help="Close Codex Desktop before one-click repair")
    parser.add_argument("--backup", action="store_true", help="Require safety backup before one-click repair")
    parser.add_argument("--fix-projects", action="store_true", help="Repair project roots during one-click repair")
    parser.add_argument("--no-credentials", action="store_true", help="Do not copy or restore credentials")
    parser.add_argument("--merge-global-state", action="store_true", help="Merge safe global-state fields only")

    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("status", help="Show current provider/thread status")
    subparsers.add_parser("sync", help="Move all thread providers to the current provider")
    restore_parser = subparsers.add_parser("restore", help="Restore from a backup")
    restore_parser.add_argument("--backup", help="Backup file path; newest backup is used when omitted")
    subparsers.add_parser("backup", help="Create a manual backup")
    details_parser = subparsers.add_parser("backup-details", help="Show backup manifest/details")
    details_parser.add_argument("--backup", help="Backup file or directory path; newest backup is used when omitted")
    update_parser = subparsers.add_parser("backup-update", help="Update backup display name or notes")
    update_parser.add_argument("--backup", required=True, help="Backup file or directory path")
    update_parser.add_argument("--display-name")
    update_parser.add_argument("--notes")
    delete_parser = subparsers.add_parser("backup-delete", help="Delete one backup")
    delete_parser.add_argument("--backup", required=True, help="Backup file or directory path")
    subparsers.add_parser("project-diagnose", help="Diagnose Codex Desktop project roots")
    subparsers.add_parser("project-repair", help="Safely repair Codex Desktop project roots")

    args = parser.parse_args()
    if args.one_click_safe_sync:
        try:
            payload = one_click_safe_sync(
                mode=args.mode,
                close_codex=args.close_codex,
                backup=args.backup,
                fix_projects=args.fix_projects,
                no_credentials=args.no_credentials,
                merge_global_state=args.merge_global_state,
            )
        except Exception as exc:
            error_payload = {"ok": False, "error": str(exc)}
            if args.json:
                print(to_json(error_payload))
            else:
                print(error_payload["error"])
            return 1
        payload["ok"] = True
        if args.json:
            print(to_json(payload))
        else:
            print(payload.get("summary") or payload)
        return 0

    if not args.command:
        parser.error("command is required unless --one-click-safe-sync is used")

    paths = resolve_paths(args.codex_home)

    try:
        if args.command == "status":
            payload = get_status(paths)
        elif args.command == "sync":
            payload = sync_to_current_provider(paths)
        elif args.command == "restore":
            payload = restore_backup(paths, args.backup)
        elif args.command == "backup":
            ensure_environment(paths)
            backup_started_at = time.monotonic()
            payload = {
                "action": "backup",
                "backup_path": str(make_backup(paths, "manual")),
                "timing": {"total_ms": elapsed_ms(backup_started_at)},
            }
        elif args.command == "backup-details":
            payload = backup_details(paths, args.backup)
        elif args.command == "backup-update":
            payload = update_backup_manifest(paths, args.backup, args.display_name, args.notes)
        elif args.command == "backup-delete":
            payload = delete_backup(paths, args.backup)
        elif args.command == "project-diagnose":
            ensure_environment(paths)
            payload = {"action": "project-diagnose", **diagnose_projects(paths)}
        elif args.command == "project-repair":
            backup_path = make_backup(paths, "pre-project-repair")
            payload = repair_projects(paths)
            payload["safety_backup"] = str(backup_path)
        else:
            raise RuntimeError(f"Unsupported command: {args.command}")
    except Exception as exc:
        error_payload = {"ok": False, "error": str(exc)}
        if args.json:
            print(to_json(error_payload))
        else:
            print(error_payload["error"])
        return 1

    if isinstance(payload, dict):
        payload["ok"] = True

    if args.json:
        print(to_json(payload))
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
