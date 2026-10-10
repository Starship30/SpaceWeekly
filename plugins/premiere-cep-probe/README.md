# Premiere 26.3 单卡实验（不是正式兼容端）

只验证一张已有 MOGRT。未确认前不做批量导入，也不接入 SpaceWeekly 的导出调度。现有 UXP 插件保持原样。

`panel.js` 直接 require 相邻 UXP 的 `protocol.js` 和 `mapping.json`，复用校验与帧量化，不复制它们。`probe.jsx` 仅包含公开 ExtendScript 的目标检查、单卡插入、控件文字读写与时长检查。无 Bridge、服务、QE、重试、自动保存或自动回滚。

## 安装与执行

1. 本机 Premiere 文件版本已核实为 26.3.0.93，CEPHtmlEngine 为 12.0.1。这个面板只允许 26.3。
2. 在仓库中运行 `powershell -NoProfile -File plugins\premiere-cep-probe\install-local.ps1`。脚本在用户 CEP 扩展目录创建普通文件夹，仅复制面板 HTML、JS 和 manifest；`probe-source.json` 指向仓库源码，JSX、JSON 校验和映射仍直接共用仓库文件。不覆盖现有面板、不改注册表。修改面板 JS 或 manifest 后需要更新安装文件；只修改 JSX 或共享文件时无需复制。
3. 本实验未签名。若 CEP 未启用开发面板加载，请由用户自行设置 Adobe 官方开发开关：`HKCU\Software\Adobe\CSXS.12` 下字符串 `PlayerDebugMode=1`。这是 CEP 的签名检查调试设置，区别于 UDT 的 UXP 开发模式。测试后恢复原值。不要为了此实验修改防火墙、系统安全设置或禁用其他检查。
4. 重启 Premiere，先确认已保存的空工程可以正常操作，再从 **窗口 → 扩展 → SpaceWeekly 单卡验证（实验）** 手动打开。先只检查面板能否正常显示；稳定后再建立专用空序列（含 V1、A1）。保留此前失败片段所在序列，不使用它作为空目标。退出前关闭实验面板：CEP 会恢复退出时仍打开的面板，`AutoVisible=false` 不能阻止工作区恢复。
5. 选择单卡测试 JSON 和此前真实的 `未命名.mogrt`。JSON 可以有多条，但明确只处理第 1 条；建议使用新的独立单卡测试 job_id。
6. 声明并绑定专用空序列，设置 8 秒，再执行一次。逐步结果和目标信息写入仓库 `output/cep-single-card-<job_id SHA256>.json`。
7. 同一 job_id 的记录使用独占创建，即使失败也禁止重放。失败时保留片段和记录；不删除原 UXP 执行记录。修复后新建测试任务及空序列。
8. API 回读全部通过后，再人工核对节目监视器：中文字形、换行、字体、样式、实际标题摘要及卡片时长。回读通过不等于视觉验收通过。

初始 `getValue` 必须返回字符串，但它可能带样式 JSON；这不证明可写或不可写。本实验只尝试 Adobe 官方示例的 `setValue(普通文字, true)`，不修改 JSON 内部字段。写入后必须回读到完全相等的普通文字，其他结果完整记录并立即停止；不会为了通过检查而解析或改写未知载荷。`setValue` 返回结果、逐控件回读以及 start/end tick 数均检查；绑定目标在每次写入前重新核验。

“读取当前单卡控件（只读）”用于保留失败现场时诊断：当前序列必须只有 V1 上的一张卡片，控件返回值保存到 `output/cep-text-inspection-<时间戳>.json`，不会插入、改文字或调时长。

## 核实的来源

