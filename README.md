# douban-reader

豆瓣阅读电子书抓取器：Selenium 驱动浏览器、断点续抓、按章节输出 Markdown / JSON。

> **免责声明**
>
> 本项目**仅用于学习与技术研究**，禁止用于侵害任何人的版权。
> 请只抓取你自己已购买或有权访问的内容，并仅作个人备份，不要传播抓取结果；
> 请遵守目标站点的服务条款，不要把 `sleep_min`/`sleep_max` 调得过小。
>
> 仓库里**不含**任何书稿正文与个人凭据：`book/`（正文与插图）、`cookie.txt`（登录态）、
> `logs/`、`config.local.toml`、`chrome-profile/` 都在 `.gitignore` 中排除，
> 请勿用 `git add -f` 强行提交这些路径。

---

## 怎么用

### 方式一：图形界面（不想记命令就用这个）

**双击 `run_gui.cmd`** 即可，无需装任何额外依赖（界面是 Python 自带的 tkinter 写的）。

界面上要填的就四个东西：**电子书 ID**（阅读器地址 `read.douban.com/reader/ebook/【这串数字】/` 里那串数字）、**起始页**、**结束页**，以及 cookie 文件（默认 `cookie.txt`，点「怎么填?」有图文步骤）。

| 按钮 | 作用 |
| --- | --- |
| 检查计划 | **不联网、不开浏览器**，告诉你这本书已经缓存了多少页、还差哪些页 |
| 开始抓取 | 正式抓取；打开浏览器，逐页抓取并随时在下方日志区显示进度 |
| 只重建章节（离线） | 不联网，只用本地缓存重新生成 `chapter/` 与 `json/` |
| 停止 | 当前页抓完就停；已抓内容照常保存，再点一次「开始抓取」即可续抓 |
| 打开输出目录 / 打开日志 | 在资源管理器里打开产物目录、最新日志 |
| 保存为默认配置 | 把当前表单写进 `config.local.toml`，命令行 `run.cmd` 也会用它 |

界面底部是**实时日志**，抓取过程中能看到「[计划] …」「[正式] p7 …」「[停止] …」这类信息，和命令行完全一致。

> 双击没反应？先用带控制台的方式启动看报错：`.venv\Scripts\python.exe -m douban_reader.gui`，
> 或者看 `logs\gui-error-*.log`（用 `pythonw` 启动时没有控制台，错误会写在这里）。

### 方式二：命令行

```cmd
:: 1) 依赖（首次；已装好可跳过）
.venv\Scripts\python.exe -m pip install -r requirements.txt

:: 2) 放置 cookie（见下一节）

:: 3) 看生效配置与抓取计划，不启动浏览器
run.cmd --dry-run

:: 4) 正式抓取
run.cmd
```

四种等价入口，任选：

| 入口 | 说明 |
| --- | --- |
| `run_gui.cmd` | 图形界面（双击即可） |
| `run.cmd` | 命令行，自动用 `.venv`，参数原样透传 |
| `python -m douban_reader` | 标准方式，在项目根目录执行 |
| `python main.py` | 兼容旧习惯的薄封装（内容只有 5 行） |

### 获取 / 更新 cookie

1. 浏览器登录豆瓣阅读，打开这本电子书的阅读页（`https://read.douban.com/reader/ebook/450696/`）；
2. F12 → Network 面板 → 刷新 → 点任意一条发往 `read.douban.com` 的请求 → Headers → Request Headers；
3. 复制 `Cookie` 那一行的**值**（不要带 `Cookie:` 前缀），整行粘贴覆盖 `cookie.txt`。

`cookie.txt` 已在 `.gitignore` 里，**不会被提交**；格式示例见 `cookie.example.txt`。
cookie 失效时程序会明确报出来并以退出码 `2` 结束，不会用空内容静默重试。

---

## 目录结构

