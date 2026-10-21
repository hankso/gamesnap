# GameSnap

[![Build & Release](https://github.com/hankso/gamesnap/actions/workflows/ci.yml/badge.svg)](https://github.com/hankso/gamesnap/actions/workflows/ci.yml)

> 像 Git 一样为游戏存档创建**增量快照**，随时备份、恢复、导出。

GameSnap 是一个单文件 Python 应用（`GSnap.py`），用于游戏存档的自动化备份。
使用哈希去重存储 + Zstandard 压缩以节省磁盘空间，
支持手动、定时、全局快捷键、监控文件变动、跟随游戏进程多种触发方式，
提供 **GUI / TUI / CLI / HTTP API / 守护进程** 五种使用形态，支持自更新，附带 Steam 创意工坊模组管理器。

- 跨平台：Windows / Linux / macOS（Python ≥ 3.11）
- 界面语言：内置中英文，可自己编写 `lang-*.json` 扩展

---

## 功能

- **增量快照**：文件按 MD5 前 16 位哈希存为 blob，相同内容只存一份
- **自动触发**：
  - 定时备份（默认每 10 分钟）
  - 文件监控备份（watchdog + 防抖）
  - 全局快捷键（默认 `Ctrl+Alt+N` 新建 / `Ctrl+Alt+T` 启停 / `Ctrl+Alt+D` 删除最新）
  - 跟随游戏进程：检测游戏是否运行，自动启停（需配置游戏进程名称）
- **截图留档**：创建快照时自动截取游戏窗口（需配置游戏进程名称）
- **多种界面**：桌面 GUI、终端 TUI、单命令 CLI
- **HTTP API**：内置 HTTP 服务器，简化版 JSON-RPC 远程调用
- **自动更新**：`update` 子命令从 GitHub Releases 或任意 URL 检查升级
- **通配符过滤**：`includes *.py;*.md`、`excludes *.bin` 精确控制备份范围
- **Steam 创意工坊**：`modman` 子命令列出 / 下载 / 同步 / 整理 Workshop 模组（自动安装 SteamCMD）
- **EZ Auto Backup 兼容**：可直接导入 `GAMELIST.ERAB` 游戏预设

---

## 安装

### 方式一：下载预编译版（推荐）

从 [Releases](https://github.com/hankso/gamesnap/releases/latest) 下载对应平台的单文件可执行程序：

`GameSnap[GUI]-[windows|linux|macos]-[amd64|arm64][.exe]`

> `GameSnapGUI` 是 Windows GUI Subsystem 程序，双击运行时默认隐藏终端窗口，此外与 `GameSnap` 功能完全一致

### 方式二：从源码运行

```bash
git clone https://github.com/hankso/gamesnap
cd gamesnap

# 使用 uv
uv sync
uv run GSnap.py --help

# 或使用 pip
python -m pip install .
python GSnap.py --help
```

---

## 快速开始

```bash
# 1. 启动桌面或终端界面
GameSnap gui
GameSnap tui

# 2. 无界面后台运行
GameSnap daemon --pid --log ./log.txt

# 3. 手动创建快照（打印文件信息）
GameSnap create -v

# 4. 查看所有快照（打印文件大小）
GameSnap summary --size

# 5. 恢复指定快照（默认恢复到记录中的原始目录，并同步删除多余文件）
GameSnap restore /path/to/20260401-001644-123456.zstd

# 6. 导出分享快照（并行 deflated zip 压缩）
GameSnap export /path/to/20260401-001644-123456.zstd ./share.zip

# 7. 删除指定快照（自动清理多余的 blob 文件）
GameSnap delete /path/to/20260401-001644-123456.zstd

# 8. 指定配置文件启动（默认程序同目录 `config.json`）
GameSnap <command> --config /path/to/custom.json

# 9. 导入 EZAB 预设
GameSnap <command> --preset /path/to/GAMELIST.ERAB <GAME>

# 10. 手动覆盖参数
GameSnap <command>                  \
    --param compress_STORAGE=true   \ # 参数名忽略大小写
    --param watchdog_enabled        \ # bool 类型参数可省略 `=true`
    --param incs="*.bin;*.dat"      \ # 支持别名简写
    --param bdir="C:\path/to\bdir"    # 兼容 slash 和 backslash
```

| 选项 | 说明 |
|---|---|
| `-v, --verbose` | 增加日志细节 |
| `-c, --console {show,hide,auto}` | 控制终端窗口显隐（Windows） |
| `-s, --serial` | 禁用并发，单线程处理文件 |
| `--config PATH` | 指定配置文件 |
| `--preset FILE GAME` | 从 EZAB 预设文件加载指定游戏的存档目录与进程名 |
| `--param KEY[=VAL] ...` | 手动覆盖参数 |

| 命令 | 说明 |
|---|---|
| `gui` | 图形用户界面（默认命令） |
| `tui [--css PATH...]` | 终端用户界面（textual） |
| `daemon [--log PATH] [--pid PATH]` | 无界面后台运行，配合配置文件中的定时/监控/热键任务 |
| `create` | 手动创建一次快照 |
| `restore RECORD [MEMBER...] [--sdir DIR]` | 恢复快照；`MEMBER` 可按相对路径过滤部分文件 |
| `export RECORD OUTPUT` | 将快照打包为 zip（内含原始目录结构 + 截图） |
| `delete (PATH... \| --latest N \| --oldest N \| --all)` | 删除快照并自动清理不再被引用的 blob |
| `summary [--size]` | 列出备份目录下所有快照（`--size` 显示体积与压缩比） |
| `modman [ID...] [--mdir DIR] [--copy DIR] [--sync] [--gid ID] [--yes]` | Steam 创意工坊模组管理（详见下文） |
| `update (--url URL \| --repo USER REPO \| --ppid PID \| --gen DIR)` | 检查下载新版本 / 生成版本信息 |
| `test` | 运行自检，验证引擎全部流程并报告错误 |

> 子命令用法详见 `GameSnap <command> -h`

### Steam 创意工坊（modman）

```bash
# 列出已下载的全部创意工坊内容（按名称/时间/大小排序：--sort NAME）
GameSnap modman

# 下载创意工坊物品或合集（自动查询所属游戏，可 --gid 指定）
GameSnap modman --yes 3171167894 [--gid 108600]  # 未找到 SteamCMD 时自动安装

# 更新 MDIR 下所有已下载模组
GameSnap modman --sync

# 将 $MDIR/steamapps/workshop/content/*/mods/* 同步到指定目录
GameSnap modman --copy "D:/Games/mods/"
```

### 自动更新（update）

```bash
# 从 GitHub Releases 检查并升级（默认本仓库）
GameSnap update --repo hankso gamesnap

# 从任意 URL 获取 latest 元数据
GameSnap update --url https://example.com/gamesnap/

# 生成版本信息文件（发布流程用）
GameSnap update --gen ./dist/
```

---

## 配置说明

默认配置文件为程序同目录下的 `config.json`，JSON 字段与短别名等价（HTTP API / 命令行均可用别名）：

| JSON 字段 | 别名 | 说明 | 默认值 |
|---|---|---|---|
| `watchdog_enabled` | `watch` | 启用文件变更监控备份 | `false` |
| `watchdog_seconds` | `wintv` | 监控防抖时间（秒），变更静默后才开始备份 | `30` |
| `schedule_enabled` | `sched` | 启用定时备份 | `true` |
| `schedule_seconds` | `sintv` | 定时间隔（秒） | `600` |
| `http_server_port` | `port` | HTTP API 端口（`0` 停止服务） | `9999` |
| `preview_filepath` | `pimg` | 游戏存档内缩略图路径（没有可留空） | `""` |
| `includes_pattern` | `incs` | 包含通配符，`;` 分隔，如 `*.sav;*.bin` | `""` |
| `excludes_pattern` | `excs` | 排除通配符，`;` 分隔 | `""` |
| `source_directory` | `sdir` | 游戏存档目录 | 程序所在目录 |
| `backup_directory` | `bdir` | 备份目录（不允许位于 sdir 内部） | `sdir` 同级 `snapshots/` |
| `backup_max_count` | `bcnt` | 最多保留快照数，超出自动清理最旧 | `50` |
| `sound_of_success` | `succ` | 备份成功提示音（音频文件路径或系统音名称） | `SystemDefault` |
| `sound_of_failure` | `fail` | 备份失败提示音 | `SystemHand` |
| `hotkey_of_create` | `knew` | 创建快照全局热键 | `ctrl+alt+n` |
| `hotkey_of_toggle` | `ktog` | 启停任务全局热键 | `ctrl+alt+t` |
| `hotkey_of_delete` | `kdel` | 删除最新快照热键 | `ctrl+alt+d` |
| `syncwith_process` | `proc` | 跟随的游戏进程名，如 `ProjectZomboid64` | `""` |
| `compress_storage` | `comp` | 大文件使用 zstd 压缩存储 | `true` |
| `current_language` | `lang` | 界面语言（`en` / `zh`，可放 `lang-xx.json` 扩展） | `en` |

---

## 快照存储结构

```
backup_directory/
├── 20260401-001644-123456.zstd    # 快照记录：zstd 压缩的 JSON 元数据
├── 20260401-001644-123456.jpg     # 创建时刻的游戏窗口截图
└── blob/
    ├── 5d41402abc4b2a76           # 小文件（<4KB）或未压缩模式：按内容 MD5 前 16 位命名
    └── 7d865e959b246691.zstd      # 大文件：zstd 压缩流，按解压后内容 MD5 命名
```

> 同一备份目录内所有快照共享 `blob/`，删除快照时仅回收无引用的 blob

---

## HTTP API

程序启动后默认在 `localhost:9999` 提供 JSON API：

```bash
# 查看帮助
curl http://localhost:9999/

# 列出全部接口及其参数签名
curl http://localhost:9999/apis

# 按顺序调用多个接口（返回合并的 JSON）
curl -X POST http://localhost:9999/apis \
     -H 'Content-Type: application/json' \
     -d '{"create": {}, "summary": {"detail": true}}'
```

```javascript
// 浏览器 / JS 调用示例
fetch('http://localhost:9999/apis', {
  method: 'POST',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({delete: {record: 'D:/backups/111.zstd'}}),
}).then(res => res.json()).then(console.log);
```

---

## 性能表现

### 测试环境

| 项目 | 配置 |
| --- | --- |
| CPU | AMD 8845 |
| 操作系统 | Windows 11 (10.0.26200) |
| 内存 | DDR5 32 GB SDRAM |
| 存储 | PCIe x4 1 TB SSD |

### 测试方法

- **数据**：100,000 个小文件，随机分散在 2,000 个目录中，总大小 750 MB。
- **操作**：从指定源路径**同盘复制**到目标路径，耗时取 5 次平均值
- **工具**：
  1. Windows 资源管理器自带复制粘贴
  2. `robocopy /NS /NC /NFL /NDL /E /XO /MT:16`
  3. GameSnap create --param compress_storage=true or false

### 全量备份

| 工具 | 耗时 | 输出大小 | 备注 |
| --- | --- | --- | --- |
| Windows 资源管理器 | 240 s | 750 MB | 系统自带复制粘贴 |
| robocopy | 31 s | 750 MB | 系统自带命令行工具 |
| GameSnap comp=false | 56 s | 750 MB | |
| GameSnap comp=true | 70 s | 140 MB | 逐文件独立压缩 |


### 增量备份（部分文件小改动后再次复制）

| 工具 | 耗时 | 累计大小 | 备注 |
| --- | --- | --- | --- |
| Windows 资源管理器 | 235 s | 1500 MB | 重拷体积翻倍 |
| robocopy `/XO /MT:16` | 3 s | 762 MB | 原始文件被覆盖 |
| GameSnap | 13 s | 145 MB | 压缩与否耗时接近，保留变更前快照 |

---

## 开发者指南

### 项目架构

单文件 `GSnap.py` 自上而下分层：

| 模块 | 职责 |
|---|---|
| 工具函数 | Posix 路径、JSON 读写（自动 zstd）、`tail -f`、进程查找、注册表查询、窗口截图、线程池 `poolmap`、提示音、`PZipFile` 等 |
| `IOMixin` | dataclass 序列化基类：字段别名、类型强制转换、`ge/le` 范围约束 |
| `SnapConfig` | 全部配置项（含别名），见上文配置表 |
| `SnapRecord` | 快照元数据：`tree` / `flat` / `dirs` 三种视图惰性求值 |
| `SnapEngine` | 核心引擎：`create` / `restore` / `export` / `delete` / `summary`，内容寻址 + 去重 + zstd + 并发，`summary` 带目录 mtime 哈希缓存 |
| `SnapRouter` | HTTP 服务：反射注册 Broker 公开方法，`GET /apis` 列出签名，`POST /apis` 按序调用 |
| `SnapBroker` | 配置 setter 触发副作用（启停 watchdog 观察者 / 调度线程 / HTTP 服务 / 进程同步线程 / 热键），三态 `start`，EZAB 预设解析 |
| `UIMixin` / `TUIMixin` | 多语言、textual 对话框与文件树组件 |
| `SnapTUI` / `SnapGUI` | textual 终端界面 / tkinter 图形界面 |
| `ArgumentParser` + `make_parser` + `main` | CLI 子命令注册、`REMAINDER` 透传、rich 彩色帮助 |
| `modman` 相关 | `find_mods`（解析 `appworkshop_*.acf`）、`steam_cmd`（自动安装 SteamCMD）、Steam Web API 查询 |
| `run_update` + `Version` | 自更新，类 github 元数据，语义化版本解析器 |

### 开发环境

```bash
# 依赖组 dev（调试）/ extra（可选）/ build（打包）
uv sync --all-groups

# 安装 poe（可选）
uv tool install poethepoet # 安装到 uv tool dir
uvx --from poethepoet poe  # 或者从 uv cache dir 临时运行

# 通过 poe 运行定义在 pyproject.toml 中的任务
poe run         # 入口 GSnap:main
poe textual     # textual-dev 热重载调试 TUI
poe console     # textual 日志控制台
poe trace       # viztracer 性能追踪 → build/trace.json.gz
poe view-trace  # 可视化追踪结果
poe build       # nuitka 打包 onefile
poe view-build  # 渲染并查看 nuitka 编译 report.html
```

### 打包（Nuitka）

源码内嵌 `nuitka-project:` 指令，直接 `nuitka GSnap.py` 即可

```bash
# Windows: choco install upx
# Linux: apt install gcc upx ccache patchelf python3-tk (or upx-ucl)

nuitka GSnap.py                           # 默认生成 onefile 或 app (macOS)

# Windows 切换子系统
set SUBSYSTEM=CUI && nuitka GSnap.py      # cmd 环境变量写法 → GameSnap.exe
$env:SUBSYSTEM = "GUI"; & nuitka GSnap.py # pwsh环境变量写法 → GameSnapGUI.exe

# 或使用 poe 自动处理环境变量与依赖组
poe build
poe build-gui
```

### 兼容性说明

- Python ≥ 3.11（使用 `tomllib`）
- Python 3.14+ 使用内置 `compression.zstd`, 旧版本回退 `backports.zstd`
- Windows only: `winsound`, `winreg`, PE 头解析区分 CUI/GUI 子系统
- Unix only: `pty` 伪终端支持

---

## 历史

| 版本 | 变更 |
|---|---|
| v0.2.2 | GUI/TUI/Daemon 支持运行时切换 `config.json` |
| v0.2.1 | 支持文件名通配符过滤 |
| v0.2.0 | SnapRecord 改为存储目录树以节省空间 |
| v0.1.4 | 支持从 URL 或 GitHub 仓库自更新 |
| v0.1.3 | 新增 modman 与 daemon 模式，Linux 适配 |
| v0.1.2 | 新增终端 UI（textual）与 pyproject.toml |
| v0.1.1 | 新增图形 UI（tkinter），nuitka 打包 |
| v0.1.0 | 快照引擎与 argparse 命令行 |

## 作者与许可

- 作者：[Hankso](https://github.com/hankso) <hankso@gmail.com>
- 仓库：<https://github.com/hankso/gamesnap>
- 许可证：MIT License
