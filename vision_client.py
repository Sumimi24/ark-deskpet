import json
import re

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest


QWEN_REGIONS = (
    ("cn-beijing", "华北2（北京）"),
    ("ap-southeast-1", "新加坡"),
    ("ap-northeast-1", "日本（东京）"),
    ("us-east-1", "美国（弗吉尼亚）"),
)
QWEN_MODELS = (
    ("qwen3-vl-flash", "Flash（省流）"),
    ("qwen3-vl-plus", "Plus（更细致）"),
)


QWEN_SHARED_DOMAINS = {
    "cn-beijing": "dashscope.aliyuncs.com",
    "ap-southeast-1": "dashscope-intl.aliyuncs.com",
    "us-east-1": "dashscope-us.aliyuncs.com",
}


def _qwen_base_url(region, workspace_id):
    workspace_id = str(workspace_id).strip()
    # Numeric values commonly copied from the console are business-space
    # numbers, not valid dedicated-domain WorkspaceIds. The shared regional
    # endpoint still authenticates the same regional API key.
    if str(region) in QWEN_SHARED_DOMAINS and workspace_id.isdigit():
        return f"https://{QWEN_SHARED_DOMAINS[str(region)]}/compatible-mode/v1"
    return f"https://{workspace_id}.{region}.maas.aliyuncs.com/compatible-mode/v1"


def qwen_endpoint(region, workspace_id):
    return _qwen_base_url(region, workspace_id) + "/chat/completions"


def qwen_models_endpoint(region, workspace_id):
    return _qwen_base_url(region, workspace_id) + "/models"


def valid_workspace_id(value):
    return bool(re.fullmatch(r"[A-Za-z0-9_-]+", str(value).strip()))


def qwen_error(status, body=""):
    messages = {
        400: "百炼请求格式错误，请检查业务空间和模型。",
        401: "百炼 API Key 无效，请检查密钥。",
        403: "百炼没有访问该模型或业务空间的权限。",
        404: "百炼接口或模型不存在，请检查地域和模型名称。",
        429: "百炼请求过于频繁，请稍后再试。",
        500: "百炼服务端错误。",
        503: "百炼当前繁忙，请稍后再试。",
    }
    try:
        detail = json.loads(body).get("error", {}).get("message")
    except (TypeError, ValueError, AttributeError):
        detail = None
    if status in messages:
        if detail:
            # The server detail is useful for configuration errors, but keep
            # it short and never include the request body or credentials.
            return f"{messages[status]} {str(detail).strip()[:180]}"
        return messages[status]
    return detail or (f"百炼请求失败（HTTP {status}）。" if status else "无法连接百炼。")


def _content_text(content):
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "".join(parts).strip()
    return ""


class QwenVisionClient(QObject):
    completed = Signal(str)
    failed = Signal(str)
    connection_result = Signal(bool, str)
    busy_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.manager = QNetworkAccessManager(self)
        self.reply = None
        self.mode = None
        self.error = ""

    def _request(self, api_key, region, workspace_id, endpoint=None):
        request = QNetworkRequest(QUrl(endpoint or qwen_endpoint(region, workspace_id)))
        request.setRawHeader(b"Authorization", f"Bearer {api_key}".encode("utf-8"))
        request.setRawHeader(b"Accept", b"application/json")
        request.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json")
        request.setTransferTimeout(60000)
        return request

    def _begin(self, reply, mode):
        self.reply = reply
        self.mode = mode
        self.error = ""
        reply.errorOccurred.connect(self._on_error)
        reply.finished.connect(self._on_finished)
        self.busy_changed.emit(True)

    def test_connection(self, api_key, region, workspace_id, model):
        if self.reply is not None:
            self.connection_result.emit(False, "已有视觉请求正在进行。")
            return
        if not str(api_key).strip() or not valid_workspace_id(workspace_id):
            self.connection_result.emit(False, "请填写百炼 API Key 和业务空间 ID。")
            return
        endpoint = qwen_models_endpoint(region, workspace_id.strip())
        self._begin(self.manager.get(self._request(api_key.strip(), region, workspace_id.strip(), endpoint)), "test")

    def observe(self, api_key, region, workspace_id, model, image_data_url, prompt):
        if self.reply is not None:
            self.failed.emit("已有视觉请求正在进行。")
            return False
        if not str(api_key).strip() or not valid_workspace_id(workspace_id) or not image_data_url:
            self.failed.emit("请先填写百炼配置并获取有效截图。")
            return False
        body = json.dumps({
            "model": model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": image_data_url}},
                    {"type": "text", "text": prompt},
                ],
            }],
            "stream": False,
            "enable_thinking": False,
            "max_tokens": 160,
            "temperature": 0.8,
        }, ensure_ascii=False).encode("utf-8")
        self._begin(self.manager.post(self._request(api_key.strip(), region, workspace_id.strip()), body), "observe")
        return True

    def cancel(self):
        if self.reply is not None:
            self.reply.abort()

    def _on_error(self, _error):
        if self.reply is not None:
            status = self.reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
            # For HTTP errors, wait for finished() so qwen_error() can parse
            # the response's safe error.message. Network failures have no
            # response body and can be reported immediately.
            if status is None or int(status) == 0:
                self.error = qwen_error(0)

    def _on_finished(self):
        reply = self.reply
        if reply is None:
            return
        body = bytes(reply.readAll())
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        status = int(status) if status is not None else 0
        mode = self.mode
        error = self.error or (qwen_error(status, body.decode("utf-8", errors="replace")) if status >= 400 else "")
        self.reply = None
        self.mode = None
        reply.deleteLater()
        self.busy_changed.emit(False)
        if error:
            if mode == "test":
                self.connection_result.emit(False, error)
            else:
                self.failed.emit(error)
            return
        data = None
        try:
            data = json.loads(body.decode("utf-8"))
            choices = data.get("choices") or []
            content = _content_text((choices[0].get("message") or {}).get("content")) if choices else ""
        except (UnicodeDecodeError, ValueError, TypeError, AttributeError):
            content = ""
        if mode == "test":
            try:
                model_list = data.get("data") if isinstance(data, dict) else None
            except AttributeError:
                model_list = None
            ok = isinstance(model_list, list)
            self.connection_result.emit(ok, "连接成功。" if ok else "模型列表返回格式异常。")
        elif content:
            self.completed.emit(content)
        else:
            self.failed.emit("百炼返回了空话题。")
