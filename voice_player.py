import json
import os

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaDevices, QMediaPlayer


class VoicePlayer(QObject):
    started = Signal(str, bool)
    finished = Signal()
    error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.audio = QAudioOutput(self)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio)
        self.devices = QMediaDevices(self)
        self.devices.audioOutputsChanged.connect(self.refresh_output_device)
        self.player.mediaStatusChanged.connect(self._media_status_changed)
        self.player.errorOccurred.connect(self._error_occurred)
        self.current_line = None

    @staticmethod
    def load_manifest(pet_dir):
        path = os.path.join(pet_dir, "voices", "manifest.json")
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return []
        lines = data.get("lines", []) if isinstance(data, dict) else []
        voice_dir = os.path.dirname(path)
        valid = []
        for line in lines:
            if not isinstance(line, dict) or not line.get("text"):
                continue
            line = dict(line)
            audio = line.get("audio")
            if audio and not os.path.isabs(audio):
                line["audio"] = os.path.normpath(os.path.join(voice_dir, audio))
            valid.append(line)
        return valid

    def set_volume(self, value):
        self.audio.setVolume(max(0, min(100, int(value))) / 100.0)

    def refresh_output_device(self):
        device = self.devices.defaultAudioOutput()
        if device.isNull():
            return
        self.audio.setDevice(device)

    def output_device_name(self):
        device = self.devices.defaultAudioOutput()
        return device.description() if not device.isNull() else "未检测到音频输出设备"

    def is_playing(self):
        return self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    def play(self, line, volume):
        self.stop()
        # Windows can change the default output after the app starts (for
        # example when a headset is plugged in), so do not keep the startup
        # speaker selection for the lifetime of the deskpet.
        self.refresh_output_device()
        self.current_line = line
        self.set_volume(volume)
        audio = line.get("audio")
        has_audio = bool(audio and os.path.isfile(audio))
        self.started.emit(str(line.get("text", "")), has_audio)
        if has_audio:
            self.player.setSource(QUrl.fromLocalFile(audio))
            self.player.play()
        return has_audio

    def stop(self):
        self.player.stop()
        self.current_line = None

    def _media_status_changed(self, status):
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.current_line = None
            self.finished.emit()

    def _error_occurred(self, error, message):
        if message:
            self.error.emit(str(message))
        self.current_line = None
        self.finished.emit()
