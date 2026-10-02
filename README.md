# douban-reader

豆瓣阅读电子书抓取器：Selenium 驱动浏览器、断点续抓、按章节输出 Markdown / JSON。

**这是给自己用的单站点工具，不是通用爬虫框架。** 目标是四件事：好读、可配置、可离线回归、可断点恢复。
设计上的取舍与理由见文末[「为什么这样分层」](#为什么这样分层)。

---

## 快速开始

```cmd
:: 1) 依赖（首次）
.venv\Scripts\python.exe -m pip install -r requirements.txt
::    开发依赖（pytest + ruff）
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt

:: 2) 放置 cookie（见下一节）

:: 3) 看生效配置与抓取计划，不启动浏览器
run.cmd --dry-run

:: 4) 正式抓取
run.cmd
```

三种等价入口，任选：

| 入口 | 说明 |
| --- | --- |
| `run.cmd` | 自动用 `.venv`，参数原样透传 |
| `python -m douban_reader` | 标准方式，在项目根目录执行 |
| `python main.py` | 兼容旧习惯的薄封装（内容只有 5 行） |

### 获取 / 更新 cookie

1. 浏览器登录豆瓣阅读，打开 `https://read.douban.com/reader/ebook/450696/`；
2. F12 → Network → 任选一个请求 → 复制 `Cookie` 请求头的值；
3. 整行粘贴覆盖 `cookie.txt`（纯文本 `k=v; k=v` 与 JSON 两种格式都支持）。

`cookie.txt` 已在 `.gitignore` 里，**不会被提交**；格式示例见 `cookie.example.txt`。
cookie 失效时程序会明确报出来并以退出码 `2` 结束，不会用空内容静默重试。

---

## 目录结构

```
yjsdxsh/
├─ pyproject.toml            依赖 + ruff/pytest 配置（唯一事实源）
├─ requirements*.txt         运行时 / 开发依赖
├─ config.toml               默认配置（可提交）
├─ config.local.toml         个人覆盖（可选，已 gitignore）
├─ cookie.txt                凭据（已 gitignore）
├─ cookie.example.txt        凭据格式示例
├─ run.cmd / main.py         入口
├─ douban_reader/            代码包
│   ├─ settings.py           配置装配（默认值 < toml < 命令行）
│   ├─ logging_setup.py      日志装配（控制台 INFO / 文件 DEBUG）
│   ├─ errors.py             领域异常 + 退出码
│   ├─ models.py             Page / Paragraph / Chapter / Block（纯数据）
│   ├─ naming.py             文件与目录命名规范
│   ├─ selectors.py          全部 CSS 选择器（唯一事实来源）
│   ├─ cookies.py            cookie 解析/去重（纯函数）
│   ├─ browser.py            Chrome 会话：启动、stealth、等待、元素操作
│   ├─ reader.py             导航：打开就绪、页码、翻页、智能跳页调度
│   ├─ search_jump.py        搜索面板跳页
│   ├─ parsing.py            JS 返回值 → Page（校验 + 容错）
│   ├─ chapters.py           Page[] → Chapter[] + Markdown（纯函数）
│   ├─ storage.py            pages 缓存 / 插图 / 章节落盘
│   ├─ scraper.py            策略编排
│   ├─ cli.py                命令行入口
│   └─ js/                   parse_page.js（DOM 解析）+ stealth.min.js（vendored）
├─ tests/                    离线测试 + 可选浏览器烟囱测试
├─ book/                     抓取产物（已 gitignore）
└─ logs/                     运行日志（已 gitignore）
```

---

## 输出格式

```
book/<书名>_<ebook_id>/
├─ pages/p<页码>.json   唯一事实源：每页一个文件，带 intentional 标记
├─ chapter/00N_<章节名>_p<起>-<止>.txt   正文，Markdown 片段
├─ json/00N_<章节名>_p<起>-<止>.json     同上的结构化版本（save_json 可关）
├─ json/_index.json     书籍级索引：页数统计 + 章节清单
└─ image/p<页码>_<序号>.<ext>            插图
```

* `chapter/`、`json/`、`image/` 都是**派生数据**，随时可删可重建；
  `pages/` 才是唯一事实源，**删了就必须重新联网抓**。
* 编码统一 UTF-8、换行统一 LF，JSON 缩进 2 空格且末尾带换行；
  JSON 内部路径统一使用 `/`（旧实现在 Windows 上会写入反斜杠）。
* 段落类型：`text` 正文、`heading` 小标题、`image` 插图（含 `legend` 图注）。

### 断点续抓与「顺手缓存」

| `intentional` | 含义 | 是否进入章节 |
| --- | --- | --- |
| `true` | 落在配置的 `[start, end]` 范围内，正式抓取 | 是 |
| `false` | 翻页路上顺手缓存的页（`opportunistic` 开关） | 否 |

重跑同一命令只补缺失的正式页，已完成的页不会重复请求。
`--rebuild-only` 可以只用 `pages/` 重建章节，**完全不联网、不启动浏览器**。

### 分页重复段（`dedupe_page_overlap`）

阅读器分页时会把上一页的末段重新渲染到下一页开头。实测 200 页里有 **88 处**，
旧输出因此出现成对重复段落。默认开启的清理规则很窄：**每页第一个可用块若与上一页末块同文，
就判为分页重叠并丢弃** —— 只在页首生效，不会误伤正文中刻意的重复语句。

想恢复旧行为（保留重复段）：`--no-dedupe-overlap`。

---

## 配置

优先级：`config.toml` < `config.local.toml` < 命令行参数。
**相对路径一律相对项目根目录解析**，因此从任何工作目录运行结果一致。
**写错键名会直接报错并列出可用键**，不会静默失效。

| 分组 | 键 | 默认 | 说明 |
| --- | --- | --- | --- |
| 目标 | `ebook_id` | `"450696"` | 阅读器地址里的电子书 ID |
| | `book_title` | 空 | 留空自动从页面标题解析 |
| | `start_page` / `end_page` | `1` / `200` | 正式抓取范围（含两端） |
| 路径 | `book_root` | `"book"` | 输出父目录 |
| | `out_dir` | 空 | 显式指定完整输出目录（优先） |
| | `cookie_file` / `cookie_string` | `"cookie.txt"` / 空 | 凭据来源 |
| | `stealth_js` / `log_dir` | 空 / `"logs"` | stealth 脚本、日志目录 |
| 浏览器 | `headless` | `false` | 无头模式（调试 DOM 时建议关） |
| | `page_load_timeout` / `element_timeout` | `30.0` / `10.0` | 各类等待超时（秒） |
| | `user_data_dir` / `chrome_driver_path` | 空 | 一般不需要填 |
| | `block_resources` / `blocked_urls` | `false` / `[]` | CDP 资源拦截（见文末教训） |
| 策略 | `opportunistic` | `true` | 顺手缓存沿途页 |
| | `max_direct` | `15` | 距离 ≤ 此值逐页翻，否则搜索跳页 |
| | `use_search_jump` | `true` | 允许搜索跳页 |
| | `sleep_min` / `sleep_max` | `1.5` / `3.5` | 翻页随机等待（秒），别调太小 |
| | `page_settle` / `open_settle` | `0.3` / `2.0` | 到达页 / 首屏就绪后的稳定等待 |
| | `open_timeout` / `nav_timeout` / `search_timeout` | `30` / `20` / `12` | 三段超时 |
| | `parse_retries` / `capture_retries` | `4` / `3` | 解析、抓取重试次数 |
| | `image_timeout` / `max_turns` | `30.0` / `500` | 插图下载超时、翻页步数上限 |
| 输出 | `save_json` / `dedupe_page_overlap` | `true` / `true` | 输出 JSON、去分页重复段 |
| 日志 | `log_level` | `"INFO"` | 控制台级别（文件恒为 DEBUG） |

### 命令行

```
--ebook-id --book-title --start --end --book-root --out-dir
--cookie-file --cookie-string
--headless/--no-headless          --opportunistic/--no-opportunistic
--use-search-jump/--no-search-jump  --dedupe-overlap/--no-dedupe-overlap
--save-json/--no-save-json        --max-direct --min-delay --max-delay
--log-level --config --no-local-config
--rebuild-only   只用 pages/ 重建章节（离线）
--dry-run        只打印生效配置与计划
```

### 退出码

| 码 | 含义 | 处理建议 |
| --- | --- | --- |
| `0` | 成功 | — |
| `1` | 未预期异常 | 看 `logs/` 里的堆栈 |
| `2` | cookie 失效 | 重新登录并更新 `cookie.txt` |
| `3` | 部分页未抓到 | 重跑同一命令即可续抓 |
| `4` | 翻页/跳页中断 | `--headless false` 观察页面，或 `--no-search-jump` |
| `5` | 配置/凭据问题 | 按报错提示修正配置或凭据文件 |

---

## 常见问题

| 现象 | 原因与处理 |
| --- | --- |
| 启动即报「Cookie 已失效」 | 重新登录后更新 `cookie.txt`；报错里会带上当前 URL 与页面开头文本 |
| 提示「触发腾讯滑块」 | 被风控拦了。停一会儿，用有头模式手动过一次验证，并调大 `sleep_min/max` |
| 「找不到后翻按钮」 | 页面结构变了：改 `selectors.py`（唯一事实源） |
| 搜索跳页失败 | 自动回退逐页翻，属于可接受的降级；连续出现说明搜索面板结构变了 |
| 分页失效 / 插图不显示 | 检查是否开了 `block_resources`：**不要拦 CSS**，见文末教训 |
| 插图下载失败 | 会把远端地址写进正文并标 `download_failed`；重跑会自动补下 |
| 章节少了 | 该页没抓到（退出码 `3`），看日志里的「未完成页」并重跑 |
| 想重新生成全部章节 | `run.cmd --rebuild-only`（不联网） |

日志在 `logs/run-<时间戳>.log`，文件里始终是 DEBUG 级别；控制台级别由 `log_level` 控制。

---

## 开发

```cmd
.venv\Scripts\python.exe -m pytest            :: 离线测试（默认，不发网络、不启动浏览器）
.venv\Scripts\python.exe -m pytest -m golden  :: 只跑金标准回归
.venv\Scripts\python.exe -m pytest -m browser :: 需要本机 Chrome 的烟囱测试
.venv\Scripts\python.exe -m ruff check .      :: 静态检查
.venv\Scripts\python.exe -m ruff format .     :: 格式化
```

测试分三类，边界很清楚：

* **纯逻辑测试**：`cookies` / `chapters` / `parsing` / `storage` / `settings` / `cli` ——
  这几块都不依赖浏览器，所以能测、也就必须测；
* **金标准回归**（`golden`）：直接吃 `book/<书名>_<id>/pages/*.json` 这份真实抓取快照，
  对照 `tests/fixtures/legacy_output_manifest.json`（**重构前实现的输出指纹**，
  只存 sha256 与行数、不含书稿正文），证明重构前后逐章等价，且去重只删掉 88 处分页重复段。
  换机器没有快照时会自动跳过；
* **浏览器烟囱**（`browser`）：用 `tests/fixtures/reader_page.html` 这个仿阅读器 DOM 的本地页面
  驱动真实 Chrome，验证 stealth 是否生效、选择器能否命中、翻页等待是否可靠、JS 解析是否正确。
  默认不跑（Chrome 不可用时自动跳过）。

### 改动约定

* **选择器只写在 `selectors.py`**，JS 通过参数接收，不要在 `parse_page.js` 里新增选择器字面量
  （`tests/test_parsing.py` 会检查）。
* **`parse_page.js` 必须以顶层 `return` 结束**，不要包 IIFE（`tests/test_assets.py` 会检查）。
* 新增配置项：在 `Settings` 里加字段 → 写进 `config.toml` → 需要时在 `cli.py` 暴露参数。
  未知键会报错，所以三处必须同步。
* 落盘一律走 `storage.atomic_write_text/json`（临时文件 + `os.replace`），不要直接 `open(..., "w")`。

---

## 为什么这样分层

按**领域职责纵切**，而不是按 `config / browser_factory / scraper / parser / storage`
这类技术层横切。理由很具体：

1. **最不能动的部分正好会被横切拆散。** `search_jump.py` 里那段逻辑同时是 DOM 知识、
   面板开合时序、输入事件模拟和落地校验，它硬扛过风控调出来的；按层切开后要跳三个文件才能读全。
2. **通用化在这里是纯负担。** 只有一个站点、只有一种 Chrome、只写一种 `pN.json`，
   工厂/抽象基类/插件点换不来任何东西。
3. **真正的复杂度在别处**：反爬与等待时序、断点续抓状态、DOM→结构化数据的容错。
   分层必须围着这三件事建，`models / chapters / storage` 就是它们的家。
4. **能离线测的逻辑必须离线测。** 现在 `models / naming / cookies / parsing / chapters / storage`
   都不依赖浏览器，因此"重构是否等价"可以靠 diff 回答，而不是靠肉眼。

### 两个真实踩过的坑（都留了回归测试）

* **不要用 CDP 拦 CSS。** 旧实现曾拦 `*.css`，导致阅读器分页失效、插图不显示；
  拦字体/统计脚本还会让搜索跳页异常。所以 `block_resources` 默认关闭，
  确需开启时用 `blocked_urls` **逐个**加白名单，不要一刀切。
* **注入脚本不能用 IIFE 包裹。** `(function(){...})(...)` 的返回值会被 chromedriver 丢弃，
  Python 侧只会拿到 `None`（表现为"解析当前页失败：返回类型异常 NoneType"）。
  必须顶层 `return`。

---

## 已知限制

* 依赖站点当前 DOM；改版时改 `selectors.py`，必要时更新 `search_jump.py`。
* 只支持单本电子书一次运行；批量请在外层循环调用 CLI（记得控制频率）。
* 抓取频率由 `sleep_min/sleep_max` 控制，**请勿调得过小**；本工具面向自己已购买的内容做本地留存。
