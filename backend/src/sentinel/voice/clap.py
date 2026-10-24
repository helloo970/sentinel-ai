"""Clap-sequence detection from microphone blocks (pure numpy, light on CPU).

A clap is a short, steep level rise far above the noise floor that falls off quickly.
Default: three claps, each 0.15-0.8 s apart, trigger it. Only the level is evaluated;
audio is neither stored nor sent.
"""

from __future__ import annotations

import numpy as np

FRAME_MS = 10


class ClapDetector:
    def __init__(
        self,
        sample_rate: int = 16000,
        *,
        claps: int = 3,
        ratio: float = 8.0,
        min_level: float = 0.08,
        max_clap_ms: int = 80,
        min_gap_s: float = 0.15,
        max_gap_s: float = 0.8,
        cooldown_s: float = 1.5,
    ) -> None:
        self.sample_rate = sample_rate
        self.claps = max(2, claps)
        self.frame = sample_rate * FRAME_MS // 1000
        self.ratio = ratio
        self.min_level = min_level
        self.max_clap_frames = max_clap_ms // FRAME_MS
        self.min_gap = min_gap_s
        self.max_gap = max_gap_s
        self.cooldown = cooldown_s
        self.noise = 0.01
        self._time = 0.0
        self._loud_frames = 0
        self._clap_times: list[float] = []
        self._last_trigger = -1e9
        self._buffer = np.zeros(0, dtype=np.float32)

    def process(self, samples: np.ndarray) -> bool:
        """Process one block (float32, -1..1). True when a full clap sequence is detected."""
        data = np.concatenate([self._buffer, samples.astype(np.float32).ravel()])
        frames = len(data) // self.frame
        self._buffer = data[frames * self.frame :]
        triggered = False
        for index in range(frames):
            chunk = data[index * self.frame : (index + 1) * self.frame]
            triggered |= self._frame(float(np.sqrt(np.mean(chunk * chunk))))
        return triggered

    def _frame(self, rms: float) -> bool:
        self._time += FRAME_MS / 1000
        loud = rms > max(self.min_level, self.noise * self.ratio)
        if loud:
            self._loud_frames += 1
            return False
        triggered = False
        if 0 < self._loud_frames <= self.max_clap_frames:
            triggered = self._on_clap(self._time)
        self._loud_frames = 0
        # Track the noise floor slowly, and only during quiet phases
        self.noise = 0.98 * self.noise + 0.02 * rms
        return triggered

    def _on_clap(self, now: float) -> bool:
        if now - self._last_trigger < self.cooldown:
            return False
        if self._clap_times:
            gap = now - self._clap_times[-1]
            if gap < self.min_gap:
                return False  # echo of the same clap
            if gap > self.max_gap:
                self._clap_times = []
        self._clap_times.append(now)
        if len(self._clap_times) >= self.claps:
            self._clap_times = []
            self._last_trigger = now
            return True
        return False
