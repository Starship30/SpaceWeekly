# AI 配音接口（预留）

`ai/speech.py` 提供独立连接接口。现已有可选的 `ai/qwen_tts.py` 本地进程适配器，
在 [每周发射综述](ROUNDUP.md) 中按段调用；正常生成新闻、逐次发射 MOV 或片头不会生成语音。
无需安装 TTS、PyTorch 或模型权重。未传入适配器时明确报错，不产生文件。

## 接口约定

- `SpeechRequest(text, model, voice, language)`：明确指定配音文本；默认模型为
  `Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice`，默认语言为 `zh_CN`，空音色由适配器选择。
  模型与音色接受其他服务自己的标识，不限定厂商。
- `SpeechProvider.synthesize(request, output_path, cancel_check)`：适配器将完整的
  **PCM WAV** 写入指定临时路径。出错必须抛出异常，耗时操作应检查取消回调。
  模型加载、服务地址、鉴权、超时以及语言/音色映射由具体适配器负责。
  例如 Qwen 适配器将 `zh_CN` / `en_US` 映射为其支持的 `Chinese` / `English`。
- `generate_speech(request, output_dir, provider=..., cancel_check=...)`：验证输入和
  WAV，成功后用现有编号命名与临时替换机制发布文件；失败或取消清理本次文件，
  保留先前配音。返回 `SpeechResult(path, duration_seconds, sample_rate)`，时长来自
  实际 WAV 帧数，不使用文本阅读速度估算。

## 未来接入方式

以下为未来调用方的形状，`provider` 需由后续实现的本地或 HTTP 适配器提供：

```python
from pathlib import Path
from ai.speech import SpeechRequest, generate_speech

request = SpeechRequest(
    text="本周航天新闻。",  # 调用方明确选择或编辑的配音稿
    voice="Vivian",
    language="zh_CN",
)
result = generate_speech(request, Path("output/voiceovers"), provider=provider)
# 调用方用当前素材稳定的 item['id'] 关联 result.path 与 result.duration_seconds。
```

后续视频素材界面可在现有后台任务中调用该入口，将 WAV 作为独立音轨交给剪辑软件，
并用实际配音时长决定素材时长。当前不改动快照/执行清单协议，不自动截断配音，
也不把 WAV 混入 MOV/MP4。适配器需要自行确保请求可超时、可取消；外层只能在
调用前后检查取消，无法强行中止一个阻塞且不响应回调的适配器。

## Qwen 0.6B 对接依据

官方提供 [0.6B CustomVoice 模型](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice)，
固定音色的本地适配器可对接 `Qwen3TTSModel.generate_custom_voice`，把 `voice` 映射为
`speaker`，并将返回波形保存为 PCM WAV。其他模型或 HTTP 服务实现同一方法即可，
无需改动调用方。参考 [Qwen3-TTS 官方仓库](https://github.com/QwenLM/Qwen3-TTS)。
本地进程配置与当前验证边界见 [每周发射综述](ROUNDUP.md)；当前环境未验证真实模型音质。

## 离线验证

```powershell
python -m unittest discover -s tests -p "test_speech.py" -v
```

测试适配器只写入合成 PCM 样本，验证参数传递、其他模型标识、实际时长、
未配置行为、输入/音频校验、重复生成不覆盖及失败/取消清理，不代表实际 TTS 效果。
