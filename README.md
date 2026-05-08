# VELVET — Personal Hi-Fi Music Server

> *The art of listening.*

**Latest stable: [velvet-v2.4.19](https://github.com/Edwynl/Velvet-HiFi-MP/releases/tag/velvet-v2.4.19)** — current GitHub `main` release with portable setup improvements.

VELVET is a premium, minimalist personal music server designed for audiophiles. It provides a cinematic, high-performance web interface to manage and stream your local music library, with native support for high-resolution formats and UPnP/DLNA integration for high-end playback devices.

[中文介绍](#中文介绍)

---

## Features

- **Cinematic UI**: A refined, dark-mode-first interface inspired by luxury editorial design.
- **Hi-Fi Ready**: Native support for FLAC, DSD (DoP), and bit-perfect streaming.
- **PWA Support**: Install VELVET on your iOS or Android device for a standalone, app-like experience.
- **UPnP/DLNA**: Seamlessly stream to high-end devices like Marantz, Cambridge Audio, and HEOS-enabled speakers.
- **Smart Management**: Automatic metadata enrichment and high-resolution cover art fetching.

## Quick Start

### Windows

1. **Download/clone** this repository.
2. **Install and repair the local environment**: double-click `install.bat`.
   - To also install Python, FFmpeg, and Node.js with winget, run:
     ```powershell
     powershell -ExecutionPolicy Bypass -File .\install.ps1 -InstallSystemTools
     ```
3. **Configure**: edit `.env` if your music folder is not auto-detected.
4. **Run**: double-click `start.bat`.
5. **Access**: open `http://localhost:8765`.

The installer creates `venv/`, installs Python packages, optionally installs Node dependencies, creates a machine-local `.env`, and checks FFmpeg/fpcalc availability. Do not move `venv/` or `.env` between computers; regenerate them with `install.bat`.

### Manual

```powershell
python -m venv venv
.\venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env
.\venv\Scripts\python server.py
```

---

<a name="中文介绍"></a>
# VELVET — 私人 Hi-Fi 音乐服务器

> *聆听的艺术。*

**最新稳定版：[velvet-v2.4.19](https://github.com/Edwynl/Velvet-HiFi-MP/releases/tag/velvet-v2.4.19)** — 当前 GitHub `main` 对应版本，并加入更便携的安装配置流程。

VELVET 是一款专为音乐发烧友打造的高级简约私人音乐服务器。它提供了一个具有电影感、高性能的 Web 界面，用于管理和串流您的本地音乐库，原生支持高分辨率音频格式，并为高端播放设备提供 UPnP/DLNA 整合支持。

## 核心特性

- **电影级界面**：受奢侈品画报设计的启发，提供极致考究的深色模式界面。
- **发烧级支持**：原生支持 FLAC, DSD (DoP) 以及源码位深（Bit-perfect）串流。
- **PWA 支持**：可在 iOS 或 Android 设备上通过“添加到主屏幕”作为独立 App 使用，享受沉浸式体验。
- **UPnP/DLNA**：无缝连接至 Marantz, Cambridge Audio 等高端设备及支持 HEOS 的音箱。
- **智能库管理**：自动元数据增强及高分辨率封面获取。

## 快速开始

1. **下载或 clone 仓库**。
2. **安装/修复环境**：双击 `install.bat`。
   - 如需自动安装 Python、FFmpeg、Node.js，用 PowerShell 运行：
     ```powershell
     powershell -ExecutionPolicy Bypass -File .\install.ps1 -InstallSystemTools
     ```
3. **配置音乐目录**：检查 `.env` 里的 `VELVET_MUSIC_DIR`。
4. **启动**：双击 `start.bat`。
5. **访问**：浏览器打开 `http://localhost:8765`。

不要在电脑之间复制 `venv/` 和 `.env`。换电脑后保留源码，重新运行 `install.bat`，脚本会按新电脑路径重建虚拟环境和本地配置。

---

## License
MIT
