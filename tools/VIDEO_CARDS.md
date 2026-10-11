# 离线视频素材工作流

桌面入口见 [使用步骤](VIDEO_CARDS_UI.md)。此处说明相同 Python/FFmpeg 实现的脚本入口、协议和验证。不会重新抓取新闻、调用 AI、修改数据库或连接 Premiere。

## 本机准备

在工作区根目录运行：

```powershell
python -m pip install -r tools/requirements-video.txt
ffmpeg -version
ffprobe -version
```

FFmpeg/ffprobe 需要加入 PATH。模板使用本机已有字体 `C:/Windows/Fonts/msyhbd.ttc` 和 `C:/Windows/Fonts/bankgothic-bold.ttf`。缺失会报错；请安装有使用授权的字体，或调整 `FONT`/`NUMBER_FONT`。没有下载或分发字体。不需要 AE、Creative Cloud、UXP 或 CEP 才能生成视频。

## 生成与重生成

启用 SpaceWeekly「Premiere 内容包」后正常生成周报，取得 `.premiere.json`。从同一份快照生成透明素材：

```powershell
Set-Location D:\Desktop\SpaceWeekly
python -m tools.video_cards plugins/premiere-uxp/example.premiere.json --seconds 16
python -m tools.video_cards output/你的快照.premiere.json --encoding prores
python -m tools.video_cards output/你的快照.premiere.json --order desc
python -m tools.video_cards output/你的快照.premiere.json --item-id "该条完整ID"
```

三条原示例新闻为虚构内容。默认 PNG MOV 无损 RGBA，每帧独立编码；可选 ProRes 4444。两者为 1920×1080、30 fps、无音频透明素材。默认最短 12 秒；`--seconds` 支持 6–600 秒，按整数帧量化。发射卡始终至少 12 秒；新闻摘要超宽时以 80px/秒滚动，并延长时长，让首尾停留。粗略阅读时间警告不是旁白长度。

文件写入 `output/video-cards/<job_id 的 SHA256 前 16 位>/`。从 3.0.1 起，新闻 MOV 位于 `news/`，文件名以 `.news.mov` 结尾；发射 MOV 位于 `launches/`，文件名以 `.launch.mov` 结尾。执行清单仍在批次根目录，`file` 使用相对路径。旧的单目录清单仍可读取和复用；旧 MOV 不移动、不删除。`--order asc`（默认）按时间正序，`--order desc` 按时间倒序。先新闻、后发射，各类按原始 `published` 时间排序；发射的该字段来自 LL2 `net`。复用现有 ISO/RFC 日期解析，比较实际时刻；无时区日期沿用项目约定视为 UTC。同刻稳定保序，缺失或无效日期在各类末尾，不从显示时间猜测原始日期。`manifest.json` 按所选排序列出稳定 ID、序号、类型、编码、最新 MOV、字号、帧数与时长，记录 `order_policy: video_type_then_published_asc/desc`。排序后只给选中项编号，新闻和发射各自从 01 连续计算，记录 `numbering_policy: independent_news_and_launch` 与 `selection_policy: checked_items_only`。未选条目为 `selected: false`、`ordinal: null`、`current: false`；旧文件保留但不属于当前选择。身份使用 `item.id`，不用列表位置。`--output` 可更换目录。

同一 job_id 内，只在显示内容、序号、有效时长、编码、代码或字体变化时重生成。切换排序或选择时，序号未变且有效的卡片继续复用，序号变化的卡片重生成；旧 MOV 保留。缺失或 SHA256 不匹配也重生成。`--item-id` 仅过滤要重新生成的卡片：有新执行清单时沿用上次选中范围计算序号，拒绝更新上次未选中的 ID；没有清单或只有旧清单时按整个快照编号。命令行不带 `--item-id` 时选择整个快照；桌面入口始终显式传入当前勾选 ID。未完成或失效条目为 `current: false`，界面可用相同选择再次生成。缓存损坏会拒绝使用，建议换新输出目录。每次全新导出有新 job_id，因此是独立批次。

## 新闻与发射字段

复用原 protocol_version `1.0` 和 `items`。普通新闻仍用标题和摘要进入画面；来源、原日期、URL 和分类保留在快照中。标题一行，50–26px；过长报错，不截断。摘要固定 34px，一行滚动，连续空白归一为空格；超过 32768px 要求缩短。动画为底部蓝色横条滑入、文字从线条遮罩后滑出、按需滚动、退出。

发射信息可作为同一 JSON 的可选根字段 `launches`。采集层从本次 LL2 响应直接抽取，导出关闭时不收集，不重复请求 LL2。中文模式且航天术语翻译开启时，在导出前翻译火箭、任务、轨道、载荷和地点，复用现有 Translator 和 Word 的缓存。未知词可依配置调用 AI，失败保留原文并记录字段原因；已是中文的值保留。导出/渲染器本身不调用 AI。

旧 UXP 仍读取 `items`，不增加其发射逻辑；内容包导出仍保持入选顺序。视频读取器合并 `items` 与 `launches`，视频窗口和生成器再应用所选时间排序；新闻、发射各自独立编号，例如新闻 01、新闻 02、发射 01、发射 02。保存编辑副本时合并到 `items` 并记录所选排序，不重复附加。增加发射项不会改变新闻序号或新闻内容指纹。

每个发射条目保留公共字段，并增加：

```json
{
  "video_type": "launch",
  "launch": {
    "orbit": "低地球轨道",
    "payload": "虚构卫星任务",
    "site": "虚构发射场 A",
    "time": "2026/10/10 20:49",
    "display_timezone": "Asia/Shanghai"
  }
}
```

