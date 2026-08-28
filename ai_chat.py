import json
import os
from urllib.parse import urlparse

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)


DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODELS = (
    ("deepseek-v4-flash", "快速"),
    ("deepseek-v4-pro", "高质量"),
)
HISTORY_LIMIT = 20


def load_profile(pet_dir):
    path = os.path.join(pet_dir, "ai_profile.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def load_history(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_history(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def valid_playlist_url(value):
    parsed = urlparse(str(value).strip())
    host = (parsed.hostname or "").lower().rstrip(".")
    return parsed.scheme == "https" and (host == "qq.com" or host.endswith(".qq.com"))


def has_music_intent(text):
    return any(word in str(text) for word in ("放歌", "听歌", "音乐", "歌单"))


def parse_sse_bytes(buffer):
    """Return (remaining bytes, text deltas, done)."""
    parts = buffer.split(b"\n")
    remaining = parts.pop() if parts else b""
    deltas = []
    done = False
    for raw in parts:
        line = raw.strip()
        if not line.startswith(b"data:"):
            continue
        payload = line[5:].strip()
        if payload == b"[DONE]":
            done = True
            continue
        try:
            data = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            continue
        choices = data.get("choices") or []
        if not choices:
            continue
        delta = (choices[0].get("delta") or {}).get("content")
        if isinstance(delta, str) and delta:
            deltas.append(delta)
    return remaining, deltas, done


def api_error(status, body=""):
    messages = {
        401: "API Key 无效，请检查密钥。",
        402: "DeepSeek 余额不足，请检查账户余额。",
        429: "DeepSeek 请求过于频繁，请稍后再试。",
        500: "DeepSeek 服务端错误。",
        503: "DeepSeek 当前繁忙，请稍后再试。",
    }
    if status in messages:
        return messages[status]
    try:
        detail = json.loads(body).get("error", {}).get("message")
    except (TypeError, ValueError, AttributeError):
        detail = None
    return detail or (f"DeepSeek 请求失败（HTTP {status}）。" if status else "网络连接失败。")


class DeepSeekClient(QObject):
    delta = Signal(str)
    completed = Signal(str)
    failed = Signal(str)
    connection_result = Signal(bool, str)
    busy_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.manager = QNetworkAccessManager(self)
        self.reply = None
        self._sse_buffer = b""
        self._text = ""
        self._error = ""
        self._mode = None

    def _headers(self, api_key):
        request = QNetworkRequest(QUrl(DEEPSEEK_BASE_URL))
        request.setRawHeader(b"Authorization", f"Bearer {api_key}".encode("utf-8"))
        request.setRawHeader(b"Accept", b"application/json")
        return request

    def _begin(self, reply, mode):
        self.reply = reply
        self._mode = mode
        self._sse_buffer = b""
        self._text = ""
        self._error = ""
        reply.setProperty("ai_mode", mode)
        reply.errorOccurred.connect(self._on_error)
        reply.finished.connect(self._on_finished)
        if mode == "chat":
            reply.readyRead.connect(self._read_stream)
        self.busy_changed.emit(True)

    def test_connection(self, api_key):
        if self.reply is not None:
            self.connection_result.emit(False, "已有请求正在进行。")
            return
        if not str(api_key).strip():
            self.connection_result.emit(False, "请先填写 API Key。")
            return
        request = self._headers(api_key.strip())
        request.setUrl(QUrl(f"{DEEPSEEK_BASE_URL}/models"))
        request.setTransferTimeout(15000)
        self._begin(self.manager.get(request), "models")

    def chat(self, api_key, model, messages):
        if self.reply is not None:
            self.failed.emit("已有请求正在进行，请等待当前回复完成。")
            return False
        if not str(api_key).strip():
            self.failed.emit("请先在设置中填写 DeepSeek API Key。")
            return False
        body = json.dumps(
            {
                "model": model,
                "messages": messages,
                "stream": True,
                "stream_options": {"include_usage": True},
                "thinking": {"type": "disabled"},
                "temperature": 0.8,
                "max_tokens": 400,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = self._headers(api_key.strip())
        request.setUrl(QUrl(f"{DEEPSEEK_BASE_URL}/chat/completions"))
        request.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json")
        request.setTransferTimeout(60000)
        self._begin(self.manager.post(request, body), "chat")
        return True

    def cancel(self):
        if self.reply is not None:
            self.reply.abort()

    def _read_stream(self):
        if self.reply is None:
            return
        self._sse_buffer += bytes(self.reply.readAll())
        self._sse_buffer, deltas, _ = parse_sse_bytes(self._sse_buffer)
        for delta in deltas:
            self._text += delta
            self.delta.emit(delta)

    def _on_error(self, _error):
        if self.reply is not None:
            status = self.reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
            self._error = api_error(int(status) if status is not None else 0)

    def _on_finished(self):
        reply = self.reply
        if reply is None:
            return
        if self._mode == "chat":
            self._read_stream()
            if self._sse_buffer:
                self._sse_buffer += b"\n"
                self._read_stream()
        body = bytes(reply.readAll())
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        status = int(status) if status is not None else 0
        mode = self._mode
        error = self._error
        if not error and status >= 400:
            error = api_error(status, body.decode("utf-8", errors="replace"))
        self.reply = None
        self._mode = None
        reply.deleteLater()
        self.busy_changed.emit(False)
        if mode == "models":
            if error:
                self.connection_result.emit(False, error)
            else:
                try:
                    data = json.loads(body.decode("utf-8"))
                    models = ", ".join(item.get("id", "") for item in data.get("data", []))
                except (TypeError, ValueError, AttributeError):
                    models = ""
                self.connection_result.emit(True, f"连接成功，可用模型：{models or '未知'}")
        elif error:
            self.failed.emit(error)
        elif self._text.strip():
            self.completed.emit(self._text.strip())
        else:
            self.failed.emit("DeepSeek 返回了空内容。")


class InlineChatOverlay(QFrame):
    message_submitted = Signal(str)
    topic_requested = Signal()
    playlist_requested = Signal()
    close_requested = Signal()

    def __init__(self, pet_name, messages, parent=None):
        super().__init__(parent)
        self.setObjectName("inlineChatOverlay")
        self.setStyleSheet(
            "#inlineChatOverlay { background: rgba(25, 25, 30, 220);"
            " border: 1px solid rgba(255, 255, 255, 90); border-radius: 12px; }"
            "QLabel { color: white; }"
            "QLineEdit { background: rgba(255, 255, 255, 235); color: #222;"
            " border: 0; border-radius: 8px; padding: 6px 8px; }"
            "QPushButton { background: rgba(255, 255, 255, 36); color: white;"
            " border: 1px solid rgba(255, 255, 255, 80); border-radius: 7px; padding: 4px 8px; }"
            "QPushButton:disabled { color: rgba(255, 255, 255, 110); }"
        )
        self.setMinimumWidth(320)
        self.setMaximumWidth(360)
        self.history = []
        self.streaming = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)
        title_row = QHBoxLayout()
        self.title_label = QLabel(f"{pet_name} · AI 对话")
        title_row.addWidget(self.title_label, 1)
        self.close_button = QPushButton("收起")
        self.close_button.clicked.connect(self.close_requested.emit)
        title_row.addWidget(self.close_button)
        layout.addLayout(title_row)
        self.view = QLabel()
        self.view.setWordWrap(True)
        self.view.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.view.setMaximumWidth(336)
        layout.addWidget(self.view)
        self.status_label = QLabel("")
        layout.addWidget(self.status_label)
        self.input = QLineEdit()
        self.input.setPlaceholderText("输入消息，回车发送")
        self.input.returnPressed.connect(self._submit)
        layout.addWidget(self.input)
        row = QHBoxLayout()
        self.topic_button = QPushButton("聊点什么")
        self.topic_button.clicked.connect(self.topic_requested.emit)
        row.addWidget(self.topic_button)
        self.playlist_button = QPushButton("打开我的歌单")
        self.playlist_button.clicked.connect(self.playlist_requested.emit)
        self.playlist_button.hide()
        row.addWidget(self.playlist_button)
        self.send_button = QPushButton("发送")
        self.send_button.clicked.connect(self._submit)
        row.addWidget(self.send_button)
        layout.addLayout(row)
        self.set_messages(messages)

    def _submit(self):
        text = self.input.text().strip()
        if text and not self.streaming:
            self.input.clear()
            self.message_submitted.emit(text)

    def set_pet(self, pet_name):
        self.title_label.setText(f"{pet_name} · AI 对话")

    def set_messages(self, messages):
        self.history = [dict(item) for item in messages if isinstance(item, dict)]
        self._render()

    def add_user(self, text):
        self.history.append({"role": "user", "content": text})
        self.history.append({"role": "assistant", "content": ""})
        self.streaming = True
        self._render()
        self._set_busy(True)

    def add_topic(self, text):
        self.history.append({"role": "assistant", "content": text})
        self._render()

    def begin_topic(self):
        self._set_busy(True)

    def append_delta(self, text):
        if self.history and self.history[-1].get("role") == "assistant":
            self.history[-1]["content"] += text
            self._render()

    def finish_reply(self):
        self.streaming = False
        self._set_busy(False)

    def fail_reply(self, message):
        if self.history and self.history[-1].get("role") == "assistant":
            self.history.pop()
        self.streaming = False
        self._set_busy(False)
        self.status_label.setText(message)
        self._render()

    def show_music_action(self, visible=True):
        self.playlist_button.setVisible(bool(visible))

    def _set_busy(self, busy):
        self.streaming = bool(busy)
        self.send_button.setEnabled(not busy)
        self.topic_button.setEnabled(not busy)
        self.input.setEnabled(not busy)
        self.status_label.setText("正在生成回复…" if busy else "")

    def _render(self):
        lines = []
        for item in self.history[-4:]:
            role = "我" if item.get("role") == "user" else "桌宠"
            content = str(item.get("content", "")).strip()
            if content:
                lines.append(f"{role}：{content[:180]}")
        self.view.setText("\n\n".join(lines) or "想和我聊点什么？")
        self.adjustSize()
