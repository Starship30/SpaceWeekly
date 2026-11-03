# 语音环境自动配置

入口：**配音与画面 → 配置语音**，或 **设置 → 语音模型与显示**。
在“自动配置”选择运行方案、轻量或标准配音、明确的模型来源及保存位置，点击“检测设备与空间”，
核对下载内容后“开始自动配置”。配置可在后台运行，切换四个制作页面不会重新启动配置。
“使用已有环境”保留原解释器与本地模型输入，只检查、不安装依赖；“检查与修复”支持联检、修复、试听和诊断导出。

没有通过模型推理检查时不会标记可用。编辑稿件和无音频预览始终可用。
下载失败可重试或明确切换 ModelScope / Hugging Face，已有完整文件和可续传文件保留。
取消会结束安装子进程树或模型工作进程，不清除模型缓存；重开后重新检查再继续。
安装依赖阶段只显示阶段和实际日志，模型下载显示真实字节进度。

## 文件位置与安全边界

- 默认语音根目录：`%LOCALAPPDATA%/SpaceWeekly/voice`；可以选择其他磁盘的专用目录。
- 上次位置指针：`%LOCALAPPDATA%/SpaceWeekly/voice-location.json`，与工作区分开。
- `python/`：应用专用 Python；`environments/`：配音和对齐的独立环境。
- `models/<模型>/<快照>/`：完整本地模型；`downloads/`、`uv-cache/`：可复用下载和安装缓存。
- `deployment.json`：方案、依赖哈希、快照、设备、分能力检查结果、历史有效环境及时间。
- `deployment.log`、`checks/`：脱敏日志、实际测试音频和对齐结果。支持导出 JSON 诊断。

程序目录、工作区、非空的外来目录不能被作为新语音目录。管理文件带所有权标记，操作使用跨进程锁；
配音工作进程使用环境时不能同时安装或移除依赖。清理只接受管理根目录内明确的缓存或单个已标记环境，
不删除用户自行提供的环境、模型或工作区。清缓存保留已安装模型和环境；移除环境后标记需要检查。
重建先准备候选环境，验证通过后才更新制作设置，旧有效配置及工作区语音设置备份保留。

## 部署清单 v1

清单：`tools/voice_deployment/profiles.json`，版本 `voice-win64-20261102-v1`。
仅支持 Windows x64 自动安装；检测到显卡名称不等于 CUDA 已可用。

| 项目 | 固定版本或方案 |
| --- | --- |
| 配置工具 | Astral uv 0.9.21，官方 Windows x64 ZIP 与 EXE SHA256 校验 |
| 独立 Python | 3.12.12，uv managed Python；不写系统 PATH / 注册表 |
| CPU | PyTorch / torchaudio 2.9.1+cpu，实际推理成功后才标记本机可用 |
| NVIDIA | PyTorch / torchaudio 2.9.1+cu128，实际 GPU 运算和模型推理联检 |
| 配音环境 | qwen-tts 0.1.1，Transformers 4.57.3 |
| 对齐环境 | qwen-asr 0.0.6，Transformers 4.57.6 |
| 共同约束 | accelerate 1.12.0、NumPy 2.2.6、soundfile 0.13.1 |

两个 Qwen 包固定了不同 Transformers 版本，不能可靠放进同一环境，因而由同一入口管理两个环境。
只使用预编译包与基础 SDPA 推理，不要求 FlashAttention、vLLM、驱动、完整 CUDA Toolkit 或编译工具链。
CPU 可能较慢，不给出未经实测的耗时承诺。标准 1.7B 和其他设备是否实测，见本轮验收报告，不能根据清单存在就称为已验证。

| 模型 | Hugging Face 固定快照 | 完整运行文件大小 |
| --- | --- | ---: |
| Qwen3-TTS-12Hz-0.6B-CustomVoice | `85e237c12c027371202489a0ec509ded67b5e4b5` | 2,498,383,610 字节 |
| Qwen3-TTS-12Hz-1.7B-CustomVoice | `0c0e3051f131929182e2c023b9537f8b1c68adfe` | 4,520,159,586 字节 |
| Qwen3-ForcedAligner-0.6B | `c7cbfc2048c462b0d63a45797104fc9db3ad62b7` | 1,840,013,484 字节 |