发射 `title` 为火箭，`summary` 为任务。`published` 保留原 `net`，显示时间按配置时区转换，但不显示 UTC/GMT 偏移后缀；`display_timezone` 元数据仍保留。旧快照中的偏移后缀在编辑器、预览和 MOV 中隐藏，不移动已经转换好的时刻。ID 优先 LL2 ID，缺失用确定性 URL SHA256。载荷只取任务名称，不推断数量或质量；字段缺失允许空字符串，渲染「待确认」。桌面窗口可编辑或新增任务。英文旧快照需在联网内容阶段重新生成才能自动翻译，离线导入不调用 AI。

发射卡使用用户已确认的斜侧布局：白条和蓝面板沿相同斜率的路径进入对应位置，字段从线条后的遮罩滑出。前期 `output/ffmpeg-card-trial/` 参考素材保留，生产绘制已放入共同渲染器。

## 日期区间片头

```powershell
python -m tools.video_intro --date 2026-10-04 --end-date 2026-10-10 --title "我的航天周报" --subtitle "SPACEWEEKLY TEAM" --logo "D:\你的组徽.png"
python -m tools.video_intro --logo "D:\你的组徽.png" --logo-motion zoom
python -m tools.video_intro --logo "D:\你的动态组徽.gif"
```

省略日期默认含今天的最近 7 天；结束不能早于开始。片头为 7 秒、60 fps、1080p、H.264 MP4，无音频、深色不透明背景。名称、副标题、日期和本地组徽可自定义。桌面「制作片头 → 保存模板」保存一份名称/副标题/组徽路径/静态动效，日期不固定；旧模板默认淡入。生成器用现有编号命名和临时替换；取消/失败清理当前输出，不覆盖以前片头。

静态图片支持 PNG/JPG/WebP，`--logo-motion fade|zoom|rise` 分别为淡入（默认）、平滑缩放、上浮淡入。固定画布和浮点变换避免每帧整数尺寸变化造成的居中跳动。动态素材支持 GIF 和本地 MP4/MOV/M4V，保留自身动画，只叠加统一淡入/淡出，不叠加静态动效；**短视频播放一次后保持最后一帧，直到片头统一淡出**。GIF 仍循环，长素材最多使用前 7 秒，源音频忽略。GIF 按各帧时长播放并保留清除方式与透明区域；视频通过 FFmpeg 转为 60 fps、240×240 内居中的 RGBA 帧。保留原透明通道和宽高比，普通 MP4 原有背景不自动去除。界面缓存首帧作静态预览，实际动画在导出片头中查看。新版末帧保持示例和实际编码检查记录见 `output/video-intro-hold-trial/verification.json`；此前示例保留。

读取视频最多 420 帧，读取 GIF 最多片头时长，避免整段长视频进入内存。视频首帧预览读取超时为 10 秒，生成时读取超时为 30 秒；超时或解码失败报错，取消会结束解码进程，不改动源素材。建议使用短小、本地且由 FFmpeg 支持编码的 Logo 文件。

## 体积与 PR 验收

同一张 36 秒新闻条的本机编码对比：

| 编码 | 字节数 | 约 MB | 结果 |
| --- | ---: | ---: | --- |
| ProRes 4444 | 264,211,704 | 264.2 | 原编码保留可选 |
| PNG MOV RGBA | 55,253,075 | 55.3 | 减少约 79%，实际解码 RGBA 与原帧一致 |

比较记录在 `output/video-storage-trial/comparison.json`。PNG 为独立帧，未使用 Delta 帧；早期 qtrle 对比仅是废弃的诊断，不能当作可交付兼容文件。体积取决于画面复杂度，不能保证每条固定比例。片头示例约 146 KB。

只将清单中 `selected: true` 且 `current: true` 的最新 MOV 按条目顺序放到 PR 背景视频上方。文字已烘焙，修改需在 SpaceWeekly 重新生成。**保留旧 MOV** 以保护现有 PR 引用，不自动清理；更换编码或编号后请手动替换素材，再清理未引用旧版本。

用户已验证此前 ProRes 新闻条与发射 MOV 在 PR 可用。PNG MOV 和新片头尚未在 PR 26.3 实测。请检查：导入是否成功；上方/右侧透明区域；中文边缘；完整进出场；长摘要首尾及滚动；实际序列帧率下的播放和时长；修改单卡后的替换。PNG 无法使用或播放不顺时选 ProRes。片头直接放入时间线开头，按需要配音。

## 验证与失败处理

串行生成，每张检查格式、整数帧数、时长和解码 Alpha，PNG 额外检查所有帧独立；通过后才发布文件和清单。Ctrl+C/界面取消清理当前临时视频、日志及命名占位，失败立即停止。已完成项保留，再次执行可跳过。强制结束进程或断电不保证清理，也不承诺整批原子回滚。

```powershell
python -m unittest tests.test_video_cards tests.test_video_cards_dialog tests.test_video_assets tests.test_premiere tests.test_translator tests.test_date_ranges -v
```

87 项测试通过，覆盖 UTF-8、ID/顺序、摘要滚动、字段验证、缓存、取消/失败清理、导出关闭行为、日期区间、组徽居中、动态素材时序和模板保存。部分编码/宿主接口用模拟对象；实际 FFmpeg/Qt 验证分别记录在 `output/video-assets-smoke/verification.json`、`output/video-intro-example/verification.json`、`output/video-intro-logo-trial/verification.json`。本轮后者包括六个 7 秒/420 帧/60 fps 片头、GIF/透明 MOV/普通 MP4 的解码与循环，以及 Qt 离屏界面检查，均不是 Premiere 宿主测试。

当前不做背景素材选择、旁白、自动时间线编辑、自动拼接或渲染服务。现有 UXP 保留，CEP 不恢复，EXE 尚未重建。
