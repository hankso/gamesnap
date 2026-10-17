#!/usr/bin/env python3
# coding=utf-8
#
# File: GSnap.py
# Author: Hankso
# Webpage: https://github.com/hankso
# Time: 2026/4/1 0:16:44
#
# Versions:
#   v0.2.2: add support to switch between configs
#   v0.2.1: add filename filter like --includes *.py;*.md or --excludes *.bin
#   v0.2.0: SnapRecord: storage tree instead of paths to save space
#   v0.1.4: add support to self-update from url or github repo
#   v0.1.3: add modman and daemon mode and tested on linux
#   v0.1.2: add Terminal UI (textual) and pyproject.toml
#   v0.1.1: add Graphical UI (tkinter) and build with nuitka
#   v0.1.0: add snapshot engine and CLI by argparse
#
# Notes for packaging:
#   On Linux:           apt install gcc upx ccache patchelf python3-tk (or upx-ucl)
#   On Windows:         choco install upx
#   For GUI mode:       nuitka GSnap.py
#   For CUI on cmd:     set SUBSYSTEM=CUI && nuitka GSnap.py
#   For CUI on pwsh:    $env:SUBSYSTEM = "CUI"; & nuitka GSnap.py
#   Use uv dep-group:   uv run --no-group dev --group build nuitka GSnap.py
#   Changed in v0.1.3:  use `poe` to run commands defined in pyproject.toml
#
# nuitka-project: --mode=onefile
# nuitka-project: --remove-output
# nuitka-project: --output-dir=build
# nuitka-project: --output-filename=GameSnap
# nuitka-project: --output-folder-name=GameSnap
# nuitka-project: --report=build/report.xml
# nuitka-project: --python-flag=-u
#
# nuitka-project: --enable-plugin=tk-inter
# nuitka-project: --include-package=rich._unicode_data
# nuitka-project: --include-package-data=ttkbootstrap.assets
# nuitka-project: --include-data-files=pyproject.toml=./
# nuitka-project: --include-data-files=icon.png=./
#
# nuitka-project-if: {OS} == 'Darwin':
#    nuitka-project: --mode=app
#    nuitka-project: --macos-app-icon=icon.png
# nuitka-project-if: {OS} != 'Darwin' and __import__('shutil').which('upx') is not None:
#    nuitka-project: --enable-plugin=upx
#    nuitka-project: --onefile-no-compression
# nuitka-project-if: {OS} == 'Windows' and os.getenv('SUBSYSTEM', 'CUI') == 'CUI':
#    nuitka-project: --windows-console-mode=force
#    nuitka-project: --windows-icon-from-ico=icon.png
# nuitka-project-if: {OS} == 'Windows' and os.getenv('SUBSYSTEM', 'CUI') == 'GUI':
#    nuitka-project: --windows-console-mode=attach
#    nuitka-project: --windows-icon-from-ico=icon.png
#    nuitka-project: --output-filename=GameSnapGUI
#    nuitka-project: --output-folder-name=GameSnapGUI

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
import signal
import shutil
import struct
import typing
import fnmatch
import hashlib
import pathlib
import inspect
import logging
import tomllib
import zipfile
import argparse
import platform
import tempfile
import functools
import itertools
import threading
import subprocess
import http.server
import dataclasses
import configparser
import tkinter as tk
import urllib.request
from io import BufferedWriter
from tkinter import font, filedialog, simpledialog, messagebox
from datetime import datetime
from contextlib import suppress, contextmanager
from collections import deque
from concurrent.futures import ThreadPoolExecutor

# required: psutil keyboard textual watchdog ttkbootstrap
import psutil
import keyboard
import textual.app
import watchdog.observers
import ttkbootstrap as ttk
import textual.events as tte
import textual.widgets as ttw
import textual.containers as ttc
from PIL import Image, ImageTk, ImageDraw, ImageFont, ImageGrab
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
    import winsound                 # windows only
except Exception:
    winsound = None
try:
    import winreg                   # windows only
except Exception:
    winreg = None
try:
    import pty                      # unix only
except Exception:
    pty = None


class Path(pathlib.Path):
    '''Wrapping on pathlib.Path to ensure posix flavour __str__'''
    def __str__(self) -> str:
        return super().__str__().replace(self._flavour.sep, '/')

    def mkdirp(self, mode=0o777) -> typing.Self:
        return self.mkdir(mode, parents=True, exist_ok=True) or self

    def touchp(self, mode=0o666) -> typing.Self:
        return self.parent.mkdirp().touch(mode, exist_ok=True) or self


def abspath(path: str | Path) -> Path:
    '''Expand environ vars and user home directory and resolve the path'''
    return Path(os.path.expandvars(str(path).strip('\'" \n\r\t'))).expanduser().resolve()


try:
    compiled = globals()['__compiled__']
    ONEFILE = compiled.onefile
    BASEDIR = abspath(compiled.containing_dir)  # onefile parent
    DATADIR = abspath(__file__).parent          # inside onefile
except Exception:
    ONEFILE = False
    BASEDIR = DATADIR = abspath(__file__).parent
ICOFILE = DATADIR / 'icon.png'
CFGFILE = BASEDIR / 'config.json'
WINDOWS = platform.system() == 'Windows'
POOLMAP = True
try:
    with open(DATADIR / 'pyproject.toml', 'rb') as f:
        data = tomllib.load(f)['project']
    PROJECT = data['name']
    VERSION = data['version']
    AUTHORS = ', '.join(
        f'{author.get("name", "")} <{author.get("email", "")}>'
        for author in data['authors']
    )
    DESCSTR = data.get('description', '')
    REPOURL = data.get('urls', {}).get('repository', '')
except Exception:
    PROJECT, VERSION, AUTHORS = Path(__file__).stem, '0.0.0', 'UNKNOWN'
    DESCSTR = REPOURL = ''
APPINFO = (PROJECT, f'v{VERSION}', f'by {AUTHORS}')

logger = logging.getLogger(PROJECT)
logger.propagate = False
logger.setLevel('DEBUG')
logger.addHandler(RichHandler(
    omit_repeated_times=False, show_path=False,
    markup=True, rich_tracebacks=True, tracebacks_show_locals=True,
    log_time_format=lambda dt: f'[{dt.strftime("%X.%f")[:12]}]'  # HH:MM:SS.%3f
))


def ensure_utf8():
    '''
    Rich output usually contains unicode thus requires UTF-8 IO encoding.
    On legacy windows or piped stdout/stderr, rich.console.Console will
    try to catch UnicodeError and suggest PYTHONIOENCODING=utf-8.
    This function fix encoding without depending on environment variables.
    Also validate that nuitka compiled this program with python flag -u.
    '''
    for stream in (sys.stdout, sys.stderr):
        with suppress(AttributeError):
            if stream.encoding and stream.encoding.lower() != 'utf-8':
                stream.reconfigure(encoding='utf-8', errors='replace')
            buffered = isinstance(stream.buffer, BufferedWriter)
            if ONEFILE and buffered:
                logger.error(f'{stream.name.upper()}: {stream.buffer}', file=stream)


ensure_utf8()


def load(path: Path) -> dict:
    '''Deserialize data from file to a dict'''
    if path.suffix == '.zstd':
        with suppress(Exception), zstd.open(path, 'r') as f:
            return json.load(f)
    else:
        with suppress(Exception), open(path, encoding='utf-8') as f:
            return json_repair.load(f) if json_repair else json.load(f)
    return {}


def dump(data: dict, path: Path) -> Path:
    '''Serialize data as a binary stream and write to file'''
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
def tailf(path: Path, intv: float = 0.1, create: bool = True) -> threading.Event:
    '''Watch a file and print new lines like `tail -f`'''
    def wrapper(evt) -> None:
        with open(path, encoding='utf-8') as f:
            f.seek(0, 2)
            while not evt.wait(intv):
                if line := f.readline():
                    rprint(line, file=sys.__stdout__, end='')
    stop = threading.Event()
    task = threading.Thread(target=wrapper, args=(stop, ), name='tail-f', daemon=True)
    try:
        if not path.is_file() and create:
            path.touchp()
        if path.is_file():
            task.start()
        yield stop
    finally:
        stop.set()
        task.is_alive() and task.join(intv * 2)


def pidof(target: str) -> int:
    '''Query the process id of program name or executable path (case ignored)'''
    target = abspath(target).name.lower()
    with suppress(psutil.NoSuchProcess, psutil.AccessDenied):
        for proc in psutil.process_iter(['name']):
            if target in proc.info['name'].lower():
                return proc.pid
    return 0


def where(path: str, name: str, default: str = '') -> str:
    '''
    Query the install location of a software from Windows Registry.

    Args:
        path (str): registry folder (hkey and subkey)
        name (str): registry value to query
        default (str, optional): fallback value if path or name not found (default: '')

    Returns:
        str: path install location (must be a folder) or default value
    '''
    if winreg is None:
        return default
    for prefix, hkey in itertools.product(
        ('Software', 'Software\\WOW6432Node'),
        (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE)
    ):
        with suppress(Exception), winreg.OpenKey(hkey, f'{prefix}\\{path}') as ctx:
            val = abspath(winreg.QueryValueEx(ctx, name)[0])
            if val.is_dir():
                return str(val)
    return default


def isgui(path: str | Path = sys.argv[0]) -> bool | None:
    '''Parse PE header and check if executable is GUI subsystem program'''
    with suppress(Exception), open(abspath(path), 'rb') as f:
        f.seek(0x3C)                                                # Skip DOS Stub (60)
        offset = struct.unpack('<I', f.read(4))[0]
        f.seek(offset)
        assert f.read(4) == b'PE\x00\x00'
        f.seek(offset + 0x18)                                       # Skip signature (4) + COFF (20)
        assert struct.unpack('<H', f.read(2))[0] in [0x10B, 0x20B]  # PE32 & PE32+
        f.seek(offset + 0x18 + 0x44)                                # Subsystem
        return struct.unpack('<H', f.read(2))[0] == 2


def console(show: bool, title: str = '') -> bool:
    '''
    Manage console for Windows GUI/CUI subsystem programs.

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

        Use `dumpbin.exe /headers <program>` or `isgui(<program>)` to check exe mode:
            2 subsystem => Windows GUI
            3 subsystem => Windows CUI
    '''
    if not WINDOWS:
        return True
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    hwnd = kernel32.GetConsoleWindow()
    if not show:
        if hwnd and user32.ShowWindow(hwnd, 0):     # SW_HIDE
            atexit.register(lambda h: user32.IsWindow(h) and user32.ShowWindow(h, 1), hwnd)
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
        kernel32.SetConsoleCP(65001)                # UTF-8
        kernel32.SetConsoleOutputCP(65001)          # UTF-8
        title and kernel32.SetConsoleTitleW(title)
        return True
    except Exception:
        return False


def lprint(it: typing.Iterable) -> None:
    '''Consume and print each elements of the iterable'''
    deque(map(rprint, it), maxlen=0)


def browse(path: str | Path | None = None, start: bool = False) -> None:
    '''
    View directory or select file in explorer or open path link.

    Args:
        path (str or Path, optional): path to the folder or file
        start (bool, optional): whether to open specified path (default: False)

    Notes:
        If start is False, view folder or file in system explorer.
        If start is True and path is file, open with default system applications.
        For example, webbrowser for .html files, notepad for .txt files, etc.
    '''
    if path is None or not (path := abspath(path)).exists():
        return
    if WINDOWS:
        if start:
            os.startfile(path.as_uri())
        elif path.is_file():
            exec_result('explorer', '/select,', str(path).replace('/', os.sep))
        elif path.is_dir():
            exec_result('explorer', '/root,', str(path).replace('/', os.sep))
    elif platform.system() == 'Darwin':
        exec_result('open', '' if start else '-R', path)
    elif xdg := shutil.which('xdg-open'):
        exec_result(xdg, path if start else path.parent)
    elif gnome := shutil.which('gnome-open'):
        exec_result(gnome, path if start else path.parent)


def capture(pid: int | str) -> Image.Image | None:
    '''Take screenshot of the specified prorgam id or name'''
    search = pidof(pid) if isinstance(pid, str) else pid
    if not WINDOWS:
        return ImageGrab.grab(all_screens=True)  # FIXME: find window bbox
    hwnds: [int, tuple[int, tuple[int, int, int, int]]] = {}
    user32, gdi32, wtypes = ctypes.windll.user32, ctypes.windll.gdi32, ctypes.wintypes

    @ctypes.WINFUNCTYPE(wtypes.BOOL, wtypes.HWND, wtypes.LPARAM)
    def callback(hwnd, _) -> bool:
        pid, rect = wtypes.DWORD(), wtypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        xyxy = x1, y1, x2, y2 = (rect.left, rect.top, rect.right, rect.bottom)
        valid = user32.IsWindowVisible(hwnd) and (x2 - x1) > 1 and (y2 - y1) > 1
        if pid.value == search and valid:
            hwnds[hwnd] = [pid.value, xyxy]
        return True
    awareness()
    user32.EnumWindows(callback, 0)
    if not hwnds:
        return

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
    hwnd, (pid, bbox) = next(iter(hwnds.items()))
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


def relpath(path: str | Path, other: str | Path = BASEDIR) -> Path:
    '''Safe relative path to default BASEDIR'''
    path = abspath(path)
    with suppress(ValueError):
        return path.relative_to(abspath(other), walk_up=True)
    return path


