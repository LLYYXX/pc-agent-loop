"""音频设备：每次使用前读系统默认设备，支持热插拔后重选"""
import pyaudio

_CHECK_EVERY_READS = 30  # ~3s @ 0.1s/frame


def get_input_index():
    p = pyaudio.PyAudio()
    try:
        return int(p.get_default_input_device_info()["index"])
    finally:
        p.terminate()


def get_output_index():
    p = pyaudio.PyAudio()
    try:
        return int(p.get_default_output_device_info()["index"])
    finally:
        p.terminate()


def open_input_stream(p, rate, frames_per_buffer):
    """按当前默认输入打开流；失败则退回不显式 index。"""
    kwargs = dict(
        format=pyaudio.paInt16,
        channels=1,
        rate=rate,
        input=True,
        frames_per_buffer=frames_per_buffer,
    )
    try:
        idx = get_input_index()
        return p.open(input_device_index=idx, **kwargs), idx
    except OSError:
        return p.open(**kwargs), None


def open_output_stream(p, wf):
    kwargs = dict(
        format=p.get_format_from_width(wf.getsampwidth()),
        channels=wf.getnchannels(),
        rate=wf.getframerate(),
        output=True,
    )
    try:
        idx = get_output_index()
        return p.open(output_device_index=idx, **kwargs), idx
    except OSError:
        return p.open(**kwargs), None


def close_stream(stream):
    if not stream:
        return
    try:
        stream.stop_stream()
        stream.close()
    except OSError:
        pass
