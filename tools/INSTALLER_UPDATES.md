# Windows 安装与更新

版本以 `config.app_version()` 为准，目前为 3.1.4。冻结程序读取同次构建生成的 `release.json`。
Windows x64 使用 Python 3.12.12、PyInstaller onedir 和 Velopack 1.2.161。
`tools/requirements-build.lock` 固定构建依赖；这些依赖不要求最终用户安装。

## 构建

准备独立构建 venv，安装锁定依赖；安装 .NET SDK 后执行
`dotnet tool install vpk --tool-path build/release-tools --version 1.2.161`。
FFmpeg、ffprobe、yt-dlp 和字体的来源、许可及校验见 `PACKAGING.md`。

```powershell
build/installer-env/Scripts/python.exe -m pip install -r tools/requirements-build.lock
build/installer-env/Scripts/python.exe tools/build_installer.py --output dist/installer-3.1.4-new
```

目标目录必须为空，脚本不发布、不覆盖旧产物。构建清除默认配置中的 API 密钥和个人路径，
只带代码默认词典，不带个人词典、工作区、模型或验收数据。
产物包括 MSI、Setup.exe、完整更新包、版本源 JSON、RELEASES、校验文件和 build-info.json。
`--previous-feed` 指向上一版产物以生成 Velopack 增量包；`--reuse-onedir` 仅适用于代码未变的已编译目录。
QA 使用 `--qa-version 3.1.4-qa.N`，独立应用 ID、配置目录和更新频道，不发布到稳定源。
MSI 的 ProductVersion 使用三段数字（QA 计入第三段）；应用和更新包继续使用完整 SemVer。
只需要本地更新源时可加 `--no-msi`，不影响主程序保持 onedir。

## 安装与数据

Setup.exe 内嵌本次对应的 MSI，解出后检查 SHA256，再调用系统 Windows Installer。
两个入口使用同一个 MSI 产品和同一套中文 WPF 内嵌向导，不再提供打开即安装的快速入口。
流程为欢迎、离线用户协议、安装位置、确认、实际安装进度和完成；协议默认未同意。
再次运行同一个产品提供修复和卸载。没有增加第二套文件安装或运行中覆盖 EXE 的引擎。

默认目录为 `%LOCALAPPDATA%/Starship30.SpaceWeekly`，首次可选择其他本地可写目录，支持中文和空格。
升级检测 MSI/旧版登记并沿用原目录，不支持通过安装界面直接搬迁已有程序。
开始菜单快捷方式保留，桌面快捷方式可选，完成后可选启动；失败或取消不启动。
主界面按系统明暗模式选择对应 SSPO 品牌资源，普通确认使用同一窗口中的确认区。

内嵌 UI 构建使用固定 WiX DTF 4.0.6（下载校验写在 `msi_wizard.py`），依赖 Windows 自带 .NET Framework 4。
其许可、第三方声明和版本化的中文协议草案分别保存在 `tools/windows` 并随包分发。
草案没有经过法律审核，不能替代项目与各组件的原有许可。构建仅在全部后处理成功后向交付目录复制文件。

首次启动选择独立工作区。个人词典、草稿、图片、音频、字幕、时间表和输出继续存在工作区。
工作区指针和更新设置位于 `%APPDATA%/SpaceWeekly`；QA 为 `SpaceWeekly-QA`。
语音环境和模型保留用户选择的位置（本机现有部署在独立 D 盘目录），不进入程序版本目录。
卸载程序不会清理工作区和模型，清理数据需另行明确操作。

安装目录根部提供独立的 **Uninstall.exe**，与 `Update.exe` 和主程序启动文件并列。
双击会确认卸载，并请求正在运行的 SpaceWeekly 正常保存/关闭；不会强制杀掉配音或渲染。
关闭被取消时可取消卸载。当前 EXE/MSI 安装均调用 Windows Installer；旧 Velopack 快速安装仍保留兼容卸载路径。
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

入口为“设置 → 关于 → 检查更新”。
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

先完成本地升级验收，再核对发布文件与对应源码标签。
发布时上传对应稳定频道的 Setup.exe、MSI、完整/增量 nupkg、releases.win-x64.json、
RELEASES-win-x64、SHA256SUMS.txt。构建工作区和个人配置仅留在本地。
如使用 CI/gh 发布，发布凭据仅从环境变量或 CI Secrets 提供，不加入客户端。

当前未配置 Windows 代码签名或发布签名。SHA256 只能检查文件完整性，不能证明发布者身份。
公开分发前还需落实随包 FFmpeg GPL 和其他依赖的许可要求。
实际验收和未完成项目单独记录，不能用锁定版本或测试通过替代真实安装、真实模型或线上升级验收。

官方依据：[Python 接入](https://docs.velopack.io/getting-started/python)、
[Windows 安装目录](https://docs.velopack.io/packaging/operating-systems/windows)、
[安装器选项](https://docs.velopack.io/packaging/installer)、
[更新源](https://docs.velopack.io/integrating/update-sources)。