@contextmanager
def poolmap(ntask: int | typing.Iterable[typing.Any] = -1) -> typing.Callable[..., typing.Iterator]:
    '''Run tasks in parallel with ThreadPoolExecutor'''
    if not isinstance(ntask, int):
        ntask = len(ntask) if hasattr(ntask, '__len__') else -1
    if POOLMAP and (ntask < 0 or ntask > os.cpu_count()):
        e = ThreadPoolExecutor(thread_name_prefix='poolmap')
        m = e.map
    else:
        e, m = None, lambda *a, **k: list(map(*a))
    try:
        yield m
    finally:
        e and e.shutdown()


@contextmanager
def pidfile(path: str | Path | None, singleton: bool = True) -> None:
    '''Write current process id to a file and ensure singleton'''
    if not path:
        yield
        return
    path = abspath(path).touchp()
    with suppress(OSError, ValueError, psutil.NoSuchProcess, psutil.AccessDenied), open(path) as f:
        last, this = psutil.Process(int(f.read())), psutil.Process(os.getpid())
        # different version of executables could cause singleton violation
        # FIXME: do NOT just compare argv[0]
        if last.cmdline()[0] == this.cmdline()[0] and singleton:
            raise RuntimeError('Already running')
    with open(path, 'w') as f:
        f.write(str(os.getpid()))
    func = atexit.register(lambda p=path: p.unlink(missing_ok=True))
    try:
        yield
    finally:
        atexit.unregister(func)
        func()


@contextmanager
def redirect(path: str | Path | None, stderr: bool = True, tee: bool = False) -> None:
    '''Temporary IO stream redirection to a file'''
    if not path:
        yield
        return
    path = abspath(path).touchp()
    origin = (sys.stdout, sys.stderr)
    try:
        sys.stdout = sys.stderr = open(path, 'w', buffering=1, encoding='utf-8')
        if tee:
            with tailf(path, create=False):
                yield
        else:
            yield
    finally:
        if sys.stdout != origin[0]:
            sys.stdout.close()
        sys.stdout, sys.stderr = origin


def strftime(dt: datetime | int | float | None = None, fmt: str = '%F %X') -> str:
    '''Convert datetime or seconds since epoch to formatted string'''
    if isinstance(dt, (int, float)):
        dt = datetime.fromtimestamp(dt)
    elif not isinstance(dt, datetime):
        dt = datetime.now()
    return dt.strftime(fmt)


def awareness() -> bool:
    '''Enable process DPI awareness on Windows and return True if success'''
    if not WINDOWS:
        return False
    with suppress(Exception):       # Win10 1607+
        val = ctypes.c_void_p(-4)   # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        return ctypes.windll.user32.SetProcessDpiAwarenessContext(val) == 1
    with suppress(Exception):       # Win8.1+
        val = 2                     # PROCESS_PER_MONITOR_DPI_AWARE
        return ctypes.windll.shcore.SetProcessDpiAwareness(val) == 0
    with suppress(Exception):       # Win7/Vista
        return ctypes.windll.user32.SetProcessDPIAware() == 1


def play_sound(snd: int | str | Path = 0, dtms: int = 300) -> None:
    '''Play wav file or sound alias or beep at specified frequency'''
    if not hasattr(play_sound, 'backends'):
        play_sound.backends = (ps, bp) = ([], [])
        if winsound:                            # Windows
            ps.append(lambda s: winsound.PlaySound(s, winsound.SND_ASYNC | winsound.SND_FILENAME))
            bp.append(lambda f, d: winsound.Beep(int(f), d))
        if ffplay := shutil.which('ffplay'):    # ffmpeg
            ps.append(lambda s: exec_command(ffplay, '-nodisp', '-autoexit', s))
        if afplay := shutil.which('afplay'):    # MacOS
            ps.append(lambda s: exec_command(afplay, s))
            bp.append(lambda f, d: exec_result(afplay, '/System/Library/Sounds/Glass.aiff'))
        if paplay := shutil.which('paplay'):    # Linux PulseAudio
            ps.append(lambda s: exec_command(paplay, s))
        if aplay := shutil.which('aplay'):      # Linux ALSA
            ps.append(lambda s: exec_command(aplay, '-q', s))
        if beep := shutil.which('beep'):        # Linux beep
            bp.append(lambda f, d: exec_result(beep, '-f', f, '-l', d))
        if play := shutil.which('play'):        # Linux sox
            bp.append(lambda f, d: exec_result(
                play, '--no-show-progress', '--null', '--channels', '1',
                'synth', d / 1000, 'sine', f
            ))
        bp.append(lambda f, d: print('\a', end='', flush=True))     # terminal bell
    if isinstance(snd, (int, float)):
        if isinstance(snd, int) and snd > 0 and snd < 89:           # MIDI Note
            snd = 440 * pow(2, (snd - 69) / 12)                     # to frequency
        if dtms <= 0:
            return logger.error('Duration must be positive number')
        if snd <= 0:
            return logger.error('Frequency must be positive')
        if winsound and (snd < 37 or snd > 32767):
            return logger.error('Frequency must be in [37 ~ 32767]')
        return play_sound.backends[1][0](int(snd), int(dtms)) and None
    path = abspath(str(snd))
    if isinstance(snd, str) and winsound:
        if snd.lower().startswith('mb_') and hasattr(winsound, snd.upper()):
            return winsound.MessageBeep(getattr(winsound, snd.upper()))
        if snd.startswith('System'):
            return winsound.PlaySound(snd, winsound.SND_ASYNC | winsound.SND_ALIAS)
        if not path.is_file():
            return winsound.PlaySound(snd, winsound.SND_ASYNC | winsound.SND_APPLICATION)
    if not path.is_file() or not play_sound.backends[0]:
        return
    if isinstance(proc := play_sound.backends[0][0](path), subprocess.Popen):
        last = getattr(play_sound, 'proc', None)
        if last and last.poll() is None:
            last.terminate()
        play_sound.proc = proc


def size_format(size: int, sep: str = ' ', dem: int = 2, exp: int = 1024) -> str:
    '''Convert size in bytes to human-readable string'''
    for unit in ' KMGT':
        if size < exp:
            return f'{size:.{dem * (unit != " ")}f}{sep}{unit}B'
        size /= exp
    return f'{size:.{dem}f}{sep}PB'


def tree_mtime(root: str | Path, mtime: float = 0) -> float:
    '''Recursively get the newest modified time under the tree'''
    stack = deque([abspath(root)])
    while stack:
        with suppress(OSError), os.scandir(stack.pop()) as it:
            for e in it:
                mtime = max(mtime, e.stat().st_mtime)   # cached per entry
                if e.is_dir(follow_symlinks=False):     # avoid recursion
                    stack.append(e.path)
    return mtime


def tree_files(root: str | Path, level: int = -1) -> typing.Iterator[str]:
    '''List files like `ls -lh` + `tree -Dh`'''
    try:
        path = abspath(root)
        st = path.stat()
    except OSError:
        return
    stack = deque([(path.name, path, path.is_dir(), st.st_mode, st.st_size, st.st_mtime, '', 1, 0)])
    while stack:
        name, path, is_dir, mode, size, mtime, prefix, last, depth = stack.pop()
        conn = ('└── ' if last else '├── ') if depth else ''
        sstr, tstr = size_format(size), strftime(mtime)
        yield f'{os.path.stat.filemode(mode)} {sstr:>9s} {tstr} {prefix}{conn}{name}'
        if not is_dir or depth >= level >= 0:
            continue
        prefix += ('    ' if last else '│   ') if depth else ''
        with suppress(OSError), os.scandir(path) as it:
            for i, e in enumerate(
                sorted(it, key=lambda e: (e.is_dir(), e.name.lower()), reverse=True)
            ):
                st = e.stat()
                stack.append((
                    e.name, e.path, e.is_dir(follow_symlinks=False),
                    st.st_mode, st.st_size, st.st_mtime, prefix, not i, depth + 1
                ))


def exec_command(*a, **k) -> subprocess.Popen:
    '''Execute command and return the unstopped process'''
    return subprocess.Popen(list(map(str, a)), **{
        'text': True, 'encoding': 'utf-8',
        'stdout': subprocess.PIPE, 'stderr': subprocess.STDOUT, **k
    })


def exec_result(*a, timeout: int | None = None, **k) -> str:
    '''Execute command and wait and return its output'''
    with exec_command(*a, **k) as proc:
        try:
            return proc.communicate(timeout=timeout)[0].strip()
        except (KeyboardInterrupt, subprocess.TimeoutExpired):
            return ''
        finally:
            proc.kill()


def line_buffer(*cmd, check: bool = True, force: bool = True, **k) -> typing.Iterator[str]:
    '''Execute command and force stdout to be line buffered using PTY'''
    tpl = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    k.setdefault('bufsize', 1)
    if force and pty is not None:                           # unix pty
        mfd, sfd = pty.openpty()
        with subprocess.Popen(list(map(str, cmd)), **{
            **k, 'stdout': sfd, 'stderr': subprocess.STDOUT, 'pass_fds': [sfd]
        }) as proc, os.fdopen(mfd, 'r', buffering=1, encoding='utf-8') as fobj:
            os.close(sfd)
            try:
                yield from filter(None, map(lambda t: tpl.sub('', t.rstrip()), fobj))
            except OSError as e:
                if e.errno != 5:  # EIO is treated as EOF
                    raise
    else:
        if not force:
            pass
        elif WINDOWS:                                       # winpty or conpty
            if git := shutil.which('git'):
                winpty = abspath(f'{git}/../../usr/bin/winpty.exe')
            else:
                winpty = Path(shutil.which('winpty') or '')
            if winpty.is_file():
                cmd = [winpty, '-Xallow-non-tty', *cmd]
        elif script := shutil.which('script'):              # bsdutils
            cmd = [script, '-q', '-E', 'never', '-c', ' '.join(cmd), '/dev/null']
        elif stdbuf := shutil.which('stdbuf'):              # coreutils
            cmd = [stdbuf, '-oL', *cmd]
        with exec_command(*cmd, **k) as proc:
            yield from filter(None, map(lambda t: tpl.sub('', t.rstrip()), proc.stdout))
    if (rc := proc.poll()) and check:
        raise subprocess.CalledProcessError(rc, proc.args)


def walk_filter(root: str | Path, incs: str = '', excs: str = '') -> typing.Iterator[Path]:
    '''Glob files recursively and filter and yield Path'''
    itpl = '|'.join(map(fnmatch.translate, filter(None, set(incs.split(';')))))
    etpl = '|'.join(map(fnmatch.translate, filter(None, set(excs.split(';')))))
    inc = re.compile(itpl, re.IGNORECASE).match if itpl else lambda n: True
    exc = re.compile(etpl, re.IGNORECASE).match if etpl else lambda n: False
    path = abspath(root)
    if path.is_file():
        if inc(path.name) and not exc(path.name):
            yield path
        return
    if not path.is_dir():
        return
    # Method A
    #  for root, dirs, files in os.walk(path):
    #      dirs[:] = [d for d in dirs if not exc(d)]
    #      yield from (Path(root + '/' + fn) for fn in files if inc(fn) and not exc(fn))
    # Method B (faster)
    stack = deque([path])
    while stack:
        with suppress(OSError), os.scandir(stack.pop()) as it:
            for e in it:
                if e.is_file(follow_symlinks=False) and inc(e.name) and not exc(e.name):
                    yield Path(e.path)
                elif e.is_dir(follow_symlinks=False) and not exc(e.name):
                    stack.append(e.path)


def cached_property(func: typing.Callable) -> functools.cached_property:
    return functools.wraps(func)(functools.cached_property(func))


class PZipFile(zipfile.ZipFile):
    '''
    Patching on zipfile.ZipFile and zipfile._ZipWriteFile to add support for
    parallel compression and serial writing of pre-compressed data.
    '''
    class EmptyCompressor:
        def compress(self, data: bytes) -> bytes: return data
        def flush(self) -> bytes: return b''
    _empty = EmptyCompressor()

    def writefile(self, zinfo: zipfile.ZipInfo, fin: str | Path | bytes | typing.BinaryIO) -> None:
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
    def __aliases__(self) -> dict:
        return {
            field.metadata['alias']: field.name
            for field in dataclasses.fields(self)
            if 'alias' in field.metadata
        }

    def __getattr__(self, key: str) -> typing.Any:
        if key in self.__aliases__:
            return getattr(self, self.__aliases__[key])
        raise AttributeError(f'Config has no attribute {key}')

    def __setattr__(self, key: str, val: typing.Any) -> None:
        if key in self.__aliases__:
            key = self.__aliases__[key]
        for field in dataclasses.fields(self):
            if field.name != key:
                continue
            if callable(field.type):
                val = field.type(val)
            if 'ge' in field.metadata:
                val = max(field.metadata['ge'], val)
            if 'le' in field.metadata:
                val = min(field.metadata['le'], val)
        super().__setattr__(key, val)

    def asdict(self) -> dict:
        return {k: v for k, v in dataclasses.asdict(self).items() if k not in self.__excludes__}

    def dump(self, path: Path) -> Path: return dump(self.asdict(), path)

    @classmethod
    def load(cls, path: Path) -> typing.Self:
        fields = {f.name for f in dataclasses.fields(cls)}
        data = {k: v for k, v in load(path).items() if k in fields}
        if 'path' in fields:
            data.setdefault('path', path)
        return cls(**data)


