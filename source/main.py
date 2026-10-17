from __future__ import annotations

import ctypes
import ctypes.wintypes
import base64
import hashlib
import json
import logging
import random
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from queue import Empty, Queue
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageGrab, ImageTk, ImageOps
try:
    import imageio_ffmpeg
except ImportError:
    imageio_ffmpeg = None
import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox, simpledialog, ttk


APP_VERSION = "4.9"
APP_NAME = f"梦三国2巅峰争霸脚本 {APP_VERSION}"
WM_HOTKEY = 0x0312
HOTKEY_F8 = 1
HOTKEY_F9 = 2
VK_F8 = 0x77
VK_F9 = 0x78
VK_F12 = 0x7B

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
gdi32 = ctypes.windll.gdi32

user32.GetDC.restype = ctypes.c_void_p
gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
gdi32.CreateDIBSection.restype = ctypes.c_void_p
gdi32.SelectObject.restype = ctypes.c_void_p


PROFILE_BY_CATEGORY = {"survival": "tank", "magic": "mage", "physical": "physical"}
PROFILE_LABELS = {"tank": "肉盾", "mage": "法师", "physical": "物理"}
PROFILE_BY_LABEL = {label: profile for profile, label in PROFILE_LABELS.items()}
HERO_NAMES = set("曹操 曹丕 曹植 曹仁 曹洪 曹彰 曹冲 司马懿 司马昭 司马师 郭嘉 荀彧 贾诩 许褚 典韦 张辽 张郃 徐晃 于禁 乐进 李典 夏侯惇 夏侯渊 刘备 关羽 张飞 赵云 马超 黄忠 魏延 姜维 诸葛亮 庞统 法正 徐庶 马良 马谡 刘禅 黄月英 关平 关兴 张苞 孙权 孙策 孙坚 孙尚香 周瑜 周泰 鲁肃 吕蒙 陆逊 陆抗 黄盖 程普 韩当 太史慈 甘宁 凌统 小乔 大乔 吕布 貂蝉 董卓 华雄 袁绍 袁术 张角 张宝 张梁 张鲁 马岱 邢道荣 潘凤 公孙瓒 梦刘备 梦关羽 梦张飞 梦赵云 梦马超 梦黄忠 梦诸葛亮 梦司马懿 梦曹操 梦曹丕 梦孙权 梦孙策 梦周瑜 梦吕布 梦貂蝉 梦刘禅 梦许褚 梦郭嘉 梦孙尚香".split())


def validated_selection_name(text: str) -> str:
    # Accept known hero suffixes; never save an arbitrary OCR phrase as a name.
    exact = [name for name in HERO_NAMES if text.endswith(name)]
    if exact:
        return max(exact, key=len)
    candidates = []
    for name in HERO_NAMES:
        suffix = text[-len(name):]
        if len(name) >= 3 and len(suffix) == len(name) and suffix[0] == name[0]:
            if sum(a != b for a, b in zip(suffix, name)) == 1:
                candidates.append(name)
    return candidates[0] if len(candidates) == 1 else ""


def find_tesseract() -> Path | None:
    located = shutil.which("tesseract")
    candidates = [
        app_dir() / "ocr" / "tesseract.exe",
        Path(located) if located else None,
        Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
        Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
    ]
    return next((path for path in candidates if path is not None and path.is_file()), None)


def recognize_hero_name(image: Image.Image, selection: bool | str = False) -> str:
    """Best-effort OCR of the selected-unit name shown above the bottom hero panel."""
    executable = find_tesseract()
    if executable is None:
        return ""
    if selection is True:
        badge_name = recognize_hero_name(image, selection="badge")
        if badge_name:
            return badge_name
    width, height = image.size
    if selection == "badge":
        # Only the local selected hero's name is gold; teammates' names are white.
        box = (round(width*.595), round(height*.775), round(width*.93), round(height*.805))
    elif selection:
        # Selected-hero announcement at the left of the selection artwork.
        # Unlike decorative vertical lettering, this includes the actual hero name.
        box = (0, round(height * 0.58), round(width * 0.40), round(height * 0.66))
    else:
        box = (round(width * 0.342), round(height * 0.776), round(width * 0.615), round(height * 0.828))
    if selection:
        pixels = np.asarray(image.convert("RGB").crop(box), dtype=np.int16)
        if selection == "badge":
            mask = (pixels[:,:,0] > 150) & (pixels[:,:,1] > 110) & (pixels[:,:,2] < 130) & (pixels[:,:,0] > pixels[:,:,2]+70) & (pixels[:,:,1] > pixels[:,:,2]+50)
        else:
            mask = (pixels[:,:,1] > 140) & (pixels[:,:,1] > pixels[:,:,0] + 45) & (pixels[:,:,1] > pixels[:,:,2] + 45)
        ys, xs = np.where(mask)
        if len(xs) < 40:
            return ""
        crop = Image.fromarray(np.where(mask, 0, 255).astype(np.uint8)).crop((int(xs.min()), int(ys.min()), int(xs.max())+1, int(ys.max())+1))
    else:
        crop = image.convert("L").crop(box)
    crop = ImageEnhance.Contrast(crop.resize((crop.width * 3, crop.height * 3), Image.Resampling.BICUBIC)).enhance(2.0)
    if selection:
        crop = ImageOps.expand(crop, border=24, fill=255)
    temporary = Path(tempfile.gettempdir()) / f"mengsanguo_hero_{threading.get_ident()}_{time.monotonic_ns()}.png"
    try:
        crop.save(temporary, "PNG")
        completed = subprocess.run(
            [str(executable), str(temporary), "stdout", "-l", "chi_sim" if selection else "chi_sim+eng", "--psm", "7"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=8.0,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        text = re.sub(r"\s+", "", completed.stdout)
        if selection:
            return validated_selection_name(text)
        match = re.search(r"([\u4e00-\u9fff]{2,4})等[级圾扱]", text)
        if match:
            return match.group(1)
        return ""
    except Exception:
        return ""
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", ctypes.c_ulong), ("u", INPUT_UNION)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.c_ulong),
        ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long),
        ("biPlanes", ctypes.c_ushort),
        ("biBitCount", ctypes.c_ushort),
        ("biCompression", ctypes.c_ulong),
        ("biSizeImage", ctypes.c_ulong),
        ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long),
        ("biClrUsed", ctypes.c_ulong),
        ("biClrImportant", ctypes.c_ulong),
    ]


class RGBQUAD(ctypes.Structure):
    _fields_ = [("rgbBlue", ctypes.c_ubyte), ("rgbGreen", ctypes.c_ubyte), ("rgbRed", ctypes.c_ubyte), ("rgbReserved", ctypes.c_ubyte)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", RGBQUAD * 1)]


gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
gdi32.CreateDIBSection.argtypes = [ctypes.c_void_p, ctypes.POINTER(BITMAPINFO), ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, ctypes.c_ulong]
gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
gdi32.DeleteDC.argtypes = [ctypes.c_void_p]
user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
user32.PrintWindow.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
user32.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM]
user32.ClipCursor.argtypes = [ctypes.POINTER(RECT)]
user32.ClipCursor.restype = ctypes.c_bool


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    title: str
    x: int
    y: int
    width: int
    height: int


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


class QueueHandler(logging.Handler):
    def __init__(self, queue: Queue[str]) -> None:
        super().__init__()
        self.queue = queue

    def emit(self, record: logging.LogRecord) -> None:
        self.queue.put(self.format(record))


class WindowController:
    def __init__(self, config: dict[str, Any], log: logging.Logger) -> None:
        self.config = config
        self.log = log
        self.locked_hwnd = 0
        self.locked_pid = 0
        self.cursor_confined = False
        self.cursor_clip_rect: tuple[int, int, int, int] | None = None

    @property
    def background_mode(self) -> bool:
        return bool(self.config["safety"].get("background_mode", False))

    def find(self) -> WindowInfo | None:
        needles = [s.casefold() for s in self.config["window"]["title_contains"] if s.strip()]
        if self.locked_hwnd and user32.IsWindow(self.locked_hwnd):
            locked = self._window_info(self.locked_hwnd, needles)
            if locked is not None:
                return locked
        if self.locked_hwnd:
            self.log.warning("已锁定的游戏窗口失效，准备重新查找")
            self.locked_hwnd = 0
            self.locked_pid = 0
        candidates: list[WindowInfo] = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def callback(hwnd: int, _lparam: int) -> bool:
            info = self._window_info(hwnd, needles)
            if info is not None:
                candidates.append(info)
            return True

        user32.EnumWindows(callback, 0)
        if not candidates:
            return None
        expected = self.config["window"]["client_size"]
        exact = [w for w in candidates if [w.width, w.height] == expected]
        selected = exact[0] if exact else candidates[0]
        process_id = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(selected.hwnd, ctypes.byref(process_id))
        self.locked_hwnd = selected.hwnd
        self.locked_pid = int(process_id.value)
        self.log.info("已锁定游戏窗口：PID=%s HWND=0x%X 标题=%s", self.locked_pid, selected.hwnd, selected.title)
        return selected

    @staticmethod
    def _window_info(hwnd: int, needles: list[str]) -> WindowInfo | None:
        if not user32.IsWindowVisible(hwnd):
            return None
        process_id = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
        if process_id.value == kernel32.GetCurrentProcessId():
            return None
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return None
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        title = buffer.value
        if not any(n in title.casefold() for n in needles):
            return None
        rect = RECT()
        if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
            return None
        point = POINT(0, 0)
        if not user32.ClientToScreen(hwnd, ctypes.byref(point)):
            return None
        return WindowInfo(hwnd, title, point.x, point.y, rect.right - rect.left, rect.bottom - rect.top)

    def validate(self, require_foreground: bool = True) -> WindowInfo | None:
        win = self.find()
        if win is None:
            return None
        expected = self.config["window"]["client_size"]
        tolerance = int(self.config["window"].get("size_tolerance", 0))
        if abs(win.width - expected[0]) > tolerance or abs(win.height - expected[1]) > tolerance:
            return None
        if require_foreground and not self.background_mode and self.config["safety"].get("require_foreground", True):
            if not self.is_foreground(win):
                return None
        return win

    def validate_for_capture(self, require_foreground: bool = True) -> WindowInfo | None:
        """Allow capture-only resolutions without relaxing automation safety."""
        win = self.find()
        if win is None:
            return None
        settings = self.config.get("capture", {})
        supported = settings.get("supported_client_sizes", [[1024, 768], [2560, 1440]])
        tolerance = max(0, int(settings.get("size_tolerance", 8)))
        matches = any(
            isinstance(size, list)
            and len(size) == 2
            and abs(win.width - int(size[0])) <= tolerance
            and abs(win.height - int(size[1])) <= tolerance
            for size in supported
        )
        if not matches:
            return None
        if require_foreground and not self.is_foreground(win):
            return None
        return win

    @staticmethod
    def is_foreground(win: WindowInfo) -> bool:
        foreground = user32.GetForegroundWindow()
        if not foreground:
            return False
        if foreground == win.hwnd:
            return True
        ga_root = 2
        if user32.GetAncestor(foreground, ga_root) == user32.GetAncestor(win.hwnd, ga_root):
            return True
        target_pid = ctypes.c_ulong()
        foreground_pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(win.hwnd, ctypes.byref(target_pid))
        user32.GetWindowThreadProcessId(foreground, ctypes.byref(foreground_pid))
        return target_pid.value != 0 and target_pid.value == foreground_pid.value

    @staticmethod
    def inside(win: WindowInfo, point: list[int]) -> bool:
        return len(point) == 2 and 0 <= point[0] < win.width and 0 <= point[1] < win.height

    def confine_cursor(self, win: WindowInfo) -> bool:
        if not self.config["safety"].get("lock_cursor_while_running", True):
            return True
        clip = (win.x, win.y, win.x + win.width, win.y + win.height)
        if self.cursor_confined and self.cursor_clip_rect == clip:
            return True
        rect = RECT(*clip)
        if not user32.ClipCursor(ctypes.byref(rect)):
            self.log.error("鼠标锁定失败：客户区屏幕范围=%s", clip)
            self.cursor_confined = False
            self.cursor_clip_rect = None
            return False
        moved = self.cursor_confined and self.cursor_clip_rect != clip
        self.cursor_confined = True
        self.cursor_clip_rect = clip
        self.log.info("%s鼠标范围：游戏客户区 %s", "更新" if moved else "锁定", clip)
        return True

    def release_cursor(self) -> None:
        if self.cursor_confined:
            user32.ClipCursor(None)
            self.cursor_confined = False
            self.cursor_clip_rect = None
            self.log.info("已释放游戏客户区鼠标锁定")

    def _send(self, inputs: list[INPUT]) -> bool:
        array_type = INPUT * len(inputs)
        sent = user32.SendInput(len(inputs), array_type(*inputs), ctypes.sizeof(INPUT))
        return sent == len(inputs)

    def move_cursor(self, relative: list[int]) -> bool:
        win = self.validate(require_foreground=not self.background_mode)
        if win is None or not self.inside(win, relative):
            return False
        if self.background_mode:
            return bool(user32.PostMessageW(win.hwnd, 0x0200, 0, (int(relative[1]) << 16) | int(relative[0])))
        vx, vy = user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)
        vw, vh = user32.GetSystemMetrics(78), user32.GetSystemMetrics(79)
        if vw <= 1 or vh <= 1:
            return False
        dx = round((win.x+relative[0]-vx)*65535/(vw-1))
        dy = round((win.y+relative[1]-vy)*65535/(vh-1))
        return self._send([INPUT(type=0, mi=MOUSEINPUT(dx, dy, 0, 0x0001 | 0x4000 | 0x8000, 0, None))])

    def click(self, relative: list[int], button: str = "left", hold: float | None = None) -> bool:
        win = self.validate(require_foreground=not self.background_mode)
        if win is None:
            self.log.warning("安全拦截：窗口不可用、尺寸不符或不在前台，未点击")
            return False
        if not self.inside(win, relative):
            self.log.error("安全拦截：相对坐标越界 %s，客户区=%sx%s", relative, win.width, win.height)
            return False
        if self.background_mode:
            x, y = int(relative[0]), int(relative[1])
            lparam = (y << 16) | (x & 0xFFFF)
            user32.PostMessageW(win.hwnd, 0x0200, 0, lparam)
            if button == "right":
                down_message, up_message, down_state = 0x0204, 0x0205, 0x0002
            else:
                down_message, up_message, down_state = 0x0201, 0x0202, 0x0001
            down_ok = bool(user32.PostMessageW(win.hwnd, down_message, down_state, lparam))
            time.sleep(hold if hold is not None else float(self.config["timing"].get("click_hold", 0.10)))
            up_ok = bool(user32.PostMessageW(win.hwnd, up_message, 0, lparam))
            return down_ok and up_ok
        sx, sy = win.x + relative[0], win.y + relative[1]
        user32.SetForegroundWindow(win.hwnd)
        time.sleep(float(self.config["timing"].get("input_focus_delay", 0.05)))
        virtual_x = user32.GetSystemMetrics(76)
        virtual_y = user32.GetSystemMetrics(77)
        virtual_width = user32.GetSystemMetrics(78)
        virtual_height = user32.GetSystemMetrics(79)
        if virtual_width <= 1 or virtual_height <= 1:
            self.log.error("无法取得虚拟桌面尺寸：%sx%s", virtual_width, virtual_height)
            return False
        dx = round((sx - virtual_x) * 65535 / (virtual_width - 1))
        dy = round((sy - virtual_y) * 65535 / (virtual_height - 1))
        move = INPUT(type=0, mi=MOUSEINPUT(dx, dy, 0, 0x0001 | 0x4000 | 0x8000, 0, None))
        if not self._send([move]):
            self.log.error("SendInput 鼠标移动失败：屏幕坐标 (%s, %s)，虚拟桌面=(%s,%s,%s,%s)", sx, sy, virtual_x, virtual_y, virtual_width, virtual_height)
            return False
        time.sleep(float(self.config["timing"].get("input_move_delay", 0.08)))
        checked = self.validate(require_foreground=True)
        if checked is None or checked.hwnd != win.hwnd:
            self.log.warning("安全拦截：鼠标移动后目标窗口失去前台，取消点击")
            return False
        if button == "right":
            down_flag, up_flag = 0x0008, 0x0010
        else:
            down_flag, up_flag = 0x0002, 0x0004
        down = INPUT(type=0, mi=MOUSEINPUT(0, 0, 0, down_flag, 0, None))
        up = INPUT(type=0, mi=MOUSEINPUT(0, 0, 0, up_flag, 0, None))
        down_ok = self._send([down])
        time.sleep(hold if hold is not None else float(self.config["timing"].get("click_hold", 0.10)))
        up_ok = self._send([up])
        if down_ok and up_ok:
            self.log.info("输入确认：%s点击 客户区=%s 屏幕=(%s,%s)", button, relative, sx, sy)
        return down_ok and up_ok

    def key_tap(self, key: str) -> bool:
        win = self.validate(require_foreground=not self.background_mode)
        if win is None:
            self.log.warning("安全拦截：窗口不可用、尺寸不符或不在前台，未发送按键")
            return False
        named_keys = {"esc": 0x1B, "escape": 0x1B, "enter": 0x0D, "space": 0x20}
        named_keys.update({f"f{index}": 0x6F + index for index in range(1, 13)})
        normalized = key.casefold()
        vk = named_keys.get(normalized)
        if vk is None:
            if len(key) != 1:
                self.log.error("无法映射按键：%s", key)
                return False
            vk = user32.VkKeyScanW(ord(key.upper())) & 0xFF
        if vk == 0xFF:
            self.log.error("无法映射按键：%s", key)
            return False
        if self.background_mode:
            scan = user32.MapVirtualKeyW(vk, 0)
            down_lparam = 1 | (scan << 16)
            up_lparam = down_lparam | (1 << 30) | (1 << 31)
            down_ok = bool(user32.PostMessageW(win.hwnd, 0x0100, vk, down_lparam))
            up_ok = bool(user32.PostMessageW(win.hwnd, 0x0101, vk, up_lparam))
            return down_ok and up_ok
        down = INPUT(type=1, ki=KEYBDINPUT(vk, 0, 0, 0, None))
        up = INPUT(type=1, ki=KEYBDINPUT(vk, 0, 0x0002, 0, None))
        user32.SetForegroundWindow(win.hwnd)
        time.sleep(float(self.config["timing"].get("input_focus_delay", 0.05)))
        down_ok = self._send([down])
        time.sleep(float(self.config["timing"].get("key_hold", 0.06)))
        up_ok = self._send([up])
        return down_ok and up_ok

    def grab(self) -> tuple[WindowInfo, Image.Image] | None:
        win = self.validate(require_foreground=False)
        if win is None:
            return None
        if self.background_mode:
            image = self._grab_background(win)
            if image is None:
                return None
            return win, image
        bbox = (win.x, win.y, win.x + win.width, win.y + win.height)
        try:
            return win, ImageGrab.grab(bbox=bbox, all_screens=True)
        except Exception as exc:
            self.log.warning("客户区截图失败：%s", exc)
            return None

    def grab_capture_window(self, win: WindowInfo) -> Image.Image | None:
        """Capture a telemetry window at its native size after size validation."""
        if not user32.IsWindow(win.hwnd):
            return None
        if self.background_mode:
            return self._grab_background(win)
        try:
            bbox = (win.x, win.y, win.x + win.width, win.y + win.height)
            return ImageGrab.grab(bbox=bbox, all_screens=True)
        except Exception as exc:
            self.log.warning("实战采集截图失败：%s", exc)
            return None

    def _grab_background(self, win: WindowInfo) -> Image.Image | None:
        window_dc = user32.GetDC(win.hwnd)
        if not window_dc:
            self.log.warning("后台抓图失败：无法取得窗口 DC")
            return None
        memory_dc = gdi32.CreateCompatibleDC(window_dc)
        bitmap = None
        previous = None
        try:
            info = BITMAPINFO()
            info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            info.bmiHeader.biWidth = win.width
            info.bmiHeader.biHeight = -win.height
            info.bmiHeader.biPlanes = 1
            info.bmiHeader.biBitCount = 32
            info.bmiHeader.biCompression = 0
            bits = ctypes.c_void_p()
            bitmap = gdi32.CreateDIBSection(memory_dc, ctypes.byref(info), 0, ctypes.byref(bits), None, 0)
            if not bitmap or not bits.value:
                self.log.warning("后台抓图失败：无法创建位图")
                return None
            previous = gdi32.SelectObject(memory_dc, bitmap)
            if not user32.PrintWindow(win.hwnd, memory_dc, 0x00000003):
                self.log.warning("后台抓图失败：游戏窗口不支持 PrintWindow")
                return None
            raw = ctypes.string_at(bits.value, win.width * win.height * 4)
            image = Image.frombuffer("RGB", (win.width, win.height), raw, "raw", "BGRX", 0, 1).copy()
            if float(np.asarray(image.convert("L"), dtype=np.float32).std()) < 2.0:
                self.log.warning("后台抓图接近纯色，游戏可能不支持后台画面捕获")
                return None
            return image
        except Exception as exc:
            self.log.warning("后台抓图异常：%s", exc)
            return None
        finally:
            if previous:
                gdi32.SelectObject(memory_dc, previous)
            if bitmap:
                gdi32.DeleteObject(bitmap)
            if memory_dc:
                gdi32.DeleteDC(memory_dc)
            user32.ReleaseDC(win.hwnd, window_dc)


class DiagnosticLogHandler(logging.FileHandler):
    """Timestamp script logs on the recording's monotonic timeline."""
    def __init__(self, path: Path, started: float) -> None:
        super().__init__(path, encoding="utf-8")
        self.started = started

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.stream.write(json.dumps({"t_ms": max(0, round((time.monotonic()-self.started)*1000)), "level": record.levelname, "message": record.getMessage()}, ensure_ascii=False)+"\n")
            self.flush()
        except Exception:
            self.handleError(record)


