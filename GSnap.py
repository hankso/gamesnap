#!/usr/bin/env python3
# coding=utf-8
#
# File: GSnap.py
# Author: Hankso
# Webpage: https://github.com/hankso
# Time: 2026/4/1 0:16:44
#
# Versions:
#   v0.1.2: add TUI by textual
#   v0.1.1: add GUI by tkinter and packaging by nuitka
#   v0.1.0: add snapshot engine and CLI by argparse
#
# Notes for Windows only:
#   For GUI mode:       nuitka GSnap.py
#   For CUI on cmd:     set SUBSYSTEM=CUI && nuitka GSnap.py
#   For CUI on pwsh:    $env:SUBSYSTEM = "CUI"; & nuitka GSnap.py
#   Use uv dep-group:   uv run --no-group dev --group build nuitka GSnap.py
#
# nuitka-project: --onefile
# nuitka-project: --report=build/report.xml
# nuitka-project: --output-dir=build
# nuitka-project: --enable-plugin=tk-inter
# nuitka-project: --include-package=rich._unicode_data
# nuitka-project: --output-filename=GameSnap
# nuitka-project: --windows-icon-from-ico=icon.png
# nuitka-project: --include-data-files=icon.png=icon.png
# nuitka-project: --include-data-files=pyproject.toml=pyproject.toml
# nuitka-project-if: __import__('shutil').which('upx') is not None:
#    nuitka-project: --enable-plugin=upx
#    nuitka-project: --onefile-no-compression
# nuitka-project-if: {OS} == 'Windows' and os.getenv('SUBSYSTEM', 'GUI') == 'GUI':
#    nuitka-project: --windows-console-mode=attach
#    nuitka-project: --output-filename=GameSnapGUI
# nuitka-project-if: {OS} == 'Windows' and os.getenv('SUBSYSTEM', 'GUI') == 'CUI':
#    nuitka-project: --windows-console-mode=force
#    nuitka-project: --output-filename=GameSnapCUI

# built-in
import os
import re
import csv
import sys
import json
import time
import queue
import atexit
import ctypes
import shutil
import typing
import hashlib
import inspect
import logging
import tomllib
import zipfile
import argparse
import tempfile
import itertools
import threading
import subprocess
import dataclasses
import urllib.request
import tkinter as tk
from pathlib import Path as _Path
from tkinter import font, filedialog, messagebox
from datetime import datetime
from functools import cached_property
from contextlib import suppress, contextmanager
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from configparser import ConfigParser
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor

# required: psutil keyboard textual watchdog ttkbootstrap
import psutil
import keyboard
import textual.app
import watchdog.observers
import ttkbootstrap as ttk
import textual.events as tte
import textual.widgets as ttw
import textual.containers as ttc
from PIL import Image, ImageTk, ImageGrab
from rich import print as rprint
from rich.align import Align as RichAlign
from rich.logging import RichHandler
from ttkbootstrap.widgets.scrolled import ScrolledText

# optional: backports.zstd, rich-pixels, rich-argparse, json_repair
try:
    from compression import zstd    # python 3.14+
except Exception:
    from backports import zstd
try:
    from rich_pixels import Pixels as RichPixels
except Exception:
    RichPixels = None
try:
    from rich_argparse import RichHelpFormatter
except Exception:
    RichHelpFormatter = None
try:
    import json_repair
except Exception:
    json_repair = None
try:
    import winsound
except Exception:
    winsound = None
try:
    import winreg
except Exception:
    winreg = None


class Path(_Path):
    def __str__(self) -> str:
        return super().__str__().replace(self._flavour.sep, '/')

    def mkdirp(self, mode=0o777) -> typing.Self:
        return self.mkdir(mode, parents=True, exist_ok=True) or self

    def touchp(self, mode=0o666) -> typing.Self:
        return self.parent.mkdirp().touch(mode, exist_ok=True) or self


try:
    BASEDIR = Path(__compiled__.containing_dir).resolve()   # noqa: F821 onefile parent
    DATADIR = Path(__file__).parent.resolve()               # inside onefile
except Exception:
    BASEDIR = DATADIR = Path(__file__).parent.resolve()
ICOFILE = DATADIR / 'icon.png'
CFGFILE = BASEDIR / 'config.json'
WINDOWS = sys.platform == 'win32'
POOLMAP = True
try:
    with open(DATADIR / 'pyproject.toml', 'rb') as _f:
        _data = tomllib.load(_f)['project']
    PROJECT = _data['name']
    VERSION = _data['version']
    DESCSTR = _data['description']
    AUTHORS = ', '.join(
        f'{author.get("name", "")} <{author.get("email", "")}>'
        for author in _data['authors']
    )
except Exception:
    PROJECT, VERSION, AUTHORS, DESCSTR = Path(__file__).stem, '0.0.0', 'UNKNOWN', ''
APPINFO = (PROJECT, f'v{VERSION}', f'by {AUTHORS}')

logger = logging.getLogger(PROJECT)
logger.propagate = False
logger.setLevel('DEBUG')
logger.addHandler(RichHandler(
    show_path=False, rich_tracebacks=True, tracebacks_show_locals=True,
    log_time_format=lambda dt: f'[{dt.strftime("%X.%f")[:12]}]'  # HH:MM:SS.%3f
))


def jload(path: Path) -> dict:
    if path.suffix == '.zstd':
        with suppress(Exception), zstd.open(path, 'r') as f:
            return json.load(f)
    else:
        with suppress(Exception), open(path, encoding='utf-8') as f:
            return json_repair.load(f) if json_repair else json.load(f)
    return {}


def jdump(data: dict, path: Path) -> Path:
    if path.suffix == '.zstd':
        enc = json.JSONEncoder(default=str, separators=(',', ':'))
        with zstd.open(path.touchp(), 'wb') as f:
            for chunk in enc.iterencode(data):
                f.write(chunk.encode())
    else:
        with open(path.touchp(), 'w', encoding='utf-8') as f:
            json.dump(data, f, default=str, ensure_ascii=False, indent=2)
    return path


@contextmanager
def tailf(path: Path, intv: float = 0.1, create: bool = True) -> typing.Callable[[], None]:
    '''Watch a file and print new lines like `tail -f`'''
    if not path.is_file() and create:
        path.touchp()
    event = threading.Event()

    def wrapper() -> None:
        with open(path) as f:
            f.seek(0, 2)
            while not event.wait(intv):
                if line := f.readline():
                    print(line, end='')
    thread = threading.Thread(target=wrapper, name='tail-f', daemon=True)

    def stop() -> None:
        event.set()
        thread.join(intv * 2)
    try:
        if path.is_file():
            thread.start()
        yield stop
    finally:
        stop()


def pidof(name: str) -> int:
    '''Get process id of specified program or executable (case ignored)'''
    name = str(name).lower()
    with suppress(psutil.NoSuchProcess, psutil.AccessDenied):
        for proc in psutil.process_iter(['name']):
            if name in proc.info['name'].lower():
                return proc.pid
    return 0


def where(path: Path, key: str, default: str = '') -> str:
    '''Get installed path of a software from registry'''
    if winreg is None:
        return default
    for prefix, hkey in itertools.product(
        ('Software', 'Software\\WOW6432Node'),
        (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE)
    ):
        with suppress(Exception), winreg.OpenKey(hkey, f'{prefix}\\{path}') as ctx:
            val = abspath(winreg.QueryValueEx(ctx, key)[0])
            if val.is_dir():
                return str(val)
    return default


def browse(path: str | Path) -> None:
    '''Open file or directory in explorer'''
    path = Path(str(path)).resolve()
    if path.is_file():
        os.system(f'explorer /select,"{path}"')
    elif path.is_dir():
        os.system(f'explorer /root,"{path}"')  # alt: os.startfile


def abspath(path: str | Path) -> Path:
    '''Expand environ vars and user home directory and resolve the path'''
    return Path(os.path.expandvars(path)).expanduser().resolve()


def console(show: bool, title: str = '') -> bool:
    '''
    Manage console for Windows CUI/GUI subsystem programs.

    Args:
        show (bool): whether to show the console window
        title (str, optional): set title of console window if show is True

    Returns:
        bool: success or failed

    Notes:
        This function overrides the nuitka --windows-console-mode=<MODE> behavior.

        +---------+--------------+--------------------------+
        | MODE    | SHOW = False | SHOW = True              |
        +---------+--------------+--------------------------+
        | force   | hide window  | do nothing               |
        | hide    | do nothing   | show window              |
        | disable | do nothing   | Attach + Alloc + freopen |
        | attach  | it depends*  | Alloc + freopen          |
        +---------+--------------+--------------------------+

        * hide window if parent process has a console (e.g. PowerShell / CMD)
          do nothing if program is started by double-clicking (no console window)

        Use `dumpbin.exe /headers <program>` to check exe mode:
            2 subsystem => Windows GUI
            3 subsystem => Windows CUI
    '''
    if not WINDOWS:
        return True
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    hwnd = kernel32.GetConsoleWindow()
    if not show:
        if hwnd and user32.ShowWindow(hwnd, 0):     # SW_HIDE
            atexit.register(lambda h=hwnd: user32.ShowWindow(h, 1))
        return True
    if hwnd:
        user32.ShowWindow(hwnd, 1)                  # SW_SHOW
        title and kernel32.SetConsoleTitleW(title)
        if sys.stdout and sys.stdout.isatty():
            return True
    elif not kernel32.AttachConsole(-1):            # ATTACH_PARENT_PROCESS
        if not kernel32.AllocConsole():
            return False
    try:
        for fd in [0, 1, 2]:
            with suppress(OSError):
                os.close(fd)
        sys.stdin = open('CONIN$', 'r', encoding='utf-8')
        sys.stdout = open('CONOUT$', 'w', encoding='utf-8')
        sys.stderr = open('CONOUT$', 'w', encoding='utf-8')
        kernel32.SetConsoleCP(65001)
        kernel32.SetConsoleOutputCP(65001)      # UTF-8
        title and kernel32.SetConsoleTitleW(title)
        return True
    except Exception:
        return False


@contextmanager
def poolmap(ntask: int = -1, thread: bool = True) -> typing.Callable[..., iter]:
    '''Map tasks in parallel with ThreadPoolExecutor or ProcessPoolExecutor'''
    if POOLMAP and (ntask < 0 or ntask > os.cpu_count()):
        e = ThreadPoolExecutor(thread_name_prefix='poolmap') if thread else ProcessPoolExecutor()
        m = e.map
    else:
        e, m = None, lambda *a, **k: list(map(*a))  # consume
    try:
        yield m
    finally:
        e and e.shutdown()


def capture(pid: int | str) -> Image.Image | None:
    '''Take screenshot of prorgam'''
    hwnds: [int, tuple[int, bool, tuple[int, int, int, int]]] = {}
    if WINDOWS:
        user32, gdi32, wtypes = ctypes.windll.user32, ctypes.windll.gdi32, ctypes.wintypes

        @ctypes.WINFUNCTYPE(wtypes.BOOL, wtypes.HWND, wtypes.LPARAM)
        def callback(hwnd, _) -> bool:
            pid, rect = wtypes.DWORD(), wtypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            xyxy = x1, y1, x2, y2 = (rect.left, rect.top, rect.right, rect.bottom)
            valid = user32.IsWindowVisible(hwnd)
            hwnds[hwnd] = [pid.value, valid and (x2 - x1) > 1 and (y2 - y1) > 1, xyxy]
            return True
        awareness()
        user32.EnumWindows(callback, 0)

    search = pidof(pid) if isinstance(pid, str) else pid
    for hwnd, (pid, valid, bbox) in hwnds.items():
        if pid != search or not valid:
            continue
        if not WINDOWS:
            return ImageGrab.grab(bbox, all_screens=True)

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [
                ('biSize', wtypes.DWORD),
                ('biWidth', wtypes.LONG),
                ('biHeight', wtypes.LONG),
                ('biPlanes', wtypes.WORD),
                ('biBitCount', wtypes.WORD),
                ('biCompression', wtypes.DWORD),
                ('biSizeImage', wtypes.DWORD),
                ('biXPelsPerMeter', wtypes.LONG),
                ('biYPelsPerMeter', wtypes.LONG),
                ('biClrUsed', wtypes.DWORD),
                ('biClrImportant', wtypes.DWORD),
            ]
        size = (bbox[2] - bbox[0], bbox[3] - bbox[1])
        dch = user32.GetWindowDC(hwnd)
        mem = gdi32.CreateCompatibleDC(dch)
        ptr = ctypes.c_void_p()
        hdr = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), size[0], -size[1], 1, 32, 0)
        obj = gdi32.CreateDIBSection(mem, ctypes.byref(hdr), 0, ctypes.byref(ptr), None, 0)
        gdi32.SelectObject(mem, obj)
        user32.PrintWindow(hwnd, mem, 2)
        buf = ctypes.string_at(ptr.value, size[0] * size[1] * 4)
        img = Image.frombuffer('RGB', size, buf, 'raw', 'BGRX', 0, 1)
        gdi32.DeleteObject(obj)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(hwnd, dch)
        return img


def strftime(dt: datetime | int | float | None = None, fmt: str = '%F %X') -> str:
    if isinstance(dt, (int, float)):
        dt = datetime.fromtimestamp(dt)
    elif not isinstance(dt, datetime):
        dt = datetime.now()
    return dt.strftime(fmt)


def awareness() -> int | None:
    '''Set process DPI awareness context'''
    if not WINDOWS:
        return
    with suppress(Exception):       # Win10 1703+
        val = ctypes.c_void_p(-4)   # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        return ctypes.windll.user32.SetProcessDpiAwarenessContext(val)
    with suppress(Exception):       # Win8.1+
        val = 2                     # PROCESS_PER_MONITOR_DPI_AWARE
        return ctypes.windll.shcore.SetProcessDpiAwareness(val)
    with suppress(Exception):       # Win7/Vista
        return ctypes.windll.user32.SetProcessDPIAware()