@dataclasses.dataclass
class SnapConfig(IOMixin):
    path: Path   = ''
    __excludes__ = ('path')

    @staticmethod
    def _field(name: str, default: typing.Any = None, **k) -> dataclasses.Field:
        return dataclasses.field(default=default, metadata={'alias': name, **k})

    watchdog_enabled: bool = _field('watch', False)
    watchdog_seconds: int  = _field('wintv', 30, ge=1)
    schedule_enabled: bool = _field('sched', True)
    schedule_seconds: int  = _field('sintv', 10 * 60, ge=1)
    http_server_port: int  = _field('port', 9999, le=65535)
    preview_filepath: str  = _field('pimg', '')
    includes_pattern: str  = _field('incs', '')  # join patterns with colon `;`
    excludes_pattern: str  = _field('excs', '')
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
class SnapRecord(IOMixin):
    type: str
    sdir: Path
    hdir: Path
    size: int                = 0
    fcnt: int                = 0
    name: str                = ''
    time: float              = dataclasses.field(default_factory=time.time)
    tree: dict[str, dict]    = dataclasses.field(repr=False, compare=False, default_factory=dict)
    '''
    Layout of tree:
    {
        "folder": {
            "subfolder": {
                "file": ["hashval", rsize, mtime],
                ...
            },
            ...
        },
        ...
    }
    '''
    info: dict[str, list]    = dataclasses.field(repr=False, compare=False, default_factory=dict)
    '''
    Layout of info:
    {
        "folder/subfolder/file": [Path("/path/to/hashfile"), dsize, rsize, mtime],
        ...
    }
    '''
    view: Image.Image | None = dataclasses.field(repr=False, compare=False, default=None)
    path: Path               = ''

    __excludes__ = ('info', 'view', 'path')     # exclude from saving

    @cached_property
    def flat(self) -> dict[str, list]:
        '''
        Layout of flat:
        {
            "folder/subfolder/file": ["hashval", rsize, mtime],
            ...
        }
        '''
        def dfs(root: dict, base: str, cache: dict) -> dict:
            for part, node in root.items():
                path = base + ('/' if base else '') + part
                if isinstance(node, dict):
                    dfs(node, path, cache)
                else:
                    cache[path] = node
            return cache
        return dfs(self.tree, '', {})

    @cached_property
    def dirs(self) -> dict[str, list]:
        '''
        Layout of dirs:
        {
            "folder/subfolder": [nfile, tsize, mtime],
            ...
        }
        '''
        def dfs(root: dict, base: str, cache: dict) -> tuple[dict, int, float]:
            nfile = tsize = mtime = 0
            for part, node in root.items():
                path = base + ('/' if base else '') + part
                if isinstance(node, dict):
                    s, m = dfs(node, path, cache)[1:]
                else:
                    s, m = node[1:]
                    nfile += 1
                tsize, mtime = tsize + s, max(mtime, m)
            if base:
                cache[base] = [nfile, tsize, mtime]
            return cache, tsize, mtime
        return dict(sorted(dfs(self.tree, '', {})[0].items()))


