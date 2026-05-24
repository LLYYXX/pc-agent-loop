"""动态音频设备选择 - 每次使用前查询当前默认设备"""
import pyaudio

def get_input_index():
    p = pyaudio.PyAudio()
    idx = p.get_default_input_device_info()["index"]
    p.terminate()
    return int(idx)

def get_output_index():
    p = pyaudio.PyAudio()
    idx = p.get_default_output_device_info()["index"]
    p.terminate()
    return int(idx)
