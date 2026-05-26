"""预生成所有提示音频 - edge-tts流式 + miniaudio内存解码"""
import asyncio
import math
import os
import sys
import wave

from sop_rules import SOP_RULES

AUDIO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "alerts_audio")
VOICE = "zh-CN-XiaoxiaoNeural"
RATE = "+35%"
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
    with wave.open(path, "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(frames)


async def tts_to_wav(text, out_path):
    import edge_tts
    import miniaudio

    comm = edge_tts.Communicate(text, VOICE, rate=RATE)
    audio_data = b""
    async for chunk in comm.stream():
        if chunk["type"] == "audio":
            audio_data += chunk["data"]
    decoded = miniaudio.decode(audio_data, output_format=miniaudio.SampleFormat.SIGNED16)
    with wave.open(out_path, "w") as wf:
        wf.setnchannels(decoded.nchannels)
        wf.setsampwidth(2)
        wf.setframerate(decoded.sample_rate)
        wf.writeframes(bytes(decoded.samples))


async def generate_all():
    alerts = {}
    for stage in SOP_RULES["stages"]:
        for must in stage["must"]:
            alerts[must.get("audio_id", must["id"])] = must["alert"]
        for fb in stage.get("forbidden", []):
            alerts[fb.get("audio_id", fb["id"])] = fb["alert"]
    for must in SOP_RULES.get("insert_rules", []):
        alerts[must.get("audio_id", must["id"])] = must["alert"]
    for i, stage in enumerate(SOP_RULES["stages"][:-1]):
        if stage.get("hint_next"):
            next_stage = SOP_RULES["stages"][i + 1]
            audio_id = stage.get("hint_next_audio_id", f"hint_next_{next_stage['id']}")
            alerts[audio_id] = f"该{next_stage['name']}了"

    if "--check" in sys.argv:
        missing = [
            audio_id
            for audio_id in ("ding", *sorted(alerts))
            if not os.path.exists(os.path.join(AUDIO_DIR, f"{audio_id}.wav"))
        ]
        if missing:
            print("缺少音频:", ", ".join(missing))
            raise SystemExit(1)
        print("音频完整")
        return

    os.makedirs(AUDIO_DIR, exist_ok=True)
    generate_ding()
    print("  生成: ding")

    for audio_id, text in sorted(alerts.items()):
        wav_path = os.path.join(AUDIO_DIR, f"{audio_id}.wav")
        if os.path.exists(wav_path):
            print(f"  跳过: {audio_id}")
            continue
        print(f"  生成: {audio_id} ...", end=" ", flush=True)
        await tts_to_wav(text, wav_path)
        print("OK")
    print(f"\n完成! 共{len(alerts)}条 -> {AUDIO_DIR}")


if __name__ == "__main__":
    asyncio.run(generate_all())