class SnapEngine:
    '''Engine to create, restore, export, delete and summary snapshots'''
    @classmethod
    def create(
        cls, cfg: SnapConfig, type: str = 'manual', pid: int | None = None,
        log: typing.Callable[[Path, float], None] | None = None
    ) -> Path:
        '''
        Create a snapshot

        Args:
            cfg (SnapConfig): provide sdir, bdir, comp, bcnt
            type (str, optional): 'watch', 'sched', 'hotkey' or (default: 'manual')
            pid (int, optional): capture screenshot for specified process id
            log (Callable[[Path, float], None], optional): callback to log progress

        Returns:
            Path: record path to the created snapshot
        '''
        view = capture(pid) if pid > 1 else None
        snap = SnapRecord(type, cfg.sdir, cfg.bdir / 'blob')
        snap.hdir.mkdirp()
        if view and log:
            log(f'Captured {view.size} for {pid}', 0)
        files: dict[Path, tuple[str, int, int, int]] = {}
        infos: dict[str, tuple[Path, int, int, int]] = {
            k: v for s in cls.summary(cfg.bdir, True, first='size') for k, v in s.info.items()
        }
        log and log(f'Found {len(infos)} blob files', 0)

        def worker(src: Path) -> Path:
            rpath = src.relative_to(snap.sdir)
            stat = src.stat()
            dst, dsize, rsize, mtime = infos.get(str(rpath), [None, 0, 0, 0])
            if rsize == stat.st_size and mtime == stat.st_mtime:
                pass
            elif stat.st_size < 4096 or not cfg.comp:
                with open(src, 'rb') as f:
                    hasher = hashlib.file_digest(f, hashlib.md5)
                    dst = snap.hdir / hasher.hexdigest()[:16]
                with suppress(shutil.SameFileError, PermissionError):
                    shutil.copy2(src, dst)
                dsize = stat.st_size
            else:
                if src.is_symlink() or stat.st_nlink > 1:   # soft or hard link
                    tmp = tempfile.NamedTemporaryFile(delete=False).name
                else:
                    tmp = snap.hdir / str(stat.st_ino)
                hobj = hashlib.md5()
                with open(src, 'rb') as fin, zstd.open(tmp, 'wb') as fout:
                    while buf := fin.read(shutil.COPY_BUFSIZE):
                        hobj.update(buf)
                        fout.write(buf)
                dst = snap.hdir / (hobj.hexdigest()[:16] + '.zstd')
                with suppress(shutil.SameFileError, PermissionError):
                    shutil.move(tmp, dst)
                    shutil.copystat(src, dst)
                dsize = dst.stat().st_size
            files[rpath] = [dst.name, stat.st_size, stat.st_mtime, dsize]
            return rpath

        paths = list(walk_filter(cfg.sdir, cfg.incs, cfg.excs))
        snap.fcnt = len(paths)
        with poolmap(snap.fcnt) as pmap:
            for i, rpath in enumerate(pmap(worker, paths), 1):
                log and log(rpath, i / snap.fcnt)
        for rpath in sorted(files):
            node, parts = snap.tree, rpath.parts
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = files[rpath][:-1]
            snap.size += files[rpath][-1]
        path = snap.dump(cfg.bdir / strftime(snap.time, '%Y%m%d-%H%M%S-%f.zstd'))
        view and view.save(path.with_suffix('.jpg'))
        for s in cls.summary(cfg.bdir)[cfg.bcnt:]:
            cls.delete(s.path)
        return path

    @classmethod
    def restore(
        cls, record: Path, member: list[str] = [], sdir: Path | None = None,
        sync: bool = False, log: typing.Callable[[Path, float], None] | None = None
    ) -> Path:
        '''
        Restore a snapshot to specified directory

        Args:
            record (Path): record path
            member (list[str], optional): filter files by relative paths
            sdir (Path, optional): source directory (default: sdir of record)
            sync (bool, optional): keep sdir layout exactly same as record (default: False)
            log (Callable[[Path, float], None], optional): callback to log progress

        Returns:
            Path: record path
        '''
        snap = SnapRecord.load(record)
        sdir = sdir or snap.sdir
        srcs: list[Path] = []
        dsts: dict[Path, str] = {}
        dels: dict[Path, str] = {}
        for rel in snap.flat:
            if member and not rel.startswith(member):
                continue
            srcs.append(snap.hdir / snap.flat[rel][0])
            dsts[sdir / rel] = rel
        log and log(f'Found {len(dsts)} files to restore', 0)
        for path in walk_filter(sdir) if sync else []:
            rel = str(path.relative_to(sdir))
            if (not member or rel.startswith(member)) and rel not in snap.flat:
                dels[path] = rel
        sync and log and log(f'Found {len(dels)} file to delete', 0)
        for rel in snap.dirs:
            if not member or rel.startswith(member):
                (sdir / rel).mkdirp()
        total = len(dsts) + len(dels)
        with poolmap(total) as pmap:
            def worker(src: Path, dst: Path) -> Path:
                with suppress(shutil.SameFileError, PermissionError):
                    if src.suffix == '.zstd':
                        with zstd.open(src, 'rb') as fin, open(dst, 'wb') as fout:
                            shutil.copyfileobj(fin, fout)
                        shutil.copystat(src, dst)
                    else:
                        shutil.copy2(src, dst)
                return dst
            for i, path in enumerate(pmap(worker, srcs, dsts), 1):
                log and log(dsts[path], i / total)
            for i, path in enumerate(pmap(lambda x: x.unlink() or x, dels), i + 1):
                log and log(dels[path], i / total)
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
            output (Path): output path
            log (Callable[[Path, float], None], optional): callback to log progress

        Returns:
            Path: absolute path to the exported zip file
        '''
        snap = SnapRecord.load(record)
        if not output.name:
            output /= record.stem
        output = output.with_suffix('.zip').touchp()
        with poolmap(snap.fcnt) as pmap, PZipFile(output, 'w', zipfile.ZIP_DEFLATED) as zf:
            def worker(rel: str) -> str:
                path = snap.hdir / snap.flat[rel][0]
                zinfo = zipfile.ZipInfo.from_file(path, rel)
                zinfo.compress_type = zf.compression
                zinfo._compresslevel = zf.compresslevel
                with (zstd.open if path.suffix == '.zstd' else open)(path, 'rb') as fin:
                    zf.writefile(zinfo, fin)
                return rel
            for i, rel in enumerate(pmap(worker, snap.flat), 1):
                log and log(rel, i / snap.fcnt)
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
        hdir = SnapRecord.load(record).hdir
        snaps = cls.summary(record.parent)
        if hdir.parent != record.parent:
            snaps += cls.summary(hdir.parent)
        log and log(f'Found {len(snaps)} snapshots', 0)
        used = {v[0] for s in snaps if s.path != record for v in s.flat.values()}
        dels = {p: p.relative_to(hdir) for p in walk_filter(hdir) if p.name not in used}
        total = len(dels)
        with poolmap(total) as pmap:
            for i, path in enumerate(pmap(lambda x: x.unlink() or x, dels), 1):
                log and log(dels[path], i / total)
        record.with_suffix('.jpg').unlink(missing_ok=True)
        record.unlink()
        return record

    @classmethod
    def summary(cls, bdir: Path, detail: bool = False, first: str = '') -> list[SnapRecord]:
        '''
        List all snapshots with newest first

        Args:
            bdir (Path): SnapConfig.backup_directory
            detail (bool, optional): include detail information (default: False)
            first (str, optional): only report the first one sorted by 'mtime', 'size' or 'name'

        Returns:
            list[SnapRecord]
        '''
        if not bdir.is_dir():
            return {}
        if not hasattr(cls, '_cache'):
            cls._cache = {}
        cache = cls._cache.setdefault((str(bdir), bool(detail), str(first)), {'hash': 0})
        paths = {
            Path(e.path): [e.stat().st_mtime, e.stat().st_size, e.name]
            for e in sorted(os.scandir(bdir), key=lambda e: e.name)
            if e.is_file() and e.name.endswith(('.json', '.zstd'))
        }
        oldval, cache['hash'] = cache['hash'], hash(f'{bdir.stat().st_mtime}{paths.values()}')
        if oldval == cache['hash']:
            return cache['data']
        if first:
            col = {'mtime': 0, 'size': 1, 'name': 2}.get(first, 0)
            paths = sorted(paths, key=lambda x: paths[x][col], reverse=True)
        snaps: list[SnapRecord] = []
        for path in paths:
            try:
                assert not first or len(snaps) != 1
                snaps.append(snap := SnapRecord.load(path))
                assert detail
            except Exception:
                continue
            stats = cls._cache.setdefault(snap.hdir, {'time': 0})
            newval, oldval = snap.hdir.stat().st_mtime, stats['time']
            if oldval != newval:
                stats['size'] = {e.name: e.stat().st_size for e in os.scandir(snap.hdir)}
                stats['time'] = newval
            view = path.with_suffix('.jpg')
            if view.is_file():
                snap.view = Image.open(view).copy()
            for rel, (hval, rsize, mtime) in snap.flat.items():
                dst = snap.hdir / hval
                if hval in stats['size']:
                    snap.info[rel] = [dst, stats['size'][hval], rsize, mtime]
                    continue
                for prev in snaps[-2::-1]:
                    if rel in prev.info and any(prev.info[rel][1:]):
                        snap.info[rel] = prev.info[rel]
                        break
                else:
                    snap.info[rel] = [dst, 0, 0, 0]
                    with suppress(Exception):
                        stat = dst.stat()
                        assert stat.st_mtime == mtime
                        snap.info[rel] = [dst, stat.st_size, rsize, mtime]
        cache['data'] = sorted(snaps, key=lambda x: x.time, reverse=True)
        return cache['data']


class SnapRouter(http.server.BaseHTTPRequestHandler):
    '''Simple router to register callback functions and handle http requests'''
    route: dict[str, dict[str, typing.Any]] = {}

    @staticmethod
    def getname(obj: typing.Any) -> str: return getattr(obj, '__name__', str(obj))

    @classmethod
    def from_port(
        cls, port: int, host: str = 'localhost'
    ) -> tuple[threading.Thread, http.server.ThreadingHTTPServer]:
        srv = http.server.ThreadingHTTPServer((host, port), cls)
        return threading.Thread(target=srv.serve_forever, name='httpd', daemon=True), srv

    @classmethod
    def register(cls, func: typing.Callable[..., typing.Any], path: str = '') -> None:
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

    def do_GET(self) -> None:
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

    def do_POST(self) -> None:
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


class SnapBroker(object):
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
        self.engine = SnapEngine()
        self.config = SnapConfig()
        self.update(self.config if cfg is None else cfg)
        for attr, func in inspect.getmembers(self.__class__, callable):
            if not attr.startswith(('_', 'on_')):
                SnapRouter.register(func)

    __getitem__ = __getattr__ = lambda self, key: getattr(self.config, key)

    def __setitem__(self, key: str, val: typing.Any) -> None:
        if key in self.config.__aliases__:
            key = self.config.__aliases__[key]
        if not hasattr(self.config, key):
            return
        if hasattr(self, f'_set_{key}'):
            getattr(self, f'_set_{key}')(val)
        else:
            setattr(self.config, key, val)

    @contextmanager
    def _inject(self, k: dict) -> dict:
        newline = False
        if self._level > 1:
            k.setdefault('log', lambda p, c: logger.debug(f'{c:7.2%} {p}'))
        elif self._level and sys.stdout.isatty():
            def cprint(path: Path, pcent: float) -> None:
                nonlocal newline
                newline = True
                print(f'\r\033[K{pcent:7.2%} {path}', end='', flush=True)
            k.setdefault('log', cprint)
        try:
            yield k
            if self.config.succ and not self._nosrv:
                play_sound(self.config.succ)
        except Exception:
            if self.config.fail and not self._nosrv:
                play_sound(self.config.fail)
            raise
        finally:
            if newline:
                print('')

    @property
    def srvs(self) -> dict:
        '''State of background services'''
        return {
            'watch': self._watch.is_alive(),
            'sched': self._sched[0] and self._sched[0].is_alive(),
            'httpd': self._httpd[0] and self._httpd[0].is_alive(),
            'psync': self._psync[0] and self._psync[0].is_alive(),
            'hotkey': any(set(getattr(keyboard, '_hotkey', {})) & {
                self.config.knew, self.config.ktog, self.config.kdel
            }),
        }

    def preset(self, path: Path, name: str = '') -> dict:
        '''
        Parse presets from EZ Auto Backup format files (e.g. EZAB/GAMELIST.ERAB)

        Args:
            path (Path): path to presets file
            name (str, optional): preset name to use

        Returns:
            dict: parsed presets

        Examples:
            >>> broker = SnapBroker()
            >>> broker.preset('D:/EZAB/GAMELIST.ERAB')['games']
            {                                       # example
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
            }
            >>> broker.preset('D:/EZAB/GAMELIST.ERAB', name='Project Zomboid')['result']
            Using sdir=~/Zomboid and proc=ProjectZomboid64.exe
        '''
        data = {'games': {}, 'extra': {}, 'result': ''}
        info = configparser.ConfigParser(default_section='Info')
        with suppress(OSError), open(abspath(path), encoding='utf-8') as f:
            for row in csv.reader(f, delimiter='#'):
                if not row:
                    continue
                if row[0] == 'END':
                    break
                data['games'][row[0]] = row[1:]
            info.read_string(f.read())
        for sname, section in info.items():
            data['extra'].setdefault(sname.lower(), {}).update(section.items())
        if name not in data['games']:
            data['result'] = f'{name} not found'
        else:
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
            data['result'] = f'Using sdir={self.config.sdir} and proc={self.config.proc}'
        return data

    def update(self, cfg: dict | str | Path | None = None, **k) -> dict[str, typing.Any]:
        '''
        Update configuration from file or string

        Args:
            cfg (dict | str | Path, optional): config data or config file or JSON string

        Notes:
            If cfg is an absolute path, replace current config with the one from file.
            If str, make sure it exists. If pathlib.Path, this function ensure file exists.

        Returns:
            dict: current configuration

        Examples:
            >>> broker = SnapBroker()
            >>> broker.config.path                                  # config in memory
            ''
            >>> broker.update(None)                                 # get current config
            {'http_server_port': 9999, 'backup_max_count': 20, ...}
            >>> broker.update({'port': 0, 'bcnt': 0})               # support aliases
            {'http_server_port': 0, 'backup_max_count': 0, ...}
            >>> broker.update('{"http_server_port": 8888}')         # from JSON string
            {'http_server_port': 8888, 'backup_max_count': 10, ...}
            >>> broker.update('/absolute/path/to/config.json')      # from file
            {'http_server_port': 9999, 'backup_max_count': 10, ...}
            >>> broker.config.path                                  # path changed
            Path('/absolute/path/to/config.json')
        '''
        target = self.config
        data = cfg if isinstance(cfg, dict) else {}
        if isinstance(cfg, str):
            if '{' in cfg or ':' in cfg:
                data = json_repair.loads(cfg) if json_repair else json.loads(cfg)
            elif (path := abspath(cfg)).is_file():
                target = SnapConfig.load(path)
                data = target.asdict()
        elif isinstance(cfg, pathlib.Path):
            if not cfg.is_file():
                SnapConfig().dump(cfg)
            target = SnapConfig.load(cfg)
            data = target.asdict()
        with self._mutex:
            if target.path != self.config.path:
                self.start = 0
                self._snaps[-1] = {}
                self.config.path.name and self.config.dump(self.config.path)
                self.config.path = target.path
            for key, val in data.items():
                self[key] = val
        return self.config.asdict()

    def create(self, type: str = 'manual', **k) -> Path:
        '''
        Create a new snapshot with current SnapConfig

        Args:
            type (str, optional): 'watch', 'sched', 'hotkey' or default 'manual'

        Returns:
            Path: record path to the created snapshot
        '''
        with self._mutex, self._inject(k):
            return self.engine.create(self.config, str(type), pid=self.start, **k)

    def restore(
        self, record: Path, member: list[str] = [], sdir: Path | None = None, **k
    ) -> Path:
        '''
        Restore a snapshot from backup directory

        Args:
            record (Path): record path
            member (list[str], optional): filter files by relative paths
            sdir (Path, optional): source directory (default: sdir of record)

        Returns:
            Path: record path

        Examples:
            >>> broker = SnapBroker()
            >>> record = random.choice(broker.summary())
            >>> broker.restore(record.path)
            Path('/absolute/path/to/<record.path>')
        '''
        k.setdefault('sync', True)
        with self._mutex, self._inject(k):
            old = (self.config.watch, self.config.sched)
            self.config.watch = self.config.sched = False
            rst = self.engine.restore(abspath(record), member, sdir, **k)
            self.config.watch, self.config.sched = old
        return rst

    def export(self, record: Path, output: Path, **k) -> Path:
        '''
        Export current configuration to file or string

        Args:
            record (Path): record path
            output (Path): output path

        Returns:
            Path: absolute path to the exported zip file

        Examples:
            >>> broker = SnapBroker()
            >>> record = random.choice(broker.summary())
            >>> broker.export(record.path, '~/save')
            Path('C:/Users/<username>/save.zip')
            >>> broker.export(record.path, 'D:/')
            Path('D:/<record>.zip')
            >>> broker.export(record.path, 'D:/test.rar')
            Path('D:/test.zip')
        '''
        with self._mutex, self._inject(k):
            return self.engine.export(abspath(record), abspath(output), **k)

    def delete(self, record: Path, **k) -> Path:
        '''
        Delete a snapshot with current SnapConfig

        Args:
            record (Path): record path

        Returns:
            Path: record path

        Examples:
            >>> broker = SnapBroker()
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

    def summary(self, detail: bool = True) -> list[SnapRecord]:
        '''
        Get summary of all snapshots with current SnapConfig

        Args:
            detail (bool, optional): include detail information (default: True)

        Returns:
            list[SnapRecord]

        Examples:
            >>> broker = SnapBroker()
            >>> broker.summary(detail=False)
            [SnapRecord(type=xxx, sdir=xxx, hdir=xxx, ..., info={}), ...]
            >>> broker.summary(detail=True)[0].info
            {<relpath>: [<fullpath>, <size>, <mtime>], ...}
        '''
        with self._mutex:
            return self.engine.summary(self.config.bdir, detail=bool(detail))

    def toggle_mode(self, sync: bool = False, start: int | bool | None = None) -> int | bool:
        '''
        Switch between tri-state: manual start | manual stop | auto sync

        Args:
            sync (bool, optional): switch to sync mode (default: False)
            start (int | bool | None, optional): start/stop the service (default: None)

        Returns:
            int | bool: current mode, int for sync, bool for manual

        Examples:
            >>> broker = SnapBroker()
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
                self.start = pidof(self.config.proc)    # sync with process
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

    def on_snaps(self, callback: typing.Callable[[], None] | bool, intv: int = 3) -> None:
        '''SnapBroker.summary(True) is slow for UI so run it in a thread and cache the result'''
        if callback is True:
            return self._snaps[2].set()
        self._snaps[1].set()
        self._snaps[2].set()
        if not callable(callback):
            return self._snaps[0] and self._snaps[0].join()
        self._snaps[1].clear()

        def loop() -> None:
            while not self._snaps[1].is_set():
                snaps, self._snaps[3] = self.snaps, {str(x.path): x for x in self.summary(True)}
                if list(snaps) != list(self.snaps):
                    callback()
                self._snaps[2].wait(intv)
                self._snaps[2].clear()
        self._snaps[0] = threading.Thread(target=loop, name='summary', daemon=True)
        self._snaps[0].start()

    def _set_watchdog_enabled(self, v: bool) -> None:
        self.config.watch = v
        self._watch.unschedule_all()
        if not self.config.watch or self._nosrv:
            return

        class NestHandler(watchdog.events.FileSystemEventHandler):
            def on_modified(s, e: watchdog.events.FileSystemEvent) -> None:
                if e.is_directory or not self.start:
                    return
                logger.debug(f'Modified {Path(e.src_path)}')
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
            self._httpd = SnapRouter.from_port(self.config.port)
            self._httpd[0].start()

    def _set_syncwith_process(self, v: str) -> None:
        self.config.proc = (v or '').lower().replace('.exe', '')
        if not self.config.proc or self._nosrv:
            self._psync[1].set()
            return self._psync[0] and self._psync[0].join()
        if self._psync[0] and self._psync[0].is_alive():
            return
        self._psync[1].clear()

        def loop(intv: int = 3) -> None:
            while not self._psync[1].wait(intv):
                if not isinstance(self.start, bool):
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

    def _sound_factory(name: str) -> typing.Callable[[typing.Self, str], None]:
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

    def on_hotkey(
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
            1. All keys will be captured and suppressed during recording
            2. Press `esc` to cancel recording and return previous value
            3. If no keys pressed until timeout then return '' (cleared)
        '''
        old, self[name] = self[name], ''                    # disable current hotkey
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
            'sched': 'Schedule', 'sintv': 'Interval (s)', 'bcnt': 'Max Backup Count',
            'knew': 'Create', 'ktog': 'Start / Stop', 'kdel': 'Delete Latest',
            'succ': 'Notify success', 'fail': 'Notify failure', 'proc': 'Game Process',
            'pimg': 'Thumbnail', 'manual': 'Manual', 'hotkey': 'Hotkey',
            'snaps': 'Snapshots', 'type': 'Type', 'start': 'Start', 'stop': 'Stop',
            'files': 'Files', 'size': 'File Size', 'mtime': 'Last Modified',
            'create': 'Create', 'restore': 'Restore', 'export': 'Export', 'delete': 'Delete',
            'rename': 'Rename', 'on': 'On', 'off': 'Off', 'yes': 'Yes', 'no': 'No',
        },
        'zh': {
            'zh': '中文', 'lang': '语言', 'ready': '就绪', 'error': '错误',
            'sdir': '存档目录', 'bdir': '备份目录', 'select': '选择', 'browse': '打开',
            'cancel': '取消', 'drive': '磁盘', 'path': '路径', 'opts': '配置',
            'watch': '监控备份', 'wintv': '防抖 (秒)', 'port': 'HTTP 端口',
            'sched': '定时备份', 'sintv': '间隔 (秒)', 'bcnt': '最多备份个数',
            'knew': '新建备份', 'ktog': '启停任务', 'kdel': '删除最新',
            'succ': '成功提示', 'fail': '失败提示', 'proc': '跟随进程',
            'pimg': '缩略图', 'manual': '手动', 'hotkey': '快捷键',
            'snaps': '个备份', 'type': '类型', 'start': '开始', 'stop': '停止',
            'files': '个文件', 'size': '文件大小', 'mtime': '修改时间',
            'create': '新建', 'restore': '恢复', 'export': '导出', 'delete': '删除',
            'rename': '重命名', 'on': '已启用', 'off': '已关闭', 'yes': '是', 'no': '否',
        }
    }

    def __init__(self, brok: SnapBroker | None = None, langdir: Path = BASEDIR, **k) -> None:
        self.brok = brok or SnapBroker(CFGFILE, **k)
        for path in langdir.glob('lang-*.json'):
            if data := load(path):
                self.langs[path.stem[5:]] = data

    def __call__(self, k: str) -> str:
        return self.langs[self.brok.lang].get(k, self.langs['en'].get(k, k))


