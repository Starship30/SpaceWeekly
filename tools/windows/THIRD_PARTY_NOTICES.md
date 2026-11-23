# 第三方组件说明

安装包使用 PyInstaller、Python、PySide6/Qt、Pillow、Velopack 及其 MSI 安装组件，以及项目锁文件记录的依赖。组件各自适用其许可证；本文件不替代原始许可。

FFmpeg 包内构建包含 GPL/libx264，FFmpeg 的 LICENSE、README 与构建来源随包放在 _internal/ffmpeg。发布者须落实对应源码及其依赖的许可义务。

yt-dlp 的许可位于 _internal/video-backend。随包 Noto 字体的许可位于 _internal/assets/fonts。

Qwen 模型与独立 Python/语音环境按需下载，不包含在桌面安装包内。各模型和下载组件适用其发行方许可及服务条件。

用户协议单独位于 USER_AGREEMENT.zh-CN.md，标记为待审核草案。

安装向导使用 WiX DTF 4.0.6（Microsoft Reciprocal License，见 WIX-LICENSE.txt）。其未修改组件来自官方 NuGet，构建脚本校验固定 SHA-256。对应源代码：https://github.com/wixtoolset/wix/tree/73c897383236ddbbbc6ba257634013c1269ceec2/src/dtf 。
