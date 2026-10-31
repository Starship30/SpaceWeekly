# SpaceWeekly 3.0.3 SearchFix

构建日期：2026-10-25。包含此前 MaterialFix / DemoFix，版本保持 3.0.3，原包与 Git 标签不替换。

- 接入官方 DeepSeek Search API（POST /v1/tools/search），复用现有搜索接口、超时、错误提示及缓存，不新增依赖。中文/英文航天检索共同查找名称，官方页面优先；不额外调用 Fetch。HTTP 429 与永久鉴权/余额错误分别处理，永久错误不反复重试。
- 官方原文或已核实词库支持的中文型号可以保留中文编号，例如 Xiguang-4 01 → 西光肆号01星；名称同步用于载荷短语。缺少证据时不猜译名，不补猜数量/版本。联网模式下重查旧纯 AI 译名缓存，失败保留原文。
- 任务名移除中英文括号内说明，覆盖 Word、Markdown、PR JSON、素材窗口和下载关键词。嵌套括号可处理，未闭合/全为括号内容时保留原文。原始采集记录、原始 JSON、完整载荷说明及旧视频保留。

使用：完整解压 SpaceWeekly_v3.0.3_Windows_x64_SearchFix.zip，保留 SpaceWeekly.exe 与 _internal/。在「设置 → 网络」选择 DeepSeek Search API，勾选「翻译航天术语前联网核实名称」；在发射任务设置中启用航天术语 AI/混合翻译。直连官方 DeepSeek 的现有 AI Key 可复用，搜索 Key 留空；其他 AI 服务需独立填写官方 DeepSeek Key。切换搜索服务会清空搜索 Key 输入，防止凭据串用。保持原始设置不自动迁移。

重新生成周报并启用 Premiere 内容包，再导入新 JSON、生成发射卡。旧 JSON 离线导入只清理显示名称中的括号，不会自动联网翻译型号。无需另外安装 Python、FFmpeg 或搜索依赖。

真实验证：已用现有官方配置请求 DeepSeek Search，返回研制单位来源，并通过真实搜索 → AI 翻译 → 载荷短语流程得到「西光肆号01星」。记录位于 build/search-translation/live-result.json；真实请求未输出或保存 Key。其间一次 AI 响应中断已按失败保留原文，后续完整复验成功。自动测试和最终包验收记录见 tools/PACKAGING.md 的 SearchFix 部分。

359 项测试通过，包含首次创建 Workspace 的默认词库合并、来源与编号校验、缓存重查、中英文设置保存、任务名清理及原始数据保留。主要代码涉及 translator/search.py、translator/translator.py、translator/dictionary.py、utils/text.py、ui/settings_dialog.py 及各导出/素材调用点；回归检查位于 tests/test_search_translation.py、app.py 与 tests/check_packaged.py。复合中文数字无法与源型号直接核对时仍保守保留型号，需要已核实词库条目支持。

原生及解压 EXE 综合验收通过，ZIP CRC 与解压文件 SHA-256 一致。包大小 149,383,764 字节（142.5 MiB），144 个文件；SHA-256：f0cdabc28eccdddfcd2973b9d7b9f942d7d85a7ab8d8e06eebb16399a8ac4b0d。既有包和素材保留，本次未提交 Git 或上传 Release。

DeepSeek 搜索是按次计费的独立接口，详见 [官方文档](https://api-docs.deepseek.com/zh-cn/guides/search/)。译名依据：[研制单位公告](https://www.xiopmspace.com/news/the-world-s-first-low-inclination-remote-sensing-constellation-precursor-satellite-a-was-launched-from-shaanxi/)。本次未在 Premiere 宿主复验、重跑整周平台下载或更新 GitHub Release。
