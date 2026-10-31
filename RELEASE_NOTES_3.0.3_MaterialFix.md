# SpaceWeekly 3.0.3 MaterialFix

构建日期：2026-10-24。独立累积修复包，包含此前 DemoFix 的下载重试与回收状态隐藏修复，版本仍为 3.0.3，不替换旧包或 Git 标签。

修正 Premiere 内容包将 mission.name 同时用作任务名称与任务载荷的问题。任务名仍用于搜索；载荷资料优先使用 LL2 明确载荷记录及说明，缺失时使用任务说明。启用航天术语 AI/混合翻译时，调用现有 AI 服务提取「27颗星链卫星」「6颗遥感卫星」「MMX探测器」这样的短语，写入 launch.payload。每项有效资料增加载荷摘要请求，相同输入在当前生成任务中缓存；沿用现有取消、校验重试和失败处理。数量与版本须有来源，任务编号不当作数量，明确型号保留；本周 LL2 未写明 V2 Mini，不自动补猜。关闭翻译、词典模式或 AI 失败时保留接口说明，可能为英文。缺少说明则留空/待确认。

Word/Markdown 已有任务说明保持全文；渲染区域过长显示省略号，编辑快照不截断字段。旧 JSON 离线导入不联网补全。视频搜索与一键下载 Demo 的优先级改为：标题无「中字 / 中文字幕 / 中英字幕 / 中英双字」标记 → 优先 UP 主 → 所选时间或相关度。有字幕标记的视频仍作后备，不影响手动选择或粘贴链接。

完整解压 dist/v3.0.3-material-fix/SpaceWeekly_v3.0.3_Windows_x64_MaterialFix.zip，运行 SpaceWeekly/SpaceWeekly.exe，保留同级 _internal 文件夹。无需新增依赖，FFmpeg/ffprobe 和 yt-dlp 仍内置。重新生成周报并启用 Premiere 内容包，再加载新 JSON、重新生成发射卡；也可使用 build/ll2-payload/本周载荷短语修正.premiere.json，本次只替换 7 项载荷，原 JSON 未修改。

验证：346 项测试全部通过（build/ll2-payload/material-full-tests-sequential.log）。正式 LL2 一次批量详细查询返回本周 7 条任务，均有说明、均无独立载荷列表；使用当前配置的 AI 服务实际总结 7 条，收紧型号保留校验后复验 Xiguang-4，全部通过最终校验。7 张预览及两条完整 12 秒 PNG MOV 解码比对通过。原生 EXE、中文/空格路径解压 EXE、内置字体/FFmpeg、ZIP CRC 和解压文件 SHA-256 验收通过。期间并行构建/编码曾造成宿主内存不足，停止并行后顺序重跑通过；不以该失败运行作为功能通过证据。记录见 build/ll2-payload/summary-verification.json 与 material-package-result.json。

包大小 149,378,467 字节（142.5 MiB），解压 335,979,761 字节（320.4 MiB），144 个文件。SHA-256：de960d88524c04165d0ea04a65ecc64d2f8d41f03f7d32b3184395bfa2626692。未提交本次修复或上传 GitHub Release，旧包和旧素材保留。

未在 Premiere 宿主中复验，也未用真实账号重跑整周下载。字幕偏好只识别标题，不能保证画面没有字幕；LL2 资料完整性与平台访问限制仍由源站决定。
