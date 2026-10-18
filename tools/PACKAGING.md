# Windows 精简打包

程序版本仍为 3.0.2，已发布的原版文件保持不变。精简构建调整打包范围，不裁掉应用功能。

Windows 构建使用 Python 3.12+，安装 requirements.txt、tools/requirements-video-download.txt 和 PyInstaller。3.12 起的工具查找行为用于防止工作目录中的同名程序覆盖包内工具；spec 会拒绝更旧的构建解释器。此要求仅针对 Windows 打包。
将官方 yt-dlp 2026.08.19 的 yt-dlp.exe 与 LICENSE 放到 build/video-backend/，将 LICENSE 命名为 yt-dlp-LICENSE.txt。可用 SPACEWEEKLY_VIDEO_BACKEND 指定 EXE 的其他路径，许可文件需同目录。

下载地址：https://github.com/yt-dlp/yt-dlp/releases/tag/2026.08.19

官方 EXE SHA-256：66674953fe251b89f4d08c5f0e35e0728679bd67ab3d7d05c0562af101dd3e7a

将已验证的 Windows x64 静态 FFmpeg 8.0.1 essentials 构建中的 `bin/ffmpeg.exe`、`bin/ffprobe.exe`、`LICENSE`、`README.txt` 放到 `build/ffmpeg/`（两个 EXE 直接放在此目录，无 bin 层）。仅打包这四个文件，缺失时构建立即报错，不使用构建机器 PATH 中不确定的工具。