class TUIMixin:
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
                f'[@click=screen.rebase("{p}")]{p.name or p.drive}/[/]'
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

    class Tree(ttw.Tree):
        ICON_NODE_EXPANDED = '📂 '
        ICON_NODE = '📁 '
        ICON_FILE = '📄 '
        WIDE_CHAR = re.compile(r'''     # two column charactors
            [\u1100-\u115F] |           # Hangul Jamo
            [\u2E80-\u9FFF] |           # CJK
            [\uA960-\uA97F] |           # Korean Extend-A
            [\uAC00-\uD7AF] |           # Hangul Syllables
            [\uF900-\uFAFF] |           # CJK compatible
            [\uFF00-\uFFEF]             # Alnum + Symbol
        ''')
        SIZE_WIDTH = 9                  # xxx.xx xx
        TIME_WIDTH = 19                 # xxxx-xx-xx xx:xx:xx
        guide_depth = 3
        auto_expand = False

        def get_indent(self, node) -> int:
            depth = 0
            while node := node.parent:
                depth += 1
            return depth * self.guide_depth

        def get_length(self, label) -> int:
            return len(label) + len(self.WIDE_CHAR.findall(str(label)))

        def render_label(self, node, base, style):
            label = node.label.copy()
            label.stylize(style)
            label = label.assemble((
                self.ICON_NODE_EXPANDED if node.is_expanded else self.ICON_NODE,
                base + ttw._tree.TOGGLE_STYLE
            ) if node.allow_expand else (self.ICON_FILE, base), label)
            with suppress(AttributeError, TypeError, AssertionError):
                sstr, tstr = node.data[1:]
                width = self.size.width
                indent = self.get_indent(node)
                namelen = width - indent - self.SIZE_WIDTH - self.TIME_WIDTH - 4
                assert namelen > 10
                textlen = self.get_length(label)
                if textlen > namelen:
                    label = label[:namelen - 3] + '...'
                elif textlen < namelen:
                    label.pad_right(namelen - textlen)
                label += f' {sstr:>{self.SIZE_WIDTH}}'
                label += f' {tstr:>{self.TIME_WIDTH}}'
            return label


class SnapTUI(textual.app.App, UIMixin, TUIMixin):
    '''Terminal UI by textual'''
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
            with self.Tree('', data=('', '', ''), id='tree') as tree:
                tree.dirs: dict[str, type(tree.root)] = {}
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
        self['#slog'].write(' '.join([strftime(fmt='[%X]'), *map(str, a)]), **k)

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
        for pstr in (p for p in old if p not in new):
            table.remove_row(pstr)
        for pstr in (p for p in new if p not in old):
            snap = self.brok.snaps[pstr]
            size = size_format(snap.size)
            stype = self(snap.type)
            table.add_row(snap.name or snap.path.name, stype, size, key=pstr)
        header = table.columns[table.name_col]
        if self.brok.snaps:
            header.label = f'{len(self.brok.snaps)}/{self.brok.bcnt} {self("snaps")}'
        else:
            header.label = ''

    def sync_process(self, auto: bool | None = None) -> None:
        '''update process name and button style after optional toggle'''
        if auto is not None:
            self.brok.toggle_mode(auto)
        if isinstance(self.brok.start, bool) or not self.brok.proc:
            t, b, p = self('stop' if self.brok.start else 'start'), 'primary', 'warning'
        else:
            t, b, p = self('manual'), 'success' if self.brok.start else 'warning', 'foreground'
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

    def on_unmount(self) -> None:
        self.brok.config.path.name and self.brok.config.dump(self.brok.config.path)

    def on_key(self, evt: tte.Key) -> None:
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
            self.on_preview()
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
        '''preview image of snapshot specified by SnapConfig.preview_filepath'''
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
        elif isinstance(e, Path):                           # show thumbnail
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
        size = (max(1, self['#tree'].size.width), max(1, min(opts, self['#list'].size.height)))
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
        dt = time.perf_counter() - ts
        self.slog(f'{self(wid)}: {path} ({dt:.2f}s)')
        self.set_state('#' + wid, True)
        self.brok.on_snaps(True)
        self.sync_buttons()

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
            member = [self.select[1]] if self.select[1] else []
            message = '\n- '.join(self.select[:1] + member)
            if await self.push_screen_wait(self.AskYesNo(self('restore') + '?', message)):
                self.on_service(evt.button.id, self.brok.restore, Path(self.select[0]), member)
        elif evt.button.id == 'export':
            record = Path(self.select[0])
            output = Path('~/Downloads') / record.with_suffix('.zip').name
            if path := await self.push_screen_wait(self.AskFilename(output)):
                self.on_service(evt.button.id, self.brok.export, record, path)
        elif evt.button.id == 'delete':
            if await self.push_screen_wait(self.AskYesNo(self('delete') + '?', self.select[0])):
                self.on_service(evt.button.id, self.brok.delete, Path(self.select[0]))

    @textual.on(ttw.DataTable.RowSelected)
    @textual.on(ttw.DataTable.RowHighlighted)
    def on_select(self, evt: textual.message.Message) -> None:
        if self.screen != self.default_screen:
            return
        path = getattr(evt.row_key, 'value', '')
        if self.select[0] == path:
            return
        self.select[0] = path
        tree = self['#list #tree']
        snap = self.brok.snaps.get(self.select[0])
        item = self.select[1]
        tree.clear()
        tree.root.collapse()
        self.sync_buttons()
        self.on_preview()
        if not snap:
            tree.root.label = self.select[1] = ''
            return
        tree.dirs.clear()
        parents = {str(p) for p in Path(item).parents[:-1]} if item else set()
        for rel, (nfile, tsize, mtime) in snap.dirs.items():
            head, tail = os.path.split(rel)
            tree.dirs[rel] = tree.dirs.get(head, tree.root).add(tail, data=(
                rel, size_format(tsize), mtime and strftime(mtime) or ''
            ), expand=rel in parents)
        self.on_tree_node_expanded(parents)
        tree.root.label = f'{snap.fcnt} {self("files")}'
        self.select[1] = item if item in snap.flat else ''

    def on_tree_node_expanded(self, evt: ttw.Tree.NodeExpanded | set[str]) -> None:
        snap = self.brok.snaps[self.select[0]]
        tree = self['#list #tree']
        parents = evt if isinstance(evt, set) else {evt.node.data[0]}
        for rel in snap.flat:
            head, tail = os.path.split(rel)
            if head not in parents:
                continue
            path, dsize, rsize, mtime = snap.info[rel]
            dsize, mtime = size_format(dsize), mtime and strftime(mtime) or ''
            node = tree.dirs.get(head, tree.root).add_leaf(tail, data=(rel, dsize, mtime))
            if rel == self.select[1]:
                self.call_after_refresh(tree.move_cursor, node)
            with suppress(Exception):
                assert rel == self.brok.pimg and path.is_file()
                self.on_preview(path)

    def on_tree_node_selected(self, evt: ttw.Tree.NodeSelected) -> None:
        self.select[1] = rel = evt.node.data[0]
        if not self.brok.pimg:
            return
        snap = self.brok.snaps[self.select[0]]
        if rel not in snap.info:  # dirs
            pimg = str(Path(rel) / self.brok.pimg)
            if pimg not in snap.info:
                return
            rel = pimg
        path = snap.info[rel][0]
        if rel.endswith(self.brok.pimg) and path.is_file():
            self.on_preview(path)


class SnapGUI(ttk.Window, UIMixin):
    '''Graphical UI by tkinter and ttkbootstrap'''
    def __init__(self, *a, **k) -> None:
        success = awareness()   # before root rendered
        super().__init__(title=' '.join(APPINFO), themename='yeti', iconphoto=ICOFILE, hdpi=False)
        if success:
            self.set_wndproc()  # after window created
        UIMixin.__init__(self, *a, **k)

        self.vars = {}
        self.emoji = {}
        for key in self.brok.__aliases__:
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
            def select(v=self.vars[key]) -> None:
                v.set(filedialog.askdirectory(initialdir=v.get()) or v.get())
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
        self['opts.proc'].bind('<Double-1>', lambda e: self.sync_process(True))
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
        tree.column('size', width=120, anchor='e', stretch='no')
        tree.bind('<<TreeviewSelect>>', self.on_select)
        tree.bind('<F2>', lambda e: e.widget.selection() and self.on_rename())
        tree.bind('<F5>', lambda e: self.brok.on_snaps(True))
        tree.bind('<Escape>', lambda e: e.widget.selection_set(()))
        tree.bind('<Delete>', lambda e: e.widget.selection() and self.on_delete())
        tree.bind('<Return>', lambda e: e.widget.selection() and self.on_restore())
        tree.bind('<Double-1>', lambda e: browse(next(iter(e.widget.selection()), None)))
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
        pimg = ttk.Label(frame, name='!preview', anchor='center', cursor='sizing')
        pimg.data = {'path': None, 'image': None, 'tkimg': None}
        tree = ttk.Treeview(frame, name='tree', columns=['size', 'mtime'])
        tree.column('#0', width=300)
        tree.heading('size', anchor='e')
        tree.column('size', width=120, anchor='e', stretch='no')
        tree.heading('mtime', anchor='center')
        tree.column('mtime', width=230, anchor='center', stretch='no')
        tree.bind('<<TreeviewSelect>>', self.on_select)
        tree.bind('<<TreeviewOpen>>', lambda e: self.add_leaf({tree.focus()}))
        tree.tag_bind('c', '<<TreeviewOpen>>', lambda e: tree.item(tree.focus(), tags=('o', )))
        tree.tag_bind('o', '<<TreeviewClose>>', lambda e: tree.item(tree.focus(), tags=('c', )))
        tree.tag_configure('d', foreground='grey')
        tree.tag_configure('o', image=self.from_emoji('📂'))
        tree.tag_configure('c', image=self.from_emoji('📁'))
        tree.tag_configure('f', image=self.from_emoji('📄'))
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
            # FIXME: Fatal Python error: PyEval_RestoreThread: the function must
            # be called with the GIL held, after Python initialization and before
            # Python finalization, but the GIL is released (the current Python
            # thread state is NULL) Python runtime state: initialized
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

    def from_emoji(self, emoji: str) -> ImageTk.PhotoImage:
        '''render unicode icon (text) into tk.PhotoImage'''
        tkfont = font.nametofont('TkDefaultFont', self)
        size = tkfont.metrics()['linespace'] - 2
        for path in map(abspath, [
            '/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf',
            '/System/Library/Fonts/Apple Color Emoji.ttc',
            '$WINDIR/Fonts/seguiemj.ttf',
            tkfont.actual()['family'],
        ]):
            with suppress(Exception):
                f = ImageFont.truetype(path, size)
                break
        else:
            f = ImageFont.load_default(size)
        if emoji not in self.emoji:
            tmp = Image.new('RGBA', (size * 4, size * 4))
            x1, y1, x2, y2 = ImageDraw.Draw(tmp).textbbox((0, 0), emoji, font=f)
            img = Image.new('RGBA', (max(1, x2 - x1), max(1, y2 - y1)))
            ImageDraw.Draw(img).text((-x1, -y1), emoji, font=f, embedded_color=True)
            self.emoji[emoji] = ImageTk.PhotoImage(img)
        return self.emoji[emoji]

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
        for i, pstr in enumerate(p for p in new if p not in old):
            snap = self.brok.snaps[pstr]
            size = size_format(snap.size)
            stype = self(snap.type)
            tree.insert('', i, pstr, text=snap.name or snap.path.name, values=(stype, size))
        if self.brok.snaps:
            tree.heading('#0', text=f'{len(self.brok.snaps)}/{self.brok.bcnt} {self("snaps")}')
        else:
            tree.heading('#0', text='')

    def sync_process(self, auto: bool | None = None) -> None:
        '''update process name and button style after optional toggle'''
        if auto is not None:
            self.brok.toggle_mode(auto)
        if isinstance(self.brok.start, bool) or not self.brok.proc:
            t, b, p = self('stop' if self.brok.start else 'start'), 'primary', 'warning'
        else:
            t, b, p = self('manual'), 'success' if self.brok.start else 'warning', 'default'
        self['snap.!toggle'].config(text=t, bootstyle=b)
        self['opts.pname'].config(bootstyle=p)
        self['opts.proc'].config(bootstyle=p)

    def sync_buttons(self) -> None:
        '''update button states'''
        self.set_state('path.browse#sdir', self.brok.sdir.is_dir())
        self.set_state('path.browse#bdir', self.brok.bdir.is_dir())
        snap = self.brok.snaps.get(next(iter(self['snap.tree'].selection()), None))
        for wid in ['restore', 'export', 'delete']:
            self.set_state(f'list.{wid}', snap)
        self['list.restore'].config(bootstyle='warning' if self.brok.start > 1 else 'default')

    def on_dpichange(self, old: float, new: float) -> None:
        for f in map(font.nametofont, font.names(self)):
            f.config(size=int(f.actual()['size'] / old * new))
        self.on_preview(True)

    def on_destroy(self) -> None:
        '''save config and exit'''
        self.brok.config.path.name and self.brok.config.dump(self.brok.config.path)
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
            self.vars[name].set(self.brok.on_hotkey(name, log=lambda t: btn.config(text=t)))
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

    def add_leaf(self, parents: set[str]) -> None:
        snap = self.brok.snaps[self['snap.tree'].selection()[0]]
        tree = self['list.tree']
        for rel in snap.flat:
            head, tail = os.path.split(rel)
            if head not in parents:
                continue                # lazy loading
            if tree.exists(head + '.dummy'):
                tree.delete(head + '.dummy')
            if tree.exists(rel):
                break                   # already loaded
            path, dsize, rsize, mtime = snap.info[rel]
            tree.insert(head, 'end', id=rel, text=tail, values=(
                size_format(dsize), mtime and strftime(mtime) or ''
            ), tags=('f', ))
            with suppress(Exception):
                assert rel == self.brok.pimg and path.is_file()
                self.on_preview(path)

    def on_select(self, e: tk.Event) -> None:
        '''handle treeview select event'''
        if e.widget.selection() == getattr(e.widget, '_last_selection', None):
            return
        selection = e.widget._last_selection = e.widget.selection()
        if e.widget is self['snap.tree']:
            tree = self['list.tree']
            snap = self.brok.snaps.get(next(iter(selection), None))
            item = set(tree.selection())
            tree.delete(*tree.get_children())
            tree._last_selection = None
            self.on_preview(getattr(snap, 'view', False))
            self.sync_buttons()
            if not snap:
                return tree.heading('#0', text='')
            parents = item | {str(p) for i in item for p in Path(i).parents[:-1]}
            for rel, (nfile, tsize, mtime) in snap.dirs.items():
                head, tail = os.path.split(rel)
                tree.insert(head, 'end', id=rel, text=tail, values=(
                    size_format(tsize), mtime and strftime(mtime) or ''
                ), open=rel in parents, tags=('co'[rel in parents], ))
                nfile and tree.insert(rel, 'end', id=rel + '.dummy', tags=('d', ))
            self.add_leaf(parents)
            tree.heading('#0', text=f'{snap.fcnt} {self("files")}')
            tree.selection_set(tuple(filter(tree.exists, item)))
        elif e.widget is self['list.tree']:
            snap = self.brok.snaps[self['snap.tree'].selection()[0]]
            for rel in selection if self.brok.pimg else []:
                if rel not in snap.info:  # dirs
                    pimg = str(Path(rel) / self.brok.pimg)
                    if pimg not in snap.info:
                        continue
                    rel = pimg
                path = snap.info[rel][0]
                if rel.endswith(self.brok.pimg) and path.is_file():
                    self.on_preview(path)
                    break
            else:
                if not selection:
                    self.on_preview(snap.view)

    def on_preview(self, e: tk.Event | Image.Image | Path | bool = False) -> None:
        '''preview image of snapshot specified by SnapConfig.preview_filepath'''
        pimg = self['list.!preview']
        data = pimg.data
        last = data['image']
        if isinstance(e, Image.Image):                      # show screenshot
            if hasattr(e, 'filename'):
                return self.on_preview(Path(e.filename))
            data.update({'path': None, 'image': e})
            if not last:
                return pimg.grid()
        elif isinstance(e, Path):                           # show thumbnail
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
        size = (
            max(1, self['list.tree'].winfo_width() - 4),
            max(1, min(self['opts'].winfo_height(), self['list'].winfo_height() / 2))
        )
        if size != data['image'].size:                      # resize widget
            tmp = data['image'].copy()
            tmp.thumbnail(size, Image.Resampling.LANCZOS)   # FIXME: debounce
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
            self.brok.on_snaps(True)
            if 'create' not in widget and 'delete' not in widget:
                self.sync_buttons()
        threading.Thread(target=wrapper, name=widget, daemon=True).start()

    def on_create(self) -> None:
        '''create a manual snapshot'''
        self.on_service('snap.create', self.brok.create)

    def on_restore(self) -> None:
        '''restore the selected snapshot'''
        if self.brok.start > 1:
            self.log('Do NOT restore snapshots when game is running!', 'warning')
        record = Path(self['snap.tree'].selection()[0])
        member = self['list.tree'].selection()
        message = '\n- '.join([str(record), *member[:10]])
        if len(member) > 10:
            message += f'\nand {len(member) - 10} more paths'
        if messagebox.askyesno(self('restore') + '?', message, icon='warning'):
            self.on_service('list.restore', self.brok.restore, record, member)

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

    def on_rename(self) -> None:
        '''rename the selected snapshot'''
        snap = self.brok.snaps.get(self['snap.tree'].selection()[0])
        if snap and snap.path and (name := simpledialog.askstring(
            self('rename'), snap.path.name + (f'\n - {snap.name}' if snap.name else '')
        )) not in (None, snap.name):
            with self.brok._mutex:
                snap.name = name
                snap.dump(snap.path)
            self['snap.tree'].item(str(snap.path), text=snap.name or snap.path.name)


