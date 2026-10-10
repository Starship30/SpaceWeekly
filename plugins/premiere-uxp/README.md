# SpaceWeekly Premiere UXP MVP

状态：JSON 导出及插件控制流程已完成离线测试；**没有实际 Premiere / MOGRT 宿主验收，PR 26 与 PR 27 均不能标为已验证可用**。

## 修改文件与职责

| 文件 | 原因 |
| --- | --- |
| `exporters/premiere.py` | 从本次内存结果生成单一 JSON 快照，校验、回退记录、安全写入 |
| `services/settings.py` | 默认关闭开关、设置保存/加载、工作区输出目录同步 |
| `services/weekly_report.py` | 在已有 `_export_reports` 加入可选调度，保持返回值和旧导出行为 |
| `ui/settings_dialog.py`、`language/zh_CN.json`、`language/en_US.json` | 设置复选框及双语名称 |
| `README.md` | 接入入口与验收文档链接 |
| `plugins/premiere-uxp/manifest.json`、`index.html` | 独立 UXP 包、最小界面、按用户选择授权文件访问 |
| `plugins/premiere-uxp/main.js` | 绑定目标、基础预检、首卡验证、串行导入、错误与执行记录 |
| `plugins/premiere-uxp/protocol.js`、`mapping.json`、`text-compat.js` | 共用协议和帧量化、唯一控件映射、少量版本兼容函数 |
| `plugins/premiere-uxp/example.premiere.json`、`README.md` | 三条虚构样例、本机安装、模板约定、版本限制与手动验收 |
| `tests/test_premiere.py`、`tests/test_premiere_uxp.js` | 真实文件导出与模拟宿主控制流程验证 |

## 工作流与生成快照

1. SpaceWeekly 设置 → 导出 → 勾选 **Premiere 内容包（JSON）**。默认关闭；已有工作区配置无需迁移。
2. 正常生成周报。现有联网采集、AI、翻译保持原流程；`_export_reports()` 把这一次入选文章、AI 摘要和 `ExportContext.localized_titles` 交给导出器，没有额外抓取、AI 请求、数据库读取或 Word/Markdown 解析。
3. 设置中的工作区 `output` 目录得到 `SpaceWeekly_YYYY-MM-DD_NNN.premiere.json`。沿用 `reserved_output()` 的独占编号预留、临时文件和 `os.replace()`；取消或失败清除本次预留及临时文件。过程中预留文件为空，无法作为有效 JSON 导入。已有文件不覆盖。
4. 在 Premiere 插件中选择 JSON 和本地 MOGRT，绑定专用空序列，预检，开始导入。

导出按**传入的入选文章顺序**排列，不采用 Markdown 的日期排序或重要性分组。只含入选新闻，不额外加入 Word 的独立发射日程段。Word 的字段隐藏开关不删除 JSON 中的必填字段。

### JSON 协议 1.0

- `protocol_version: "1.0"`、`job_id`（每次成功快照生成的新 UUID）、`report_title`、`language`、`items`。
- `order_policy: "selected_articles_input_order"` 明示顺序。
- 每项 `id / title / summary / source / published / url / categories`；分类保留完整列表。
- `title` 优先非空译标题；否则原始标题。`title_origin` 记录来源。
- `summary` 优先非空 AI 摘要；否则原始摘要。`summary_origin` 和 `summary_fallback` 分别记录来源与 `missing_ai_summary` / `empty_ai_summary`；无回退时为 `null`。
- `published` 保留原始字符串，不强行解析或覆盖。首版没有显示日期字段。
- 当前 `Article`、`News` 没有稳定 ID；使用完整原始 URL 的 UTF-8 SHA-256，前缀 `url-sha256:`。改变列表位置不改变 ID；重复 URL 拒绝导出。
- 无 AI 时分类为 `[]`；原始摘要和日期可以为空字符串。零新闻可导出快照，但插件拒绝启动空任务。
- 不含正文、API Key、应用配置、数据库行 ID、轨道或 Premiere 对象。