def play_sound(snd: int | str | Path = 0, dtms: int = 300) -> None:
    '''Play sound alias or wav from file or beep at specified frequency'''
    if isinstance(snd, int):
        if not winsound:
            return
        if snd < 37 or snd > 32767:
            return logger.error('Frequency must be in [37 ~ 32767]')
        if dtms <= 0:
            return logger.error('Duration must be positive number')
        return winsound.Beep(snd, dtms)
    path = Path(str(snd))
    if isinstance(snd, str) and winsound:
        if snd.lower().startswith('mb_') and hasattr(winsound, snd.upper()):
            return winsound.MessageBeep(getattr(winsound, snd.upper()))
        if snd.startswith('System'):
            return winsound.PlaySound(snd, winsound.SND_ASYNC | winsound.SND_ALIAS)
        if not path.is_file():
            return winsound.PlaySound(snd, winsound.SND_ASYNC | winsound.SND_APPLICATION)
    if not hasattr(play_sound, 'backends'):
        play_sound.backends = lst = []
        if winsound:                # Windows
            lst.append(lambda s: winsound.PlaySound(s, winsound.SND_ASYNC | winsound.SND_FILENAME))
        if shutil.which('ffplay'):  # ffmpeg
            lst.append(lambda s: exec_command('ffplay', '-nodisp', '-autoexit', s))
        if shutil.which('afplay'):  # MacOS
            lst.append(lambda s: exec_command('afplay', s))
        if shutil.which('aplay'):   # Linux ALSA
            lst.append(lambda s: exec_command('aplay', '--quiet', s))
    if path.is_file():
        for callback in play_sound.backends:
            return callback(str(path))


def size_format(size: int) -> str:
    '''Format size in bytes to human-readable string'''
    for i in range(3, 0, -1):
        if size > 2**(i * 10):
            return f'{size / 2**(i * 10):.2f} {" KMG"[i]}B'
    return f'{size} B'


def exec_result(*a, **k) -> str:
    '''Execute command by subprocess and return its output'''
    k.setdefault('stdout', subprocess.DEVNULL)
    k.setdefault('stderr', subprocess.DEVNULL)
    return subprocess.Popen(list(map(str, a)), **k)


def exec_command(*a, **k) -> subprocess.Popen:
    '''Execute command by subprocess and return the process'''
    k.setdefault('check', False)
    k.setdefault('capture_output', True)
    return subprocess.run(list(map(str, a)), **k).stdout.decode('utf-8')


class PZipFile(zipfile.ZipFile):
    class EmptyCompressor:
        def compress(self, data): return data
        def flush(self): return b''
    _empty = EmptyCompressor()

    def writefile(self, zinfo: zipfile.ZipInfo, fin: str | Path | bytes | typing.BinaryIO) -> None:
        '''Patch on zipfile._ZipWriteFile to write pre-compressed data'''
        comp = zipfile._get_compressor(zinfo.compress_type, zinfo._compresslevel) or self._empty
        data, crc, size, close = b'', 0, 0, False
        if isinstance(fin, (bytes, bytearray)):
            crc = zipfile.crc32(fin)
            size = len(fin)
            data = comp.compress(fin)
        elif isinstance(fin, (str, Path)):
            fin = open(fin, 'rb')
            close = True
        if hasattr(fin, 'read'):
            while buf := fin.read(shutil.COPY_BUFSIZE):
                crc = zipfile.crc32(buf, crc)
                size += len(buf)
                data += comp.compress(buf)
            close and fin.close()
        data += comp.flush()
        with self._lock, self.open(zinfo, mode='w') as fout:
            fout._compressor = self._empty          # skip compression
            fout.write(data)
            fout._crc, fout._file_size = crc, size  # fix crc and size


class IOMixin:
    __excludes__ = ()

    @cached_property
    def aliases(self) -> dict:
        return {
            field.metadata['alias']: name
            for name, field in self.__dataclass_fields__.items()
            if 'alias' in field.metadata
        }

    def __getattr__(self, key: str) -> typing.Any:
        if key in self.aliases:
            return getattr(self, self.aliases[key])
        raise AttributeError(f'Config has no attribute {key}')

    def __setattr__(self, key: str, val: typing.Any) -> None:
        if key in self.aliases:
            key = self.aliases[key]
        if field := self.__dataclass_fields__.get(key):
            if callable(field.type):
                val = field.type(val)
            if 'ge' in field.metadata:
                val = max(field.metadata['ge'], val)
            if 'le' in field.metadata:
                val = min(field.metadata['le'], val)
        super().__setattr__(key, val)

    @classmethod
    def load(cls, path: Path, ensure_exist: bool = False) -> typing.Self:
        if ensure_exist and not path.is_file():
            cls().dump(path)
        return cls(**jload(path))

    def dump(self, path: Path) -> Path:
        data = dataclasses.asdict(self)
        for key in self.__excludes__:
            data.pop(key, None)
        return jdump(data, path)


@dataclasses.dataclass
class GameSnapConfig(IOMixin):
    @staticmethod
    def _field(name: str, default: typing.Any = None, **k) -> dataclasses.Field:
        return dataclasses.field(default=default, metadata={'alias': name, **k})

    watchdog_enabled: bool = _field('watch', False)
    watchdog_seconds: int  = _field('wintv', 30, ge=1)
    schedule_enabled: bool = _field('sched', True)
    schedule_seconds: int  = _field('sintv', 10 * 60, ge=1)
    http_server_port: int  = _field('port', 9999, le=65535)
    preview_filepath: str  = _field('pimg', '')
    source_directory: Path = _field('sdir', BASEDIR)
    backup_directory: Path = _field('bdir', BASEDIR.parent / 'snapshots')
    backup_max_count: int  = _field('bcnt', 50, ge=1)
    sound_of_success: str  = _field('succ', 'SystemDefault')
    sound_of_failure: str  = _field('fail', 'SystemHand')
    hotkey_of_create: str  = _field('knew', 'ctrl+alt+n')
    hotkey_of_toggle: str  = _field('ktog', 'ctrl+alt+t')
    hotkey_of_delete: str  = _field('kdel', 'ctrl+alt+d')
    syncwith_process: str  = _field('proc', '')
    compress_storage: bool = _field('comp', True)
    current_language: str  = _field('lang', 'en')


@dataclasses.dataclass
class GameSnapRecord(IOMixin):
    type: str
    sdir: Path
    hdir: Path
    size: int                = 0
    time: float              = dataclasses.field(default_factory=time.time)
    hash: dict[str, str]     = dataclasses.field(repr=False, compare=False, default_factory=dict)
    info: dict[str, list]    = dataclasses.field(repr=False, compare=False, default_factory=dict)
    view: Image.Image | None = dataclasses.field(repr=False, compare=False, default=None)
    path: Path               = ''

    __excludes__ = ('info', 'view', 'path')

    @cached_property
    def tree(self) -> dict:
        root = {}
        for rel in self.hash:
            node, parts = root, rel.split('/')
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = rel  # self.info.get(rel, [None, 0, 0])
        return root

    def walk(
        self,
        on_dirs: typing.Callable[Path, None] | None = None,
        on_file: typing.Callable[Path, None] | None = None,
    ) -> None:
        def dfs(root: dict, base: Path) -> None:
            for part, node in root.items():
                path = base / part
                if isinstance(node, dict):
                    on_dirs and on_dirs(path)
                    dfs(node, path)
                else:
                    on_file and on_file(path)
        dfs(self.tree, Path(''))


class GameSnapEngine:
    '''Engine to create, restore, export, delete and summary snapshots'''

    @classmethod
    def _raw(cls, src: Path, hdir: Path) -> tuple[Path, Path]:
        with open(src, 'rb') as f:
            hasher = hashlib.file_digest(f, hashlib.md5)
            dst = hdir / hasher.hexdigest()[:16]
        with suppress(shutil.SameFileError, PermissionError):
            shutil.copy2(src, dst)
        return src, dst

    @classmethod
    def _enc(cls, src: Path, hdir: Path) -> tuple[Path, Path]:
        stat = src.stat()
        if stat.st_size < 4096:
            return cls._raw(src, hdir)
        if src.is_symlink() or stat.st_nlink > 1:   # soft or hard link
            with tempfile.NamedTemporaryFile(delete=False) as f:
                tmp = f.name
        else:
            tmp = hdir / str(stat.st_ino)
        hobj = hashlib.md5()
        with open(src, 'rb') as fin, zstd.open(tmp, 'wb') as fout:
            while buf := fin.read(shutil.COPY_BUFSIZE):
                hobj.update(buf)
                fout.write(buf)
        dst = hdir / (hobj.hexdigest()[:16] + '.zstd')
        with suppress(shutil.SameFileError, PermissionError):
            shutil.move(tmp, dst)
            shutil.copystat(src, dst)
        return src, dst

    @classmethod
    def _dec(cls, src: Path, dst: Path) -> Path:
        with suppress(shutil.SameFileError, PermissionError):
            if src.suffix == '.zstd':
                with zstd.open(src, 'rb') as fin, open(dst, 'wb') as fout:
                    shutil.copyfileobj(fin, fout)
                shutil.copystat(src, dst)
            else:
                shutil.copy2(src, dst)
        return dst

    @classmethod
    def _rec(cls, rel: str, path: Path, zf: PZipFile) -> Path:
        zinfo = zipfile.ZipInfo.from_file(path, rel)
        zinfo.compress_type = zf.compression
        zinfo._compresslevel = zf.compresslevel
        with (zstd.open if path.suffix == '.zstd' else open)(path, 'rb') as fin:
            zf.writefile(zinfo, fin)
        return path

    @classmethod
    def create(
        cls, cfg: GameSnapConfig, type: str = 'manual', pid: int | None = None,
        log: typing.Callable[[Path, float], None] | None = None
    ) -> Path:
        '''
        Create a snapshot

        Args:
            cfg (GameSnapConfig): provide sdir, bdir, comp, bcnt
            type (str, optional): 'watch', 'sched', 'hotkey' or [default: 'manual']
            pid (int, optional): capture screenshot for specified process id
            log (Callable[[Path, float], None], optional): callback to log progress

        Returns:
            Path: record path to the created snapshot
        '''
        view = capture(pid) if pid > 1 else None
        snap = GameSnapRecord(type, cfg.sdir, cfg.bdir / 'blob')
        snap.hdir.mkdirp()
        paths = [x for x in itertools.chain(cfg.sdir.rglob('*'), [cfg.sdir]) if x.is_file()]
        total = len(paths)
        files: dict[str, tuple[int, str]] = {}
        with poolmap(total) as pmap:
            for i, (src, dst) in enumerate(pmap(
                cls._enc if cfg.comp else cls._raw,
                paths, itertools.repeat(snap.hdir)
            ), 1):
                files[str(src.relative_to(snap.sdir))] = (dst.stat().st_size, dst.name)
                log and log(src, i / total)
        snap.size = sum(val[0] for val in files.values())
        for rel in sorted(files):
            snap.hash[rel] = files[rel][1]
        path = snap.dump(cfg.bdir / strftime(snap.time, '%Y%m%d-%H%M%S-%f.zstd'))
        view and view.save(path.with_suffix('.jpg'))
        for snap in cls.summary(cfg.bdir)[cfg.bcnt:]:
            cls.delete(snap.path)
        return path

    @classmethod
    def restore(
        cls, record: Path, rpaths: list[str] = [], sdir: Path | None = None,
        sync: bool = False, log: typing.Callable[[Path, float], None] | None = None
    ) -> Path:
        '''
        Restore a snapshot to specified directory

        Args:
            record (Path): record path
            rpaths (list[str], optional): filter files by relative paths
            sdir (Path, optional): source directory [default: sdir of record]
            sync (bool, optional): keep sdir layout exactly same as record [default: False]
            log (Callable[[Path, float], None], optional): callback to log progress

        Returns:
            Path: record path
        '''
        snap = GameSnapRecord.load(record)
        sdir = sdir or snap.sdir
        srcs, dsts = [], []
        for rel, hval in snap.hash.items():
            if rpaths and not rel.startswith(rpaths):
                continue
            srcs.append(snap.hdir / hval)
            dsts.append(sdir / rel)
        for path in sdir.rglob('*') if sync else []:
            if not path.is_file():
                continue
            rel = str(path.relative_to(sdir))
            if rpaths and not rel.startswith(rpaths):
                continue
            if rel not in snap.hash:
                path.unlink()
        if rpaths:
            def filtered_mkdir(rpath: Path) -> None:
                if str(rpath).startswith(rpaths):
                    (sdir / rpath).mkdirp()
            snap.walk(on_dirs=filtered_mkdir)
        else:
            snap.walk(on_dirs=lambda rpath: (sdir / rpath).mkdirp())
        total = len(srcs)
        with poolmap(total) as pmap:
            for i, path in enumerate(pmap(cls._dec, srcs, dsts), 1):
                log and log(path, i / total)
        return record

    @classmethod
    def export(
        cls, record: Path, output: Path,
        log: typing.Callable[[Path, float], None] | None = None
    ) -> Path:
        '''
        Export a snapshot to an zip file

        Args:
            record (Path): record path
            output (Path): output filename
            log (Callable[[Path, float], None], optional): callback to log progress

        Returns:
            Path: absolute path to the exported zip file
        '''
        snap = GameSnapRecord.load(record)
        dsts = []
        for hval in snap.hash.values():
            dsts.append(snap.hdir / hval)
        total = len(dsts)
        with poolmap(total) as pmap, PZipFile(output, 'w', zipfile.ZIP_DEFLATED) as zf:
            for i, path in enumerate(pmap(cls._rec, snap.hash, dsts, itertools.repeat(zf)), 1):
                log and log(path, i / total)
            view = record.with_suffix('.jpg')
            if view.is_file():
                zf.write(view, view.name)
        return output

    @classmethod
    def delete(cls, record: Path, log: typing.Callable[[Path, float], None] | None = None) -> Path:
        '''
        Delete a snapshot and cleanup unused files

        Args:
            record (Path): record path
            log (Callable[[Path, float], None], optional): callback to log progress

        Returns:
            Path: record path
        '''
        hdir = GameSnapRecord.load(record).hdir
        snaps = cls.summary(record.parent)
        if hdir.parent != record.parent:
            snaps += cls.summary(hdir.parent)
        used = set(itertools.chain(s.hash.values() for s in snaps if s.path != record))
        used, dels = set(), set()
        for snap in snaps:
            if snap.path != record:
                used.update(snap.hash.values())
        for path in hdir.iterdir():
            if path.is_file() and path.name not in used:
                dels.add(path)
        total = len(dels)
        with poolmap(total) as pmap:
            for i, path in enumerate(pmap(lambda x: x.unlink() or x, dels), 1):
                log and log(path, i / total)
        record.with_suffix('.jpg').unlink(missing_ok=True)
        record.unlink()
        return record

    @classmethod
    def summary(cls, bdir: Path, detail: bool = False) -> list[GameSnapRecord]:
        '''
        List all snapshots with newest first

        Args:
            bdir (Path): GameSnapConfig.backup_directory
            detail (bool, optional): include detail information [default: False]

        Returns:
            list[GameSnapRecord]
        '''
        if not bdir.is_dir():
            return {}
        if not hasattr(cls, '_cache'):
            cls._cache = {}
        paths = [p for p in bdir.iterdir() if p.suffix in {'.json', '.zstd'}]
        cache = cls._cache.setdefault((str(bdir), bool(detail)), {})
        hasher = hashlib.md5(f'{bdir.stat().st_mtime}'.encode())
        for path in paths:
            stat = path.stat()
            hasher.update(f'{path}{stat.st_mtime}|{stat.st_size}'.encode())
        hval, cache['hash'] = cache.get('hash', ''), hasher.hexdigest()
        if hval == cache['hash']:
            return cache['data']
        snaps = []
        for path in paths:
            try:
                snap = GameSnapRecord.load(path)
                snap.path = path
                snaps.append(snap)
                assert detail
            except Exception:
                continue
            view = path.with_suffix('.jpg')
            if view.is_file():
                snap.view = Image.open(view).copy()
            for rel, hval in snap.hash.items():
                dst = snap.hdir / hval
                try:
                    for prev in snaps[-2::-1]:
                        if rel in prev.info:
                            snap.info[rel] = prev.info[rel]
                            break
                    else:
                        stat = dst.stat()
                        snap.info[rel] = [dst, stat.st_size, stat.st_mtime]
                except Exception:
                    snap.info[rel] = [dst, 0, 0]
        cache['data'] = sorted(snaps, key=lambda x: x.time, reverse=True)
        return cache['data']


