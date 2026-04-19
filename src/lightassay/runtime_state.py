"""Runtime state for workbooks and the global default agent.

Workbook runtime state is scoped to the caller's current working
directory so ``continue`` can locate the last-touched workbook without
the user repeating the path. The pointer is a single source of truth:
it is written to one location under ``state_root`` only (no dual-write).

When ``state_root`` differs from cwd the pointer is still written under
``state_root`` — callers can point ``--output-dir`` wherever they like —
but we never silently mirror to a second location. Having one
authoritative file prevents the two copies from drifting apart.

Corrupt pointer files surface as explicit errors rather than silently
behaving as "no pointer". A corrupt pointer is a real fault and
hiding it causes the same failure to recur on every future run.

The default agent is different: it is global user config, not tied to a
single workbook directory. It lives under the user's config home
(``$XDG_CONFIG_HOME/lightassay`` or ``~/.config/lightassay``).
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone

from .errors import EvalError

_STATE_DIRNAME = ".lightassay"
_ACTIVE_WORKBOOK_FILENAME = "active_workbook.json"
_WORKBOOK_REGISTRY_FILENAME = "workbooks.json"
_EXEC_LOG_FILENAME = "execution_log.jsonl"
_AGENT_FILENAME = "agent.json"

_ID_SAFE_RE = re.compile(r"[^A-Za-z0-9_-]+")


def _state_dir(root: str) -> str:
    return os.path.join(os.path.abspath(root), _STATE_DIRNAME)


def _ensure_state_dir(root: str) -> str:
    path = _state_dir(root)
    os.makedirs(path, exist_ok=True)
    return path


def _config_home(config_root: str | None = None) -> str:
    if config_root is not None:
        return os.path.abspath(config_root)
    env_value = os.environ.get("XDG_CONFIG_HOME")
    if env_value and env_value.strip():
        return os.path.abspath(env_value)
    return os.path.join(os.path.expanduser("~"), ".config")


def _agent_state_dir(config_root: str | None = None) -> str:
    return os.path.join(_config_home(config_root), "lightassay")


def _ensure_agent_state_dir(config_root: str | None = None) -> str:
    path = _agent_state_dir(config_root)
    os.makedirs(path, exist_ok=True)
    return path


def _workbook_id_from_path(workbook_path: str) -> str:
    """Derive the canonical workbook id from *workbook_path*.

    The id is the workbook filename stem with any trailing ``.workbook``
    suffix removed, so a workbook named ``my-eval.workbook.md`` gets
    id ``my-eval`` and can be addressed with ``--workbook-id my-eval``.
    """
    stem = os.path.splitext(os.path.basename(workbook_path))[0]
    if stem.endswith(".workbook"):
        stem = stem[: -len(".workbook")]
    safe = _ID_SAFE_RE.sub("-", stem).strip("-")
    return safe or "workbook"


def set_active_workbook(workbook_path: str, state_root: str = ".") -> str:
    """Record *workbook_path* as the active workbook and remember it in
    the workbook registry for ``--workbook-id`` lookup.

    The pointer is written to a single authoritative location under
    ``state_root``; the caller that chose ``state_root`` owns pointer
    state for that directory.

    Returns the absolute path of the pointer file.
    """
    abs_workbook = os.path.abspath(workbook_path)
    updated_at = datetime.now(timezone.utc).isoformat()
    payload = {
        "workbook_path": abs_workbook,
        "updated_at": updated_at,
    }

    state_dir = _ensure_state_dir(state_root)
    pointer_path = os.path.join(state_dir, _ACTIVE_WORKBOOK_FILENAME)
    with open(pointer_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    _register_workbook(abs_workbook, updated_at, state_root=state_root)
    return pointer_path


def get_active_workbook(state_root: str = ".") -> str | None:
    """Return the active workbook path recorded at ``state_root``.

    Returns ``None`` when no pointer has been written yet. A pointer
    file that exists but is malformed raises ``EvalError`` — corrupt
    state is a real fault and hiding it masks recurring failures.
    """
    pointer_path = os.path.join(_state_dir(state_root), _ACTIVE_WORKBOOK_FILENAME)
    if not os.path.isfile(pointer_path):
        return None
    try:
        with open(pointer_path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        raise EvalError(
            f"Active workbook pointer is corrupt: {pointer_path!r}: {exc}. "
            "Delete the file or re-run quickstart to reset it."
        ) from exc
    if not isinstance(data, dict):
        raise EvalError(
            f"Active workbook pointer is corrupt: {pointer_path!r}: expected a JSON object."
        )
    value = data.get("workbook_path")
    if not isinstance(value, str) or not value.strip():
        raise EvalError(
            f"Active workbook pointer is corrupt: {pointer_path!r}: "
            "missing or invalid 'workbook_path' field."
        )
    return value


def _register_workbook(
    workbook_path: str,
    updated_at: str,
    *,
    state_root: str,
) -> None:
    registry = _read_workbook_registry(state_root)
    workbook_id = _workbook_id_from_path(workbook_path)
    registry[workbook_id] = {
        "workbook_path": workbook_path,
        "updated_at": updated_at,
    }
    state_dir = _ensure_state_dir(state_root)
    registry_path = os.path.join(state_dir, _WORKBOOK_REGISTRY_FILENAME)
    with open(registry_path, "w", encoding="utf-8") as fh:
        json.dump(registry, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def _read_workbook_registry(state_root: str) -> dict:
    registry_path = os.path.join(_state_dir(state_root), _WORKBOOK_REGISTRY_FILENAME)
    if not os.path.isfile(registry_path):
        return {}
    try:
        with open(registry_path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        raise EvalError(
            f"Workbook registry is corrupt: {registry_path!r}: {exc}. "
            "Delete the file to reset the registry."
        ) from exc
    if not isinstance(data, dict):
        raise EvalError(f"Workbook registry is corrupt: {registry_path!r}: expected a JSON object.")
    return data


def list_known_workbooks(state_root: str = ".") -> list[dict]:
    """Return a list of ``{id, workbook_path, updated_at}`` dicts."""
    registry = _read_workbook_registry(state_root)
    items: list[dict] = []
    for workbook_id, entry in registry.items():
        if not isinstance(entry, dict):
            continue
        items.append(
            {
                "id": workbook_id,
                "workbook_path": entry.get("workbook_path"),
                "updated_at": entry.get("updated_at"),
            }
        )
    items.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
    return items


def resolve_workbook_id(workbook_id: str, state_root: str = ".") -> str | None:
    """Return the workbook path for *workbook_id* or ``None`` if unknown."""
    registry = _read_workbook_registry(state_root)
    entry = registry.get(workbook_id)
    if not isinstance(entry, dict):
        return None
    path = entry.get("workbook_path")
    if not isinstance(path, str) or not path.strip():
        raise EvalError(f"Workbook registry entry for {workbook_id!r} is malformed.")
    return path


def append_execution_log(entry: dict, state_root: str = ".") -> str:
    """Append *entry* to the JSONL execution log under *state_root*."""
    state_dir = _ensure_state_dir(state_root)
    log_path = os.path.join(state_dir, _EXEC_LOG_FILENAME)

    payload = dict(entry)
    payload.setdefault("timestamp", datetime.now(timezone.utc).isoformat())

    with open(log_path, "a", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)
        fh.write("\n")
    return log_path


def execution_log_path(state_root: str = ".") -> str:
    """Return the expected JSONL execution log path (even if not yet created)."""
    return os.path.join(_state_dir(state_root), _EXEC_LOG_FILENAME)


def set_default_agent(name: str, config_root: str | None = None) -> str:
    """Persist *name* as the default agent for later commands."""
    stripped = (name or "").strip()
    if not stripped:
        raise EvalError("Agent name must be a non-empty string.")
    state_dir = _ensure_agent_state_dir(config_root)
    path = os.path.join(state_dir, _AGENT_FILENAME)
    payload = {
        "agent": stripped,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return path


def get_default_agent(config_root: str | None = None) -> str | None:
    """Return the persisted default agent name, or ``None`` if unset."""
    path = os.path.join(_agent_state_dir(config_root), _AGENT_FILENAME)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        raise EvalError(
            f"Default agent state is corrupt: {path!r}: {exc}. "
            "Delete the file or run `lightassay agents` to reset it."
        ) from exc
    if not isinstance(data, dict):
        raise EvalError(f"Default agent state is corrupt: {path!r}: expected a JSON object.")
    value = data.get("agent")
    if not isinstance(value, str) or not value.strip():
        raise EvalError(
            f"Default agent state is corrupt: {path!r}: missing or invalid 'agent' field."
        )
    return value
