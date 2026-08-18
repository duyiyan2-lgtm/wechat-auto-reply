"""微信 4.x 无控件树时的视觉后端：截图找未读红点，OCR 会话名后回复。"""

from __future__ import annotations

import ctypes
import logging
import re
import time
from dataclasses import dataclass

import numpy as np
import win32clipboard
import win32con
import win32gui
import win32ui
from PIL import Image, ImageOps

log = logging.getLogger("wechat-auto-reply")

GROUP_RE = re.compile(r"[（(]\s*\d+\s*[）)]")
GROUP_WORDS = ("群", "班", "队", "组", "公众号", "服务通知")


@dataclass
class Badge:
    x: int
    y: int
    w: int
    h: int
    name: str = ""

    @property
    def cx(self) -> int:
        return self.x + self.w // 2

    @property
    def cy(self) -> int:
        return self.y + self.h // 2


def find_weixin_hwnd() -> int:
    hwnd = win32gui.FindWindow("Qt51514QWindowIcon", "微信")
    if not hwnd:
        hwnd = win32gui.FindWindow("Qt51514QWindowIcon", "Weixin")
    return int(hwnd or 0)


def restore_window(hwnd: int) -> None:
    if hwnd and win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        time.sleep(0.4)


def capture_window(hwnd: int) -> Image.Image:
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    width, height = right - left, bottom - top
    hwnd_dc = win32gui.GetWindowDC(hwnd)
    mfc = win32ui.CreateDCFromHandle(hwnd_dc)
    save = mfc.CreateCompatibleDC()
    bitmap = win32ui.CreateBitmap()
    bitmap.CreateCompatibleBitmap(mfc, width, height)
    save.SelectObject(bitmap)
    ok = ctypes.windll.user32.PrintWindow(hwnd, save.GetSafeHdc(), 2)
    if not ok:
        raise RuntimeError("截取微信窗口失败")
    info = bitmap.GetInfo()
    bits = bitmap.GetBitmapBits(True)
    img = Image.frombuffer(
        "RGB", (info["bmWidth"], info["bmHeight"]), bits, "raw", "BGRX", 0, 1
    )
    win32gui.DeleteObject(bitmap.GetHandle())
    save.DeleteDC()
    mfc.DeleteDC()
    win32gui.ReleaseDC(hwnd, hwnd_dc)
    return img


def _red_mask(arr: np.ndarray) -> np.ndarray:
    r = arr[:, :, 0].astype(np.int16)
    g = arr[:, :, 1].astype(np.int16)
    b = arr[:, :, 2].astype(np.int16)
    return (r > 200) & (r - g > 80) & (r - b > 80) & (g < 120) & (b < 120)


def find_badges(img: Image.Image) -> list[Badge]:
    arr = np.array(img.convert("RGB"))
    h, w, _ = arr.shape
    mask = _red_mask(arr)
    # 左侧会话头像右上角一带，避开最左侧导航栏和右侧聊天区
    x0, x1 = int(w * 0.15), int(w * 0.20)
    y0, y1 = 70, h - 20
    mask[:, :x0] = False
    mask[:, x1:] = False
    mask[:y0, :] = False
    mask[y1:, :] = False

    visited = np.zeros_like(mask, dtype=bool)
    badges: list[Badge] = []
    ys, xs = np.where(mask)
    for x, y in zip(xs.tolist(), ys.tolist()):
        if visited[y, x]:
            continue
        stack = [(x, y)]
        visited[y, x] = True
        pts: list[tuple[int, int]] = []
        while stack:
            cx, cy = stack.pop()
            pts.append((cx, cy))
            for nx, ny in (
                (cx + 1, cy),
                (cx - 1, cy),
                (cx, cy + 1),
                (cx, cy - 1),
            ):
                if 0 <= nx < w and 0 <= ny < h and mask[ny, nx] and not visited[ny, nx]:
                    visited[ny, nx] = True
                    stack.append((nx, ny))
        xs2 = [p[0] for p in pts]
        ys2 = [p[1] for p in pts]
        bw, bh = max(xs2) - min(xs2) + 1, max(ys2) - min(ys2) + 1
        if not (12 <= len(pts) <= 180 and 8 <= bw <= 20 and 8 <= bh <= 20):
            continue
        if abs(bw - bh) > 6:
            continue
        badges.append(Badge(min(xs2), min(ys2), bw, bh))
    badges.sort(key=lambda b: b.y)
    return badges


def ocr_image(img: Image.Image) -> str:
    try:
        import winocr
    except ImportError:
        return ""
    inv = ImageOps.invert(img.convert("RGB"))
    try:
        result = winocr.recognize_pil_sync(inv, lang="zh-Hans-CN")
    except Exception:
        try:
            result = winocr.recognize_pil_sync(inv, lang="zh-CN")
        except Exception as exc:
            log.debug("OCR 失败: %s", exc)
            return ""
    if isinstance(result, dict):
        return str(result.get("text") or "")
    return str(getattr(result, "text", "") or "")


def ocr_row_name(img: Image.Image, badge: Badge) -> str:
    w, h = img.size
    top = max(70, badge.y - 18)
    bottom = min(h - 10, badge.y + 50)
    left = int(w * 0.16)
    right = int(w * 0.40)
    crop = img.crop((left, top, right, bottom))
    text = ocr_image(crop)
    return re.sub(r"\s+", "", text)


def ocr_header(img: Image.Image) -> str:
    w, _h = img.size
    crop = img.crop((int(w * 0.40), 8, int(w * 0.92), 70))
    return re.sub(r"\s+", "", ocr_image(crop))


def looks_like_group(name: str) -> bool:
    if not name:
        return False
    if GROUP_RE.search(name):
        return True
    return any(word in name for word in GROUP_WORDS)


def copy_text(text: str) -> None:
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
    finally:
        win32clipboard.CloseClipboard()


def click_window(hwnd: int, x: int, y: int) -> None:
    import pyautogui

    left, top, _, _ = win32gui.GetWindowRect(hwnd)
    pyautogui.FAILSAFE = False
    pyautogui.click(left + x, top + y)
    time.sleep(0.25)


def send_to_current_chat(hwnd: int, img: Image.Image, message: str) -> None:
    import pyautogui

    w, h = img.size
    # 右侧输入框大约在聊天区底部中间
    ix = int(w * 0.62)
    iy = int(h * 0.92)
    click_window(hwnd, ix, iy)
    copy_text(message)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.15)
    pyautogui.hotkey("alt", "s")
    time.sleep(0.25)


def scan_unread(hwnd: int | None = None) -> tuple[Image.Image, list[Badge]]:
    hwnd = hwnd or find_weixin_hwnd()
    if not hwnd:
        raise RuntimeError("找不到微信窗口")
    restore_window(hwnd)
    win32gui.SetForegroundWindow(hwnd)
    time.sleep(0.2)
    img = capture_window(hwnd)
    badges = find_badges(img)
    for badge in badges:
        badge.name = ocr_row_name(img, badge)
    return img, badges
