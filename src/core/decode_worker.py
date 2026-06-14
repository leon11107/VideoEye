"""Background decode worker: serializes all decoder access on one thread."""

from PyQt6.QtCore import QThread, pyqtSignal, QMutex, QWaitCondition

from .decoder import Decoder


class DecodeWorker(QThread):
    """Serializes all decoder access on one thread.

    PyAV's decoder is not thread-safe and decoding a 4K frame takes long
    enough to stall the UI, so every decode runs here instead. Only the most
    recent request is served -- intermediate requests during rapid scrubbing
    are dropped. The shared lock is also held by open()/close() on the UI
    thread, so decoder lifecycle never overlaps a decode.
    """

    ready = pyqtSignal(int, object, object)  # index, rgb|None, analysis|None

    def __init__(self, decoder: Decoder, lock: QMutex):
        super().__init__()
        self._decoder = decoder
        self._lock = lock
        self._mutex = QMutex()
        self._cond = QWaitCondition()
        self._pending = -1
        self._abort = False

    def request(self, index: int) -> None:
        self._mutex.lock()
        self._pending = index
        self._cond.wakeAll()
        self._mutex.unlock()

    def stop(self) -> None:
        self._mutex.lock()
        self._abort = True
        self._cond.wakeAll()
        self._mutex.unlock()
        self.wait()

    def run(self) -> None:
        while True:
            self._mutex.lock()
            while self._pending < 0 and not self._abort:
                self._cond.wait(self._mutex)
            if self._abort:
                self._mutex.unlock()
                return
            index = self._pending
            self._pending = -1
            self._mutex.unlock()

            rgb = analysis = None
            self._lock.lock()
            try:
                if self._decoder.is_open:
                    rgb = self._decoder.decode_frame(index)
                    if rgb is not None:
                        analysis = self._decoder.get_analysis(index)
            except Exception:
                rgb = analysis = None
            finally:
                self._lock.unlock()

            # Always emit the decoded frame. The worker only ever decodes the
            # latest pending request (intermediate ones are overwritten before
            # it picks them up), so emissions are already near-current; during
            # playback this delivers every decoded frame for real-time display.
            self.ready.emit(index, rgb, analysis)
