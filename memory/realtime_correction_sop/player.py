"""播报模块 - 预生成音频播放"""
import os
import wave

import pyaudio

from io_devices import close_stream, open_output_stream

AUDIO_DIR = os.path.join(os.path.dirname(__file__), "alerts_audio")


def play(audio_id):
    """按音频名播放预生成音频；失败时重选当前默认输出再试一次"""
    path = os.path.join(AUDIO_DIR, f"{audio_id}.wav")
    if not os.path.exists(path):
        print(f"[播报缺失] {audio_id}", flush=True)
        return

    for attempt in range(2):
        p = None
        stream = None
        wf = None
        try:
            wf = wave.open(path, "rb")
            p = pyaudio.PyAudio()
            stream, _ = open_output_stream(p, wf)
            data = wf.readframes(1024)
            while data:
                stream.write(data)
                data = wf.readframes(1024)
            return
        except Exception as e:
            if attempt == 0:
                print(f"[播报] 输出设备异常，重选默认设备: {e}", flush=True)
            else:
                print(f"[播报错误] {e}", flush=True)
        finally:
            close_stream(stream)
            if p:
                p.terminate()
            if wf:
                wf.close()
