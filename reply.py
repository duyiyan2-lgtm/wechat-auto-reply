"""微信 PC 离线留言自动回复。

通过官方 Windows 客户端做 UI 自动化（模拟点击/输入），不注入进程、
不走非官方协议。个人微信没有开放自动回复接口，这是目前可用的做法。

使用前请先登录 PC 微信 4.x，保持窗口可见，不要锁屏。
微信 4.x 需要先开一次系统「讲述人」再重新打开微信，控件树才会出现。
首次请运行: python reply.py --prepare
按 Ctrl+C 停止。
"""

from __future__ import annotations

import argparse
import ctypes
import json
import logging
import os
import subprocess
import sys
import time
import winreg
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = ROOT / "config.yaml"
STATE_PATH = ROOT / "data" / "replied.json"
LOG_DIR = ROOT / "logs"

DEFAULT_BLOCKLIST = (
    "文件传输助手",
    "微信团队",
    "微信支付",
    "腾讯新闻",
    "服务通知",
    "订阅号消息",
)


def setup_logging() -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("wechat-auto-reply")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S")
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    fh = logging.FileHandler(
        LOG_DIR / f"reply-{datetime.now():%Y%m%d}.log", encoding="utf-8"
    )
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(fh)
    return logger


log = setup_logging()


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"找不到配置文件: {path}")
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError("config.yaml 格式错误，根节点必须是映射")

    message = str(data.get("message") or "").strip()
    if not message:
        raise ValueError("config.yaml 里的 message 不能为空")

    hours = data.get("active_hours") or {}
    if not isinstance(hours, dict):
        hours = {}

    blocklist = [str(x).strip() for x in (data.get("blocklist") or []) if str(x).strip()]
    for name in DEFAULT_BLOCKLIST:
        if name not in blocklist:
            blocklist.append(name)

    return {
        "message": message,
        "cooldown_minutes": max(1, int(data.get("cooldown_minutes") or 60)),
        "poll_interval": max(3, float(data.get("poll_interval") or 8)),
        "duration": str(data.get("duration") or "0").strip(),
        "reply_groups": bool(data.get("reply_groups")),
        "allowlist": [str(x).strip() for x in (data.get("allowlist") or []) if str(x).strip()],
        "blocklist": blocklist,
        "active_start": str(hours.get("start") or "").strip(),
        "active_end": str(hours.get("end") or "").strip(),
    }


def load_state() -> dict[str, float]:
    if not STATE_PATH.exists():
        return {}
    try:
        raw = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    out: dict[str, float] = {}
    for name, ts in (raw or {}).items():
        try:
            out[str(name)] = datetime.fromisoformat(str(ts)).timestamp()
        except ValueError:
            continue
    return out


def save_state(state: dict[str, float]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        name: datetime.fromtimestamp(ts).isoformat(timespec="seconds")
        for name, ts in state.items()
    }
    STATE_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def parse_duration(text: str) -> float | None:
    """返回秒数。0 / 空表示不限时。"""
    text = (text or "").strip().lower()
    if not text or text in {"0", "0s", "0min", "0h"}:
        return None
    if text.endswith("h"):
        return float(text[:-1]) * 3600
    if text.endswith("min"):
        return float(text[:-3]) * 60
    if text.endswith("s"):
        return float(text[:-1])
    raise ValueError(f"无法解析 duration: {text}，请用 30s / 20min / 2h / 0")


def in_active_hours(start: str, end: str, now: datetime | None = None) -> bool:
    if not start or not end:
        return True
    now = now or datetime.now()
    t0 = datetime.strptime(start, "%H:%M").time()
    t1 = datetime.strptime(end, "%H:%M").time()
    cur = now.time()
    if t0 <= t1:
        return t0 <= cur <= t1
    return cur >= t0 or cur <= t1


def in_cooldown(state: dict[str, float], name: str, minutes: int) -> bool:
    last = state.get(name)
    if last is None:
        return False
    return (time.time() - last) < minutes * 60


A11Y_HINT = """
微信 4.x 还读不到界面。请双击 start.bat，
它会自动打开讲述人并重启微信（需重新登录时按提示扫码）。
""".strip()


def find_weixin_hwnd() -> int:
    import win32gui

    hwnd = win32gui.FindWindow("Qt51514QWindowIcon", "微信")
    if not hwnd:
        hwnd = win32gui.FindWindow("Qt51514QWindowIcon", "Weixin")
    return int(hwnd or 0)


