# 视频搜索、下载与哔哩哔哩扫码登录

这是已有「视频素材」窗口的子对话框，沿用中英文和浅色/深色主题。下载独立于卡片生成；保留原始快照和 Premiere 清单协议。

## 使用

1. 从源码启动 SpaceWeekly，打开「视频素材 → 视频下载」。不需要先加载 JSON；加载后「JSON 任务」读取发射任务名称（导出 JSON 的 summary，title 为火箭名称），选择后填入可编辑关键词。具体任务名优先；「10颗卫星」等泛化载荷数量会改用火箭名，零结果时尝试中文/数字及型号名称变体（最多四个检索词）。手动改写的关键词按原样搜索，不自动替换。任务列表默认排除新闻；勾选「包含新闻条」可加入新闻关键词/标题，原素材列表和 JSON 不变。
2. 可以优先选择最多 10 个 UP 主：填写数字 UID 或 https://space.bilibili.com/<UID>，用逗号/空格分隔；留空不设优先。先搜索并选择结果，再通过「更多 → 优先所选 UP 主」加入实际 UID。名单和排序按 Workspace 保存，关闭窗口也会保存有效输入。优先 UP 结果与全站结果合并去重，优先组排前，组内使用所选排序；UP 接口受限时仍可使用其他作者的公开结果并显示真实限制。结果和任务默认时间正序，支持倒序及平台相关度；未知时间放最后。搜索 Bilibili，列表显示实际返回的标题、封面、作者、时长、平台、日期。单击结果填入链接，双击解析。也可直接粘贴 `/video/BV…`、`/video/av…` 或 `b23.tv` 短链接；保留 `p=` 分 P。当前只处理一个视频/分 P，不下载整个合集或直播。
3. 可选点击「哔哩哔哩扫码登录」，使用哔哩哔哩 App 扫码并确认。二维码约三分钟有效，显示等待扫码、待确认、过期/失败，可刷新或关闭取消。登录后会话加密保存，关闭窗口、重启应用后自动恢复并在后台验证；退出登录删除。平台会话过期仍需重新扫码。登录后重新解析视频，获取此会话实际可见的格式。
4. 解析后默认选择最高可下载 MP4 画质，也可手动改选。优先最高分辨率，同分辨率优先 H.264；缺少 FFmpeg 时选无需合并的最高可用格式，若所有格式均需合并则明确提示。DASH 视频流会与可用 M4A 音频通过 FFmpeg 合并。为 Premiere 兼容性可优先选择 `avc`（H.264）；其他 MP4 编码并不保证所有 Premiere 版本都支持。没有可用音轨时可能得到无声视频。
5. 确认自己有使用权且平台允许保存，再下载。进度显示当前音/视频流的百分比、速度和 ETA（后端提供时），切换流可能重置百分比；合并和检查显示不确定进度。只有文件验证及元数据写入成功后才显示完成。
6. 完成后通过「更多 → 打开视频文件 / 打开下载目录」在 Premiere 中导入 MP4。下载到当前 Workspace 的 `video-downloads/`，与卡片输出目录分开。修改卡片输出路径不会移动下载目录。

时间排序仅作用于本次检索结果，并非拉取全站或 UP 主全部历史视频：不按日期限制时全站最多 20 条、指定 UP 主最多取各自第一页的 20 条；日期检索全站最多 5 页（100 条）、每位 UP 主最多扫描 5 页并保留 20 条匹配。UP 主空间接口受限时，尝试公开关键词搜索前 3 页并按真实 UID 过滤，明确提示可能遗漏；均失败或无法确认匹配时显示真实错误，不报告虚假成功/完整空结果。

程序在解析和下载前查询平台视频信息，要求 `rights.download == 1`，拒绝付费、付费预览、DRM、不可访问及权限无法确认的内容。扫码登录不保证更高画质或下载权限，也不绕过上述限制；下载权不等于转载或商业使用权。

## 按任务时间自动寻找与 Demo

