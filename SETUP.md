# VELVET — 完整安装与配置手册

> 个人 Hi-Fi 音乐服务器 · 类 Roon 体验 · 完全本地运行  
> 当前 GitHub 版本 velvet-v2.4.19 | 适用于 Windows 10/11

---

## 目录

1. [系统要求](#1-系统要求)
2. [快速开始（5分钟上手）](#2-快速开始)
3. [详细安装步骤](#3-详细安装步骤)
4. [首次配置](#4-首次配置)
5. [音乐库扫描](#5-音乐库扫描)
6. [元数据与封面](#6-元数据与封面)
7. [音频指纹识别](#7-音频指纹识别-acoustid)
8. [升频功能（DSD256）](#8-升频功能)
9. [投送到 HiFi 设备](#9-投送到-hifi-设备)
10. [多房间网络串流](#10-多房间网络串流)
11. [播放功能说明](#11-播放功能说明)
12. [环境变量与高级配置](#12-环境变量与高级配置)
13. [常见问题](#13-常见问题)
14. [文件结构说明](#14-文件结构说明)
15. [API 参考](#15-api-参考)

---

## 1. 系统要求

| 项目 | 最低要求 | 推荐配置 |
|---|---|---|
| 操作系统 | Windows 10 64-bit | Windows 11 |
| Python | 3.10+ | 3.12 |
| RAM | 4GB | 8GB+ |
| CPU | 双核 | 四核（升频时占用较高）|
| 磁盘（程序） | 200MB | — |
| 网络 | 局域网 100Mbps | 千兆局域网 |

**必须安装：**
- Python 3.10+ — 可由 `install.ps1 -InstallSystemTools` 通过 winget 自动安装
- FFmpeg — 可由 `install.ps1 -InstallSystemTools` 通过 winget 自动安装；升频功能强烈推荐

**可选安装：**
- [fpcalc.exe（Chromaprint）](https://acoustid.org/chromaprint) — 音频指纹识别功能

---

## 2. 快速开始

```
1. 从 GitHub 下载 zip 或 git clone 到任意目录，例如 D:\VELVET\
2. 双击 install.bat，自动创建 venv、安装依赖、生成 .env
3. 如需自动安装 Python/FFmpeg/Node.js：
   powershell -ExecutionPolicy Bypass -File .\install.ps1 -InstallSystemTools
4. 双击 start.bat
5. 浏览器打开 http://localhost:8765
6. 进入 Settings → 填入音乐目录路径 → 点击 Save & Scan Library
```

**首次扫描时间参考：**
- 10,000 首：约 5–15 分钟
- 50,000 首（~500GB）：约 1–2 小时
- 150,000 首（~2TB）：约 3–5 小时

扫描过程中可以正常使用，已扫描的内容实时可用。

---

## 3. 详细安装步骤

### 3.1 推荐：一键安装/修复环境

在项目根目录打开 PowerShell：

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1 -InstallSystemTools
```

脚本会自动完成：

- 检测/安装 Python 3.10+
- 创建 `venv/`
- 安装 `requirements.txt`
- 检测/安装 FFmpeg
- 安装 Node.js 依赖（用于前端测试）
- 创建本机专用 `.env`
- 编译检查关键 Python 文件

如果你已经手动安装了 Python 和 FFmpeg，只需双击 `install.bat`。

### 3.2 手动安装 Python

1. 访问 https://www.python.org/downloads/
2. 下载 Windows installer (64-bit)，推荐 3.12.x
3. 安装时勾选 "Add python.exe to PATH"
4. 重新打开终端，运行 `python --version` 验证

### 3.3 手动安装 FFmpeg（升频功能）

1. 访问 https://github.com/BtbN/FFmpeg-Builds/releases
2. 下载 `ffmpeg-master-latest-win64-gpl.zip`
3. 解压到 `C:\ffmpeg\`
4. 将 `C:\ffmpeg\bin` 添加到系统 PATH：
   - Win+S 搜索"环境变量" → 编辑系统环境变量
   - 点击"环境变量" → 找到 Path → 编辑 → 新建 → 填入 `C:\ffmpeg\bin`
   - 确定保存
5. 验证（重新打开 cmd）：
   ```
   ffmpeg -version
   ```

也可以把 `ffmpeg.exe` 放在项目的 `tools\bin\` 目录，`start.bat` 会自动把它加入本次进程 PATH。

### 3.4 安装 Chromaprint / fpcalc（指纹识别）

1. 访问 https://acoustid.org/chromaprint
2. 下载 **Windows** 版本（`chromaprint-fpcalc-1.5.1-windows-x86_64.zip`）
3. 解压，将 `fpcalc.exe` 复制到 VELVET 程序目录（与 `server.py` 同级）
   ```
   D:\VELVET\
   ├── fpcalc.exe   ← 放这里
   ├── server.py
   ├── start.bat
   └── ...
   ```
   或者添加到系统 PATH（任选其一）
   也可以放入 `tools\bin\fpcalc.exe`。

### 3.5 运行 VELVET

双击 `start.bat`，脚本会自动：
1. 调用 `install.ps1` 检查/修复本地环境
2. 创建或复用 Python 虚拟环境 `venv/`
3. 自动安装所有依赖（fastapi、uvicorn、mutagen、httpx 等）
4. 读取 `.env`
5. 启动服务器（端口 8765）
6. 同时启动 UPnP/DLNA 服务（端口 8766）

控制台会显示：
```
================================================
  VELVET Personal Music Server
================================================
  Browser UI: http://localhost:8765
  Network:    http://192.168.1.100:8765
  UPnP/DLNA:  visible to Marantz M1 & Cambridge EXN100
================================================
```

---

## 4. 首次配置

打开浏览器访问 `http://localhost:8765`，点击左侧 **Settings**。

### 4.1 设置音乐目录

在 **Music Library Folder** 输入你的音乐库路径，例如：

```
D:\Music
E:\HiRes Music
\\NAS\Music          ← 网络路径也支持
```

> **注意：** 路径中如有中文或空格，确保路径完整正确。建议使用绝对路径。

### 4.2 环境变量方式（开机自启推荐）

也可以在 `.env` 中直接修改默认路径：

```env
VELVET_MUSIC_DIR=C:\Music
VELVET_DATA_DIR=velvet_data
```

改为你的实际路径：

```env
VELVET_MUSIC_DIR=D:\HiResMusic
```

---

## 5. 音乐库扫描

### 5.1 首次扫描

Settings → **Save & Scan Library** → 后台自动运行

进度条显示：
- 已处理文件数 / 总文件数
- 新增曲目数
- 当前处理文件名

### 5.2 增量扫描

再次点击 **Rescan** 时，已索引的文件会被跳过（通过文件路径判断），只处理新增文件。大库二次扫描通常只需几分钟。

### 5.3 支持的音频格式

| 格式 | 扩展名 | 说明 |
|---|---|---|
| FLAC | `.flac` | 最常用无损格式 |
| MP3 | `.mp3` | 有损压缩 |
| AAC/M4A | `.m4a` `.aac` | Apple 格式 |
| WAV | `.wav` | 无压缩 PCM |
| AIFF | `.aiff` `.aif` | Mac 无损 |
| WavPack | `.wv` | 无损压缩 |
| APE | `.ape` | Monkey's Audio |
| Ogg Vorbis | `.ogg` | 开源有损 |
| Opus | `.opus` | 现代有损 |
| **DSD（原生）** | `.dsf` `.dff` | DSD64/128/256/512 |
| WMA | `.wma` | Windows Media |
| MKA | `.mka` | Matroska 音频 |

---

## 6. 元数据与封面

VELVET 按以下优先级查找专辑封面：

```
优先级 1: 音频文件内嵌封面（FLAC Picture Block / ID3 APIC / MP4 covr）
优先级 2: 同目录下的图片文件（cover.jpg / folder.jpg / front.jpg）
优先级 3: MusicBrainz Cover Art Archive（后台自动请求）
```

### 6.1 手动触发封面抓取

进入任意专辑页面 → 点击 **🔍 Fetch Cover** → 自动查询 MusicBrainz

Settings 页面也有 **批量抓取** 按钮，会对所有缺封面的专辑触发 MusicBrainz 查询（受 API 速率限制，大库需要较长时间）。

### 6.2 艺人传记（Wikipedia）

点击艺人页面时自动从 Wikipedia 拉取简介（英文优先）。首次加载约 1 秒，之后写入数据库实时显示。

Settings → **Fetch Artist Bios** 可批量为所有艺人抓取传记。

---

## 7. 音频指纹识别（AcoustID）

这是解决**标签混乱、文件名错误**问题的核心功能，对于从各处收集的大库尤其有价值。

### 7.1 工作原理

```
音频文件 → fpcalc.exe 计算声学指纹 → AcoustID API 匹配
→ 返回 MusicBrainz 数据（正确的歌名、专辑、艺人、MBID）
→ 同时从 Cover Art Archive 下载对应封面
→ 更新数据库
```

指纹识别基于**声音内容本身**，不依赖文件名或现有标签，准确率极高。

### 7.2 使用步骤

1. 确保 `fpcalc.exe` 在程序目录或 PATH 中
2. Settings → **Start Fingerprinting (200 tracks)**
3. 进度条实时更新，后台运行不影响播放
4. 完成后提示修正了多少条记录

> **注意：** AcoustID 免费 API 有速率限制，每批处理 200 首，每首间隔约 350ms。200 首约需 2 分钟。

### 7.3 单曲指纹识别

通过 API 对单首曲目识别：
```
POST /api/enrich/fingerprint/{track_id}
```

---

## 8. 升频功能

VELVET 使用 **FFmpeg + SoX 重采样引擎**（precision=28，HiFi 级别）进行实时升频。

### 8.1 升频模式

| 模式 | 输出采样率 | 说明 |
|---|---|---|
| Source | 原始 | 不处理，直接串流 |
| 2× HiRes | 88.2kHz | CD → 双倍 |
| 4× HiRes | 176.4kHz | 标准 Hi-Res |
| 8× (≈DSD128) | 352.8kHz | DSD128 等效采样率 |
| **16× (≈DSD256)** | **705.6kHz** | **DSD256 等效采样率** |

在播放器右下角的下拉菜单选择升频模式，切换时自动重新推流，不需要重新选歌。

### 8.2 关于"原生 DSD"的说明

VELVET 输出的是**高采样率 PCM（FLAC 格式封装）**，而非原生 DSD 比特流。

| 方式 | 格式 | 兼容性 |
|---|---|---|
| VELVET 升频 | 705.6kHz PCM/FLAC | 所有支持 Hi-Res 的设备和播放器 |
| 原生 DSD 输出 | DSD256 比特流 | 需要 ASIO 驱动 + HQPlayer/JRiver |

对于 Marantz M1 和 Cambridge EXN100 这类网络播放器，**高采样率 PCM 是正确的方式**，它们并不直接接收 DSD 比特流。

### 8.3 CPU 使用率参考

| 模式 | CPU 占用（i7-10700） |
|---|---|
| Source | <1% |
| 2× / 4× | 5–10% |
| 8× (≈DSD128) | 15–25% |
| 16× (≈DSD256) | 35–60% |

---

## 9. 投送到 HiFi 设备

### 9.1 Marantz Model M1

**方式一：UPnP/DLNA（推荐，最高音质）**

VELVET 启动时自动广播 UPnP 设备，无需任何额外配置。

1. 打开手机 **HEOS app**
2. 音乐来源 → 音乐服务器（My VELVET）
3. 找到 **"VELVET"** → 浏览艺人 / 专辑 / 播放

**方式二：AirPlay 2**

1. 在 Windows 设置 → 声音 → 选择 Marantz M1 作为音频输出
2. VELVET 网页播放器的声音会通过 AirPlay 发送到 M1
3. 或使用 [TuneBlade](https://www.tuneblade.com/)（免费）将 Windows 音频转 AirPlay

**支持的格式（通过 UPnP）：** FLAC、AIFF、WAV、MP3、AAC、OGG，最高 192kHz/24-bit

---

### 9.2 Cambridge Audio EXN100

**方式一：UPnP/DLNA（推荐）**

1. 打开 **StreamMagic app**
2. Library → Music Servers → 找到 **"VELVET"**
3. 浏览播放，支持高达 32-bit/768kHz PCM

**方式二：Chromecast（最简单）**

1. 打开 VELVET 网页（Chrome/Edge）
2. 浏览器右上角 → 投射 → 选择 **Cambridge EXN100**
3. 整个网页音频推送到 EXN100

**方式三：AirPlay 2**

同 Marantz M1 的 AirPlay 方式

**EXN100 特有：** 此设备也是 **Roon Ready** 认证设备，如果你同时有 Roon，可以同时使用。

---

### 9.3 其他 DLNA 设备

任何支持 DLNA/UPnP 的设备（Sony、Denon、Pioneer、KEF、Naim 等）均可使用。
使用手机 App 如 **mconnect Player**、**BubbleUPnP**（Android）或 **Serenade**（iOS）控制。

---

## 10. 多房间网络串流

VELVET 默认绑定到 `0.0.0.0`，意味着同局域网内所有设备都可以访问。

### 10.1 查找你的 PC IP 地址

```
Win+R → cmd → ipconfig
```
找到 **IPv4 地址**，例如 `192.168.1.100`

### 10.2 其他设备访问

| 设备 | 访问方式 |
|---|---|
| 手机/平板 | 浏览器打开 `http://192.168.1.100:8765` |
| 另一台电脑 | 同上 |
| HiFi 设备 | 通过 HEOS/StreamMagic app 找到 VELVET |
| 电视 | 通过 Chromecast / 浏览器 |

### 10.3 多点播放注意事项

VELVET 支持多个客户端**同时连接**，各自独立播放不同音乐。  
真正的"多房间同步"（毫秒级）需要 Roon RAAT 协议，UPnP 各端点独立控制。

---

## 11. 播放功能说明

### 播放器控制

| 按钮 | 功能 |
|---|---|
| ▶ / ❚❚ | 播放 / 暂停 |
| ⏮ | 上一首（3秒内点击返回开头） |
| ⏭ | 下一首 |
| 🔀 | 随机播放（打乱当前队列） |
| 🔁 | 循环模式：关闭 → 全部循环 → 单曲循环 |

### 队列管理

- 播放专辑 / 点击 Play All → 整张专辑加入队列
- 点击播放器右侧 **队列图标** → 侧滑出队列面板
- 队列中点击任意曲目直接跳转
- 当前播放曲目有金色跳动指示器

### 歌词显示

- 点击播放器右侧 **歌词图标** → 底部滑出歌词面板
- 使用 [LRCLIB](https://lrclib.net) 免费 API（无需注册）
- 支持 **逐行同步滚动**，当前行自动高亮放大
- 点击歌词行可跳转到对应播放位置
- 无同步歌词时显示纯文本版本

### 升频选择

播放器右下角下拉菜单，切换立即生效（重新请求串流）。

---

## 12. 环境变量与高级配置

可在 `.env` 中设置以下变量，也可以在运行前设置同名环境变量。新配置统一使用 `VELVET_` 前缀，旧的 `DATA_DIR`、`UPNP_DEVICE_NAME` 等仍尽量兼容。

| 变量 | 默认值 | 说明 |
|---|---|---|
| `VELVET_MUSIC_DIR` | `C:\Music` | 音乐库根目录 |
| `VELVET_DATA_DIR` | `velvet_data` | 数据库和封面缓存目录 |
| `VELVET_PORT` | `8765` | Web 服务端口 |
| `VELVET_HOST` | `0.0.0.0` | 监听地址 |
| `VELVET_UPNP_FRIENDLY_NAME` | `VELVET` | DLNA/UPnP 设备名 |

### 修改端口

编辑 `.env`：

```env
VELVET_PORT=8765
```

UPnP 控制端口目前仍在 `upnp_server.py` 中定义为 `8766`。

### 开机自启（Windows 任务计划）

1. Win+S 搜索"任务计划程序"
2. 创建基本任务 → 触发器：用户登录时
3. 操作：启动程序 → 浏览到 `start.bat`
4. 起始位置：填入 `start.bat` 所在目录

或者创建快捷方式放入启动文件夹（`Win+R` → `shell:startup`）。

### 使用 Windows 防火墙（局域网访问）

如果其他设备无法访问，检查防火墙设置：

```
控制面板 → Windows Defender 防火墙 → 允许应用通过防火墙
→ 允许 Python 通过私有网络
```

或手动开放端口：
```
netsh advfirewall firewall add rule name="VELVET" dir=in action=allow protocol=TCP localport=8765
netsh advfirewall firewall add rule name="VELVET-UPnP" dir=in action=allow protocol=TCP localport=8766
netsh advfirewall firewall add rule name="VELVET-SSDP" dir=in action=allow protocol=UDP localport=1900
```

---

## 13. 常见问题

### Q: 双击 start.bat 一闪而过？
**A:** 右键 start.bat → 以管理员身份运行。或打开 cmd，`cd` 到 VELVET 目录后运行 `start.bat`，这样可以看到错误信息。

### Q: 提示"Python 未找到"？
**A:** 推荐运行 `powershell -ExecutionPolicy Bypass -File .\install.ps1 -InstallSystemTools`。如果手动安装 Python，安装时勾选 "Add to PATH"，然后重新打开终端。

### Q: 升频后没有声音？
**A:** 确认 FFmpeg 已安装且在 PATH 中，或放在 `tools\bin\ffmpeg.exe`。在 cmd 中输入 `ffmpeg -version` 验证。

### Q: HiFi 设备找不到 VELVET？
**A:** 检查以下几点：
1. PC 和 HiFi 设备在同一个 Wi-Fi / 网络
2. Windows 防火墙已允许 Python（UDP 端口 1900）
3. 某些路由器默认阻止 UPnP 组播，在路由器管理界面开启 "UPnP" 或 "DLNA"

### Q: 封面显示不出来？
**A:** 首次抓取需要时间（MusicBrainz 限速 1 req/s）。点击专辑页的 "🔍 Fetch Cover" 手动触发。或检查文件内是否有内嵌封面（用 Mp3tag 查看）。

### Q: 扫描非常慢？
**A:** 正常现象。Mutagen 需要读取每个文件的标签。2TB 库（约 15 万首）首次扫描约 3–5 小时。后续增量扫描很快。可以边扫描边听已扫描的部分。

### Q: 指纹识别提示 fpcalc 未找到？
**A:** 将 `fpcalc.exe` 放到与 `server.py` 同一个目录下，或添加到系统 PATH。

### Q: 歌词找不到？
**A:** LRCLIB 收录了大量英文和日文歌曲，中文华语歌曲覆盖率相对较低。若找不到会显示 "No lyrics found"。

### Q: 播放 DSD 文件时有噪声？
**A:** DSF/DFF 文件会先转换为 PCM 串流。如果 DAC 需要原生 DSD，需要通过 USB 直连配合 ASIO 驱动使用，网络串流暂不支持原生 DSD 比特流。

### Q: 如何备份我的库数据？
**A:** 只需备份 `velvet_data/` 文件夹，里面包含：
- `library.db` — 完整的曲库索引和播放历史
- `covers/` — 已下载的封面图片

### Q: 换电脑/移动文件夹后环境坏了怎么办？
**A:** 不要复制旧电脑的 `venv/`、`.env`、`node_modules/`。这些目录包含本机路径和平台绑定依赖。换电脑后保留源码，重新运行 `install.bat`；如果系统缺 Python/FFmpeg/Node.js，运行 `install.ps1 -InstallSystemTools`。

---

## 14. 文件结构说明

```
velvet_v3/
│
├── start.bat              ← Windows 启动脚本（双击运行）
├── server.py              ← 主服务器（FastAPI）
│                            - HTTP API（扫描、串流、元数据、统计）
│                            - 音频串流（支持 Range 请求）
│                            - FFmpeg 升频管线
│                            - 播放历史记录
│                            - 歌词 API（LRCLIB）
│
├── enrichment.py          ← 元数据增强模块
│                            - AcoustID 音频指纹识别
│                            - MusicBrainz 元数据查询
│                            - Cover Art Archive 封面下载
│                            - Wikipedia 艺人传记抓取
│
├── upnp_server.py         ← UPnP/DLNA 媒体服务器
│                            - SSDP 设备广播（UDP 1900）
│                            - ContentDirectory:1 服务（浏览库）
│                            - ConnectionManager:1 服务
│                            - HTTP 设备描述（端口 8766）
│
├── requirements.txt       ← Python 依赖列表
├── README.md              ← 英文简介
├── SETUP.md               ← 本文档（完整中文手册）
│
├── fpcalc.exe             ← [需自行下载] Chromaprint 指纹工具
│
├── static/
│   └── index.html         ← 前端界面（单文件 SPA）
│                            - 专辑 / 艺人 / 搜索浏览
│                            - 播放控制 + 队列管理
│                            - 歌词面板（同步滚动）
│                            - 播放历史 / 最多播放统计
│                            - 设置 + 扫描 + 指纹识别控制
│
└── velvet_data/          ← [自动创建] 数据目录
    ├── library.db         ← SQLite 数据库（曲库 + 播放历史）
    └── covers/            ← 封面图片缓存
```

---

## 15. API 参考

VELVET 提供完整的 REST API，可供第三方客户端或自动化脚本使用。  
完整文档访问：`http://localhost:8765/docs`（FastAPI 自动生成的 Swagger UI）

### 常用端点

```
# 统计
GET  /api/stats                    → 曲库概览（艺人/专辑/曲目数）
GET  /api/stats/listening          → 播放统计（总播放次数、收听时长）

# 浏览
GET  /api/artists                  → 艺人列表（支持 search/limit/offset）
GET  /api/artists/{id}             → 艺人详情
GET  /api/artists/{id}/albums      → 艺人的专辑列表
GET  /api/albums                   → 专辑列表（支持 search/sort/year）
GET  /api/albums/{id}              → 专辑详情 + 曲目列表
GET  /api/search?q=keyword         → 全文搜索（曲目/专辑/艺人）
GET  /api/genres                   → 流派列表
GET  /api/recently-added           → 最近入库专辑

# 封面
GET  /api/covers/{album_id}        → 专辑封面图片

# 串流
GET  /api/stream/{track_id}        → 音频串流（支持 Range）
                                     ?upsample=none|2x|4x|8x|16x

# 扫描
POST /api/scan                     → 开始扫描（body: {"music_dir": "..."}）
GET  /api/scan/status              → 扫描进度
POST /api/scan/enrich-covers       → 批量抓取缺失封面

# 指纹识别
POST /api/enrich/fingerprint       → 批量指纹识别（后台运行）
GET  /api/enrich/fingerprint/status → 指纹识别进度
POST /api/enrich/fingerprint/{id}  → 单曲指纹识别
POST /api/enrich/bios              → 批量抓取艺人传记

# 播放历史
POST /api/tracks/{id}/played       → 记录播放（body: {"duration_played": 秒}）
GET  /api/history                  → 最近播放历史
GET  /api/most-played              → 最多播放排行

# 歌词
GET  /api/tracks/{id}/lyrics       → 获取歌词（同步 + 纯文本）

# 艺人传记
POST /api/artists/{id}/enrich-bio  → 获取/更新单个艺人 Wikipedia 传记

# 配置
GET  /api/config                   → 当前配置
POST /api/config                   → 更新配置（body: {"music_dir": "..."}）
```

---

## 关于 VELVET

VELVET 是一个完全本地运行的个人音乐服务器，不依赖任何订阅服务或云端。  
所有数据存储在本地，音乐文件不会上传到任何地方。

对于元数据查询，会访问以下免费公共 API：
- **MusicBrainz** — 音乐数据库（https://musicbrainz.org）
- **Cover Art Archive** — 专辑封面（https://coverartarchive.org）
- **AcoustID** — 音频指纹（https://acoustid.org）
- **Wikipedia** — 艺人传记（https://www.wikipedia.org）
- **LRCLIB** — 歌词数据库（https://lrclib.net）

以上服务均为免费开放，VELVET 遵守各服务的速率限制。
