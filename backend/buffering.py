"""Small, immutable-update rolling buffers. No trained feature logic is duplicated."""
from dataclasses import dataclass, field
from statistics import median


@dataclass
class WindowBuffer:
    boot_id: str
    fingerprint: str
    last_seen: float
    samples: list[dict] = field(default_factory=list)

    def append(self, samples, now):
        return WindowBuffer(self.boot_id, self.fingerprint, now, (self.samples + samples)[-20:])

    def valid_window(self):
        if len(self.samples) != 20:
            return False
        intervals = [b["t_ms"] - a["t_ms"] for a, b in zip(self.samples, self.samples[1:])]
        return (3700 <= self.samples[-1]["t_ms"] - self.samples[0]["t_ms"] <= 4100
                and all(100 <= interval <= 300 for interval in intervals)
                and 120 <= median(intervals) <= 280)