def restore_weixin_window() -> int:
    import win32con
    import win32gui

    hwnd = find_weixin_hwnd()
    if not hwnd:
        return 0
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        time.sleep(0.8)
    return hwnd


def weixin_control_count() -> int:
    from pywinauto import Desktop

    hwnd = restore_weixin_window()
    if not hwnd:
        return 0
    win = Desktop(backend="uia").window(handle=hwnd)
    try:
        return len(win.children())
    except Exception:
        return 0


def weixin_process_running() -> bool:
    try:
        import psutil

        return any(
            (p.info.get("name") or "").lower() == "weixin.exe"
            for p in psutil.process_iter(["name"])
        )
    except Exception:
        return False


def weixin_ui_ready() -> bool:
    """控件树足够深，说明 4.x 已打开无障碍。"""
    if not weixin_process_running():
        return False
    hwnd = restore_weixin_window()
    if not hwnd:
        return False
    from pywinauto import Desktop

    win = Desktop(backend="uia").window(handle=hwnd)
    try:
        cls = win.class_name()
    except Exception:
        cls = ""
    if cls in {"mmui::MainWindow", "mmui::LoginWindow"}:
        return True
    if cls.startswith("mmui::") and weixin_control_count() >= 5:
        return True
    return weixin_control_count() >= 8


def set_screen_reader_flag(enabled: bool) -> None:
    """告诉系统当前有屏幕阅读器，微信 4.x 才会露出控件。"""
    spi_setscreenreader = 0x0047
    spif = 0x01 | 0x02
    ctypes.windll.user32.SystemParametersInfoW(
        spi_setscreenreader, 1 if enabled else 0, 0, spif
    )


def find_weixin_exe() -> Path | None:
    candidates = [
        Path(r"D:\weixin\Weixin\Weixin.exe"),
        Path(r"D:\weixin\Weixin.exe"),
    ]
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Tencent\Weixin") as key:
            install, _ = winreg.QueryValueEx(key, "InstallPath")
            candidates.insert(0, Path(install) / "Weixin.exe")
    except OSError:
        pass
    try:
        import psutil

        for proc in psutil.process_iter(["name", "exe"]):
            if (proc.info.get("name") or "").lower() == "weixin.exe" and proc.info.get("exe"):
                candidates.insert(0, Path(proc.info["exe"]))
    except Exception:
        pass
    for path in candidates:
        if path and path.exists():
            return path
    return None


def narrator_running() -> bool:
    try:
        import psutil

        return any(
            (p.info.get("name") or "").lower() == "narrator.exe"
            for p in psutil.process_iter(["name"])
        )
    except Exception:
        return False


def hide_narrator_windows() -> None:
    import win32con
    import win32gui

    def cb(hwnd, _acc):
        try:
            title = win32gui.GetWindowText(hwnd)
            cls = win32gui.GetClassName(hwnd)
        except Exception:
            return True
        blob = f"{title} {cls}"
        if "Narrator" in blob or "讲述人" in blob:
            win32gui.ShowWindow(hwnd, win32con.SW_HIDE)
        return True

    win32gui.EnumWindows(cb, None)