class GameSnapRouter(BaseHTTPRequestHandler):
    '''Simple router to register callback functions and handle http requests'''
    route: dict[str, dict[str, typing.Any]] = {}

    @staticmethod
    def getname(obj: typing.Any) -> str: return getattr(obj, '__name__', str(obj))

    @classmethod
    def from_port(
        cls, port: int, host: str = 'localhost'
    ) -> tuple[threading.Thread, ThreadingHTTPServer]:
        srv = ThreadingHTTPServer((host, port), cls)
        return threading.Thread(target=srv.serve_forever, name='httpd', daemon=True), srv

    @classmethod
    def register(cls, func: typing.Callable[..., typing.Any], path: str = ''):
        sig = inspect.signature(func)
        cls.route[path or cls.getname(func)] = {'func': func, 'meta': {
            'desc': inspect.getdoc(func) or '',
            'params': {
                n: {
                    'type': cls.getname(p.annotation),
                    'default': None if p.default is p.empty else p.default,
                    'required': p.default is p.empty,
                }
                for n, p in sig.parameters.items()
                if n != 'self' and p.kind not in [p.VAR_POSITIONAL, p.VAR_KEYWORD]
            },
            'return': cls.getname(sig.return_annotation)
        }}

    def send_json(self, data: dict, code: int = 200) -> None:
        self.send_response(code)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(data, default=str).encode())

    def do_GET(self):
        '''
        Simple HTTP Server to handle JSON requests and response in JSON

        +--------+-------+--------------------------------------+
        | Method | Path  | Description                          |
        +--------+-------+--------------------------------------+
        | GET    | /     | return this help message             |
        | GET    | /apis | list all available functions         |
        | POST   | /apis | call functions with given parameters |
        +--------+-------+--------------------------------------+

        Examples:

        ```bash
        curl -X GET http://localhost:<PORT>/apis

        curl -X POST http://localhost:<PORT>/apis \\
             -H 'Content-Type: application/json' \\
             -d '{"summary": {"detail": true}}'
        ```

        ```python
        import requests
        URL = 'http://localhost:<PORT>/apis'
        apis = requests.get(URL).json()

        create_then_summary = {'create': {}, 'summary': {'detail': True}}
        print(requests.post(URL, json=create_then_summary).json())
        ```

        ```javascript
        fetch('http://localhost:<PORT>/apis', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({delete: {record: 'C:/backups/111.json'}}),
        }).then(res => res.json()).then(console.log);
        ```

        On success:
        ```json
        {"create": "...", "summary": [...], "delete": "...", ...}
        ```

        On error:
        ```json
        {"error": "Invalid request: ..."}
        ```
        '''
        if self.path == '/apis':
            self.send_json({key: val['meta'] for key, val in self.route.items()})
        else:
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self.end_headers()
            self.wfile.write(inspect.getdoc(self.do_GET).encode())

    def do_POST(self):
        try:
            assert self.path == '/apis', f'unknown path {self.path}'
            content_length = int(self.headers['Content-Length'])
            data = json.loads(self.rfile.read(content_length).decode())
            rst = {}
            for name, param in data.items():
                assert name in self.route, f'unknown callback {name}'
                assert isinstance(param, dict), f'unknown parameter {param}'
                rst[name] = self.route[name]['func'](**param)
            self.send_json(rst)
        except json.JSONDecodeError:
            self.send_json({'error': 'Invalid JSON'}, 400)
        except AssertionError as e:
            self.send_json({'error': f'Invalid request: {e}'}, 400)
        except Exception as e:
            self.send_json({'error': f'Request failed: {e}'}, 500)