默认启用「按任务日期寻找」。使用 JSON 的原始 published 时间，换算为北京时间自然日：例如 2026-10-09 发射，检索范围为 10-09 00:00 到 10-10 23:59:59，包含当天和次日。跨月、跨年按自然日计算；列表日期与筛选使用相同的北京时间。搜索接口发送 pubtime_begin_s / pubtime_end_s，并在本地复核实际发布时间，排除范围外及不能确认日期的视频。参数及自然日处理参考 [SearxNG 的 Bilibili 引擎源码](https://github.com/searxng/searxng/blob/master/searx/engines/bilibili.py)，已用真实平台响应验证。

缺少/无效 published 不猜日期，提示补充带原始日期的 JSON；关闭时间匹配可以手动不限日期搜索。没有选择 JSON 任务时，普通关键词搜索仍不限制日期。时间限制按视频发布时间，不能证明画面属于该次发射；晚于次日发布的素材也会被排除。

「全部发射任务下载 Demo」遍历当前 JSON 中的全部发射任务，逐项寻找并下载一条视频：

1. 导入 JSON，保留「按任务日期寻找」，可设置优先 UP 主并选择排序（默认时间正序）。Demo 始终排除新闻，即使下拉列表勾选「包含新闻条」也不会下载新闻。
2. 勾选素材使用权和平台保存权限，点击「更多 → 全部发射任务下载 Demo」。不需要逐项选中，编辑框中的手动关键词不会替代其他任务名称。
3. 每项使用自己的任务名称；泛化载荷描述用火箭名，零结果时按候选名称后备。日期始终是各自发射当天及次日，UP 名单只表示优先。最多尝试前三条真实候选；无日期、无匹配、网络或权限失败记录具体原因并继续下一项。
4. 每项下载一条完整视频，默认最高可下载 MP4 画质，同分辨率优先 H.264；复用 FFmpeg 合并、文件验证和重复缓存。总任务进度与当前文件进度分开显示，逐项记录成功路径或失败原因。
5. 本批全部视频放在同一个按运行当天北京时间命名的文件夹，例如 video-downloads/2026-10-11/；同日再运行用 2026-10-11 (2)/。文件按原视频标题命名，同名加 (2) 等序号，非法字符替换、过长标题截断。
6. 取消会停止当前任务和后续任务，已完成视频保留。打开目录进入本批文件夹，可一次导入 Premiere。单项无匹配不会改为下载其他日期的视频。

全部任务都失败时明确显示成功 0 项，不显示下载成功。重复素材会校验已有文件并复制到当前批次目录，保留先前批次；同一批已存在的相同链接和格式直接复用。没有引入新依赖。平台零结果只有明确 numResults=0、numPages=0 且无验证票据才按空结果处理；挑战、网络失败和缺失数据仍是错误。

## 依赖与 Windows EXE

主应用依赖仍使用项目 `requirements.txt`。新依赖是可选的：

```powershell
python -m pip install -r tools/requirements-video-download.txt
python app.py
```

- `yt-dlp>=2026.8.19,<2027`：解析及实际下载。使用启动 SpaceWeekly 的同一 Python 环境。也支持 PATH 中的 `yt-dlp`。
- `qrcode>=8.2,<9`：仅生成登录二维码，复用项目已有 Pillow。未安装时搜索/下载仍可用，扫码入口显示明确安装提示。
- 登录持久化使用 Windows 自带 DPAPI，无额外 pip 依赖；其他系统暂只支持内存登录，并明确提示不能保存。
- FFmpeg：音视频合并格式必需；ffprobe 用于检查本地视频流。本地 FFmpeg 构建将两者放入 `_internal/ffmpeg/`，下载、卡片和片头共用，免安装、免配置 PATH。源码用户及原已发布包仍需将两者放入 PATH。

旧 Windows EXE **需要重新打包**。在构建环境安装上述可选依赖，并按 [打包说明](PACKAGING.md) 准备 yt-dlp 和 FFmpeg 文件，再运行 `python -m PyInstaller --clean SpaceWeekly.spec`。冻结程序不启动自身去执行 `python -m yt_dlp`；先查找程序同目录的 yt-dlp.exe 覆盖文件，再使用内置 video-backend/yt-dlp.exe，最后查找 PATH。原 GitHub 3.0.2 ZIP 的后端在程序同目录，且需要外部 FFmpeg；本地后续构建将后端与许可放入 _internal。请从 [yt-dlp 官方发布页](https://github.com/yt-dlp/yt-dlp/releases) 获取对应 Windows 可执行文件。

本地 FFmpeg 构建是上述历史发布包的后续构建，打包方法已加入 FFmpeg/ffprobe，详见 [打包说明](PACKAGING.md)；原 GitHub Release 未替换。程序仅在自身进程内将内置目录加入 PATH 并禁止隐式查找工作目录中的同名程序，子进程继承，不修改 Windows 全局环境变量。任一内置工具存在即可启用对应路径，缺失工具仍可从明确 PATH 后备。源码运行仍使用系统工具。

缺少 yt-dlp 不影响 SpaceWeekly 启动或卡片/片头功能，仅在解析/下载时提示。缺少 FFmpeg 时不能下载需要合并的格式；纯音视频一体 MP4 仍可处理。平台接口变化时需更新和回归验证后端版本。

## 文件与取消

```text
<Workspace>/video-downloads/
  .bilibili-session.bin       # Windows 当前用户加密会话
  .search-preferences.json    # 公开的 UP UID 与排序偏好
  2026-10-11/                 # 本批视频的共同目录
    原视频标题.mp4
    同名标题.mp4
    同名标题 (2).mp4
  2026-10-11 (2)/             # 同日另一批
    原视频标题.mp4
  job-<随机标识>/             # 每次下载/缓存复制的独立记录及临时文件
    metadata.json
```

元数据包含标题、原始规范链接、平台、视频 ID、文件格式、格式 ID、保存路径、开始/完成时间与状态。先在独立 job 目录下载和验证，再以不覆盖的原子移动发布到日期目录。失败/取消只清理当前 job 的媒体和本次新生成文件；已完成文件保留。记录不含登录凭据或签名媒体地址。

普通单链接下载也保存到日期目录；重复的相同链接和格式可直接返回已验证文件。批量 Demo 每次创建独立日期目录，并将已验证缓存复制到本批目录；兼容旧目录的 media.mp4 和元数据，原文件保留。同名序号由实际文件冲突决定，不覆盖手动放入的文件。文件名处理 Windows 保留名、非法字符和长度；输出与缓存路径必须留在 Workspace 中。

搜索、解析、二维码生成/轮询和整批 Demo 在 QThread 中执行；封面用 QtNetwork 异步读取。Demo 逐项执行，取消复用 CancellationToken。关闭窗口会取消并等待后台退出，保持 Qt 事件循环响应；网络请求可能等待当前请求超时。Windows 用 Job Object 管理 yt-dlp/FFmpeg 子进程树，不支持时回退 taskkill。强制结束或断电不保证清理，磁盘故障可能使失败记录无法更新。

## 登录与隐私

调用哔哩哔哩官方 `passport-login/web/qrcode/generate` / `poll` 接口，成功后验证 `nav.isLogin` 与实际登录 Cookie。二维码链接仅允许官方 passport/account 的明确登录路径。失败、取消、过期不产生已登录状态。

扫码成功后，在当前 Workspace 的 video-downloads/.bilibili-session.bin 保存 Windows 当前用户范围的 DPAPI 密文（[CryptProtectData](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata)、[CryptUnprotectData](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptunprotectdata)）。先加密再写入，临时文件也是密文，没有明文 Cookie 文件。关闭窗口清除内存副本并保留密文；退出登录同时删除密文。恢复时确认过期才删除，网络失败保留会话并提示待验证；不能加密/解密/写入时明确报错，不宣称已保存。更换 Windows 用户、电脑或用户密钥后可能需重新扫码。Cookie 不从浏览器读取，不写入通用设置、日志、下载元数据或公开偏好文件。工作线程使用独立副本并在结束时清除，凭据通过子进程 **stdin 配置管道** 传给 yt-dlp，不放进进程命令行。当前 yt-dlp 的 Cookie header 兼容路径会提示弃用；输入视频主机设为 `bilibili.com`，使其 CookieJar 仅向 Bilibili 网站/API 发送账号 Cookie，测试确认不会发送给 hdslb 或其他域名。升级后端时须复查这条兼容路径。

QR 轮询的调试日志过滤登录地址，异常和后端诊断移除 URL 与敏感字段。不会保存刷新令牌、二维码密钥或二维码图片，不自动续期或绕过平台会话失效。会话失效后重新扫码。点击「打开原搜索页/原视频」使用系统浏览器，其账号会话与本窗口相互独立。

## 参考项目研究与技术选择

阅读了三个项目的 README、架构和相关核心源码，未把它们整体接入：

| 项目 | 核心源码与结论 | 本次应用 |
| --- | --- | --- |
| [OpenCLI](https://github.com/jackwener/OpenCLI) | [extension background](https://github.com/jackwener/OpenCLI/blob/main/extension/src/background.ts)、[CDP](https://github.com/jackwener/OpenCLI/blob/main/extension/src/cdp.ts)、[bridge](https://github.com/jackwener/OpenCLI/blob/main/src/browser/bridge.ts)、[page](https://github.com/jackwener/OpenCLI/blob/main/src/browser/page.ts)、[Bili 搜索](https://github.com/jackwener/OpenCLI/blob/main/clis/bilibili/search.js)：扩展与本地 daemon 通过 WebSocket 连接，浏览器 debugger/CDP 按目标页执行 JS、页面操作及网络捕获，保留目标/上下文以复用现有会话；等待页面稳定或加载后提取数据。Bili 适配器使用 WBI 签名搜索接口。 | 可参考页面等待/提取思路；完整接入需要 Node、daemon 和扩展，本次不引入。普通匿名 API 已有真实成功结果。 |
| [OpenBiliClaw](https://github.com/whiteguo233/OpenBiliClaw) | [api.py](https://github.com/whiteguo233/OpenBiliClaw/blob/main/src/openbiliclaw/bilibili/api.py)：nav/WBI 签名、搜索结果清理、412/voucher 风控重试；[任务调度](https://github.com/whiteguo233/OpenBiliClaw/blob/main/extension/src/background/bili-task-dispatcher.ts)与[页面提取](https://github.com/whiteguo233/OpenBiliClaw/blob/main/extension/src/content/bili/task-executor.ts)：后台搜索页、MutationObserver/轮询等待卡片，提取标题、链接、图片、作者、时长/日期。另读 [auth.py](https://github.com/whiteguo233/OpenBiliClaw/blob/main/src/openbiliclaw/bilibili/auth.py) 的 Cookie/nav 验证与文件持久化。 | 参考实际卡片选择器与数据字段，API 失败后尝试 HTML 搜索页；采用真实错误状态，不沿用失败返回空列表的行为。无需推荐/个性化系统。指定 UP 主空间搜索参考其 nav/WBI 参数签名；会话用 Windows 用户加密保存，不沿用浏览器 Cookie 读取或明文持久化。 |
| [Playwright](https://github.com/microsoft/playwright) | [browserType.ts](https://github.com/microsoft/playwright/blob/main/packages/playwright-core/src/server/browserType.ts)、[Python BrowserType 文档](https://playwright.dev/python/docs/api/class-browsertype)、[locators](https://playwright.dev/python/docs/locators)：支持页面导航、定位/自动等待、隔离或持久浏览器上下文、Chromium CDP 附加。能处理动态卡片，复用会话需要明确授权及浏览器配置。 | 保留可选路径；当前无需浏览器安装、扩展或大型运行时。HTML 仅为 JS 空壳时诚实报错，并提供打开真实搜索页的入口。未实现 Playwright 渲染后备。 |

最终使用 requests + BeautifulSoup 做轻量搜索，独立 yt-dlp 子进程解析/下载与进度输出，复用 FFmpeg 合并，Qt worker 负责交互。参考了 [yt-dlp Bilibili extractor](https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/extractor/bilibili.py) 的页面初始数据、DASH 格式和访问限制处理。设置常规浏览器 User-Agent/Referer 解决了实测默认请求的 412；没有破解验证或伪造视频结果。

`models/video_download.py` 保存公共数据；`services/video_download.py` 管理搜索/解析/下载/文件；`services/bilibili_login.py` 管理 QR 会话；`services/video_download_state.py` 管理加密会话和公开搜索偏好；`ui/video_download_dialog.py` 集成后台任务和登录子窗口。未来平台支持可按服务层增加，当前 UI/URL 校验明确只支持 Bilibili。

## 验证与实际限制

相关自动检查共 142 项通过（包含原视频素材功能回归、加密会话跨进程恢复/退出删除、网络失败保留、真实 UID 过滤、任务名称/新闻默认排除和排序，以及日期边界、分页、本地时间复核、Demo 阶段/取消/权限失败/无匹配、全部任务遍历、逐项日期、错误后继续、同批目录与日期编号、原标题及同名保护）。测试命令：

```powershell
python -m unittest discover -s tests -p "test_video*.py" -v
```

自动检查包括：原视频窗口入口、新闻条/发射卡/片头、Workspace 与快照保留、URL/短链接与非法主机、API/HTML 搜索及真实失败、格式与权限检查、文件命名/路径、重复复用与丢失文件恢复、进度/取消/子进程树、Qt 主循环与双击保护、依赖缺失、中英文/两种主题，以及 QR 状态/取消/凭据清除/命令行和日志脱敏。网络响应与账号确认部分使用模拟；测试成功不等于所有平台视频可下载。

2026-10-11 的真实网络验证（记录于 `output/video-download-trial/`）：

- 搜索 `SpaceX Starship` 返回 20 个真实结果，Qt 窗口显示封面与字段。
- [Big Buck Bunny 测试视频](https://www.bilibili.com/video/BV1wgHm6tE4J) 的平台 API 允许保存且不是付费内容。影片遵循 [Blender Foundation 的 CC BY 3.0 授权](https://peach.blender.org/about/)。完成两次实际 Qt → QThread → yt-dlp → FFmpeg 下载，所得 H.264 640×360 + AAC MP4 为 35,105,035 字节、约 634.67 秒。ffprobe 检查音视频流，FFmpeg 解码开头三秒及 630–633 秒通过；下载期间 Qt 定时器持续运行。
- 真实传输期间取消，取消记录保留、当前临时媒体清除、此前已完成文件 SHA-256 不变。另用实际父/子进程测试验证 Windows 子进程树取消。
- 真实官方 QR 接口生成二维码并返回等待扫码状态，随后取消；未保存二维码或密钥。初次请求发生 TLS 连接错误，重试成功，并发现接口使用新的 account.bilibili.com 登录路径；校验已同时支持经过确认的两种官方路径。
- 中英文、浅色/深色界面进行 Qt 离屏截图检查。
- 本次新增选项的实际匿名检索：SpaceX Starship 返回 20 条，按原始发布时间正序检查通过；指定 UID 23184651 的空间接口返回 -403 访问权限不足，公开搜索后备取得 2 条 UID 匹配的视频，真实限制保留在界面。记录见 search-options-verification.json。随后真实 Qt 窗口在中英文/两种主题下均返回 2 条 UID 匹配结果，并如实显示 -403 / HTTP 412 限制；搜索期间 Qt 定时器持续运行，截图和记录见 options-ui-verification.json。

- 自动 Demo 真实测试使用另一份公开 [Big Buck Bunny 样片](https://www.bilibili.com/video/BV1vJeW6jEjJ)，其平台允许保存，影片为 Blender Foundation CC BY 3.0。使用样片真实发布日生成明确标记「非实际发射任务」的 JSON 测试夹具，按 2026-09-16–09-17 及 UID 3745053711273842 搜索，找到一条并新下载 32,178,989 字节的 360p H.264 + AAC MP4、约 634.62 秒，ffprobe 和 FFmpeg 开头三秒解码通过，Qt 定时器持续运行、原夹具 JSON 不变。文件与记录见 output/video-download-demo-trial/demo-verification.json。 最终代码再次运行时，页面解析遭遇 HTTP 412，已显示真实失败，原成功记录仍保留；该次未验证完整 Demo 的真实重复复用，记录见 demo-repeat-verification.json。
- 错误日期 2026-10-08–10-09 的真实检索得到零匹配，Demo 显示无匹配、不生成完成文件、既有文件列表保持；记录见 date-negative-verification.json。此前首个搜索索引样片已不可访问（62002），未使用其作为下载成功证据。

2026-10-11 新增整批 Demo 的真实网络验证见 output/video-download-demo-trial/batch-verification.json：使用同一公开 CC BY 3.0 样片的合成 JSON（3 个发射任务记录加 1 条新闻，不代表实际发射），故意选中新闻且改写手动关键词，再运行 Demo。三项都被处理、新闻排除；一项在真实检索/解析/保存权限确认后复用此前实际下载的 32,178,989 字节 MP4，复制到 2026-10-11 日期目录，按完整原视频标题合法化命名，SHA-256 与原文件相同。ffprobe 音视频检查及开头三秒解码通过。另一项在错误日期没有匹配，检索同时遇到 HTTP 412 限制；最后一项缺少日期，均记录失败并继续。Qt 定时器运行 395 次，原 JSON、夹具与既有视频均保持不变。本次批量测试使用实际已有视频缓存，没有重新从网络传输媒体；更早的单任务新下载证据保留在 demo-verification.json。日期编号、同名保护、缓存复制、失败后继续与部分完成后取消在自动测试中验证；未用真实新闻周的全部发射任务跑一整批。

Windows DPAPI 使用合成会话实际加解密并在新 Python 进程恢复；未使用真实账号完成扫码确认，真实账号重启恢复、登录后 UP 接口及增加的画质/内容仍需用户验证；不承诺 VIP/付费下载。当前环境测试使用 Python 3.12.14、PySide6 6.11.2、yt-dlp 2026.8.19 和 FFmpeg 8.0.1，未单独验证项目最低 Python 3.10。未验证 YouTube、动态浏览器渲染或真实 Premiere 导入；Windows EXE 的 3.0.2 构建与启动验收已完成。平台风控、限流、接口变更、区域/账号限制仍可能造成实际失败。

## 本轮代码文件

新增：`models/video_download.py`、`services/video_download.py`、`services/bilibili_login.py`、`services/video_download_state.py`、`ui/video_download_dialog.py`、`tests/test_video_download.py`、`tests/test_video_download_dialog.py`、`tests/test_video_login.py`、`tests/test_video_download_options.py`、`tests/test_video_download_demo.py`、`tools/requirements-video-download.txt`、本说明。

修改：`ui/video_cards_dialog.py`（下载入口及 Workspace）、`ui/main_window.py`（关闭时等待子窗口）、`ui/style.qss`、`language/zh_CN.json`、`language/en_US.json`、`tools/VIDEO_CARDS_UI.md`、`README.md`、`.gitignore`。原有 `ui/video_intro_dialog.py` 和 `tools/video_cards.py` 已检查，未为此功能修改。工作区原先已有的其他改动保留。


2026-10-18 本地改进验证：对「朱雀二号E / 10颗卫星」示例，在 2026-10-14–10-15 的匿名公开检索实际返回 9 条结果，含 10 月 14 日一箭 10 星发射报道；实际检索词为「朱雀二号E」。没有下载这些媒体或确认其转载许可。名称后备、UP 优先与其他作者保留、名单关闭保存通过自动测试；未用用户原始四项 JSON 完整重跑整批下载。记录见 build/video-ux-bilibili-search.json。网络/挑战错误直接报告，不当作零结果触发名称重试；真实零结果才尝试下一个名称。