def start_narrator_muted() -> None:
    """不提权启动讲述人，藏窗口，并尽量关掉朗读。"""
    key_path = r"Software\Microsoft\Narrator\NoRoam"
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path) as key:
        winreg.SetValueEx(key, "WinEnterLaunchEnabled", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(key, "DuckAudio", 0, winreg.REG_DWORD, 0)

    if not narrator_running():
        narrator = Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32" / "Narrator.exe"
        launched = False
        if narrator.exists():
            # 用 explorer 代启，避免 CreateProcess 的 740 提权错误
            rc = ctypes.windll.shell32.ShellExecuteW(
                None, "open", str(narrator), None, None, 0
            )
            launched = rc > 32
        if not launched:
            log.warning("无法启动讲述人。")
        time.sleep(2.5)

    hide_narrator_windows()
    if narrator_running():
        log.info("讲述人已在后台运行（不要关掉它）。")
    else:
        log.warning("讲述人没能保持运行，微信可能仍然读不到控件。")


def ensure_narrator() -> None:
    if not narrator_running():
        start_narrator_muted()
    else:
        hide_narrator_windows()


def stop_weixin() -> None:
    import psutil

    procs = [
        p
        for p in psutil.process_iter(["name"])
        if (p.info.get("name") or "").lower() == "weixin.exe"
    ]
    for p in procs:
        try:
            p.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    gone, alive = psutil.wait_procs(procs, timeout=8)
    for p in alive:
        try:
            p.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    deadline = time.time() + 15
    while time.time() < deadline:
        if not weixin_process_running() and not find_weixin_hwnd():
            time.sleep(2.5)
            return
        time.sleep(0.4)
    log.warning("微信进程仍未完全退出，继续尝试启动。")


def start_weixin() -> None:
    exe = find_weixin_exe()
    if not exe:
        raise SystemExit("找不到 Weixin.exe，请先手动打开电脑版微信。")
    rc = ctypes.windll.shell32.ShellExecuteW(None, "open", str(exe), None, str(exe.parent), 1)
    if rc <= 32:
        subprocess.Popen([str(exe)], cwd=str(exe.parent))
    log.info("已启动微信：%s", exe)


def wait_weixin_ui(timeout: float = 90) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        ensure_narrator()
        if weixin_ui_ready():
            return True
        time.sleep(2)
    return weixin_ui_ready()


def bootstrap_weixin() -> None:
    """开讲述人 + 重启微信，让 4.x 露出控件树。"""
    if weixin_ui_ready():
        log.info("控件树已经可见，无需重启微信。")
        return

    log.info("正在打开无障碍并重启微信（会先关掉当前微信）。")
    set_screen_reader_flag(True)
    start_narrator_muted()
    if not narrator_running():
        raise SystemExit("讲述人没能启动，微信 4.x 无法自动回复。请按 Win+Ctrl+Enter 打开讲述人后再试。")
    if weixin_process_running():
        stop_weixin()
    # 微信必须在讲述人仍在运行时完成主界面创建
    time.sleep(1)
    ensure_narrator()
    start_weixin()
    log.info("微信已重新打开，等待主界面加载（请不要关讲述人；如弹出登录请扫码）…")
    deadline = time.time() + 25
    while time.time() < deadline and not weixin_process_running():
        time.sleep(0.5)
    if not weixin_process_running():
        raise SystemExit("微信没能启动，请手动打开微信后再双击 start.bat。")
    # 主界面大约十几秒后才建完，讲述人必须活到那一刻
    stable_until = time.time() + 18
    while time.time() < stable_until:
        ensure_narrator()
        time.sleep(1)
    if wait_weixin_ui(60):
        log.info("成功：已读到微信控件，可以自动回复了。")
        return
    raise SystemExit(
        "重启后仍读不到微信界面。请确认已登录、窗口没有最小化，"
        "并且不要关闭讲述人，然后再次双击 start.bat。"
    )


def prepare_accessibility() -> None:
    bootstrap_weixin()


def apply_global_config() -> None:
    from pyweixin import GlobalConfig

    GlobalConfig.close_weixin = False
    GlobalConfig.is_maximize = False
    GlobalConfig.search_pages = 0
    GlobalConfig.send_delay = 0.25


def check_weixin() -> None:
    from vision_backend import capture_window, find_badges, find_weixin_hwnd, ocr_header

    hwnd = find_weixin_hwnd()
    if not hwnd:
        raise SystemExit("找不到微信窗口。请先打开并登录电脑版微信 4.x，窗口不要最小化。")
    restore_weixin_window()
    img = capture_window(hwnd)
    header = ocr_header(img)
    badges = find_badges(img)
    log.info("已找到微信窗口 %sx%s", img.size[0], img.size[1])
    if header:
        log.info("当前聊天标题：%s", header)
    log.info("当前可见未读红点：%s", len(badges))
    log.info("视觉后端可用（微信 4.x 已锁控件，改用截图识别）。")


def should_skip_by_name(name: str, cfg: dict[str, Any]) -> str | None:
    if name in cfg["blocklist"]:
        return "黑名单"
    if cfg["allowlist"] and name not in cfg["allowlist"]:
        return "不在白名单"
    return None


def handle_badge(badge, hwnd, img, cfg: dict[str, Any], state: dict[str, float], dry_run: bool) -> str:
    from vision_backend import (
        click_window,
        capture_window,
        looks_like_group,
        ocr_header,
        send_to_current_chat,
    )

    name = (badge.name or "").strip() or f"未读@{badge.y}"
    reason = should_skip_by_name(name, cfg)
    if reason:
        return f"{name} → 跳过（{reason}）"
    if not cfg["reply_groups"] and looks_like_group(name):
        return f"{name} → 跳过（群聊/公众号）"
    if in_cooldown(state, name, cfg["cooldown_minutes"]):
        return f"{name} → 跳过（冷却中）"
    if dry_run:
        return f"{name} → 演练，未发送"

    click_window(hwnd, badge.cx + 40, badge.cy)
    time.sleep(0.45)
    img2 = capture_window(hwnd)
    header = ocr_header(img2) or name
    if should_skip_by_name(header, cfg):
        return f"{header} → 跳过（黑名单）"
    if not cfg["reply_groups"] and looks_like_group(header):
        return f"{header} → 跳过（群聊/公众号）"

    send_to_current_chat(hwnd, img2, cfg["message"])
    key = header or name
    state[key] = time.time()
    save_state(state)
    return f"{key} → 已回复"


def run_loop(cfg: dict[str, Any], dry_run: bool, once: bool) -> None:
    from vision_backend import find_weixin_hwnd, scan_unread

    hwnd = find_weixin_hwnd()
    if not hwnd:
        raise SystemExit("找不到微信窗口。请先打开并登录电脑版微信，窗口不要最小化。")
    restore_weixin_window()

    deadline = None
    seconds = parse_duration(cfg["duration"])
    if seconds:
        deadline = time.time() + seconds

    state = load_state()
    log.info("离线留言已启动。正文：%s", cfg["message"].replace("\n", " / "))
    log.info(
        "冷却 %s 分钟，扫描间隔 %s 秒，群聊回复=%s",
        cfg["cooldown_minutes"],
        cfg["poll_interval"],
        cfg["reply_groups"],
    )
    if cfg["allowlist"]:
        log.info("白名单：%s", "、".join(cfg["allowlist"]))
    if dry_run:
        log.info("当前是演练模式，不会真正发消息。")
    log.info("请保持微信窗口可见，不要锁屏。按 Ctrl+C 停止。")

    try:
        while True:
            if deadline and time.time() >= deadline:
                log.info("已到设定运行时长，退出。")
                return
            if not in_active_hours(cfg["active_start"], cfg["active_end"]):
                log.info(
                    "不在工作时段 %s–%s，等待中…",
                    cfg["active_start"],
                    cfg["active_end"],
                )
                time.sleep(cfg["poll_interval"])
                continue

            hwnd = find_weixin_hwnd()
            if not hwnd:
                log.warning("微信窗口消失，等待中…")
                time.sleep(cfg["poll_interval"])
                if once:
                    return
                continue

            try:
                img, badges = scan_unread(hwnd)
            except Exception as exc:
                log.warning("扫描失败，将重试: %s", exc)
                time.sleep(cfg["poll_interval"])
                if once:
                    return
                continue

            if badges:
                log.info("发现 %s 个未读红点", len(badges))
                for badge in badges:
                    try:
                        result = handle_badge(badge, hwnd, img, cfg, state, dry_run)
                        log.info("  %s", result)
                    except Exception as exc:
                        log.exception("  处理红点失败: %s", exc)
            else:
                log.info("暂无新消息")

            if once:
                return
            time.sleep(cfg["poll_interval"])
    except KeyboardInterrupt:
        log.info("已手动停止。")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="微信 PC 离线留言自动回复")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="配置文件路径")
    parser.add_argument("--check", action="store_true", help="只检查能否连上微信，不发消息")
    parser.add_argument("--prepare", action="store_true", help="打开讲述人并重启微信")
    parser.add_argument("--bootstrap", action="store_true", help="同 --prepare")
    parser.add_argument("--dry-run", action="store_true", help="演练：扫描但不发送")
    parser.add_argument("--once", action="store_true", help="只扫描一轮后退出")
    parser.add_argument("--scan", action="store_true", help="只列出当前未读红点")
    args = parser.parse_args(argv)

    try:
        if args.prepare or args.bootstrap:
            bootstrap_weixin()
            return 0
        if args.check:
            check_weixin()
            return 0
        if args.scan:
            from vision_backend import find_weixin_hwnd, scan_unread

            hwnd = find_weixin_hwnd()
            if not hwnd:
                raise SystemExit("找不到微信窗口。")
            _img, badges = scan_unread(hwnd)
            if not badges:
                log.info("没有发现未读红点")
            for badge in badges:
                log.info("红点 y=%s 识别名=%s", badge.y, badge.name or "(空)")
            return 0
        cfg = load_config(args.config)
        run_loop(cfg, dry_run=args.dry_run, once=args.once)
        return 0
    except SystemExit as exc:
        if isinstance(exc.code, str):
            log.error("%s", exc.code)
            return 1
        return int(exc.code or 0)
    except Exception as exc:
        log.exception("启动失败: %s", exc)
        return 1


if __name__ == "__main__":
    if sys.platform != "win32":
        print("本脚本只支持 Windows 上的 PC 微信。")
        sys.exit(2)
    sys.exit(main())
