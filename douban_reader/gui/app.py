"""tkinter 图形界面（标准库，**零新增依赖**）。

设计原则
--------
* 界面只负责"收集参数 + 显示进度/日志 + 触发动作"，业务逻辑全部复用命令行那一套
  （:class:`~douban_reader.settings.Settings` / :class:`~douban_reader.scraper.Scraper`），
  所以 GUI 与 CLI 的行为永远一致；
* 抓取跑在后台线程，进度与日志经事件队列回到主线程 —— tkinter 控件只能在主线程碰；
* 表单可以一键写进 ``config.local.toml``，于是 GUI 与命令行共享同一份配置。

启动方式：``run_gui.cmd``（双击即可）或 ``python -m douban_reader.gui``。
"""

from __future__ import annotations

import logging
import os
import queue
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from ..errors import ConfigError
from ..settings import LOCAL_CONFIG_FILE, LOG_LEVELS, Settings, load_settings
from .controller import (
    KIND_LABELS,
    FormValues,
    PlanSummary,
    TaskEvent,
    TaskRunner,
    build_plan,
    build_settings,
    latest_log_file,
)

logger = logging.getLogger(__name__)

APP_TITLE = "豆瓣阅读抓取器"
LOG_LEVEL_CHOICES = list(LOG_LEVELS)
MAX_LOG_LINES = 4000
POLL_INTERVAL_MS = 100

COOKIE_HELP = """\
怎么拿到 cookie（只需三步）：

1. 浏览器登录豆瓣阅读，打开这本电子书的阅读页；
2. F12 → Network（网络）面板 → 刷新页面 → 点任意一条发往 read.douban.com 的请求；
3. Headers → Request Headers → 找到 Cookie，复制它的**值**（不要带 "Cookie:" 前缀），
   整行粘贴覆盖项目里的 cookie.txt。

要点：
· 关键字段是 ark_session（登录态），只有 bid/_ga 之类的统计 cookie 不够；
· 不要从 Application → Cookies 的表格里复制（那会串位）；
· 这个输入框留空 = 不用 cookie 文件（配合 Chrome 配置目录复用登录态时才这么做）；
· cookie 过期时程序会明确报"Cookie 已失效"，重新复制覆盖即可。
"""


def enable_dpi_awareness() -> None:
    """Windows 高分屏下让界面不发虚；失败就算了（不影响功能）。"""
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:  # noqa: BLE001 - 非 Windows 或老系统
        pass


def open_in_file_manager(path: Path) -> bool:
    """在资源管理器/访达里打开路径（Windows 用 startfile，其它平台尽力而为）。"""
    try:
        if hasattr(os, "startfile"):
            os.startfile(str(path))  # type: ignore[attr-defined]  # noqa: S606
            return True
        subprocess.Popen(["xdg-open", str(path)])  # noqa: S603,S607
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("打开 %s 失败：%s", path, exc)
        return False


