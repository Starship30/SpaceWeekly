# SpaceWeekly

## 项目简介

SpaceWeekly 是一个自动生成 AI 航天周报的软件。

主要流程：

RSS/API

↓

获取新闻列表

↓

下载网页

↓

解析正文

↓

AI 总结

↓

SQLite 保存

↓

Markdown 周报

↓

（未来支持 HTML / PDF）

---

# 开发环境

Python 3.10

Windows 11

VS Code

编码：

UTF-8

---

# 项目结构

SpaceWeekly/

main.py

requirements.txt

README.md

PROJECT.md

ROADMAP.md

models/

feeds/

downloader/

parsers/

database/

ai/

exporters/

utils/

---

# 数据流

RSS

↓

Feed

↓

List[News]

↓

Downloader

↓

HTML

↓

Parser

↓

Article

↓

AI

↓

SQLite

↓

Markdown

---

# 模块职责

## main.py

项目入口。

只负责调度。

禁止：

- requests
- BeautifulSoup
- feedparser
- SQLite
- AI

main.py 应保持尽量简洁。

---

## feeds/

负责：

RSS

API

↓

News

不能解析正文。

不能保存数据库。

返回：

List[News]

---

## downloader/

负责：

download(url)

↓

HTML

不能：

解析 HTML

不能：

AI

不能：

SQLite

---

## parsers/

负责：

HTML

↓

Article

Parser 不允许：

print()

数据库操作

AI

---

## database/

负责：

SQLite

只负责：

保存

读取

查询

禁止：

网页解析

AI

---

## ai/

负责：

调用大模型。

默认：

DeepSeek API

以后允许：

OpenAI

Gemini

Ollama

所有 AI 模块统一接口：

summarize(article)

---

## exporters/

负责：

Markdown

HTML

以后支持：

PDF

---

## models/

所有数据结构放这里。

目前：

News

Article

以后：

WeeklyReport

---

# 数据模型

## News

字段：

source

title

published

summary

url

News 不保存正文。

---

## Article

字段：

news

body

parser

Article 保存正文。

---

# 编码规范

遵循 PEP8。

全部使用：

type hints

dataclass

每个函数只负责一件事。

函数长度建议：

50 行以内。

main.py：

50 行以内。

优先保证可读性。

---

# 命名规范

类：

PascalCase

例如：

News

Article

函数：

snake_case

例如：

get_news()

download()

parse()

变量：

snake_case

例如：

news_list

article

body

---

# 开发原则

保持模块化。

不要重复代码。

不要修改已有接口。

新增功能优先新增模块。

尽量不要修改 main.py。

---

# 当前版本

SpaceWeekly v1.0 Alpha

已完成：

✅ RSS

✅ Downloader

✅ News

✅ Article

✅ JPL Parser

计划完成：

⬜ NASA Science Parser

⬜ SQLite

⬜ DeepSeek

⬜ Markdown 导出

---

# 禁止事项

不要：

修改目录结构

不要：

重命名 dataclass 字段

不要：

修改已有接口

不要：

引入 Flask

不要：

引入 FastAPI

不要：

引入 Django

不要：

引入异步

不要：

引入复杂设计模式

例如：

Factory

Singleton

Dependency Injection

保持简单。

---

# Codex 工作原则

如果需要修改架构：

先说明原因。

不要直接修改。

输出：

问题分析

↓

影响范围

↓

修改方案

↓

等待确认。

禁止直接重构整个项目。


# 设计理念

SpaceWeekly 采用"低耦合、高内聚"的模块化设计。

每个模块只负责一件事：

- Feed：获取新闻列表
- Downloader：下载网页
- Parser：解析正文
- AI：生成摘要
- Database：持久化存储
- Exporter：导出报告

模块之间通过统一的数据对象（News、Article）通信。

新增功能应优先通过新增模块实现，而不是修改已有模块。

整个项目以"简单、可维护、易扩展"为首要目标，而不是追求复杂的软件设计模式。

## Version Policy

每个版本只完成一个核心功能。

v1.0 Alpha

Parser

v1.1

SQLite

v1.2

Markdown Export

v1.3

DeepSeek

v1.4

GUI

禁止跨版本开发。

禁止提前实现未来版本功能。