class GameSnapBroker(object):
    '''Manage config, engine, router and subtasks (watchdog, scheduler etc.)'''

    def __init__(self, cfg: str | Path | None = None, srv: bool = True, verbose: int = 0) -> None:
        self._start: int | bool = 0
        self._nosrv = not srv
        self._level = int(verbose or 0)
        self._mutex = threading.Lock()
        self._watch = watchdog.observers.Observer()
        self._snaps = [None, threading.Event(), threading.Event(), {}]
        self._sched = [None, threading.Event(), threading.Event()]
        self._psync = [None, threading.Event()]
        self._httpd = [None, None]
        self.engine = GameSnapEngine()
        self.config = GameSnapConfig()
        self.update(self.config if cfg is None else cfg)
        for attr, func in vars(self.__class__).items():
            if not attr.startswith('_') and callable(func):
                GameSnapRouter.register(func)

    __getitem__ = __getattr__ = lambda self, key: getattr(self.config, key)

    def __setitem__(self, key: str, val: typing.Any) -> None:
        if key in self.config.aliases:
            key = self.config.aliases[key]
        if not hasattr(self.config, key):
            return
        if hasattr(self, f'_set_{key}'):
            getattr(self, f'_set_{key}')(val)
        else:
            setattr(self.config, key, val)

    @contextmanager
    def _inject(self, k: dict):
        newline = False
        if self._level > 1:
            k.setdefault('log', lambda p, c: logger.debug(f'{c:7.2%} {p}'))
        elif self._level:
            def cprint(path: Path, pcent: float) -> None:
                nonlocal newline
                newline = True
                print(f'\r\033[K{pcent:7.2%} {path}', end='', flush=True)
            k.setdefault('log', cprint)
        try:
            yield k
        except Exception:
            if self.config.fail and not self._nosrv:
                play_sound(self.config.fail)
            raise
        finally:
            if self.config.succ and not self._nosrv:
                play_sound(self.config.succ)
            if newline:
                print('')

    def preset(self, path: Path, name: str = '') -> dict:
        '''
        Parse presets from EZ Auto Backup format files (e.g. EZAB/GAMELIST.ERAB)

        Args:
            path (Path): path to presets file
            name (str, optional): preset name to use

        Returns:
            dict: parsed presets

        Examples:
            >>> broker = GameSnapBroker()
            >>> broker.preset('D:/EZAB/GAMELIST.ERAB')
            {'games': {                             # example
                <Game name>: [                      # 'Project Zomboid'
                    <Save file basename>,           # 'Map'
                    <Save file extension>,          # '.bin'
                    <Game process>,                 # 'ProjectZomboid64.exe'
                    <Save file path>,               # '<UserName>\\Zomboid'
                    <Saves in separate folder>,     # 1
                    <Delete before restore>,        # 0
                    <Only backup single file>,      # 1: Map*.bin, 0: Map.bin
                ],
                ...
            }}
            >>> broker.preset('D:/EZAB/GAMELIST.ERAB', name='Project Zomboid')
            Using sdir=~/Zomboid and proc=ProjectZomboid64.exe
        '''
        data = {'games': {}}
        info = ConfigParser(default_section='Info')
        with open(abspath(path), encoding='utf8') as f:
            for row in csv.reader(f, delimiter='#'):
                if not row:
                    continue
                if row[0] == 'END':
                    break
                data['games'][row[0]] = row[1:]
            info.read_string(f.read())
        for sname, section in info.items():
            data.setdefault(sname.lower(), {}).update(section.items())
        if name in data['games']:
            sdir = abspath(re.sub(r'<(\w+)>', lambda m: {
                'id': '*',
                'steam': where('Valve\\Steam', 'InstallPath', '~/.steam'),
                'ubisoft': where('Ubisoft\\Launcher', 'InstallDir', '~/.ubisoft'),
                'appdata': '~/AppData',
                'username': '~',
                'documents': '~/Documents',
                'mydocuments': '~/Documents',
            }.get(m.group(1).lower(), ''), data['games'][name][3]))
            for i, part in enumerate(sdir.parts):
                if not set(part).intersection({'*', '?', '[', ']'}):
                    continue
                base, pattern = Path(*sdir.parts[:i]), os.sep.join(sdir.parts[i:])
                with suppress(Exception):
                    if pattern:
                        self.config.sdir = next(base.glob(pattern))
                    elif base.is_dir():
                        self.config.sdir = base
                    self.config.proc = data['games'][name][2]
                break
            if self._level:
                logger.info(f'Using sdir={self.config.sdir} and proc={self.config.proc}')
        return data

    def update(self, cfg: str | dict | Path | None = None, **k) -> dict[str, typing.Any]:
        '''
        Update configuration from file or string

        Args:
            cfg (str | dict | Path, optional): absolute path to config file or JSON string

        Returns:
            dict: current configuration

        Examples:
            >>> broker = GameSnapBroker()
            >>> broker.update(None)                     # get current config
            {'http_server_port': 9999, 'backup_max_count': 20, ...}
            >>> broker.update({'port': 0, 'bcnt': 0})   # support alias
            {'http_server_port': 0, 'backup_max_count': 0, ...}
            >>> broker.update('{"http_server_port": 8888}')
            {'http_server_port': 8888, 'backup_max_count': 10, ...}
            >>> broker.update('/absolute/path/to/config.json')
            {'http_server_port': 9999, 'backup_max_count': 10, ...}
        '''
        data = {}
        if isinstance(cfg, str):
            if '{' in cfg:
                data = json_repair.loads(cfg) if json_repair else json.loads(cfg)
            elif Path(cfg).is_absolute():
                data = jload(cfg)
        elif isinstance(cfg, dict):
            data = cfg
        elif isinstance(cfg, _Path):
            data = dataclasses.asdict(GameSnapConfig.load(cfg, ensure_exist=True))
        with self._mutex:
            for key, val in data.items():
                self[key] = val
        return dataclasses.asdict(self.config)

    def create(self, type: str = 'manual', **k) -> Path:
        '''
        Create a new snapshot with current GameSnapConfig

        Args:
            type (str, optional): 'watch', 'sched', 'hotkey' or [default: 'manual']

        Returns:
            Path: record path to the created snapshot
        '''
        with self._mutex, self._inject(k):
            return self.engine.create(self.config, str(type), self.start, **k)

    def restore(
        self, record: Path, rpaths: list[str] = [], sdir: Path | None = None, **k
    ) -> Path:
        '''
        Restore a snapshot from backup directory

        Args:
            record (Path): record path
            rpaths (list[str], optional): filter files by relative paths
            sdir (Path, optional): source directory [default: sdir of record]

        Returns:
            Path: record path

        Examples:
            >>> broker = GameSnapBroker()
            >>> record = random.choice(broker.summary())
            >>> broker.restore(record.path)
            Path('/absolute/path/to/<record.path>')
        '''
        k.setdefault('sync', True)
        with self._mutex, self._inject(k):
            old = (self.config.watch, self.config.sched)
            self.config.watch = self.config.sched = False
            rst = self.engine.restore(abspath(record), rpaths, sdir, **k)
            self.config.watch, self.config.sched = old
        return rst

    def export(self, record: Path, output: Path, **k) -> Path:
        '''
        Export current configuration to file or string

        Args:
            record (Path): record path
            output (Path): output filename

        Returns:
            Path: absolute path to the exported zip file

        Examples:
            >>> broker = GameSnapBroker()
            >>> record = random.choice(broker.summary())
            >>> broker.export(record.path, '~/save')
            Path('C:/Users/<username>/save.zip')
        '''
        with self._mutex, self._inject(k):
            output = abspath(output).with_suffix('.zip').touchp()
            return self.engine.export(abspath(record), output, **k)

    def delete(self, record: Path, **k) -> Path:
        '''
        Delete a snapshot with current GameSnapConfig

        Args:
            record (Path): record path

        Returns:
            Path: record path

        Examples:
            >>> broker = GameSnapBroker()
            >>> broker.delete(random.choice(broker.summary()).path)
        '''
        with self._mutex, self._inject(k):
            return self.engine.delete(abspath(record), **k)

    def delete_latest(self, **k) -> Path:
        '''Delete the latest snapshot in backup directory'''
        for snap in self.summary(False)[:1]:
            return self.delete(snap.path, **k)

    def delete_oldest(self, **k) -> Path:
        '''Delete the oldest snapshot in backup directory'''
        for snap in self.summary(False)[-1:]:
            return self.delete(snap.path, **k)

    def delete_all(self, **k) -> list[Path]:
        '''Delete all snapshots in backup directory'''
        return [self.delete(snap.path, **k) for snap in self.summary(False)]

    def summary(self, detail: bool = True) -> list[GameSnapRecord]:
        '''
        Get summary of all snapshots with current GameSnapConfig

        Args:
            detail (bool, optional): include detail information [default: True]

        Returns:
            list[GameSnapRecord]

        Examples:
            >>> broker = GameSnapBroker()
            >>> broker.summary(detail=False)[0].info
            {}
            >>> broker.summary(detail=True)[0].info
            {<relpath>: [<fullpath>, <size>, <mtime>], ...}
        '''
        with self._mutex:
            return self.engine.summary(self.config.bdir, detail=bool(detail))

    def toggle_mode(self, sync: bool = False, start: int | bool | None = None) -> int | bool:
        '''
        Switch between manual and sync mode, then start/stop the service

        Args:
            sync (bool, optional): switch to sync mode [default: False]
            start (int | bool | None, optional): start/stop the service [default: None]

        Returns:
            int | bool: current mode

        Examples:
            >>> broker = GameSnapBroker()
            >>> proc = subprocess.Popen(broker.syncwith_process)
            >>> broker.start
            <proc.pid>                          # sync mode (process running)
            >>> proc.terminate()
            >>> broker.start
            0                                   # sync mode (process not running)
            >>> broker.toggle_mode()
            False                               # turn to manual mode (not started)
            >>> broker.toggle_mode()
            True                                # toggle manual mode (started)
            >>> broker.toggle_mode(sync=True)
            0                                   # turn to sync mode (not started)
        '''
        with self._mutex:
            if sync:
                self.start = int(self.start)            # sync with process
            elif isinstance(start, (int, bool)):
                self.start = start
            elif isinstance(self.start, int):
                self.start = bool(self.start)           # manual mode
            else:
                self.start = not self.start             # start / stop
        return self.start

    def _set_start(self, new: bool | int) -> None:
        old, self._start = self.start, new
        if not old and new and self.config.sched:
            self._sched[2].set()

    start = property(lambda s: s._start, _set_start)
    snaps = property(lambda s: s._snaps[3])

    def on_snaps(self, callback: typing.Callable[[], None] | bool | None = None) -> None:
        if isinstance(callback, bool):
            return self._snaps[2].set()
        self._snaps[1].set()
        self._snaps[2].set()
        if not callable(callback):
            return self._snaps[0] and self._snaps[0].join()
        self._snaps[1].clear()

        def loop() -> None:
            while not self._snaps[1].is_set():
                snaps, self._snaps[3] = self.snaps, {str(x.path): x for x in self.summary()}
                if list(snaps) != list(self.snaps):
                    callback()
                self._snaps[2].wait(5)
        self._snaps[0] = threading.Thread(target=loop, name='summary', daemon=True)
        self._snaps[0].start()

    def total_size(self, snaps: list[GameSnapRecord] | None = None) -> int:
        return sum(itertools.chain(
            (s.path.stat().st_size for s in (snaps or self.snaps)),
            {k: v[1] for s in (snaps or self.snaps) for k, v in s.info.items()}.values()
        ))

    def _set_watchdog_enabled(self, v: bool) -> None:
        self.config.watch = v
        self._watch.unschedule_all()
        if not self.config.watch or self._nosrv:
            return

        class NestHandler(watchdog.events.FileSystemEventHandler):
            def on_modified(s, e: watchdog.events.FileSystemEvent) -> None:
                if e.is_directory or not self.start:
                    return
                logger.debug(f'Modified {e.src_path}')
                if hasattr(s, 'debounce'):
                    s.debounce.cancel()
                s.debounce = threading.Timer(self.config.wintv, self.create, ('watch',))
                s.debounce.start()
        self._watch.schedule(NestHandler(), self.config.sdir, recursive=True)
        if not self._watch.is_alive():
            self._watch.name = 'observer'
            self._watch.start()

    def _set_schedule_enabled(self, v: bool) -> None:
        self.config.sched = v
        self._sched[1].set()
        self._sched[2].set()
        if not self.config.sched or self._nosrv:
            return self._sched[0] and self._sched[0].join()
        self._sched[1].clear()

        def loop() -> None:
            while not self._sched[1].is_set():
                intv, tout = self.config.sintv, not self._sched[2].wait(self.config.sintv)
                if (tout or intv != self.config.sintv) and self.start:
                    self.create('sched')
                self._sched[2].clear()
        self._sched[0] = threading.Thread(target=loop, name='scheduler', daemon=True)
        self._sched[0].start()

    def _set_schedule_seconds(self, v: int) -> None:
        self.config.sintv = v
        self._sched[2].set()

    def _set_http_server_port(self, v: int) -> None:
        self.config.port = v
        alive = self._httpd[0] and self._httpd[0].is_alive()
        if alive and self._httpd[1].server_port != self.config.port:
            self._httpd[1].shutdown()
            self._httpd[1].server_close()
            self._httpd[0].join(1)
        if self.config.port and not alive and not self._nosrv:
            self._httpd = GameSnapRouter.from_port(self.config.port)
            self._httpd[0].start()

    def _set_syncwith_process(self, v: str) -> None:
        self.config.proc = (v or '').lower()
        if not self.config.proc or self._nosrv:
            self._psync[1].set()
            return self._psync[0] and self._psync[0].join()
        if self._psync[0] and self._psync[0].is_alive():
            return
        self._psync[1].clear()

        def loop(intv: int = 1) -> None:
            while not self._psync[1].wait(intv):
                if isinstance(self.start, int):
                    self.start = pidof(self.config.proc)
        self._psync[0] = threading.Thread(target=loop, name='syncwith-process', daemon=True)
        self._psync[0].start()

    def _set_source_directory(self, v: Path) -> None:
        sdir = abspath(v)
        if not sdir.exists():
            raise ValueError('sdir does not exist')
        if self.config.sdir != sdir:
            self.config.sdir = sdir
            self._set_watchdog_enabled(self.config.watch)  # refersh watchdog
        if not self.config.bdir or self.config.bdir.is_relative_to(sdir):
            self._set_backup_directory(sdir.parent / 'snapshots')

    def _set_backup_directory(self, v: Path) -> None:
        bdir = abspath(v)
        if bdir.is_relative_to(self.config.sdir):
            raise ValueError('bdir must be outside of sdir')
        self.config.bdir = bdir

    def _set_preview_filepath(self, v: str) -> None:
        self.config.pimg = v and Path(v) or ''

    def _sound_factory(name: str):
        def wrapper(self, v: str) -> None:
            old, new = getattr(self.config, name), str(v or '')
            if old != new:
                setattr(self.config, name, new)
                new and play_sound(new)
        return wrapper

    def _hotkey_factory(name: str, callback: typing.Callable[[typing.Self], None]):
        def wrapper(self, v: str) -> None:
            old, new = getattr(self.config, name), str(v or '')
            with suppress(Exception):
                keyboard.remove_hotkey(old)
            try:
                if new and not self._nosrv:
                    keyboard.add_hotkey(new, lambda: logger.info(f'{name}: {callback(self)}'))
            except Exception as e:
                logger.error(e)
            setattr(self.config, name, new)
        return wrapper

    _set_sound_of_success = _sound_factory('succ')
    _set_sound_of_failure = _sound_factory('fail')
    _set_hotkey_of_create = _hotkey_factory('knew', lambda s: s.create('hotkey'))
    _set_hotkey_of_toggle = _hotkey_factory('ktog', lambda s: s.toggle_mode(start=not s.start))
    _set_hotkey_of_delete = _hotkey_factory('kdel', lambda s: s.delete_latest())

    def record_hotkey(
        self, name: typing.Literal['knew', 'ktog', 'kdel'],
        tout: int = 5, log: typing.Callable[str, None] | None = None
    ) -> str:
        '''
        Listen for keyboard events and record a hotkey

        Args:
            name (str): must be 'knew', 'ktog' or 'kdel'
            tout (int): seconds to wait for key press
            log (Callable[str, None], optional): callback to log key bindings

        Returns:
            str: new hotkey

        Notes:
            1. All keys will be captured and suppressed for further propagation
            2. If no keys pressed until timeout then return '' (cleared)
            3. Press `esc` to cancel recording and return previous value
        '''
        old, self[name] = self[name], ''                    # disable hotkey
        buf, que = {}, queue.Queue()
        stop = keyboard.hook(que.put, suppress=True)        # start record
        try:
            while True:
                evt = que.get(timeout=tout)
                assert 'esc' not in evt.name
                buf[evt.name] = evt.event_type == 'up'
                if not buf[evt.name]:                       # key pressed
                    log and log('+'.join(buf))
                elif not all(buf.values()):                 # not released
                    continue
                elif all(map(keyboard.is_modifier, buf)):   # no valid key
                    log and log('+'.join(buf) + '+?')
                else:                                       # all released
                    self[name] = '+'.join(buf)
                    break
        except (AssertionError, KeyboardInterrupt):         # cancel record
            self[name] = old
        except queue.Empty:                                 # timeout
            pass
        finally:
            stop()
        return self[name]


class UIMixin:
    langs = {
        'en': {
            'en': 'English', 'lang': 'Language', 'ready': 'Ready', 'error': 'Error',
            'sdir': 'Source', 'bdir': 'Backup', 'select': 'Select', 'browse': 'Open',
            'cancel': 'Cancel', 'drive': 'Drive', 'path': 'Paths', 'opts': 'Options',
            'watch': 'Watchdog', 'wintv': 'Debounce (s)', 'port': 'HTTP Server Port',
            'sched': 'Schedule', 'sintv': 'Interval (s)', 'bcnt': 'Backup Count',
            'knew': 'Create', 'ktog': 'Start / Stop', 'kdel': 'Delete Latest',
            'succ': 'Notify success', 'fail': 'Notify failure', 'proc': 'Game Process',
            'pimg': 'Thumbnail', 'manual': 'Manual', 'hotkey': 'Hotkey',
            'snaps': 'Snapshots', 'type': 'Type', 'start': 'Start', 'stop': 'Stop',
            'files': 'Files', 'size': 'File Size', 'mtime': 'Last Modified',
            'create': 'Create', 'restore': 'Restore', 'export': 'Export', 'delete': 'Delete',
            'on': 'On', 'off': 'Off', 'yes': 'Yes', 'no': 'No',
        },
        'zh': {
            'zh': '中文', 'lang': '语言', 'ready': '就绪', 'error': '错误',
            'sdir': '存档目录', 'bdir': '备份目录', 'select': '选择', 'browse': '打开',
            'cancel': '取消', 'drive': '磁盘', 'path': '路径', 'opts': '配置',
            'watch': '监控变动', 'wintv': '防抖 (秒)', 'port': 'HTTP 端口',
            'sched': '定时备份', 'sintv': '间隔 (秒)', 'bcnt': '备份数量',
            'knew': '新建备份', 'ktog': '启停任务', 'kdel': '删除最新',
            'succ': '成功提示', 'fail': '失败提示', 'proc': '跟随进程',
            'pimg': '缩略图', 'manual': '手动', 'hotkey': '快捷键',
            'snaps': '个备份', 'type': '类型', 'start': '开始', 'stop': '停止',
            'files': '个文件', 'size': '文件大小', 'mtime': '修改时间',
            'create': '新建', 'restore': '恢复', 'export': '导出', 'delete': '删除',
            'on': '已启用', 'off': '已关闭', 'yes': '是', 'no': '否',
        }
    }

    def __init__(self, brok: GameSnapBroker | None = None, langdir: Path = BASEDIR, **k):
        self.brok = brok or GameSnapBroker(CFGFILE, **k)
        for path in langdir.glob('lang-*.json'):
            if data := jload(path):
                self.langs[path.stem[5:]] = data

    def __call__(self, k: str) -> str:
        return self.langs[self.brok.lang].get(k, self.langs['en'].get(k, k))


class AskMixin:
    class AskYesNo(textual.screen.ModalScreen[bool]):
        CSS = '''AskYesNo Static { min-width: 50vw; }'''

        def __init__(self, title: str = '', msg: str = '', subtitle: str = '') -> None:
            self._msg, self._title, self._subtitle = msg, title, subtitle
            super().__init__()

        def compose(self) -> textual.app.ComposeResult:
            with ttc.Grid(classes='border') as grid:
                grid.border_title = self._title
                grid.border_subtitle = self._subtitle
                yield ttw.Static(self._msg, classes='col2')
                yield ttw.Button(self.app('yes') + ' (y)', id='y', variant='success')
                yield ttw.Button(self.app('no') + ' (n)', id='n')

        def on_button_pressed(self, evt: ttw.Button.Pressed) -> None:
            self.dismiss(evt.button.id == 'y')

        def on_key(self, evt: typing.Any) -> None:
            if evt.key.lower() in ['n', 'escape']:
                self.dismiss(False)
            elif evt.key.lower() == 'y':
                self.dismiss(True)

    class AskDirectory(textual.screen.ModalScreen[Path | None]):
        CSS = '''AskDirectory DirectoryTree { height: auto; max-height: 50vh; margin: 1 0; }'''
        base = textual.reactive.reactive(Path)
        path = textual.reactive.reactive(Path)

        class DirOnlyTree(ttw.DirectoryTree):
            def filter_paths(self, paths: list[Path]) -> list[Path]:
                return [p for p in paths if p.is_dir()]

        def __init__(self, path: Path = Path(''), base: Path | None = None) -> None:
            super().__init__()
            self.set_reactive(self.__class__.base, abspath(base or path.parent))
            self.set_reactive(self.__class__.path, abspath(path).relative_to(self.base))

        @property
        def parts(self) -> str:
            return ''.join(
                f'[@click=screen.rebase("{p}")]{p.name or str(p)[:WINDOWS and 2 or 0]}/[/]'
                for p in [self.base, *self.base.parents][::-1]
            )

        def compose(self) -> textual.app.ComposeResult:
            with ttc.Grid(classes='border') as grid:
                grid.border_title = self.__class__.__name__
                if WINDOWS:
                    drive, drives = self.base.anchor[0], []
                    bitmask = ctypes.windll.kernel32.GetLogicalDrives()
                    for i in range(26):
                        if bitmask & (1 << i):
                            drives.append(chr(i + ord('A')))
                    yield ttw.Label(self.app('drive') + ':')
                    yield ttw.Select.from_values(drives, value=drive, allow_blank=False)
                with ttc.Vertical(classes='col2'):
                    yield ttw.Static(self.parts, id='base')
                    with self.DirOnlyTree(self.base, id='tree') as tree:
                        tree.show_root = False
                    yield ttw.Input(str(self.path), id='path', classes='flat')
                yield ttw.Button(self.app('select') + ' (y)', id='y', variant='success')
                yield ttw.Button(self.app('cancel') + ' (n)', id='n')

        def on_directory_tree_directory_selected(self, evt: typing.Any) -> None:
            self.path = evt.path.resolve()

        def on_input_changed(self, evt: ttw.Input.Changed) -> None:
            self.path = abspath(self.base / evt.value)

        def on_select_changed(self, evt: ttw.Select.Changed) -> None:
            if evt.value != self.base.anchor[0]:
                self.path = Path(f'{evt.value}:/')

        def on_button_pressed(self, evt: typing.Any) -> None:
            self.dismiss((self.base / self.path) if evt.button.id == 'y' else None)

        def on_key(self, evt: typing.Any) -> None:
            if evt.key.lower() in ['n', 'escape']:
                self.dismiss(None)
            elif evt.key.lower() == 'y':
                self.dismiss(self.base / self.path)

        def action_rebase(self, base: str | Path) -> None:
            self.base = Path(base)
            self.path = self.base / self.path.name

        def watch_base(self, path: Path) -> None:
            tree = self.query_one('#tree')
            if tree.path != self.base:
                tree.path = self.base
            self.query_one('#base').update(self.parts)

        def watch_path(self, path: Path) -> None:
            if path.is_absolute() and not path.is_relative_to(self.base):
                self.base = path.parent
            self.set_reactive(self.__class__.path, path.relative_to(self.base))
            self.query_one('#path').value = str(self.path)

    class AskFilename(AskDirectory):
        CSS = '''AskFilename DirectoryTree { height: auto; max-height: 50vh; margin: 1 0; }'''
        DirOnlyTree = ttw.DirectoryTree

        def on_directory_tree_directory_selected(self, evt: typing.Any) -> None:
            self.action_rebase(evt.path)

        def on_directory_tree_file_selected(self, evt: ttw.DirectoryTree.FileSelected) -> None:
            self.path = evt.path