def find_mods(root: Path, gid: int = 0) -> dict[int, list[tuple[int, int, str, Path]]]:
    '''search mods under root and filter by optional game id'''
    gstr = gid and str(gid) or ''
    mods, cache = {}, {}
    for acf in root.parent.glob('appworkshop_*.acf'):
        with suppress(Exception), open(acf, encoding='utf-8') as f:
            cache.update(json.loads(re.sub(r'"(\d+)"(?!:)', lambda m: m.group(1), re.sub(
                r'([}"])(\s*)(["{])', lambda m: m.expand(
                    f'\\1{":" if "{" in m.group() or "\n" not in m.group() else ","}\\3'
                ), '{' + f.read() + '}'
            )))['AppWorkshop']['WorkshopItemsInstalled'])
    for game in root.iterdir() if root.is_dir() else []:
        if not game.is_dir() or not game.name.isdigit() or (gstr and gstr != game.name):
            continue
        mods[int(game.name)] = lst = []
        for workshop in game.iterdir():
            if not workshop.is_dir() or not workshop.name.isdigit():
                continue
            wid = int(workshop.name)
            for mod in (workshop / 'mods').iterdir():
                if not mod.is_dir():
                    continue
                if workshop.name in cache:
                    size = cache[workshop.name]['size']
                    mtime = cache[workshop.name]['timeupdated']
                else:
                    size = sum(map(lambda p: p.is_file() and p.stat().st_size, mod.rglob('*')))
                    mtime = mod.stat().st_mtime
                lst.append((wid, mtime, mod.name, size, mod))
    if not mods:
        if not gid:
            logger.warning(f'No workshops under {root}')
        else:
            logger.warning(f'No workshops under {root} for game {gid}')
    return mods


def steam_cmd(cmd: Path, yes: bool = False) -> Path | None:
    '''
    Validate SteamCMD executable or download and install if not found.

    Args:
        cmd (Path): path to steamcmd.exe or steamcmd.sh
        yes (bool): assume answer "yes" to any prompts

    Returns:
        Path: path to installed SteamCMD executable
        None: not found or install failed
    '''
    if cmd.is_file() and 'version' in exec_result(cmd, '--help').lower():
        return cmd
    if not yes and input(
        f'SteamCMD not found: install to {cmd.parent}?\n'
        'About 700KB to download and 100MB after installed\nY/[N]: '
    ).lower() != 'y':
        return logger.error(f'Invalid SteamCMD: {cmd}')
    suffix = {'Darwin': '_osx.tar.gz', 'Linux': '_linux.tar.gz'}.get(platform.system(), '.zip')
    cmd = cmd.parent / f'steamcmd{steam_cmd.suffix}'
    try:
        path, msg = urllib.request.urlretrieve(
            f'https://steamcdn-a.akamaihd.net/client/installer/steamcmd{suffix}',
            cmd.parent.mkdirp() / f'steamcmd{suffix}'
        )
        logger.info(msg.as_string().strip())
        shutil.unpack_archive(path, cmd.parent)
        logger.warning('It may take a few minutes to setup SteamCMD...')
        lprint(line_buffer(cmd, '--help'))
        return cmd
    except Exception:
        cmd.unlink(missing_ok=True)
        logger.exception('Install SteamCMD failed')


steam_cmd.suffix = '.exe' if WINDOWS else '.sh'


def steam_post(api: str, data: dict, timeout: int = 3) -> dict:
    with urllib.request.urlopen(
        urllib.parse.urljoin('https://api.steampowered.com', api),
        urllib.parse.urlencode(data).encode(), timeout=timeout
    ) as rep:
        data = json.load(rep)['response']
        assert data['result'] == 1
        return data


def local_link(path: str | Path, title: typing.Any = None) -> str:
    '''markup for local files or folders'''
    return f'[link={abspath(path).as_uri()}]{title or path}[/link]'


def steam_link(id: int, title: typing.Any = None) -> str:
    '''markup for steam collection / workshop page'''
    return f'[link=https://steamcommunity.com/sharedfiles/filedetails/?id={id}]{title or id}[/link]'


def query_wids(*ids: int) -> typing.Iterator[int]:
    '''query steam workshop ids from collection id'''
    if not ids:
        return
    req = {'collectioncount': len(ids)}
    for i, cid in enumerate(ids):
        req[f'publishedfileids[{i}]'] = cid
    rep = steam_post('ISteamRemoteStorage/GetCollectionDetails/v1', req)
    for collection in rep['collectiondetails']:
        cid = int(collection['publishedfileid'])
        if collection['result'] != 1:  # not a CollectionID
            yield cid
        else:
            wids = [i['publishedfileid'] for i in collection['children'] if i['filetype'] == 0]
            logger.info(f'Found collection {steam_link(cid)} containing {len(wids)} workshops')
            yield from map(int, wids)


def query_gids(*ids: int) -> typing.Iterator[tuple[int, int]]:
    '''query steam game id from workshop id'''
    if not ids:
        return
    req = {'itemcount': len(ids)}
    for i, wid in enumerate(ids):
        req[f'publishedfileids[{i}]'] = wid
    rep = steam_post('ISteamRemoteStorage/GetPublishedFileDetails/v1', req)
    assert rep['resultcount'] == req['itemcount']
    for workshop in rep['publishedfiledetails']:
        wid = int(workshop['publishedfileid'])
        if workshop['result'] != 1:  # not a WorkshopID
            continue
        gid = workshop['consumer_app_id']
        logger.info(f'Found game {gid} workshop {steam_link(wid)} {workshop["title"]}')
        yield wid, gid


def run_modman(args: argparse.Namespace, _: SnapBroker) -> None:
    '''list or download steam workshops'''
    if not args.mdir:
        args.mdir = args.exec.parent
    mparts, sparts = args.mdir.parts, Path('steamapps/workshop/content').parts
    for i in range(len(mparts) - len(sparts) + 1):
        if mparts[i:i + len(sparts)] == sparts:         # crop until suffix
            cdir = Path(*mparts[:i + len(sparts)])
            break
    else:                                               # pad with suffix
        match = 0
        for i in range(1, min(len(mparts), len(sparts)) + 1):
            if mparts[-i:] == sparts[:i]:
                match = i
        cdir = Path(*mparts[:-match if match else None]).joinpath(*sparts)
    logger.debug(f'Using {cdir}')
    if not args.ids and not args.sync and not args.copy:
        for gid, lst in find_mods(cdir, args.gid).items():
            nwid, size = len({d[0] for d in lst}), sum({d[0]: d[3] for d in lst}.values())
            rprint(f'Game {gid} found {size_format(size):>9s} {nwid} workshops and {len(lst)} mods')
            if not args.verbose:
                continue
            lst = sorted(lst, key=lambda d: d[{'TIME': 1, 'NAME': 2, 'SIZE': 3}.get(args.sort, 0)])
            for i, (wid, mtime, name, size, path) in enumerate(lst):
                slink, flink = steam_link(wid), local_link(path, name)
                if mtime > tree_mtime(path, path.stat().st_mtime):
                    flink = f'[dim]{flink}[/dim]'
                rprint(f'   - [{slink}] {size_format(size):>9s} {strftime(mtime)} {flink}')
        return
    try:
        wids = dict(query_gids(*query_wids(*set(args.ids))))
    except Exception as e:
        wids = dict.fromkeys(args.ids, args.gid) if args.gid else {}
        logger.error(f'Query GID for {args.ids} failed: {e}')
        if args.gid:
            logger.info(f'Using GID {args.gid} for {args.ids}')
        else:
            logger.warning(f'Skip downloading {args.ids}, please specify GID')
    if args.sync:
        wids.update({d[0]: gid for gid, lst in find_mods(cdir, args.gid).items() for d in lst})
    if wids and (cmd := steam_cmd(args.exec, args.yes)):
        logger.info(f'Using {cmd} to download {len(wids)} workshops')
        tpl, done = re.compile(r'Success[^\d]*(\d+).*\1[^\d]*(\d+)'), {}
        # SteamCMD logs maybe messy so regex is used to extract valid information
        with tempfile.NamedTemporaryFile(delete_on_close=False) as script:
            script.write('\n'.join([f'force_install_dir "{args.mdir}"', 'login anonymous'] + [
                f'workshop_download_item {gid} {wid}' for wid, gid in wids.items()
            ]).encode())
            script.close()
            for line in line_buffer(cmd, f'+runscript "{script.name}"', '+quit', check=False):
                if 'error' in line.lower():
                    logger.error(line[0 if args.verbose else 7:])
                elif args.verbose:
                    logger.debug(line)
                if 'success' not in line.lower():
                    continue
                for wid in (matched := dict(
                    map(int, pair) for pair in tpl.findall(line)
                )).keys():
                    if wid not in wids:
                        matched = {}
                if not matched and not args.verbose:
                    logger.info(line)
                for wid, size in matched.items():
                    if wid in done:
                        continue
                    size = size_format(done.setdefault(wid, size))
                    logger.info(f'{len(done)}/{len(wids)} downloaded {steam_link(wid)} {size}')
    if args.copy:
        games = find_mods(cdir)
        if args.gid and args.gid not in games:
            return logger.error(f'Game ID {args.gid} not found')
        elif not args.gid and len(games) == 1:
            args.gid = list(games)[0]
        elif not args.gid:
            return logger.warning(f'Choose game ID from {list(games)}')
        mods = {path: name for w, m, name, s, path in games[args.gid]}
        olds = {m.name for m in os.scandir(args.copy) if m.is_dir()}
        logger.info(f'Syncing {len(mods)} mods for game {args.gid}')
        with poolmap(mods) as pmap:
            for dst in pmap(lambda src, dst: shutil.copytree(
                src, args.copy / dst, dirs_exist_ok=True
            ), mods.keys(), mods.values()):
                if dst.name in olds:
                    logger.info(f'[yellow]~[/] {local_link(dst, dst.name)}')
                else:
                    logger.info(f'[green]+ {local_link(dst, dst.name)}[/]')
        for mod in sorted(olds.difference(mods.values())):
            logger.warning(f'[red]- {local_link(args.copy / mod, mod)}[/]')


