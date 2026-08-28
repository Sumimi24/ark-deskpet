import datetime
import glob
import json
import os
import time


_MONITOR_CACHE = {}


def _message_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") in ("input_text", "output_text", "text"):
                text = item.get("text", "")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return ""


def _clean(text, limit=180):
    lines = [" ".join(line.split()) for line in str(text).replace("\r\n", "\n").splitlines()]
    text = "\n".join(line for line in lines if line).strip()
    if len(text) > limit:
        text = text[: max(0, limit - 1)] + "…"
    return text


def _user_text(payload):
    if payload.get("type") == "user_message":
        text = payload.get("message")
        if isinstance(text, str):
            return text
    content = payload.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return _message_text(content)
    return ""


def _empty_state():
    return {
        "active": False,
        "task": None,
        "model": None,
        "progress": None,
        "started_at": None,
        "total_tokens": None,
        "last_finished": None,
        "has_lifecycle": False,
        "offset": 0,
        "partial": "",
    }


def _set_task(state, text):
    if not text or "<environment_context>" in text or "permissions instructions" in text:
        return
    marker = "My request for Codex:"
    if marker in text:
        text = text.split(marker, 1)[1]
    text = _clean(text)
    if text:
        state["task"] = text


def _set_progress(state, text):
    if isinstance(text, str) and text.strip():
        state["progress"] = _clean(text)


def _read_message(state, payload):
    ptype = payload.get("type")
    role = payload.get("role")
    if ptype in ("user_message", "message") and role in (None, "user"):
        _set_task(state, _user_text(payload))
    elif ptype in ("agent_message", "message") and role in (None, "assistant"):
        if payload.get("phase") == "commentary":
            _set_progress(
                state,
                payload.get("message") or _message_text(payload.get("content")),
            )


def _read_payload(state, obj):
    payload = obj.get("payload") or {}
    ptype = payload.get("type")
    if ptype == "task_started":
        state["active"] = True
        state["has_lifecycle"] = True
        state["task"] = None
        state["progress"] = None
        started = payload.get("started_at")
        state["started_at"] = (
            float(started) if isinstance(started, (int, float)) else None
        )
    elif ptype == "task_complete":
        state["active"] = False
        state["has_lifecycle"] = True
        completed = payload.get("completed_at")
        if isinstance(completed, (int, float)):
            state["last_finished"] = float(completed)
        _set_progress(state, payload.get("last_agent_message"))
    elif ptype == "token_count":
        info = payload.get("info") or {}
        usage = info.get("total_token_usage") or {}
        tokens = usage.get("total_tokens")
        if isinstance(tokens, (int, float)):
            state["total_tokens"] = int(tokens)
    elif ptype == "item_completed":
        item = payload.get("item") or {}
        if item.get("type") == "UserMessage":
            _set_task(state, _message_text(item.get("content")))
        elif item.get("type") == "AgentMessage" and item.get("phase") == "commentary":
            _set_progress(state, _message_text(item.get("content")))
    elif ptype in ("message", "user_message", "agent_message"):
        _read_message(state, payload)

    thread_settings = payload.get("thread_settings")
    if isinstance(thread_settings, dict) and thread_settings.get("model"):
        state["model"] = str(thread_settings["model"])
    elif payload.get("model"):
        state["model"] = str(payload["model"])


def _consume_rollout(path, state):
    size = os.path.getsize(path)
    offset = int(state.get("offset", 0))
    if offset > size:
        state.clear()
        state.update(_empty_state())
        offset = 0
    try:
        with open(path, "rb") as handle:
            handle.seek(offset)
            data = handle.read()
    except OSError:
        return
    prefix = state.pop("partial", "")
    if prefix:
        data = prefix.encode("utf-8") + data
    lines = data.splitlines()
    if data and not data.endswith(b"\n"):
        state["partial"] = lines.pop().decode("utf-8", errors="replace") if lines else data.decode("utf-8", errors="replace")
    else:
        state["partial"] = ""
    state["offset"] = size
    for line in lines:
        try:
            _read_payload(state, json.loads(line))
        except (TypeError, ValueError):
            continue


def _parse_rollout(path, now=None):
    now = time.time() if now is None else float(now)
    mtime = os.path.getmtime(path)
    state = dict(_MONITOR_CACHE.get(path) or _empty_state())
    _consume_rollout(path, state)
    active = state["active"] if state["has_lifecycle"] else (now - mtime) < 8
    state["active"] = active
    _MONITOR_CACHE[path] = dict(state)
    return {
        "active": active,
        "task": state["task"],
        "model": state["model"],
        "progress": state["progress"],
        "elapsed": (
            int(now - state["started_at"])
            if active and state["started_at"] is not None
            else None
        ),
        "tokens": state["total_tokens"],
        "last_finished": (
            datetime.datetime.fromtimestamp(state["last_finished"]).strftime("%H:%M")
            if state["last_finished"] is not None
            else None
        ),
    }


def get_codex_status():
    root = os.path.join(os.path.expanduser("~"), ".codex", "sessions")
    files = glob.glob(os.path.join(root, "**", "rollout-*.jsonl"), recursive=True)
    if not files:
        return {
            "active": False,
            "task": None,
            "model": None,
            "progress": None,
            "elapsed": None,
            "tokens": None,
            "last_finished": None,
        }
    path = max(files, key=os.path.getmtime)
    return _parse_rollout(path)