[三条虚构新闻示例](example.premiere.json) 由真实导出器生成：前两条展示译标题及 AI 摘要，第三条展示原始摘要回退；域名和新闻均为验收样例。不要把其内容当真实报道。

## 同一个插件包与版本状态

manifest v5 的**加载门槛为 Premiere 26.0.0**，这是插件尝试加载与探测的最低版本，不代表所有 26.x 模板已兼容。界面、协议、映射、目标绑定、串行编排共用；只有 `text-compat.js` 隔离文字读写。没有引入第二套 JSON 或重复插件包。

启动使用 `require("uxp").host.version` / `host.name` / `versions.uxp` 报告实际宿主，并检测 `MogrtText` 构造器及读写方法。运行时还检查时间线接口和实际模板参数，不凭版本号判定兼容。

| 宿主 | 已实现的路径 | 实测宿主版本 | 尚未验证 / 阻塞 |
| --- | --- | --- | --- |
| PR 26.x | 共用 JSON、选择文件、目标绑定、空序列检查、插入、帧时长编排；对实际公开标量文字值做条件性写入与回读探测 | 无 | **完整兼容尚未确立**。26.5 官方类型声明没有 `MogrtText`；通用参数接口不保证 MOGRT 文本可读写。实际模板若返回不透明值、样式 JSON 或接口报错，会在首卡停止；仅插入成功不算验收通过 |
| PR 27.x 提供 MogrtText 的构建 | 同一编排；类型检查 → `MogrtText.setText()` → `createKeyframe(MogrtText)` → `createSetValueAction()` → 文字回读及帧时长核对 | 无 | 官方 27.0 Beta 声明和示例提供该类型；仍需实际构建与 MOGRT 验证文字渲染、字体保持、裁剪、返回片段与宿主 UI 行为 |

### 核实的公开 API 与具体限制

查阅日期：2026-10-09。官网部分类文档仍显示早期类型；27.x 新文字签名以 Adobe 官方类型声明及示例补充核实。

