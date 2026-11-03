# Windows 安装与更新

版本以 `config.app_version()` 为准，目前为 3.1.2。冻结程序读取同次构建生成的 `release.json`。
Windows x64 使用 Python 3.12.12、PyInstaller onedir 和 Velopack 1.2.161。
`tools/requirements-build.lock` 固定构建依赖；这些依赖不要求最终用户安装。

## 构建

准备独立构建 venv，安装锁定依赖；安装 .NET SDK 后执行
`dotnet tool install vpk --tool-path build/release-tools --version 1.2.161`。
FFmpeg、ffprobe、yt-dlp 和字体的来源、许可及校验见 `PACKAGING.md`。

```powershell
build/installer-env/Scripts/python.exe -m pip install -r tools/requirements-build.lock
build/installer-env/Scripts/python.exe tools/build_installer.py --output dist/installer-3.1.2-new
```

目标目录必须为空，脚本不发布、不覆盖旧产物。构建清除默认配置中的 API 密钥和个人路径，
只带代码默认词典，不带个人词典、工作区、模型或验收数据。
产物包括 MSI、Setup.exe、完整更新包、版本源 JSON、RELEASES、校验文件和 build-info.json。
`--previous-feed` 指向上一版产物以生成 Velopack 增量包；`--reuse-onedir` 仅适用于代码未变的已编译目录。
QA 使用 `--qa-version 3.1.2-qa.N`，独立应用 ID、配置目录和更新频道，不发布到稳定源。
MSI 的 ProductVersion 使用三段数字（QA 计入第三段）；应用和更新包继续使用完整 SemVer。
只需要本地更新源时可加 `--no-msi`，不影响主程序保持 onedir。

## 安装与数据

Setup.exe 是 Velopack 一键安装器，默认安装到 `%LOCALAPPDATA%/Starship30.SpaceWeekly`，
创建桌面和开始菜单快捷方式；不需要管理员权限或用户 Python。
安装进度使用固定浅色背景、深色中文提示和实际进度条。
再次运行出现“已安装”提示，可取消、打开安装目录或修复；这和软件内的首次启动向导不同。
Windows 的确认和错误对话框仍由系统绘制。

需要安装向导及“修复 / 卸载”维护界面时，使用同目录的 **MSI 安装包**。
这是 Velopack 官方支持的 Windows Installer 打包方式，按用户安装，不要求管理员权限。
再次打开同一 MSI 由 Windows Installer 显示维护选项。原生 Setup.exe 的内置提示只有修复，
不能通过应用 QSS 增加卸载按钮，因此它继续作为快速安装选项，不声称已经改变其按钮。
安装和维护页使用白色背景资源，避免将深色标题图与系统文字颜色混用。

首次启动选择独立工作区。个人词典、草稿、图片、音频、字幕、时间表和输出继续存在工作区。
工作区指针和更新设置位于 `%APPDATA%/SpaceWeekly`；QA 为 `SpaceWeekly-QA`。
语音环境和模型保留用户选择的位置（本机现有部署在独立 D 盘目录），不进入程序版本目录。
卸载程序不会清理工作区和模型，清理数据需另行明确操作。

安装目录根部提供独立的 **Uninstall.exe**，与 `Update.exe` 和主程序启动文件并列。
双击会确认卸载，并请求正在运行的 SpaceWeekly 正常保存/关闭；不会强制杀掉配音或渲染。
关闭被取消时可取消卸载。MSI 安装调用 Windows Installer，EXE 安装调用 Velopack 卸载器。
安装与更新的快速回调从版本目录补齐根部入口，不覆盖正在运行的主程序。
`Uninstall.exe` 使用 Windows 10/11 自带的 .NET Framework 4，不需要用户 Python、.NET SDK 或语音模型。
构建机另需系统 `Framework64/v4.0.30319/csc.exe`，脚本会明确检查；这不是最终用户的构建依赖。
静默验收可用 `Uninstall.exe --silent`；软件仍在运行时返回 10，保持程序与数据不变。

旧便携版可在首次启动或“设置 → 关于”选择**具体旧目录**和空的目标目录导入。
导入先生成 ZIP 备份，不删除原目录；复制已知数据目录并调整被复制素材的绝对引用。
外部模型/环境原位保留；未复制的外部引用记录在导入清单，移动旧目录前需检查清单。
工作区格式标记和迁移备份位于工作区 config，较新且不兼容的格式会拒绝打开。
未实现旧版格式自动回滚。

## 软件内更新

入口为“设置 → 关于 → 检查更新”；顶部“更多 → 关于”到达同一页面。
同页提供“卸载 SpaceWeekly”，先处理未保存内容和制作任务，再交给框架/Windows Installer。
此入口调用安装目录的同一个 Uninstall.exe。
启动后后台检查可关闭。无更新的后台检查不弹窗，网络失败不阻断制作。
稳定源为公开 GitHub Releases：`https://github.com/Starship30/SpaceWeekly`，
频道 `win-x64`，客户端无 GitHub 令牌，不接受草稿、预发布或降级。

下载由 Velopack SDK 校验，缓存保留原始发布元数据的 SHA256。
SDK 的本地待安装记录可能省略校验值，因此程序流式计算本地包 SHA256 并与原始清单比较。
若 SDK 的已安装基础包没有校验值，退回校验完整更新包，不把空值当成可信的增量基础。
重启更新前重新校验，走已有未保存提醒、任务取消/等待和 Qwen 卸载流程，随后框架等待主进程退出并安装。
不会在运行中复制覆盖 EXE。下载包和待安装包不属于三天清理白名单。
失败保留现有版本和制作成果；不承诺未验证的自动回滚。

## 手动发布

本轮不创建或覆盖 GitHub Release。先完成本地 A→B 验收，再人工审查文件。
发布时上传对应稳定频道的 Setup.exe、完整/增量 nupkg、releases.win-x64.json、
RELEASES-win-x64、SHA256SUMS.txt；不得上传 build 工作区或个人配置。
如使用 CI/gh 发布，发布凭据仅从环境变量或 CI Secrets 提供，不加入客户端。

当前未配置 Windows 代码签名或发布签名。SHA256 只能检查文件完整性，不能证明发布者身份。
公开分发前还需落实随包 FFmpeg GPL 和其他依赖的许可要求。
实际验收和未完成项目单独记录，不能用锁定版本或测试通过替代真实安装、真实模型或线上升级验收。

官方依据：[Python 接入](https://docs.velopack.io/getting-started/python)、
[Windows 安装目录](https://docs.velopack.io/packaging/operating-systems/windows)、
[安装器选项](https://docs.velopack.io/packaging/installer)、
[更新源](https://docs.velopack.io/integrating/update-sources)。