class GameSnapTUI(textual.app.App, UIMixin, AskMixin):
    CSS = '''
        Screen {
            layout: grid;
            grid-size: 2;
            grid-rows: auto auto 1fr auto;
            grid-columns: 3fr 4fr;
        }

        ModalScreen {
            align: center middle;
            Grid {
                padding: 1 2;
                grid-size: 2;
                grid-gutter: 1;
                grid-rows: auto;
                grid-columns: 1fr auto;
            }
        }

        * { scrollbar-size-vertical: 1; }

        Label {
            height: 100%;
            content-align: left middle;
        }

        .border { border: round $primary; }

        .row2 { row-span: 2; }

        .col2 { column-span: 2; }

        .flat {
            padding: 0;
            margin: 0 -1;
            height: 1 !important;
            border: none !important;
        }

        .hide { display: none; }

        .fold * { display: none; }

        .btns {
            height: 3;
            Button {
                width: 1fr;
                margin: 0 1;
            }
        }

        #path {
            padding: 0 1;
            Grid {
                height: 3;
                grid-size: 4;
                grid-columns: auto 1fr auto auto;
                Button:last-child { margin-left: 1; }
            }
        }

        #opts {
            max-height: 26;
            grid-size: 2;
            grid-rows: auto;
            grid-columns: auto 1fr;
            padding-left: 1;
            overflow-y: auto;
            Label { padding-right: 1; }
            Switch, RadioButton { width: 1fr; }
        }

        #snap DataTable { height: 1fr; margin: 0 1 1 1; }

        #list {
            margin-left: 1;
            Tree { margin: 0 1 1 1; }
            Static { margin: 0 1; width: auto; }
        }

        #logs {
            grid-size: 2;
            grid-gutter: 1;
            grid-columns: auto 1fr;
            Grid {
                height: 1;
                grid-size: 2;
                grid-columns: auto;
                #minus { display: none; }
                &.expand {
                    grid-size: 1;
                    grid-rows: 1fr auto;
                    #minus { display: block; }
                }
            }
            RichLog { height: 1; overflow-y: auto; }
            ProgressBar Bar { width: 1fr; padding-right: 1; }
        }
    '''
    TITLE = ' '.join(APPINFO[:2])
    SUB_TITLE = ' '.join(APPINFO[2:])
    AUTO_FOCUS = ''
    BINDINGS = [
        textual.binding.Binding(
            key='question_mark',
            action='show_help_panel',
            description='Show help screen',
            key_display='?'
        ),
        ('q', 'quit', 'Quit the app'),
        ('d', 'toggle_dark', 'Toggle dark mode'),
    ]

    def __init__(self, css: list[Path] | None = None, *a, **k) -> None:
        if css:
            super().__init__(css_path=css, watch_css=True)
        else:
            super().__init__()
        UIMixin.__init__(self, *a, **k)
        self.select: tuple[str, str] = ['', '']

    def __getitem__(self, key: typing.Any) -> textual.widget.Widget: return self.query_one(key)

    def compose(self) -> textual.app.ComposeResult:
        yield ttw.Header(show_clock=True)
        with ttc.Vertical(id='path', classes='border col2') as frame:
            frame.border_title = self('path')
            for i, key in enumerate(['sdir', 'bdir']):
                yield ttc.Grid(
                    ttw.Label(self(key)),
                    ttw.Input(str(self.brok[key]), id=key),
                    ttw.Button(self('select'), id=f'select-{key}', variant='warning'),
                    ttw.Button(self('browse'), id=f'browse-{key}'),
                )
        with ttc.Grid(id='opts', classes='border') as frame:
            frame.border_title = self('opts')
            yield ttw.Label(self('lang'))
            yield ttw.Select.from_values(
                self.langs, id='lang', value=self.brok.lang, allow_blank=False
            )
            for key in ['watch', 'wintv', 'sched', 'sintv', 'bcnt', 'port', 'proc']:
                val = self.brok[key]
                yield ttw.Label(self(key), id=f'l{key}')
                if isinstance(val, bool):
                    yield ttw.RadioButton(self('on' if val else 'off'), id=key, value=val)
                elif isinstance(val, int):
                    yield ttw.Input(id=key, value=str(val), type='integer')
                else:
                    yield ttw.Input(id=key, value=str(val))
        with ttc.Vertical(id='list', classes='border row2'):
            with ttw.Static(id='pimg') as pimg:
                pimg.data = {'path': None, 'image': None, 'pixels': None}
            with ttw.Tree('', data=('', 0, 0), id='tree') as tree:
                tree.ICON_NODE_EXPANDED = '📂 '
                tree.ICON_NODE = '📁 '
                tree.ICON_FILE = '📄 '
            yield ttc.Horizontal(
                ttw.Button(self('restore'), id='restore'),
                ttw.Button(self('export'), id='export'),
                ttw.Button(self('delete'), id='delete'),
                classes='btns'
            )
        with ttc.Vertical(id='snap', classes='border'):
            with ttw.DataTable(id='table', cursor_type='row') as table:
                table.name_col = table.add_column('', key='name')
                table.add_column(self('type'), key='type', width=8)
                table.add_column(self('size'), key='size', width=9)
            yield ttc.Horizontal(
                ttw.Button(self('create'), id='create'),
                ttw.Button(self('manual'), id='toggle'),
                classes='btns'
            )
        with ttc.Grid(id='logs', classes='col2'):
            with ttc.Grid():
                yield ttw.Static('-', id='minus')
                yield ttw.Static('+', id='plus')
            yield ttw.ProgressBar(id='pbar', total=1, show_eta=False, classes='hide')
            yield ttw.RichLog(id='slog', highlight=True, max_lines=99, wrap=True)
        yield ttw.Footer()

    def slog(self, *a, **k) -> None:
        '''display message on RichLog'''
        self['#slog'].write(' '.join([strftime('[%X]'), *map(str, a)]), **k)

    def set_state(self, w: str | textual.widget.Widget, enable: typing.Any) -> bool:
        '''enable or disable a widget'''
        if isinstance(w, str):
            w = self[w]
        w.disabled = not enable
        return bool(enable)

    def sync_snaplist(self, force: bool = False) -> None:
        '''update snapshots in datatable'''
        table = self['#snap #table']
        if force:
            table.clear()
        old, new = [rkey.value for rkey in table.rows], list(self.brok.snaps)
        for pstr in [p for p in old if p not in new]:
            table.remove_row(pstr)
        for pstr in [p for p in new if p not in old]:
            snap = self.brok.snaps[pstr]
            size = size_format(snap.size)
            stype = self(snap.type)
            table.add_row(snap.path.name, stype, size, key=pstr)
        header = table.columns[table.name_col]
        if self.brok.snaps:
            header.label = f'{len(self.brok.snaps)}/{self.brok.bcnt} {self("snaps")}'
        else:
            header.label = ''

    def sync_process(self, auto: bool | None = None) -> None:
        '''update process name and button style after optional toggle'''
        if auto is not None:
            self.brok.toggle_mode(auto)
        if isinstance(self.brok.start, int) and self.brok.proc:
            t, b, p = self('manual'), 'success' if self.brok.start else 'warning', 'foreground'
        else:
            t, b, p = self('stop' if self.brok.start else 'start'), 'primary', 'warning'
        self['#toggle'].label = t
        self['#toggle'].variant = b
        self['#proc'].styles.color = self['#lproc'].styles.color = getattr(self.current_theme, p)

    def sync_buttons(self) -> None:
        '''update button states'''
        self.set_state('#browse-sdir', self.brok.sdir.is_dir())
        self.set_state('#browse-bdir', self.brok.bdir.is_dir())
        for widget in self['#list .btns'].children:
            self.set_state(widget, self.select[0])
        self['#restore'].variant = 'warning' if self.brok.start > 1 else 'default'

    def action_slog_height(self, val: int) -> None:
        if val > 0:
            h = max(min(self['#slog'].styles.height.value + 1, len(self['#slog'].lines)), 1)
            self['#logs Grid'].styles.height = self['#slog'].styles.height = h
            self['#logs Grid'].classes = 'expand'
            self['#logs'].refresh(layout=True)
        elif val < 0:
            h = max(self['#slog'].styles.height.value - 1, 1)
            self['#logs Grid'].styles.height = self['#slog'].styles.height = h
            self['#logs Grid'].classes = 'expand' if h > 1 else ''

    def action_collapse(self, w: str | textual.widget.Widget) -> None:
        if isinstance(w, str):
            w = self[w]
        if w.has_class('fold'):
            w.border_title = self(w.id)
            w.border_subtitle = ''
        elif w.id == 'path':
            w.border_title = f'SDIR:{self.brok.sdir}'
            w.border_subtitle = f'BDIR:{self.brok.bdir}'
        elif w.id == 'opts':
            w.border_title, w.border_subtitle = (
                f'LANG:{self.brok.lang}'
                f' WATCH:{self.brok.watch and self.brok.wintv or "OFF"}'
                f' SCHED:{self.brok.sched and self.brok.sintv or "OFF"}',
                f'BCNT:{self.brok.bcnt} PORT:{self.brok.port} PROC:{self.brok.proc}'
            )
        w.toggle_class('fold')

    def on_mount(self) -> None:
        self.sync_buttons()
        self.set_interval(1, self.sync_process)
        self.brok.on_snaps(self.sync_snaplist)
        self.slog(self('ready'))

    def on_unmount(self) -> None: self.brok.dump(CFGFILE)

    def on_key(self, evt: tte.Key):
        wid = getattr(self.focused, 'id', None)
        if wid == 'table':
            if evt.key == 'escape':
                fake = ttw.DataTable.RowSelected(None, 0, None)
                self.on_data_table_row_selected(fake)
            elif evt.key == 'delete':
                self.select[0] and self.on_delete()
            elif evt.key == 'ctrl+j':
                self.select[0] and self.on_restore()
            elif evt.key == 'f5':
                self.brok.on_snaps(True)
        elif wid == 'tree' and evt.key == 'escape':
            self.select[1] = ''
            self.focused.unselect()
        elif wid == 'proc' and evt.key == 'enter':
            self.sync_process(True)
        elif wid == 'slog' and evt.character in ['+', '=', '-', '_']:
            self.action_slog_height(1 if evt.character in '+=' else -1)
        elif wid and evt.key == 'escape':
            self.action_hide_help_panel()

    @textual.on(tte.Click, '#path, #opts, #pimg, #plus, #minus, #lproc')
    def on_click(self, evt: tte.Click) -> None:
        if self.screen != self.default_screen:
            return
        if evt.widget.id in ['path', 'opts'] and not evt.y:
            self.action_collapse(evt.widget)
        elif evt.widget.id == 'pimg':
            if evt.chain > 1 and evt.widget.data['image']:
                evt.widget.data['image'].show()
        elif evt.widget.id == 'minus':
            self.action_slog_height(-1)
        elif evt.widget.id == 'plus':
            self.action_slog_height(+1)
        elif evt.widget.id == 'lproc':
            self.sync_process(True)

    @textual.on(ttw.Input.Changed)
    @textual.on(ttw.Select.Changed)
    @textual.on(ttw.Switch.Changed)
    @textual.on(ttw.RadioButton.Changed)
    def on_config(self, evt: textual.message.Message) -> None:
        '''sync between UI and config data'''
        if self.screen != self.default_screen:
            return
        key = evt.control.id
        try:
            old, new = self.brok[key], evt.value
            if str(old) == str(new):
                return
            self.brok[key] = new
            val = self.brok[key]
            if key == 'lang':
                self.refresh(recompose=True)  # await self.wait_for_refresh()
                self.call_later(self.sync_snaplist)
                self.call_later(self.sync_process)
            elif key == 'proc':
                self.sync_process(True)
            elif key == 'watch':
                evt.control.label = 'ON' if val else 'OFF'
                self.set_state('#wintv', val)
            elif key == 'sched':
                evt.control.label = 'ON' if val else 'OFF'
                self.set_state('#sintv', val)
            self.slog(f'{self(key)} = {val}')
        except Exception as e:
            self.slog(e)
        finally:
            val = self.brok[key]
            if not isinstance(val, bool):
                val = str(val)
            evt.control.value = val

    # @textual.on(tte.Resize)
    def on_preview(self, e: tte.Resize | Image.Image | Path | bool = False) -> None:
        '''preview image of snapshot specified by GameSnapConfig.preview_filepath'''
        if not RichPixels:
            return
        pimg = self['#pimg']
        data = pimg.data
        if isinstance(e, Image.Image):                      # show screenshot
            if hasattr(e, 'filename'):
                return self.on_preview(Path(e.filename))
            if data['image'] == e and data['pixels']:
                return pimg.update(data['pixels'])
            data.update({'path': None, 'image': e})
        elif isinstance(e, _Path):                          # show thumbnail
            if data['path'] == e and data['pixels']:
                return pimg.update(data['pixels'])
            if e.suffix == '.zstd':
                with zstd.open(e, 'rb') as fin:
                    data.update({'path': e, 'image': Image.open(fin).copy()})
            else:
                data.update({'path': e, 'image': Image.open(e).copy()})
        elif not e or not data['image']:                    # hide preview
            data['path'] = data['image'] = data['pixels'] = None
            return pimg.update('')
        opts = self['#opts'].size.height * 2
        size = (max(1, self['#tree'].size.width), max(1, opts, self['#list'].size.height))
        if size != data['image'].size:                      # resize widget
            tmp = data['image'].copy()
            tmp.thumbnail(size, Image.Resampling.LANCZOS)
            data['pixels'] = RichPixels.from_image(tmp)
        elif not data['pixels']:
            data['pixels'] = RichPixels.from_image(data['image'])
        pimg.update(RichAlign.center(data['pixels']))

    @textual.work(thread=True)
    def on_service(self, wid: str, task: typing.Callable[..., Path], *a, **k) -> None:
        '''run the service in a separate thread'''
        self.set_state('#' + wid, False)
        ts = time.perf_counter()
        self['#slog'].add_class('hide')
        self['#pbar'].remove_class('hide')
        path = task(*a, **k, log=lambda p, c: self['#pbar'].update(progress=c))
        self['#pbar'].add_class('hide')
        self['#slog'].remove_class('hide')
        self.slog(f'{self(wid)}: {path} ({time.perf_counter() - ts:.2f}s)')
        self.set_state('#' + wid, True)
        self.sync_buttons()
        self.brok.on_snaps(True)

    @textual.work
    async def on_button_pressed(self, evt: ttw.Button.Pressed) -> None:
        if evt.button.id == 'toggle':
            self.sync_process(False)
        elif evt.button.id.startswith('browse-'):
            browse(self.brok[evt.button.id[7:]])
        elif evt.button.id.startswith('select-'):
            key = evt.button.id[7:]
            if path := await self.push_screen_wait(self.AskDirectory(self.brok[key])):
                self[f'#{key}'].value = str(path)
        elif evt.button.id == 'create':
            self.on_service(evt.button.id, self.brok.create)
        elif evt.button.id == 'restore':
            rpaths = [self.select[1]] if self.select[1] else []
            message = '\n- '.join(self.select[:1] + rpaths)
            if await self.push_screen_wait(self.AskYesNo(self('restore') + '?', message)):
                self.on_service(evt.button.id, self.brok.restore, Path(self.select[0]), rpaths)
        elif evt.button.id == 'export':
            record = Path(self.select[0])
            output = Path('~/Downloads') / record.with_suffix('.zip').name
            if path := await self.push_screen_wait(self.AskFilename(output)):
                self.on_service(evt.button.id, self.brok.export, record, path)
        elif evt.button.id == 'delete':
            if await self.push_screen_wait(self.AskYesNo(self('delete') + '?', self.select[0])):
                self.on_service(evt.button.id, self.brok.delete, Path(self.select[0]))

    def on_data_table_row_selected(self, evt: ttw.DataTable.RowSelected) -> None:
        path = getattr(evt.row_key, 'value', '')
        if self.select[0] != path:
            self.select[0] = path
        else:
            return
        tree = self['#list #tree']
        snap = self.brok.snaps.get(self.select[0])
        tree.clear()
        tree.root.collapse()
        self.sync_buttons()
        self.on_preview()
        if not snap:
            tree.root.label = self.select[1] = ''
            return
        dirs: dict[Path, type(tree.root)] = {Path('.'): tree.root}

        def add_node(r: Path):
            dirs[r] = dirs.get(r.parent, tree.root).add(r.name, data=(str(r), 0, 0))

        def add_leaf(r: Path):
            rel = str(r)
            path, size, mtime = snap.info[rel]
            leaf = dirs.get(r.parent, tree.root).add_leaf(tree.ICON_FILE + r.name, data=(
                rel, size_format(size), mtime and strftime(mtime) or ''
            ))
            if rel == self.select[1]:
                node = leaf
                while node := node.parent:
                    node.expand()
                tree.move_cursor(leaf)  # FIXME: node not selected and size & mtime not rendered
            with suppress(Exception):
                assert rel == self.brok.pimg and path.is_file()
                self.on_preview(path)

        snap.walk(on_dirs=add_node)
        snap.walk(on_file=add_leaf)
        tree.root.label = f'{len(snap.hash)} {self("files")}'
        self.select[1] = tree.cursor_node and tree.cursor_node.data[0]

    def on_tree_node_selected(self, evt: ttw.Tree.NodeSelected) -> None:
        self.select[1] = rel = evt.node.data[0]
        if not self.brok.pimg or self.select[0] not in self.brok.snaps:
            return
        info = self.brok.snaps[self.select[0]].info
        if rel not in info:  # dirs
            pimg = str(Path(rel) / self.brok.pimg)
            if pimg not in info:
                return
            rel = pimg
        if rel.endswith(self.brok.pimg) and info[rel][0].is_file():
            self.on_preview(info[rel][0])