本机来源为 [Gyan FFmpeg builds](https://www.gyan.dev/ffmpeg/builds/)，版本 `8.0.1-essentials_build-www.gyan.dev`。原始 README 和 GPL v3 LICENSE 随包保留，README 内含构建配置与对应 [FFmpeg 源码版本](https://github.com/FFmpeg/FFmpeg/commit/894da5ca7d)。此构建启用了 GPL/libx264，不应当按 LGPL 构建描述。再次公开分发时须同时落实对应源码与其依赖的许可义务。

| 工具 | SHA-256 |
| --- | --- |
| ffmpeg.exe | 5af82a0d4fe2b9eae211b967332ea97edfc51c6b328ca35b827e73eac560dc0d |
| ffprobe.exe | 192a1d6899059765ac8c39764fc3148d4e6049955956dc2029f81f4bd6a8972d |

从项目根目录执行 python -m PyInstaller --noconfirm --clean SpaceWeekly.spec。
使用 python tests/check_packaged.py 指定生成的 SpaceWeekly.exe 验收。

打包保留 QWidget、QtNetwork、SVG、Pillow 图像格式、Word 导出和证书校验；排除本应用未使用的 NumPy/OpenBLAS、pyOpenSSL/cryptography、Qt QML/PDF/虚拟键盘与软件 OpenGL。账号会话仍用 Windows DPAPI 加密。若未来加入 OpenGL/QML/PDF 界面，需要恢复对应组件。

yt-dlp 与许可内置在 video-backend 中，无需单独分发 yt-dlp.exe。程序同目录的 yt-dlp.exe 仍可覆盖内置版本，PATH 后备仍保留。FFmpeg/ffprobe 与许可放入 `_internal/ffmpeg/`，运行时仅为当前进程和子进程优先设置该路径；源码运行仍沿用系统 PATH。无需修改调用模块，无需安装 Python、yt-dlp 或 FFmpeg。

默认生成 dist/SpaceWeekly/SpaceWeekly.exe 和 _internal/，解压后顶层只保留两项。必须完整移动此文件夹，_internal 不可删除。这样无需每次启动解压依赖。

若只关心单个文件，可设置 SPACEWEEKLY_ONEFILE=1 再打包；该模式仍需每次启动解压，实测没有改善启动速度，因此不推荐用于本次优化。

## 早期不含 FFmpeg 的精简包验证（2026-10-12）

| 项目 | 已发布原包 | 快速精简包 |
| --- | ---: | ---: |
| ZIP 下载大小 | 91.5 MiB | 60.8 MiB |
| 解压后实际磁盘文件大小 | 92.4 MiB | 114.9 MiB |
| 同批三次启动中位数 | 11.22 秒 | 3.62 秒 |

快速包顶层为两项，_internal 内有依赖；整个包共 136 个实际文件，并非总共只有两个文件。ZIP 下载更小；解压后的静态磁盘占用更大，因为依赖不再藏在 EXE 中每次解压到临时目录。这是启动速度与静态占用的取舍。单 EXE 构建约 61 MiB，但实测启动仍有明显波动。

启动计时从创建进程到程序自己的可见主窗口能处理消息，使用独立的临时工作区。没有清空操作系统缓存，不是严格冷启动测量；此前新构建首次启动也出现过约 13 秒，不保证所有电脑首次启动低于 5 秒。首轮测试中的系统查询方法随后收紧为仅定位所启动进程树，表内为同一方法、同一批次的对比。

267 项 Python 测试通过；正式 EXE 验证 Qt 窗口、原素材/片头/写作规则、默认最高画质、二维码、内置 yt-dlp 启动、Word 保存/读取、TLS 证书配置、SVG、北京时间及纽约冬夏令时、正常退出。ZIP CRC 与全部解压文件 SHA-256 校验一致。本轮没有重新传输最高画质影片，也没有验证真实账号扫码或 Premiere 宿主。

完整解压 SpaceWeekly_v3.0.2_Windows_x64_Fast.zip，再运行 SpaceWeekly.exe；勿只复制 EXE，勿删除 _internal。无需安装 Python、yt-dlp 或二维码依赖。FFmpeg/ffprobe 仍需在 PATH。已有 Workspace、JSON、manifest 和下载素材继续沿用。

原 GitHub v3.0.2 Release 与标签没有替换；此处为本地精简构建。

## 内置 FFmpeg 构建（2026-10-16）

输出目录为 `dist/v3.0.2-ffmpeg/SpaceWeekly/`，仍保持顶层 EXE 和 `_internal/` 两项。两个静态工具和许可合计增加约 189 MiB 解压体积；没有引入 ffplay、扩展、浏览器或额外 Python 依赖。它们不在正常启动时执行或解压。

新增运行时路径测试覆盖内置优先、无 PATH、工具不全时保留外部后备。正式 EXE 验收只保留 Windows 系统目录在 PATH，实际执行内置 FFmpeg/ffprobe，生成 H.264/AAC 并封装 MP4、生成透明 PNG MOV 和 ProRes 4444 MOV，并验证媒体流。该验收同时覆盖原窗口、yt-dlp、二维码、Word、TLS、SVG、时区和正常退出。下载后端继续显式传递已找到的 FFmpeg 所在目录。

270 项 Python 测试通过；正式 EXE 和 ZIP 解压后（含空格路径）的 EXE 均通过上述验收，ZIP CRC 与全部解压文件 SHA-256 校验一致。此轮没有重新执行 Bilibili 网络下载或真实账号扫码。

| 项目 | 内置 FFmpeg 包 |
| --- | ---: |
| ZIP 下载大小 | 128.9 MiB（135,168,242 字节） |
| 解压文件总大小 | 304.2 MiB（318,926,026 字节） |
| 文件数量 | 140（顶层两项） |
| 三次启动 | 4.064 / 3.023 / 2.674 秒 |
| 启动中位数 | 3.023 秒 |

启动测量沿用进程树内可见主窗口响应检查、独立已配置 Workspace；未清空系统缓存，不保证其他机器或冷启动同样耗时。计时与媒体验收使用临时工作区，不更改已有素材。

本地 ZIP 为 `dist/v3.0.2-ffmpeg/SpaceWeekly_v3.0.2_Windows_x64_FFmpeg.zip`，SHA-256 为 `1724df38b014fab1f9b7e89fa90d5a211b5c02068d721fbb284cd990c2a74351`。完整解压后运行 SpaceWeekly.exe，无需另外安装 FFmpeg/ffprobe。原发布包、快速包及用户 Workspace 均保留，尚未替换 GitHub Release。

## 缺少 BankGothic 的片头修复（2026-10-17）

旧代码固定加载开发电脑的 `C:/Windows/Fonts/bankgothic-bold.ttf`，普通电脑缺少此字体时，片头在 FFmpeg 编码之前的文字准备阶段报 `cannot open resource`，新闻条和发射卡编号也受影响。已在新进程中同时屏蔽该字体的存在检查和 Pillow 加载，复现同样错误。

修复后从当前 Windows 目录读取字体；BankGothic 可用时保留原样，缺少时依次回退到 Arial Bold、Segoe UI Bold 和所选中文字体。中文字体也提供微软雅黑粗体、微软雅黑、黑体和宋体后备。字体无法加载时显示具体路径，不在应用导入阶段阻断启动。不下载或重新分发商业字体。

273 项 Python 测试通过，包括新进程中缺少 BankGothic 时的片头、新闻条和发射卡渲染。实际 Qt → QThread → 包内 FFmpeg 测试仅保留包内媒体工具和 Windows 系统目录在 PATH，并模拟该字体缺失：自动选中 Arial Bold，PNG Logo 预览通过，生成 H.264、1920×1080、60 fps、420 帧、7 秒的 MP4，生成期间界面定时器触发 537 次。记录在 `build/font-fix-verification/result.json`，未修改用户工作区。

后续本地修复包为 `dist/v3.0.2-font-fix/SpaceWeekly_v3.0.2_Windows_x64_FontFix.zip`，继续内置 FFmpeg/ffprobe；完整解压后运行 EXE，保留 `_internal/`。原包保留，GitHub Release 未替换；尚未在用户截图对应电脑上复测。

正式 EXE 与 ZIP 解压后含空格路径的 EXE 均通过验收，验收强制使用系统后备数字字体和 PNG Logo，并清除外部工具 PATH；FFmpeg/ffprobe、下载后端、原对话框、Word、TLS 和时区检查通过。ZIP CRC 与全部解压文件 SHA-256 一致。包大小 135,168,545 字节（128.9 MiB），140 个文件、顶层两项；SHA-256 为 `1a65e3ec9fc4f8bec7290954e43794bfbf82c3daa2d22fc765a1b307b87fcdef`。


## FontFix2：内置中文资源与实际加载校验（2026-10-17）

用户确认 FontFix 包在预览和生成时仍报错。再次检查发现第一版仍依赖系统中文字体，且只检查文件存在。将 Windows 字体目录指向空目录后，已复现旧 FontFix 的片头准备失败；目标电脑的新错误原文/日志尚未获得，不能认定这就是其唯一触发点。

- 候选字体逐一实际加载，损坏或不可读时跳过；最终使用随包的 Noto Sans CJK SC Bold。保持系统可用字体的原有优先级。
- 字体通过缓存的不可变字节和 BytesIO 加载；预览和后台线程不共享可变字体对象，也不为每个字体实例保留字体文件句柄。
- 字体来自 Noto 官方仓库，未修改、未裁剪；OFL 许可与来源和 SHA-256 一起放入 assets/fonts/。新增字体本体 17,002,248 字节，不增加 Python 运行依赖。
- 片头预览、准备和编码失败复用现有日志设施，记录具体调用栈及所选字体；用户 Workspace 的 errors/ 保存错误记录，logs/ 保存常规日志。
- 276 项 Python 测试通过，包括系统字体全缺失时的中文片头/新闻/发射卡、损坏首选字体、中文字体路径和同时保留 800 个内存字体对象。

[Pillow 官方文档](https://pillow.readthedocs.io/en/stable/reference/ImageFont.html) 提供内存加载以规避 Windows 文件句柄限制的建议；本机直接路径加载 800 个小字体对象也成功，因此未将句柄耗尽认定为目标电脑已证实的原因。

新版 EXE 验收会将 WINDIR 指向空目录并移除外部媒体工具 PATH，明确断言正文和数字均选中内置字体，再通过实际生成完整 7 秒片头的专用标记确认代码已执行。ZIP 解压路径包含中文及空格。新包目录为 dist/v3.0.2-font-fix2/，原包和原 Workspace 保留，GitHub Release 未替换。


FontFix2 最终包验收结果：正式 EXE 和中文、空格解压路径中的 EXE 均通过严格的内置字体选择与完整 420 帧/7 秒片头编码标记检查。ZIP CRC 和全部解压文件 SHA-256 一致。

ZIP 149,344,769 字节（142.4 MiB），解压 320.4 MiB，143 个文件，顶层 EXE 和 _internal 两项。SHA-256：`84844cbeb5ee1498a8e8e220e9dbe980a7cdbaea7e4038cea4d1d45d8972c3cb`。

本轮未重新测量启动用时，也未重新联网下载 Bilibili 视频。目标电脑的实际触发点仍待新错误原文/日志确认；不要把本机通过等同于目标电脑已解决。


## FFmpegFix：工具查找复查（2026-10-18）

复现并修复两处运行时查找问题：Windows 上仅把包内目录放在 PATH 首位仍可能先选中当前工作目录的同名程序；原逻辑要求 ffmpeg 和 ffprobe 同时存在，缺少一个会使另一个也无法从包内启用。

运行时 hook 仅在当前进程设置 NoDefaultCurrentDirectoryInExePath=1，禁止隐式从工作目录寻找程序；任一内置媒体工具存在就将其目录放入 PATH 首位。缺失工具仍可使用明确 PATH 中的后备，不修改 Windows 全局环境。下载后端继续显式传入 --ffmpeg-location。中英文错误提示说明应完整解压并保留 _internal/ffmpeg/。

两个工具均为 AMD64，PE 导入仅含 Windows 系统库，没有额外的 FFmpeg 共享 DLL 或 VC++ 运行库依赖。该检查与实际执行一起验证，未据此承诺所有 Windows 版本兼容。

278 项 Python 测试通过。新增测试覆盖工作目录中的同名工具不能覆盖内置工具、仅缺少一个文件以及空目录后备。正式 EXE 验收移除外部工具 PATH、屏蔽系统字体，并在工作目录放入无效的 ffmpeg.exe/ffprobe.exe，再生成完整 420 帧片头及透明卡片。

新增离线实际下载/合并检查：

```powershell
python tests/check_media_backend.py dist/v3.0.2-ffmpeg-fix/SpaceWeekly
```

测试以包内 FFmpeg 自行生成视频和音频，通过仅绑定 127.0.0.1 的临时 HTTP 服务由包内 yt-dlp 实际下载，再调用包内 FFmpeg 合并。已取得 H.264 + AAC、1 秒 MP4，ffprobe 检查及完整解码通过；不访问外部平台、不使用账号会话。日志见 build/v3.0.2-ffmpeg-fix-media-backend.log。

新本地包为 dist/v3.0.2-ffmpeg-fix/SpaceWeekly_v3.0.2_Windows_x64_FFmpegFix.zip，同时保留 FontFix2 的内置中文字体和日志修复。无需另装 FFmpeg，必须完整解压并保留 _internal/。原本地包、Workspace 和 GitHub Release 均保留。本轮没有在用户报错的电脑上复测，也没有重新验证 Bilibili 联网下载或真实账号扫码。

FFmpegFix 最终包验收结果：正式 EXE 和中文、空格路径下的解压 EXE 均通过上述严格验收。ZIP CRC 与全部解压文件 SHA-256 一致。ZIP 149,344,696 字节（142.4 MiB），解压 320.4 MiB，共 143 个文件，顶层 EXE 和 _internal 两项。SHA-256：`679a29a90553d51feb0d74206232da24efc0ffef3467d23d3f9033541a663256`。


## VideoUX Preview：素材、搜索和片头交互（2026-10-18）

本地版本仍为 3.0.2，最终包命名 SpaceWeekly_v3.0.2_Windows_x64_VideoUX_Preview.zip。继续保留内置 FFmpeg/ffprobe、yt-dlp、FontFix2 字体和安全工具查找；没有增加运行依赖。素材窗口使用可保存的发射卡背景不透明度和更大的默认新闻标题，下载辅助动作收进「更多」，优先 UP 名单保存并与全站结果合并，泛化载荷任务使用火箭名称变体，AI 标题规则接入实际生成，片头新增播放、暂停和拖动预览。

302 项 Python 测试通过（build/video-ux-tests.log）。实际 35% 不透明度的完整 12 秒 PNG MOV / ProRes 4444 通过编码和 Alpha 检查；中文深色、英文浅色的原生 Windows 截图均正常。片头在原生窗口实际播放并拖到 2.5 秒，后台 GIF 准备、缓存、取消和关闭等待有回归测试。正式 EXE 验收新增播放/拖动标记，并实际生成两种半透明发射卡及完整 7 秒片头；不依赖外部 FFmpeg 或系统字体。

匿名公开检索「朱雀二号E」在 2026-10-14–10-15 实际返回 9 条记录，包括当天一箭 10 星报道，见 build/video-ux-bilibili-search.json；未下载这些视频。其余任务保留任务名优先策略，零结果才试名称后备，网络限制直接显示错误。没有用用户原四项 JSON 重跑完整下载，也没有调用真实 AI 模型检验新标题效果或在 Premiere 中复验。GitHub Release 未替换。

离屏测试无法读取 Windows 字体时可能呈现方框；界面视觉验收改用原生 Windows 平台和正常系统字体。屏蔽系统字体只用于确认导出器确实使用内置 Noto 字体，不用该环境的界面截图判断字体效果。

VideoUX Preview 最终包验收：正式 EXE 和中文/空格路径下的解压 EXE 均通过新增片头播放/拖动、半透明发射卡和完整片头检查。ZIP CRC 与全部解压文件 SHA-256 一致。ZIP 149,357,640 字节（142.4 MiB），解压 320.4 MiB，共 143 个文件，顶层仍为 SpaceWeekly.exe 与 _internal。SHA-256：`a3998aae8aefae466822742035626b4981bec8768cc57b6b889f16819b696765`。