class GuiApp:
    """主窗口。"""

    def __init__(self, root: tk.Tk, settings: Settings, *, config_error: str | None = None) -> None:
        self.root = root
        self.base_settings = settings
        self.runner = TaskRunner()
        self.plan: PlanSummary | None = None
        self.last_log_file: Path | None = None

        self.vars: dict[str, tk.Variable] = {
            "ebook_id": tk.StringVar(),
            "start_page": tk.StringVar(),
            "end_page": tk.StringVar(),
            "book_root": tk.StringVar(),
            "cookie_file": tk.StringVar(),
            "log_level": tk.StringVar(),
            "headless": tk.BooleanVar(),
            "opportunistic": tk.BooleanVar(),
            "use_search_jump": tk.BooleanVar(),
            "dedupe_overlap": tk.BooleanVar(),
            "save_json": tk.BooleanVar(),
            "open_when_done": tk.BooleanVar(value=True),
        }
        self.status_var = tk.StringVar(value="就绪")
        self.detail_var = tk.StringVar(value="")

        self._build_widgets()
        self._load_form(FormValues.from_settings(settings))
        if config_error:
            self._append_log(f"配置文件有问题：{config_error}", "ERROR")
            self._append_log(
                "已用内置默认值启动，可在界面里改好参数后点「保存为默认配置」。", "WARNING"
            )
        self.refresh_plan(log_it=False)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(POLL_INTERVAL_MS, self._drain_events)

    # ------------------------------------------------------------ 构建界面

    def _build_widgets(self) -> None:
        self.root.title(APP_TITLE)
        self.root.minsize(880, 620)
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(3, weight=1)

        self._build_target_frame()
        self._build_option_frame()
        self._build_button_frame()
        self._build_status_frame()
        self._build_log_frame()

    def _build_target_frame(self) -> None:
        frame = ttk.LabelFrame(self.root, text=" 抓取目标 ", padding=(10, 6))
        frame.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 4))
        frame.columnconfigure(1, weight=1)
        frame.columnconfigure(4, weight=1)

        ttk.Label(frame, text="电子书 ID").grid(row=0, column=0, sticky="w")
        entry = ttk.Entry(frame, textvariable=self.vars["ebook_id"], width=16)
        entry.grid(row=0, column=1, sticky="w", padx=(6, 18))
        ttk.Label(frame, text="起始页").grid(row=0, column=2, sticky="w")
        ttk.Entry(frame, textvariable=self.vars["start_page"], width=8).grid(
            row=0, column=3, sticky="w", padx=(6, 18)
        )
        ttk.Label(frame, text="结束页").grid(row=0, column=4, sticky="w")
        ttk.Entry(frame, textvariable=self.vars["end_page"], width=8).grid(
            row=0, column=5, sticky="w", padx=6
        )

        ttk.Label(frame, text="输出父目录").grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(frame, textvariable=self.vars["book_root"]).grid(
            row=1, column=1, columnspan=3, sticky="ew", padx=(6, 18), pady=(6, 0)
        )
        ttk.Label(frame, text="cookie 文件").grid(row=1, column=4, sticky="w", pady=(6, 0))
        cookie_row = ttk.Frame(frame)
        cookie_row.grid(row=1, column=5, sticky="ew", pady=(6, 0))
        ttk.Entry(cookie_row, textvariable=self.vars["cookie_file"], width=16).pack(
            side="left", fill="x", expand=True
        )
        ttk.Button(cookie_row, text="怎么填?", width=8, command=self.show_cookie_help).pack(
            side="left", padx=(6, 0)
        )
        self.entry_ebook_id = entry

    def _build_option_frame(self) -> None:
        frame = ttk.LabelFrame(self.root, text=" 选项 ", padding=(10, 6))
        frame.grid(row=1, column=0, sticky="ew", padx=10, pady=4)

        options = [
            ("opportunistic", "顺手缓存沿途页"),
            ("headless", "无头模式（后台运行，不显示浏览器）"),
            ("use_search_jump", "允许搜索跳页（快，依赖站点搜索框）"),
            ("dedupe_overlap", "去掉分页重复段"),
            ("save_json", "同时输出 json/"),
            ("open_when_done", "完成后打开输出目录"),
        ]
        for index, (key, label) in enumerate(options):
            ttk.Checkbutton(frame, text=label, variable=self.vars[key]).grid(
                row=index // 3, column=index % 3, sticky="w", padx=(0, 16), pady=2
            )

        ttk.Label(frame, text="日志级别").grid(row=2, column=0, sticky="w", pady=(6, 0))
        ttk.Combobox(
            frame,
            textvariable=self.vars["log_level"],
            values=LOG_LEVEL_CHOICES,
            state="readonly",
            width=10,
        ).grid(row=2, column=1, sticky="w", pady=(6, 0))

    def _build_button_frame(self) -> None:
        frame = ttk.Frame(self.root)
        frame.grid(row=2, column=0, sticky="ew", padx=10, pady=4)

        self.btn_plan = ttk.Button(frame, text="检查计划", command=self.refresh_plan)
        self.btn_plan.pack(side="left")
        self.btn_start = ttk.Button(
            frame, text="开始抓取", command=lambda: self.start_task("scrape")
        )
        self.btn_start.pack(side="left", padx=(6, 0))
        self.btn_rebuild = ttk.Button(
            frame, text="只重建章节（离线）", command=lambda: self.start_task("rebuild")
        )
        self.btn_rebuild.pack(side="left", padx=(6, 0))
        self.btn_stop = ttk.Button(frame, text="停止", command=self.stop_task, state="disabled")
        self.btn_stop.pack(side="left", padx=(6, 0))

        ttk.Separator(frame, orient="vertical").pack(side="left", fill="y", padx=10)
        ttk.Button(frame, text="打开输出目录", command=self.open_output_dir).pack(side="left")
        ttk.Button(frame, text="打开日志", command=self.open_log).pack(side="left", padx=(6, 0))
        ttk.Button(frame, text="保存为默认配置", command=self.save_config).pack(
            side="left", padx=(6, 0)
        )
        ttk.Button(frame, text="打开配置文件", command=self.open_config_file).pack(
            side="left", padx=(6, 0)
        )

    def _build_status_frame(self) -> None:
        frame = ttk.Frame(self.root)
        frame.grid(row=3, column=0, sticky="ew", padx=10)
        frame.columnconfigure(0, weight=1)

        ttk.Label(frame, textvariable=self.status_var).grid(row=0, column=0, sticky="w")
        self.progress = ttk.Progressbar(frame, mode="determinate", length=260)
        self.progress.grid(row=0, column=1, sticky="e")
        ttk.Label(frame, textvariable=self.detail_var, foreground="#555555").grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(2, 0)
        )

    def _build_log_frame(self) -> None:
        frame = ttk.LabelFrame(self.root, text=" 运行日志 ", padding=(6, 4))
        frame.grid(row=4, column=0, sticky="nsew", padx=10, pady=(4, 10))
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        self.root.rowconfigure(4, weight=1)

        self.log_text = tk.Text(
            frame,
            height=16,
            wrap="none",
            font=("Consolas", 9),
            state="disabled",
            background="#fbfbfb",
        )
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=self.log_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scrollbar.set)
        self.log_text.tag_configure("DEBUG", foreground="#777777")
        self.log_text.tag_configure("WARNING", foreground="#b06a00")
        self.log_text.tag_configure("ERROR", foreground="#b00020")
        self.log_text.tag_configure("DONE", foreground="#0a6b2b")

    # ------------------------------------------------------------ 表单读写

    def _load_form(self, values: FormValues) -> None:
        for key in (
            "ebook_id",
            "start_page",
            "end_page",
            "book_root",
            "cookie_file",
            "log_level",
        ):
            self.vars[key].set(str(getattr(values, key)))
        for key in (
            "headless",
            "opportunistic",
            "use_search_jump",
            "dedupe_overlap",
            "save_json",
        ):
            self.vars[key].set(bool(getattr(values, key)))

    def _collect_form(self) -> FormValues:
        return FormValues(
            ebook_id=self.vars["ebook_id"].get(),
            start_page=self.vars["start_page"].get(),
            end_page=self.vars["end_page"].get(),
            book_root=self.vars["book_root"].get(),
            cookie_file=self.vars["cookie_file"].get(),
            log_level=self.vars["log_level"].get() or "INFO",
            headless=bool(self.vars["headless"].get()),
            opportunistic=bool(self.vars["opportunistic"].get()),
            use_search_jump=bool(self.vars["use_search_jump"].get()),
            dedupe_overlap=bool(self.vars["dedupe_overlap"].get()),
            save_json=bool(self.vars["save_json"].get()),
        )

    def _collect_settings(self, *, show_error: bool) -> Settings | None:
        try:
            return build_settings(self._collect_form())
        except ConfigError as exc:
            if show_error:
                messagebox.showerror("参数有问题", str(exc), parent=self.root)
            self.status_var.set(f"参数有问题：{exc}")
            return None

    # ------------------------------------------------------------ 计划

    def refresh_plan(self, *, log_it: bool = True) -> None:
        settings = self._collect_settings(show_error=False)
        if settings is None:
            self.detail_var.set("")
            return
        try:
            self.plan = build_plan(settings)
        except Exception as exc:  # noqa: BLE001 - 界面不能因为算计划失败就崩
            self.status_var.set(f"检查计划失败：{exc}")
            logger.warning("检查计划失败：%s", exc)
            return
        self.status_var.set(self.plan.describe())
        self.detail_var.set(f"输出目录：{self.plan.root_dir}")
        if log_it:
            self._append_log(f"[计划] {self.plan.describe()}", "INFO")
            self._append_log(f"[计划] 输出目录 {self.plan.root_dir}", "DEBUG")

    # ------------------------------------------------------------ 任务

    def start_task(self, kind: str) -> None:
        settings = self._collect_settings(show_error=True)
        if settings is None:
            return
        try:
            self.runner.start(settings, kind=kind)
        except RuntimeError as exc:
            messagebox.showwarning("正在运行", str(exc), parent=self.root)
            return

        self._append_log(f"===== {KIND_LABELS.get(kind, kind)}开始 =====", "DONE")
        self._set_running(True)
        self.progress.configure(mode="indeterminate")
        self.progress.start(60)

    def stop_task(self) -> None:
        if not self.runner.is_running:
            return
        self.runner.request_stop()
        self.status_var.set("正在停止…（当前页抓完就停，已抓内容会保留）")
        self._append_log("[停止] 已请求停止，等待当前页结束", "WARNING")

    def _set_running(self, running: bool) -> None:
        state = "disabled" if running else "normal"
        for button in (self.btn_plan, self.btn_start, self.btn_rebuild):
            button.configure(state=state)
        self.btn_stop.configure(state="normal" if running else "disabled")
        if not running:
            self.progress.stop()
            self.progress.configure(mode="determinate", value=0)
            self.refresh_plan(log_it=False)

    def _drain_events(self) -> None:
        """把后台线程的事件搬到界面上（每 100ms 一次，只处理有限条避免卡顿）。"""
        for _ in range(200):
            try:
                event = self.runner.events.get_nowait()
            except queue.Empty:
                break
            if event.kind == "log":
                self._append_log(event.text, event.level)
            elif event.kind == "progress":
                self._update_progress(event)
            elif event.kind == "done":
                self._handle_done(event)
        self.root.after(POLL_INTERVAL_MS, self._drain_events)

    def _update_progress(self, event: TaskEvent) -> None:
        if event.total <= 0:
            self.status_var.set(f"已抓 {event.done} 页…")
            return
        if str(self.progress.cget("mode")) != "determinate":
            self.progress.stop()
            self.progress.configure(mode="determinate")
        self.progress.configure(maximum=event.total, value=event.done)
        self.status_var.set(f"抓取中：{event.done} / {event.total} 页")

    def _handle_done(self, event: TaskEvent) -> None:
        result = event.result
        self._set_running(False)
        self._append_log("===== 结束 =====", "DONE")
        if result is None:
            self.status_var.set("任务结束（没有结果）")
            return
        for line in result.summary_lines():
            self._append_log(f"  {line}", "ERROR" if line.startswith("错误") else "DONE")

        if result.error:
            self.status_var.set(f"失败：{result.error}")
            if result.exit_code == 2:
                messagebox.showwarning(
                    "cookie 已失效",
                    "阅读器要求登录。请重新复制 cookie 覆盖 cookie.txt 后重试。\n"
                    "（点界面上的「怎么填?」有详细步骤）",
                    parent=self.root,
                )
        elif result.stopped:
            self.status_var.set(f"已停止：本次抓了 {result.pages_fetched} 页，重跑即续抓")
        elif result.incomplete_pages:
            self.status_var.set(
                f"部分完成：还有 {len(result.incomplete_pages)} 页没抓到，再点一次「开始抓取」即可续抓"
            )
        else:
            self.status_var.set(
                f"完成：本次抓 {result.pages_fetched} 页 · 共 "
                f"{sum(1 for c in result.chapters if c.has_content)} 章"
            )

        if self.vars["open_when_done"].get() and not result.error:
            self.open_output_dir()

    # ------------------------------------------------------------ 侧边动作

    def open_output_dir(self) -> None:
        settings = self._collect_settings(show_error=False)
        target = self.plan.root_dir if self.plan else (settings.book_dir() if settings else None)
        if target is None:
            return
        target.mkdir(parents=True, exist_ok=True)
        if open_in_file_manager(target):
            self._append_log(f"已打开输出目录 {target}", "DEBUG")

    def open_log(self) -> None:
        settings = self._collect_settings(show_error=False)
        log_dir = settings.log_path if settings else self.base_settings.log_path
        latest = latest_log_file(log_dir)
        target = latest or log_dir
        if not target.exists():
            self._append_log(f"还没有日志文件（{log_dir}）", "WARNING")
            return
        open_in_file_manager(target)

    def open_config_file(self) -> None:
        target = (
            LOCAL_CONFIG_FILE
            if LOCAL_CONFIG_FILE.is_file()
            else self.base_settings.resolve_path("config.toml")
        )
        if not target.is_file():
            messagebox.showinfo("没有配置文件", f"没找到 {target}", parent=self.root)
            return
        open_in_file_manager(target)

    def save_config(self) -> None:
        try:
            text = self._collect_form().to_toml()
        except ConfigError as exc:
            messagebox.showerror("参数有问题", str(exc), parent=self.root)
            return
        try:
            LOCAL_CONFIG_FILE.write_text(text, encoding="utf-8", newline="\n")
        except OSError as exc:
            messagebox.showerror("保存失败", f"{LOCAL_CONFIG_FILE}\n{exc}", parent=self.root)
            return
        self._append_log(f"已保存为默认配置：{LOCAL_CONFIG_FILE}", "DONE")
        messagebox.showinfo(
            "已保存",
            f"这些设置已写入：\n{LOCAL_CONFIG_FILE}\n\n"
            "以后直接双击 run_gui.cmd 就是这套参数；\n"
            "命令行 run.cmd 也会用同一份配置。",
            parent=self.root,
        )

    def show_cookie_help(self) -> None:
        messagebox.showinfo("怎么填 cookie", COOKIE_HELP, parent=self.root)

    def _on_close(self) -> None:
        if self.runner.is_running and not messagebox.askokcancel(
            "正在运行",
            "抓取还在进行中。停止并退出吗？\n（已抓到的页会保留，下次重跑即续抓）",
            parent=self.root,
        ):
            return
        self.runner.request_stop()
        self.root.destroy()

    # ------------------------------------------------------------ 日志区

    def _append_log(self, text: str, level: str = "INFO") -> None:
        tag = (
            level if level in {"DEBUG", "WARNING", "ERROR"} else ("DONE" if level == "DONE" else "")
        )
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text.rstrip() + "\n", tag)
        if int(self.log_text.index("end-1c").split(".")[0]) > MAX_LOG_LINES:
            self.log_text.delete("1.0", f"{MAX_LOG_LINES // 2}.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")


def main(argv: list[str] | None = None) -> int:
    """启动 GUI，返回进程退出码。"""
    from ..logging_setup import configure_console_encoding

    configure_console_encoding()
    enable_dpi_awareness()

    config_error: str | None = None
    try:
        settings = load_settings()
    except ConfigError as exc:
        # 配置坏了也必须能开界面，否则用户没法修
        config_error = str(exc)
        settings = Settings()

    root = tk.Tk()
    GuiApp(root, settings, config_error=config_error)
    root.mainloop()
    return 0


if __name__ == "__main__":  # pragma: no cover - 手动运行入口
    sys.exit(main())
