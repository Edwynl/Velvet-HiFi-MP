# VELVET 远程访问设置指南

本文档介绍如何从外部网络访问你的 VELVET 服务器。

---

## 方案一：Tailscale（推荐）

### 简介

Tailscale 是一个基于 WireGuard 的虚拟专用网络（VPN），可以让你在不同设备之间建立加密的点到点连接。

- **免费额度**：3 个用户，无设备数量限制
- **优点**：零配置端口转发、端到端加密、无需公网 IP
- **适用场景**：家里有宽带即可，不需要路由器设置

### 步骤 1：在服务器上安装 Tailscale

**方式 A：使用 winget（推荐）**
```powershell
winget install tailscale.tailscale
```

**方式 B：手动下载**
1. 访问 https://tailscale.com/download
2. 下载 Windows 版本
3. 运行安装程序

### 步骤 2：启动 Tailscale 并登录

```powershell
tailscale up
```

这会打开浏览器，让你用 Google/GitHub/Microsoft 账号登录。

### 步骤 3：配置 Tailscale（可选）

首次运行后，可以配置以下选项：

```powershell
# 开启 SSH（可选，方便远程管理）
tailscale up --ssh

# 允许其他设备发现本地网络设备
tailscale up --accept-routes

# 查看 Tailscale 状态
tailscale status
```

### 步骤 4：获取你的 Tailscale IP

```powershell
tailscale ip -4
```

你会得到一个类似 `100.x.x.x` 的 IP 地址，这就是你在 Tailscale 网络中的内网 IP。

### 步骤 5：在手机上安装 Tailscale

- **iOS**：App Store 搜索 "Tailscale"
- **Android**：Google Play 搜索 "Tailscale"

用同样的账号登录。

### 步骤 6：访问 MusicIQ

现在，在手机上打开浏览器，访问：

```
http://100.x.x.x:8765
```

（将 `100.x.x.x` 替换为你服务器的 Tailscale IP）

### 步骤 7：设置开机自启动

将 Tailscale 设置为开机自启动：

```powershell
# 启动 Tailscale GUI
start "" "%LOCALAPPDATA%\Programs\Tailscale\Tailscale.exe"

# 或者使用命令行启动（无 GUI）
sc config tailscaled start= demand
net start tailscaled
tailscale up
```

### 自动启动脚本（推荐）

创建一个 `velvet_remote_start.bat` 文件：

```batch
@echo off
title MusicIQ Remote Start

echo [*] Starting Tailscale...
start "" "%LOCALAPPDATA%\Programs\Tailscale\Tailscale.exe"

echo [*] Waiting for Tailscale to connect...
timeout /t 10 /nobreak

echo [*] Starting MusicIQ...
call velvet_autostart.bat
```

---

## 方案二：Cloudflare Tunnel

### 简介

Cloudflare Tunnel（以前叫 Argo Tunnel）通过 Cloudflare 的全球网络将你的服务器暴露到互联网，无需公网 IP 和端口转发。

- **免费额度**：每月 1GB 流量
- **优点**：无需安装客户端软件、免费、Cloudflare CDN 加速
- **缺点**：流量经过 Cloudflare 服务器（不是点对点）

### 步骤 1：注册 Cloudflare 账号

1. 访问 https://dash.cloudflare.com
2. 注册账号
3. 将你的域名添加到 Cloudflare（如果还没有域名，可以跳过这步，用免费 tunnel）

### 步骤 2：安装 cloudflared

```powershell
winget install cloudflare.cloudflared
```

### 步骤 3：创建 Tunnel

```powershell
# 登录 Cloudflare
cloudflared tunnel login

# 创建新隧道
cloudflared tunnel create musiciq

# 记下隧道 ID（下一步需要）
```

### 步骤 4：配置 Tunnel

创建配置文件 `cloudflared.yml`：

```yaml
tunnel: <你的隧道ID>
credentials-file: C:\Users\<你的用户名>\.cloudflared\<隧道ID>.json

ingress:
  - hostname: musiciq.yourdomain.com
    service: http://localhost:8765
  - service: http_status:404
```

（需要将 `yourdomain.com` 替换为你的 Cloudflare 域名）

### 步骤 5：启动 Tunnel

```powershell
cloudflared tunnel run musiciq
```

### 步骤 6：访问 MusicIQ

现在可以访问：
```
https://musiciq.yourdomain.com
```

### 自动启动 Tunnel

创建 `tunnel_autostart.bat`：

```batch
@echo off
title Cloudflare Tunnel
cloudflared tunnel run musiciq
```

---

## 对比总结

| 特性 | Tailscale | Cloudflare Tunnel |
|------|-----------|-------------------|
| 速度 | 点对点，快 | 经过 Cloudflare |
| 延迟 | 低 | 视地理位置 |
| 免费额度 | 3 用户，无限设备 | 1GB/月 |
| 设置难度 | 简单 | 中等 |
| 隐私 | 端到端加密 | 数据经过 Cloudflare |
| 域名 | 不需要 | 需要域名 |

---

## 故障排除

### Tailscale 连接不上？

1. 检查服务是否运行：
   ```powershell
   tailscale status
   ```

2. 检查防火墙是否阻止：
   ```powershell
   tailscale status --json
   ```

3. 重新登录：
   ```powershell
   tailscale logout
   tailscale up
   ```

### Cloudflare Tunnel 无法访问？

1. 检查 tunnel 状态：
   ```powershell
   cloudflared tunnel info musiciq
   ```

2. 查看日志：
   ```powershell
   cloudflared tunnel run musiciq --loglevel debug
   ```

### MusicIQ 无法通过远程 IP 访问？

1. 确认 MusicIQ 绑定到所有接口：
   - 检查 `server.py` 中是否使用 `0.0.0.0`

2. 检查防火墙：
   ```powershell
   netsh advfirewall firewall add rule name="MusicIQ" dir=in action=allow protocol=tcp localport=8765
   ```
