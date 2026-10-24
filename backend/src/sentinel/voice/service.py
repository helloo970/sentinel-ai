"""Voice control: wake word, triple clap, push-to-talk, Whisper and Piper.

All voice packages are optional (`pip install -e ".[voice]"`). If they are missing, the
app stays fully usable; announcements are then spoken by the app itself (Web Speech).
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import json
import logging
import queue
import re
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from sentinel.secrets_filter import redact
from sentinel.voice.clap import ClapDetector
from sentinel.voice.stopword import is_stop_command

if TYPE_CHECKING:
    from sentinel.services import Services

log = logging.getLogger(__name__)

FAREWELL = re.compile(
    r"\b(thanks|thank you|bye|goodbye|that's all|that is all|that's it|stop)\b", re.I
)
SAMPLE_RATE = 16000
BLOCK = 1280  # 80 ms, fits openWakeWord
SILENCE_RMS = 0.012
SILENT_AFTER_S = 8.0
SILENT_HINT = (
    "The microphone is not delivering any sound. Pick another microphone in the settings "
    "or allow access in Windows: Settings -> Privacy -> Microphone -> "
    '"Let desktop apps access your microphone".'
)
MAX_RECORD_S = 10.0
END_SILENCE_S = 0.8
WAKE_THRESHOLD = 0.5


def _has(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


WAKE_FILES = {
    "hey_jarvis": "hey_jarvis_v0.1.onnx",
    "melspec": "melspectrogram.onnx",
    "embedding": "embedding_model.onnx",
}


def wake_model_dir(data_dir: Path) -> Path:
    """Wake-word models in the data folder (the program folder is only temporary in the installer).

    If missing, they are downloaded once from the official openWakeWord release.
    """
    folder = data_dir / "voice" / "openwakeword"
    if all((folder / name).is_file() for name in WAKE_FILES.values()):
        return folder
    from openwakeword.utils import download_models

    folder.mkdir(parents=True, exist_ok=True)
    download_models(["hey_jarvis_v0.1"], target_directory=str(folder))
    return folder


class VoiceService:
    def __init__(self, svc: Services) -> None:
        self.svc = svc
        self.state = "off"
        self._loop: asyncio.AbstractEventLoop | None = None
        self._audio: queue.Queue[np.ndarray] = queue.Queue(maxsize=200)
        self._speech: queue.Queue[str] = queue.Queue(maxsize=20)
        self._stop = threading.Event()
        self._ptt = threading.Event()
        self._listen = threading.Event()
        self._play_lock = threading.Lock()
        # Conversation mode: keep listening after a reply without another "Hey Jarvis"
        self._followup_armed = False
        self.followup_until = 0.0
        self._threads: list[threading.Thread] = []
        self._stream: Any = None
        self._stt: Any = None
        self._tts: Any = None
        self._wake: Any = None
        self.clap = ClapDetector(SAMPLE_RATE, claps=svc.config.voice_clap_count)
        self.voice_active = False
        self.claps_active = False
        self.last_transcript: str | None = None
        self.level = 0.0
        self._input_rate = SAMPLE_RATE
        self._silent_since = time.time()
        self._worker: threading.Thread | None = None
        from sentinel.voice.elevenlabs import ElevenLabsSTT

        self.cloud_stt = ElevenLabsSTT(svc.config.config_dir)
        self.stt_provider = "local"
        self._keyterms: tuple[float, list[str]] = (0.0, [])
        self.device_name = "Windows default"
        self.error: str | None = None

    # ---------------------------------------------------------------- Status

    def capabilities(self) -> dict[str, bool]:
        return {
            "microphone": _has("sounddevice"),
            "stt": _has("faster_whisper"),
            "wakeword": _has("openwakeword"),
            "tts": _has("piper")
            and bool(self.svc.config.piper_voice and self.svc.config.piper_voice.is_file()),
        }

    @property
    def enabled(self) -> bool:
        """The in-app switch (stored) takes precedence over SENTINEL_VOICE_ENABLED."""
        stored = self.svc.store.get("voice_enabled")
        return stored == "1" if stored in ("0", "1") else self.svc.config.voice_enabled

    def status(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "enabled": self.enabled,
            "active": self.voice_active,
            "stt_ready": self._stt not in (None, False),
            "listening_claps": self.claps_active,
            "followup": self.in_followup(),
            "capabilities": self.capabilities(),
            "error": self.error,
            "last_transcript": self.last_transcript,
            "wakeword": self.svc.config.voice_wakeword,
            "tts_engine": self.svc.config.tts_engine,
            "tts_provider": "elevenlabs"
            if self.svc.config.tts_engine == "edge"
            and self.svc.eleven is not None
            and self.svc.eleven.configured
            else self.svc.config.tts_engine,
            "tts_error": self.svc.eleven.last_error if self.svc.eleven is not None else None,
            "stt_provider": self.stt_provider,
            "stt_error": self.cloud_stt.last_error,
            "device": self.device_name,
            "tts_voice": self.svc.config.tts_voice,
            "clap": self.svc.config.voice_clap,
        }

    def _set_state(self, state: str) -> None:
        if state == "idle" and self._followup_armed:
            self._followup_armed = False
            self.followup_until = time.time() + self.svc.config.voice_followup_s
        self.state = state
        self.svc.bus.publish("voice", {"state": state})

    # ---------------------------------------------------------------- Start/Stop

    def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._threads.append(threading.Thread(target=self._speech_worker, daemon=True, name="tts"))
        self._threads[-1].start()
        caps = self.capabilities()
        cfg = self.svc.config
        # Voice (wake word, push-to-talk) needs Whisper; claps only need the microphone
        self.voice_active = self.enabled and caps["microphone"] and caps["stt"]
        self.claps_active = cfg.voice_clap and caps["microphone"]
        if self.enabled and not self.voice_active:
            self.error = 'Voice packages missing: pip install -e ".[voice]"'
        if not (self.voice_active or self.claps_active):
            return
        self._open_stream()
        if self.voice_active:
            self._preload_stt()

    # ---------------------------------------------------------------- Microphone

    def device_choice(self) -> dict[str, Any] | None:
        raw = self.svc.store.get("voice_device")
        if not raw:
            return None
        try:
            choice = json.loads(raw)
        except ValueError:
            return None
        return choice if isinstance(choice, dict) else None

    def devices(self) -> dict[str, Any]:
        from sentinel.voice import devices

        try:
            items = devices.list_inputs()
        except Exception as exc:
            return {"devices": [], "selected": self.device_choice(), "error": str(exc)}
        return {"devices": items, "selected": self.device_choice(), "current": self.device_name}

    def set_device(self, name: str | None, api: str | None) -> dict[str, Any]:
        """Choose another microphone (None = Windows default) and switch immediately."""
        if name:
            self.svc.store.set("voice_device", json.dumps({"name": name, "api": api}))
        else:
            self.svc.store.set("voice_device", "")
        self._close_stream()
        self.error = None
        if self.voice_active or self.claps_active:
            self._open_stream()
        return self.devices()

    def _close_stream(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                log.debug("audio stream close failed", exc_info=True)

    def _open_stream(self) -> None:
        if self._stream is not None:
            return
        try:
            import sounddevice as sd

            from sentinel.voice import devices

            device = devices.resolve(self.device_choice())
            rate = int(device["rate"]) if device else SAMPLE_RATE
            self._input_rate = rate
            self.device_name = device["name"] if device else "Windows default"
            self._silent_since = time.time()
            self._stream = sd.InputStream(
                device=device["index"] if device else None,
                samplerate=rate,
                channels=1,
                dtype="float32",
                blocksize=max(1, round(BLOCK * rate / SAMPLE_RATE)),
                callback=self._on_audio,
            )
            self._stream.start()
            log.info("microphone opened: %s (%s Hz)", self.device_name, rate)
        except Exception as exc:
            log.warning("microphone failed: %s", exc)
            self.error = f"Microphone unavailable: {exc}"
            self._stream = None
            return
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(target=self._listen_worker, daemon=True, name="voice")
            self._worker.start()
            self._threads.append(self._worker)
        self._set_state("idle")

    def set_enabled(self, on: bool) -> dict[str, Any]:
        """Turn voice on or off in the running app (no restart)."""
        self.svc.store.set("voice_enabled", "1" if on else "0")
        caps = self.capabilities()
        self.voice_active = on and caps["microphone"] and caps["stt"]
        self.error = None
        if on and not self.voice_active:
            self.error = "Microphone or speech recognition is missing in this installation"
        if self.voice_active:
            self._open_stream()
            self._preload_stt()
        elif not on:
            self._ptt.clear()
            self.end_followup()
        self.svc.audit.append(
            actor="user",
            source="user",
            action="voice_toggle",
            params={"on": on},
            decision="execute",
        )
        return self.status()

    def _preload_stt(self) -> None:
        """Load Whisper in the background on first use (downloads the model once)."""
        if self._stt is not None:
            return

        def load() -> None:
            previous = self.state
            self._set_state("loading")
            try:
                self._load_stt()
            except Exception as exc:
                self.error = f"Speech recognition could not be loaded: {exc}"
                self._stt = None
            finally:
                self._set_state(previous if previous not in ("loading", "off") else "idle")

        threading.Thread(target=load, daemon=True, name="stt-load").start()

    def stop(self) -> None:
        self._stop.set()
        self._close_stream()

    # ---------------------------------------------------------------- Models

    def _load_stt(self) -> Any:
        if self._stt is None:
            from faster_whisper import WhisperModel

            self._stt = WhisperModel(
                self.svc.config.whisper_model, device="cpu", compute_type="int8"
            )
        return self._stt

    def _load_wake(self) -> Any:
        if self._wake is None and self.svc.config.voice_wakeword and _has("openwakeword"):
            try:
                from openwakeword.model import Model

                folder = wake_model_dir(self.svc.config.data_dir)
                self._wake = Model(
                    wakeword_models=[str(folder / WAKE_FILES["hey_jarvis"])],
                    melspec_model_path=str(folder / WAKE_FILES["melspec"]),
                    embedding_model_path=str(folder / WAKE_FILES["embedding"]),
                    inference_framework="onnx",
                )
            except Exception as exc:
                log.warning("wake word unavailable: %s", exc)
                self.error = (
                    '"Hey Jarvis" unavailable (model missing) - the microphone button still works.'
                )
                self._wake = False
        return self._wake or None

    def _load_tts(self) -> Any:
        if self._tts is None:
            voice_path = self.svc.config.piper_voice
            if voice_path and voice_path.is_file() and _has("piper"):
                try:
                    from piper import PiperVoice

                    self._tts = PiperVoice.load(str(voice_path))
                except Exception as exc:
                    log.warning("piper failed: %s", exc)
                    self._tts = False
            else:
                self._tts = False
        return self._tts or None

    # ---------------------------------------------------------------- Recording

    def _on_audio(self, indata: np.ndarray, frames: int, _time: Any, _status: Any) -> None:
        block = indata[:, 0]
        if self._input_rate != SAMPLE_RATE and len(block):
            # Resample to 16 kHz (Whisper and the wake word expect 16 kHz)
            target = max(1, round(len(block) * SAMPLE_RATE / self._input_rate))
            block = np.interp(
                np.linspace(0, len(block) - 1, target), np.arange(len(block)), block
            ).astype(np.float32)
        self.level = float(np.sqrt(np.mean(block * block))) if len(block) else 0.0
        now = time.time()
        if self.level > 1e-5:
            self._silent_since = now
            if self.error and self.error.startswith(SILENT_HINT[:20]):
                self.error = None
        elif now - self._silent_since > SILENT_AFTER_S and not self.error:
            self.error = SILENT_HINT
        with contextlib.suppress(queue.Full):
            self._audio.put_nowait(block.copy())

    def _listen_worker(self) -> None:
        wake = self._load_wake() if self.voice_active else None
        while not self._stop.is_set():
            try:
                block = self._audio.get(timeout=0.5)
            except queue.Empty:
                continue
            if self.voice_active and wake is None and self._wake is None:
                wake = self._load_wake()  # voice was switched on at runtime
            if not self.voice_active:
                wake = None
            if self.state == "speaking":
                continue  # do not treat our own announcements as commands
            if self.voice_active and self._ptt.is_set():
                self._record_and_handle(first=block, until_release=True)
                continue
            if self.voice_active and self._listen.is_set():
                self._listen.clear()
                log.info("listen once")
                self._record_and_handle(first=block, until_release=False)
                continue
            if self.claps_active and self.clap.process(block):
                self.wake_app("clap")
                continue
            if self.in_followup() and float(np.sqrt(np.mean(block * block))) > SILENCE_RMS * 3:
                self.followup_until = 0.0
                self._record_and_handle(first=block, until_release=False)
                continue
            if wake is not None:
                scores = wake.predict((block * 32767).astype(np.int16))
                if max(scores.values(), default=0.0) >= WAKE_THRESHOLD:
                    log.info("wake word detected")
                    wake.reset()
                    self._record_and_handle(first=None, until_release=False)

    def in_followup(self, now: float | None = None) -> bool:
        return self.voice_active and (now or time.time()) < self.followup_until

    def end_followup(self) -> None:
        self._followup_armed = False
        self.followup_until = 0.0

    def wake_app(self, reason: str) -> None:
        """Triple clap: bring the app to the front and start the world briefing."""
        self.svc.audit.append(
            actor="user",
            source="user",
            action="wake",
            params={"reason": reason},
            decision="execute",
        )
        morning = self.svc.reports.is_first_wake_today() if self.svc.reports else False
        self.svc.bus.publish("wake", {"reason": reason, "morning": morning})

    def _record_and_handle(self, *, first: np.ndarray | None, until_release: bool) -> None:
        self._set_state("listening")
        chunks = [first] if first is not None else []
        started = time.time()
        last_voice = time.time()
        heard = False
        while time.time() - started < MAX_RECORD_S and not self._stop.is_set():
            try:
                block = self._audio.get(timeout=0.5)
            except queue.Empty:
                block = None
            if block is not None:
                chunks.append(block)
                if float(np.sqrt(np.mean(block * block))) > SILENCE_RMS:
                    heard, last_voice = True, time.time()
            if until_release:
                if not self._ptt.is_set():
                    break
            elif (heard and time.time() - last_voice > END_SILENCE_S) or (
                not heard and time.time() - started > 4.0
            ):
                break
        audio = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)
        self._handle_audio(audio)

    def _handle_audio(self, audio: np.ndarray) -> None:
        level = float(np.sqrt(np.mean(audio * audio))) if len(audio) else 0.0
        log.info("recorded %.1f s, level %.4f", len(audio) / SAMPLE_RATE, level)
        if len(audio) < SAMPLE_RATE // 3:
            self._set_state("idle")
            return
        self._set_state("thinking")
        text = self._transcribe_cloud(audio)
        if text is not None:
            log.info("transcribed %d chars (elevenlabs)", len(text))
            self.handle_transcript(text)
            return
        prompt = ", ".join(self.keyterms()[:30])
        try:
            try:
                segments, _info = self._load_stt().transcribe(
                    audio, language="en", beam_size=1, vad_filter=True, initial_prompt=prompt
                )
                segments = list(segments)
            except Exception as exc:
                # Recognise anyway without the VAD model (e.g. missing file)
                log.warning("transcribe with vad failed, retrying without: %s", exc)
                segments, _info = self._load_stt().transcribe(
                    audio, language="en", beam_size=1, vad_filter=False, initial_prompt=prompt
                )
            text = " ".join(s.text.strip() for s in segments).strip()
        except Exception as exc:
            self.error = f"Speech recognition failed: {exc}"
            self._set_state("idle")
            return
        log.info("transcribed %d chars", len(text))
        self.handle_transcript(text)

    def keyterms(self) -> list[str]:
        """Words Jarvis should understand especially well: names and terms from the brain."""
        cached_at, terms = self._keyterms
        if time.time() - cached_at < 300 and terms:
            return terms
        terms = ["Jarvis", "SENTINEL", self.svc.config.user_name]
        brain = getattr(self.svc, "brain", None)
        if brain is not None:
            try:
                rows = self.svc.db.query(
                    "SELECT label FROM brain_nodes WHERE origin IN ('user', 'seed') "
                    "AND category IN ('ich', 'quellen', 'interessen', 'projekte', 'geraete', "
                    "'skills', 'ziele') ORDER BY hits DESC, updated DESC LIMIT 60"
                )
                terms += [r["label"] for r in rows if not r["label"].startswith(("Stil", "Style"))]
            except Exception as exc:  # the brain is optional
                log.debug("keyterms from brain failed: %s", exc)
        from sentinel.voice.elevenlabs import clean_keyterms

        terms = clean_keyterms(terms)
        self._keyterms = (time.time(), terms)
        return terms

    def _transcribe_cloud(self, audio: np.ndarray) -> str | None:
        """ElevenLabs Scribe when possible; None = recognise locally."""
        from sentinel.voice.elevenlabs import ElevenLabsError, wav_bytes

        if self.svc.config.stt_engine != "elevenlabs" or not self.cloud_stt.available:
            self.stt_provider = "local"
            return None
        try:
            text = self.cloud_stt.transcribe(wav_bytes(audio), keyterms=self.keyterms())
        except ElevenLabsError as exc:
            log.info("cloud stt failed, using local: %s", exc)
            self.stt_provider = "local"
            return None
        self.stt_provider = "elevenlabs"
        return text

    def handle_transcript(self, text: str) -> None:
        """Process recognised text. The emergency stop is detected locally, before anything else."""
        text = text.strip()
        self.last_transcript = redact(text)
        if not text:
            self._set_state("idle")
            return
        self.svc.bus.publish("transcript", {"text": self.last_transcript})
        if is_stop_command(text):
            self.svc.engine.halt(actor="user", via="voice")
            self.say("Emergency stop active. Everything halted.")
            self._set_state("idle")
            return
        if self._loop is None:
            self._set_state("idle")
            return

        async def emit(event: dict[str, Any]) -> None:
            self.svc.bus.publish("chat", {"conversation": "voice", **event})

        future = asyncio.run_coroutine_threadsafe(
            self.svc.chat.send(text, conversation="voice", emit=emit, via="voice"), self._loop
        )
        try:
            result = future.result(timeout=180)
            if FAREWELL.search(text):
                self.end_followup()
            elif self.svc.config.voice_followup_s:
                self._followup_armed = True
            self.say(result.get("text", ""))
        except Exception as exc:
            log.warning("voice chat failed: %s", type(exc).__name__)
            self.error = f"Chat failed: {exc}"
        finally:
            if self.state != "speaking":
                self._set_state("idle")

    # ---------------------------------------------------------------- Push-to-Talk

    def set_speaking(self, on: bool) -> None:
        """The app is currently playing the voice: do not record anything as a command meanwhile."""
        if on:
            self._set_state("speaking")
        elif self.state == "speaking":
            self._set_state("idle")

    def ptt_start(self) -> bool:
        if self._stream is None or not self.voice_active:
            log.info(
                "ptt refused: stream=%s active=%s", self._stream is not None, self.voice_active
            )
            return False
        log.info("ptt start")
        self._ptt.set()
        return True

    def listen_once(self) -> bool:
        """One click on the microphone: listen until the user stops speaking."""
        if self._stream is None or not self.voice_active:
            return False
        self._listen.set()
        return True

    def ptt_stop(self) -> None:
        self._ptt.clear()

    # ---------------------------------------------------------------- Output

    def say(self, text: str) -> None:
        text = redact(text).strip()
        if not text:
            return
        try:
            self._speech.put_nowait(text[:1500])
        except queue.Full:
            log.info("speech queue full")

    def say_blocking(self, text: str) -> bool:
        """Speak immediately and wait. False if Piper is missing (the app then speaks itself)."""
        text = redact(text).strip()[:1500]
        if self.svc.config.tts_engine != "piper":
            return False  # the app speaks itself (online voice)
        tts = self._load_tts()
        if not text or tts is None or not _has("sounddevice"):
            return False
        previous = self.state
        self._set_state("speaking")
        try:
            with self._play_lock:
                self._play(tts, text)
        except Exception as exc:
            self.error = f"Speech output failed: {exc}"
            return False
        finally:
            self._set_state(previous if previous not in ("speaking", "thinking") else "idle")
        return True

    def _speech_worker(self) -> None:
        while not self._stop.is_set():
            try:
                text = self._speech.get(timeout=0.5)
            except queue.Empty:
                continue
            tts = self._load_tts() if self.svc.config.tts_engine == "piper" else None
            if tts is None or not _has("sounddevice"):
                # The app plays the voice (online voice or Web Speech)
                self.svc.bus.publish("say", {"text": text})
                continue
            previous = self.state
            self._set_state("speaking")
            try:
                with self._play_lock:
                    self._play(tts, text)
            except Exception as exc:
                self.error = f"Speech output failed: {exc}"
                self.svc.bus.publish("say", {"text": text})
            finally:
                self._set_state(previous if previous not in ("speaking", "thinking") else "idle")

    def _play(self, tts: Any, text: str) -> None:
        import sounddevice as sd

        rate = int(tts.config.sample_rate)
        parts: list[np.ndarray] = []
        if hasattr(tts, "synthesize_stream_raw"):
            for raw in tts.synthesize_stream_raw(text):
                parts.append(np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768)
        else:
            for chunk in tts.synthesize(text):
                samples = np.asarray(chunk.audio_int16_array, dtype=np.int16)
                parts.append(samples.astype(np.float32) / 32768)
        if parts:
            sd.play(np.concatenate(parts), rate)
            sd.wait()
