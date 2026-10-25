# 启动本机最新版 main

双击桌面的 **VELVET main**，或 main 工作树里的 `start-main.bat`：

```text
C:\Users\edwyn\.codex\worktrees\main-runtime\VELVET_NEXT\start-main.bat
```

入口会通过 `git worktree list` 找到 main，使用已有 Python 环境，在 main 目录运行其 `server.py`，网页端口固定为 **8765**。服务启动后自动打开浏览器；运行期间保留命令窗口，按 Ctrl+C 停止。iPhone 继续使用原来的 VELVET 地址。

如果 main 已在运行，会核对进程路径、实际 HTML 和前端资源，再打开页面。若 8765 被其他目录或程序占用，会报错并保留该进程，不会抢占端口。8766 仍是 UPnP 服务。

只检查入口和运行版本，不启动、不打开浏览器：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start-main.ps1 -CheckOnly
```

原始目录 `C:\Antigravity\Claude Code\VELVET_NEXT` 当前是保留未提交工作的功能分支。那里的旧 `start.bat` 会启动它所在目录的代码，不会自动切换到 main；不能用它启动正式 8765 服务。

此入口不自动拉取、合并或切换分支，不修改媒体库路径、PWA 或原有启动脚本。源码更新需要另外完成同步；它负责启动本地 main。
