# VELVET PWA 与正式运行保护

这些规则由 Edwyn 要求保留，适用于本仓库所有分支与工作树。全局副本位于 `C:\Users\edwyn\.codex\AGENTS.md`。

## iOS PWA

除非用户明确要求改变 PWA 行为，必须保留现有 iPhone 桌面 App 启动、全屏、安全区、顶部菜单与搜索框可见性。头像、专辑排版、歌手排序等任务不得顺带重写 PWA。

保留以下已修复行为（修复基线提交 `7decc87`；在最新 main 上继续工作）：

- viewport 为 `width=device-width, initial-scale=1.0, viewport-fit=cover`。
- iOS WebKit 的 `display-mode: standalone` 修复：html/body/#app、侧栏及移动播放层使用现有 100vh 规则，#app 保留 `minmax(0, 1fr) auto`，#main 保留 `min-height: 0`。
- 顶部、左右和底部 safe-area 以及菜单、搜索框的可用宽度。
- manifest、Apple Web App 元数据、启动/显示设置、用户现有启动脚本、认证和缓存方案。不可通过旧文件覆盖或未经核对的分支合并回退这些行为。

## 正式服务

- 8765 只运行 main 的正式网页；8766 是 UPnP XML 服务，不是 UI。
- 当前 main 工作树：`C:\Users\edwyn\.codex\worktrees\main-runtime\VELVET_NEXT`。启动时使用该工作树作为工作目录，并传入其中 server.py 的绝对路径。路径变动时用 `git worktree list` 确认 main。
- 原始 checkout `C:\Antigravity\Claude Code\VELVET_NEXT` 可能仍是旧功能分支；不得从这里直接启动 8765。保留其中未提交工作、共享媒体数据和用户启动方案。
- 测试使用独立空闲端口及隔离数据。停止服务前核对 PID、端口和进程路径，不终止无关进程。

## 发布检查

1. 确认 main 与远端状态，保留已有和其他任务的新修复，不 force push、不整文件回退。
2. 执行 `npm run test:pwa`，并检查 PWA 相关 diff。
3. 发布后读取 `http://localhost:8765/`，将实际 HTML 与 main 的 `static/index.html` 比较（可归一化换行），核对 standalone 和 safe-area；检查实际进程路径及更新资源。
4. 真实 iPhone 的 WebKit 行为需要手机复核；静态测试通过不能代替真实设备验证。

只有用户明确要求的具体 PWA 改动才可以调整这些保护行为。普通 UI 任务无需重复请求许可，直接保持保护规则执行。