class CombatRecorder:
    """Visible, foreground-only game telemetry recorder.

    It deliberately polls a small action-key allowlist instead of installing a
    system-wide keyboard hook. No text, clipboard content, process memory or
    input from other applications is collected.
    """

    KEY_CODES = {
        "A": 0x41,
        "B": 0x42,
        "D": 0x44,
        "E": 0x45,
        "Q": 0x51,
        "R": 0x52,
        "W": 0x57,
        "Z": 0x5A,
        "1": 0x31,
        "2": 0x32,
        "3": 0x33,
        "4": 0x34,
        "5": 0x35,
        "6": 0x36,
        "F1": 0x70,
        "F4": 0x73,
        "F11": 0x7A,
    }
    MOUSE_CODES = {"left": 0x01, "right": 0x02, "middle": 0x04}

    def __init__(self, base: Path, config: dict[str, Any], log: logging.Logger) -> None:
        self.base = base
        self.config = config
        self.log = log
        self.window = WindowController(config, log)
        self.active = threading.Event()
        self.shutdown = threading.Event()
        self.thread: threading.Thread | None = None
        self.session_dir: Path | None = None
        self.last_session_dir: Path | None = None
        self.started_monotonic = 0.0
        self.started_wall = ""
        self.event_count = 0
        self.frame_count = 0
        self.video_frame_count = 0
        self.video_file = ""
        self.video_output_size: list[int] = []
        self.session_client_size: list[int] = []
        self.capture_profile = "tank"
        self.capture_profile_label = "肉盾"
        self.hero_name = ""
        self.session_stamp = ""
        self.hero_ocr_attempts = 0
        self._hero_ocr_running = threading.Event()
        self._key_states = {name: False for name in self.KEY_CODES}
        self._mouse_states = {name: False for name in self.MOUSE_CODES}
        self._last_mouse: tuple[int, int] | None = None
        self._last_foreground: bool | None = None
        self.mode = "manual"
        self.diagnostic_handler: DiagnosticLogHandler | None = None
        self.state_provider: Any = None
        self.ensure_capture_folders()

    def capture_root(self) -> Path:
        output_name = str(self.config.get("capture", {}).get("output_dir", "combat_capture")).strip() or "combat_capture"
        return self.base / output_name

    def ensure_capture_folders(self) -> None:
        root = self.capture_root()
        for label in PROFILE_LABELS.values():
            try:
                folder = root / label
                folder.mkdir(parents=True, exist_ok=True)
                guide = folder / "放置采集文件到这里.txt"
                if not guide.exists():
                    guide.write_text(f"把需要作为【{label}】职业学习的完整实战采集文件夹放到这里，然后点击软件右侧“分析新增采集”。\n", encoding="utf-8")
            except OSError as exc:
                self.log.warning("无法创建%s采集分类目录：%s", label, exc)

    @property
    def is_active(self) -> bool:
        return self.active.is_set()

    def start(self, mode: str = "manual") -> bool:
        if self.active.is_set():
            return True
        if self.thread is not None and self.thread.is_alive():
            self.log.warning("上一段录像仍在保存，请稍后重试")
            return False
        self.mode = "autoplay" if mode == "autoplay" else "manual"
        win = self.window.validate_for_capture(require_foreground=False)
        if win is None:
            found = self.window.find()
            actual = f"{found.width}×{found.height}" if found is not None else "未找到窗口"
            allowed = self.config.get("capture", {}).get("supported_client_sizes", [[1024, 768], [1920, 1080], [2560, 1440]])
            self.log.warning("实战采集：窗口尺寸不受支持（当前：%s，允许：%s），未开始", actual, allowed)
            return False
        settings = self.config.get("capture", {})
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        category = str(self.config.get("entry", {}).get("hero_category", "survival"))
        self.capture_profile = PROFILE_BY_CATEGORY.get(category, "tank")
        self.capture_profile_label = PROFILE_LABELS[self.capture_profile]
        self.hero_name = ""
        self.hero_ocr_attempts = 0
        self._hero_ocr_running.clear()
        self.session_stamp = stamp
        category_root = (self.base / "autoplay_recordings" / self.capture_profile_label) if self.mode == "autoplay" else self.capture_root() / self.capture_profile_label
        category_root.mkdir(parents=True, exist_ok=True)
        session = category_root / f"实战采集_{self.capture_profile_label}-待识别_{stamp}"
        suffix = 1
        while session.exists():
            session = category_root / f"实战采集_{self.capture_profile_label}-待识别_{stamp}_{suffix:02d}"
            suffix += 1
        try:
            (session / "frames").mkdir(parents=True, exist_ok=False)
            (session / "config_snapshot.json").write_text(
                json.dumps(self.config, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            (session / "采集说明.txt").write_text(
                "本目录由梦三国2巅峰争霸脚本实战采集模式生成。\n"
                "支持 1024×768、1920×1080 与 2560×1440 游戏客户区，自动记录本局实际尺寸。\n"
                "gameplay.mp4：软件同步录制的游戏窗口视频，不包含桌面其他区域。\n"
                "actions.jsonl：带毫秒时间戳的战斗键、鼠标原始坐标、归一化坐标和 1024×768 换算坐标。\n"
                "frames：游戏客户区原始分辨率校准截图。将整个采集目录压缩发送即可。\n",
                encoding="utf-8",
            )
        except Exception as exc:
            self.log.error("实战采集：创建记录目录失败：%s", exc)
            return False
        self.session_dir = session
        self.last_session_dir = session
        self.started_monotonic = time.monotonic()
        if self.mode == "autoplay":
            self.diagnostic_handler = DiagnosticLogHandler(session / "decisions.jsonl", self.started_monotonic)
            self.log.addHandler(self.diagnostic_handler)
        self.started_wall = datetime.now().astimezone().isoformat(timespec="milliseconds")
        self.session_client_size = [win.width, win.height]
        self.event_count = 0
        self.frame_count = 0
        self.video_frame_count = 0
        self.video_file = "gameplay.mp4"
        self.video_output_size = []
        self._key_states = {name: False for name in self.KEY_CODES}
        self._mouse_states = {name: False for name in self.MOUSE_CODES}
        self._last_mouse = None
        self._last_foreground = None
        self.shutdown.clear()
        self._write_metadata("", 0)
        self.active.set()
        self.thread = threading.Thread(target=self._run, daemon=True, name="CombatRecorder")
        self.thread.start()
        self.log.warning("实战采集已开始：客户区=%sx%s，同步记录操作、截图和 MP4；目录=%s", win.width, win.height, session)
        return True

    def stop(self) -> None:
        if not self.active.is_set() and (self.thread is None or not self.thread.is_alive()):
            return
        self.active.clear()
        if self.thread is not None and self.thread.is_alive():
            self.thread.join(timeout=45.0)
        self.log.warning(
            "实战采集已结束：事件 %s 条，校准截图 %s 张，视频帧 %s；目录=%s",
            self.event_count,
            self.frame_count,
            self.video_frame_count,
            self.last_session_dir,
        )

    def close(self) -> None:
        self.shutdown.set()
        self.stop()

    def _elapsed_ms(self) -> int:
        return max(0, round((time.monotonic() - self.started_monotonic) * 1000))

    def _event(self, handle: Any, event_type: str, **payload: Any) -> None:
        record = {
            "t_ms": self._elapsed_ms(),
            "wall_time": datetime.now().astimezone().isoformat(timespec="milliseconds"),
            "type": event_type,
            **payload,
        }
        handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        handle.flush()
        self.event_count += 1

    def _relative_cursor(self, win: WindowInfo) -> tuple[int, int] | None:
        point = POINT()
        if not user32.GetCursorPos(ctypes.byref(point)):
            return None
        relative = (int(point.x - win.x), int(point.y - win.y))
        if 0 <= relative[0] < win.width and 0 <= relative[1] < win.height:
            return relative
        return None

    @staticmethod
    def _coordinate_payload(cursor: tuple[int, int], win: WindowInfo) -> dict[str, Any]:
        x, y = cursor
        nx = x / max(1, win.width - 1)
        ny = y / max(1, win.height - 1)
        return {
            "x": x,
            "y": y,
            "x_norm": round(nx, 6),
            "y_norm": round(ny, 6),
            "x_1024": min(1023, max(0, round(nx * 1023))),
            "y_768": min(767, max(0, round(ny * 767))),
            "client_width": win.width,
            "client_height": win.height,
        }

    def _release_held_states(self, handle: Any) -> None:
        for name, pressed in list(self._key_states.items()):
            if pressed:
                self._event(handle, "key", key=name, state="up", reason="game_lost_foreground")
                self._key_states[name] = False
        for name, pressed in list(self._mouse_states.items()):
            if pressed:
                self._event(handle, "mouse_button", button=name, state="up", reason="game_lost_foreground")
                self._mouse_states[name] = False
        self._last_mouse = None

    def _write_metadata(self, ended_wall: str, duration_ms: int) -> None:
        if self.session_dir is None:
            return
        metadata = {
            "format": "mengsanguo-combat-capture-v2",
            "app": APP_NAME,
            "started_wall_time": self.started_wall,
            "ended_wall_time": ended_wall,
            "duration_ms": duration_ms,
            "window_title_contains": self.config.get("window", {}).get("title_contains", []),
            "client_size": self.session_client_size,
            "reference_client_size": [1024, 768],
            "coordinate_fields": ["x", "y", "x_norm", "y_norm", "x_1024", "y_768"],
            "foreground_only": True,
            "allowed_keys": list(self.KEY_CODES),
            "recorded_mouse_buttons": list(self.MOUSE_CODES),
            "event_count": self.event_count,
            "frame_count": self.frame_count,
            "video_file": self.video_file,
            "video_frame_count": self.video_frame_count,
            "video_output_size": self.video_output_size,
            "video_quality": str(self.config.get("capture", {}).get("video_quality", "720p")),
            "nvidia_video_required": False,
            "learning_profile": self.capture_profile,
            "profession_label": self.capture_profile_label,
            "hero_name": self.hero_name,
            "recording_mode": self.mode,
            "eligible_for_learning": self.mode == "manual",
            "timeline": "t_ms is milliseconds since recording start; video is silent, foreground-only",
        }
        (self.session_dir / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _finalize_session_name(self) -> None:
        if self.session_dir is None:
            return
        hero = self.hero_name or "待识别"
        hero = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9_-]+", "", hero)[:12] or "待识别"
        prefix = "挂机录像" if self.mode == "autoplay" else "实战采集"
        desired = self.session_dir.parent / f"{prefix}_{self.capture_profile_label}-{hero}_{self.session_stamp}"
        suffix = 1
        while desired.exists() and desired != self.session_dir:
            desired = self.session_dir.parent / f"{prefix}_{self.capture_profile_label}-{hero}_{self.session_stamp}_{suffix:02d}"
            suffix += 1
        if desired == self.session_dir:
            self.last_session_dir = self.session_dir
            return
        try:
            self.session_dir.rename(desired)
            self.session_dir = desired
            self.last_session_dir = desired
            self.log.info("实战采集：已按职业和英雄命名：%s", desired.name)
        except OSError as exc:
            self.log.warning("实战采集：目录重命名失败，保留原名称：%s", exc)

    def _request_hero_ocr(self, image: Image.Image) -> None:
        if self.hero_name or self.hero_ocr_attempts >= 120 or self._hero_ocr_running.is_set():
            return
        self.hero_ocr_attempts += 1
        self._hero_ocr_running.set()
        snapshot = image.copy()

        def recognize() -> None:
            try:
                detected_name = recognize_hero_name(snapshot)
                if detected_name and not self.hero_name:
                    self.hero_name = detected_name
                    self.log.info("实战采集：识别英雄名为 %s，将用于保存前缀", detected_name)
            finally:
                self._hero_ocr_running.clear()

        threading.Thread(target=recognize, daemon=True, name="HeroNameOCR").start()

    def _recognize_hero_from_saved_frames(self) -> None:
        if self.hero_name or self.session_dir is None:
            return
        frames = sorted((self.session_dir / "frames").glob("*.jpg"))
        if not frames:
            return
        # The selected hero is often visible only near the beginning of a recording.
        # Scan these saved frames after recording, without interrupting play or pressing F1.
        indexes = list(dict.fromkeys(list(range(min(10, len(frames)))) + list(range(10, min(150, len(frames)), 3))))
        deadline = time.monotonic() + 22.0
        for index in indexes:
            if time.monotonic() > deadline:
                break
            try:
                with Image.open(frames[index]) as frame:
                    detected = recognize_hero_name(frame.convert("RGB"), selection=True)
            except Exception:
                detected = ""
            if detected:
                self.hero_name = detected
                self.log.info("实战采集：从选人后的金色姓名/左侧提示识别英雄名为 %s", detected)
                return
        indexes = [len(frames) - 1, max(0, len(frames) - 5), len(frames) // 2, len(frames) // 4]
        for index in dict.fromkeys(indexes):
            if time.monotonic() > deadline + 8.0:
                break
            try:
                detected = recognize_hero_name(Image.open(frames[index]).convert("RGB"))
            except Exception:
                detected = ""
            if detected:
                self.hero_name = detected
                self.log.info("实战采集：结束时从历史画面识别英雄名为 %s", detected)
                return

    def _open_video_encoder(self, settings: dict[str, Any]) -> tuple[subprocess.Popen[bytes] | None, float]:
        if self.session_dir is None or imageio_ffmpeg is None:
            self.video_file = ""
            self.log.error("实战录像：缺少内置 FFmpeg，操作数据仍会继续采集")
            return None, 0.0
        fps = min(15.0, max(4.0, float(settings.get("video_fps", 8.0))))
        source_width, source_height = self.session_client_size
        quality = str(settings.get("video_quality", "720p")).lower()
        if quality == "original":
            width, height = source_width, source_height
        else:
            target_height = 1080 if quality == "1080p" else 720
            height = target_height
            width = round(source_width * target_height / max(1, source_height))
        width, height = max(2, int(width) // 2 * 2), max(2, int(height) // 2 * 2)
        self.video_output_size = [width, height]
        output_path = self.session_dir / self.video_file
        try:
            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
            command = [
                ffmpeg, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                "-s", f"{width}x{height}", "-r", f"{fps:g}", "-i", "-", "-an",
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", str(int(settings.get("video_crf", 24))),
                "-pix_fmt", "yuv420p", str(output_path),
            ]
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=flags,
            )
            self.log.info("实战录像：MP4 编码器已启动，输出=%sx%s @ %.1f FPS", width, height, fps)
            return process, fps
        except Exception as exc:
            self.video_file = ""
            self.video_output_size = []
            self.log.error("实战录像：启动编码器失败，操作数据仍会继续采集：%s", exc)
            return None, 0.0

    def _video_bytes(self, image: Image.Image, cursor: tuple[int, int] | None) -> bytes:
        frame = image.convert("RGB")
        if cursor is not None and self.config.get("capture", {}).get("video_cursor_overlay", True):
            frame = frame.copy()
            draw = ImageDraw.Draw(frame)
            x, y = cursor
            radius = max(7, round(frame.width / 180))
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), outline=(255, 230, 40), width=max(2, radius // 3))
            draw.line((x - radius * 2, y, x + radius * 2, y), fill=(255, 60, 40), width=2)
            draw.line((x, y - radius * 2, x, y + radius * 2), fill=(255, 60, 40), width=2)
        if list(frame.size) != self.video_output_size:
            frame = frame.resize(tuple(self.video_output_size), Image.Resampling.BILINEAR)
        return frame.tobytes()

    def _run(self) -> None:
        if self.session_dir is None:
            self.active.clear()
            return
        settings = self.config.get("capture", {})
        is_2k = self.session_client_size[0] >= 2500
        poll_interval = min(0.10, max(0.008, float(settings.get("poll_interval", 0.02))))
        frame_interval = max(0.25, float(settings.get("frame_interval_2k" if is_2k else "frame_interval", 2.0 if is_2k else 1.0)))
        min_move = max(1, int(settings.get("mouse_move_min_pixels", 3)))
        jpeg_quality = min(95, max(45, int(settings.get("jpeg_quality_2k" if is_2k else "jpeg_quality", 76 if is_2k else 82))))
        video_process, video_fps = self._open_video_encoder(settings)
        video_period = 1.0 / video_fps if video_fps > 0 else 0.0
        next_video = 0.0
        next_frame = 0.0
        next_diagnostic = 0.0
        next_disk_check = 0.0
        next_hero_ocr = 30.0
        last_video_bytes: bytes | None = None
        actions_path = self.session_dir / "actions.jsonl"
        try:
            with actions_path.open("a", encoding="utf-8", buffering=1) as handle:
                self._event(handle, "session_start", client_size=self.session_client_size, reference_client_size=[1024, 768], video_file=self.video_file)
                while self.active.is_set() and not self.shutdown.is_set():
                    loop_started = time.monotonic()
                    elapsed = loop_started - self.started_monotonic
                    if self.mode == "autoplay":
                        if elapsed >= max(1.0, float(settings.get("autoplay_max_minutes", 60))) * 60:
                            self.log.warning("挂机录像达到时长上限，仅停止录像，挂机继续")
                            break
                        if elapsed >= next_disk_check:
                            if shutil.disk_usage(self.session_dir).free < 512*1024*1024:
                                self.log.warning("剩余空间不足512MB，仅停止录像，挂机继续")
                                break
                            next_disk_check = elapsed + 10.0
                    if self.mode == "autoplay" and elapsed >= next_diagnostic and self.state_provider is not None:
                        self._event(handle, "bot_state", **self.state_provider())
                        next_diagnostic = elapsed + 1.0
                    win = self.window.validate_for_capture(require_foreground=False)
                    if win is not None and [win.width, win.height] != self.session_client_size:
                        self._event(handle, "window_size_changed", expected=self.session_client_size, actual=[win.width, win.height])
                        win = None
                    foreground = bool(win is not None and self.window.is_foreground(win))
                    if foreground != self._last_foreground:
                        self._event(handle, "game_foreground", active=foreground)
                        if not foreground:
                            self._release_held_states(handle)
                        self._last_foreground = foreground
                    cursor: tuple[int, int] | None = None
                    captured_image: Image.Image | None = None
                    if foreground and win is not None:
                        cursor = self._relative_cursor(win)
                        if cursor is not None and (self._last_mouse is None or abs(cursor[0] - self._last_mouse[0]) >= min_move or abs(cursor[1] - self._last_mouse[1]) >= min_move):
                            self._event(handle, "mouse_move", **self._coordinate_payload(cursor, win))
                            self._last_mouse = cursor
                        for name, vk in self.KEY_CODES.items():
                            pressed = bool(user32.GetAsyncKeyState(vk) & 0x8000)
                            if pressed != self._key_states[name]:
                                self._event(handle, "key", key=name, state="down" if pressed else "up")
                                self._key_states[name] = pressed
                        for name, vk in self.MOUSE_CODES.items():
                            pressed = bool(user32.GetAsyncKeyState(vk) & 0x8000)
                            if pressed != self._mouse_states[name]:
                                payload: dict[str, Any] = {"button": name, "state": "down" if pressed else "up"}
                                if cursor is not None:
                                    payload.update(self._coordinate_payload(cursor, win))
                                self._event(handle, "mouse_button", **payload)
                                self._mouse_states[name] = pressed
                        if elapsed >= next_video or elapsed >= next_frame:
                            captured_image = self.window.grab_capture_window(win)
                        if captured_image is not None and elapsed >= next_frame:
                            frame_name = f"frame_{self.frame_count:06d}_{self._elapsed_ms():010d}.jpg"
                            captured_image.convert("RGB").save(self.session_dir / "frames" / frame_name, "JPEG", quality=jpeg_quality, optimize=False)
                            self._event(handle, "frame", file=f"frames/{frame_name}", width=win.width, height=win.height)
                            self.frame_count += 1
                            next_frame = elapsed + frame_interval
                        if captured_image is not None and video_process is not None:
                            last_video_bytes = self._video_bytes(captured_image, cursor)
                    if video_process is not None and elapsed >= next_video:
                        if last_video_bytes is not None and video_process.stdin is not None:
                            try:
                                due = max(1, min(int(video_fps*2), int(elapsed*video_fps)+1-self.video_frame_count))
                                video_process.stdin.write(last_video_bytes * due)
                                first_frame = self.video_frame_count
                                self.video_frame_count += due
                                if self.mode == "autoplay":
                                    self._event(handle, "video_frames", first_frame=first_frame, last_frame=self.video_frame_count-1)
                            except (BrokenPipeError, OSError) as exc:
                                self._event(handle, "video_error", message=str(exc))
                                video_process = None
                        next_video = elapsed + video_period
                    remaining = poll_interval - (time.monotonic() - loop_started)
                    if remaining > 0:
                        time.sleep(remaining)
                self._release_held_states(handle)
                self._event(handle, "session_stop")
        except Exception:
            self.log.error("实战采集异常：\n%s", traceback.format_exc())
        finally:
            if video_process is not None:
                try:
                    if video_process.stdin is not None:
                        video_process.stdin.close()
                    video_process.wait(timeout=10.0)
                except Exception:
                    video_process.kill()
            ended = datetime.now().astimezone().isoformat(timespec="milliseconds")
            duration = self._elapsed_ms()
            wait_deadline = time.monotonic() + 2.0
            while self._hero_ocr_running.is_set() and time.monotonic() < wait_deadline:
                time.sleep(0.05)
            self._recognize_hero_from_saved_frames()
            if self.diagnostic_handler is not None:
                self.log.removeHandler(self.diagnostic_handler)
                self.diagnostic_handler.close()
                self.diagnostic_handler = None
            try:
                self._write_metadata(ended, duration)
            except Exception as exc:
                self.log.error("实战采集：写入元数据失败：%s", exc)
            self._finalize_session_name()
            self.active.clear()


class TemplateDetector:
    def __init__(self, base: Path, config: dict[str, Any], log: logging.Logger) -> None:
        self.base = base
        self.config = config
        self.log = log
        self.templates: dict[str, np.ndarray] = {}
        self.last_matches: dict[str, tuple[list[int], float] | None] = {}
        self.missing_reported: set[str] = set()
        self.reload()

    def reload(self) -> None:
        self.templates.clear()
        self.last_matches.clear()
        self.missing_reported.clear()
        template_dir = self.base / self.config.get("template_dir", "templates")
        for name, spec in self.config["detectors"].items():
            path = template_dir / spec["file"]
            if not path.is_file():
                continue
            try:
                image = Image.open(path).convert("L")
                template_crop = spec.get("template_crop")
                if template_crop:
                    left, top, right, bottom = [int(value) for value in template_crop]
                    if left < 0 or top < 0 or right <= left or bottom <= top or right > image.width or bottom > image.height:
                        raise ValueError(f"template_crop 越界：{template_crop}，图片尺寸={image.size}")
                    image = image.crop((left, top, right, bottom))
                self.templates[name] = np.asarray(image, dtype=np.float32)
            except Exception as exc:
                self.log.error("模板加载失败 %s：%s", path.name, exc)
        self.log.info("已加载 %s/%s 个状态模板", len(self.templates), len(self.config["detectors"]))

    def score(self, name: str, screenshot: Image.Image) -> float | None:
        spec = self.config["detectors"].get(name)
        template = self.templates.get(name)
        if not spec or template is None:
            if name not in self.missing_reported:
                self.log.warning("缺少模板：%s（%s）", name, spec["file"] if spec else "未配置")
                self.missing_reported.add(name)
            return None
        x, y, w, h = spec["region"]
        if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > screenshot.width or y + h > screenshot.height:
            self.log.error("检测区域越界：%s region=%s", name, spec["region"])
            return None
        crop = screenshot.crop((x, y, x + w, y + h)).convert("L")
        target_h, target_w = template.shape
        if crop.size != (target_w, target_h):
            crop = crop.resize((target_w, target_h), Image.Resampling.BILINEAR)
        actual = np.asarray(crop, dtype=np.float32)
        return float(1.0 - np.mean(np.abs(actual - template)) / 255.0)

    def matches(self, name: str, screenshot: Image.Image) -> bool:
        located = self.locate(name, screenshot)
        self.last_matches[name] = located
        if located is None:
            return False
        threshold = float(self.config["detectors"][name].get("threshold", 0.90))
        return located[1] >= threshold

    def locate(self, name: str, screenshot: Image.Image) -> tuple[list[int], float] | None:
        spec = self.config["detectors"].get(name)
        template = self.templates.get(name)
        if not spec or template is None:
            if name not in self.missing_reported:
                self.log.warning("缺少模板：%s（%s）", name, spec["file"] if spec else "未配置")
                self.missing_reported.add(name)
            return None
        screen = np.asarray(screenshot.convert("L"), dtype=np.float32)
        template_h, template_w = template.shape
        search = spec.get("search_region", [0, 0, screenshot.width, screenshot.height])
        sx, sy, sw, sh = [int(value) for value in search]
        if sx < 0 or sy < 0 or sw <= 0 or sh <= 0 or sx + sw > screenshot.width or sy + sh > screenshot.height:
            self.log.error("模板搜索区域越界：%s search_region=%s", name, search)
            return None
        area = screen[sy : sy + sh, sx : sx + sw]
        if template_w > sw or template_h > sh:
            self.log.error("模板大于搜索区域：%s", name)
            return None

        stride = max(1, int(spec.get("search_stride", 3)))
        candidate_y = np.arange(0, sh - template_h + 1, stride, dtype=np.int32)
        candidate_x = np.arange(0, sw - template_w + 1, stride, dtype=np.int32)
        if candidate_y.size == 0 or candidate_x.size == 0:
            return None
        sample_rows = np.unique(np.linspace(0, template_h - 1, min(7, template_h), dtype=np.int32))
        sample_cols = np.unique(np.linspace(0, template_w - 1, min(9, template_w), dtype=np.int32))
        errors = np.zeros((candidate_y.size, candidate_x.size), dtype=np.float32)
        for ty in sample_rows:
            rows = ty + candidate_y[:, None]
            for tx in sample_cols:
                cols = tx + candidate_x[None, :]
                errors += np.abs(area[rows, cols] - template[ty, tx])
        best_score = -1.0
        best_x, best_y = 0, 0
        flat_errors = errors.ravel()
        refine_count = min(max(1, int(spec.get("refine_candidates", 16))), flat_errors.size)
        coarse_candidates = np.argpartition(flat_errors, refine_count - 1)[:refine_count]
        checked: set[tuple[int, int]] = set()
        for flat_index in coarse_candidates:
            coarse_index = np.unravel_index(int(flat_index), errors.shape)
            coarse_y = int(candidate_y[coarse_index[0]])
            coarse_x = int(candidate_x[coarse_index[1]])
            for yy in range(max(0, coarse_y - stride), min(sh - template_h, coarse_y + stride) + 1):
                for xx in range(max(0, coarse_x - stride), min(sw - template_w, coarse_x + stride) + 1):
                    if (xx, yy) in checked:
                        continue
                    checked.add((xx, yy))
                    patch = area[yy : yy + template_h, xx : xx + template_w]
                    score = float(1.0 - np.mean(np.abs(patch - template)) / 255.0)
                    if score > best_score:
                        best_score = score
                        best_x, best_y = xx, yy
        center = [sx + best_x + template_w // 2, sy + best_y + template_h // 2]
        return center, best_score


class BattleStatsReader:
    def __init__(self, base: Path, config: dict[str, Any], log: logging.Logger) -> None:
        self.config = config
        self.log = log
        self.templates: dict[str, list[np.ndarray]] = {str(value): [] for value in range(10)}
        digit_dir = base / config.get("template_dir", "templates") / "digits"
        for path in sorted(digit_dir.glob("*.png")):
            digit = path.stem[:1]
            if digit not in self.templates:
                continue
            try:
                image = Image.open(path).convert("L").resize((18, 28), Image.Resampling.BILINEAR)
                self.templates[digit].append(np.asarray(image, dtype=np.float32) / 255.0)
            except Exception as exc:
                self.log.warning("战绩数字模板加载失败 %s：%s", path.name, exc)
        loaded = sum(len(items) for items in self.templates.values())
        self.log.info("已加载 %s 个战绩数字模板", loaded)

    @staticmethod
    def _color_mask(image: Image.Image, kind: str) -> np.ndarray:
        rgb = np.asarray(image.convert("RGB"))
        if kind == "white":
            return (rgb[:, :, 0] > 145) & (rgb[:, :, 1] > 145) & (rgb[:, :, 2] > 145)
        return (rgb[:, :, 0] > 150) & (rgb[:, :, 1] > 85) & (rgb[:, :, 1] < 225) & (rgb[:, :, 2] < 120)

    @staticmethod
    def _normalize(mask: np.ndarray) -> np.ndarray | None:
        mask = mask.copy()
        mask[:, mask.sum(axis=0) < 2] = False
        mask[mask.sum(axis=1) < 2, :] = False
        ys, xs = np.where(mask)
        if not len(xs):
            return None
        crop = (mask[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1] * 255).astype("uint8")
        resized = Image.fromarray(crop).resize((18, 28), Image.Resampling.BILINEAR)
        return np.asarray(resized, dtype=np.float32) / 255.0

    def _classify(self, item: np.ndarray) -> tuple[str, float]:
        scores: dict[str, float] = {}
        for digit, variants in self.templates.items():
            if variants:
                scores[digit] = max(1.0 - float(np.mean(np.abs(item - template))) for template in variants)
        if not scores:
            return "", 0.0
        best = max(scores, key=scores.get)
        return best, scores[best]

    def _read_digits(self, image: Image.Image, box: tuple[int, int, int, int], kind: str) -> tuple[str, float]:
        left, top, right, bottom = box
        left, top = max(0, left), max(0, top)
        right, bottom = min(image.width, right), min(image.height, bottom)
        if right <= left or bottom <= top:
            return "", 0.0
        mask = self._color_mask(image.crop((left, top, right, bottom)), kind)
        columns = np.where(mask.sum(axis=0) >= 2)[0]
        runs: list[list[int]] = []
        max_gap = 2 if kind == "white" else 1
        for column in columns:
            value = int(column)
            if not runs or value - runs[-1][-1] > max_gap:
                runs.append([value])
            else:
                runs[-1].append(value)
        result: list[str] = []
        confidences: list[float] = []
        minimum_width = 1 if kind == "white" else 3
        for run in runs:
            if run[-1] - run[0] < minimum_width:
                continue
            item = self._normalize(mask[:, run[0] : run[-1] + 1])
            if item is None:
                continue
            digit, confidence = self._classify(item)
            if digit:
                result.append(digit)
                confidences.append(confidence)
        return "".join(result), min(confidences, default=0.0)

    def read(self, screenshot: Image.Image, anchor: list[int]) -> dict[str, int] | None:
        if not all(self.templates.values()):
            return None
        ax, ay = int(anchor[0]), int(anchor[1])
        boxes = {
            "kills": (ax - 120, ay + 8, ax - 98, ay + 36),
            "deaths": (ax - 84, ay + 8, ax - 60, ay + 36),
            "assists": (ax - 52, ay + 8, ax - 20, ay + 36),
        }
        output: dict[str, int] = {}
        threshold = float(self.config.get("statistics", {}).get("digit_threshold", 0.68))
        for name, box in boxes.items():
            text, confidence = self._read_digits(screenshot, box, "white")
            if not text or confidence < threshold:
                return None
            output[name] = int(text)
        score_text, score_confidence = self._read_digits(screenshot, (ax + 22, ay - 9, screenshot.width, ay + 10), "gold")
        if not score_text or score_confidence < threshold:
            return None
        output["score"] = int(score_text[-6:])
        if any(value < 0 or value > 999 for key, value in output.items() if key != "score") or output["score"] > 999999:
            return None
        return output


class LocalStrategyLearner:
    """Small, explainable local learner; never uploads data or rewrites code."""

    PROFILE_DEFAULTS = {
        "tank": {"label": "肉盾", "chase": 0.85, "attack": 4.1, "follow": 3.2, "retreat": 3.8, "skill_scale": 1.00, "focus": 220, "isolated": 0},
        "mage": {"label": "法师", "chase": 1.05, "attack": 5.0, "follow": 2.5, "retreat": 2.4, "skill_scale": 0.78, "focus": 260, "isolated": 1},
        "physical": {"label": "物理", "chase": 0.60, "attack": 3.4, "follow": 3.0, "retreat": 3.0, "skill_scale": 0.92, "focus": 205, "isolated": 1},
    }

    def __init__(self, base: Path, config: dict[str, Any], log: logging.Logger) -> None:
        self.base, self.config, self.log = base, config, log
        self.path = base / "local_learning.json"
        self.data = self._load()
        migrated = self._migrate_capture_schema()
        self.profile = self._configured_profile()
        if migrated:
            self._save()

    def _migrate_capture_schema(self) -> bool:
        version = int(self.data.get("version", 1))
        if version >= 5:
            return False
        if self.path.is_file():
            backup = self.path.with_name("local_learning_before_4.4.json")
            if not backup.exists():
                shutil.copy2(self.path, backup)
        for state in self.data.get("profiles", {}).values():
            if isinstance(state, dict):
                state.pop("capture_samples", None)
                state.pop("capture_seconds", None)
                state.pop("capture_features", None)
                state.pop("capture_heroes", None)
                state.pop("shared_capture_samples", None)
                state.pop("capture_feature_samples", None)
                state.pop("capture_feature_values", None)
        self.data["processed_captures"] = {}
        self.data["version"] = 5
        self.log.warning("本地学习：升级4.4独立职业校准，旧档已备份；重新分析采集，保留战绩学习")
        return True

    def _configured_profile(self) -> str:
        configured = str(self.config.get("local_learning", {}).get("profile", "auto"))
        if configured in self.PROFILE_DEFAULTS:
            return configured
        category = str(self.config.get("entry", {}).get("hero_category", "survival"))
        return PROFILE_BY_CATEGORY.get(category, "tank")

    def _load(self) -> dict[str, Any]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                return value
        except FileNotFoundError:
            pass
        except Exception as exc:
            self.log.warning("本地学习：读取数据失败，使用新档案：%s", exc)
        return {"version": 5, "profiles": {}, "processed_captures": {}}

    def _state(self, profile: str | None = None) -> dict[str, Any]:
        key = profile or self.profile
        profiles = self.data.setdefault("profiles", {})
        return profiles.setdefault(key, {"samples": 0, "wins": 0, "reward_ema": 0.0, "adjustment": 0.0, "best_reward": -1.0, "best_adjustment": 0.0, "capture_samples": 0, "capture_features": {}})

    def _save(self) -> None:
        try:
            self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except Exception as exc:
            self.log.warning("本地学习：保存失败：%s", exc)

    def set_profile_from_category(self, category: str) -> None:
        self.profile = PROFILE_BY_CATEGORY.get(category, "tank")
        self.config.setdefault("local_learning", {})["profile"] = self.profile
        self.apply()

    def apply(self) -> None:
        settings = self.config.get("local_learning", {})
        if not settings.get("enabled", True):
            return
        defaults = self.PROFILE_DEFAULTS[self.profile]
        state = self._state()
        minimum = max(1, int(settings.get("min_samples", 3)))
        learned = float(state.get("adjustment", 0.0)) if int(state.get("samples", 0)) >= minimum else 0.0
        learned = max(-0.25, min(0.25, learned))
        timing = self.config.setdefault("timing", {})
        combat = self.config.setdefault("combat", {})
        capture_samples = max(0, int(state.get("capture_samples", 0)))
        capture_features = state.get("capture_features", {}) if isinstance(state.get("capture_features"), dict) else {}
        capture_minimum = max(1, int(settings.get("capture_min_samples", 2)))
        capture_weight = 0.0
        if capture_samples >= capture_minimum:
            capture_weight = min(float(settings.get("capture_max_weight", 0.35)), capture_samples * 0.08)

        def blended(name: str, fallback: float) -> float:
            observed = capture_features.get(name)
            # Input frequency includes tactical pauses, not just cooldowns.
            # Limit calibration instead of mistaking observed rhythm for optimal play.
            count = int(state.get("capture_feature_samples", {}).get(name, 0))
            limit = min(0.35, max(0.0, float(settings.get("capture_relative_limit", 0.20))))
            def bounded(value: float) -> float:
                return max(fallback * (1-limit), min(fallback * (1+limit), float(value)))
            own_weight = capture_weight if count >= capture_minimum and isinstance(observed, (int, float)) and np.isfinite(observed) else 0.0
            shared = state.get("shared_capture", {})
            shared_value = shared.get("capture_features", {}).get(name)
            shared_count = int(shared.get("capture_feature_samples", {}).get(name, 0))
            shared_weight = 0.0
            if self.profile == "tank" and settings.get("share_combat_captures_with_tank", True) and name in {"chase_interval", "attack_interval"} and shared_count >= capture_minimum and isinstance(shared_value, (int, float)) and np.isfinite(shared_value):
                shared_weight = min(0.15, shared_count * 0.02)
            own_weight = min(own_weight, max(0.0, 0.35-shared_weight))
            return fallback * (1-own_weight-shared_weight) + (bounded(observed)*own_weight if own_weight else 0.0) + (bounded(shared_value)*shared_weight if shared_weight else 0.0)

        chase_base = blended("chase_interval", defaults["chase"])
        attack_base = blended("attack_interval", defaults["attack"])
        timing["chase_click_interval"] = round(chase_base * (1.0 - 0.28 * learned), 2)
        timing["target_attack_interval"] = round(attack_base * (1.0 - 0.22 * learned), 2)
        timing["team_follow_interval"] = round(defaults["follow"] * (1.0 + 0.15 * learned), 2)
        timing["retreat_interval"] = round(defaults["retreat"] * (1.0 + 0.30 * learned), 2)
        base_skills = [10.0, 16.0, 22.0, 35.0]
        skill_bases: list[float] = []
        for index, value in enumerate(base_skills):
            fallback = value * defaults["skill_scale"]
            fallback = blended(f"skill_interval_{index}", fallback)
            skill_bases.append(fallback)
        timing["skill_slot_intervals"] = [round(v * (1.0 - 0.16 * learned), 2) for v in skill_bases]
        combat["friendly_focus_radius"] = int(defaults["focus"])
        combat["isolated_friendly_max"] = int(defaults["isolated"])
        self.log.info("本地校准：%s · %s段独立采集 · %s局战绩 · 采集权重%.0f%%（不等于已学会战术）", defaults["label"], capture_samples, state.get("samples", 0), capture_weight*100)

    def learn(self, record: dict[str, Any]) -> bool:
        settings = self.config.get("local_learning", {})
        if not settings.get("enabled", True) or record.get("learning_applied") or record.get("outcome") not in {"胜", "负"}:
            return False
        profile = self.profile
        state = self._state(profile)
        kills, deaths, assists = int(record.get("kills", 0)), int(record.get("deaths", 0)), int(record.get("assists", 0))
        score_cap = max(1, int(self.config.get("statistics", {}).get("score_cap", 1500)))
        score = min(score_cap, max(0, int(record.get("score", 0)))) / score_cap
        kda = min(3.0, (kills + assists * 0.55) / (deaths + 1.0)) / 3.0
        participation = kills + assists
        survival = 1.0 - min(1.0, deaths / 15.0)
        result = 1.0 if record["outcome"] == "胜" else -1.0
        if profile == "tank":
            # Tank play is judged primarily by real participation, not by
            # staying alive.  Dying with assists can be useful; repeated deaths
            # without kills/assists are explicitly penalized as ineffective
            # tower diving or non-participation.
            engagement = min(1.0, participation / 12.0)
            ineffective_deaths = max(0.0, deaths - participation * 0.75)
            waste = min(1.0, ineffective_deaths / 8.0)
            reward = result * 0.28 + score * 0.25 + engagement * 0.35 + kda * 0.12 - waste * 0.20 - 0.18
            if participation == 0:
                reward = min(reward, -0.35)
        else:
            reward = result * 0.42 + score * 0.28 + kda * 0.20 + survival * 0.10 - 0.30
        reward = max(-1.0, min(1.0, reward))
        samples = int(state.get("samples", 0)) + 1
        ema = reward if samples == 1 else float(state.get("reward_ema", 0.0)) * 0.78 + reward * 0.22
        rate = max(0.01, min(0.15, float(settings.get("learning_rate", 0.06))))
        adjustment = max(-0.25, min(0.25, float(state.get("adjustment", 0.0)) + rate * reward))
        state.update(samples=samples, wins=int(state.get("wins", 0)) + (1 if record["outcome"] == "胜" else 0), reward_ema=round(ema, 5), adjustment=round(adjustment, 5))
        history = state.setdefault("reward_history", [])
        history.append({
            "reward": round(reward, 4), "kills": kills, "deaths": deaths,
            "assists": assists, "score": int(record.get("score", 0)),
            "outcome": record["outcome"],
        })
        del history[:-20]
        if ema > float(state.get("best_reward", -1.0)):
            state["best_reward"], state["best_adjustment"] = round(ema, 5), round(adjustment, 5)
        elif samples >= 6 and ema < float(state.get("best_reward", -1.0)) - 0.25:
            state["adjustment"] = float(state.get("best_adjustment", 0.0))
            self.log.warning("本地学习：近期表现下降，%s策略已回滚到最佳参数", self.PROFILE_DEFAULTS[profile]["label"])
        record["learning_applied"] = True
        record["learning_profile"] = profile
        record["learning_reward"] = round(reward, 4)
        self._save()
        self.apply()
        self.log.info("本地学习：%s第 %s 局，评价 %+.3f，胜率 %.1f%%", self.PROFILE_DEFAULTS[profile]["label"], samples, reward, state["wins"] * 100.0 / samples)
        return True

    @staticmethod
    def _bounded_median(values: list[float], minimum: float, maximum: float) -> float | None:
        valid = [value for value in values if minimum <= value <= maximum]
        if not valid:
            return None
        return round(float(np.median(np.asarray(valid, dtype=np.float32))), 3)

    @staticmethod
    def _intervals(times: list[float]) -> list[float]:
        return [later - earlier for earlier, later in zip(times, times[1:]) if later > earlier]

    def _capture_fingerprint(self, metadata_path: Path, actions_path: Path) -> str:
        digest = hashlib.sha256()
        # Naming/editing metadata or moving a session must not train it twice.
        with actions_path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024*1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _capture_roots(self) -> list[Path]:
        output_name = str(self.config.get("capture", {}).get("output_dir", "combat_capture")).strip() or "combat_capture"
        candidates = [self.base / output_name]
        try:
            for sibling in self.base.parent.glob("梦三国2巅峰争霸脚本 *"):
                if sibling.is_dir():
                    candidates.append(sibling / output_name)
        except OSError:
            pass
        unique: list[Path] = []
        seen: set[str] = set()
        for candidate in candidates:
            key = str(candidate.resolve()).lower() if candidate.exists() else str(candidate.absolute()).lower()
            if key not in seen:
                seen.add(key)
                unique.append(candidate)
        return unique

    @staticmethod
    def _capture_click_region(event: dict[str, Any], metadata: dict[str, Any], snapshot: dict[str, Any]) -> str:
        width, height = metadata.get("client_size", [1024, 768])[:2]
        width, height = max(1, int(width)), max(1, int(height))
        x, y = event.get("x"), event.get("y")
        if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
            nx, ny = event.get("x_norm"), event.get("y_norm")
            if not isinstance(nx, (int, float)) or not isinstance(ny, (int, float)):
                return "unknown"
            x, y = nx*(width-1), ny*(height-1)
        # Classic HUD stays bottom-centred at higher recording resolutions;
        # it does not stretch proportionally across the whole 2K image.
        dx, dy = max(0, (width-1024)/2), max(0, height-768)
        mx, my, mw, mh = snapshot.get("coordinates", {}).get("minimap_region", [8,608,158,154])
        if dx+mx <= x < dx+mx+mw and dy+my <= y < dy+my+mh:
            return "minimap"
        if 130 <= x <= width-54 and 70 <= y < height-180:
            return "ground"
        return "hud"

    def _analyze_capture(self, session: Path, folder_profile: str = "") -> dict[str, Any] | None:
        metadata_path = session / "metadata.json"
        actions_path = session / "actions.jsonl"
        if not metadata_path.is_file() or not actions_path.is_file():
            return None
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if metadata.get("recording_mode") == "autoplay" or metadata.get("eligible_for_learning") is False:
            return None
        duration = max(1.0, float(metadata.get("duration_ms", 0)) / 1000.0)
        if duration < 30.0:
            return None
        snapshot: dict[str, Any] = {}
        snapshot_path = session / "config_snapshot.json"
        if snapshot_path.is_file():
            try:
                snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
            except Exception:
                snapshot = {}
        profile = str(metadata.get("learning_profile", ""))
        if profile not in self.PROFILE_DEFAULTS:
            category = str(snapshot.get("entry", {}).get("hero_category", ""))
            profile = PROFILE_BY_CATEGORY.get(category, "")
        if folder_profile in self.PROFILE_DEFAULTS:
            profile = folder_profile
        if profile not in self.PROFILE_DEFAULTS:
            return None
        hero_name = str(metadata.get("hero_name", "")).strip()
        if not hero_name:
            folder_match = re.search(r"_(肉盾|法师|物理)-([^_]+)_", session.name)
            if folder_match and folder_match.group(2) != "待识别":
                hero_name = folder_match.group(2)
        if not hero_name:
            frames = sorted((session / "frames").glob("*.jpg")) if (session / "frames").is_dir() else []
            probes = []
            if frames:
                probes = [frames[0], frames[len(frames) // 2], frames[-1]]
            for frame_path in dict.fromkeys(probes):
                try:
                    hero_name = recognize_hero_name(Image.open(frame_path).convert("RGB"))
                except Exception:
                    hero_name = ""
                if hero_name:
                    break
        skill_keys = [str(key).upper() for key in snapshot.get("combat", {}).get("skill_keys", self.config.get("combat", {}).get("skill_keys", ["q", "w", "e", "r"]))]
        skill_times: dict[str, list[float]] = {key: [] for key in skill_keys}
        key_times: dict[str, list[float]] = {}
        right_clicks: list[float] = []
        left_clicks = 0
        minimap_clicks = 0
        foreground_seconds = 0.0
        foreground_started: float | None = None
        with actions_path.open("r", encoding="utf-8") as handle:
            for raw in handle:
                try:
                    event = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    continue
                timestamp = max(0.0, float(event.get("t_ms", 0))) / 1000.0
                kind = event.get("type")
                if kind == "game_foreground":
                    if bool(event.get("active")):
                        if foreground_started is not None:
                            continue
                        foreground_started = timestamp
                    elif foreground_started is not None:
                        foreground_seconds += max(0.0, timestamp - foreground_started)
                        foreground_started = None
                elif kind == "key" and event.get("state") == "down":
                    key = str(event.get("key", "")).upper()
                    key_times.setdefault(key, []).append(timestamp)
                    if key in skill_times:
                        skill_times[key].append(timestamp)
                elif kind == "mouse_button" and event.get("state") == "down":
                    button = str(event.get("button", ""))
                    click_region = self._capture_click_region(event, metadata, snapshot)
                    if click_region == "minimap":
                        minimap_clicks += 1
                    if button == "right":
                        # Exclude minimap and HUD/equipment clicks from ground rhythm.
                        if click_region == "ground":
                            right_clicks.append(timestamp)
                    elif button == "left":
                        left_clicks += 1
        if foreground_started is not None:
            foreground_seconds += max(0.0, duration - foreground_started)
        if foreground_seconds < 30.0:
            return None
        active_duration = foreground_seconds
        minutes = max(0.5, active_duration / 60.0)
        action_count = len(right_clicks) + left_clicks + sum(len(value) for value in key_times.values())
        if action_count < 12:
            return None
        chase_values = self._intervals(right_clicks)
        attack_values = self._intervals(key_times.get("A", []))
        chase = self._bounded_median(chase_values, 0.18, 3.0) if len([v for v in chase_values if .18 <= v <= 3.0]) >= 5 else None
        attack = self._bounded_median(attack_values, 0.5, 12.0) if len([v for v in attack_values if .5 <= v <= 12.0]) >= 5 else None
        skill_intervals: list[float | None] = []
        for key in skill_keys[:4]:
            intervals = self._intervals(skill_times.get(key, []))
            skill_intervals.append(self._bounded_median(intervals, 2.0, 120.0) if len([v for v in intervals if 2.0 <= v <= 120.0]) >= 5 else None)
        return {
            "fingerprint": self._capture_fingerprint(metadata_path, actions_path),
            "learning_profile": profile,
            "hero_name": hero_name,
            "duration_seconds": round(active_duration, 1),
            "chase_interval": chase,
            "attack_interval": attack,
            "skill_intervals": skill_intervals,
            "right_clicks_per_minute": round(len(right_clicks) / minutes, 2),
            "skills_per_minute": round(sum(len(value) for value in skill_times.values()) / minutes, 2),
            "items_per_minute": round(sum(len(key_times.get(str(index), [])) for index in range(1, 7)) / minutes, 2),
            "minimap_clicks_per_minute": round(minimap_clicks / minutes, 2),
            "f1_per_minute": round(len(key_times.get("F1", [])) / minutes, 2),
        }

    def _merge_capture_features(self, state: dict[str, Any], features: dict[str, Any]) -> None:
        old_count = max(0, int(state.get("capture_samples", 0)))
        learned = state.setdefault("capture_features", {})
        counts = state.setdefault("capture_feature_samples", {})
        observed_values = state.setdefault("capture_feature_values", {})

        def merge_scalar(name: str, value: Any) -> None:
            if not isinstance(value, (int, float)) or not np.isfinite(value):
                return
            counts[name] = int(counts.get(name, 0)) + 1
            values = observed_values.setdefault(name, [])
            values.append(float(value))
            del values[:-200]
            learned[name] = round(float(np.median(values)), 4)

        for name in ("chase_interval", "attack_interval", "right_clicks_per_minute", "skills_per_minute", "items_per_minute", "minimap_clicks_per_minute", "f1_per_minute"):
            merge_scalar(name, features.get(name))
        old_skills = learned.get("skill_intervals", [None, None, None, None])
        new_skills = features.get("skill_intervals", [])
        merged_skills: list[float | None] = []
        for index in range(4):
            previous = old_skills[index] if isinstance(old_skills, list) and index < len(old_skills) else None
            value = new_skills[index] if isinstance(new_skills, list) and index < len(new_skills) else None
            if isinstance(value, (int, float)) and np.isfinite(value):
                slot_name = f"skill_interval_{index}"
                merge_scalar(slot_name, value)
                merged_skills.append(learned[slot_name])
            else:
                merged_skills.append(previous if isinstance(previous, (int, float)) else None)
        learned["skill_intervals"] = merged_skills
        state["capture_samples"] = old_count + 1
        state["capture_seconds"] = round(float(state.get("capture_seconds", 0.0)) + float(features.get("duration_seconds", 0.0)), 1)

    def analyze_new_captures(self) -> dict[str, Any]:
        processed = self.data.setdefault("processed_captures", {})
        added = skipped = invalid = 0
        added_by_profile = {"tank": 0, "mage": 0, "physical": 0}
        scanned_paths: set[str] = set()
        for root in self._capture_roots():
            if not root.is_dir():
                continue
            sessions = []
            for label, profile in PROFILE_BY_LABEL.items():
                folder = root / label
                if folder.is_dir():
                    sessions.extend((item, profile) for item in sorted(folder.iterdir()) if item.is_dir())
            sessions.extend((item, "") for item in sorted(root.iterdir()) if item.is_dir() and item.name not in PROFILE_BY_LABEL)
            for session, folder_profile in sessions:
                if not session.is_dir():
                    continue
                session_key = str(session.resolve()).lower()
                if session_key in scanned_paths:
                    continue
                scanned_paths.add(session_key)
                try:
                    actions_path, metadata_path = session / "actions.jsonl", session / "metadata.json"
                    if actions_path.is_file() and metadata_path.is_file() and self._capture_fingerprint(metadata_path, actions_path) in processed:
                        skipped += 1
                        continue
                    features = self._analyze_capture(session, folder_profile)
                    if features is None:
                        invalid += 1
                        continue
                    fingerprint = str(features.pop("fingerprint"))
                    if fingerprint in processed:
                        skipped += 1
                        continue
                    profile = str(features.pop("learning_profile", self.profile))
                    if profile not in self.PROFILE_DEFAULTS:
                        profile = self.profile
                    label = self.PROFILE_DEFAULTS[profile]["label"]
                    hero_name = str(features.pop("hero_name", "")).strip()
                    state = self._state(profile)
                    self._merge_capture_features(state, features)
                    if profile != "tank":
                        # Share universal movement/attack rhythm only. Hero-specific
                        # skill slots, cooldowns and outcome rewards stay independent.
                        shared = self._state("tank").setdefault("shared_capture", {})
                        self._merge_capture_features(shared, {key: features.get(key) for key in ("chase_interval", "attack_interval", "duration_seconds")})
                    heroes = state.setdefault("capture_heroes", {})
                    if hero_name:
                        heroes[hero_name] = int(heroes.get(hero_name, 0)) + 1
                    processed[fingerprint] = {"profile": profile, "hero": hero_name, "session": session.name, "analyzed_at": datetime.now().astimezone().isoformat(timespec="seconds")}
                    added += 1
                    added_by_profile[profile] += 1
                    display_name = f"{label}-{hero_name}" if hero_name else f"{label}-未识别英雄"
                    self.log.info("采集学习：已分析 %s → %s策略", session.name, display_name)
                except Exception as exc:
                    invalid += 1
                    self.log.warning("采集学习：跳过 %s：%s", session.name, exc)
        if added:
            self.data["version"] = max(5, int(self.data.get("version", 1)))
            self._save()
            self.apply()
        totals = {key: int(self._state(key).get("capture_samples", 0)) for key in self.PROFILE_DEFAULTS}
        breakdown = "、".join(f"{PROFILE_LABELS[key]}{value}" for key, value in added_by_profile.items() if value) or "无新增"
        self.log.info("采集学习完成：%s；重复 %s，无效 %s", breakdown, skipped, invalid)
        return {"added": added, "added_by_profile": added_by_profile, "totals": totals, "skipped": skipped, "invalid": invalid}

    def summary(self) -> str:
        state = self._state()
        samples, wins = int(state.get("samples", 0)), int(state.get("wins", 0))
        captures = int(state.get("capture_samples", 0))
        label = self.PROFILE_DEFAULTS[self.profile]["label"]
        result_text = f"{samples}战绩/胜率{wins * 100 / samples:.0f}%" if samples else "0战绩"
        shared_count = int(state.get("shared_capture", {}).get("capture_samples", 0)) if self.profile == "tank" else 0
        shared_text = f" + {shared_count}跨职业参考" if shared_count else ""
        return f"{label}：{captures}独立采集{shared_text}\n{result_text}"


class BotEngine:
    def __init__(self, base: Path, config: dict[str, Any], log: logging.Logger) -> None:
        self.base = base
        self.config = config
        self.log = log
        self.window = WindowController(config, log)
        self.detector = TemplateDetector(base, config, log)
        self.stats_reader = BattleStatsReader(base, config, log)
        self.learner = LocalStrategyLearner(base, config, log)
        self.learner.apply()
        self.battle_history = self._load_battle_history()
        self.current_battle_stats: dict[str, int] | None = None
        self.stats_revision = 0
        self.round_stats_saved = False
        self.round_completion_recorded = False
        self.completed_rounds = 0
        self.last_stats_sample = 0.0
        self.active = threading.Event()
        self.shutdown = threading.Event()
        self.permanently_stopped = False
        self.state = "已暂停"
        self.last_action = 0.0
        self.last_move = 0.0
        self.last_skill = 0.0
        self.last_skill_use: dict[str, float] = {}
        self.next_skill_slot = 0
        self.last_chase_click = 0.0
        self.last_target_attack = 0.0
        self.no_enemy_since = 0.0
        self.last_teleport_attempt = 0.0
        self.last_boss_observe = 0.0
        self.last_tower_support = 0.0
        self.last_tactical_move = 0.0
        self.last_group_follow = 0.0
        self.last_retreat = 0.0
        self.self_selected = False
        self.enemy_track_memory: list[dict[str, float]] = []
        self.last_target_reason = ""
        self.last_target_friendly_count = 0
        self.last_summon = 0.0
        self.hero_selected = False
        self.hero_select_seen_at = 0.0
        self.hero_wait_logged = False
        self.hero_session_active = False
        self.talent_selected = False
        self.talent_panel_seen = False
        self.talent_wait_logged = False
        self.talent_click_pending_at = 0.0
        # Only a hero/talent screen observed by this process may arm the
        # opening shop workflow.  Starting or resetting the bot mid-match must
        # attach directly to combat instead of replaying start-of-match steps.
        self.opening_sequence_seen = False
        self.shop_configured = False
        self.shop_scheme_selected = False
        self.last_shop_attempt = 0.0
        self.shop_confirmations = 0
        self.card_panel_handled = False
        self.card_flips_done = False
        self.card_panel_handled_at = 0.0
        self.last_card_open_attempt = 0.0
        self.last_card_stack_y = 0
        self.last_inventory_use = 0.0
        self.score_cap_reached = False
        self.dead_seen = False
        self.game_started_at = 0.0
        self.last_buyback_attempt = 0.0
        self.buyback_attempted = False
        self.buyback_visible_since = 0.0
        self.buyback_last_point: list[int] | None = None
        self.buyback_click_count = 0
        # A room may keep looking similar while matchmaking starts.  Never click
        # a second button in the same room cycle; otherwise a weak template hit
        # on "返回"/"取消" can immediately cancel the queue.
        self.room_match_armed = True
        self.room_wait_started = 0.0
        self.room_last_seen_at = 0.0
        self.last_unknown_diagnostic = 0.0
        self.town_social_armed = True
        self.guild_expedition_armed = True
        self.expedition_mode_armed = True
        self.detected_state = ""
        self.detected_state_hits = 0
        self.thread = threading.Thread(target=self._run, daemon=True, name="BotEngine")

    def _load_battle_history(self) -> list[dict[str, Any]]:
        path = self.base / "battle_history.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return [item for item in data if isinstance(item, dict)]
        except FileNotFoundError:
            pass
        except Exception as exc:
            self.log.warning("读取历史战绩失败，将从空记录开始：%s", exc)
        return []

    def _save_battle_history(self) -> None:
        path = self.base / "battle_history.json"
        try:
            path.write_text(json.dumps(self.battle_history, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except Exception as exc:
            self.log.warning("保存历史战绩失败：%s", exc)

    def clear_battle_history(self) -> None:
        """Clear persisted finished-match records without interrupting combat."""
        self.battle_history.clear()
        self.stats_revision += 1
        self._save_battle_history()
        self.log.info("战斗记录：历史数据已清空；当前进行中的实时战绩仍会继续刷新")

    def _sample_battle_stats(self, screenshot: Image.Image, anchor: list[int] | None = None, force: bool = False) -> None:
        if not self.config.get("statistics", {}).get("enabled", True):
            return
        interval = float(self.config.get("statistics", {}).get("sample_interval", 2.0))
        now = time.monotonic()
        if not force and now - self.last_stats_sample < interval:
            return
        self.last_stats_sample = now
        if anchor is None:
            located = self.detector.locate("in_game", screenshot)
            threshold = float(self.config.get("detectors", {}).get("in_game", {}).get("threshold", 0.96))
            if located is None or located[1] < threshold:
                return
            anchor = located[0]
        stats = self.stats_reader.read(screenshot, anchor)
        if stats is not None:
            score_cap = max(1, int(self.config.get("statistics", {}).get("score_cap", 1500)))
            if stats.get("score", 0) >= score_cap or self.score_cap_reached:
                stats["score"] = score_cap
                if not self.score_cap_reached:
                    self.score_cap_reached = True
                    self.log.info("战绩达到 %s：本局已打满；继续基本作战并等待真实结算", score_cap)
            if stats != self.current_battle_stats:
                self.current_battle_stats = stats
                # The UI previously refreshed only after match finalization,
                # leaving an old record visible throughout the next match.
                # Treat every changed live sample as a display revision.
                self.stats_revision += 1

    def _result_outcome(self, screenshot: Image.Image) -> str:
        located = self.detector.locate("result_defeat", screenshot)
        threshold = float(self.config.get("detectors", {}).get("result_defeat", {}).get("threshold", 0.94))
        if located is not None and located[1] >= threshold:
            return "负"
        if "result_defeat" in self.detector.templates:
            return "胜"
        return "待确认"

    def _finalize_battle_stats(self, outcome: str | None = None) -> None:
        if self.round_stats_saved:
            if outcome in {"胜", "负"} and self.battle_history:
                if self.battle_history[0].get("outcome") != outcome:
                    self.battle_history[0]["outcome"] = outcome
                    self.learner.learn(self.battle_history[0])
                    self.stats_revision += 1
                    self._save_battle_history()
                    self.log.info("本局胜负已确认：%s", outcome)
            return
        if self.current_battle_stats is None:
            return
        record: dict[str, Any] = {
            "time": datetime.now().strftime("%m-%d %H:%M"),
            "outcome": outcome if outcome in {"胜", "负"} else "待确认",
            **self.current_battle_stats,
        }
        limit = max(1, int(self.config.get("statistics", {}).get("history_limit", 10)))
        self.battle_history.insert(0, record)
        del self.battle_history[limit:]
        self.learner.learn(record)
        self.round_stats_saved = True
        self.stats_revision += 1
        self._save_battle_history()
        self.log.info(
            "本局战绩已保存：%s / 击杀 %s / 死亡 %s / 助攻 %s / 战绩 %s",
            record["outcome"],
            record["kills"],
            record["deaths"],
            record["assists"],
            record["score"],
        )

    def _mark_round_complete(self) -> bool:
        """Count a finished match once and apply the configured run limit."""
        if self.round_completion_recorded:
            return False
        self.round_completion_recorded = True
        self.completed_rounds += 1
        entry = self.config.get("entry", {})
        try:
            limit = max(0, int(entry.get("loop_limit", 0)))
        except (TypeError, ValueError):
            limit = 0
        target = "无限" if limit == 0 else str(limit)
        self.log.info("循环计数：已完成 %s / %s 局", self.completed_rounds, target)
        if limit == 0 or self.completed_rounds < limit:
            return False

        action = entry.get("completion_action", "stop")
        if action == "shutdown":
            try:
                creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                subprocess.Popen(
                    [
                        "shutdown.exe",
                        "/s",
                        "/t",
                        "60",
                        "/c",
                        f"{APP_NAME} 已完成 {self.completed_rounds} 局，电脑将在 60 秒后关机。运行 shutdown /a 可取消。",
                    ],
                    creationflags=creation_flags,
                )
                self.log.warning("已达到设定局数：电脑将在 60 秒后关机；可运行 shutdown /a 取消")
            except Exception as exc:
                self.log.error("已达到设定局数，但安排关机失败：%s", exc)
        else:
            self.log.warning("已达到设定局数：停止脚本")
        self.stop_now()
        self.state = "已完成设定局数"
        return True

    def start_thread(self) -> None:
        self.thread.start()

    def toggle(self) -> None:
        if self.permanently_stopped:
            self.log.warning("已被 F9 停止；请点击“重置停止”后再按 F8")
            return
        if self.active.is_set():
            self.active.clear()
            self.state = "已暂停"
            self.log.info("F8：已暂停")
        else:
            if self.state == "商店未完成：请人工确认":
                self._reset_shop_progress()
            self.active.set()
            self.state = "运行中"
            self.log.info("F8：开始运行")

    def stop_now(self) -> None:
        self.active.clear()
        self.permanently_stopped = True
        self.state = "已停止"
        self.window.release_cursor()
        self.log.warning("F9：已紧急停止，不再发送任何输入")

    def reset_stop(self) -> None:
        self.window.release_cursor()
        keep_current_round_recorded = "结算" in self.state or self.state == "已完成设定局数"
        self.permanently_stopped = False
        self.state = "已暂停"
        self.hero_selected = False
        self.hero_select_seen_at = 0.0
        self.hero_wait_logged = False
        self.hero_session_active = False
        self.talent_selected = False
        self.talent_panel_seen = False
        self.talent_wait_logged = False
        self.talent_click_pending_at = 0.0
        self.opening_sequence_seen = False
        self.shop_configured = False
        self.shop_scheme_selected = False
        self.last_shop_attempt = 0.0
        self.shop_confirmations = 0
        self.card_panel_handled = False
        self.card_flips_done = False
        self.card_panel_handled_at = 0.0
        self.last_card_open_attempt = 0.0
        self.last_card_stack_y = 0
        self.last_inventory_use = 0.0
        self.score_cap_reached = False
        self.dead_seen = False
        self.game_started_at = 0.0
        self.last_buyback_attempt = 0.0
        self.buyback_attempted = False
        self.buyback_visible_since = 0.0
        self.buyback_last_point = None
        self.buyback_click_count = 0
        self.room_match_armed = True
        self.room_wait_started = 0.0
        self.room_last_seen_at = 0.0
        self.last_unknown_diagnostic = 0.0
        self.town_social_armed = True
        self.guild_expedition_armed = True
        self.expedition_mode_armed = True
        self.last_summon = 0.0
        self.last_skill_use = {}
        self.next_skill_slot = 0
        self.last_chase_click = 0.0
        self.last_target_attack = 0.0
        self.no_enemy_since = 0.0
        self.last_teleport_attempt = 0.0
        self.last_boss_observe = 0.0
        self.last_tower_support = 0.0
        self.last_tactical_move = 0.0
        self.last_group_follow = 0.0
        self.last_retreat = 0.0
        self.self_selected = False
        self.enemy_track_memory = []
        self.last_target_reason = ""
        self.last_target_friendly_count = 0
        self.detected_state = ""
        self.detected_state_hits = 0
        self.round_completion_recorded = keep_current_round_recorded
        self.completed_rounds = 0
        self.log.info("停止状态已重置；按 F8 可重新开始")

    def close(self) -> None:
        self.active.clear()
        self.shutdown.set()
        self.window.release_cursor()

    def set_match_mode(self, mode: str) -> None:
        normalized = "power" if mode == "power" else "casual"
        self.config.setdefault("entry", {})["match_mode"] = normalized
        label = "战力匹配" if normalized == "power" else "休闲匹配"
        self.log.info("匹配模式已切换为：%s", label)

    def set_hero_category(self, category: str) -> None:
        normalized = category if category in {"survival", "physical", "magic"} else "survival"
        self.config.setdefault("entry", {})["hero_category"] = normalized
        self.learner.set_profile_from_category(normalized)
        labels = {"survival": "肉盾", "physical": "物理", "magic": "法师"}
        self.log.info("英雄分类与本地学习职业已切换为：%s", labels[normalized])

    def set_defense_lane(self, lane: str) -> None:
        normalized = lane if lane in {"top", "middle", "bottom"} else "off"
        self.config.setdefault("entry", {})["defense_lane"] = normalized
        labels = {"off": "关闭（全图作战）", "top": "上路", "middle": "中路", "bottom": "下路"}
        self.last_move = 0.0
        self.no_enemy_since = 0.0
        self.last_teleport_attempt = 0.0
        self.log.info("单路防守已切换为：%s", labels[normalized])

    def set_card_flip_count(self, count: int) -> None:
        normalized = min(4, max(0, int(count)))
        self.config.setdefault("entry", {})["card_flip_count"] = normalized
        if normalized == 0:
            self.log.info("翻卡已切换为：全局不翻卡")
        else:
            self.log.info("每次翻卡数量已切换为：%s 张", normalized)

    def set_loop_limit(self, count: int) -> None:
        normalized = max(0, min(999, int(count)))
        self.config.setdefault("entry", {})["loop_limit"] = normalized
        self.log.info("循环局数已切换为：%s", "一直循环" if normalized == 0 else f"{normalized} 局")

    def set_completion_action(self, action: str) -> None:
        normalized = "shutdown" if action == "shutdown" else "stop"
        self.config.setdefault("entry", {})["completion_action"] = normalized
        self.log.info("达到局数后的动作：%s", "关闭电脑" if normalized == "shutdown" else "停止脚本")

    def set_shop_key(self, key: str) -> None:
        normalized = "f11" if str(key).lower() == "f11" else "f4"
        self.config.setdefault("combat", {})["shop_key"] = normalized
        self.log.info("商店快捷键已切换为：%s", normalized.upper())

    def set_shop_scheme(self, choice: int) -> None:
        normalized = min(2, max(0, int(choice)))
        self.config.setdefault("entry", {})["shop_scheme"] = normalized
        self.shop_scheme_selected = False
        labels = {0: "保持当前方案", 1: "推荐方案一", 2: "推荐方案二"}
        self.log.info("装备方案已切换为：%s", labels[normalized])

    def set_skill_layout(self, layout: str) -> None:
        normalized = "werd" if layout == "werd" else "qwer"
        keys = ["w", "e", "r", "d"] if normalized == "werd" else ["q", "w", "e", "r"]
        self.config.setdefault("entry", {})["skill_layout"] = normalized
        self.config.setdefault("combat", {})["skill_keys"] = keys
        self.log.info("技能快捷键已切换为：%s", "/".join(key.upper() for key in keys))

    def set_buyback_policy(self, policy: str) -> None:
        normalized = policy if policy in {"always", "after_5m", "never"} else "never"
        self.config.setdefault("entry", {})["buyback_policy"] = normalized
        labels = {"always": "全局自动买活", "after_5m": "开局 5 分钟后自动买活", "never": "永久不买活"}
        self.log.info("买活策略已切换为：%s", labels[normalized])

    def set_talent_choice(self, choice: int) -> None:
        normalized = min(5, max(1, int(choice)))
        self.config.setdefault("entry", {})["talent_choice"] = normalized
        self.last_summon = 0.0
        summon_key = str(self.config.get("combat", {}).get("summon_key", "z")).upper()
        suffix = f"（启用 {summon_key} 键召唤援兵）" if normalized == 5 else f"（不使用 {summon_key} 键）"
        self.log.info("开局天赋已切换为：第 %s 个%s", normalized, suffix)

    @staticmethod
    def _gold_buyback_point(screenshot: Image.Image) -> list[int] | None:
        # Find the filled gold button, not the surrounding dialog/text template.
        rgb = np.asarray(screenshot.convert("RGB"), dtype=np.int16)
        sx, sy = screenshot.width / 1024, screenshot.height / 768
        x0, y0, x1, y1 = round(280*sx), round(30*sy), round(440*sx), round(95*sy)
        crop = rgb[y0:y1, x0:x1]
        gold = (crop[:,:,0] > 150) & (crop[:,:,1] > 100) & (crop[:,:,2] < 140) & (crop[:,:,0] > crop[:,:,2] + 50)
        rows = np.flatnonzero(gold.sum(axis=1) >= 40*sx)
        groups = np.split(rows, np.where(np.diff(rows) > 1)[0] + 1)
        groups = [group for group in groups if len(group) >= 10*sy]
        if not groups:
            return None
        group = max(groups, key=len)
        cols = np.flatnonzero(gold[group].sum(axis=0) >= len(group)*0.45)
        if len(cols) < 55*sx:
            return None
        return [x0 + int((cols[0]+cols[-1])/2), y0 + int((group[0]+group[-1])/2)]

    def _try_buyback(self, screenshot: Image.Image) -> str:
        policy = self.config.get("entry", {}).get("buyback_policy", "never")
        if policy == "never":
            return ""
        now = time.monotonic()
        if policy == "after_5m":
            if self.game_started_at <= 0.0 or now - self.game_started_at < 300.0:
                return ""
        point = self._gold_buyback_point(screenshot)
        if point is None:
            self.buyback_visible_since = 0.0
            self.buyback_last_point = None
            return "confirming" if self.buyback_click_count > 0 else ""

        if self.buyback_last_point is None or abs(point[0] - self.buyback_last_point[0]) > 12 or abs(point[1] - self.buyback_last_point[1]) > 12:
            self.buyback_last_point = point
            self.buyback_visible_since = now
            self.log.info("买活：检测到金色购买按钮中心 %s", point)
        if self.buyback_visible_since <= 0.0:
            self.buyback_visible_since = now
            return "waiting"
        stable_delay = 0.0
        if now - self.buyback_visible_since < stable_delay:
            return "waiting"
        retry = float(self.config.get("timing", {}).get("buyback_retry", 1.5))
        if self.last_buyback_attempt > 0.0 and now - self.last_buyback_attempt < retry:
            return "confirming"

        self.last_buyback_attempt = now
        if self._safe_click_point(point, "金色买活按钮中心", hold=0.22):
            self.buyback_click_count += 1
            self.state = "买活确认中"
            self.log.info(
                "买活：第 %s 次点击金色购买按钮；继续复查死亡界面，未成功会自动重试（策略：%s）",
                self.buyback_click_count,
                policy,
            )
            return "clicked"
        return "waiting"

    def _safe_click(self, name: str, button: str = "left") -> bool:
        if not self.active.is_set() or self.permanently_stopped:
            return False
        point = self.config["coordinates"][name]
        return self._safe_click_point(point, name, button)

    def _state_confirmed(self, name: str) -> bool:
        if self.detected_state == name:
            self.detected_state_hits += 1
        else:
            self.detected_state = name
            self.detected_state_hits = 1
        return self.detected_state_hits >= int(self.config["safety"].get("state_confirm_frames", 2))

    def _safe_click_point(self, point: list[int], label: str, button: str = "left", hold: float | None = None) -> bool:
        if not self.active.is_set() or self.permanently_stopped:
            return False
        ok = self.window.click(point, button, hold=hold)
        if ok:
            self.log.info("操作：%s %s %s", "右键" if button == "right" else "点击", label, point)
            self.last_action = time.monotonic()
        return ok

    def _safe_click_ratio(self, name: str, label: str, button: str = "left") -> bool:
        win = self.window.validate(require_foreground=True)
        if win is None:
            self.log.warning("安全拦截：窗口不可用、尺寸不符或不在前台")
            return False
        ratio = self.config["coordinates"][name]
        point = [min(win.width - 1, max(0, round(win.width * ratio[0]))), min(win.height - 1, max(0, round(win.height * ratio[1])))]
        return self._safe_click_point(point, label, button)

    def _find_flashing_minimap_target(self) -> tuple[list[int], float] | None:
        x, y, width, height = self.config["coordinates"]["minimap_region"]
        samples = max(3, int(self.config["timing"].get("flash_sample_count", 5)))
        interval = float(self.config["timing"].get("flash_sample_interval", 0.10))
        frames: list[np.ndarray] = []
        for index in range(samples):
            if not self.active.is_set() or self.permanently_stopped:
                return None
            captured = self.window.grab()
            if captured is None:
                return None
            win, screenshot = captured
            if not self.window.background_mode and self.config["safety"].get("require_foreground", True) and not self.window.is_foreground(win):
                return None
            if x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > win.width or y + height > win.height:
                self.log.error("安全拦截：小地图检测区域越界 %s", [x, y, width, height])
                return None
            crop = screenshot.crop((x, y, x + width, y + height)).convert("RGB")
            frames.append(np.asarray(crop, dtype=np.float32))
            if index + 1 < samples:
                time.sleep(interval)

        differences = [np.mean(np.abs(frames[i] - frames[i - 1]), axis=2) for i in range(1, len(frames))]
        delta = np.max(np.stack(differences), axis=0)
        pixel_threshold = float(self.config["timing"].get("flash_pixel_threshold", 24.0))
        delta = np.where(delta >= pixel_threshold, delta, 0.0)
        window_size = max(5, int(self.config["timing"].get("flash_window", 15)))
        window_size = min(window_size, width, height)
        windows = np.lib.stride_tricks.sliding_window_view(delta, (window_size, window_size))
        scores = windows.sum(axis=(2, 3))
        best_index = np.unravel_index(int(np.argmax(scores)), scores.shape)
        best_score = float(scores[best_index])
        minimum = float(self.config["timing"].get("flash_min_score", 1800.0))
        if best_score < minimum:
            self.log.warning("小地图未检测到可靠闪动目标（得分 %.0f < %.0f），本轮不移动", best_score, minimum)
            return None
        center_x = x + int(best_index[1]) + window_size // 2
        center_y = y + int(best_index[0]) + window_size // 2
        point = [center_x, center_y]
        self.log.info("识别：小地图闪动目标 %s，得分 %.0f", point, best_score)
        return point, best_score

    def _choose_hero(self, anchor: list[int]) -> None:
        category = self.config.get("entry", {}).get("hero_category", "survival")
        offsets_by_category = self.config["coordinates"].get("hero_offsets_by_category", {})
        offsets = offsets_by_category.get(category, self.config["coordinates"].get("hero_offsets", []))
        labels = {"survival": "肉盾", "physical": "物理", "magic": "法师"}
        points = [[anchor[0] + offset[0], anchor[1] + offset[1]] for offset in offsets]
        win = self.window.validate(require_foreground=True)
        valid = [p for p in points if win is not None and self.window.inside(win, p)]
        if not valid:
            self.log.error("没有有效的预设英雄坐标")
            return
        selected_point = random.choice(valid)
        if self.window.click(selected_point):
            self.last_action = time.monotonic()
            self.log.info("操作：从%s栏随机选择英雄 %s", labels.get(category, "肉盾"), selected_point)
            time.sleep(float(self.config["timing"].get("after_hero_click", 0.8)))
            captured = self.window.grab()
            ready = self.detector.locate("ready_button", captured[1]) if captured is not None else None
            threshold = float(self.config["detectors"]["ready_button"].get("threshold", 0.90))
            if ready is not None and ready[1] >= threshold and self._safe_click_point(ready[0], "识别到的准备按钮"):
                self.hero_selected = True
                self.log.info("选人：英雄与准备按钮均已点击，本轮不再重复选择")
            else:
                score = ready[1] if ready is not None else 0.0
                self.log.warning("选人：准备按钮相似度 %.3f 低于 %.3f，保留未完成状态并自动重试", score, threshold)

    def _shop_auto_buy_checked(self, screenshot: Image.Image) -> bool:
        point = self._shop_checkbox_point(screenshot) or self.config["coordinates"].get("shop_auto_buy", [449, 275])
        radius = 14
        left, top = max(0, point[0] - radius), max(0, point[1] - radius)
        right, bottom = min(screenshot.width, point[0] + radius), min(screenshot.height, point[1] + radius)
        if right <= left or bottom <= top:
            return False
        rgb = np.asarray(screenshot.crop((left, top, right, bottom)).convert("RGB"), dtype=np.int16)
        red, green, blue = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
        check_pixels = (green >= 120) & (green >= red * 1.25) & (green >= blue * 1.05)
        return int(check_pixels.sum()) >= int(self.config["combat"].get("shop_check_min_green_pixels", 8))

    def _shop_structure_visible(self, screenshot: Image.Image) -> bool:
        """Reject label-like textures unless the surrounding shop layout is present."""
        detector_config = self.config["detectors"]["shop_panel"]
        regions = detector_config.get("structure_regions", [])
        if not regions:
            return True
        rgb = np.asarray(screenshot.convert("RGB"), dtype=np.int16)
        for rule in regions:
            x, y, width, height = [int(value) for value in rule.get("region", [])]
            if width <= 0 or height <= 0 or x < 0 or y < 0:
                return False
            if x + width > screenshot.width or y + height > screenshot.height:
                return False
            patch = rgb[y:y + height, x:x + width]
            red, green, blue = patch[:, :, 0], patch[:, :, 1], patch[:, :, 2]
            maximum = patch.max(axis=2)
            minimum = patch.min(axis=2)
            if "min_dark_ratio" in rule:
                dark_ratio = float((maximum < int(rule.get("dark_ceiling", 115))).mean())
                if dark_ratio < float(rule["min_dark_ratio"]):
                    return False
            if "min_low_saturation_ratio" in rule:
                low_saturation = float(((maximum - minimum) < int(rule.get("saturation_span", 45))).mean())
                if low_saturation < float(rule["min_low_saturation_ratio"]):
                    return False
            if "min_warm_ratio" in rule:
                warm = (red > blue * 1.15) & (red > green * 1.03)
                if float(warm.mean()) < float(rule["min_warm_ratio"]):
                    return False
        return True

    def _shop_panel_match(self, screenshot: Image.Image) -> tuple[list[int], float] | None:
        button = self.detector.locate("shop_switch_button", screenshot)
        title = self.detector.locate("shop_title", screenshot)
        if button is None or title is None:
            return None
        if button[1] < .94 or title[1] < .93:
            return None
        if abs(button[0][0]-386) > 8 or abs(button[0][1]-274) > 8:
            return None
        if abs(title[0][0]-515) > 15 or abs(title[0][1]-101) > 10:
            return None
        if not self._shop_structure_visible(screenshot):
            return None
        # Retain the public anchor contract used by shop_checkbox_offset, but
        # derive it from the stable button rather than low-contrast label text.
        return ([button[0][0]+99,button[0][1]+1], min(button[1],title[1]))

    def _shop_panel_visible(self, screenshot: Image.Image) -> bool:
        return self._shop_panel_match(screenshot) is not None

    def _shop_scheme_panel_visible(self, screenshot: Image.Image) -> bool:
        """Detect the recommended-build overlay from its two long warm row borders."""
        # Row borders may remain visible underneath the translucent main shop.
        # The top-level overlay title must be present before routing any input.
        title = self.detector.locate("shop_scheme_title", screenshot)
        if title is None or title[1] < float(self.config["detectors"].get("shop_scheme_title", {}).get("threshold", .94)):
            return False
        if screenshot.width < 810 or screenshot.height < 446:
            return False
        rgb = np.asarray(screenshot.convert("RGB"), dtype=np.int16)
        red, green, blue = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
        warm = (
            (red >= 75)
            & (green >= 45)
            & (green <= 140)
            & (blue <= 90)
            & (red >= green * 1.12)
            & (green >= blue * 1.08)
        )
        strong_rows = 0
        for center_y in (293, 378):
            top, bottom = max(0, center_y - 2), min(screenshot.height, center_y + 3)
            if int(warm[top:bottom, 235:810].sum()) >= 500:
                strong_rows += 1
        return strong_rows == 2

    def _select_shop_scheme(self, screenshot: Image.Image) -> Image.Image | None:
        try:
            desired = min(2, max(0, int(self.config.get("entry", {}).get("shop_scheme", 0))))
        except (TypeError, ValueError):
            desired = 0
        if desired == 0 or self.shop_scheme_selected:
            self.shop_scheme_selected = True
            if self._shop_scheme_panel_visible(screenshot):
                if not self._safe_click_point([805, 168], "关闭不再需要的方案子窗口"):
                    return None
                time.sleep(0.35)
                captured = self._shop_capture_without_tooltip()
                if captured is None or self._shop_scheme_panel_visible(captured[1]):
                    return None
                return captured[1]
            return screenshot

        if not self._shop_scheme_panel_visible(screenshot):
            if not self._safe_click("shop_switch_scheme"):
                self.log.warning("装备方案：未能点击切换方案，稍后重试")
                return None
            self.log.info("装备方案：已点击切换方案，等待推荐出装窗口")
            time.sleep(float(self.config["timing"].get("shop_scheme_open_wait", 0.8)))
        else:
            self.log.info("装备方案：恢复已打开的选择窗口，不重复按F4或切换方案")
        captured = self._shop_capture_without_tooltip()
        if captured is None or not self._shop_scheme_panel_visible(captured[1]):
            self.log.warning("装备方案：未确认推荐出装窗口已经打开，本次不继续点击")
            return None

        if not self._safe_click("shop_scheme_recommended_tab"):
            self.log.warning("装备方案：未能点击推荐标签")
            return None
        time.sleep(float(self.config["timing"].get("shop_scheme_tab_wait", 0.35)))
        captured = self._shop_capture_without_tooltip()
        if captured is None or not self._shop_scheme_panel_visible(captured[1]):
            self.log.warning("装备方案：点击推荐标签后窗口状态异常，本次停止操作")
            return None

        options = self.config["coordinates"].get("shop_scheme_options", {})
        point = options.get(str(desired))
        if not point:
            return None
        captured = None
        for attempt in range(2):
            if not self._safe_click_point(point, f"推荐方案{desired}左侧方框（第{attempt+1}/2次）"):
                return None
            time.sleep(float(self.config["timing"].get("shop_scheme_return_wait", 0.8)))
            captured = self._shop_capture_without_tooltip()
            if captured is None or not self._shop_scheme_panel_visible(captured[1]):
                break
        if captured is None:
            self.log.warning("装备方案：选择后无法读取返回画面")
            return None
        if self._shop_scheme_panel_visible(captured[1]):
            self.log.warning("装备方案：两次选择后窗口仍未消失，保留失败状态；下次从当前窗口恢复，禁止反复F4")
            return None
        if not self._shop_panel_visible(captured[1]):
            self.log.warning("装备方案：选择窗口虽已消失，但尚未确认返回商店主界面")
            return None
        self.shop_scheme_selected = True
        self.log.info("装备方案：窗口已消失并返回核心装备页面，确认推荐方案 %s 选择成功", desired)
        return captured[1]

    def _town_structure_score(self, screenshot: Image.Image) -> float:
        """Measure the stable warm room-list grid at the upper-left of town."""
        spec = self.config["detectors"]["town_social"]
        x, y, width, height = [int(value) for value in spec.get("structure_region", [0, 30, 300, 220])]
        if width <= 0 or height <= 0 or x < 0 or y < 0:
            return 0.0
        if x + width > screenshot.width or y + height > screenshot.height:
            return 0.0
        patch = np.asarray(screenshot.crop((x, y, x + width, y + height)).convert("RGB"), dtype=np.int16)
        red, green, blue = patch[:, :, 0], patch[:, :, 1], patch[:, :, 2]
        warm = (red > 100) & (red > green * 1.12) & (green > blue * 1.15)
        return float(warm.mean())

    def _shop_checkbox_point(self, screenshot: Image.Image) -> list[int] | None:
        located = self._shop_panel_match(screenshot)
        if located is None:
            return None
        offset = self.config["coordinates"].get("shop_checkbox_offset", [-41, 3])
        point = [int(located[0][0] + offset[0]), int(located[0][1] + offset[1])]
        if not (0 <= point[0] < screenshot.width and 0 <= point[1] < screenshot.height):
            return None
        return point

    def _confirm_shop_auto_buy_stable(self) -> bool:
        """Keep the shop open and require the green check across multiple frames."""
        duration = max(2.0, float(self.config["timing"].get("shop_verify_duration", 2.5)))
        interval = min(0.8, max(0.25, float(self.config["timing"].get("shop_verify_interval", 0.5))))
        required = max(3, int(self.config["timing"].get("shop_verify_samples", 4)))
        # Sample count and observed duration are independent requirements.
        # A slow VM must be allowed to collect the fourth frame AFTER 2.5s.
        deadline = time.monotonic() + max(8.0, duration + required * interval * 2)
        confirmed = 0
        first_positive = None
        captured = self._shop_capture_without_tooltip()
        while time.monotonic() < deadline:
            if not self.active.is_set() or self.permanently_stopped:
                return False
            if captured is None or not self._shop_panel_visible(captured[1]):
                self.log.warning("商店复核：确认期间商店面板消失")
                return False
            if not self._shop_auto_buy_checked(captured[1]):
                self.log.warning("商店复核：确认期间未检测到绿色勾")
                return False
            confirmed += 1
            observed_at = time.monotonic()
            if first_positive is None:
                first_positive = observed_at
            if confirmed >= required and observed_at-first_positive >= duration:
                self.log.info("商店复核：绿色勾持续%.1f秒，确认%s帧；慢抓图不会因超过2.5秒被判失败", observed_at-first_positive, confirmed)
                return True
            time.sleep(interval)
            captured = self.window.grab()
        self.log.warning("商店复核：等待超时，%s帧仍未同时满足持续时间及帧数，不判成功", confirmed)
        return False

    def _card_panel_button(self, screenshot: Image.Image) -> list[int] | None:
        located = self.detector.locate("card_smart_sell", screenshot)
        if located is None:
            return None
        threshold = float(self.config["detectors"]["card_smart_sell"].get("threshold", 0.90))
        return located[0] if located[1] >= threshold else None

    def _card_one_click_button(self, screenshot: Image.Image) -> list[int] | None:
        located = self.detector.locate("card_one_click", screenshot)
        if located is None:
            return None
        threshold = float(self.config["detectors"]["card_one_click"].get("threshold", 0.90))
        return located[0] if located[1] >= threshold else None

    def _card_panel_visible(self, screenshot: Image.Image) -> bool:
        return self._card_panel_button(screenshot) is not None or self._card_one_click_button(screenshot) is not None

    def _card_badge_is_zero(self, grayscale: np.ndarray, group_top: int) -> bool:
        template = self.detector.templates.get("card_count_zero")
        if template is None:
            return False
        template_h, template_w = template.shape
        best_score = -1.0
        for top in range(max(0, group_top - 5), min(grayscale.shape[0] - template_h, group_top + 2) + 1):
            for left in range(998, min(grayscale.shape[1] - template_w, 1007) + 1):
                patch = grayscale[top : top + template_h, left : left + template_w]
                score = float(1.0 - np.mean(np.abs(patch - template)) / 255.0)
                best_score = max(best_score, score)
        threshold = float(self.config["detectors"]["card_count_zero"].get("threshold", 0.90))
        return best_score >= threshold

    def _find_numbered_card_stacks(self, screenshot: Image.Image) -> list[list[int]]:
        # Card stacks are aligned to the right edge.  Their quantity badge is a
        # compact white digit at x ~= 1004..1016.  Group bright rows while
        # allowing small holes inside digits such as 2, 3, 4 and 5.
        if screenshot.width < 1017 or screenshot.height < 500:
            return []
        auto_purchase = self.detector.locate("auto_purchase", screenshot)
        auto_threshold = float(self.config["detectors"]["auto_purchase"].get("threshold", 0.90))
        if auto_purchase is not None and auto_purchase[1] >= auto_threshold:
            self.log_once("card_auto_purchase_guard", "翻卡：右侧识别为自动购买装备提示，不作为卡包入口")
            return []
        rgb = np.asarray(screenshot.convert("RGB"), dtype=np.int16)
        red, green, blue = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
        bright = (red > 180) & (green > 170) & (blue > 150)
        row_scores = bright[260:500, 1004:1017].sum(axis=1)
        rows = np.flatnonzero(row_scores >= 1) + 260
        groups: list[list[int]] = []
        for row in rows:
            row = int(row)
            if not groups or row - groups[-1][-1] > 3:
                groups.append([row])
            else:
                groups[-1].append(row)
        grayscale = np.asarray(screenshot.convert("L"), dtype=np.float32)
        results: list[list[int]] = []
        for group in groups:
            top, bottom = group[0], group[-1]
            pixel_count = int(row_scores[top - 260 : bottom - 259].sum())
            if bottom - top + 1 >= 7 and pixel_count >= 15:
                if self._card_badge_is_zero(grayscale, top):
                    self.log.info("翻卡：忽略数量为 0 的卡包，数字位置 y=%s", top)
                    continue
                results.append([995, max(260, top - 5)])
        return results

    def _choose_next_card_stack(self, screenshot: Image.Image) -> list[int] | None:
        stacks = self._find_numbered_card_stacks(screenshot)
        if not stacks:
            return None
        stacks.sort(key=lambda point: point[1])
        selected = next((point for point in stacks if point[1] > self.last_card_stack_y + 20), stacks[0])
        self.last_card_stack_y = selected[1]
        return selected

    def _cards_blocked_by_score(self) -> bool:
        return self.config.get("entry", {}).get("stop_cards_at_score_cap", True) and self.score_cap_reached

    def _handle_card_panel(self, screenshot: Image.Image) -> bool:
        sell_button = self._card_panel_button(screenshot)
        one_click_button = self._card_one_click_button(screenshot)
        if sell_button is None and one_click_button is None:
            return False
        self.state = "翻卡处理中"
        self._sample_battle_stats(screenshot, force=True)
        flip_count = min(4, max(0, int(self.config.get("entry", {}).get("card_flip_count", 0))))
        if self._cards_blocked_by_score() and not self.card_flips_done:
            flip_count = 0
        if flip_count == 0:
            if time.monotonic() - self.card_panel_handled_at >= 1.0 and self.window.key_tap("esc"):
                self.card_panel_handled_at = time.monotonic()
                self.log.info("翻卡：全局不翻卡，按 ESC 关闭意外出现的翻卡面板")
            return True
        if self.card_panel_handled:
            timeout = float(self.config["timing"].get("card_panel_timeout", 8.0))
            if time.monotonic() - self.card_panel_handled_at >= timeout:
                if self.window.key_tap("esc"):
                    self.log.info("翻卡：智能出售后面板仍未自动关闭，按 ESC 返回游戏")
                    self.card_panel_handled_at = time.monotonic()
            return True
        if self.card_flips_done:
            if sell_button is None:
                self.state = "等待智能出售"
                return True
            if self._safe_click_point(sell_button, "识别到的智能出售按钮"):
                self.card_panel_handled = True
                self.card_panel_handled_at = time.monotonic()
                self.log.info("翻卡：已点击智能出售")
            return True
        slots = list(self.config["coordinates"].get("card_flip_slots", []))
        if len(slots) < flip_count:
            self.log.error("翻卡：card_flip_slots 少于所选数量 %s，本轮不操作", flip_count)
            return True
        for point in random.sample(slots, flip_count):
            fresh = self.window.grab()
            if fresh is not None:
                self._sample_battle_stats(fresh[1], force=True)
            if self._cards_blocked_by_score():
                self.log.info("翻卡：战绩已满，停止后续卡牌点击")
                break
            if not self._safe_click_point(point, "随机翻卡位置"):
                return True
            time.sleep(float(self.config["timing"].get("card_flip_gap", 0.18)))
        self.card_flips_done = True
        self.card_panel_handled_at = time.monotonic()
        self.log.info("翻卡：已随机点击 %s 张，等待智能出售按钮", flip_count)
        time.sleep(float(self.config["timing"].get("card_reveal_wait", 0.35)))
        captured = self.window.grab()
        button = self._card_panel_button(captured[1]) if captured is not None else None
        if button is None:
            self.log.info("翻卡：卡牌动画尚未结束，下一帧继续确认智能出售")
            return True
        if self._safe_click_point(button, "识别到的智能出售按钮"):
            self.card_panel_handled = True
            self.card_panel_handled_at = time.monotonic()
            self.log.info("翻卡：已随机翻开 %s 张并点击智能出售", flip_count)
        return True

    def _find_counted_inventory_slots(self, screenshot: Image.Image) -> list[list[int]]:
        slots = self.config["coordinates"].get("inventory_slot_centers", [])
        if screenshot.height < 766:
            return []
        rgb = np.asarray(screenshot.convert("RGB"), dtype=np.int16)
        results: list[list[int]] = []
        for point in slots:
            center_x, center_y = int(point[0]), int(point[1])
            left, right = center_x + 9, center_x + 17
            top, bottom = center_y + 5, center_y + 16
            if left < 0 or top < 0 or right > screenshot.width or bottom > screenshot.height:
                continue
            patch = rgb[top:bottom, left:right]
            maximum = patch.max(axis=2)
            minimum = patch.min(axis=2)
            neutral_bright = (minimum >= 155) & ((maximum - minimum) <= 50)
            if int(neutral_bright.sum()) >= int(self.config["combat"].get("inventory_count_min_pixels", 8)):
                results.append([center_x, center_y])
        return results

    def _use_counted_inventory_items(self, screenshot: Image.Image) -> None:
        points = self._find_counted_inventory_slots(screenshot)
        if not points:
            return
        used: list[list[int]] = []
        for point in points:
            if self._safe_click_point(point, "带数量的可用物品"):
                used.append(point)
                time.sleep(float(self.config["timing"].get("inventory_item_gap", 0.10)))
        if used:
            self.log.info("战斗物品：循环点击 %s 个带数量物品 %s", len(used), used)

    def _shop_capture_without_tooltip(self) -> tuple[Any, Image.Image] | None:
        if not self.active.is_set() or self.permanently_stopped:
            return None
        if not self.window.move_cursor([500, 70]):
            return None
        time.sleep(0.2)
        return self.window.grab()

    def _reset_shop_progress(self) -> None:
        self.shop_setup_started = 0.0
        self.shop_setup_attempts = 0
        self.shop_setup_deferred = False

    def _close_shop_verified(self) -> bool:
        captured = self._shop_capture_without_tooltip()
        if captured is None:
            return False
        if self._shop_scheme_panel_visible(captured[1]):
            if not self._safe_click_point([805, 168], "关闭装备方案子窗口"):
                return False
            time.sleep(0.35)
            captured = self._shop_capture_without_tooltip()
            if captured is None or self._shop_scheme_panel_visible(captured[1]):
                return False
        if self._shop_panel_visible(captured[1]):
            if not self._safe_click("shop_close"):
                return False
            time.sleep(0.35)
            captured = self._shop_capture_without_tooltip()
        return captured is not None and not self._shop_panel_visible(captured[1]) and not self._shop_scheme_panel_visible(captured[1])

    def _configure_shop(self) -> bool:
        now = time.monotonic()
        if not getattr(self, "shop_setup_started", 0.0):
            self.shop_setup_started = now
        self.shop_setup_attempts = getattr(self, "shop_setup_attempts", 0) + 1
        self.state = f"开局商店：第{min(self.shop_setup_attempts,4)}/4次尝试，复核{self.shop_confirmations}/2"
        if now-self.shop_setup_started > 60 or self.shop_setup_attempts > 4:
            if self._close_shop_verified():
                self.shop_setup_deferred = True
                self.state = "游戏中 · 出装未确认"
                self.log.error("商店初始化未通过，已确认关闭界面：继续基本作战，自动出装仍标为未确认；本局不再反复开店，请人工检查。避免在出生点持续暂停")
            else:
                self.active.clear()
                self.window.release_cursor()
                self.state = "商店无法关闭：请人工处理"
                self.log.error("商店初始化失败且界面未能关闭：暂停输入，请立即人工处理")
            return False
        self.last_shop_attempt = time.monotonic()
        shop_key = self.config["combat"].get("shop_key", "f4")
        captured = self._shop_capture_without_tooltip()
        if captured is None:
            self.log.warning("商店初始化失败：无法读取商店画面")
            return False
        if self._shop_scheme_panel_visible(captured[1]):
            selected = self._select_shop_scheme(captured[1])
            if selected is None:
                return False
            captured = (captured[0], selected)
        if not self._shop_panel_visible(captured[1]):
            if not self.window.key_tap(shop_key):
                self.log.warning("商店初始化失败：未能发送 %s，稍后重试", shop_key.upper())
                return False
            self.log.info("操作：按下 %s 打开商店", shop_key.upper())
            time.sleep(float(self.config["timing"].get("shop_open_delay", 0.8)))
            captured = self._shop_capture_without_tooltip()
            if captured is None or not self._shop_panel_visible(captured[1]):
                self.log.warning("商店：按下 %s 后未确认商店已经打开，稍后重试", shop_key.upper())
                return False
        else:
            self.log.info("商店：检测到面板已经打开，不重复按 %s", shop_key.upper())
        panel_settle = max(0.5, float(self.config["timing"].get("shop_panel_settle_delay", 1.0)))
        time.sleep(panel_settle)
        captured = self._shop_capture_without_tooltip()
        if captured is None or not self._shop_panel_visible(captured[1]):
            self.log.warning("商店：等待界面稳定后面板不可见，稍后重试")
            return False
        selected_image = self._select_shop_scheme(captured[1])
        if selected_image is None:
            return False
        captured_image = selected_image
        if self._shop_auto_buy_checked(captured_image):
            self.log.info("商店：检测到自动出装绿色勾，保持面板开启进行稳定复核")
        else:
            checkbox = self._shop_checkbox_point(captured_image)
            if checkbox is None:
                self.log.warning("商店初始化失败：未能根据自动出装文字定位复选框中心")
                return False
            if not self._safe_click_point(checkbox, "动态定位的自动出装复选框中心"):
                self.log.warning("商店初始化失败：复选框中心不可点击")
                return False
            self.log.info("操作：自动出装未勾选，点击复选框中心 %s", checkbox)
            time.sleep(max(0.8, float(self.config["timing"].get("shop_click_delay", 0.8))))
        if not self._confirm_shop_auto_buy_stable():
            self.log.warning("商店：绿色勾未通过持续确认；关闭商店后稍后重新打开补勾")
            self._close_shop_verified()
            return False
        if self._close_shop_verified():
            self.shop_confirmations += 1
            required = max(2, int(self.config["timing"].get("shop_start_confirmations", 2)))
            if self.shop_confirmations < required:
                self.shop_configured = False
                self.last_shop_attempt = time.monotonic()
                self.log.info(
                    "商店：开局第 %s/%s 次确认成功并已关闭；短暂等待后将重新打开做第二次独立复核",
                    self.shop_confirmations,
                    required,
                )
                return False
            self.shop_configured = True
            self.log.info("商店：开局 %s 次确认全部通过，关闭商店并永久结束本局商店检查", required)
            return True
        else:
            self.log.warning("商店关闭失败；稍后将先确认面板状态再重试")
            return False

    def _find_enemy_target(self, screenshot: Image.Image) -> list[int] | None:
        x, y, width, height = self.config["coordinates"].get("combat_region", [130, 70, 840, 500])
        if x < 0 or y < 0 or x + width > screenshot.width or y + height > screenshot.height:
            return None
        rgb = np.asarray(screenshot.crop((x, y, x + width, y + height)).convert("RGB"), dtype=np.int16)
        red, green, blue = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
        red_mask = (red >= 145) & (red >= green * 1.38) & (red >= blue * 1.30) & (green <= 125)
        blue_mask = (blue >= 105) & (blue >= red * 1.18) & (blue >= green * 1.05)

        runs: list[tuple[int, int, int]] = []
        for row in range(height):
            columns = np.flatnonzero(red_mask[row])
            if columns.size == 0:
                continue
            starts = np.r_[0, np.flatnonzero(np.diff(columns) > 1) + 1]
            ends = np.r_[starts[1:] - 1, columns.size - 1]
            for start, end in zip(starts, ends):
                left, right = int(columns[start]), int(columns[end])
                if 14 <= right - left + 1 <= 130:
                    runs.append((left, right, row))

        groups: list[list[int]] = []
        for left, right, row in runs:
            group = None
            for candidate in groups:
                if row - candidate[3] > 2:
                    continue
                overlap = min(right, candidate[1]) - max(left, candidate[0]) + 1
                if overlap >= min(right - left + 1, candidate[1] - candidate[0] + 1) * 0.55:
                    group = candidate
                    break
            if group is None:
                groups.append([left, right, row, row, 1])
            else:
                group[0] = min(group[0], left)
                group[1] = max(group[1], right)
                group[3] = row
                group[4] += 1

        candidates: list[dict[str, Any]] = []
        for left, right, top, bottom, rows in groups:
            bar_width = right - left + 1
            bar_height = bottom - top + 1
            if bar_width < 24 or not (2 <= bar_height <= 16) or rows < 4:
                continue
            strip_left, strip_right = max(0, left - 8), min(width, right + 35)
            strip_top, strip_bottom = min(height, bottom + 1), min(height, bottom + 14)
            blue_columns = np.flatnonzero(np.any(blue_mask[strip_top:strip_bottom, strip_left:strip_right], axis=0))
            if blue_columns.size == 0:
                continue
            starts = np.r_[0, np.flatnonzero(np.diff(blue_columns) > 1) + 1]
            ends = np.r_[starts[1:] - 1, blue_columns.size - 1]
            longest = max(zip(starts, ends), key=lambda pair: int(blue_columns[pair[1]]) - int(blue_columns[pair[0]]))
            blue_left = strip_left + int(blue_columns[longest[0]])
            blue_right = strip_left + int(blue_columns[longest[1]])
            if blue_right - blue_left + 1 < 8:
                continue
            target = [x + (blue_left + blue_right) // 2, min(screenshot.height - 1, y + bottom + 42)]
            distance = ((target[0] - screenshot.width * 0.5) ** 2 + (target[1] - screenshot.height * 0.45) ** 2) ** 0.5
            score = bar_width * rows + int(blue_mask[strip_top:strip_bottom, strip_left:strip_right].sum()) - distance * 0.12
            candidates.append({"point": target, "bar_width": float(bar_width), "base_score": float(score)})

        if not candidates:
            self.enemy_track_memory = []
            self.last_target_reason = ""
            self.last_target_friendly_count = 0
            return None

        # Friendly heroes, soldiers and buildings normally expose a horizontal
        # green health bar.  Use compact green runs as a visual approximation
        # of where the friendly group is currently fighting.
        friendly_mask = (green >= 120) & (green >= red * 1.18) & (green >= blue * 1.04) & (red <= 150)
        friendly_points: list[tuple[int, int]] = []
        for row in range(height):
            columns = np.flatnonzero(friendly_mask[row])
            if columns.size == 0:
                continue
            starts = np.r_[0, np.flatnonzero(np.diff(columns) > 1) + 1]
            ends = np.r_[starts[1:] - 1, columns.size - 1]
            for start, end in zip(starts, ends):
                left, right = int(columns[start]), int(columns[end])
                if 18 <= right - left + 1 <= 170:
                    point = (x + (left + right) // 2, y + row + 30)
                    if all(abs(point[1] - old[1]) > 5 or abs(point[0] - old[0]) > 16 for old in friendly_points):
                        friendly_points.append(point)

        focus_radius = float(self.config["combat"].get("friendly_focus_radius", 190.0))
        old_tracks = self.enemy_track_memory
        new_tracks: list[dict[str, float]] = []
        for candidate in candidates:
            tx, ty = candidate["point"]
            nearby = 0
            proximity = 0.0
            for fx, fy in friendly_points:
                distance = ((tx - fx) ** 2 + (ty - fy) ** 2) ** 0.5
                if distance <= focus_radius:
                    nearby += 1
                    proximity += 1.0 - distance / focus_radius

            previous = None
            previous_distance = 10_000.0
            for track in old_tracks:
                distance = ((tx - track["x"]) ** 2 + (ty - track["y"]) ** 2) ** 0.5
                if distance < previous_distance and distance <= 80.0:
                    previous = track
                    previous_distance = distance
            losing_health = bool(previous is not None and previous["bar_width"] - candidate["bar_width"] >= 3.0)
            candidate["friendly_count"] = nearby
            candidate["friendly_proximity"] = proximity
            candidate["losing_health"] = losing_health
            new_tracks.append({"x": float(tx), "y": float(ty), "bar_width": candidate["bar_width"]})

        self.enemy_track_memory = new_tracks
        if self.config["combat"].get("prioritize_friendly_target", True):
            # A shrinking red bar is the strongest visual clue that allies are
            # already attacking it.  Otherwise prefer the enemy surrounded by
            # the greatest friendly presence, then fall back to old scoring.
            best = max(
                candidates,
                key=lambda item: (
                    int(item["losing_health"]),
                    int(item["friendly_count"]),
                    float(item["friendly_proximity"]),
                    float(item["base_score"]),
                ),
            )
            if best["losing_health"]:
                self.last_target_reason = "友军正在造成伤害"
            elif best["friendly_count"]:
                self.last_target_reason = f"友军集火附近({best['friendly_count']})"
            else:
                self.last_target_reason = "常规目标"
        else:
            best = max(candidates, key=lambda item: float(item["base_score"]))
            self.last_target_reason = "常规目标"
        self.last_target_friendly_count = int(best.get("friendly_count", 0))
        return best["point"]

    def _minimap_lane_mask(self, width: int, height: int, lane: str) -> np.ndarray:
        """Return a minimap mask for the selected top/middle/bottom lane."""
        yy, xx = np.mgrid[0:height, 0:width]
        nx = xx / max(1, width - 1)
        ny = yy / max(1, height - 1)
        corridor = float(self.config["combat"].get("defense_lane_corridor", 0.24))
        corridor = min(0.36, max(0.12, corridor))
        if lane == "top":
            return ((nx <= corridor) & (ny <= 0.88)) | ((ny <= corridor) & (nx <= 0.88))
        if lane == "bottom":
            return ((ny >= 1.0 - corridor) & (nx >= 0.12)) | ((nx >= 1.0 - corridor) & (ny >= 0.12))
        if lane == "middle":
            return np.abs(nx + ny - 1.0) <= corridor
        return np.ones((height, width), dtype=bool)

    def _find_minimap_enemy_cluster(self, screenshot: Image.Image, lane: str = "off") -> list[int] | None:
        x, y, width, height = self.config["coordinates"]["minimap_region"]
        crop = np.asarray(screenshot.crop((x, y, x + width, y + height)).convert("RGB"), dtype=np.int16)
        red, green, blue = crop[:, :, 0], crop[:, :, 1], crop[:, :, 2]
        mask = (red >= 140) & (red >= green * 1.35) & (red >= blue * 1.20) & (green <= 135)
        if lane in {"top", "middle", "bottom"}:
            mask &= self._minimap_lane_mask(width, height, lane)
        window_size = 11
        windows = np.lib.stride_tricks.sliding_window_view(mask.astype(np.uint8), (window_size, window_size))
        scores = windows.sum(axis=(2, 3))
        best_index = np.unravel_index(int(np.argmax(scores)), scores.shape)
        minimum = max(1, int(self.config["combat"].get("defense_lane_min_red_pixels", 5)))
        if int(scores[best_index]) < minimum:
            return None
        return [x + int(best_index[1]) + window_size // 2, y + int(best_index[0]) + window_size // 2]

    def _minimap_color_masks(self, screenshot: Image.Image) -> tuple[np.ndarray, np.ndarray]:
        """Return conservative red-enemy and green/blue-friendly minimap masks."""
        x, y, width, height = self.config["coordinates"]["minimap_region"]
        crop = np.asarray(screenshot.crop((x, y, x + width, y + height)).convert("RGB"), dtype=np.int16)
        red, green, blue = crop[:, :, 0], crop[:, :, 1], crop[:, :, 2]
        enemy = (red >= 150) & (red >= green * 1.42) & (red >= blue * 1.28) & (green <= 125)
        friendly_green = (green >= 125) & (green >= red * 1.16) & (green >= blue * 1.02)
        friendly_blue = (blue >= 125) & (blue >= red * 1.18) & (blue >= green * 1.08)
        return enemy, friendly_green | friendly_blue

    def _find_minimap_friendly_group(self, screenshot: Image.Image) -> tuple[list[int], int, int] | None:
        """Locate the strongest non-base friendly cluster on the minimap."""
        x, y, width, height = self.config["coordinates"]["minimap_region"]
        enemy, friendly = self._minimap_color_masks(screenshot)
        window_size = max(15, int(self.config["combat"].get("team_group_window", 21)))
        if window_size >= min(width, height):
            return None
        friendly_scores = np.lib.stride_tricks.sliding_window_view(
            friendly.astype(np.uint8), (window_size, window_size)
        ).sum(axis=(2, 3))
        enemy_scores = np.lib.stride_tricks.sliding_window_view(
            enemy.astype(np.uint8), (window_size, window_size)
        ).sum(axis=(2, 3))
        minimum = max(4, int(self.config["combat"].get("team_group_min_friendly_pixels", 10)))
        valid = friendly_scores >= minimum
        # The friendly base is bottom-left. It remains the safe fallback for
        # retreat, but should not outrank a real moving group during follow.
        yy, xx = np.indices(friendly_scores.shape)
        centers_x = xx + window_size // 2
        centers_y = yy + window_size // 2
        # Both base corners contain permanent buildings/portraits. Team side
        # changes between matches; neither corner is a reliable moving group.
        home_mask = ((xx <= width * 0.32) & (yy+window_size >= height * 0.68)) | ((xx+window_size >= width * 0.68) & (yy <= height * 0.32))
        valid &= ~home_mask
        if not np.any(valid):
            return None
        contested = valid & (enemy_scores >= 8)
        if self.config["combat"].get("prefer_contested_friendly_group", True) and np.any(contested):
            valid = contested
        score = np.minimum(friendly_scores, 80).astype(np.float32) + np.minimum(enemy_scores, 30) * 2.0
        score[~valid] = -1.0
        row, col = np.unravel_index(int(np.argmax(score)), score.shape)
        return (
            [x + int(col) + window_size // 2, y + int(row) + window_size // 2],
            int(friendly_scores[row, col]),
            int(enemy_scores[row, col]),
        )

    def _retreat_to_team_or_home(self, screenshot: Image.Image, now: float) -> bool:
        interval = float(self.config["timing"].get("retreat_interval", 4.0))
        if now - self.last_retreat < interval:
            return True
        group = self._find_minimap_friendly_group(screenshot)
        if group is not None:
            point, friendly_count, enemy_count = group
            label = f"友方主力撤退点(友={friendly_count},红={enemy_count})"
        else:
            point = self._friendly_home_from_minimap(screenshot)
            if point is None:
                self.last_retreat = now
                self.state = "落单：等待可靠友方位置"
                self.log.warning("撤退：没有可靠友军或基地阵营证据，不盲点固定左下角")
                return True
            label = "按小地图阵营证据选择的基地撤退点"
        self.last_retreat = now
        moved = self._move_to_tactical_point(point, label)
        if moved:
            self.state = "落单撤退"
            self.log.info("战术撤退：敌方在场但缺少友军支援，停止追击并前往%s %s", label, point)
        return True

    def _friendly_home_from_minimap(self, screenshot: Image.Image) -> list[int] | None:
        x, y, width, height = self.config["coordinates"]["minimap_region"]
        enemy, friendly = self._minimap_color_masks(screenshot)
        cw, ch = max(1, int(width*.30)), max(1, int(height*.30))
        corners = [(slice(height-ch,height),slice(0,cw)),(slice(0,ch),slice(width-cw,width))]
        greens = [int(friendly[region].sum()) for region in corners]
        scores = [greens[i]-int(enemy[region].sum())*.5 for i,region in enumerate(corners)]
        if max(greens) < 10 or abs(scores[0]-scores[1]) < 8:
            return None
        return [x+12,y+height-12] if scores[0]>scores[1] else [x+width-12,y+12]

    def _should_retreat_isolated(self, now: float) -> bool:
        limit = max(0, int(self.config["combat"].get("isolated_friendly_max", 1)))
        if not self.config["combat"].get("retreat_when_isolated", True) or self.last_target_friendly_count > limit:
            self.isolated_since = 0.0
            self.isolated_samples = 0
            return False
        if not getattr(self, "isolated_since", 0.0):
            self.isolated_since = now
            self.isolated_samples = 1
            return False
        self.isolated_samples = getattr(self, "isolated_samples", 0) + 1
        return self.isolated_samples >= 3 and now - self.isolated_since >= 2.0

    def _follow_friendly_group(self, screenshot: Image.Image, now: float) -> bool:
        interval = max(6.0, float(self.config["timing"].get("team_follow_interval", 5.0)))
        if now - self.last_group_follow < interval:
            # Continue the successful group movement instead of immediately
            # overriding it with a random minimap move/teleport in the next frame.
            return self.last_group_follow > 0.0
        group = self._find_minimap_friendly_group(screenshot)
        if group is None:
            return False
        point, friendly_count, enemy_count = group
        if not self._move_to_tactical_point(point, "友方主力集结点"):
            return False
        self.last_group_follow = now
        self.state = "跟随友方主力"
        self.log.info("跟团：小地图友方候选区 %s（绿像素=%s，红像素=%s），移动至少保留%.1f秒；不是已确认英雄人数", point, friendly_count, enemy_count, interval)
        return True

    def _find_minimap_boss_cross(self, screenshot: Image.Image) -> list[int] | None:
        """Find the large red orthogonal BOSS marker without mistaking normal red dots."""
        x, y, width, height = self.config["coordinates"]["minimap_region"]
        enemy, _friendly = self._minimap_color_masks(screenshot)
        radius = max(4, int(self.config["combat"].get("boss_cross_radius", 6)))
        arm_min = max(3, int(self.config["combat"].get("boss_cross_arm_pixels", 5)))
        total_min = max(9, int(self.config["combat"].get("boss_cross_min_pixels", 13)))
        axis_ratio_min = float(self.config["combat"].get("boss_cross_axis_ratio", 0.42))
        best: tuple[float, list[int]] | None = None
        diameter = radius*2+1
        if min(enemy.shape) < diameter:
            return None
        horizontal_counts = np.lib.stride_tricks.sliding_window_view(enemy, diameter, axis=1).sum(axis=2)[radius:-radius]
        vertical_counts = np.lib.stride_tricks.sliding_window_view(enemy, diameter, axis=0).sum(axis=2)[:, radius:-radius]
        candidates = np.argwhere((horizontal_counts >= arm_min) & (vertical_counts >= arm_min))
        # Only inspect axis-qualified pixels; retain the original scoring/tie order.
        for row, col in candidates:
                cy, cx = int(row)+radius, int(col)+radius
                horizontal = int(enemy[cy, cx - radius : cx + radius + 1].sum())
                vertical = int(enemy[cy - radius : cy + radius + 1, cx].sum())
                if horizontal < arm_min or vertical < arm_min:
                    continue
                patch = enemy[cy - radius : cy + radius + 1, cx - radius : cx + radius + 1]
                total = int(patch.sum())
                if total < total_min:
                    continue
                # Crosses concentrate pixels on the two axes.  Dense square
                # hero clusters and ordinary circular red dots score lower.
                axis_pixels = horizontal + vertical - int(enemy[cy, cx])
                off_axis = max(0, total - axis_pixels)
                if axis_pixels / max(1, total) < axis_ratio_min:
                    continue
                score = axis_pixels * 2.0 - off_axis * 0.45
                if best is None or score > best[0]:
                    best = (score, [x + cx, y + cy])
        return best[1] if best is not None else None

    def _find_minimap_tower_fight(self, screenshot: Image.Image) -> tuple[str, list[int], int, int] | None:
        """Find mixed red/friendly fights and classify them by map territory.

        The map is oriented with the friendly base at bottom-left and the enemy
        base at top-right.  Mixed colour clusters deep in either half are the
        safest visual proxy for a tower attack/defence without reading memory.
        """
        x, y, width, height = self.config["coordinates"]["minimap_region"]
        enemy, friendly = self._minimap_color_masks(screenshot)
        window_size = max(11, int(self.config["combat"].get("tower_fight_window", 15)))
        if window_size >= min(width, height):
            return None
        red_scores = np.lib.stride_tricks.sliding_window_view(enemy.astype(np.uint8), (window_size, window_size)).sum(axis=(2, 3))
        friendly_scores = np.lib.stride_tricks.sliding_window_view(friendly.astype(np.uint8), (window_size, window_size)).sum(axis=(2, 3))
        red_min = max(2, int(self.config["combat"].get("tower_fight_min_red_pixels", 4)))
        friendly_min = max(2, int(self.config["combat"].get("tower_fight_min_friendly_pixels", 4)))
        valid = (red_scores >= red_min) & (friendly_scores >= friendly_min)
        if not np.any(valid):
            return None
        combined = red_scores * 1.15 + friendly_scores
        combined[~valid] = -1
        candidates = np.argwhere(valid)
        territory_margin = float(self.config["combat"].get("tower_territory_margin", 0.10))
        best: tuple[float, str, list[int], int, int] | None = None
        for row, col in candidates:
            cx = int(col) + window_size // 2
            cy = int(row) + window_size // 2
            territory = cx / max(1, width - 1) - cy / max(1, height - 1)
            if territory >= territory_margin:
                kind = "attack"
            elif territory <= -territory_margin:
                kind = "defend"
            else:
                continue
            red_count = int(red_scores[row, col])
            friendly_count = int(friendly_scores[row, col])
            # Defence is slightly more urgent at equal activity.
            score = float(combined[row, col]) + (3.0 if kind == "defend" else 0.0)
            if best is None or score > best[0]:
                best = (score, kind, [x + cx, y + cy], red_count, friendly_count)
        if best is None:
            return None
        return best[1], best[2], best[3], best[4]

    def _scene_has_friendly_activity(self, screenshot: Image.Image) -> bool:
        x, y, width, height = self.config["coordinates"].get("combat_region", [130, 70, 840, 500])
        rgb = np.asarray(screenshot.crop((x, y, x + width, y + height)).convert("RGB"), dtype=np.int16)
        red, green, blue = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
        mask = (green >= 125) & (green >= red * 1.18) & (green >= blue * 1.03)
        minimum_run = max(16, int(self.config["combat"].get("friendly_activity_run", 22)))
        for row in range(height):
            columns = np.flatnonzero(mask[row])
            if columns.size < minimum_run:
                continue
            splits = np.r_[0, np.flatnonzero(np.diff(columns) > 1) + 1, columns.size]
            if any(int(columns[splits[index + 1] - 1] - columns[splits[index]] + 1) >= minimum_run for index in range(len(splits) - 1)):
                return True
        return False

    def _double_click_minimap(self, point: list[int], label: str) -> bool:
        if not self._safe_click_point(point, label, "left"):
            return False
        self.self_selected = False
        time.sleep(float(self.config["timing"].get("minimap_double_click_gap", 0.10)))
        return self._safe_click_point(point, label, "left")

    def _move_to_tactical_point(self, point: list[int], label: str, double_click: bool = False) -> bool:
        if self.config["combat"].get("minimap_move_mode", "direct") == "direct":
            # Move to the map coordinate itself. Camera-centre right clicks are
            # displaced when the viewport is clamped near map edges/buildings.
            if not self._safe_click_point(point, f"小地图直接移动：{label}", "right"):
                return False
            self.last_move = time.monotonic()
            self.last_tactical_move = self.last_move
            return True
        clicked = self._double_click_minimap(point, label) if double_click else self._safe_click_point(point, label, "left")
        if not clicked:
            return False
        self.self_selected = False
        time.sleep(float(self.config["timing"].get("view_switch_delay", 0.25)))
        if not self._safe_click_ratio("ground_move_ratio", f"前往{label}", "right"):
            self._focus_self("战术移动点击失败，返回自身视角")
            return False
        self.last_move = time.monotonic()
        self.last_tactical_move = self.last_move
        return True

    def _find_friendly_teleport_target(self, screenshot: Image.Image) -> list[int] | None:
        x, y, width, height = self.config["coordinates"].get("combat_region", [130, 70, 840, 500])
        rgb = np.asarray(screenshot.crop((x, y, x + width, y + height)).convert("RGB"), dtype=np.int16)
        red, green, blue = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
        mask = (green >= 130) & (green >= red * 1.20) & (green >= blue * 1.05)
        best: tuple[int, list[int]] | None = None
        for row in range(height):
            columns = np.flatnonzero(mask[row])
            if columns.size == 0:
                continue
            starts = np.r_[0, np.flatnonzero(np.diff(columns) > 1) + 1]
            ends = np.r_[starts[1:] - 1, columns.size - 1]
            for start, end in zip(starts, ends):
                left, right = int(columns[start]), int(columns[end])
                run_width = right - left + 1
                if 25 <= run_width <= 160:
                    target = [x + (left + right) // 2, min(screenshot.height - 1, y + row + 38)]
                    if best is None or run_width > best[0]:
                        best = (run_width, target)
        return best[1] if best is not None else None

    def _teleport_ready(self, screenshot: Image.Image) -> bool:
        located = self.detector.locate("teleport_ready", screenshot)
        if located is None:
            return False
        return located[1] >= float(self.config["detectors"]["teleport_ready"].get("threshold", 0.88))

    def _focus_self(self, reason: str) -> bool:
        taps = max(2, int(self.config["combat"].get("self_focus_taps", 2)))
        gap = float(self.config["timing"].get("self_focus_gap", 0.12))
        for index in range(taps):
            if not self.window.key_tap("f1"):
                return False
            if index + 1 < taps:
                time.sleep(gap)
        self.log.info("操作：连续按 %s 次 F1，%s", taps, reason)
        return True

    def _try_teleport(self, screenshot: Image.Image, lane: str = "off") -> bool:
        destination = self._find_minimap_enemy_cluster(screenshot, lane)
        labels = {"top": "上路", "middle": "中路", "bottom": "下路"}
        target_label = f"小地图{labels.get(lane, '敌方')}红点区域"
        if destination is None:
            return False
        return self._try_teleport_to_point(destination, target_label)

    def _try_teleport_to_point(self, destination: list[int], target_label: str) -> bool:
        if not self._safe_click_point(destination, target_label):
            return False
        time.sleep(float(self.config["timing"].get("view_switch_delay", 0.25)))
        captured = self.window.grab()
        if captured is None:
            self._focus_self("传送取图失败，返回自身视角")
            return False
        friendly = self._find_friendly_teleport_target(captured[1])
        if friendly is None:
            self._focus_self("未找到传送目标，返回自身视角")
            return False
        teleport_key = self.config["combat"].get("teleport_key", "b")
        if not self.window.key_tap(teleport_key):
            return False
        time.sleep(float(self.config["timing"].get("key_gap", 0.12)))
        if not self._safe_click_point(friendly, "传送目标"):
            self._focus_self("传送目标点击失败，返回自身视角")
            return False
        self.log.info("传送：B 可用，已选择远处友方建筑或单位 %s", friendly)
        time.sleep(float(self.config["timing"].get("teleport_channel_wait", 3.5)))
        self._focus_self("传送后选中自己并回到自身视角")
        self.last_move = 0.0
        return True

    def _handle_boss_or_tower_priority(self, screenshot: Image.Image, now: float, mode: str = "all") -> bool:
        """Handle BOSS support and tower fights before ordinary roaming."""
        combat = self.config["combat"]
        # Legacy colour-cluster heuristics are not evidence of a tower attack.
        # Keep this experiment opt-in; never silently enable it from old config.
        if not combat.get("experimental_support_enabled", False):
            return False
        timing = self.config["timing"]
        if mode != "tower" and combat.get("boss_support_enabled", True):
            boss_interval = float(timing.get("boss_observe_interval", 4.0))
            boss = self._find_minimap_boss_cross(screenshot) if now - self.last_boss_observe >= boss_interval else None
            if boss is not None:
                self.last_boss_observe = now
                if self._double_click_minimap(boss, "BOSS 红色十字"):
                    self.log.info("BOSS：发现红色十字 %s，双击小地图观察争夺状态", boss)
                    time.sleep(float(timing.get("boss_view_settle", 0.45)))
                    captured = self.window.grab()
                    if captured is not None:
                        enemy = self._find_enemy_target(captured[1])
                        friendly_present = self._scene_has_friendly_activity(captured[1])
                        if enemy is not None or friendly_present:
                            self.log.info(
                                "BOSS：区域内检测到%s，提升为最高支援优先级",
                                "敌方目标" if enemy is not None else "友方单位",
                            )
                            if self._teleport_ready(screenshot) and now - self.last_teleport_attempt >= float(timing.get("teleport_retry", 15.0)):
                                self.last_teleport_attempt = now
                                if self._try_teleport_to_point(boss, "BOSS 支援点"):
                                    return True
                            if enemy is not None:
                                attack_key = combat.get("attack_key", "a")
                                if self.window.key_tap(attack_key):
                                    time.sleep(float(timing.get("key_gap", 0.12)))
                                    self._safe_click_point(enemy, "BOSS 区域敌方目标")
                            return self._move_to_tactical_point(boss, "BOSS 支援点", double_click=False)
                    self._focus_self("BOSS 区域暂时无人，返回自身视角")

        if mode != "boss" and combat.get("tower_support_enabled", True):
            tower_interval = float(timing.get("tower_support_interval", 3.0))
            if now - self.last_tower_support >= tower_interval:
                tower = self._find_minimap_tower_fight(screenshot)
                if tower is not None:
                    kind, point, red_count, friendly_count = tower
                    self.last_tower_support = now
                    if kind == "defend":
                        self.log.info("防御塔：我方半场出现交战 红=%s 友=%s，优先就近防御", red_count, friendly_count)
                        if self._teleport_ready(screenshot) and now - self.last_teleport_attempt >= float(timing.get("teleport_retry", 15.0)):
                            self.last_teleport_attempt = now
                            if self._try_teleport_to_point(point, "我方防御塔防守点"):
                                return True
                        return self._move_to_tactical_point(point, "我方防御塔防守点")
                    self.log.info("防御塔：友军在敌方半场推进 红=%s 友=%s，前往协同攻击", red_count, friendly_count)
                    return self._move_to_tactical_point(point, "敌方防御塔协同攻击点")
        return False

    def _locate_talent_choice(self, image: Image.Image, choice: int) -> list[int] | None:
        """Require agreement between visible rows, not one obscured fifth icon."""
        if not self.detector.matches("talent", image):
            return None
        hits = []
        for index in (1, 2, 3):
            name = f"talent_anchor_{index}"
            if name not in self.config["detectors"]:
                continue
            hit = self.detector.locate(name, image)
            if hit is not None and hit[1] >= float(self.config["detectors"][name].get("threshold", .94)):
                hits.append((index, hit[0]))
        for i, first in enumerate(hits):
            for second in hits[i+1:]:
                pitch = (second[1][1]-first[1][1])/(second[0]-first[0])
                if abs(first[1][0]-second[1][0]) <= 6 and 43 <= pitch <= 48:
                    point = [round((first[1][0]+second[1][0])/2), round(first[1][1]+(choice-first[0])*pitch)]
                    if 680 <= point[0] <= 750 and 330 <= point[1] <= 580:
                        return point
        legacy = self.detector.locate("talent_5", image)
        if legacy is not None and legacy[1] >= float(self.config["detectors"]["talent_5"].get("threshold", .92)):
            spacing = int(self.config["coordinates"].get("talent_option_spacing", 46))
            return [legacy[0][0], legacy[0][1]-(5-choice)*spacing]
        return None

    def _fight(self, screenshot: Image.Image) -> None:
        now = time.monotonic()
        defense_lane = self.config.get("entry", {}).get("defense_lane", "off")
        if defense_lane not in {"top", "middle", "bottom"}:
            defense_lane = "off"
        lane_labels = {"top": "上路", "middle": "中路", "bottom": "下路"}
        state_suffixes: list[str] = []
        if defense_lane != "off":
            state_suffixes.append(f"{lane_labels[defense_lane]}防守")
        if self.score_cap_reached:
            state_suffixes.append("战绩已满")
        if state_suffixes:
            self.state = "游戏中 · " + " · ".join(state_suffixes)
        if not self.self_selected or now-getattr(self, "last_self_focus", 0.0) >= 8.0:
            if self._focus_self("选中自己并进入自身视角"):
                self.self_selected = True
                self.last_self_focus = time.monotonic()
                fresh = self.window.grab()
                if fresh is None:
                    return
                screenshot = fresh[1]
            else:
                return
        talent_choice = min(5, max(1, int(self.config.get("entry", {}).get("talent_choice", 5))))
        summon_interval = float(self.config["timing"].get("summon_interval", 60.0))
        if talent_choice == 5 and now - self.last_summon >= summon_interval:
            summon_key = self.config["combat"].get("summon_key", "z")
            if self.window.key_tap(summon_key):
                self.log.info("操作：按下 %s 键召唤背包内援兵", str(summon_key).upper())
                self.last_summon = now

        # Local combat is evaluated before any optional remote support task.
        enemy = self._find_enemy_target(screenshot) if self.config["combat"].get("targeting_enabled", True) else None
        if enemy is not None:
            self.no_enemy_since = 0.0
            isolated_max = max(0, int(self.config["combat"].get("isolated_friendly_max", 1)))
            if self._should_retreat_isolated(now):
                self._retreat_to_team_or_home(screenshot, now)
                return
            inventory_interval = float(self.config["timing"].get("inventory_item_interval", 8.0))
            if self.config["combat"].get("use_counted_inventory_items", True) and now - self.last_inventory_use >= inventory_interval:
                self._use_counted_inventory_items(screenshot)
                self.last_inventory_use = time.monotonic()
            chase_interval = float(self.config["timing"].get("chase_click_interval", 0.75))
            if self.config["combat"].get("chase_target_enabled", True) and now - self.last_chase_click >= chase_interval:
                if self._safe_click_point(enemy, "追击并持续攻击敌方目标", "right"):
                    self.last_chase_click = time.monotonic()
            if now - self.last_target_attack >= float(self.config["timing"].get("target_attack_interval", 4.5)):
                attack_key = self.config["combat"].get("attack_key", "a")
                if self.window.key_tap(attack_key):
                    time.sleep(float(self.config["timing"].get("key_gap", 0.12)))
                    self._safe_click_point(enemy, "识别到的红色敌方目标")
                    self.log.info("攻击目标：%s %s", self.last_target_reason, enemy)
                    self.last_target_attack = time.monotonic()
            skill_keys = [str(key) for key in self.config["combat"].get("skill_keys", ["q", "w", "e", "r"])]
            configured_intervals = self.config["timing"].get("skill_slot_intervals", [10.0, 16.0, 22.0, 35.0])
            skill_intervals = [float(value) for value in configured_intervals]
            while len(skill_intervals) < len(skill_keys):
                skill_intervals.append(skill_intervals[-1] if skill_intervals else 18.0)
            for offset in range(len(skill_keys)):
                slot = (self.next_skill_slot + offset) % len(skill_keys)
                key = skill_keys[slot]
                if now - self.last_skill_use.get(key, 0.0) < skill_intervals[slot]:
                    continue
                skill_target = enemy
                if self.config["combat"].get("aim_skills_at_enemy", True):
                    fresh = self.window.grab()
                    if fresh is None:
                        break
                    refreshed_target = self._find_enemy_target(fresh[1])
                    if refreshed_target is None:
                        self.log.debug("技能取消：敌方目标已消失，不点击旧位置")
                        break
                    if self._should_retreat_isolated(time.monotonic()):
                        self._retreat_to_team_or_home(fresh[1], time.monotonic())
                        return
                    skill_target = refreshed_target
                if self.window.key_tap(key):
                    time.sleep(float(self.config["timing"].get("key_gap", 0.12)))
                    if self.config["combat"].get("aim_skills_at_enemy", True):
                        self._safe_click_point(skill_target, f"指向性技能 {key.upper()} 的敌方目标")
                    self.last_skill_use[key] = time.monotonic()
                    self.last_skill = self.last_skill_use[key]
                    self.next_skill_slot = (slot + 1) % len(skill_keys)
                    self.log.info("技能节奏：释放第 %s 槽 %s，下一次该槽至少等待 %.1f 秒", slot + 1, key.upper(), skill_intervals[slot])
                break
            return

        if self.no_enemy_since <= 0.0:
            self.no_enemy_since = now
        self.isolated_since = 0.0
        self.isolated_samples = 0
        if self._handle_boss_or_tower_priority(screenshot, now):
            return
        if not self.self_selected:
            return
        if self._try_open_cards(screenshot, now):
            return
        if self.config["combat"].get("follow_friendly_group", True) and self._follow_friendly_group(screenshot, now):
            return
        no_enemy_wait = float(self.config["timing"].get("no_enemy_before_teleport", 3.0))
        teleport_retry = float(self.config["timing"].get("teleport_retry", 15.0))
        if (
            self.config["combat"].get("teleport_enabled", True)
            and now - self.no_enemy_since >= no_enemy_wait
            and now - self.last_teleport_attempt >= teleport_retry
            and self._teleport_ready(screenshot)
        ):
            self.last_teleport_attempt = now
            if self._try_teleport(screenshot, defense_lane):
                self.no_enemy_since = 0.0
                return

        if defense_lane != "off":
            lane_move_interval = float(self.config["timing"].get("defense_lane_move_interval", 3.0))
            if now - self.last_move < lane_move_interval:
                return
            target = self._find_minimap_enemy_cluster(screenshot, defense_lane)
            if target is None:
                return
            lane_label = lane_labels[defense_lane]
            if not self._safe_click_point(target, f"{lane_label}红点目标", "left"):
                return
            time.sleep(float(self.config["timing"].get("view_switch_delay", 0.25)))
            if not self._safe_click_ratio("ground_move_ratio", f"向{lane_label}红点移动", "right"):
                return
            self.last_move = time.monotonic()
            self.self_selected = False
            self.log.info("单路防守：锁定%s红点 %s，已向该位置移动", lane_label, target)
            return

        if now - self.last_move >= float(self.config["timing"]["move_interval"]):
            target = self._find_flashing_minimap_target()
            if target is None:
                return
            if not self._safe_click_point(target[0], "小地图闪动目标", "left"):
                return
            time.sleep(float(self.config["timing"].get("view_switch_delay", 0.25)))
            if not self._safe_click_ratio("ground_move_ratio", "主画面移动目标", "right"):
                return
            self.last_move = time.monotonic()
            self.self_selected = False

    def _try_open_cards(self, screenshot: Image.Image, now: float) -> bool:
        """Optional card task after local combat, with a verified-open backoff."""
        if getattr(self, "card_open_pending_at", 0.0):
            if now - self.card_open_pending_at < 1.5:
                return False
            self.card_open_pending_at = 0.0
            self.card_open_block_until = now + 60.0
            self.log.warning("翻卡：未确认面板打开，60秒内不再点击入口，继续战斗")
        if now < getattr(self, "card_open_block_until", 0.0):
            return False
        count = min(4, max(0, int(self.config.get("entry", {}).get("card_flip_count", 0))))
        if not count or self._cards_blocked_by_score():
            return False
        if now - self.last_card_open_attempt < max(20.0, float(self.config["timing"].get("card_open_retry", 8.0))):
            return False
        point = self._choose_next_card_stack(screenshot)
        if point is None:
            return False
        self.last_card_open_attempt = now
        if self._safe_click_point(point, "待确认翻卡入口"):
            self.card_open_pending_at = time.monotonic()
            self.log.info("翻卡：入口点击已发送，必须确认面板打开；不把点击视为成功")
            return True
        return False

    def _run(self) -> None:
        while not self.shutdown.is_set():
            if not self.active.is_set() or self.permanently_stopped:
                time.sleep(0.1)
                continue
            captured = self.window.grab()
            if captured is None:
                self.window.release_cursor()
                self.state = "等待有效窗口"
                self.log_once("window", "未找到符合标题与 1024×768 客户区要求的窗口；不执行操作")
                time.sleep(1.0)
                continue
            win, image = captured
            if not self.window.confine_cursor(win):
                self.state = "鼠标锁定失败"
                time.sleep(0.5)
                continue
            if not self.window.background_mode and self.config["safety"].get("require_foreground", True) and not self.window.is_foreground(win):
                self.state = "等待窗口置前"
                self.log_once("foreground", "目标窗口不在前台；不执行操作")
                time.sleep(0.5)
                continue
            self._once_keys.clear()
            try:
                hero_candidate = self.detector.locate("hero_select", image)
                self.detector.last_matches["hero_select"] = hero_candidate
                hero_threshold = float(self.config["detectors"]["hero_select"].get("threshold", 0.95))
                hero_select_visible = hero_candidate is not None and hero_candidate[1] >= hero_threshold
                hero_guard_threshold = float(
                    self.config["detectors"]["hero_select"].get("transition_guard_threshold", max(0.88, hero_threshold - 0.04))
                )
                hero_transition_visible = hero_candidate is not None and hero_candidate[1] >= hero_guard_threshold
                room_candidate = self.detector.locate("room", image)
                self.detector.last_matches["room"] = room_candidate
                room_threshold = float(self.config["detectors"]["room"].get("threshold", 0.90))
                room_score = room_candidate[1] if room_candidate is not None else 0.0
                room_detected = room_candidate is not None and room_score >= room_threshold
                # Large animated mounts/effects can briefly resemble the hero banner. A
                # confirmed hero screen still wins, but a weak transition hint must not
                # discard an almost exact Match-button result.
                strong_room_threshold = max(
                    room_threshold,
                    float(self.config["detectors"]["room"].get("strong_threshold", 0.94)),
                )
                if hero_select_visible:
                    room_detected = False
                elif hero_transition_visible and room_score < strong_room_threshold:
                    room_detected = False
                town_candidate = self.detector.locate("town_social", image)
                self.detector.last_matches["town_social"] = town_candidate
                town_spec = self.config["detectors"]["town_social"]
                town_relaxed_threshold = float(town_spec.get("relaxed_threshold", 0.84))
                town_score = town_candidate[1] if town_candidate is not None else 0.0
                town_structure_score = self._town_structure_score(image)
                town_structure_required = float(town_spec.get("min_structure_warm_ratio", 0.20))
                town_detected = (
                    town_candidate is not None
                    and town_score >= town_relaxed_threshold
                    and town_structure_score >= town_structure_required
                )
                matching_detected = self.detector.matches("matching", image)
                card_panel_visible = self._card_panel_visible(image)
                if not card_panel_visible and (self.card_panel_handled or self.card_flips_done):
                    self.card_panel_handled = False
                    self.card_flips_done = False
                    self.card_panel_handled_at = 0.0
                    self.self_selected = False
                    self.log.info("翻卡：面板已关闭，恢复自身视角和状态识别")
                if self.detector.matches("result", image):
                    self.room_wait_started = 0.0
                    self.room_last_seen_at = 0.0
                    self.state = "结算"
                    confirmed = self._state_confirmed("result")
                    if confirmed:
                        self.hero_session_active = False
                        self._finalize_battle_stats(self._result_outcome(image))
                        if self._mark_round_complete():
                            continue
                    if confirmed and time.monotonic() - self.last_action > 2.0:
                        hit = self.detector.last_matches.get("result")
                        if hit is not None and self._safe_click_point(hit[0], "识别到的关闭按钮"):
                            self.hero_selected = False
                            self.hero_select_seen_at = 0.0
                            self.hero_wait_logged = False
                            self.hero_session_active = False
                            self.talent_selected = False
                            self.talent_panel_seen = False
                            self.talent_wait_logged = False
                            self.talent_click_pending_at = 0.0
                            self.opening_sequence_seen = False
                            self.shop_configured = False
                            self.shop_scheme_selected = False
                            self.last_shop_attempt = 0.0
                            self.shop_confirmations = 0
                            self.card_panel_handled = False
                            self.card_flips_done = False
                            self.card_panel_handled_at = 0.0
                            self.last_card_open_attempt = 0.0
                            self.last_card_stack_y = 0
                            self.last_inventory_use = 0.0
                            self.score_cap_reached = False
                            self.dead_seen = False
                            self.game_started_at = 0.0
                            self.last_buyback_attempt = 0.0
                            self.buyback_attempted = False
                            self.buyback_visible_since = 0.0
                            self.buyback_last_point = None
                            self.buyback_click_count = 0
                            self.room_match_armed = True
                            self.room_wait_started = 0.0
                            self.town_social_armed = True
                            self.guild_expedition_armed = True
                            self.expedition_mode_armed = True
                            self.last_summon = 0.0
                            self.last_skill_use = {}
                            self.next_skill_slot = 0
                            self.last_chase_click = 0.0
                            self.last_target_attack = 0.0
                            self.no_enemy_since = 0.0
                            self.last_teleport_attempt = 0.0
                            self.self_selected = False
                            self.enemy_track_memory = []
                            self.last_target_reason = ""
                elif self.detector.matches("showcase", image):
                    self.room_wait_started = 0.0
                    self.room_last_seen_at = 0.0
                    self.state = "结算个人展示"
                    confirmed = self._state_confirmed("showcase")
                    if confirmed:
                        self.hero_session_active = False
                        self._finalize_battle_stats()
                        if self._mark_round_complete():
                            continue
                    if confirmed and time.monotonic() - self.last_action > 2.0:
                        dismiss_key = self.config["combat"].get("dismiss_showcase_key", "esc")
                        if self.window.key_tap(dismiss_key):
                            self.log.info("操作：按下 %s 关闭个人展示", dismiss_key.upper())
                            self.last_action = time.monotonic()
                elif card_panel_visible:
                    self.card_open_pending_at = 0.0
                    self.room_wait_started = 0.0
                    self.room_last_seen_at = 0.0
                    self._handle_card_panel(image)
                elif self.detector.matches("dead", image):
                    self.room_wait_started = 0.0
                    self.room_last_seen_at = 0.0
                    confirmed = self._state_confirmed("dead")
                    if confirmed and not self.dead_seen:
                        self.log.info("识别：死亡，等待复活")
                        self.buyback_visible_since = 0.0
                        self.buyback_last_point = None
                        self.buyback_click_count = 0
                        self.last_buyback_attempt = 0.0
                    if confirmed:
                        self.dead_seen = True
                        self._sample_battle_stats(image)
                        self.no_enemy_since = 0.0
                        self.self_selected = False
                        self.enemy_track_memory = []
                        self.last_target_reason = ""
                        buyback_status = self._try_buyback(image)
                        buyback_states = {
                            "waiting": "等待买活按钮稳定",
                            "clicked": "买活确认中",
                            "confirming": "买活确认中",
                        }
                        self.state = buyback_states.get(buyback_status, "死亡等待")
                    else:
                        self.state = "死亡等待"
                elif self.detector.matches("talent", image):
                    self.room_wait_started = 0.0
                    self.room_last_seen_at = 0.0
                    self.room_match_armed = True
                    self.state = "选择天赋"
                    confirmed = self._state_confirmed("talent")
                    if confirmed:
                        self.hero_session_active = False
                        self.opening_sequence_seen = True
                    if confirmed and self.game_started_at <= 0.0:
                        self.game_started_at = time.monotonic()
                    if confirmed and not self.talent_panel_seen:
                        self.talent_panel_seen = True
                        self.talent_wait_logged = False
                        self.talent_selected = False
                        self.talent_click_pending_at = 0.0
                        self.shop_configured = False
                        self.shop_scheme_selected = False
                        self.last_shop_attempt = 0.0
                        self.shop_confirmations = 0
                        self._reset_shop_progress()
                        self.log.info("天赋：确认面板已出现，必须等待面板消失后才视为选择成功")
                    retry = float(self.config["timing"].get("talent_retry", 1.5))
                    if confirmed and time.monotonic() - self.talent_click_pending_at >= retry:
                        if not self.active.is_set() or self.permanently_stopped or not self.window.move_cursor([500, 180]):
                            continue
                        time.sleep(0.35)
                        fresh = self.window.grab()
                        if fresh is None:
                            continue
                        talent_choice = min(5, max(1, int(self.config.get("entry", {}).get("talent_choice", 5))))
                        talent_point = self._locate_talent_choice(fresh[1], talent_choice)
                        if talent_point is not None:
                            if self._safe_click_point(talent_point, f"识别确认的第 {talent_choice} 个天赋"):
                                self.talent_click_pending_at = time.monotonic()
                                self.last_move = 0.0
                                self.last_summon = 0.0
                                self.log.info("天赋：已点击第 %s 项，等待面板消失确认；若仍存在将自动重试", talent_choice)
                        else:
                            self.log.warning("天赋：移开鼠标后定位依据仍不足，保持商店关闭；等待下一帧，不盲点")
                elif self.detector.matches("in_game", image):
                    self.room_wait_started = 0.0
                    self.room_last_seen_at = 0.0
                    self.room_match_armed = True
                    confirmed = self._state_confirmed("in_game")
                    if confirmed:
                        self.hero_session_active = False
                    if confirmed and self.game_started_at <= 0.0:
                        self.game_started_at = time.monotonic()
                    if confirmed and self.dead_seen:
                        self.log.info("识别：已复活，重新移动")
                        self.dead_seen = False
                        self.buyback_attempted = False
                        self.last_buyback_attempt = 0.0
                        self.buyback_visible_since = 0.0
                        self.buyback_last_point = None
                        self.buyback_click_count = 0
                        self.last_move = 0.0
                        self.self_selected = False
                        self.enemy_track_memory = []
                        self.last_target_reason = ""
                    self.state = "游戏中"
                    if confirmed:
                        stats_hit = self.detector.last_matches.get("in_game")
                        self._sample_battle_stats(image, stats_hit[0] if stats_hit is not None else None)
                        if not self.talent_selected:
                            talent_choice = min(5, max(1, int(self.config.get("entry", {}).get("talent_choice", 5))))
                            if self.talent_panel_seen:
                                self.log.info("天赋：面板已经消失，确认第 %s 项选择成功", talent_choice)
                            elif self.opening_sequence_seen:
                                # A normal opening can expose the in-game HUD a few
                                # frames before the talent panel is rendered.  Never
                                # treat that transient HUD as proof that talent was
                                # selected, otherwise F4 can cover the late panel.
                                self.state = "等待天赋面板"
                                if not self.talent_wait_logged:
                                    self.log.info("开局：已进入游戏画面，但天赋面板尚未识别；禁止打开商店，继续等待天赋出现")
                                    self.talent_wait_logged = True
                                time.sleep(float(self.config["timing"].get("scan_interval", 0.5)))
                                continue
                            else:
                                # The process attached to a match that was already
                                # running.  There is no reliable way to infer the
                                # earlier talent/shop state from a normal combat
                                # frame, so never replay those opening actions.
                                self.shop_configured = True
                                self.shop_scheme_selected = True
                                self.last_shop_attempt = time.monotonic()
                                self.log.info("中途接管：未经过本轮选人/天赋界面，跳过天赋与商店初始化，直接开始作战")
                            self.talent_selected = True
                            self.talent_panel_seen = False
                            self.talent_wait_logged = False
                            self.talent_click_pending_at = 0.0
                        shop_retry = float(self.config["timing"].get("shop_retry", 2.0))
                        if not self.shop_configured and not getattr(self, "shop_setup_deferred", False):
                            if time.monotonic() - self.last_shop_attempt >= shop_retry:
                                self._configure_shop()
                            time.sleep(float(self.config["timing"].get("scan_interval", 0.5)))
                            continue
                        if getattr(self, "shop_setup_deferred", False):
                            self.state = "游戏中 · 出装未确认"
                        self._fight(image)
                elif hero_select_visible:
                    self.room_wait_started = 0.0
                    self.room_last_seen_at = 0.0
                    self.room_match_armed = True
                    self.state = "选人"
                    confirmed = self._state_confirmed("hero_select")
                    if confirmed and not self.hero_session_active:
                        self.hero_session_active = True
                        self.opening_sequence_seen = True
                        self.hero_selected = False
                        self.hero_select_seen_at = 0.0
                        self.hero_wait_logged = False
                        self.current_battle_stats = None
                        self.round_stats_saved = False
                        self.round_completion_recorded = False
                        self._reset_shop_progress()
                        self.card_open_pending_at = 0.0
                        self.card_open_block_until = 0.0
                        self.last_group_follow = 0.0
                        self.isolated_since = 0.0
                        self.isolated_samples = 0
                        self.last_stats_sample = 0.0
                        self.talent_selected = False
                        self.talent_panel_seen = False
                        self.talent_wait_logged = False
                        self.talent_click_pending_at = 0.0
                        self.shop_configured = False
                        self.shop_scheme_selected = False
                        self.last_shop_attempt = 0.0
                        self.shop_confirmations = 0
                        self.card_panel_handled = False
                        self.card_flips_done = False
                        self.card_panel_handled_at = 0.0
                        self.last_card_open_attempt = 0.0
                        self.last_card_stack_y = 0
                        self.last_inventory_use = 0.0
                        self.score_cap_reached = False
                        self.game_started_at = 0.0
                        self.last_buyback_attempt = 0.0
                        self.buyback_attempted = False
                        self.buyback_visible_since = 0.0
                        self.buyback_last_point = None
                        self.buyback_click_count = 0
                        self.last_skill_use = {}
                        self.next_skill_slot = 0
                        self.last_chase_click = 0.0
                        self.last_target_attack = 0.0
                        self.hero_select_seen_at = time.monotonic()
                        self.hero_wait_logged = True
                        category = self.config.get("entry", {}).get("hero_category", "survival")
                        labels = {"survival": "肉盾", "physical": "物理", "magic": "法师"}
                        self.log.info("识别：进入选人界面，等待 15 秒后从%s栏随机选择", labels.get(category, "肉盾"))
                    wait_seconds = float(self.config["timing"].get("hero_select_delay", 15.0))
                    elapsed = time.monotonic() - self.hero_select_seen_at if self.hero_select_seen_at > 0.0 else 0.0
                    if confirmed and not self.hero_selected and elapsed < wait_seconds:
                        self.state = "选人等待 %d 秒" % max(1, int(wait_seconds - elapsed + 0.999))
                    if confirmed and not self.hero_selected and elapsed >= wait_seconds and time.monotonic() - self.last_action > 1.0:
                        hit = self.detector.last_matches.get("hero_select")
                        if hit is not None:
                            self._choose_hero(hit[0])
                elif matching_detected:
                    self.room_wait_started = 0.0
                    self.room_last_seen_at = 0.0
                    if self._state_confirmed("matching"):
                        self.hero_session_active = False
                    self.state = "匹配中（取消按钮已确认）"
                elif not self.room_match_armed:
                    self.room_wait_started = 0.0
                    self.room_last_seen_at = 0.0
                    if room_candidate is not None and room_score >= town_relaxed_threshold:
                        self.state = "匹配中（取消按钮已出现）"
                    else:
                        self.state = "匹配中（等待选人）"
                elif room_detected:
                    self.state = "房间等待"
                    confirmed = self._state_confirmed("room")
                    if confirmed:
                        self.hero_session_active = False
                        self.room_last_seen_at = time.monotonic()
                    if confirmed and self.room_match_armed and self.room_wait_started <= 0.0:
                        self.room_wait_started = time.monotonic()
                        self.log.info("识别：房间处于未匹配状态，等待约 10 秒后点击匹配")
                    wait_seconds = float(self.config["timing"].get("room_retry", 10.0))
                    elapsed = time.monotonic() - self.room_wait_started if self.room_wait_started > 0.0 else 0.0
                    if confirmed and self.room_match_armed and elapsed < wait_seconds:
                        self.state = "房间等待匹配 %d 秒" % max(1, int(wait_seconds - elapsed + 0.999))
                    if confirmed and self.room_match_armed and elapsed >= wait_seconds:
                        hit = self.detector.last_matches.get("room")
                        if hit is not None and self._safe_click_point(hit[0], "识别到的匹配按钮"):
                            self.room_match_armed = False
                            self.room_wait_started = 0.0
                            self.log.info("安全锁：本轮房间已点击一次匹配，进入其他状态前不再重复点击")
                elif self.detector.matches("expedition_dialog", image):
                    self.room_wait_started = 0.0
                    self.room_last_seen_at = 0.0
                    self.state = "选择匹配模式"
                    if self._state_confirmed("expedition_dialog") and self.expedition_mode_armed:
                        mode = self.config.get("entry", {}).get("match_mode", "casual")
                        coordinate = "expedition_power" if mode == "power" else "expedition_casual"
                        label = "战力匹配" if mode == "power" else "休闲匹配"
                        if self._safe_click(coordinate):
                            self.expedition_mode_armed = False
                            self.log.info("城镇入口：已选择%s，本轮不再重复点击", label)
                elif self.detector.matches("guild_expedition", image):
                    self.room_wait_started = 0.0
                    self.room_last_seen_at = 0.0
                    self.state = "军团主界面"
                    if self._state_confirmed("guild_expedition") and self.guild_expedition_armed:
                        hit = self.detector.last_matches.get("guild_expedition")
                        if hit is not None and self._safe_click_point(hit[0], "军团出征"):
                            self.guild_expedition_armed = False
                            self.log.info("城镇入口：已打开军团出征，本轮不再重复点击")
                elif town_detected:
                    if self.game_started_at > 0.0 and not self.round_completion_recorded:
                        if not self._state_confirmed("abnormal_exit"):
                            continue
                        self.active.clear()
                        self.window.release_cursor()
                        self.state = "异常离场：请人工确认"
                        self.log.error("未收到正常结算却回到城镇：暂停挂机并释放鼠标；请检查被踢/断线提示，人工处理后再按F8。不自动重连或继续排队")
                        continue
                    self.room_wait_started = 0.0
                    self.room_last_seen_at = 0.0
                    self.state = "城镇地图"
                    if self._state_confirmed("town_social") and self.town_social_armed:
                        hit = self.detector.last_matches.get("town_social")
                        if hit is not None and self._safe_click_point(hit[0], "右下角社会"):
                            self.town_social_armed = False
                            self.log.info("城镇入口：已打开社会系统，本轮不再重复点击")
                else:
                    self._state_confirmed("unknown")
                    grace = max(2.0, float(self.config["timing"].get("room_detection_grace", 20.0)))
                    now = time.monotonic()
                    if (
                        self.room_match_armed
                        and self.room_wait_started > 0.0
                        and self.room_last_seen_at > 0.0
                        and now - self.room_last_seen_at <= grace
                    ):
                        wait_seconds = float(self.config["timing"].get("room_retry", 10.0))
                        elapsed = now - self.room_wait_started
                        if elapsed < wait_seconds:
                            remaining = max(1, int(wait_seconds - elapsed + 0.999))
                            self.state = "房间识别波动 · 倒计时保持 %d 秒" % remaining
                        else:
                            self.state = "房间识别波动 · 等待重新确认"
                    else:
                        if self.room_wait_started > 0.0:
                            self.log.warning("房间画面连续 %.1f 秒未重新确认，取消本次等待计时", grace)
                        self.room_wait_started = 0.0
                        self.room_last_seen_at = 0.0
                        self.state = "未知画面（不操作）"
                    diagnostic_interval = max(2.0, float(self.config["timing"].get("unknown_diagnostic_interval", 5.0)))
                    if now - self.last_unknown_diagnostic >= diagnostic_interval:
                        hero_score = hero_candidate[1] if hero_candidate is not None else 0.0
                        self.log.warning(
                            "未知画面诊断：房间按钮 %.3f/%.3f，选人界面 %.3f/%.3f，选人过渡 %.3f；城镇入口 %.3f/%.3f，房间列表暖色 %.3f/%.3f",
                            room_score,
                            room_threshold,
                            hero_score,
                            hero_threshold,
                            hero_guard_threshold,
                            town_score,
                            town_relaxed_threshold,
                            town_structure_score,
                            town_structure_required,
                        )
                        self.last_unknown_diagnostic = now
            except Exception:
                self.log.error("状态循环异常：\n%s", traceback.format_exc())
                self.active.clear()
                self.state = "异常暂停"
            time.sleep(float(self.config["timing"]["scan_interval"]))

    _once_keys: set[str] = set()

    def log_once(self, key: str, message: str) -> None:
        if key not in self._once_keys:
            self.log.warning(message)
            self._once_keys.add(key)


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.base = app_dir()
        self.log_queue: Queue[str] = Queue()
        self.log = logging.getLogger("mengsanguo-bot")
        self.log.setLevel(logging.INFO)
        self.log.handlers.clear()
        handler = QueueHandler(self.log_queue)
        handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s  %(message)s", "%H:%M:%S"))
        self.log.addHandler(handler)
        self.config = self.load_config()
        self.engine = BotEngine(self.base, self.config, self.log)
        self.recorder = CombatRecorder(self.base, self.config, self.log)
        self.recorder.state_provider = lambda: {
            "state": self.engine.state, "running": self.engine.active.is_set(),
            "self_selected": self.engine.self_selected,
            "target_reason": self.engine.last_target_reason,
            "nearby_friendly_count": self.engine.last_target_friendly_count,
            "completed_rounds": self.engine.completed_rounds,
            "shop_configured": self.engine.shop_configured,
            "shop_confirmations": self.engine.shop_confirmations,
            "shop_attempts": getattr(self.engine, "shop_setup_attempts", 0),
            "shop_setup_deferred": getattr(self.engine, "shop_setup_deferred", False),
        }
        self.capture_stopping = False
        self.last_capture_hotkey = 0.0
        self.hotkey_actions: Queue[Any] = Queue()
        self.analysis_in_progress = False
        self.update_in_progress = False
        self._build_ui()
        self.engine.start_thread()
        self.hotkey_thread = threading.Thread(target=self._hotkey_loop, daemon=True, name="Hotkeys")
        self.hotkey_thread.start()
        self.capture_hotkey_thread = threading.Thread(target=self._capture_hotkey_loop, daemon=True, name="CaptureHotkey")
        self.capture_hotkey_thread.start()
        self.root.after(100, self._poll)
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self._startup_report()

    def load_config(self) -> dict[str, Any]:
        path = self.base / "config.json"
        try:
            with path.open("r", encoding="utf-8") as handle:
                config = json.load(handle)
            defaults_path = self.base / "config.example.json"
            if defaults_path.is_file():
                defaults = json.loads(defaults_path.read_text(encoding="utf-8"))
                def add_missing(current, default):
                    for key, value in default.items():
                        if key not in current:
                            current[key] = value
                        elif isinstance(current[key], dict) and isinstance(value, dict):
                            add_missing(current[key], value)
                add_missing(config, defaults)
                # 4.8 public-source assets were privacy-cropped. Older personal
                # configs may still contain the original full-frame crop box.
                for key, spec in config.get("detectors", {}).items():
                    crop = spec.get("template_crop")
                    default_spec = defaults.get("detectors", {}).get(key, {})
                    if crop and not default_spec.get("template_crop") and spec.get("file") == default_spec.get("file"):
                        try:
                            with Image.open(self.base / config.get("template_dir", "templates") / spec["file"]) as template:
                                if template.size == (crop[2] - crop[0], crop[3] - crop[1]):
                                    spec.pop("template_crop")
                        except (OSError, KeyError, IndexError, TypeError):
                            pass
            return config
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"无法读取配置文件：\n{path}\n\n{exc}")
            raise

    def _build_ui(self) -> None:
        self.root.title(APP_NAME)
        width, height = 1240, 880
        self.root.geometry(f"{width}x{height}")
        self.root.resizable(False, False)
        self.root.configure(bg="#050912")

        families = set(tkfont.families(self.root))
        display_font = next(
            (name for name in ("汉仪菱心体简", "优设标题黑", "站酷酷黑", "Microsoft YaHei UI", "微软雅黑") if name in families),
            "Microsoft YaHei UI",
        )
        mono_font = "Cascadia Mono" if "Cascadia Mono" in families else "Consolas"

        background_path = self.base / "assets" / "ui_background.png"
        try:
            source = Image.open(background_path).convert("RGB")
            scale = max(width / source.width, height / source.height)
            resized = source.resize((int(source.width * scale), int(source.height * scale)), Image.Resampling.LANCZOS)
            left = max(0, (resized.width - width) // 2)
            upper = max(0, (resized.height - height) // 2)
            background = resized.crop((left, upper, left + width, upper + height))
            background = ImageEnhance.Brightness(background).enhance(0.42)
            background = background.filter(ImageFilter.GaussianBlur(0.45))
        except Exception:
            background = Image.new("RGB", (width, height), "#07101d")
        self.background_photo = ImageTk.PhotoImage(background)
        self.icon_photo = ImageTk.PhotoImage(background.resize((64, 64), Image.Resampling.LANCZOS))
        self.root.iconphoto(True, self.icon_photo)

        canvas = tk.Canvas(self.root, width=width, height=height, bd=0, highlightthickness=0)
        canvas.pack(fill="both", expand=True)
        canvas.create_image(0, 0, image=self.background_photo, anchor="nw")
        canvas.create_rectangle(0, 0, width, 104, fill="#07101d", outline="#2b4b67", width=1, stipple="gray75")
        canvas.create_text(25, 25, text=APP_NAME, anchor="nw", fill="#e8c46b", font=(display_font, 25, "bold"))
        canvas.create_text(28, 68, text=f"DOMINION AUTOMATION CORE  //  VERSION {APP_VERSION}", anchor="nw", fill="#59d9ff", font=(mono_font, 10, "bold"))
        tk.Button(canvas, text="检查更新 / 一键更新", command=self._check_update, bg="#14647c", fg="#eaffff", bd=0, cursor="hand2", font=(display_font, 10, "bold")).place(x=610, y=58, width=170, height=29)
        canvas.create_text(
            1210,
            31,
            text="本软件永久免费，为爱发电，请勿用于商业用途",
            anchor="ne",
            fill="#f1d58a",
            font=(display_font, 13, "bold"),
        )
        canvas.create_text(1210, 66, text="请设置游戏界面风格为经典画面", anchor="ne", fill="#62ddff", font=(display_font, 12, "bold"))
        canvas.create_line(25, 94, 1215, 94, fill="#b78a37", width=2)

        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(
            "Cyber.TCombobox",
            foreground="#f3e7c2",
            fieldbackground="#111c2b",
            background="#1b3046",
            bordercolor="#466a86",
            lightcolor="#466a86",
            darkcolor="#466a86",
            arrowcolor="#6ee6ff",
            padding=6,
            font=(display_font, 10, "bold"),
        )
        style.map("Cyber.TCombobox", fieldbackground=[("readonly", "#111c2b")], foreground=[("readonly", "#f3e7c2")])
        self.root.option_add("*TCombobox*Listbox.background", "#101b2a")
        self.root.option_add("*TCombobox*Listbox.foreground", "#f3e7c2")
        self.root.option_add("*TCombobox*Listbox.selectBackground", "#245d78")

        settings = tk.Frame(canvas, bg="#09121f", highlightbackground="#38566e", highlightthickness=1)
        canvas.create_window(24, 119, width=610, height=420, anchor="nw", window=settings)
        tk.Label(settings, text="◈  作战配置矩阵", bg="#09121f", fg="#6ee6ff", font=(display_font, 13, "bold")).place(x=18, y=12)
        tk.Label(settings, text="切换后自动保存", bg="#09121f", fg="#71869a", font=(display_font, 9), anchor="e").place(x=390, y=17, width=204, height=24)

        label_style = {"bg": "#09121f", "fg": "#a9bfd0", "font": (display_font, 10, "bold"), "anchor": "w"}

        def add_field(label: str, x: int, y: int, variable: tk.StringVar, values: tuple[str, ...], command: Any, box_width: int = 16) -> None:
            tk.Label(settings, text=label, **label_style).place(x=x, y=y, width=176, height=24)
            box = ttk.Combobox(settings, textvariable=variable, values=values, state="readonly", width=box_width, style="Cyber.TCombobox", font=(display_font, 9))
            box.place(x=x, y=y + 24, width=176, height=31)
            box.bind("<<ComboboxSelected>>", command)

        self.status_var = tk.StringVar(value="初始化")
        configured_mode = self.config.get("entry", {}).get("match_mode", "casual")
        self.match_mode_var = tk.StringVar(value="战力匹配" if configured_mode == "power" else "休闲匹配")
        category = self.config.get("entry", {}).get("hero_category", "survival")
        category_labels = {"survival": "肉盾", "physical": "物理", "magic": "法师"}
        self.hero_category_var = tk.StringVar(value=category_labels.get(category, "肉盾"))
        flip_count = min(4, max(0, int(self.config.get("entry", {}).get("card_flip_count", 0))))
        self.card_flip_count_var = tk.StringVar(value="全局不翻卡" if flip_count == 0 else str(flip_count))
        configured_shop_key = str(self.config.get("combat", {}).get("shop_key", "f4")).lower()
        self.shop_key_var = tk.StringVar(value="F11" if configured_shop_key == "f11" else "F4")
        configured_layout = self.config.get("entry", {}).get("skill_layout")
        if configured_layout not in {"werd", "qwer"}:
            configured_layout = "werd" if self.config.get("combat", {}).get("skill_keys") == ["w", "e", "r", "d"] else "qwer"
        skill_label = "默认 W/E/R/D" if configured_layout == "werd" else "自定义 Q/W/E/R"
        self.skill_layout_var = tk.StringVar(value=skill_label)
        buyback_labels = {
            "always": "全局自动买活",
            "after_5m": "开局5分钟后自动买活",
            "never": "永久不买活",
        }
        configured_buyback = self.config.get("entry", {}).get("buyback_policy", "never")
        self.buyback_policy_var = tk.StringVar(value=buyback_labels.get(configured_buyback, "永久不买活"))
        try:
            configured_loop_limit = max(0, int(self.config.get("entry", {}).get("loop_limit", 0)))
        except (TypeError, ValueError):
            configured_loop_limit = 0
        self.loop_limit_var = tk.StringVar(value="一直循环" if configured_loop_limit == 0 else str(configured_loop_limit))
        completion_action = self.config.get("entry", {}).get("completion_action", "stop")
        self.completion_action_var = tk.StringVar(value="关闭电脑" if completion_action == "shutdown" else "停止脚本")
        defense_lane_labels = {"off": "关闭（全图）", "top": "上路防守", "middle": "中路防守", "bottom": "下路防守"}
        configured_defense_lane = self.config.get("entry", {}).get("defense_lane", "off")
        self.defense_lane_var = tk.StringVar(value=defense_lane_labels.get(configured_defense_lane, "关闭（全图）"))
        talent_choice = min(5, max(1, int(self.config.get("entry", {}).get("talent_choice", 5))))
        self.talent_choice_var = tk.StringVar(value=f"第 {talent_choice} 个")
        try:
            configured_scheme = min(2, max(0, int(self.config.get("entry", {}).get("shop_scheme", 0))))
        except (TypeError, ValueError):
            configured_scheme = 0
        scheme_labels = {0: "保持当前方案", 1: "推荐方案一", 2: "推荐方案二"}
        self.shop_scheme_var = tk.StringVar(value=scheme_labels[configured_scheme])
        quality_labels = {"720p": "720P（省空间）", "1080p": "1080P（高清）", "original": "原始分辨率"}
        configured_quality = str(self.config.get("capture", {}).get("video_quality", "720p")).lower()
        self.video_quality_var = tk.StringVar(value=quality_labels.get(configured_quality, quality_labels["720p"]))
        configured_volume = min(100, max(0, int(self.config.get("capture", {}).get("voice_volume", 100))))
        volume_choices = (0, 30, 60, 80, 100)
        nearest_volume = min(volume_choices, key=lambda value: abs(value - configured_volume))
        self.voice_volume_var = tk.StringVar(value="静音" if nearest_volume == 0 else f"{nearest_volume}%")

        add_field("军团出征", 18, 50, self.match_mode_var, ("休闲匹配", "战力匹配"), self._change_match_mode)
        add_field("英雄 / 学习职业", 218, 50, self.hero_category_var, ("肉盾", "物理", "法师"), self._change_hero_category)
        add_field("翻卡数量", 418, 50, self.card_flip_count_var, ("全局不翻卡", "1", "2", "3", "4"), self._change_card_flip_count, 13)
        add_field("商店快捷键", 18, 120, self.shop_key_var, ("F4", "F11"), self._change_shop_key)
        add_field("技能键位方案", 218, 120, self.skill_layout_var, ("默认 W/E/R/D", "自定义 Q/W/E/R"), self._change_skill_layout)
        add_field("死亡买活策略", 418, 120, self.buyback_policy_var, ("全局自动买活", "开局5分钟后自动买活", "永久不买活"), self._change_buyback_policy, 18)
        add_field("循环局数", 18, 206, self.loop_limit_var, ("一直循环", "1", "2", "3", "5", "10", "20", "50"), self._change_loop_limit)
        add_field("完成后动作", 218, 206, self.completion_action_var, ("停止脚本", "关闭电脑"), self._change_completion_action)
        add_field("单路防守", 418, 206, self.defense_lane_var, ("关闭（全图）", "上路防守", "中路防守", "下路防守"), self._change_defense_lane, 13)
        add_field("开局天赋", 18, 284, self.talent_choice_var, ("第 1 个", "第 2 个", "第 3 个", "第 4 个", "第 5 个"), self._change_talent_choice)
        add_field("装备方案", 218, 284, self.shop_scheme_var, ("保持当前方案", "推荐方案一", "推荐方案二"), self._change_shop_scheme, 15)
        add_field("录像画质", 418, 284, self.video_quality_var, ("720P（省空间）", "1080P（高清）", "原始分辨率"), self._change_video_quality, 15)
        self.stop_cards_at_cap_var = tk.BooleanVar(value=self.config.get("entry", {}).get("stop_cards_at_score_cap", True))
        tk.Checkbutton(settings, text="战绩满 1500 后停止翻卡", variable=self.stop_cards_at_cap_var,
                       command=self._change_stop_cards_at_cap, bg="#09121f", fg="#6ee6ff",
                       selectcolor="#111c2b", activebackground="#09121f", activeforeground="#6ee6ff",
                       font=(display_font, 9)).place(x=18, y=346, width=255, height=25)
        tk.Label(settings, text="第 5 天赋按 Z 召唤", bg="#09121f", fg="#71869a", font=(display_font, 9, "bold")).place(x=318, y=350, width=270)
        tk.Label(settings, text="共享攻击/移动节奏，职业技能独立", bg="#09121f", fg="#71869a", font=(display_font, 8), anchor="w", wraplength=385).place(x=18, y=378, width=385, height=32)
        tk.Button(settings, text="手动重试出装", command=self._retry_shop, bg="#26384b", fg="#d6e7f2", font=(display_font, 9, "bold"), bd=0).place(x=420, y=375, width=174, height=32)

        controls = tk.Frame(canvas, bg="#09121f", highlightbackground="#795f2f", highlightthickness=1)
        canvas.create_window(650, 119, width=306, height=420, anchor="nw", window=controls)
        tk.Label(controls, text="◉  CORE STATUS", bg="#09121f", fg="#e8c46b", font=(mono_font, 12, "bold")).place(x=18, y=14)
        self.status_dot = tk.Label(controls, text="●", bg="#09121f", fg="#f0aa3c", font=(display_font, 16, "bold"))
        self.status_dot.place(x=18, y=48)
        self.status_value = tk.Label(controls, textvariable=self.status_var, bg="#09121f", fg="#f3e7c2", font=(display_font, 15, "bold"), anchor="w")
        self.status_value.configure(font=(display_font, 13, "bold"), wraplength=230)
        self.status_value.place(x=48, y=43, width=230, height=43)

        button_base = {"font": (display_font, 10, "bold"), "bd": 0, "relief": "flat", "cursor": "hand2", "activeforeground": "white"}
        tk.Button(controls, text="▶  F8  启动 / 暂停", command=self._toggle_engine, bg="#14647c", fg="#eaffff", activebackground="#1b829e", **button_base).place(x=18, y=88, width=270, height=38)
        tk.Button(controls, text="■  F9  紧急停止", command=self._emergency_stop, bg="#7a2831", fg="#fff0f0", activebackground="#a43743", **button_base).place(x=18, y=134, width=132, height=36)
        tk.Button(controls, text="↻  重置停止", command=self.engine.reset_stop, bg="#26384b", fg="#d6e7f2", activebackground="#39536e", **button_base).place(x=156, y=134, width=132, height=36)
        self.cycle_status_var = tk.StringVar(value="本次运行：0 / ∞ 局")
        tk.Label(controls, textvariable=self.cycle_status_var, bg="#0d1928", fg="#6ee6ff", font=(mono_font, 11, "bold"), anchor="w", padx=10, highlightbackground="#233c52", highlightthickness=1).place(x=18, y=184, width=270, height=40)
        tk.Label(controls, text="录制模式", bg="#09121f", fg="#768b9c", font=(display_font, 9), anchor="w").place(x=18, y=234, width=110, height=26)
        self.recording_mode_var = tk.StringVar(value="人工采集" if self.config.get("capture", {}).get("recording_mode", "autoplay") == "manual" else "挂机录像")
        self.recording_mode_box = ttk.Combobox(controls, textvariable=self.recording_mode_var, values=("挂机录像", "人工采集"), state="readonly", style="Cyber.TCombobox", font=(display_font, 9))
        self.recording_mode_box.place(x=148, y=232, width=140, height=31)
        self.recording_mode_box.bind("<<ComboboxSelected>>", self._change_recording_mode)
        tk.Label(controls, text="采集语音音量", bg="#09121f", fg="#987f55", font=(display_font, 9, "bold"), anchor="w").place(x=18, y=273, width=150, height=24)
        volume_box = ttk.Combobox(controls, textvariable=self.voice_volume_var, values=("静音", "30%", "60%", "80%", "100%"), state="readonly", width=8, style="Cyber.TCombobox")
        volume_box.place(x=188, y=270, width=100, height=31)
        volume_box.bind("<<ComboboxSelected>>", self._change_voice_volume)
        self.capture_button = tk.Button(controls, text="●  F12 开始实战采集", command=self._toggle_capture, bg="#58411e", fg="#fff0c8", activebackground="#80612d", **button_base)
        self.capture_button.place(x=18, y=316, width=270, height=38)
        self.capture_status_var = tk.StringVar(value=f"MP4 + 操作 · {self.video_quality_var.get()}")
        tk.Label(controls, textvariable=self.capture_status_var, bg="#09121f", fg="#8ba2b3", font=(display_font, 8), anchor="center", wraplength=260).place(x=18, y=364, width=270, height=40)

        if self.config["safety"].get("background_mode", False):
            note = "后台模式不移动真实鼠标；仅在尺寸匹配且识别到模板时向目标窗口发送消息。F9 可随时停止。"
        else:
            note = "前台安全模式  //  仅锁定 1024×768 梦三国窗口  //  F9 随时释放并停止"
        canvas.create_text(25, 553, text=note, anchor="nw", fill="#9db2c3", font=(display_font, 9, "bold"))

        log_panel = tk.Frame(canvas, bg="#050b13", highlightbackground="#38566e", highlightthickness=1)
        canvas.create_window(24, 582, width=932, height=268, anchor="nw", window=log_panel)
        tk.Label(log_panel, text="SYSTEM TELEMETRY  //  运行日志", bg="#050b13", fg="#59d9ff", font=(mono_font, 11, "bold")).pack(anchor="w", padx=14, pady=(10, 6))
        log_body = tk.Frame(log_panel, bg="#050b13")
        log_body.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        scrollbar = tk.Scrollbar(log_body, bg="#142536", troughcolor="#070d16", activebackground="#2f6077", bd=0)
        scrollbar.pack(side="right", fill="y")
        self.text = tk.Text(
            log_body,
            state="disabled",
            wrap="word",
            font=(mono_font, 9),
            bg="#070d16",
            fg="#b8cad6",
            insertbackground="#6ee6ff",
            selectbackground="#244a62",
            relief="flat",
            padx=10,
            pady=8,
            yscrollcommand=scrollbar.set,
        )
        self.text.pack(fill="both", expand=True)
        scrollbar.config(command=self.text.yview)
        self.text.tag_configure("INFO", foreground="#8fd9ef")
        self.text.tag_configure("WARNING", foreground="#f0c36a")
        self.text.tag_configure("ERROR", foreground="#ff7782")

        stats_panel = tk.Frame(canvas, bg="#09121f", highlightbackground="#795f2f", highlightthickness=1)
        canvas.create_window(980, 119, width=236, height=731, anchor="nw", window=stats_panel)
        tk.Label(stats_panel, text="◈ 最近战斗结果", bg="#09121f", fg="#e8c46b", font=(display_font, 11, "bold")).place(x=16, y=14, width=150)
        tk.Button(
            stats_panel,
            text="清空",
            command=self._clear_battle_history,
            bg="#26384b",
            fg="#d6e7f2",
            activebackground="#39536e",
            activeforeground="white",
            font=(display_font, 8, "bold"),
            bd=0,
            relief="flat",
            cursor="hand2",
        ).place(x=174, y=12, width=46, height=25)
        tk.Label(stats_panel, text="BATTLE ARCHIVE", bg="#09121f", fg="#617d91", font=(mono_font, 8, "bold")).place(x=17, y=43)

        self.latest_kills_var = tk.StringVar(value="--")
        self.latest_deaths_var = tk.StringVar(value="--")
        self.latest_assists_var = tk.StringVar(value="--")
        self.latest_score_var = tk.StringVar(value="----")
        self.latest_outcome_var = tk.StringVar(value="--")
        metric_specs = [
            ("击杀", self.latest_kills_var, 16, "#62e6ff"),
            ("死亡", self.latest_deaths_var, 86, "#ff7782"),
            ("助攻", self.latest_assists_var, 156, "#b99cff"),
        ]
        for label, variable, x, color in metric_specs:
            tk.Label(stats_panel, text=label, bg="#09121f", fg="#8399aa", font=(display_font, 9, "bold")).place(x=x, y=74, width=56)
            tk.Label(stats_panel, textvariable=variable, bg="#0d1928", fg=color, font=(mono_font, 22, "bold"), highlightbackground="#233c52", highlightthickness=1).place(x=x, y=96, width=56, height=52)
        self.score_title_var = tk.StringVar(value="最近战绩")
        tk.Label(stats_panel, textvariable=self.score_title_var, bg="#09121f", fg="#8399aa", font=(display_font, 9, "bold")).place(x=16, y=164)
        tk.Label(stats_panel, text="状态", bg="#09121f", fg="#8399aa", font=(display_font, 9, "bold")).place(x=132, y=164)
        tk.Label(stats_panel, textvariable=self.latest_score_var, bg="#0d1928", fg="#e8c46b", font=(mono_font, 23, "bold"), highlightbackground="#795f2f", highlightthickness=1).place(x=16, y=187, width=108, height=58)
        self.latest_outcome_label = tk.Label(stats_panel, textvariable=self.latest_outcome_var, bg="#0d1928", fg="#71e6a5", font=(display_font, 11, "bold"), wraplength=78, highlightbackground="#795f2f", highlightthickness=1)
        self.latest_outcome_label.place(x=132, y=187, width=80, height=58)
        self.learning_status_var = tk.StringVar(value=self.engine.learner.summary())
        tk.Label(stats_panel, textvariable=self.learning_status_var, bg="#09121f", fg="#71e6a5", font=(display_font, 8, "bold"), anchor="w", justify="left", wraplength=200).place(x=16, y=250, width=204, height=52)
        self.analyze_capture_button = tk.Button(
            stats_panel,
            text="◆  分析新增采集",
            command=self._start_capture_analysis,
            bg="#20566a",
            fg="#eaffff",
            activebackground="#2c7690",
            activeforeground="white",
            font=(display_font, 9, "bold"),
            bd=0,
            relief="flat",
            cursor="hand2",
        )
        self.analyze_capture_button.place(x=16, y=310, width=196, height=31)
        tk.Label(stats_panel, text="最近五局 · 胜负 / KDA / 战绩", bg="#09121f", fg="#59d9ff", font=(display_font, 8, "bold")).place(x=16, y=350, width=204, height=24)
        self.history_text = tk.Text(
            stats_panel,
            state="disabled",
            wrap="word",
            font=(mono_font, 9),
            bg="#070d16",
            fg="#b8cad6",
            relief="flat",
            padx=8,
            pady=8,
        )
        self.history_text.place(x=16, y=376, width=196, height=323)
        self.history_text.tag_configure("latest", foreground="#e8c46b")
        self.history_text.tag_configure("normal", foreground="#8fb0c2")
        self.last_stats_revision = -1
        self._refresh_stats_panel()
        canvas.create_text(1216, 863, text="SAFE WINDOW INPUT  •  MEMORY-FREE", anchor="se", fill="#657b8c", font=(mono_font, 8, "bold"))

    def _retry_shop(self) -> None:
        self.engine._reset_shop_progress()
        self.engine.shop_configured = False
        self.engine.shop_confirmations = 0
        self.engine.last_shop_attempt = 0.0
        self.engine.shop_scheme_selected = True
        self.log.warning("已请求手动重试自动出装：保留当前装备方案；游戏置前且脚本运行后重新确认。不绕过F9停止")

    def _change_match_mode(self, _event: Any = None) -> None:
        mode = "power" if self.match_mode_var.get() == "战力匹配" else "casual"
        self.engine.set_match_mode(mode)
        try:
            path = self.base / "config.json"
            path.write_text(json.dumps(self.config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except Exception as exc:
            self.log.warning("匹配模式已在本次运行中切换，但保存配置失败：%s", exc)

    def _change_hero_category(self, _event: Any = None) -> None:
        categories = {"肉盾": "survival", "物理": "physical", "法师": "magic"}
        category = categories.get(self.hero_category_var.get(), "survival")
        self.engine.set_hero_category(category)
        self.learning_status_var.set(self.engine.learner.summary())
        try:
            path = self.base / "config.json"
            path.write_text(json.dumps(self.config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except Exception as exc:
            self.log.warning("英雄分类已在本次运行中切换，但保存配置失败：%s", exc)

    def _change_defense_lane(self, _event: Any = None) -> None:
        lanes = {"关闭（全图）": "off", "上路防守": "top", "中路防守": "middle", "下路防守": "bottom"}
        self.engine.set_defense_lane(lanes.get(self.defense_lane_var.get(), "off"))
        try:
            path = self.base / "config.json"
            path.write_text(json.dumps(self.config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except Exception as exc:
            self.log.warning("单路防守设置已在本次运行中切换，但保存配置失败：%s", exc)

    def _change_talent_choice(self, _event: Any = None) -> None:
        label = self.talent_choice_var.get()
        try:
            choice = int(label.replace("第", "").replace("个", "").strip())
        except ValueError:
            choice = 5
        self.engine.set_talent_choice(choice)
        try:
            path = self.base / "config.json"
            path.write_text(json.dumps(self.config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except Exception as exc:
            self.log.warning("开局天赋设置已在本次运行中切换，但保存配置失败：%s", exc)

    def _change_stop_cards_at_cap(self) -> None:
        enabled = bool(self.stop_cards_at_cap_var.get())
        self.config.setdefault("entry", {})["stop_cards_at_score_cap"] = enabled
        try:
            (self.base / "config.json").write_text(json.dumps(self.config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except Exception as exc:
            self.log.warning("保存满分停止翻卡选项失败：%s", exc)
        self.log.info("战绩满分后停止翻卡：%s", "开启" if enabled else "关闭")

    def _change_card_flip_count(self, _event: Any = None) -> None:
        label = self.card_flip_count_var.get()
        count = 0 if label == "全局不翻卡" else int(label)
        self.engine.set_card_flip_count(count)
        try:
            path = self.base / "config.json"
            path.write_text(json.dumps(self.config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except Exception as exc:
            self.log.warning("翻卡数量已在本次运行中切换，但保存配置失败：%s", exc)

    def _change_loop_limit(self, _event: Any = None) -> None:
        label = self.loop_limit_var.get()
        count = 0 if label == "一直循环" else int(label)
        self.engine.set_loop_limit(count)
        try:
            path = self.base / "config.json"
            path.write_text(json.dumps(self.config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except Exception as exc:
            self.log.warning("循环局数已在本次运行中切换，但保存配置失败：%s", exc)

    def _change_completion_action(self, _event: Any = None) -> None:
        requested_shutdown = self.completion_action_var.get() == "关闭电脑"
        if requested_shutdown and not messagebox.askyesno(
            "确认关机动作",
            "达到设定局数后将停止脚本，并安排电脑在 60 秒后关机。\n\n确定启用吗？",
            parent=self.root,
        ):
            self.completion_action_var.set("停止脚本")
            requested_shutdown = False
        self.engine.set_completion_action("shutdown" if requested_shutdown else "stop")
        try:
            path = self.base / "config.json"
            path.write_text(json.dumps(self.config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except Exception as exc:
            self.log.warning("完成动作已在本次运行中切换，但保存配置失败：%s", exc)

    def _change_shop_key(self, _event: Any = None) -> None:
        key = "f11" if self.shop_key_var.get() == "F11" else "f4"
        self.engine.set_shop_key(key)
        try:
            path = self.base / "config.json"
            path.write_text(json.dumps(self.config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except Exception as exc:
            self.log.warning("商店快捷键已在本次运行中切换，但保存配置失败：%s", exc)

    def _change_shop_scheme(self, _event: Any = None) -> None:
        schemes = {"保持当前方案": 0, "推荐方案一": 1, "推荐方案二": 2}
        self.engine.set_shop_scheme(schemes.get(self.shop_scheme_var.get(), 0))
        try:
            path = self.base / "config.json"
            path.write_text(json.dumps(self.config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except Exception as exc:
            self.log.warning("装备方案已在本次运行中切换，但保存配置失败：%s", exc)

    def _change_video_quality(self, _event: Any = None) -> None:
        profiles = {
            "720P（省空间）": "720p",
            "1080P（高清）": "1080p",
            "原始分辨率": "original",
        }
        quality = profiles.get(self.video_quality_var.get(), "720p")
        self.config.setdefault("capture", {})["video_quality"] = quality
        try:
            path = self.base / "config.json"
            path.write_text(json.dumps(self.config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            self.capture_status_var.set(f"MP4 + 操作 · {self.video_quality_var.get()}")
            self.log.info("录像画质已切换：%s（下一次采集生效）", self.video_quality_var.get())
        except Exception as exc:
            self.log.warning("录像画质已在本次运行中切换，但保存配置失败：%s", exc)

    def _change_voice_volume(self, _event: Any = None) -> None:
        label = self.voice_volume_var.get()
        volume = 0 if label == "静音" else min(100, max(0, int(label.rstrip("%"))))
        capture = self.config.setdefault("capture", {})
        capture["voice_volume"] = volume
        capture["voice_announcement"] = volume > 0
        try:
            (self.base / "config.json").write_text(json.dumps(self.config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            self.log.info("采集语音音量已切换为：%s", label)
            if volume > 0:
                self._speak_async("语音音量设置成功")
        except Exception as exc:
            self.log.warning("语音音量已在本次运行中切换，但保存配置失败：%s", exc)

    def _start_capture_analysis(self) -> None:
        if self.analysis_in_progress:
            return
        if self.recorder.is_active:
            messagebox.showwarning("正在采集", "请先按 F12 结束当前实战采集，再分析已保存的数据。", parent=self.root)
            return
        self.analysis_in_progress = True
        self.engine.active.clear()
        self.engine.window.release_cursor()
        self.engine.state = "正在分析采集数据"
        self.analyze_capture_button.configure(text="分析中…", state="disabled")

        def analyze() -> None:
            try:
                result = self.engine.learner.analyze_new_captures()
                self.hotkey_actions.put(("analysis_done", result))
            except Exception as exc:
                self.log.error("采集学习异常：\n%s", traceback.format_exc())
                self.hotkey_actions.put(("analysis_error", str(exc)))

        threading.Thread(target=analyze, daemon=True, name="CaptureLearning").start()

    def _change_skill_layout(self, _event: Any = None) -> None:
        layout = "werd" if self.skill_layout_var.get() == "默认 W/E/R/D" else "qwer"
        self.engine.set_skill_layout(layout)
        try:
            path = self.base / "config.json"
            path.write_text(json.dumps(self.config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except Exception as exc:
            self.log.warning("技能快捷键已在本次运行中切换，但保存配置失败：%s", exc)

    def _change_buyback_policy(self, _event: Any = None) -> None:
        policies = {
            "全局自动买活": "always",
            "开局5分钟后自动买活": "after_5m",
            "永久不买活": "never",
        }
        policy = policies.get(self.buyback_policy_var.get(), "never")
        self.engine.set_buyback_policy(policy)
        try:
            path = self.base / "config.json"
            path.write_text(json.dumps(self.config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except Exception as exc:
            self.log.warning("买活策略已在本次运行中切换，但保存配置失败：%s", exc)

    def _toggle_capture(self) -> None:
        if getattr(self, "update_in_progress", False):
            self.log.warning("更新期间不能启动录制")
            return
        if self.capture_stopping:
            return
        if self.recorder.is_active and self.recorder.mode == "autoplay":
            self._stop_autoplay_recording()
            return
        if not self.recorder.is_active and self.recording_mode_var.get() == "挂机录像":
            if self.recorder.start("autoplay"):
                self._speak_async("挂机录像开始，脚本运行不受影响")
            else:
                self._speak_async("挂机录像启动失败")
            return
        if self.recorder.is_active:
            self.recorder.stop()
            if not self.recorder.hero_name and self.recorder.session_dir is not None:
                name = simpledialog.askstring(
                    "请手动命名英雄",
                    f"本局职业：{self.recorder.capture_profile_label}\n未能识别英雄名，请输入英雄名称（例如：曹植、周泰）。\n点击确定后完成命名和保存。",
                    parent=self.root,
                )
                if name is not None:
                    cleaned = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9_-]+", "", name.strip())[:12]
                    if cleaned:
                        self.recorder.hero_name = cleaned
                        metadata_path = self.recorder.session_dir / "metadata.json"
                        try:
                            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                            metadata["hero_name"] = cleaned
                            metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                            self.recorder._finalize_session_name()
                        except Exception as exc:
                            self.log.error("手动英雄名保存失败：%s", exc)
            self.engine.state = "已暂停"
            self._speak_async("数据采集结束，文件已保存")
            return
        self.engine.active.clear()
        self.engine.window.release_cursor()
        self.engine.state = "实战采集准备"
        if self.recorder.start():
            self.engine.state = "实战采集中"
            self._speak_async("数据采集开始")
        else:
            self.engine.state = "采集启动失败"
            self._speak_async("数据采集启动失败")

    def _speak_async(self, text: str) -> None:
        if not self.config.get("capture", {}).get("voice_announcement", True):
            return
        def speak() -> None:
            try:
                safe_text = text.replace("'", "''")
                volume = min(100, max(0, int(self.config.get("capture", {}).get("voice_volume", 100))))
                script = f"$voice=New-Object -ComObject SAPI.SpVoice;$voice.Volume={volume};$voice.Rate=0;[void]$voice.Speak('{safe_text}')"
                encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
                subprocess.Popen(
                    ["powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-EncodedCommand", encoded],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except Exception as exc:
                self.log.warning("语音播报失败：%s", exc)
        threading.Thread(target=speak, daemon=True, name="VoiceAnnouncement").start()

    def _emergency_stop(self) -> None:
        self.engine.stop_now()
        if self.recorder.mode == "autoplay":
            self._stop_autoplay_recording()
        else:
            self.recorder.stop()

    def _change_recording_mode(self, _event: Any = None) -> None:
        self.config.setdefault("capture", {})["recording_mode"] = "autoplay" if self.recording_mode_var.get() == "挂机录像" else "manual"
        (self.base / "config.json").write_text(json.dumps(self.config, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")

    def _stop_autoplay_recording(self) -> None:
        if self.capture_stopping:
            return
        self.capture_stopping = True
        def finish() -> None:
            self.recorder.stop()
            self.hotkey_actions.put("autoplay_saved")
        threading.Thread(target=finish, daemon=True, name="SaveAutoplayRecording").start()

    def _refresh_stats_panel(self) -> None:
        self.learning_status_var.set(self.engine.learner.summary())
        history = list(self.engine.battle_history)
        live = self.engine.current_battle_stats if not self.engine.round_stats_saved else None
        if live:
            self.latest_kills_var.set(str(live.get("kills", "--")))
            self.latest_deaths_var.set(str(live.get("deaths", "--")))
            self.latest_assists_var.set(str(live.get("assists", "--")))
            self.latest_score_var.set(str(live.get("score", "----")))
            self.latest_outcome_var.set("战斗")
            self.score_title_var.set("实时战绩")
            self.latest_outcome_label.configure(fg="#62e6ff")
        elif history:
            latest = history[0]
            self.latest_kills_var.set(str(latest.get("kills", "--")))
            self.latest_deaths_var.set(str(latest.get("deaths", "--")))
            self.latest_assists_var.set(str(latest.get("assists", "--")))
            self.latest_score_var.set(str(latest.get("score", "----")))
            self.latest_outcome_var.set(str(latest.get("outcome", "--")))
            self.score_title_var.set("最近战绩")
            outcome = str(latest.get("outcome", "--"))
            self.latest_outcome_label.configure(fg="#71e6a5" if outcome == "胜" else "#ff7782" if outcome == "负" else "#e8c46b")
        else:
            self.latest_kills_var.set("--")
            self.latest_deaths_var.set("--")
            self.latest_assists_var.set("--")
            self.latest_score_var.set("----")
            self.latest_outcome_var.set("--")
            self.score_title_var.set("最近战绩")
            self.latest_outcome_label.configure(fg="#8399aa")
        display_limit = max(1, int(self.config.get("statistics", {}).get("display_limit", 5)))
        self.history_text.configure(state="normal")
        self.history_text.delete("1.0", "end")
        if not history:
            self.history_text.insert("end", "等待首场战斗结算…\n", "normal")
        else:
            for index, record in enumerate(history[:display_limit], 1):
                line = (
                    f"{index:02d}  [{record.get('outcome', '--')}]  {record.get('time', '--')}\n"
                    f"    {record.get('kills', 0)} / {record.get('deaths', 0)} / {record.get('assists', 0)}"
                    f"   战绩 {record.get('score', 0)}\n\n"
                )
                self.history_text.insert("end", line, "latest" if index == 1 else "normal")
        self.history_text.configure(state="disabled")
        self.last_stats_revision = self.engine.stats_revision

    def _clear_battle_history(self) -> None:
        if not messagebox.askyesno("清空战斗记录", "确定清空所有已保存的历史战斗记录吗？\n\n当前进行中的实时战绩仍会继续显示。", parent=self.root):
            return
        self.engine.clear_battle_history()
        self._refresh_stats_panel()

    def _startup_report(self) -> None:
        self.log.info("程序目录：%s", self.base)
        self.log.info("热键：F8 启动/暂停，F9 紧急停止，F12 开始/结束实战采集")
        self.log.info("实战采集：支持 1024×768/1920×1080/2560×1440，可选720P、1080P或原始分辨率录像；采集期间自动化暂停")
        ocr_path = find_tesseract()
        if ocr_path is not None:
            self.log.info("英雄名OCR：已启用（%s）", ocr_path)
        else:
            self.log.warning("英雄名OCR：未找到中文 Tesseract；采集仍按职业保存，英雄名前缀将显示待识别")
        self.log.info("目标客户区：%sx%s", *self.config["window"]["client_size"])
        mode = self.config.get("entry", {}).get("match_mode", "casual")
        self.log.info("军团出征模式：%s", "战力匹配" if mode == "power" else "休闲匹配")
        category = self.config.get("entry", {}).get("hero_category", "survival")
        labels = {"survival": "肉盾", "physical": "物理", "magic": "法师"}
        self.log.info("英雄分类 / 本地学习职业：%s", labels.get(category, "肉盾"))
        self.log.info("%s；所有学习数据仅保存在本机 local_learning.json", self.engine.learner.summary())
        defense_lane = self.config.get("entry", {}).get("defense_lane", "off")
        defense_labels = {"off": "关闭（全图作战）", "top": "上路", "middle": "中路", "bottom": "下路"}
        self.log.info("单路防守：%s", defense_labels.get(defense_lane, "关闭（全图作战）"))
        talent_choice = min(5, max(1, int(self.config.get("entry", {}).get("talent_choice", 5))))
        summon_key = str(self.config.get("combat", {}).get("summon_key", "z")).upper()
        self.log.info("开局天赋：第 %s 个；%s 键背包援兵：%s", talent_choice, summon_key, "启用" if talent_choice == 5 else "禁用")
        flip_count = int(self.config.get("entry", {}).get("card_flip_count", 0))
        self.log.info("翻卡设置：%s", "全局不翻卡" if flip_count == 0 else f"每次 {flip_count} 张")
        loop_limit = int(self.config.get("entry", {}).get("loop_limit", 0))
        completion_action = self.config.get("entry", {}).get("completion_action", "stop")
        self.log.info(
            "循环设置：%s；达到局数后%s",
            "一直循环" if loop_limit == 0 else f"{loop_limit} 局",
            "关闭电脑" if completion_action == "shutdown" else "停止脚本",
        )
        self.log.info("商店快捷键：%s", str(self.config.get("combat", {}).get("shop_key", "f4")).upper())
        scheme = min(2, max(0, int(self.config.get("entry", {}).get("shop_scheme", 0))))
        scheme_labels = {0: "保持当前方案", 1: "推荐方案一", 2: "推荐方案二"}
        self.log.info("装备方案：%s（仅正常开局选择）", scheme_labels[scheme])
        skill_keys = self.config.get("combat", {}).get("skill_keys", ["q", "w", "e", "r"])
        self.log.info("技能快捷键：%s", "/".join(str(key).upper() for key in skill_keys))
        buyback = self.config.get("entry", {}).get("buyback_policy", "never")
        buyback_labels = {"always": "全局自动买活", "after_5m": "开局 5 分钟后自动买活", "never": "永久不买活"}
        self.log.info("买活策略：%s", buyback_labels.get(buyback, "永久不买活"))
        if self.config.get("statistics", {}).get("enabled", True):
            self.log.info("战绩统计：已启用，结算时保存最近 %s 场", self.config.get("statistics", {}).get("history_limit", 10))
        if self.config["safety"].get("background_mode", False):
            self.log.info("后台模式：已启用窗口消息输入和 PrintWindow 抓图，不会移动真实鼠标")
        else:
            self.log.info("前台模式：游戏必须位于前台；适合在虚拟机内独立运行")
        if not self.engine.detector.templates:
            self.log.warning("当前没有可用模板。请按使用说明放入 1024×768 截图裁剪模板；在此之前程序不会点击。")

    def _hotkey_loop(self) -> None:
        registered8 = bool(user32.RegisterHotKey(None, HOTKEY_F8, 0, VK_F8))
        registered9 = bool(user32.RegisterHotKey(None, HOTKEY_F9, 0, VK_F9))
        if not registered8 or not registered9:
            self.log.error("F8/F9 全局热键注册失败；可改用窗口内按钮")
        msg = ctypes.wintypes.MSG()
        while not self.engine.shutdown.is_set():
            result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if result <= 0:
                break
            if msg.message == WM_HOTKEY:
                if msg.wParam == HOTKEY_F8:
                    if self.recorder.is_active and self.recorder.mode != "autoplay":
                        self.log.warning("实战采集中：已忽略 F8，避免启动自动化干扰人工操作")
                    else:
                        self._toggle_engine()
                elif msg.wParam == HOTKEY_F9:
                    self._emergency_stop()
        if registered8:
            user32.UnregisterHotKey(None, HOTKEY_F8)
        if registered9:
            user32.UnregisterHotKey(None, HOTKEY_F9)

    def _capture_hotkey_loop(self) -> None:
        """Poll F12 so overlays cannot block capture by owning RegisterHotKey."""
        was_down = False
        self.log.info("F12 采集热键：兼容检测已启用（不占用系统热键）")
        while not self.engine.shutdown.is_set():
            is_down = bool(user32.GetAsyncKeyState(VK_F12) & 0x8000)
            if is_down and not was_down:
                now = time.monotonic()
                if now - self.last_capture_hotkey >= 0.8:
                    self.last_capture_hotkey = now
                    self.hotkey_actions.put("toggle_capture")
            was_down = is_down
            time.sleep(0.03)

    def _poll(self) -> None:
        while True:
            try:
                action = self.hotkey_actions.get_nowait()
            except Empty:
                break
            if action == "toggle_capture":
                self._toggle_capture()
            elif action == "autoplay_saved":
                self.capture_stopping = False
                self._speak_async("挂机录像结束，文件已保存")
            elif isinstance(action, tuple) and action[0] == "analysis_done":
                result = action[1]
                self.analysis_in_progress = False
                self.analyze_capture_button.configure(text="◆  分析新增采集", state="normal")
                self.learning_status_var.set(self.engine.learner.summary())
                self.engine.state = "采集分析完成"
                if int(result.get("added", 0)) > 0:
                    counts = result.get("added_by_profile", {})
                    totals = result.get("totals", {})
                    added_text = "、".join(
                        f"{PROFILE_LABELS[key]} {int(counts.get(key, 0))}局"
                        for key in ("tank", "mage", "physical")
                        if int(counts.get(key, 0)) > 0
                    )
                    total_text = "、".join(f"{PROFILE_LABELS[key]} {int(totals.get(key, 0))}局" for key in ("tank", "mage", "physical"))
                    messagebox.showinfo(
                        "采集学习完成",
                        f"新增：{added_text}\n"
                        f"累计：{total_text}\n"
                        f"重复跳过：{result.get('skipped')} 局；无效/未结束：{result.get('invalid')} 局。",
                        parent=self.root,
                    )
                else:
                    messagebox.showinfo(
                        "没有新增采集",
                        "未发现可分析的新对局。\n\n程序会扫描当前版本及同一目录下旧版本的 combat_capture；请确认采集已经按 F12 正常结束。",
                        parent=self.root,
                    )
            elif isinstance(action, tuple) and action[0] == "analysis_error":
                self.analysis_in_progress = False
                self.analyze_capture_button.configure(text="◆  分析新增采集", state="normal")
                self.engine.state = "采集分析失败"
                messagebox.showerror("采集分析失败", str(action[1]), parent=self.root)
        recording_manual = self.recorder.is_active and self.recorder.mode != "autoplay"
        display_state = "实战采集中" if recording_manual else self.engine.state
        deferred = getattr(self.engine, "shop_setup_deferred", False)
        if deferred and not recording_manual and "出装未确认" not in display_state:
            display_state = "出装未确认 · " + display_state
        if deferred and not getattr(self, "_announced_shop_deferred", False):
            self._speak_async("自动出装未确认，已经关闭商店继续作战，请人工检查")
        self._announced_shop_deferred = deferred
        self.status_var.set(display_state)
        self.recording_mode_box.configure(state="disabled" if self.recorder.is_active or self.capture_stopping else "readonly")
        if self.recorder.is_active:
            self.capture_button.configure(text="■ F12 结束" + ("挂机录像" if self.recorder.mode == "autoplay" else "人工采集"), bg="#a83a43", activebackground="#c94d57")
            self.capture_status_var.set(f"REC  视频 {self.recorder.video_frame_count}  ·  事件 {self.recorder.event_count}")
        else:
            self.capture_button.configure(text="正在保存…" if self.capture_stopping else "● F12 开始"+self.recording_mode_var.get(), bg="#58411e", activebackground="#80612d")
            if self.recorder.last_session_dir is None:
                self.capture_status_var.set(f"MP4 + 操作 · {self.video_quality_var.get()}")
            else:
                self.capture_status_var.set(f"已保存：{self.recorder.last_session_dir.name}")
        try:
            loop_limit = max(0, int(self.config.get("entry", {}).get("loop_limit", 0)))
        except (TypeError, ValueError):
            loop_limit = 0
        self.cycle_status_var.set(f"本次运行：{self.engine.completed_rounds} / {'∞' if loop_limit == 0 else loop_limit} 局")
        state = display_state
        if "停止" in state or "异常" in state or "失败" in state:
            status_color = "#ff6470"
        elif "暂停" in state or "等待" in state or "未确认" in state:
            status_color = "#f0b84d"
        else:
            status_color = "#58e6b2"
        self.status_dot.configure(fg=status_color)
        self.status_value.configure(fg=status_color)
        if self.last_stats_revision != self.engine.stats_revision:
            self._refresh_stats_panel()
        while True:
            try:
                line = self.log_queue.get_nowait()
            except Empty:
                break
            self.text.configure(state="normal")
            tag = "ERROR" if " ERROR " in line else "WARNING" if " WARNING " in line else "INFO"
            self.text.insert("end", line + "\n", tag)
            self.text.see("end")
            self.text.configure(state="disabled")
        self.root.after(100, self._poll)

    def _toggle_engine(self) -> None:
        if self.update_in_progress:
            self.log.warning("更新期间不能启动挂机")
            return
        self.engine.toggle()

    def _check_update(self) -> None:
        from update_ui import show_update_dialog
        show_update_dialog(self, APP_VERSION)

    def _close(self) -> None:
        self.recorder.close()
        self.engine.close()
        self.root.destroy()


def main() -> None:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
