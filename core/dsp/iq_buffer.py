import numpy as np
import threading

class IQRingBuffer:
    def __init__(self, capacity: int = 2_000_000):
        self.capacity = int(capacity)
        self.buf = np.zeros(self.capacity, dtype=np.complex64)
        self.wpos = 0
        self.lock = threading.Lock()

    def push(self, iq: np.ndarray):
        iq = np.asarray(iq, dtype=np.complex64)
        n = iq.size
        if n <= 0:
            return
        if n >= self.capacity:
            iq = iq[-self.capacity:]
            n = iq.size

        with self.lock:
            end = self.wpos + n
            if end <= self.capacity:
                self.buf[self.wpos:end] = iq
            else:
                k = self.capacity - self.wpos
                self.buf[self.wpos:] = iq[:k]
                self.buf[:end - self.capacity] = iq[k:]
            self.wpos = end % self.capacity

    def latest(self, n: int) -> np.ndarray:
        n = min(int(n), self.capacity)
        with self.lock:
            end = self.wpos
            start = (end - n) % self.capacity
            if start < end:
                out = self.buf[start:end].copy()
            else:
                out = np.concatenate((self.buf[start:], self.buf[:end])).copy()
        return out
