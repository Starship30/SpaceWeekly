# Desktop workflow migration

Checkpoint before migration: commit `90db4de`, annotated local tag `3.1.0demo`.
The tag records the working launch research, roundup and optional TTS implementation; no EXE was rebuilt.

| Previous entry | Production page |
|---|---|
| RSS panel, dashboard dates and collection | Materials: sources, independent news/launch ranges, collection |
| Roundup task selection/research | Materials: launch list and inline evidence/details |
| News generation / roundup modal editor | Script: separate news and launch-roundup editors |
| Roundup voice tab | Voice & visuals: persistent segment speech controls |
| Video cards, opener and nested downloads | Voice & visuals: peer tools, existing workers/renderers |
| Scattered format/export buttons | Export: selected content, formats, destination and results |
| Settings / writing rules | One secondary settings area, with existing rule editor |
| Launch diagnostic, about, connection tests | Secondary tools, outside the production navigation |
| Always-visible full log | Collapsible bottom task/log area |

Implementation keeps one persistent controller per tool. Switching production pages does not recreate
controllers, launch drafts, Qwen processes or download workers. Standalone adapters remain for existing
callers/tests; the main production UI does not use their modal `exec()` adapters.

## 使用

仍通过 `python main.py` 启动源码桌面。首次工作区选择器保留；顶部“新建 / 打开工作区”
可以选择新目录或现有工作区。顶部显示期次标题、新闻日期、周报发射日期、综述日期及保存状态，
三者不会被合并为同一范围。

1. **资料**：在“来源与日期”设置新闻和周报发射范围，显式采集。在“发射任务与资料”
   设置综述范围、选入任务、重点、分组和顺序；列表旁查看原说明、来源片段及确认状态。
   “补充缺失资料”“重新核对选中任务”“同步已保存资料”复用已有资料服务，前两项明确联网。
   “已补充资料 / LL2 / 独立支持 / 人工确认”分别显示，不互相代替。
2. **稿件**：顶部选择“新闻周报”或“每周发射综述”。新闻复用原筛选、摘要与标题流程，
   综述复用原整稿生成、单段重写和润色流程。人工编辑标识保留，整稿覆盖需要原有确认。
   正式名称和任务关联在折叠区域，事实稿、屏幕文字和最终朗读分别编辑。
3. **配音与画面**：从保存的期次或“稿件 → 打开草稿”直接进入分段试听。
   综述配音、逐次卡片、片头和下载为同级标签；“从当前稿件创建卡片”沿用原稳定 ID、
   渲染器和素材链接。切换标签不关闭下载，不新建 Qwen 提供商。
4. **导出**：统一选择输出目录，按内容类型显示格式。没有 TTS 也可导出综述稿件与快照；
   未齐备有效音频时不生成假的整段 WAV/SRT。新闻导出只读取准备好的内容，日期变化时要求
   恢复范围或显式重新采集，不会为导出隐式联网。

API、搜索、语言、主题、默认参数及 TTS 路径集中在顶部“设置”，写作规则保留现有编辑器。
说话人、发音替换、段间停顿及预览时长留在配音页面。连接测试与模型查询使用后台任务，
“更多 → 测试发射数据”复用 LL2 诊断且可请求取消。

底部默认只显示任务摘要，完整错误及进度在“展开任务与日志”查看。取消等待实际线程或
模型后端确认；网络阻塞调用可能等到超时才返回。已经完成的素材不会被取消操作删除。

## 保存与恢复

“保存期次”将当前内容写入工作区 `config/production-session.json`，修改自动恢复文件为
`cache/production-recovery.json`。内容包括新闻与原始候选、综述快照和人工编辑、卡片、
片头、待应用的日期/配音输入以及页面选择；不会把 API 密钥或登录 Cookie 写入期次文件。
打开时使用较新的有效文件。损坏恢复文件不会被后台静默覆盖。

“更多 → 打开期次 / 版本”可以恢复完整期次；替换新闻或卡片前的版本位于
`output/production-versions/`。关闭或切换工作区会处理未保存编辑并等待运行任务取消、
Qwen 卸载确认。切换内容类型和页面保留编辑器实例，无需先保存。

## 本轮验证（2026-10-31）

- 全量回归 488 项，487 通过，1 项原有打包路径测试失败。当前 Python 为 3.10.6，
  `shutil.which` 不遵守 Python 3.12+ 打包运行时使用的进程路径保护标志。
  该测试及 `tools/packaging/runtime_ffmpeg.py` 与检查点相同，本轮没有修改打包行为。
- 最后相关回归 147 项全部通过，包含新增 17 项桌面流程测试。网络和模型用固定资料及
  模拟提供商；这些结果不代表真实 AI、音质或下载验证。
- 原生 Windows Qt 窗口使用隔离工作区，读取上一轮四项真实任务/DeepSeek 草稿，
  实际检查编辑、切页、保存重开、直接进入配音页、无 TTS 稿件导出、卡片后台取消与重试。
  实际 FFmpeg 生成一段 ProRes MOV，ffprobe 测得 1920×1080、12 秒。
- 明暗主题、英文界面、1000×720 逻辑窗口及系统 DPR 1.5、附加缩放 DPR 1.875 已检查。
  预览保持比例、底部摘要截短，完整日志可展开。截图来自实际 Qt 窗口自身的 grab。
- Windows Computer Use 工具初始化失败（kernel assets 路径不存在）；以上原生操作通过
  应用自身控件 API、QtTest 和事件循环完成，不等同于人工鼠标/多显示器验收。
- 显式 LL2 刷新实际尝试失败：`ll.thespacedevs.com` DNS 解析失败（11001）。
  UI 保留失败原因和旧资料；没有把回放快照描述为新采集成功。本轮未调用文本 API，
  没有 Qwen 环境合成、账号下载或 Premiere 宿主验证，也未重打包 EXE。

本地证据在 `build/desktop-ui-20261031/`：`regression-final.log`、`targeted-pass.log`、
`native-1/acceptance.json`、`native-1_25/acceptance.json` 和 `screens/*.png`。
GUI 验收草稿带有明确的“界面验收”编辑标记；原始旧稿未改动。

Premiere 待检查：将实际 MOV 导入素材箱，确认透明通道、1920×1080、播放时长，
再与清单中的任务 ID、文件路径及顺序核对。当前不标记宿主验收通过。