- [SequenceEditor](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/sequenceeditor)：`getEditor(sequence)`、`insertMogrtFromPath(path, TickTime, videoIndex, audioIndex)`。插件只从绑定序列建立 editor，固定写入 V1（索引 0），音频参数为 A1（索引 0），模板必须无音频。
- [VideoClipTrackItem](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/videocliptrackitem)：`getComponentChain()`、`createSetEndAction()`、`getStartTime()`、`getEndTime()`。
- [ComponentParam](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/componentparam)、[Keyframe](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/keyframe)：按所有组件的 `displayName` 搜索标题、摘要及可选来源控件，检查唯一性和 `isTimeVarying()`；使用 `getStartValue()`、`createKeyframe()`、`createSetValueAction()`，不靠固定索引。
- [26.5.0 官方类型声明](https://github.com/adobe/premierepro-types/blob/v26.5.0/src/premierepro.d.ts)：`getStartValue(): Promise<Keyframe>`、`Keyframe.value.value`，`getValueAtTime(TickTime)` 返回公开标量，`createKeyframe` 接受字符串等通用类型。实际 26.x 控件只有返回普通字符串时才允许探测；`getStartValue()` 报错时尝试公开 `getValueAtTime(0)`。随后由实际 `createKeyframe` 类型检查、事务、逐控件回读核验，任何一步失败立即停止。
- **26.x 阻塞点**：声明没有公开的专用 MOGRT 文字类型或样式载荷操作接口；不能由通用 setter 推断 MOGRT 支持。插件拒绝解析/改写未经验证的 `textEditValue` 等内部 JSON，也不把数值、颜色、不透明对象当文字。结构化原模板载荷不能靠字符串覆盖。
- Adobe 的[运行时类型修正](https://github.com/adobe/premierepro-types/commit/38a054af286fa52670a541b324c36da7d65861c5)还记录 `getValueAtTime()` 的 `{ value: scalar }` 包装形式；条件路径接受实际普通字符串或这份公开声明中的字符串包装，仍拒绝样式载荷。类型声明与较早官网签名存在差异，必须以本机实测最终确认。
- [27.x 官方类型声明](https://github.com/adobe/premierepro-types/blob/38a054af286fa52670a541b324c36da7d65861c5/src/premierepro.d.ts)、[官方变更记录](https://github.com/adobe/premierepro-types/blob/main/CHANGELOG.md)、[官方 MOGRT 文字读取示例](https://github.com/AdobeDocs/uxp-premiere-pro-samples/blob/main/sample-panels/premiere-api/src/keyframe.ts)：27.0.0-beta.22 起声明 `MogrtText`；`getStartValue` 返回专用对象，`createKeyframe` 可接受该对象，`getText / setText` 提供公开文字操作。`setText` 会把多文字样式折叠为单一 run，因此首版**拒绝多样式控件**，从实际值对象改文字以保留单一字体样式。
- [TickTime](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/ticktime)、[SequenceSettings](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/sequencesettings)：`getVideoFrameRate()` 从 26.2 才提供。本机 26.0 已确认缺少该方法；较早宿主使用公开 `Sequence.getTimebase()`（25.6 起）读取每帧 tick 数，写入 `FrameRate.ticksPerFrame`，不猜测或手填帧率。`round(seconds × fps)` 量化整数帧，再 `createWithFrameAndFrameRate(frameCount, frameRate)` 创建时间对象。以累计整数帧定位，避免逐卡浮点秒累计；26.0 回退仍需真实预检及导入验收。
- [Project](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/project)：`getProject(Guid)`、`getSequence(Guid)`、`lockedAccess()`、`executeTransaction()`。`getSequence` 在最新声明中返回 Promise，代码统一 `await`，兼容早期同步返回。只执行当前卡片的文字和结束时间事务，不承诺跨整个任务的原子回滚。
- [宿主信息](https://developer.adobe.com/premiere-pro/uxp/resources/recipes/host-info/)、[文件访问](https://developer.adobe.com/premiere-pro/uxp/resources/recipes/filesystem-operations/)：使用 `localFileSystem: "request"` 和用户文件选择器；无需完整磁盘权限、网络权限或服务。运行记录保存在 UXP 插件数据目录。

若 26.x 模板首卡阻塞，可行的当前替代是使用提供已核实 `MogrtText` 能力的 27.x 构建，再做完整宿主验收；或在 26.x 手动填写模板文字（这是人工流程，**不算自动兼容**）。另一个 26.x 模板只能在真实宿主通过标题、摘要及存在的来源控件写入/回读和可见文字验收后才算适用。是否增加旧技术兼容端应在记录具体构建、模板和错误后另行讨论；本阶段没有 CEP、ExtendScript、QE DOM 或 Bridge。

## 本机加载插件

### 26.3 本机排查结果（2026-10-09）

Premiere 26.3.0 / UXP 9.3.0 的现有模板已实测：插入成功；补齐组件遍历的 `await` 后可找到 `SW_SUMMARY`。其 `getStartValue()` 返回对象，`value` 与嵌套 `value` 仍为对象；公开 `getValueAtTime(0)` 调用失败，因此尚不能自动写入文字，未通过完整兼容验收。27.x 尚未实测。

面板启动时若活动序列 V1 恰有一个片段，会只读列出组件及参数名称，并检查必需文字控件；不会写入、裁剪或重放失败任务。读取失败保留原片段。该诊断用于定位模板/API 限制，不能当成导入成功。

1. 准备 Premiere 26.x 或提供上述能力的 27.x 构建，以及 Adobe UXP Developer Tool（UDT 2.2 或更新版本）。不要把 27.x Beta 类型声明当成当地稳定版已具备能力的证明。
2. 在 Premiere 的设置中启用开发者模式；在 UDT 启用 Developer Mode。启动 Premiere，确认它出现在 UDT Connected Applications。
3. 在 UDT **Add Plugin**，选择此目录的 `manifest.json`，再选择目标 Premiere 并 **Load**。无需 npm 安装或构建。
4. 在 Premiere 的 UXP 插件菜单打开 **SpaceWeekly** 面板，查看启动输出中的确切 Premiere / UXP 版本与文字路径。开发调试使用 UDT 的 Debug；本仓库没有自动打包、发布或安装。

参考：[官方 UDT 工作流](https://developer.adobe.com/premiere-pro/uxp/plugins/tutorials/udt-deep-dive/)。菜单位置可能因宿主版本不同，按该构建的 UXP 开发者设置操作。

## MOGRT 制作约定

首版建议制作一个简单、无音频、无需外部图片或媒体的 AE 新闻卡片合成：背景加标题、摘要文字层，来源文字层可选。把对应 Source Text 暴露到 Essential Graphics，并将**显示控件名**精确命名如下，导出本地 `.mogrt`。

| JSON 字段 | 自定义模板控件名 | 内容 | 要求 |
| --- | --- | --- | --- |
| `title` | `SW_TITLE` | 新闻标题 | 必需 |
| `summary` | `SW_SUMMARY` | 新闻摘要 | 必需 |
| `source` | `SW_SOURCE` | 来源 | 模板控件可选，JSON 字段仍保留 |

这些是 SpaceWeekly 自定义约定，**不是 Adobe 内置参数名**。唯一映射在 `mapping.json`。模板没有 `SW_SOURCE` 时跳过来源写入与回读，保留 JSON 中的 `source`；有该控件时照常填写，仍检查类型及唯一性。标题和摘要始终必需。所有存在的映射控件必须唯一、可编辑、无文字关键帧，27.x 使用单一文字样式（整段同一字体、字号等）；保留中文字体，提前检查摘要文本框容量。不要对 Source Text 添加会覆盖输入的表达式。

模板原生时长应至少达到计划卡片时长，并在目标序列帧率下留足裁剪余量。默认 8 秒时建议使用长于 8 秒的静态模板，先验证结束时间能裁到量化后的帧数；不要假设任意模板均可延长。PR 原生图形导出的 MOGRT 或其他来源模板须单独验收，不能由文件扩展名推断文字接口支持。

## 手动验收（每个宿主版本单独执行）

记录**确切宿主版本/构建、UXP 版本、模板文件及来源、测试日期**。以下任何实际宿主测试本次均未执行。

1. 建立测试工程及专用空序列，至少有 V1 / A1；首版不允许字幕轨道，所有视频/音频轨道必须空且无转场。解除轨道锁定，停止播放。激活序列，勾选专用空序列声明并点击绑定，核对面板显示的项目/序列名称与 GUID。
2. 导入 `example.premiere.json` 和自己的本地 MOGRT，保持 8 秒，点击预检。此时不得新增时间线片段；日志说明仅完成基础预检，尚未验证模板控件。
3. **先验最关键路径**：从示例复制只保留一条 `items`，给予新的 `job_id`，在新的空序列开始导入。确认 MOGRT 插入 → 找到唯一的标题、摘要及存在的来源控件 → 写入并回读 → 结束时间与帧数一致。没有来源控件的模板应正常完成，JSON 中来源保留。目视检查节目监视器中的中文、字体和文字布局；参数回读不能代替渲染检查。失败时保存日志，记录当前遗留卡片，禁止继续多条。
4. 首卡通过后，使用原三条示例及另一个空序列，按示例顺序生成三卡。25 fps 时每卡 200 帧，总计 600 帧；30000/1001 fps 时每卡 240 帧，总计 720 帧（约 24.024 秒）。检查无间隙/重叠、类别和原始日期仍在 JSON 中、摘要第三条正确使用原文。
5. 重复点击开始不得并发。完成后再次选择同 `job_id`，即使换一个空序列也必须拒绝；对非空序列绑定也应拒绝。一次失败或中断记录也禁止自动重放。
6. 测试缺控件、重复控件、错误控件类型、多样式、带音频模板、过短模板、错误协议/重复 ID。失败后应立即停止后续新闻，显示完成数、插入数、条目 ID 和错误，序列可能保留失败项。
7. 导入过程中切换活动序列/项目、关闭目标、改变帧率、编辑卡片位置或新增轨道内容：应在下一检查停止，绝不能往新活动序列写入。不要在正常使用中边导入边编辑或播放。
8. 分别在真实 26.x 与 27.x 重做以上验收。26.x 必须可见写入标题、摘要及模板存在的来源控件才算完整兼容；只有插入不合格。若失败，记录 `getStartValue / getValueAtTime / createKeyframe / createSetValueAction` 的具体异常。

## 执行记录与失败恢复

`plugin-data:/runs.json` 存储 `job_id`、宿主能力、绑定目标、帧设置、`running / completed / failed` 状态、`attempted_id`、已插入 ID、已完成 ID 和错误。写入开始记录后才修改时间线，每卡保存进度。记录存在的任务一律拒绝自动重放；崩溃留下的 `running` 也拒绝。

记录缺失是首次使用；记录损坏、无法读取或保存则停止。文件写入本身不是跨宿主事务；中断写入可能损坏，插件会在再次读取时拒绝继续。记录只属于当前插件安装，不跨电脑/用户/卸载保留；空序列检查提供额外保护，但不是全局任务去重服务。

部分失败**不自动删除已插入 MOGRT，不自动重试，也不承诺原子回滚**。人工检查已完成项及 `attempted_id`（插入调用报错时可能仍有变化）。确认宿主时间线后，由用户手动整理或撤销。确需重做时，用户明确制作新的任务快照/`job_id` 并使用新的空序列；不要仅清除记录静默重放。插件不保存工程，最终保存由用户完成。

## 已执行检查

```powershell
python -m unittest tests.test_premiere -v
node tests/test_premiere_uxp.js
node --check plugins/premiere-uxp/main.js
node --check plugins/premiere-uxp/text-compat.js
```

- 新增 Python：10 项通过，覆盖字段优先级/回退、中文、原始日期、顺序、稳定身份、编号、取消/写入/替换失败、设置保存及默认关闭调度。
- Python 回归：新测试加 `test_v242 / test_spacecom_web / test_date_ranges / test_provider_selection`，共 60 项通过。当前 Windows 沙箱阻止系统临时目录中的 `os.replace`，因此回归运行将 `tempfile.tempdir` 指向仓库 `build`；没有改生产写入代码或旧测试。
- Node：13 组模拟宿主行为检查通过，包括 PR 27 类型路径、PR 26 公开标量条件路径、缺少来源控件仍完成导入、标题/摘要缺失拒绝、不可用文字类型拒绝、帧量化、重复任务、部分失败、目标切换、并发与执行记录。**模拟 PR 26/27 版本号不是实测宿主版本。**
- 真实导出器已生成并通过插件协议读取三条中文示例。
- PySide6 无窗口设置冒烟检查通过：默认关闭、勾选后保存值为 true，现有 Word/Markdown 选择保留；JS 语法检查和 `git diff --check` 通过。

## 范围限制

一个本地 MOGRT、V1、固定卡片时长、只向专用空序列从零顺序追加；开始时首卡是能力门槛，纯预检无法在不插入模板的情况下验证控件。只检查并使用公开接口，不反向解析 MOGRT 内部内容。

没有数据库迁移、图片字段/采集/替换、已有时间线更新、多模板、片头片尾、自动保存、渲染、HTTP/WebSocket、常驻后台、跨剪辑软件抽象或通用框架。无法保证用户在检查与同步写入之间的全部宿主操作都原子隔离；实际宿主并发行为仍需上述验收。