- [Adobe PProPanel 官方 MOGRT 示例](https://github.com/Adobe-CEP/Samples/blob/master/PProPanel/jsx/PPRO/Premiere.jsx)：`importMGT`、`getMGTComponent`、遍历参数及 `getValue`/`setValue`。示例接口存在不代表本机模板写入成功。
- [Adobe ExtendScript 类型声明](https://github.com/Adobe-CEP/Samples/blob/master/PProPanel/jsx/PremierePro.23.0.d.ts)：时间对象、轨道、目标 sequenceID 等。
- [Adobe CEP 12 CSInterface](https://github.com/Adobe-CEP/CEP-Resources/blob/master/CEP_12.x/CSInterface.js)：本实验仅使用其原生 `getSystemPath` 和 `evalScript` 调用方式，没有复制整套 CSInterface。
- [CEP 开发指南](https://github.com/Adobe-CEP/CEP-Resources/blob/master/CEP_12.x/Documentation/CEP%2012%20HTML%20Extension%20Cookbook.md)：扩展位置、manifest 和开发签名检查。

## 验证记录

`node tests/test_premiere_cep_probe.js` 是模拟宿主检查，覆盖单卡文字回读、帧时长、非空/切换目标、重复控件、不透明文字和失败停止，不能作为真实兼容性证据。

真实模板在本地找到：`C:\Users\NASA\AppData\Roaming\Adobe\Common\Motion Graphics Templates\未命名.mogrt`，包含 AE 工程与标题、摘要控件。本实验结果必须以本机 `output` 记录和视觉核验补全；尚未执行的步骤不得记为通过。

单卡测试包为仓库 `output/premiere-cep-one-card-c416e9c9-e2eb-4357-9da8-868bd50ec4f0.json`，沿用现有实际导出第一条的身份和字段，仅替换测试标题、两行摘要和独立 job_id。开发签名设置由用户操作。

用户曾报告在打开工程或重启时卡死，未产生新单卡执行记录。旧目录连接已移至 `CEP/spaceweekly.cep.singlecard.disabled`（扫描目录外）。源码、失败片段、工程、记录及现有 UXP 均保留。这是隔离措施，不证明卡死根因。

用户随后确认空工程恢复正常。已移除 manifest 的 ScriptPath，统一在按钮调用时才 evalFile 加载 JSX；面板打开时不调用 Premiere 脚本、不读取活动序列或修改时间线。恢复旧面板 ID 注册后仍复现工程打开无响应，尚未手动打开面板或执行新的文字测试；延迟加载 JSX 未解决启动问题。该轮只在确认空工程无修改后终止了无响应进程，没有保存或覆盖工程。

2026-10-09 又以普通文件夹安装相同面板 ID：首页显示正常，但用户打开空工程后报告卡死。移出扫描目录到 `CEP/spaceweekly.cep.singlecard.physical.disabled` 后，检测到 `sxc.prproj` 响应，用户反馈“有一点卡，但还是能操作”。本轮没有终止进程，也没有执行单卡写入。普通目录未排除启动问题；不是目录连接单独导致卡死的证据。

已读 CEP 临时日志：旧面板有 `Injecting cep_node`、`GL is disabled` 和 Heartbeat 记录，但本轮没有更新的面板渲染日志，未见 CEPHtmlEngine 进程。没有卡死调用栈，不能据此归因于 GPU、Node 或模板文本接口。未重置用户偏好或删除媒体缓存。

普通目录安装已改用新的面板 ID `spaceweekly.cep.singlecard.manual`，保持同一 bundle、校验、映射和单卡测试代码。新 ID 用于避免恢复旧工作区中的实验面板实例，不是已证实的修复。模拟检查已验证两个 manifest ID 一致、无 ScriptPath、AutoVisible=false、面板启动零宿主调用以及普通安装目录指向共享源码。

新 ID 实测：用户能打开面板，但内容白屏；18:18 的新日志仅有 Node 注入记录，renderer 日志为空。安装 HTML 完整，panel.js 哈希与源码一致，尚不能归因于 Node 或 GPU。当前已将**本机安装的 manifest 临时改为 `loading-check.html`，移除 CEFCommandLine**；这是无脚本、无 Node、无宿主调用的纯静态显示检查。仓库正常 manifest、panel.js、probe.jsx 未因此改写。需关闭面板并完整重启 Premiere 后再手动打开，反馈能否看到“SpaceWeekly 静态页面已加载”。当前安装不能执行单卡导入。检查完成后才恢复源码 manifest（MainPath=index.html）并继续逐步排查；不能把静态显示成功当作单卡兼容成功。

| 检查项 | 当前结果 |
| --- | --- |
| 26.3.0.93 安装版本、CEP 12.0.1、真实模板及两个控件名 | 已核实；两个控件实际返回样式 JSON 字符串 |
| 模拟单卡、中文回读、帧时长、失败及目标保护 | Node 检查通过，仅模拟 |
| 既有 UXP 模拟检查、JSON 导出测试 | 16 组、10 项通过 |
| Premiere 实际插入 | 已通过，节点 000f4241，标题及摘要控件各一个 |
| 启动及工程打开 | 旧 ID 的延迟 JSX、普通目录安装仍有卡死反馈；新手动面板 ID 可打开但白屏，纯静态检查待实测 |
| 文字写入回读、时长 | 首次在文字写入前被过严的初始值检查阻止；修正后尚待新单卡任务实测 |
| 字形、换行、字体及样式 | 待真实 API 检查后视觉验收 |

静态页复测仍未显示任何文字（用户截图为深色空面板）。已从真实 CEPHtmlEngine 命令行确认入口是安装目录的 `loading-check.html`，命令行配置为空对象，且存在 renderer 进程；18:21 的新面板日志为空。安装静态页内容完整。这排除了本次页面需要 Node、业务 JS 或 MOGRT 调用的前提，但尚不能在页面加载、缓存、CEF 渲染或宿主环境之间定位根因。

用户确认退出后，已检查 Premiere 和本实验 CEP 进程均已退出，将 `Temp/cep_cache/PPRO_26.3.0_spaceweekly.cep.singlecard.manual` 移至 `C:\Users\NASA\AppData\Local\Temp\spaceweekly-cep-manual-cache-backup-20261009`。原缓存位置已不存在，内容完整保留，等待同一静态页复测。未动全局 CEF 缓存、其他插件、注册表或工程设置。

缓存隔离复测：用户仍报告空白，新的专属缓存已重建。Windows Application 事件提供了额外证据：2026-10-09 18:20:43（本机时间）的事件 1000 记录 `CEPHtmlEngine.exe 12.0.1.2` 在 `libcef.dll 99.2.15.0` 中出现 `0xc0000005`，偏移 `0x118c6d1`；18:20:48 的事件 1001 记录相同 APPCRASH。该事件发生于较早一轮，不能直接当作最新空白的原因，也不能据此认定 GPU 故障。

当前本机诊断配置：保持 `loading-check.html`、无 Node 和宿主脚本，仅在安装 manifest 中增加 `--disable-gpu`，用于下一次静态页渲染对照。仓库正常 manifest 未改，不改变 Premiere 工程的渲染器。参数及配置已静态核对，真实效果待重新打开宿主后确认。依据：[Adobe CEP 自定义 CEF 参数](https://github.com/Adobe-CEP/CEP-Resources/blob/master/CEP_12.x/Documentation/CEP%2012%20HTML%20Extension%20Cookbook.md#customize-cef-command-line-parameters)、[Chromium 参数定义](https://chromium.googlesource.com/chromium/src/+/refs/heads/main/content/public/common/content_switches.cc)。若此对照仍失败，应保留证据并处理 CEP 宿主加载问题，不继续改文字接口或宣称兼容。

GPU 对照结果：仍黑屏。已核实实际进程入口为静态页，编码参数解码为 `["--disable-gpu"]`，renderer 使用 `--disable-gpu-compositing`，GPU 进程使用 `--use-gl=disabled`。18:38:54 的本轮日志记录 ANGLE/Vulkan 初始化错误 `-3`、`eglInitialize SwANGLE failed` / `EGL_NOT_INITIALIZED`，随后 GPU 进程退出。说明此渲染回退也失败；不能据此断言显卡驱动、运行库或安装文件中的哪一项是根因，更不能判定 MOGRT 文字接口不可用。

实验已停止加载：本机安装完整移至 `C:\Users\NASA\AppData\Roaming\Adobe\CEP\spaceweekly.cep.singlecard.manual.disabled-20261009`，原 extensions 注册路径已不存在。当前已打开的黑面板需手动关闭；没有终止宿主进程。三份现场日志保存到 `output/cep-loading-failure-20261009/`。暂不恢复注册或增加更多猜测性启动参数；先解决宿主静态页面显示问题。单卡文字写入、回读和时长验证仍未完成，现有 UXP、JSON 导出、工程和此前失败片段保留。

后续官方对照（同日）：已下载并校验 Adobe-CEP/Samples 固定提交 `e4946b73ac1e566dced8e95dba10811c31036927` 的 UISamples，页面资源原样保留，只调整安装 manifest 的 PPRO 范围与手动打开设置，不安装 .debug。用户实际打开官方 UI 面板仍为空白；进程确认加载官方 index.html、扩展 ID 正确、自定义参数为空，日志为空，未见该轮匹配崩溃事件。该结果支持共用 CEP 加载/渲染环境方向，不能确定驱动、Vulkan 或项目代码根因。官方临时安装随后也已移出扫描目录，原失败实验未恢复。详细版本、签名、源码哈希、对照差异和解释边界见 `output/cep-official-comparison-20261009/DIAGNOSIS.md`。26.3 单卡文字、回读及时长验收仍未通过。

实际运行 `test1.prproj / 序列 01` 成功插入 `D:\Desktop\未命名.mogrt`。失败记录：`output/cep-single-card-0b25340275ecdc55881d8158407a9d574cc187ae7d987aafafcc1987c1b361da.json`。只读诊断：`output/cep-text-inspection-1791534385174.json`。`SW_TITLE` 为 OPPOSans-Bold / 70 / 斜体，`SW_SUMMARY` 为 OPPOSans / 34，均单文本段且有 `textEditValue`；这是观察值，不是公开载荷规范。尚未写文字或调整时长。新版本撤销“初始样式 JSON 必须拒绝”的错误推断，只尝试官方字符串写法，并保持写后回读失败立即停止。需新 job_id 及新的专用空序列，禁止重放旧任务。