```
douban_reader/                 项目根目录（外层）与内层同名包是标准 flat layout
├─ pyproject.toml            依赖 + ruff/pytest 配置（唯一事实源）
├─ requirements*.txt         运行时 / 开发依赖
├─ config.toml               默认配置（可提交）
├─ config.local.toml         个人覆盖（可选，已 gitignore）
├─ cookie.txt                凭据（已 gitignore）
├─ cookie.example.txt        凭据格式示例
├─ run.cmd / run_gui.cmd     命令行 / 图形界面入口
├─ main.py                   兼容入口（等价于 python -m douban_reader）
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
│   ├─ gui/                  图形界面（tkinter，标准库）
│   │   ├─ controller.py     表单↔配置、离线计划、后台任务、事件队列（无 tkinter，可单测）
│   │   └─ app.py            界面控件与线程间通信
│   └─ js/                   parse_page.js（DOM 解析）+ stealth.min.js（vendored）
├─ tests/                    离线测试 + 可选浏览器/GUI 测试
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

### 请求量控制（尽量少打扰站点）

抓取器按"计划先行、每页最多加载一次"来设计，具体机制：

| 机制 | 作用 |
| --- | --- |
| 计划先行 | 已正式抓过的页不再请求；范围内全抓完时**连浏览器都不启动**，直接离线聚合 |
| 走位只朝待抓页 | 阅读器恢复在第 10 页、要抓 1-7 时，只倒着走一趟；不会"倒着抓一遍再正向空翻一遍" |
| 空翻换跳页 | 中间页都已抓过（或关掉顺手缓存）时，用搜索跳页代替逐页翻 |
| 翻页后确认到位 | 阅读器页码会抖动/回跳（实测出现过"实得 p17、期望 p19"）：只有**页码稳定在目标页**才算翻过去，否则再点一次；最多点 3 次，不无限重试 |
| cookie 先注入再导航 | 首屏只加载一次，不再是"加载 → 注入 cookie → 刷新"两次 |
| 仅缓存页不下载插图 | 顺手缓存的页只存正文，不产生任何 HTTP 请求 |
| 插图已存在则跳过 | 续抓/重跑不会重复下载同一张图 |
| 失败快速止损 | 连续 3 页失败就停下本轮（退出码 3），重跑即续抓，不会一直空转 |

因此同样范围的第二次运行请求数恒为 **0**。另外刻意**没有**做的事：不换抓取引擎、
不并发轰炸站点、不调小随机等待 —— 少发请求靠的是"不重复"，不是"更快更密"。

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
| 策略 | `opportunistic` | `true` | 顺手缓存沿途页（只存正文，不下载插图，因此不产生额外请求） |
| | `max_direct` | `15` | 距离 ≤ 此值逐页翻，否则搜索跳页 |
| | `use_search_jump` | `true` | 允许搜索跳页 |
| | `sleep_min` / `sleep_max` | `1.5` / `3.5` | 翻页随机等待（秒），别调太小 |
| | `page_settle` / `open_settle` | `0.3` / `2.0` | 到达页 / 首屏就绪后的稳定等待 |
| | `open_timeout` / `nav_timeout` | `30` / `20` | 打开阅读器 / 单次翻页的超时（秒） |
| | `turn_timeout` / `page_ready_timeout` | `5` / `10` | 点一次翻页后等页码到位 / 等页码稳定到目标页：页码抖动、点击被吞时靠这两个值兜住 |
| | `search_timeout` | `12` | 搜索跳页单步超时（秒） |
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
| `4` | 翻页/跳页中断 | 用 `--no-headless`（默认可见）观察页面，或 `--no-search-jump` |
| `5` | 配置/凭据问题 | 按报错提示修正配置或凭据文件 |

---

## 常见问题

| 现象 | 原因与处理 |
| --- | --- |
| 启动即报「Cookie 已失效」 | 重新登录后更新 `cookie.txt`；报错里会带上当前 URL 与页面开头文本 |
| 提示「触发腾讯滑块」 | 被风控拦了。停一会儿，用有头模式手动过一次验证，并调大 `sleep_min/max` |
| 「找不到后翻按钮」 | 页面结构变了：改 `selectors.py`（唯一事实源） |
| 搜索跳页失败 | 自动回退逐页翻，属于可接受的降级；连续出现说明搜索面板结构变了 |
| 日志里出现「期望 p14 实得 p13」 | 阅读器页码抖动/回跳。工具会等页码稳定、必要时重点一次（最多 3 次）；若成片出现，说明站点在限速，把 `turn_timeout` 与 `page_ready_timeout` 调大、并把 `sleep_min/sleep_max` 调大后重跑 |
| 分页失效 / 插图不显示 | 检查是否开了 `block_resources`：**不要拦 CSS**，见文末教训 |
| 插图下载失败 | 会把远端地址写进正文并标 `download_failed`；重跑会自动补下 |
| 章节少了 | 该页没抓到（退出码 `3`），看日志里的「未完成页」并重跑 |
| 想重新生成全部章节 | `run.cmd --rebuild-only`（不联网） |

日志在 `logs/<动作>-<时间戳>.log`（命令行是 `run-*.log`，界面是 `scrape-*.log` / `rebuild-*.log`），
文件里始终是 DEBUG 级别；控制台级别由 `log_level` 控制。

---

## 开发

```cmd
.venv\Scripts\python.exe -m pytest            :: 离线测试（默认，不发网络、不启动浏览器）
.venv\Scripts\python.exe -m pytest -m golden  :: 只跑金标准回归
.venv\Scripts\python.exe -m pytest -m browser :: 需要本机 Chrome 的烟囱测试
.venv\Scripts\python.exe -m pytest -m gui     :: 需要图形环境的界面测试（隐藏窗口，不打扰你）
.venv\Scripts\python.exe -m ruff check .      :: 静态检查
.venv\Scripts\python.exe -m ruff format .     :: 格式化
```

测试分四类，边界很清楚：

* **纯逻辑测试**：`cookies` / `chapters` / `parsing` / `storage` / `settings` / `cli` / `gui.controller` ——
  这几块都不依赖浏览器或图形环境，所以能测、也就必须测。
  GUI 的界面逻辑（表单↔配置、离线计划、后台任务、日志与进度事件）全在 `gui/controller.py`，
  `gui/app.py` 只负责摆控件，这样"能不能正常用"不靠手点来验证；
* **金标准回归**（`golden`）：直接吃 `book/<书名>_<id>/pages/*.json` 这份真实抓取快照，
  对照 `tests/fixtures/legacy_output_manifest.json`（**重构前实现的输出指纹**，
  只存 sha256 与行数、不含书稿正文），证明重构前后逐章等价，且去重只删掉 88 处分页重复段。
  换机器没有快照时会自动跳过；
* **浏览器烟囱**（`browser`）：用 `tests/fixtures/reader_page.html` 这个仿阅读器 DOM 的本地页面
  驱动真实 Chrome，验证 stealth 是否生效、选择器能否命中、翻页等待是否可靠、JS 解析是否正确，
  还有一个本地 HTTP 服务用来证明"cookie 注入后目标页只加载一次"。默认不跑（Chrome 不可用时自动跳过）；
* **界面烟囱**（`gui`）：真实创建 Tk 窗口但**隐藏**，验证界面能搭起来、按钮状态、进度与结束处理不炸。
  默认不跑（无图形环境时自动跳过）。

### 改动约定

* **选择器只写在 `selectors.py`**，JS 通过参数接收，不要在 `parse_page.js` 里新增选择器字面量
  （`tests/test_parsing.py` 会检查）。
* **`parse_page.js` 必须以顶层 `return` 结束**，不要包 IIFE（`tests/test_assets.py` 会检查）。
* **`.cmd` / `.bat` 必须"纯 ASCII + CRLF + 无 BOM + 不用 `chcp`"四件套**：
  cmd.exe 是按**字节偏移**解析批处理的，多字节字符（中文注释）、LF 换行、
  以及读到一半才 `chcp` 换代码页，都会让偏移错位，于是它把注释的碎片当命令执行
  （症状：先冒出两行"不是内部或外部命令"，然后才正常输出），严重时直接假死。
  中文用法放在 `run.cmd --help` 与本文档里，**不要写进 `.cmd`**。
  仓库的 `.gitattributes` 保证 checkout 出来是 CRLF，但用编辑器改完要自行确认
  —— `tests/test_assets.py` 会逐条检查这四项规则。
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
* **批处理脚本必须纯 ASCII + CRLF。** cmd.exe 按字节偏移解析 `.cmd`：LF 换行、
  中文注释、读到一半 `chcp` 换代码页，三者任一都会让偏移错位 —— 轻则把注释碎片当命令执行
  （"不是内部或外部命令"），重则整段假死不返回。报错信息与真实原因毫无关系，极难排查，
  所以 `.cmd` 里只留英文注释，中文用法交给 `run.cmd --help`。

---

## 已知限制

* 依赖站点当前 DOM；改版时改 `selectors.py`，必要时更新 `search_jump.py`。
* 只支持单本电子书一次运行；批量请在外层循环调用 CLI（记得控制频率）。
* 抓取频率由 `sleep_min/sleep_max` 控制，**请勿调得过小**；本工具面向自己已购买的内容做本地留存。
