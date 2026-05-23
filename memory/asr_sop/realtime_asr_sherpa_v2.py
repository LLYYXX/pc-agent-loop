# realtime_asr_sherpa_v2.py - 双线程+固定窗口切段
import sherpa_onnx, pyaudio, numpy as np, threading, queue, time
from datetime import datetime

model_dir = "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17"
recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
    model=f"{model_dir}/model.int8.onnx",
    tokens=f"{model_dir}/tokens.txt",
    num_threads=2, sample_rate=16000, use_itn=True,
)

SEGMENT_SEC = 10  # 每10秒切一段
OUTPUT_FILE = "asr_output.txt"

audio_q = queue.Queue()
stop_event = threading.Event()

def capture_thread():
    p = pyaudio.PyAudio()
    stream = p.open(format=pyaudio.paInt16, channels=1, rate=16000,
                    input=True, frames_per_buffer=1600)
    try:
        while not stop_event.is_set():
            data = stream.read(1600, exception_on_overflow=False)
            audio_q.put(np.frombuffer(data, dtype=np.int16))
    finally:
        stream.stop_stream(); stream.close(); p.terminate()

def recognize_thread():
    buf = []
    chunks_per_seg = int(16000 * SEGMENT_SEC / 1600)  # 10秒=100个chunk
    count = 0
    while not stop_event.is_set():
        try:
            samples = audio_q.get(timeout=0.5)
        except queue.Empty:
            continue
        buf.append(samples)
        count += 1
        if count >= chunks_per_seg:
            all_samples = np.concatenate(buf).astype(np.float32) / 32768.0
            s = recognizer.create_stream()
            s.accept_waveform(16000, all_samples)
            recognizer.decode_stream(s)
            text = s.result.text.strip()
            if text:
                now = datetime.now().strftime("%H:%M:%S")
                print(f"[{now}] {text}")
                with open(OUTPUT_FILE, 'a', encoding='utf-8') as f:
                    f.write(f"[{now}] {text}\n")
            buf, count = [], 0

print(f"=== SenseVoice 双线程实时识别 (每{SEGMENT_SEC}秒) ===")
print(f"输出: {OUTPUT_FILE} | Ctrl+C 停止\n")

t1 = threading.Thread(target=capture_thread, daemon=True)
t2 = threading.Thread(target=recognize_thread, daemon=True)
t1.start(); t2.start()

try:
    while True: t1.join(0.5)
except KeyboardInterrupt:
    print("\n停止中...")
    stop_event.set(); t1.join(2); t2.join(2)
    print("已停止")