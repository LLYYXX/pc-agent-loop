"""预生成所有提示音频 - edge-tts流式 + miniaudio内存解码"""
import asyncio, math, os, wave
from sop_rules import SOP_RULES
import edge_tts, miniaudio

AUDIO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "alerts_audio")
VOICE = "zh-CN-YunxiNeural"
RATE = "+40%"
DING_PATH = os.path.join(AUDIO_DIR, "ding.wav")

def generate_ding(path=DING_PATH, sample_rate=22050):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    frames = bytearray()
    notes = [(440, 0.075, 3200), (587, 0.155, 3800)]
    gap = 0.024
    for freq, duration, gain in notes:
        total = round(sample_rate * duration)
        for i in range(total):
            t = i / sample_rate
            x = i / max(total - 1, 1)
            attack = min(1.0, t / 0.024)
            release = max(0.0, 1.0 - x) ** 2.6
            amp = int(gain * attack * release * math.sin(2 * math.pi * freq * t))
            frames += amp.to_bytes(2, "little", signed=True)
        frames += b"\x00\x00" * round(sample_rate * gap)
    with wave.open(path, 'w') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(frames)

async def tts_to_wav(text, out_path):
    comm = edge_tts.Communicate(text, VOICE, rate=RATE)
    audio_data = b""
    async for chunk in comm.stream():
        if chunk["type"] == "audio":
            audio_data += chunk["data"]
    decoded = miniaudio.decode(audio_data, output_format=miniaudio.SampleFormat.SIGNED16)
    with wave.open(out_path, 'w') as wf:
        wf.setnchannels(decoded.nchannels)
        wf.setsampwidth(2)
        wf.setframerate(decoded.sample_rate)
        wf.writeframes(bytes(decoded.samples))

async def generate_all():
    os.makedirs(AUDIO_DIR, exist_ok=True)
    generate_ding()
    print("  生成: ding")

    alerts = set()
    for stage in SOP_RULES["stages"]:
        for must in stage["must"]:
            alerts.add(must["alert"])
        for fb in stage.get("forbidden", []):
            alerts.add(fb["alert"])

    for text in sorted(alerts):
        wav_path = os.path.join(AUDIO_DIR, f"{text}.wav")
        if os.path.exists(wav_path):
            print(f"  跳过: {text}")
            continue
        print(f"  生成: {text} ...", end=" ", flush=True)
        await tts_to_wav(text, wav_path)
        print("OK")
    print(f"\n完成! 共{len(alerts)}条 -> {AUDIO_DIR}")

if __name__ == "__main__":
    asyncio.run(generate_all())
