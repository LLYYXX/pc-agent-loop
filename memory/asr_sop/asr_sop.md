# ASR SOP (语音识别)

## ★推荐方案: sherpa-onnx + SenseVoice (已验证 2025-06-22)
- 安装: `pip install sherpa-onnx` (无需proxy, 纯pip)
- 模型: sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17 (~230MB int8)
  - 下载: `curl -L -o sv.tar.bz2 --proxy http://127.0.0.1:2082 https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17.tar.bz2 && tar xf sv.tar.bz2`
  - 本机已下载: temp/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/
- 性能: 加载1.4s | RTF=0.025 (40x实时) | 支持中英日韩粤5语言 | CPU int8

### 用法
```python
import sherpa_onnx, wave, numpy as np
model_dir = './sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17'
recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
    model=f'{model_dir}/model.int8.onnx',
    tokens=f'{model_dir}/tokens.txt',
    language='zh', use_itn=True, num_threads=4)
with wave.open('audio.wav', 'rb') as wf:
    sr, data = wf.getframerate(), wf.readframes(wf.getnframes())
samples = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
stream = recognizer.create_stream()
stream.accept_waveform(sr, samples.tolist())
recognizer.decode_stream(stream)
text = stream.result.text
```

## 备选1: faster-whisper
- 安装: `pip install faster-whisper` (需proxy, 依赖ctranslate2)
- 模型自动从HuggingFace下载(需proxy环境变量), 缓存在 `~/.cache/huggingface/`
- 模型选择: base(~150MB,够用) / small(~500MB) / medium(~1.5GB,高精度)

```python
import os
os.environ['HTTPS_PROXY'] = 'http://127.0.0.1:2082'
from faster_whisper import WhisperModel
model = WhisperModel("base", device="cpu", compute_type="int8")
segments, info = model.transcribe("audio.wav", language="zh", beam_size=5)
text = "".join([s.text for s in segments])
```

## 备选2: vosk (最轻量)
- 安装: `pip install vosk` (需proxy)
- 模型需手动下载解压: https://alphacephei.com/vosk/models

## 对比
| | sherpa-onnx+SenseVoice | faster-whisper | vosk |
|---|---|---|---|
| 中文质量 | ★★★极好 | ★★好(base) | ★一般 |
| 多语言 | 中英日韩粤 | 99语言 | 按模型 |
| 安装 | pip一键,模型需下 | pip+模型自动下 | pip+手动下 |
| 速度(CPU) | RTF=0.025(极快) | RTF~0.3 | 快 |

## 注意
- 输入须为WAV(PCM), 微信silk语音需先转换(见wechat_db_sop语音提取section)
- sherpa-onnx自动重采样(支持非16kHz输入)
- SenseVoice API: `OfflineRecognizer.from_sense_voice(model=, tokens=, language=, use_itn=, num_threads=)`

## 实时识别 (已验证 2026-03-10)
- **推荐**: sherpa-onnx + SenseVoice + VAD分段
  - 完整实现: `realtime_asr_sherpa_v2.py` (双线程录音+识别, 10s窗口切段)
  - 优势: 支持远距离说话、连续说话、识别准确
  - 对比Google Speech API: Google不支持远方人说话和连续说话场景
- 参数: 静音阈值150, 静音1.2s触发识别, 最少0.5s语音