每个文件保存大小、SHA256 和 ModelScope 对应的文件提交版本。两个源的运行文件已比对为相同内容，
共享按 SHA256 寻址的续传缓存；切换源不会把不匹配版本拼成完整模型。
只下载强制对齐权重，不额外下载 ASR 模型。空间显示区分精确模型字节与环境占用估算。
所有传递依赖固定版本与哈希，使用 `uv pip sync --require-hashes --only-binary :all:` 安装。
锁文件旁的 `.resolve.in` 用于解析 PyPI 依赖；`.in` 记录官方 CPU / CUDA 二进制链接。
更新方案时需重新解析 Windows cp312 依赖、固定官方 Torch 链接及哈希、更新锁文件哈希，并在真实机器联检，
不能直接将锁中的版本改成 latest。

## 真实联检与制作接入

目标解释器检查全部锁定包版本，导入对应 Qwen 包，实际执行 CPU 或 CUDA 张量运算。
随后通过原 QwenTTSProvider 工作进程生成短中文 WAV，检查样本数、可读性、时长及非静音。
卸载 TTS，再由原 ForcedAligner 工作进程对齐同一 WAV 和最终文本，检查字符覆盖、有序、非零和音频边界。
两项能力分别记录，失败不删除已生成音频，也不伪造成功。
通过后自动保存解释器与模型位置，音色从实际模型配置及工作进程读取，0.6B 不提供风格指令。
原同步缓存、句子与任务 ID、人工校准、稿件保护及 FFmpeg 导出继续沿用。

软件更新只会检查方案/锁/快照兼容性，不会在打开软件时下载或替换模型。
更新依赖、模型或工作进程协议时必须递增部署方案版本，旧版本会提示检查兼容性。
打包仅附清单、锁文件和小型工作脚本，深度学习环境与权重放在用户可写目录，不使用 PyInstaller 解释器执行 pip。
本轮未重新打包 EXE。

## 本机实测（2026-11-02）

通过真实软件入口，已完成 NVIDIA 与 CPU 的轻量方案部署、短中文合成和 ForcedAligner 联检。
保存于 `D:/SpaceWeeklyVoice/语音环境 验证`，中文和空格路径均实际运行。
NVIDIA 为 RTX 5060 Laptop 8GB / 驱动 592.01；CPU 为 AMD Ryzen 9 8940HX，内存约16GB。
GPU 方案另用原有两项中国发射稿件生成42.88秒旁白、四单元真实自动对齐及1920×1080带音轨视频。
CPU完成短音频与对齐联检，未执行同一长稿的CPU整期导出；1.7B标准模型尚未下载实测。

本机 ModelScope 连接超时后，通过界面明确切换 Hugging Face 完成下载。
实际取消后保留493MB部分文件，重开继续完成，已完成文件的修改时间保持不变；
另检查官方源返回 HTTP 206，指定偏移的16字节与已校验本地文件一致。

实测修复了隔离Python忽略环境编码导致的中文管道路径错误、原生库的UTF-8文件名处理、
Windows子进程树退出及临时日志释放时序。历史日志在重开后仍能展开，诊断包括脱敏日志与模块检查记录。
试听入口可以播放真实检查WAV；程序检查不能代替用户对读音、音质及自动对齐边界的听看判断。
详见 [真实验收报告](../build/voice-setup-20261102/REPORT.md)。

官方依据：[uv managed Python](https://docs.astral.sh/uv/guides/install-python/)、
[uv 锁依赖](https://docs.astral.sh/uv/pip/compile/)、
[PyTorch 2.9.1 安装方案](https://pytorch.org/get-started/previous-versions/)、
[Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS)、
[Qwen3-ForcedAligner](https://github.com/QwenLM/Qwen3-ASR#forcedaligner-usage)。