class GameSnapGUI(ttk.Window, UIMixin):
    def __init__(self, *a, **k) -> None:
        bind = awareness() is not None
        super().__init__(title=' '.join(APPINFO), themename='yeti', iconphoto=ICOFILE, hdpi=False)
        if bind:
            self.set_wndproc()
        UIMixin.__init__(self, *a, **k)

        self.vars = {}
        for key in self.brok.aliases:
            val = self.brok[key]
            self.vars[key] = {
                bool: ttk.BooleanVar, int: ttk.IntVar
            }.get(type(val), ttk.StringVar)(self, name=key, value=val)
            self.vars[key].trace('w', self.on_config)

        self.protocol('WM_DELETE_WINDOW', self.on_destroy)
        self.init_widgets()
        self.on_render()

    def __getitem__(self, k: str) -> tk.Widget: return self.nametowidget(k)

    def init_path(self, P: int = 10) -> ttk.Frame:
        frame = ttk.Frame(self, name='path', padding=(P, P, P, P // 2))
        frame.columnconfigure(1, weight=1)
        for i, key in enumerate(['sdir', 'bdir']):
            def select(v=self.vars[key]):
                v.set(filedialog.askdirectory() or v.get())
            var = self.vars[key]
            for j, elem in enumerate([
                ttk.Label(frame, name=key),
                ttk.Entry(frame, textvariable=var),
                ttk.Button(frame, name=f'select#{key}', command=select, bootstyle='warning'),
                ttk.Button(frame, name=f'browse#{key}', command=lambda v=var: browse(v.get())),
            ]):
                elem.grid(row=i, column=j, sticky='ew', padx=P, pady=P)
        return frame

    def init_opts(self, P: int = 10) -> ttk.Frame:
        frame = ttk.Frame(self, name='opts', padding=(P, 0, 0, 0))
        frame.columnconfigure(0, weight=1)
        for i, elem in enumerate([
            ttk.Label(frame, name='lang'),
            ttk.OptionMenu(frame, self.vars['lang'], None, *self.langs, style='outline'),
            ttk.Label(frame, name='watch'),
            ttk.Checkbutton(frame, variable=self.vars['watch'], bootstyle='round-toggle'),
            ttk.Label(frame, name='wintv'),
            ttk.Spinbox(frame, textvariable=self.vars['wintv'], to=99999, width=5, name='wbox'),
            ttk.Label(frame, name='sched'),
            ttk.Checkbutton(frame, variable=self.vars['sched'], bootstyle='round-toggle'),
            ttk.Label(frame, name='sintv'),
            ttk.Spinbox(frame, textvariable=self.vars['sintv'], to=99999, width=5, name='sbox'),
            ttk.Label(frame, name='bcnt'),
            ttk.Spinbox(frame, textvariable=self.vars['bcnt'], to=999, width=5),
            ttk.Label(frame, name='port'),
            ttk.Spinbox(frame, textvariable=self.vars['port'], to=99999, width=5),
            ttk.Label(frame, name='knew'),
            ttk.Button(frame, name='!knew', command=lambda: self.on_record('knew')),
            ttk.Label(frame, name='ktog'),
            ttk.Button(frame, name='!ktog', command=lambda: self.on_record('ktog')),
            ttk.Label(frame, name='kdel'),
            ttk.Button(frame, name='!kdel', command=lambda: self.on_record('kdel')),
            ttk.Label(frame, name='succ'),
            ttk.Entry(frame, textvariable=self.vars['succ'], width=15),
            ttk.Label(frame, name='fail'),
            ttk.Entry(frame, textvariable=self.vars['fail'], width=15),
            ttk.Label(frame, name='proc'),
            ttk.Entry(frame, textvariable=self.vars['proc'], width=15, name='pname'),
            ttk.Label(frame, name='pimg'),
            ttk.Entry(frame, textvariable=self.vars['pimg'], width=15),
        ]):
            elem.grid(row=i // 2, column=i % 2, sticky='we'[i % 2], padx=P, pady=P // 2)
        for name in ['knew', 'ktog', 'kdel']:
            text = self.vars[name].get() or 'click to set'
            self[f'opts.{name}'].bind('<Button-1>', lambda e, n=name: self.on_record(n))
            self[f'opts.!{name}'].config(text=text, bootstyle='link')
        self['opts.pname'].bind('<Return>', lambda e: self.sync_process(True))
        self['opts.proc'].bind('<Button-1>', lambda e: self.sync_process(True))
        self.set_state('opts.wbox', self.vars['watch'].get())
        self.set_state('opts.sbox', self.vars['sched'].get())
        return frame

    def init_snap(self, P: int = 10) -> ttk.Frame:
        frame = ttk.Frame(self, name='snap', padding=(P, 0, 0, 0))
        tree = ttk.Treeview(frame, name='tree', columns=['type', 'size'], selectmode='browse')
        tree.column('#0', width=320)
        tree.heading('type', anchor='center')
        tree.column('type', width=100, anchor='center', stretch='no')
        tree.heading('size', anchor='e')
        tree.column('size', width=110, anchor='e', stretch='no')
        tree.bind('<<TreeviewSelect>>', self.on_select)
        tree.bind('<F5>', lambda e: self.brok.on_snaps(True))
        tree.bind('<Escape>', lambda e: e.widget.selection_set(()))
        tree.bind('<Delete>', lambda e: e.widget.selection() and self.on_delete())
        tree.bind('<Return>', lambda e: e.widget.selection() and self.on_restore())
        tree.pack(fill='both', expand=True, padx=P, pady=P)
        for elem in [
            ttk.Button(frame, name='create', command=self.on_create),
            ttk.Button(frame, name='!toggle', command=lambda: self.sync_process(False)),
        ]:
            elem.pack(side='left', fill='x', padx=P, pady=P // 2, expand=True)
        return frame

    def init_list(self, P: int = 10) -> ttk.Frame:
        frame = ttk.Frame(self, name='list', padding=(0, 0, P, 0))
        frame.rowconfigure(1, weight=1)
        pimg = ttk.Label(frame, name='!preview', anchor='center')
        pimg.data = {'path': None, 'image': None, 'tkimg': None}
        tree = ttk.Treeview(frame, name='tree', columns=['size', 'mtime'])
        tree.column('#0', width=260)
        tree.heading('size', anchor='e')
        tree.column('size', width=110, anchor='e', stretch='no')
        tree.heading('mtime', anchor='center')
        tree.column('mtime', width=230, anchor='center', stretch='no')
        tree.bind('<<TreeviewSelect>>', self.on_select)
        tree.bind('<Escape>', lambda e: e.widget.selection_set(()))
        tree.grid(row=1, column=0, columnspan=3, sticky='nsew', padx=P, pady=(P // 2, P))
        pimg.grid(row=0, column=0, columnspan=3, sticky='ew', padx=P, pady=(P // 2, P))
        pimg.bind('<Configure>', self.on_preview)
        pimg.bind('<Double-1>', lambda e: pimg.data['image'] and pimg.data['image'].show())
        pimg.grid_remove()
        for i, elem in enumerate([
            ttk.Button(frame, name='restore', command=self.on_restore),
            ttk.Button(frame, name='export', command=self.on_export),
            ttk.Button(frame, name='delete', command=self.on_delete),
        ]):
            frame.columnconfigure(i, weight=1)
            elem.grid(row=2, column=i, sticky='ew', padx=P, pady=P // 2)
        self.sync_buttons()
        return frame

    def init_logs(self, P: int = 10) -> ttk.Frame:
        frame = ttk.Frame(self, name='logs', padding=(P, P // 2, P, P))
        frame.columnconfigure(0, weight=1)
        self.stat = lstat = ttk.Label(frame, name='!stat')
        self.logs = slogs = ScrolledText(frame, padding=P, height=5, autohide=True)
        blogs = ttk.Button(frame, text='<', bootstyle='link', command=self.on_collapse(slogs))
        lstat.grid(row=0, column=0, sticky='ew', padx=P)
        blogs.grid(row=0, column=1, sticky='e', padx=P)
        slogs.grid(row=1, column=0, columnspan=2, sticky='nsew')
        slogs.grid_remove()
        self.set_state(slogs._text, False)
        return frame

    def init_widgets(self) -> None:
        self.rowconfigure(2, weight=1)
        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=5)
        self.init_path().grid(row=0, column=0, sticky='ew', columnspan=2)
        self.init_opts().grid(row=1, column=0, sticky='ew')
        self.init_snap().grid(row=2, column=0, sticky='nsew')
        self.init_list().grid(row=1, column=1, sticky='nsew', rowspan=2)
        self.init_logs().grid(row=3, column=0, sticky='ew', columnspan=2)
        self.sync_language()
        threading.excepthook = self.report_callback_exception

    def log(
        self, msg: typing.Any = '', style: str = 'info',
        timeout: int = 8000, history: bool = False
    ) -> None:
        '''display message on status label for specified timeout'''
        if msg:
            self.stat.config(bootstyle=style, text=str(msg))
        if history and msg:
            self.set_state(self.logs._text, True)
            self.logs.insert('end', f'[{strftime()}] {msg}\n')
            self.logs.see('end')
            self.set_state(self.logs._text, False)
        if timeout:
            if hasattr(self, 'logid'):
                self.after_cancel(self.logid)
            self.logid = self.after(timeout, self.log, self('ready'), 'primary', 0)

    def report_callback_exception(self, *args) -> None:
        '''override to log exceptions'''
        if isinstance(args[0], threading.ExceptHookArgs):
            msg = f'{self("error")}: {args[0].exc_value}'
            if args[0].thread:
                msg += f' from {args[0].thread.name}'
            logger.exception(msg)
        else:
            msg = f'{self("error")}: {args[1]}'
            super().report_callback_exception(*args)
        self.log(msg, 'danger', history=True)

    def set_state(self, w: str | tk.Widget, enable: typing.Any) -> bool:
        '''enable or disable a widget'''
        if isinstance(w, str):
            w = self[w]
        w.config(state='normal' if enable else 'disabled')
        return bool(enable)

    def set_wndproc(self) -> None:
        '''set window procedure to handle WM_DPICHANGED event'''
        if hasattr(self, '_old_scale'):
            return
        user32, wtypes = ctypes.windll.user32, ctypes.wintypes
        WNDARGS = [wtypes.HWND, wtypes.UINT, wtypes.WPARAM, wtypes.LPARAM]
        user32.SetWindowLongPtrW.argtypes = [wtypes.HWND, ctypes.c_int, ctypes.c_void_p]
        user32.SetWindowLongPtrW.restype = ctypes.c_void_p
        user32.CallWindowProcW.argtypes = [ctypes.c_void_p, *WNDARGS]
        user32.CallWindowProcW.restype = wtypes.LPARAM

        @ctypes.WINFUNCTYPE(wtypes.LPARAM, *WNDARGS)
        def callback(hwnd, msg, wparam, lparam) -> wtypes.LPARAM:
            if msg == 0x02E0 or msg == 0x02E3:  # WM_DPICHANGED[_AFTERPARENT]
                def wrapper(dpi: int) -> None:
                    old, new = self._old_scale, (dpi / 96) or self.winfo_scale()
                    with suppress(Exception):
                        if old != new:
                            self.on_dpichange(old, new)
                    self._old_scale = new
                self.after(0, wrapper, ((wparam >> 16) & 0xFFFF + (wparam & 0xFFFF)) // 2)
            return user32.CallWindowProcW(self._old_wndproc, hwnd, msg, wparam, lparam)
        self._old_scale = self.winfo_scale()
        self._old_wndproc = user32.SetWindowLongPtrW(self._root().winfo_id(), -4, callback)
        self._new_wndproc = callback            # save to avoid gc

    def winfo_scale(self) -> float:
        '''get window DPI scale factor (1 means 96DPI => 100%)'''
        wtypes = ctypes.wintypes
        hwnd = wtypes.HWND(self._root().winfo_id())
        with suppress(Exception):
            if swnd := ctypes.windll.user32.GetDpiForWindow(hwnd) / 96:
                return swnd
        with suppress(Exception):
            x, y = wtypes.UINT(), wtypes.UINT()
            mtype = 0                   # MDT_EFFECTIVE_DPI
            flags = wtypes.DWORD(2)     # MONITOR_DEFAULTTONEAREST
            if smon := 0 if ctypes.windll.shcore.GetDpiForMonitor(
                ctypes.windll.user32.MonitorFromWindow(hwnd, flags),
                mtype, ctypes.pointer(x), ctypes.pointer(y)
            ) else ((x.value + y.value) / 2 / 96):
                return smon
        with suppress(Exception):
            if sdev := ctypes.windll.shcore.GetScaleFactorForDevice(0) / 100:
                return sdev
        return 1

    def sync_language(self, widget: typing.Any = None) -> None:
        '''update text of all widgets in root'''
        for name, child in (widget or self).children.items():
            if not name.startswith('!'):
                if child.winfo_class() == 'Treeview':
                    for column in child['columns']:
                        child.heading(column, text=self(column))
                elif child.winfo_class() in ['TLabel', 'TButton']:
                    child.config(text=self(name.split('#')[0]))
            self.sync_language(child)

    def sync_snaplist(self, force: bool = False) -> None:
        '''update snapshots in treeview'''
        tree = self['snap.tree']
        if force:
            tree._last_selection = None
            tree.delete(*tree.get_children())
        old, new = tree.get_children(), list(self.brok.snaps)
        tree.delete(*(p for p in old if p not in new))
        for i, pstr in enumerate([p for p in new if p not in old]):
            snap = self.brok.snaps[pstr]
            size = size_format(snap.size)
            stype = self(snap.type)
            tree.insert('', i, pstr, text=snap.path.name, values=(stype, size))
        if self.brok.snaps:
            tree.heading('#0', text=f'{len(self.brok.snaps)}/{self.brok.bcnt} {self("snaps")}')
        else:
            tree.heading('#0', text='')

    def sync_process(self, auto: bool | None = None) -> None:
        '''update process name and button style after optional toggle'''
        if auto is not None:
            self.brok.toggle_mode(auto)
        if isinstance(self.brok.start, int) and self.brok.proc:
            t, b, p = self('manual'), 'success' if self.brok.start else 'warning', 'default'
        else:
            t, b, p = self('stop' if self.brok.start else 'start'), 'primary', 'warning'
        self['snap.!toggle'].config(text=t, bootstyle=b)
        self['opts.pname'].config(bootstyle=p)
        self['opts.proc'].config(bootstyle=p)

    def sync_buttons(self) -> None:
        '''update button states'''
        self.set_state('path.browse#sdir', self.brok.sdir.is_dir())
        self.set_state('path.browse#bdir', self.brok.bdir.is_dir())
        snap = self.brok.snaps.get((self['snap.tree'].selection() or [None])[0])
        for wid in ['restore', 'export', 'delete']:
            self.set_state(f'list.{wid}', snap)
        self['list.restore'].config(bootstyle='warning' if self.brok.start > 1 else 'default')

    def on_dpichange(self, old: float, new: float) -> None:
        for f in map(font.nametofont, font.names(self)):
            f.config(size=int(f.actual()['size'] / old * new))
        self.on_preview(True)

    def on_destroy(self) -> None:
        '''save config and exit'''
        self.brok.dump(CFGFILE)
        self.destroy()

    def on_collapse(self, e: tk.Widget, vertical: bool = True) -> typing.Callable[[], None]:
        '''collapse the widget vertically or horizontally'''
        def wrapper() -> None:
            v = -1 if e.winfo_viewable() else 1
            e.grid_remove() if v == -1 else e.grid()
            W, H = self.winfo_width(), self.winfo_height()
            w, h = e.winfo_reqwidth(), e.winfo_reqheight()
            self.geometry(f'{W}x{H + v * h}' if vertical else f'{W + v * w}x{H}')
        return wrapper

    def on_render(self) -> None:
        '''move window to screen center'''
        def interval() -> None:
            self.after(1000, interval)
            self.sync_process()
        interval()
        self.update_idletasks()
        self.place_window_center()
        self.minsize(self.winfo_width(), self.winfo_height())
        self.brok.on_snaps(self.sync_snaplist)
        self.log(timeout=1)

    def on_record(self, name: str) -> None:
        '''record hotkey and update config'''
        btn = self[f'opts.!{name}']
        if btn.instate(['disabled']):
            return

        def wrapper() -> None:
            self.set_state(btn, False)
            btn.config(text='esc to cancel')
            self.vars[name].set(self.brok.record_hotkey(name, log=lambda t: btn.config(text=t)))
            btn.config(text=self.vars[name].get() or 'click to set')
            self.set_state(btn, True)
        threading.Thread(target=wrapper, name='record-hotkey', daemon=True).start()

    def on_config(self, key: str, *_) -> None:
        '''sync between UI and config data'''
        try:
            old, new = self.brok[key], self.vars[key].get()
            if str(old) == str(new):
                return
            self.brok[key] = new
            val = self.brok[key]  # value maybe formatted
            if key == 'lang':
                self.sync_language()
                self.sync_snaplist()
                self.sync_process()
                self.log(timeout=1)
            elif key == 'proc':
                self.sync_process(True)
            elif key == 'watch':
                self.set_state('opts.wbox', val)
            elif key == 'sched':
                self.set_state('opts.sbox', val)
            elif key in ['sdir', 'bdir']:
                self.sync_buttons()
            self.log(f'{self(key)} = {val}', history=True)
        except Exception as e:
            self.log(e, 'danger', history=True)
        finally:
            self.after(50, lambda: self.vars[key].set(self.brok[key]))  # recursive

    def on_select(self, e: tk.Event) -> None:
        '''handle treeview select event'''
        if e.widget.selection() == getattr(e.widget, '_last_selection', None):
            return
        selection = e.widget._last_selection = e.widget.selection()
        if e.widget is self['snap.tree']:
            tree = self['list.tree']
            item = set(tree.selection())
            snap = self.brok.snaps.get((selection or [''])[0])
            tree.delete(*tree.get_children())
            self.on_preview(getattr(snap, 'view', None))
            self.sync_buttons()
            if not snap:
                return tree.heading('#0', text='')
            dirs: dict[Path, str] = {Path('.'): ''}

            def add_node(r: Path):
                dirs[r] = tree.insert(dirs.get(r.parent, ''), 'end', r, text=r.name)

            def add_leaf(r: Path):
                rel = str(r)
                path, size, mtime = snap.info[rel]
                tree.insert(dirs.get(r.parent, ''), 'end', r, text=r.name, values=(
                    size_format(size), mtime and strftime(mtime) or ''
                ))
                with suppress(Exception):
                    assert rel == self.brok.pimg and path.is_file()
                    self.on_preview(path)

            snap.walk(on_dirs=add_node)
            snap.walk(on_file=add_leaf)
            tree.heading('#0', text=f'{len(snap.hash)} {self("files")}')
            tree.selection_set(tuple(item.intersection(tree.get_children())))
        elif e.widget is self['list.tree'] and selection and self.brok.pimg:
            info = self.brok.snaps[self['snap.tree'].selection()[0]].info
            for rel in selection:
                if rel not in info:  # dirs
                    pimg = str(Path(rel) / self.brok.pimg)
                    if pimg not in info:
                        continue
                    rel = pimg
                if rel.endswith(self.brok.pimg) and info[rel][0].is_file():
                    self.on_preview(info[rel][0])
                    break

    def on_preview(self, e: tk.Event | Image.Image | Path | bool = False) -> None:
        '''preview image of snapshot specified by GameSnapConfig.preview_filepath'''
        pimg = self['list.!preview']
        data = pimg.data
        last = data['image']
        if isinstance(e, Image.Image):                      # show screenshot
            if hasattr(e, 'filename'):
                return self.on_preview(Path(e.filename))
            data.update({'path': None, 'image': e})
            if not last:
                return pimg.grid()
        elif isinstance(e, _Path):                          # show thumbnail
            if data['path'] == e:
                return
            elif e.suffix == '.zstd':
                with zstd.open(e, 'rb') as fin:
                    data.update({'path': e, 'image': Image.open(fin).copy()})
            else:
                data.update({'path': e, 'image': Image.open(e).copy()})
            if not last:
                return pimg.grid()
        elif not e or not data['image']:                    # hide preview
            data['path'] = data['image'] = data['tkimg'] = None
            return pimg.grid_remove()
        size = (max(1, self['list.tree'].winfo_width() - 4), max(1, self['opts'].winfo_height()))
        if size != data['image'].size:                      # resize widget
            tmp = data['image'].copy()
            tmp.thumbnail(size, Image.Resampling.LANCZOS)
            data['tkimg'] = ImageTk.PhotoImage(tmp)         # save to avoid gc
        elif not data['tkimg']:
            data['tkimg'] = ImageTk.PhotoImage(data['image'])
        pimg.config(image=data['tkimg'])

    def on_service(self, widget: str, task: typing.Callable[..., Path], *a, **k) -> None:
        '''run the service in a separate thread'''
        def wrapper() -> None:
            self.set_state(widget, False)
            ts = time.perf_counter()
            path = task(*a, **k, log=lambda p, c: self.log(f'{c:7.2%} {p}', timeout=0))
            dt = time.perf_counter() - ts
            self.log(f'{self(widget.split(".")[-1])}: {path} ({dt:.2f}s)', history=True)
            self.set_state(widget, True)
            self.sync_buttons()
            self.brok.on_snaps(True)
        threading.Thread(target=wrapper, name=widget, daemon=True).start()

    def on_create(self) -> None:
        '''create a manual snapshot'''
        self.on_service('snap.create', self.brok.create)

    def on_restore(self) -> None:
        '''restore the selected snapshot'''
        if self.brok.start > 1:
            self.log('Do NOT restore snapshots when game is running!', 'warning')
        record = Path(self['snap.tree'].selection()[0])
        rpaths = self['list.tree'].selection()
        message = '\n- '.join([str(record), *rpaths[:10]])
        if len(rpaths) > 10:
            message += f'\nand {len(rpaths) - 10} more paths'
        if messagebox.askyesno(self('restore') + '?', message, icon='warning'):
            self.on_service('list.restore', self.brok.restore, record, rpaths)

    def on_export(self) -> None:
        '''export the selected snapshot'''
        record = Path(self['snap.tree'].selection()[0])
        if path := filedialog.asksaveasfilename(
            defaultextension='.zip', initialdir='~/Downloads', initialfile=record.stem
        ):
            self.on_service('list.export', self.brok.export, record, path)

    def on_delete(self) -> None:
        '''delete the selected snapshot'''
        record = Path(self['snap.tree'].selection()[0])
        if messagebox.askyesno(self('delete') + '?', record, icon='warning'):
            self.on_service('list.delete', self.brok.delete, record)


def modman(args: argparse.Namespace) -> None:
    '''download and sync steam mods / workshops'''
    if not args.mdir:
        args.mdir = args.cdir
    if not args.wid and not args.mdir.is_dir():
        return logger.error(f'Invalid directory: {args.mdir}')
    src = args.mdir / 'steamapps' / 'workshop' / 'content'
    gids = [int(x.name) for x in src.iterdir() if x.is_dir() and x.name.isdigit()]
    if args.sync:
        gid = args.gid or (gids[0] if len(gids) == 1 else None)
        if not gids:
            return logger.error(f'No content found under {args.mdir}')
        elif gid not in gids:
            if args.gid:
                return logger.error(f'Game ID {args.gid} not found')
            return logger.warning(f'Choose game ID from {gids}')
        mods = [x for x in src.glob(f'{gid}/*/mods/*') if x.is_dir()]
        with poolmap(len(mods)) as pmap:
            for dst in pmap(lambda mod: shutil.copytree(
                mod, args.sync / mod.name, dirs_exist_ok=True
            ), mods):
                logger.info(dst)
        return
    if not args.wid:
        for gid in gids:
            mods, wcnt = [], 0
            for wid in src.glob(f'{gid}/*'):
                if not wid.is_dir() or not wid.name.isdigit():
                    continue
                for mod in (wid / 'mods').iterdir():
                    if mod.is_dir():
                        mods.append((wid.name, mod.name, mod.stat().st_mtime))
                wcnt += 1
            rprint(f'Game {gid} with {wcnt} workshops and {len(mods)} mods')
            for wid, mod, mtime in mods:
                rprint(f'   + [{wid}] ({strftime(mtime)}) {mod}')
        if not gids:
            logger.warning(f'No content found under {args.mdir}')
        return
    cmd = (args.cdir / 'steamcmd').with_suffix('.exe' if WINDOWS else '')
    if not cmd.is_file() or 'version' not in exec_result(cmd, '--help').lower():
        if not WINDOWS or (not args.yes and input(
            f'SteamCMD not found: install to {args.cdir}?\n'
            'About 700KB to download and 100MB after installed\ny/[N]'
        ).lower() != 'y'):
            return logger.error(f'Invalid SteamCMD: {cmd}')
        args.cdir.mkdirp()
        try:
            urllib.request.urlretrieve(
                'https://steamcdn-a.akamaihd.net/client/installer/steamcmd.zip',
                args.cdir / 'steamcmd.zip'
            )
            with zipfile.ZipFile(args.cdir / 'steamcmd.zip', 'r') as zf:
                zf.extractall(args.cdir)
                zf.printdir()
            logger.warning('It may takes very long time to setup SteamCMD...')
            with tailf(args.cdir / 'logs' / 'bootstrap_log.txt'):
                exec_result(cmd, '--help')
        except Exception:
            return logger.exception('Install SteamCMD failed')
    logger.info(f'Using {cmd}')
    if not args.gid:
        try:
            URL = 'https://steamworkshopdownloader.io/api/details/file'
            with urllib.request.urlopen(URL, f'[{args.wid}]'.encode(), timeout=10) as rep:
                info = json.loads(rep.read().decode('utf-8'))[0]
            args.gid = info['consumer_appid']
            args.wid = info['publishedfileid']
            logger.info(f'Found `{info["title"]}` for game `{info["app_name"]}`')
        except Exception:
            return logger.exception(f'Query game ID for {args.wid} failed')
    output = exec_result(
        cmd, f'+force_install_dir "{args.mdir}"', '+login anonymous',
        f'+workshop_download_item {args.gid} {args.wid}', '+quit'
    )
    for line in output.splitlines():
        if 'success' in line.lower():
            return line
    return output


def test(brok: GameSnapBroker) -> None:
    '''self-test and report errors'''
    with tempfile.TemporaryDirectory() as folder:
        base = Path(folder)
        try:
            sdir = base / 'src'
            bdir = base / 'dst'
            hdir = bdir / 'blob'
            text = sdir / 'text'
            cfg = base / 'config.json'
            out = base / 'export.zip'
            logger.info('TEST START'.center(20, '-'))
            brok.update(cfg)
            assert cfg.is_file(), 'config file is not created'
            brok['knew'] = brok['ktog'] = brok['kdel'] = ''  # disable hotkey
            logger.info('set sdir'.center(20, '-'))
            with suppress(ValueError):
                brok['sdir'] = sdir
                assert False, 'source directory must exist'
            sdir.mkdir()
            brok['sdir'] = sdir
            logger.info('set bdir'.center(20, '-'))
            with suppress(ValueError):
                brok['bdir'] = brok.sdir / 'nested'
                assert False, 'nested directory is not supported'
            brok['bdir'] = bdir
            logger.info('create'.center(20, '-'))
            brok.create()
            assert len(brok.summary()) == 1, 'no snapshot created'
            text.write_text('foo')
            brok.create()
            assert len(brok.summary()) == 2, 'no snapshot created'
            assert len(list(hdir.iterdir())) == 1, 'blob files not created'
            brok['wintv'], brok['sintv'] = 1, 1
            brok['watch'], brok['sched'] = True, False
            brok.toggle_mode(start=True)
            logger.info('watchdog'.center(20, '-'))
            text.write_text('bar')
            time.sleep(1.5)
            assert len(brok.summary()) > 2, 'new snapshot not created'
            brok['watch'], brok['sched'] = False, True
            logger.info('schedule'.center(20, '-'))
            time.sleep(1.5)
            assert len(brok.summary()) > 3, 'new snapshot not created'
            brok.toggle_mode(start=False)
            snaps = brok.summary()
            logger.info('summary'.center(20, '-'))
            assert id(brok.summary()) == id(brok.summary()), 'cache not working'
            logger.info('restore'.center(20, '-'))
            brok.restore(snaps[-2].path)
            assert text.read_text() == 'foo', 'snapshot not restored'
            logger.info('export'.center(20, '-'))
            brok.export(snaps[0].path, out.with_suffix('.tgz'))
            assert out.is_file() and out.stat().st_size, 'zip file not exported'
            logger.info('delete'.center(20, '-'))
            brok.delete_latest()
            brok.delete_oldest()
            assert len(brok.summary()) > 1, 'latest snapshot not deleted'
            logger.info('max count'.center(20, '-'))
            brok['bcnt'] = 1
            brok.create()
            assert len(brok.summary()) == 1, 'snapshots not auto cleaned'
            logger.info('file missing'.center(20, '-'))
            for x in hdir.iterdir():
                x.unlink()
            os.utime(brok.summary()[0].path)  # force cache miss
            path, size, mtime = list(brok.summary()[0].info.values())[0]
            assert not any([path.exists(), size, mtime]), 'blob files missing'
            logger.info('ALL DONE'.center(20, '-'))
        except Exception:
            logger.exception('\n'.join(
                f'{path.relative_to(base)} [{size_format(path.stat().st_size)}]'
                for path in sorted(base.rglob('*'))
            ))


def main() -> int:
    def filepath(s: str) -> Path:
        path = abspath(s)
        if not path.is_file():
            raise argparse.ArgumentTypeError(f'{s} is not a file')
        return path

    class ArgumentParser(argparse.ArgumentParser):
        def __init__(self, *a, **k) -> None:
            if RichHelpFormatter:
                k.setdefault('formatter_class', RichHelpFormatter)
            self.parents = k.get('parents', [])
            super().__init__(*a, **k)

        def add_subparser(self, name: str, **k) -> typing.Self:
            if not hasattr(self, 'subparsers'):
                self.subparsers = self.add_subparsers(title='Supported commands', metavar='')
            k.setdefault('description', k.get('help'))
            k.setdefault('parents', self.parents)
            return self.subparsers.add_parser(name, **k)

        def print_help(self, *a, **k) -> None:
            console(True)
            super().print_help(*a, **k)

        def error(self, *a, **k) -> None:
            console(True)
            super().error(*a, **k)

    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument('-V', '--version', action='version', version=' '.join(APPINFO[1:]))
    shared.add_argument('-v', '--verbose', action='count', help='enable verbose logging')
    shared.add_argument('-s', '--nocon', action='store_true', help='disable concurrency')
    shared.add_argument('--cfg', type=abspath, default=CFGFILE, help='load config from file')
    shared.add_argument('--use', nargs=2, metavar=('PATH', 'NAME'), help='use NAME from presets')

    epilog = 'See <command> -h for more helps'
    parser = ArgumentParser(prog=PROJECT, parents=[shared], description=DESCSTR, epilog=epilog)

    sparser = parser.add_subparser('create', help='create a new snapshot')
    sparser.set_defaults(func=lambda a, b: f'Created at {b.create()}')

    sparser = parser.add_subparser('restore', help='restore a snapshot')
    sparser.add_argument('record', type=filepath, help='record path')
    sparser.add_argument('rpaths', nargs='*', help='filter files by relative path')
    sparser.add_argument('--sdir', help='source directory [default: sdir of record]')
    sparser.set_defaults(func=lambda a, b: f'Restored {b.restore(a.record, a.rpaths, a.sdir)}')

    sparser = parser.add_subparser('export', help='export a snapshot')
    sparser.add_argument('record', type=filepath, help='record path')
    sparser.add_argument('output', type=abspath, help='output filename')
    sparser.set_defaults(func=lambda a, b: f'Exported to {b.export(a.record, a.output)}')

    def do_delete(args, brok):
        rst = brok.delete_all() if args.all else []
        rst += [brok.delete(path) for path in args.record]
        rst += [brok.delete_latest() for _ in range(args.latest)]
        rst += [brok.delete_oldest() for _ in range(args.oldest)]
        return '\n - '.join(['Deleted', *map(str, rst)]) if rst else 'No snapshots to delete'
    sparser = parser.add_subparser('delete', help='delete snapshots')
    group = sparser.add_mutually_exclusive_group(required=True)
    group.add_argument('--record', metavar='PATH', nargs='*', default=[], type=filepath)
    group.add_argument('--latest', metavar='N', nargs='?', const=1, default=0, type=int)
    group.add_argument('--oldest', metavar='N', nargs='?', const=1, default=0, type=int)
    group.add_argument('--all', action='store_true')
    sparser.set_defaults(func=do_delete)

    def do_summary(args, brok):
        snaps = brok.summary(args.detail)
        for snap in snaps:
            tstr, size = strftime(snap.time), size_format(snap.size)
            rprint(tstr, f'{snap.type:6s}', len(snap.hash), 'files', size if args.detail else '')
            rprint(f' - source: {snap.sdir}\n - hashes: {snap.hdir}\n - record: {snap.path}')
        if not snaps:
            return f'No snapshots under {brok.bdir}'
        msg = f'Found {len(snaps)} of max {brok.bcnt} snapshots'
        if args.detail:
            msg += f' total {size_format(brok.total_size(snaps))}'
        return msg
    sparser = parser.add_subparser('summary', help='list snapshots')
    sparser.add_argument('--detail', action='store_true', help='show size info')
    sparser.set_defaults(func=do_summary)

    cmd = abspath(shutil.which('steamcmd') or '~/.steamcmd/steamcmd')
    sparser = parser.add_subparser('modman', help=modman.__doc__)
    group = sparser.add_mutually_exclusive_group()
    group.add_argument(
        'wid', nargs='?', metavar='WID', type=int, help='mod / workshop ID to download')
    group.add_argument('--sync', metavar='DIR', type=abspath, help='sync from mdir to here')
    sparser.add_argument(
        '--cdir', metavar='DIR', type=abspath, default=cmd.parent, help='path to SteamCMD')
    sparser.add_argument(
        '--mdir', metavar='DIR', type=abspath, help='path to download mods [default: cdir]')
    sparser.add_argument('--gid', type=int, help='specify game ID')
    sparser.add_argument('--yes', action='store_true', help='install SteamCMD if not found')
    sparser.set_defaults(func=lambda a, b: modman(a))

    sparser = parser.add_subparser('test', help=test.__doc__)
    sparser.set_defaults(task=lambda a, b: test(b))

    sparser = parser.add_subparser('tui', help='run terminal user interface')
    sparser.add_argument('--css', type=filepath, nargs='+', help='load .tcss for debugging')
    sparser.set_defaults(task=lambda a, b: GameSnapTUI(css=a.css, brok=b).run())

    def default(args: argparse.Namespace, brok: GameSnapBroker) -> None:
        try:
            GameSnapGUI(brok).mainloop()
        except Exception as e:
            console(True)
            raise e
    sparser = parser.add_subparser('gui', help='run graphical user interface (default)')
    parser.set_defaults(task=default, func=None)

    args = parser.parse_args()
    console(args.func or args.task is not default, ' '.join(APPINFO[:2 if not args.func else 0]))
    if not args.cfg.is_file() and args.cfg != CFGFILE:
        logger.warning(f'{args.cfg} does not exist: fallback to {CFGFILE}')
        args.cfg = CFGFILE
    if args.use:
        args.use = [filepath(args.use[0]), args.use[1]]
    if args.nocon:
        global POOLMAP
        POOLMAP = False
    with suppress(KeyboardInterrupt):
        brok = GameSnapBroker(args.cfg, verbose=args.verbose, srv=not args.func)
        if args.use:
            brok.preset(args.use[0], args.use[1])
        if args.func:
            ts = time.perf_counter()
            if ret := args.func(args, brok):
                rprint(ret)
                rprint(f'Used time: {time.perf_counter() - ts:.3f}s')
        else:
            args.task(args, brok)


__all__ = [
    # classes
    'Path', 'PZipFile', 'GameSnapConfig', 'GameSnapRecord', 'GameSnapEngine',
    'GameSnapRouter', 'GameSnapBroker', 'GameSnapTUI', 'GameSnapGUI',
    # contants
    'BASEDIR', 'DATADIR', 'CFGFILE', 'POOLMAP', 'APPINFO',
    # functions
    'jload', 'jdump', 'tailf', 'pidof', 'where', 'browse', 'console', 'poolmap',
    'capture', 'awareness', 'play_sound', 'size_format', 'modman', 'test', 'main',
]


def __dir__() -> tuple[str]: return sorted(__all__)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception:
        logger.exception('')
        sys.exit(1)