try:
    from packaging.version import Versio as Version
except Exception:
    @functools.total_ordering
    class Version:
        '''Simple semantic version parser like packaging.version.Version'''
        _tpl = re.compile(
            r'(?P<major>0|[1-9]\d*)\.'
            r'(?P<minor>0|[1-9]\d*)\.'
            r'(?P<patch>0|[1-9]\d*)'
            r'(?:\-(?P<pre>[\w-]+(?:\.[\w-]+)*))?'
            r'(?:\+(?P<build>[\w-]+(?:\.[\w-]+)*))?'
        )

        def __init__(self, version: str) -> None:
            m = self._tpl.search(str(version))
            if not m:
                raise ValueError(f'Invalid semantic version {version}')
            self._ver = tuple(int(i) for i in m.groups()[:3]) + m.groups()[3:]
            self._raw = m.group()

        def __str__(self) -> str: return self._raw

        def __repr__(self) -> str: return f"<Version('{self._raw}')>"

        def __hash__(self) -> int: return hash(self._ver)

        def __eq__(self, o: typing.Any) -> bool:
            if not isinstance(o, Version):
                try:
                    return self == Version(o)
                except ValueError:
                    return NotImplemented
            return self._ver[:4] == o._ver[:4]

        def __lt__(self, o: typing.Any) -> bool:
            if not isinstance(o, Version):
                try:
                    return self < Version(o)
                except ValueError:
                    return NotImplemented
            if self._ver[:3] != o._ver[:3]:
                return self._ver[:3] < o._ver[:3]
            if self._ver[3] is not None and o._ver[3] is not None:
                aparts, bparts = self._ver[3].split('.'), o._ver[3].split('.')
                for a, b in zip(aparts, bparts):
                    if a != b:
                        if a.isdigit() and b.isdigit():
                            return int(a) < int(b)
                        return True if a.isdigit() else (False if b.isdigit() else a < b)
                return len(aparts) < len(bparts)
            return self._ver[3] is not None and o._ver[3] is None


def asset_name(
    tpl: str = '{name}-{ver}-{plat}-{arch}{ext}', version: str | Version = VERSION
) -> str:
    system, machine = platform.system().lower(), platform.machine().lower()
    if machine in ('arm', 'armv8', 'aarch64'):
        machine = 'arm64'
    if machine in ('x64', 'win64', 'x86_64', 'x86-64'):
        machine = 'amd64'
    return tpl.format(
        name=PROJECT, ver=version, ext='.exe' if WINDOWS else '',
        plat='macos' if system == 'darwin' else system, arch=machine or 'amd64',
    ).strip()


def run_update(args: argparse.Namespace, _: SnapBroker) -> None:
    '''check and download updates'''
    current = abspath(sys.argv[0])  # follow symlink
    if args.gen:
        if not ONEFILE:
            for path in (BASEDIR / 'build').glob(f'{PROJECT}*{".exe" if WINDOWS else ""}'):
                if path.is_file():
                    break
            else:
                return logger.error('Could not generate version info from source file')
        else:
            path = current
        if path.parent != args.gen:
            src, path = path, shutil.copy2(path, args.gen.mkdirp() / path.name)
            logger.info(f'Copied {src} => {path}')
        if '{name}' not in args.match:
            args.match = ('{name}-' + args.match).replace('--', '-')
        info = dump({'tag_name': VERSION, 'assets': [{
            'name': asset_name(args.match),
            'size': path.stat().st_size,
            'digest': 'md5:' + hashlib.md5(path.read_bytes()).hexdigest(),
            'updated_at': strftime(path.stat().st_mtime, '%FT%XZ'),
            'browser_download_url': path.name,
        }]}, args.gen / 'latest')
        logger.info(info.read_text())
        return 'Test with --url=' + info.parent.as_uri()
    if args.ppid:
        logger.info(f'Welcome to v{VERSION}')
        if not args.keep:
            with suppress(psutil.NoSuchProcess, psutil.AccessDenied):
                psutil.Process(args.ppid).wait(5)
            with suppress(PermissionError):
                current.with_suffix('.old').unlink(missing_ok=True)
        if not args.args:
            return logger.warning('Nothing to do')
        argv = [str(current), *args.args]
        return os.execv(argv[0], argv) if ONEFILE else logger.debug(f'Running {argv}')
    try:
        with urllib.request.urlopen(
            f'https://api.github.com/repos/{"/".join(args.repo)}/releases/latest'
            if not args.url else urllib.parse.urljoin(args.url, 'latest'), timeout=3
        ) as rep:
            data = json.load(rep)
            assets = data['assets']
        newver = Version(data['tag_name'])
    except Exception:
        return logger.exception(f'Failed to fetch version from {args.url or args.repo}')
    if not args.force and newver <= VERSION:
        return logger.info(f'Skip version v{newver}')
    logger.info(f'Found verseion v{newver} (current v{VERSION})')
    suffix = asset_name(args.match, newver)
    for asset in assets:
        if asset['name'].endswith(suffix):
            break
    else:
        return logger.error('\n - '.join([
            'No candidates to download, please choose from:',
            *(asset['name'] for asset in assets)
        ]))
    logger.info(f'Found {asset["name"]} {size_format(asset["size"])} {asset["updated_at"]}')
    target = BASEDIR / Path(asset['name']).name
    digest, _, hval = str(asset['digest']).lower().partition(':')
    hasher = hashlib.new(digest) if digest in hashlib.algorithms_available else None
    try:
        with urllib.request.urlopen(
            urllib.parse.urljoin(args.url, asset['browser_download_url'])
            if args.url else asset['browser_download_url'], timeout=10
        ) as rep, open(target, 'wb') as f:
            while buf := rep.read(shutil.COPY_BUFSIZE):
                hasher and hasher.update(buf)
                f.write(buf)
        if hasher and hasher.hexdigest() != hval:
            target.unlink()
            return logger.error(f'Download failed: {digest} does not match')
    except Exception:
        target.unlink(missing_ok=True)
        return logger.exception('Failed to download executable')
    try:
        if not WINDOWS:
            target.chmod(current.stat().st_mode if ONEFILE else 0o755)
        # this program is already compiled with python -u flag thus unbuffered
        # here we use line_buffer just to rprint log lines and check returncode
        lprint(line_buffer(target, 'test', force=False))
    except Exception:
        target.unlink()
        return logger.error('Self-test failed: skip this version')
    try:
        if not ONEFILE:                             # running from script
            return logger.warning(f'Nothing to do: tested {target}')
        with suppress(PermissionError):
            if not args.keep:
                current.unlink()
                logger.info(f'Removed {current}')
        if current.is_file():                       # keep or file locked
            backup = current.with_suffix('.old')
            backup.unlink(missing_ok=True)
            shutil.move(current, backup)
            logger.info(f'Backup {current} => {backup}')
        if Path(sys.argv[0]).is_symlink():          # link to new version
            os.unlink(sys.argv[0])
            try:
                os.symlink(target, sys.argv[0])
                logger.info(f'Update {sys.argv[0]} => {target}')
            except OSError as e:
                if e.winerror != 1314:
                    raise
                logger.warning('Failed to creat symlink on Windows')
        elif VERSION not in current.stem:           # replace executable
            logger.info(f'Rename {target} => {current}')
            target = shutil.move(target, current)
        else:
            logger.warning(f'Skipped {current} with version info')
    except Exception:
        target.unlink()
        return logger.exception('Failed to replace executable')
    argv = [str(target), sys.argv[1], '--ppid', str(os.getpid()), *args.args]
    logger.info(f'Goodbye from v{VERSION}')
    return os.execv(argv[0], argv) if ONEFILE else logger.debug(f'Running {argv}')


