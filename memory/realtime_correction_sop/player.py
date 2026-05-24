"""播报模块 - 预生成音频播放"""
import os, wave, pyaudio
from io_devices import get_output_index

AUDIO_DIR = os.path.join(os.path.dirname(__file__), "alerts_audio")

def play(audio_id):
    """按音频名播放预生成音频"""
    path = os.path.join(AUDIO_DIR, f"{audio_id}.wav")
    if not os.path.exists(path):
        print(f"[播报缺失] {audio_id}", flush=True)
        return
    try:
        wf = wave.open(path, 'rb')
        p = pyaudio.PyAudio()
        stream = p.open(format=p.get_format_from_width(wf.getsampwidth()),
                       channels=wf.getnchannels(),
                       rate=wf.getframerate(),
                       output=True,
                       output_device_index=get_output_index())
        data = wf.readframes(1024)
        while data:
            stream.write(data)
            data = wf.readframes(1024)
        stream.stop_stream()
        stream.close()
        p.terminate()
        wf.close()
    except Exception as e:
        print(f"[播报错误] {e}", flush=True)
