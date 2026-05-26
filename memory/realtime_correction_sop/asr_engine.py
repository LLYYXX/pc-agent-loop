"""ASR引擎 - 纯音频采集+识别，回调输出增量文本"""
import os
import threading
import time

import numpy as np
import pyaudio
import sherpa_onnx

from io_devices import _CHECK_EVERY_READS, close_stream, get_input_index, open_input_stream

RATE = 16000
WINDOW_SEC = 5.0
TRIGGER_SEC = 0.5
MODEL_DIR = os.path.join(os.path.dirname(__file__), "..", "..",
    "temp", "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17")

_recognizer = None
_ring_buf = np.zeros(int(RATE * WINDOW_SEC), dtype=np.float32)
_buf_pos = 0
_buf_lock = threading.Lock()
_last_text = ""
_stop = threading.Event()
_on_text = None  # callback(new_part)


def _get_new_part(old, new):
    prefix = os.path.commonprefix([old, new])
    return new[len(prefix):] if prefix else new


def _capture():
    p = pyaudio.PyAudio()
    stream = None
    device_idx = None
    read_count = 0

    def _reopen(reason=""):
        nonlocal stream, device_idx, read_count
        close_stream(stream)
        if reason:
            print(f"[ASR] {reason}", flush=True)
        stream, device_idx = open_input_stream(p, RATE, int(RATE * 0.1))
        read_count = 0

    _reopen()
    global _buf_pos
    while not _stop.is_set():
        try:
            data = stream.read(int(RATE * 0.1), exception_on_overflow=False)
        except (OSError, IOError) as e:
            _reopen(f"输入异常，重选默认设备: {e}")
            time.sleep(0.3)
            continue

        read_count += 1
        if device_idx is not None and read_count >= _CHECK_EVERY_READS:
            read_count = 0
            try:
                if get_input_index() != device_idx:
                    _reopen("默认输入设备已变更，重新打开")
            except OSError as e:
                _reopen(f"查询输入设备失败: {e}")

        samples = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
        with _buf_lock:
            n = len(samples)
            end = _buf_pos + n
            if end <= len(_ring_buf):
                _ring_buf[_buf_pos:end] = samples
            else:
                tail = len(_ring_buf) - _buf_pos
                _ring_buf[_buf_pos:] = samples[:tail]
                _ring_buf[:n - tail] = samples[tail:]
            _buf_pos = end % len(_ring_buf)

    close_stream(stream)
    p.terminate()


def _recognize():
    global _last_text
    time.sleep(1.0)
    while not _stop.is_set():
        time.sleep(TRIGGER_SEC)
        with _buf_lock:
            audio = np.roll(_ring_buf, -_buf_pos).copy()
        s = _recognizer.create_stream()
        s.accept_waveform(RATE, audio.tolist())
        _recognizer.decode_stream(s)
        text = s.result.text.strip()
        if not text or text == _last_text:
            if _on_text:
                _on_text(None)  # tick信号，无新文本
            continue
        new_part = _get_new_part(_last_text, text)
        _last_text = text
        if new_part and _on_text:
            _on_text(new_part)


def start(on_text):
    """启动ASR引擎。on_text(new_part_or_None) 每0.5s回调一次"""
    global _recognizer, _on_text, _last_text, _buf_pos
    _on_text = on_text
    _last_text = ""
    _buf_pos = 0
    _stop.clear()
    _ring_buf[:] = 0
    _recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
        model=os.path.join(MODEL_DIR, "model.int8.onnx"),
        tokens=os.path.join(MODEL_DIR, "tokens.txt"),
        language="zh", use_itn=True, num_threads=4)
    t1 = threading.Thread(target=_capture, daemon=True)
    t2 = threading.Thread(target=_recognize, daemon=True)
    t1.start()
    t2.start()
    return t1, t2


def stop():
    _stop.set()