def run_pack(*a) -> None:
    '''find and rename and test artifacts'''
    base = BASEDIR / 'build' / 'GameSnap'
    dst = (BASEDIR / 'dist').mkdirp() / asset_name()
    dzip = dst.with_suffix('.zip') if WINDOWS else Path(f'{dst}.zip')
    for src in base.parent.iterdir():
        if src.suffix.lower() in {'.msi', '.dmg', '.appimage'}:
            shutil.copy2(src, dst.with_suffix(src.suffix))
    if (exe := base.with_suffix('.exe')).is_file():     # windows onefile
        shutil.copy2(exe, dst)
        with PZipFile(dzip, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.write(exe, exe.name)
            if (gui := base.with_name('GameSnapGUI.exe')).is_file():
                zf.write(gui, gui.name)
                shutil.copy2(gui, dst.with_name(dst.name.replace('GameSnap', 'GameSnapGUI')))
    if (app := base.with_suffix('.app')).is_dir():      # macOS app bundle
        macho = app / f'Contents/MacOS/{app.stem}'
        lprint(line_buffer(macho, 'test', force=False))
        if ditto := shutil.which('ditto'):
            lprint(line_buffer(ditto, '-c', '-k', '--keepParent', app, dzip))
        else:
            shutil.make_archive(dst.with_suffix(''), 'zip', app.parent, app.name)
    if (elf := base.with_suffix('')).is_file():         # linux onefile
        shutil.copy2(elf, dst)
        with PZipFile(dzip, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.write(elf, elf.name)
    lprint(tree_files(base.parent, level=2))
    lprint(tree_files(dzip.parent))
    if dzip.is_file():
        logger.info(f'Packed {dzip}')
    if dst.is_file():
        logger.info(f'Copied {dst}')
        lprint(line_buffer(dst, 'test', force=False))


def run_test(*a) -> None:
    '''self-test and report errors'''
    with tempfile.TemporaryDirectory() as folder:
        base = Path(folder)
        sdir = base / 'src'
        bdir = base / 'dst'
        hdir = bdir / 'blob'
        code = sdir / 'code.py'
        text = sdir / 'text.md'
        out = base / 'export.zip'
        cfg = base / CFGFILE.name
        logger.info('TEST START'.center(20, '-'))
        try:
            brok = SnapBroker(cfg)
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
            code.write_text('print("hello world")')
            brok['excs'] = '*.py'
            brok.create()
            brok['excs'] = ''
            assert len(brok.summary()) == 1, 'no snapshot created'
            assert brok.summary()[0].fcnt == 0, 'files not excluded'

            text.write_text('foo')
            brok['incs'] = '*.md'
            brok.create()
            brok['incs'] = ''
            assert len(brok.summary()) == 2, 'no snapshot created'
            assert brok.summary()[0].fcnt == 1, 'files not included'
            assert len(list(hdir.iterdir())) == 1, 'blob files not created'

            # TODO: sdir is a file

            logger.info('watchdog'.center(20, '-'))
            brok['wintv'] = 1
            brok['watch'], brok['sched'] = True, False
            brok.start = True
            text.write_text('bar')
            time.sleep(1.5)
            brok.start = False
            assert len(brok.summary()) == 3, 'new snapshot not created'

            logger.info('schedule'.center(20, '-'))
            brok['sintv'] = 1
            brok['watch'], brok['sched'] = False, True
            brok.start = True
            time.sleep(1.5)
            brok.start = False
            assert len(brok.summary()) == 4, 'new snapshot not created'

            logger.info('summary'.center(20, '-'))
            snaps = brok.summary()
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
            assert len(brok.summary()) == 2, 'lsnapshot not deleted'

            logger.info('max count'.center(20, '-'))
            brok['bcnt'] = 1
            brok.create()
            assert len(brok.summary()) == 1, 'snapshots not auto cleaned'

            logger.info('file missing'.center(20, '-'))
            for x in hdir.iterdir():
                x.unlink()
            os.utime(brok.summary()[0].path)  # force cache miss
            data = list(brok.summary()[0].info.values())[0]
            assert not data[0].exists() or not any(data[1:]), 'blob files missing'
        except Exception:
            logger.exception('\n'.join(tree_files(base)))
            return 1
        finally:
            logger.info('TEST DONE'.center(20, '-'))


def run_delete(args: argparse.Namespace, brok: SnapBroker) -> str:
    rst: list[Path] = brok.delete_all() if args.all else []
    rst += [brok.delete(path) for path in args.records]
    rst += [brok.delete_latest() for _ in range(args.latest)]
    rst += [brok.delete_oldest() for _ in range(args.oldest)]
    if not rst:
        return 'No snapshots to delete'
    return ('\n - ' if len(rst) > 1 else ' ').join(['Deleted', *map(str, rst)])


def run_summary(args: argparse.Namespace, brok: SnapBroker) -> str:
    snaps = brok.summary(detail=bool(args.size))
    for snap in snaps:
        if args.size:
            rsize, dsize = sum(v[1] for v in snap.flat.values()), snap.size
            size = f'{size_format(dsize):>9s} <= {size_format(rsize):>9s} ({dsize / rsize:.2%})'
        else:
            size = ''
        rprint(strftime(snap.time), f'{snap.type:6s}', snap.fcnt, 'files', size)
        if snap.name:
            rprint(f' - rename: {snap.name}')
        rprint(f' - source: {snap.sdir}\n - hashes: {snap.hdir}\n - record: {snap.path}\n')
    if not snaps:
        return f'No snapshots under {brok.bdir}'
    msg = f'Found {len(snaps)} of {brok.bcnt} max'.ljust(len(strftime()) + 1)
    if args.size:
        msg += f' Total {len({k for snap in snaps for k in snap.flat})} files'
        dsize = sum(itertools.chain(
            (snap.path.stat().st_size for snap in snaps),
            {k: v[1] for snap in snaps for k, v in snap.info.items()}.values()
        ))
        msg += f' {size_format(dsize):>9s}'
    return msg


def run_daemon(args: argparse.Namespace, brok: SnapBroker) -> None:
    if not any(brok.srvs.values()):
        logger.warning(f'Broker has nothing to do: check {args.config}')
    logger.info(f'Running {PROJECT} in background: press Ctrl+C to stop')
    with pidfile(args.pid), redirect(args.log):
        while True:
            time.sleep(1)


def run_gui(_: argparse.Namespace, brok: SnapBroker) -> None:
    try:
        SnapGUI(brok).mainloop()
    except Exception as e:
        console(True)
        raise e


class ArgumentParser(argparse.ArgumentParser):
    def __init__(self, *a, **k) -> None:
        if RichHelpFormatter:
            k.setdefault('formatter_class', RichHelpFormatter)
        self.parents = k.get('parents', [])
        super().__init__(*a, **k)

    def __iter__(self) -> typing.Iterator[argparse.Action]: yield from self.walk(self)

    @classmethod
    def walk(cls, parser: argparse.ArgumentParser) -> typing.Iterator[argparse.Action]:
        for action in parser._actions:
            if action.nargs == argparse.PARSER:
                for subparser in (action.choices or {}).values():
                    yield from cls.walk(subparser) if subparser else []
            else:
                yield action

    def add_subparser(self, name: str, **k) -> typing.Self:
        if not hasattr(self, 'subparsers'):
            self.subparsers = self.add_subparsers(title='Supported commands', metavar='')
        k.setdefault('parents', self.parents)
        k.setdefault('add_help', not any([
            isinstance(a, argparse._HelpAction) for p in k['parents'] for a in self.walk(p)
        ]))
        k.setdefault('description', k.get('help'))
        return self.subparsers.add_parser(name, **k)

    def parse_args(self, *a, **k) -> argparse.Namespace:
        args, argv = self.parse_known_args(*a, **k)
        dest = next((a.dest for a in self if a.nargs == argparse.REMAINDER), '')
        if argv and dest in args:
            setattr(args, dest, getattr(args, dest) + argv)
        elif argv:
            self.error(argparse._('unrecognized arguments: %s') % ' '.join(argv))
        return args

    def print_help(self, *a, **k) -> None:
        console(True)
        super().print_help(*a, **k)

    def error(self, *a, **k) -> None:
        console(True)
        super().error(*a, **k)


def make_parser() -> ArgumentParser:
    def urlparse(s: str) -> str:
        url = urllib.parse.urlparse(s)
        if not url.scheme or not any([url.netloc, url.path]):
            raise argparse.ArgumentTypeError(f'{s} is not a valid URL')
        return url.geturl() + '/'

    def filepath(s: str) -> Path:
        path = abspath(s)
        if not path.is_file():
            raise argparse.ArgumentTypeError(f'{s} is not a file')
        return path

    def parameter(s: str) -> tuple[str, typing.Any]:
        key, _, val = s.partition('=')
        if not key.strip():
            raise argparse.ArgumentTypeError(f'{s} is not a parameter')
        with suppress(json.JSONDecodeError):
            val = json.loads(val.strip() or 'true')
        return key.strip().lower(), val.strip() if isinstance(val, str) else val

    class Preset(argparse.Action):
        def __call__(self, p, ns, values, os=None):
            try:
                setattr(ns, self.dest, (filepath(values[0]), values[1]))
            except argparse.ArgumentTypeError as e:
                raise argparse.ArgumentError(self, e)

    version = ' '.join(APPINFO[1:])
    if ONEFILE and (gui := isgui()) is not None:
        version += f' (Windows {"CG"[gui]}UI subsystem)'

    gopt = argparse.ArgumentParser(add_help=False)
    g = gopt.add_argument_group('Global Options')
    g.add_argument('-h', '--help', action='help', help='show this help message and exit')
    g.add_argument('-V', '--version', action='version', version=version)
    g.add_argument('-v', '--verbose', action='count', help='show more log message')
    g.add_argument('-c', '--console', metavar='MODE', choices=['show', 'hide', 'auto'],
                   help='select mode from [[i]%(choices)s[/]] (default: [i]auto[/])')
    g.add_argument('-s', '--serial', action='store_true', help='disable concurrency')

    bopt = argparse.ArgumentParser(add_help=False)
    g = bopt.add_argument_group('Broker Options')
    g.add_argument('--config', metavar='PATH', type=abspath, default=CFGFILE,
                   help=f'load config from file (default: [i] {relpath(CFGFILE)}[/])')
    g.add_argument('--preset', metavar=('FILE', 'GAME'), nargs=2, action=Preset,
                   help='load [i]GAME[/] preset from [i]FILE[/]')
    g.add_argument('--param', type=parameter, metavar='KEY[=VAL]', nargs='+', action='extend',
                   help='override config without saving and applied after [i]--preset[/]')

    parser = ArgumentParser(
        prog=PROJECT, parents=[gopt, bopt], description=DESCSTR,
        add_help=False, epilog='See [cyan]<command> -h[/] for more helps'
    )
    parser.set_defaults(task=run_gui, func=None)

    s = parser.add_subparser('gui', help='run graphical user interface (default)')
    s.set_defaults(task=run_gui)

    s = parser.add_subparser('tui', help='run terminal user interface')
    s.add_argument('--css', type=filepath, nargs='+', help='load .tcss for debugging')
    s.set_defaults(task=lambda a, b: SnapTUI(css=a.css, brok=b).run())

    s = parser.add_subparser('daemon', help='run without user interface')
    s.add_argument('--log', metavar='PATH', type=abspath, help='redirect stdout to logfile')
    s.add_argument('--pid', metavar='PATH', nargs='?', type=abspath,
                   const=abspath(tempfile.gettempdir()) / PROJECT.lower(),
                   help='use and verify pidfile (default: [i]%(const)s[/])')
    s.set_defaults(task=run_daemon)

    s = parser.add_subparser('create', help='create a manual snapshot')
    s.set_defaults(func=lambda a, b: f'Created {b.create()}')

    s = parser.add_subparser('restore', help='recover a snapshot to [i]SDIR[/]')
    s.add_argument('record', type=filepath, help='path to the snapshot')
    s.add_argument('member', nargs='*', help='filter files by relative path')
    s.add_argument('--sdir', help='source directory (default: [i]SDIR[/] from record)')
    s.set_defaults(func=lambda a, b: f'Restored {b.restore(a.record, a.member, a.sdir)}')

    s = parser.add_subparser('export', help='export a snapshot to zip file')
    s.add_argument('record', type=filepath, help='path to the snapshot')
    s.add_argument('output', type=abspath, help='path to the zip file')
    s.set_defaults(func=lambda a, b: f'Exported {b.export(a.record, a.output)}')

    s = parser.add_subparser('delete', help='delete snapshots and unused blob files')
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument('records', metavar='PATH', nargs='*', default=[], type=filepath,
                   help='remove specified snapshots')
    g.add_argument('--latest', metavar='N', nargs='?', const=1, default=0, type=int,
                   help='remove latest snapshots (default: [i]%(const)s[/])')
    g.add_argument('--oldest', metavar='N', nargs='?', const=1, default=0, type=int,
                   help='remove oldest snapshots (default: [i]%(const)s[/])')
    g.add_argument('--all', action='store_true', help='remove all snapshots')
    s.set_defaults(func=run_delete)

    s = parser.add_subparser('summary', help='list snapshots under backup directory')
    s.add_argument('--size', action='store_true', help='show detail of size info')
    s.set_defaults(func=run_summary)

    steamcmd = shutil.which('steamcmd' + steam_cmd.suffix) or shutil.which('steamcmd')
    steamcmd = abspath(steamcmd or f'~/.steamcmd/steamcmd{steam_cmd.suffix}')
    s = parser.add_subparser('modman', help=run_modman.__doc__, parents=[gopt])
    s.add_argument('ids', metavar='ID', nargs='*', type=int,
                   help='collection or workshop IDs to download / update')
    s.add_argument('--exec', metavar='PATH', type=filepath, default=steamcmd,
                   help='path to SteamCMD exec (default: %(default)s)')
    s.add_argument('--mdir', type=abspath,
                   help=f'path to download mods (default: [i]{steamcmd.parent}[/])')
    s.add_argument('--copy', metavar='PATH', type=abspath,
                   help='search [i]GID[/] mods under [i]MDIR[/] and copy to here')
    s.add_argument('--sync', action='store_true', help='update all mods under [i]MDIR[/]')
    s.add_argument('--sort', metavar='ATTR', choices=['ID', 'TIME', 'NAME', 'SIZE'], default='ID',
                   help='sort mods by [[i]%(choices)s[/]] (default: %(default)s)')
    s.add_argument('--gid', type=int, help='specify Steam Game ID')
    s.add_argument('--yes', action='store_true', help='install SteamCMD if not found')
    s.set_defaults(func=run_modman)

    s = parser.add_subparser('update', help=run_update.__doc__, parents=[gopt])
    s.add_argument('args', nargs=argparse.REMAINDER, help='arguments to pass through')
    s.add_argument('--match', metavar='TPL',
                   default=inspect.signature(asset_name).parameters['tpl'].default,
                   help='asset template [[i]name, plat, arch, ver, ext[/]] (default %(default)s)')
    s.add_argument('--force', action='store_true', help='force update to legacy versions')
    s.add_argument('--keep', action='store_true', help='do not clean up old versions')
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument('--url', type=urlparse, help='fetch latest version from URL')
    g.add_argument('--repo', metavar=('USER', 'REPO'), nargs=2, default=REPOURL.split('/')[-2:],
                   help='fetch latest release from github (default: %(default)s)')
    g.add_argument('--ppid', metavar='PID', type=int, help='parent process id to wait for')
    g.add_argument('--gen', metavar='DIR', type=abspath, help='generate version info')
    s.set_defaults(func=run_update)

    s = parser.add_subparser('test', help=run_test.__doc__, parents=[gopt])
    s.set_defaults(task=run_test)
    return parser


def main() -> int:
    try:
        if WINDOWS:
            signal.signal(signal.SIGBREAK, signal.default_int_handler)
        args = make_parser().parse_args()
        console({'show': True, 'hide': False}.get(
            args.console, args.func or args.task is not run_gui
        ), ' '.join(APPINFO[:0 if args.func else 2]))
        if args.func in {run_modman, run_update} or args.task is run_test:
            args.config = None
        elif not args.config.is_file() and args.config != CFGFILE:
            logger.warning(f'{args.config} does not exist: fallback to {CFGFILE}')
            args.config = CFGFILE
        if args.serial:
            global POOLMAP
            POOLMAP = False
        brok = SnapBroker(args.config, verbose=args.verbose, srv=not args.func)
        if args.preset:
            logger.info(brok.preset(args.preset[0], args.preset[1])['result'])
        if args.param:
            brok.update(dict(args.param))
        if not args.func:
            return args.task(args, brok)
        ts = time.perf_counter()
        if ret := (args.func(args, brok) or '').strip():
            rprint(f'{ret}\n(used time: {time.perf_counter() - ts:.3f}s)')
    except KeyboardInterrupt:
        return 130  # 128 + SIGINT
    except Exception:
        logger.exception('')
        return 1


__all__ = [
    # classes
    'Path', 'PZipFile', 'SnapTUI', 'SnapGUI',
    'SnapConfig', 'SnapRecord', 'SnapEngine', 'SnapRouter', 'SnapBroker',
    # contants
    'ONEFILE', 'BASEDIR', 'DATADIR', 'CFGFILE', 'POOLMAP', 'APPINFO',
    # functions
    'load', 'dump', 'tailf', 'pidof', 'where', 'isgui',
    'browse', 'abspath', 'capture', 'console', 'poolmap',
    'pidfile', 'redirect', 'strftime', 'awareness',
    'play_sound', 'size_format', 'exec_result', 'exec_command', 'line_buffer',
]


def __dir__() -> tuple[str]: return __all__


if __name__ == '__main__':
    sys.exit(main())
