# Velvet Remote Bridge（Windows）— 安装和使用

**用途：** 父母家的 Velvet 是唯一音乐服务器、唯一搜索索引和唯一 FLAC 文件所在地。你家 Windows 只运行这个轻量 Bridge；iPhone 打开 Bridge 提供的 Velvet 页面，用原来的 Artist / Album / Track 搜索和播放列表，选择本地 Cambridge Audio EXN100 作为输出。**不需要 StreamMagic 整理曲库，不需要 AirPlay，不需要 USB，不复制音乐文件。**

> 版本：0.1.9，测试版。已完成模拟父服务器 / 模拟 DLNA 设备离线测试。**尚未在真实 Windows + Tailscale + EXN100 上验收**。首次播放需要做实体设备测试。

## 网络拓扑

```
父母家 Windows                         你家 Windows                              EXN100
  Velvet :8765 -- Tailscale 加密 -->   Remote Bridge 控制 :8877
  唯一 FLAC 和数据库                    Remote Bridge 音频 :8878  -- 家用 LAN -->  原生 FLAC 解码
                                             ^
                                             |
                                       iPhone 的 Safari
                                   使用 Velvet 搜索、排队、选 EXN100
```

## 一、安装准备

1. **父母家 Windows：** Velvet 已正常运行，曲库可通过 Tailscale 访问。记下父母家 Windows 的 Tailscale IP（例如 `100.101.102.103`），一般 Velvet 端口为 `8765`。必须先在父母家 Velvet 主机上设置好密码：main 版 Velvet 把投放（Cast）当作管理员操作，第一次选 EXN100 播放时会要求输入管理员密码。
2. **你家 Windows：** 安装并登录 [Tailscale](https://tailscale.com/download)，加入与父母家相同的 tailnet。安装 **Python 3.10 或更新版本**（[python.org](https://www.python.org/downloads/windows/)；安装时勾选 *Add python.exe to PATH*）。无其他 Python 包依赖。
3. **EXN100：** 通过 Wi-Fi 或 Ethernet 接入你家网络，且与 Windows 的局域网网卡可互访（避免访客隔离或不同 VLAN）。Windows 防火墙允许 Python 在 **Private/专用网络** 通信。不要在路由器上做公网端口映射。

## 二、在你家 Windows 启动

1. 解压整个 `Velvet-Remote-Bridge` 文件夹，例如 `C:\Velvet-Remote-Bridge`。
2. 双击 **`start_bridge.bat`**。第一次会创建 `config.json` 并打开记事本。
3. 把 `upstream_url` 改成父母家的真实 Velvet 地址。例如：

   ```json
   {
     "upstream_url": "http://100.101.102.103:8765",
     "control_bind": "auto",
     "control_port": 8877,
     "lan_ip": "auto",
     "media_port": 8878,
     "renderer_network": "auto",
     "preferred_renderer": "EXN100",
     "stay_awake": true
   }
   ```

4. 保存记事本，关闭记事本窗口。`start_bridge.bat` 会继续运行。看到日志中的 `Control UI`、`EXN100 media (LAN)`、`Parent Velvet` 即表示两个监听端口已启动。
5. 在你家 Windows 浏览器打开 `http://127.0.0.1:8877/bridge/health`（如果 `control_bind=auto` 选择的是 Tailscale IP，则需打开 `http://你家Windows的TailscaleIP:8877/bridge/health`）。确认有正常 JSON。

6. **Windows 防火墙（推荐一次性设置，需管理员 PowerShell）。** 第一次启动时 Windows 可能弹出"允许 Python 通信"，务必勾选 **专用网络**。如果家里网络被识别为"公用"，或者弹窗被关掉了，EXN100 会连不上音频端口。可以直接执行：

   ```powershell
   New-NetFirewallRule -DisplayName "Velvet Bridge media (LAN)" -Direction Inbound -Protocol TCP -LocalPort 8878 -RemoteAddress LocalSubnet -Action Allow
   New-NetFirewallRule -DisplayName "Velvet Bridge control (Tailscale)" -Direction Inbound -Protocol TCP -LocalPort 8877 -InterfaceAlias Tailscale -Action Allow
   ```

7. **让你家 Windows 随时可被远程使用（必需）。** 从手机远程开始播放时，手机要先通过 Tailscale 连上这台 Windows；它一旦因空闲睡着，第一次播放就无法开始（Bridge 收不到任何请求，也无法自己唤醒）。所以这台 Windows 平时无人使用时也必须醒着、Tailscale 在线、Bridge 在运行：
   - **电源（Bridge 已默认处理空闲睡眠）：** `config.json` 的 `"stay_awake": true`（默认）表示只要 Bridge 在运行，就阻止 Windows 因空闲进入睡眠（屏幕仍可照常关闭）。它挡不住：开始菜单里的"睡眠"、按电源键、笔记本合盖、Windows 更新后的自动重启。建议再把电源计划设为接通电源时从不睡眠、从不休眠（管理员 PowerShell）：

     ```powershell
     powercfg /change standby-timeout-ac 0
     powercfg /change hibernate-timeout-ac 0
     ```

     笔记本请在"选择关闭盖子的功能"中把"接通电源"设为"不采取任何操作"。**检查方法：** Bridge 运行时，在管理员 PowerShell 执行 `powercfg /requests`，`SYSTEM:` 一栏下应能看到 `python.exe`。
   - **Tailscale 无人值守：** 任务栏 Tailscale 图标 → Preferences → 勾选 **Run unattended**。否则这台 Windows 注销或重启后、没人登录时，就不在 tailnet 上，手机连不上。
   - **开机自启：** 任务计划程序 → 创建任务 → 触发器"登录时" → 操作"启动程序"选 `start_bridge.bat`，"起始于"填 Bridge 所在文件夹。Windows 重启后需要有用户登录才会启动 Bridge：可用 `netplwiz` 设置自动登录；或把任务设为"不管用户是否登录都要运行"（这样不会出现防火墙弹窗，请先用上一步的命令建好防火墙规则）。
   - 若把 `stay_awake` 设为 `false`，Bridge 只在播放或暂停期间阻止睡眠（旧版行为）；空闲后 Windows 可能睡着，手机就无法远程开始播放，除非你已把电源计划设为从不睡眠。
   - 父母家那台 Windows 同样不能睡眠：请在其电源设置中设为"从不睡眠"，保持 Velvet 主机常开。

> **自动 IP 选择错误时：** Bridge 会优先使用默认路由（通常是 Wi-Fi 或以太网）的地址，不会再误选 Hyper-V / WSL / Docker 虚拟网卡；启动日志中的 `EXN100 media (LAN)` 地址应与 `ipconfig` 里 Wi-Fi/以太网的 IPv4 一致。若开启了 Tailscale 出口节点（exit node）或仍然不对，请手动设置。 用 Windows `ipconfig` 查看你家 Windows 的 Wi-Fi/以太网 IPv4 地址，把 `lan_ip` 从 `auto` 改为该地址（如 `192.168.1.20`）。用 `tailscale ip -4` 查看你家 Windows 的 Tailscale IP，把 `control_bind` 改为该地址（如 `100.110.120.130`）。`renderer_network` 默认按 LAN IP 的 `/24` 子网计算；若 EXN100 使用非 `/24` LAN，请改为实际网段，如 `192.168.0.0/23`。

## 三、在 iPhone 通过 Velvet 搜索和播放

1. iPhone 开启 Tailscale，并连接与 Windows 相同的 tailnet。
2. 打开 Safari：`http://你家Windows的TailscaleIP:8877`，例如 `http://100.110.120.130:8877`。
3. 你会看到**原来的 Velvet 网页**，无需新增一个音乐库。父母家 Velvet 会把 Bridge 当作同一 tailnet 内的听众，通常**不会**要求登录；第一次投放到 EXN100 时会弹出 Administrator Verification，输入主服务器的管理员密码即可（该浏览器会话内有效）。
   如果想用 MagicDNS 名字（如 `http://home-pc:8877`）而不是 IP 打开，且出现 `421` 错误，请把这个名字加入 `config.json` 的 `allowed_hosts`，例如 `"allowed_hosts": ["home-pc"]`。
4. 打开 Velvet 的 **输出 Zones/设备选择**，等待约 2 秒的局域网设备扫描，找到 **EXN100**（或 Cambridge Audio 型号名）。
5. 把 **DSP/EQ 设为 Off，ReplayGain 设为 Off**。这版只支持原始音乐文件直传，不做音频转码。
6. 从 Velvet 搜索歌手、专辑或曲目并点击播放；EXN100 通过局域网直接向你家 Windows 请求音频，你家 Windows 再从父母家 Velvet 流式读取原始 FLAC。

**StreamMagic 无需用于搜索。** 它仍可用于观察 EXN100 的播放状态以及设备设置。手机是遥控器，不承担 FLAC 音频中转。

**锁屏后整张专辑 / 歌单照样连续播放。** iPhone 锁屏后 Safari 会暂停网页，Velvet 网页自己的"自动下一首"也就停了。因此 Bridge 在转发 Velvet 页面时加入了一个小脚本（`/bridge/queue-sync.js`），把当前播放队列（只有曲目 ID、位置和重复模式）告诉 Bridge；由你家的 Windows Bridge 检测 EXN100 一首播完，再把下一首交给 EXN100。手机重新打开时，页面会自动跳到 EXN100 正在播的那首，不会重新起播。队列只保存在 Bridge 内存里，不建第二套曲库。顺序与 Velvet 投放时一致：播到队列末尾停止；"全部循环"回到第一首；"单曲循环"在投放模式下与 Velvet 相同，继续下一首。在 Velvet 里点停止、换歌、上一首/下一首，Bridge 会立即跟随；即使与自动切歌同时发生，也以你的操作为准。

**Safari / PWA 被系统完全重新加载后**（iOS 有时会丢弃后台页面），重新打开 Bridge 地址时，页面会自动接回正在播放（或暂停）的 EXN100：显示当前曲目和整张队列，可直接暂停、切歌、停止，不会重新起播。只有在这个新页面上你还没有选择输出、也没有播放任何东西时才会自动接回；一旦你自己选了输出（比如本机播放器）或播放了别的，就不会再被接管。接回时也会恢复"重复"模式（例如全部循环），并且只是显示、不会改动 Bridge 上的队列。另一台手机换了歌，这台手机也会跟着显示。

**手机和平板同时使用时**，一台设备对队列的修改（随机播放、重复模式、增删曲目）会在约 2 秒内出现在另一台上，即使正在播的是同一首歌。两台设备几乎同时修改时，各改各的部分（比如一台打乱顺序、另一台切换重复）会合并保留；如果两台都改了曲目顺序，以先到 Bridge 的为准，后到的设备会显示"The queue was changed on another device"并切换为那个版本，不会悄悄覆盖。每台设备第一次修改队列时，若该浏览器会话还没输入过管理员密码，会弹出 Administrator Verification。

**一台设备停止，所有设备都停止。** 手机 A 停止 EXN100（例如把输出切回本机播放器）后，Bridge 立刻结束这次播放；手机 B 会在约 2 秒内显示"EXN100 stopped playing"并停止跟随，即使停止发生在一首歌的最后几秒，也不会有任何设备自动接着播下一首。同样，如果有人在 StreamMagic 里用 EXN100 播别的内容，所有 Velvet 页面都会显示已停止，不会把它抢回来。之后在任意设备上点一首歌即可重新投放；只按播放键会提示"Playback was stopped: choose a track to play it again"。自动下一首只由 Bridge 决定，网页本身不再在曲终时自行投放。

**队列中某首无法播放时**（父母家曲库里已删除、格式不支持，或 EXN100 拒绝），Bridge 会跳过它继续播下一首，并在页面上提示跳过了几首；最多绕队列一圈，不会无限循环。父母家 Velvet 暂时连不上（Tailscale 断线、重启）时不会跳歌，而是逐渐拉长间隔重试 4 次，仍失败才停下并提示原因。

**已知限制：** UPnP 不告诉 Bridge 渲染器为什么停止。在 EXN100 或 StreamMagic 上手动按停止时，如果离曲目结尾已不足约 1.5 秒，Bridge 无法与自然播完区分，可能会接着播下一首；更早按停止则不会。要可靠地结束播放，请在 Velvet 页面里停止或切换输出。

## 四、音质和安全说明

- 对 **FLAC**：Bridge 不解码、不重新编码、不重采样，仅逐字节转发父母家 Velvet 的原始 HTTP 音频流。支持 DLNA 常见 `HEAD`、`GET`、`Range`；EXN100 的实际播放格式仍需在设备上实测。
- 当前版本不支持远程的 Velvet DSP/EQ 和 ReplayGain，也**不保证 DSD/DoP bit-perfect**。某些非 FLAC 音频容器的 DLNA 兼容性仍需验证。
- **无第二套音乐库、无 SMB 映射、无本地曲库扫描**。Bridge 不在磁盘写入音乐文件、数据库或 API 密钥；播放时只在内存保存短期播放授权。
- 控制入口默认只监听你家 Windows 的 **Tailscale IPv4**（检测不到时退回 `127.0.0.1`），**不会**默认开放到整个 LAN；音频入口只监听你家 Windows 的 LAN IPv4 且媒体 URL 只能从被选中的 EXN100 IP 使用。
- 父母家 Velvet 信任 Bridge 转发的每一个请求（它是 tailnet 内的设备）。所以 Bridge 自己会检查是**哪台 tailnet 设备**在访问 `:8877`：默认只允许与这台 Bridge 电脑**同一个 Tailscale 账号**的设备（你的 iPhone / iPad）；父母或其他账号的设备、tag 设备一律拒绝（403）。如需放行，在 `config.json` 的 `allowed_peers` 写入对方的 Tailscale 登录名或 Tailscale IP，例如 `"allowed_peers": ["you@example.com", "100.101.1.20"]`（写了列表后只认列表）。读不到 `tailscale status` 时一律拒绝，只有本机浏览器例外。身份只按真实 TCP 来源 IP 在 tailnet 中查找；请求自带的 `Tailscale-User-Login`、`X-Forwarded-For` 等头一律不作数，只有本机 Tailscale Serve 确实代理到 8877（且未开 Funnel）时，才采信 Serve 注入的登录名。这些头也不会转发给父母家 Velvet。也建议在 Tailscale 管理后台的 ACL 里把 `:8877` 限定给自己的设备。**绝对不要**对 Bridge 开启 Tailscale **Funnel**（公网发布），否则任何人都能收听整个曲库。Bridge 只接受以 Tailscale IP、本机名 / MagicDNS 名或 `allowed_hosts` 访问，防止恶意网页通过 DNS rebinding 借道。
- 局域网内任意设备都能回应 SSDP 自称是播放器；Bridge 会去掉设备名中的 HTML 字符，防止恶意设备名在 Velvet 页面里执行脚本。
- **不要**为 `8877/8878` 配置路由器端口转发或公网开放规则。Windows 防火墙建议把 `8878` TCP 入站限定为 EXN100 的局域网 IP，只选 Private 网络。
- 网络需要父母家的**上传带宽**能够持续支持 FLAC 码率；高解析和多房间同时播放需要更高带宽。
- 这是正常的在线串流；父母家断网、Velvet 停止或任一台 Windows 睡眠，播放就会中断。两台 Windows 在听歌时均须保持开机。
- iOS Safari 通过 HTTP 的 Tailscale IP 可作为网页访问，但完整 PWA/Service Worker 要求 HTTPS 安全来源；本测试版不自动部署 HTTPS。

## 五、排查问题

| 现象 | 检查项目 |
|---|---|
| Safari 无法打开 Bridge | `start_bridge.bat` 是否运行？`control_bind` 是不是你家 Windows 的 Tailscale IPv4？iPhone 是否已连接 Tailscale？|
| 登录 Velvet 失败 | 父母家的 `http://100.xxx.xxx.xxx:8765` 是否可在你家 Windows 浏览器打开？主服务器是否已初始化密码？|
| 找不到 EXN100 | 确保 EXN100 和 Windows 在同一 LAN/VLAN；Windows 防火墙允许 UDP 1900 出站与回应；检查 `lan_ip`、`renderer_network`。|
| EXN100 显示设备但无法播放 | Windows 防火墙允许 `8878/TCP` 的来自 EXN100 的入站连接；关闭 DSP/EQ 和 ReplayGain；确认媒体流从父母家 Velvet 可读取。|
| 投放时弹出管理员验证 | 正常：main 版 Velvet 的投放需要管理员会话。输入父母家 Velvet 的管理员密码后会自动重试。|
| 页面显示 `421` | 用 Tailscale IP 打开 Bridge，或把所用的名字加入 `allowed_hosts`。|
| 页面显示 `This Tailscale device may not use the bridge` | 该设备不属于 Bridge 电脑的 Tailscale 账号；把它的登录名或 IP 加入 `allowed_peers`，并确认 `tailscale status` 在 Bridge 电脑上可用。|
| 暂停很久后继续播放没声音 | 渲染器 5 分钟不读取数据，Bridge 会断开该连接（避免线程长期挂起）；正常的 DLNA 播放器继续时会用 Range 重新请求。如果 EXN100 实测不能恢复，请记录下来反馈。|
| 锁屏后播完一首就停 | 确认是用 Bridge 地址（`:8877`）打开的 Velvet，而不是直接打开父母家的 `:8765`；Bridge 日志在切歌时会显示 `Advanced to queue position N`。|
| 选了 DSP/EQ 预设后无法投放 | 这版只直传原始文件；把该 Zone 的 DSP/EQ 设为 Off、ReplayGain 设为 Off。|
| 播放较长歌曲中断 | 检查父母家上传速度、Tailscale 稳定性、Windows 电源设置、父母家 Velvet 服务状态。|
| 封面、收藏或播放历史不同步 | 正常情况下它们仍由父母家唯一 Velvet 数据库提供；如果出现不同步，先检查 Bridge 是否打开了正确的父母家 `upstream_url`。|

## 六、开发与本地测试

打包下载用 ZIP（只打包已提交的文件，文件名带版本号和提交号，旧 ZIP 不会被误当成新版）：

```powershell
python build_zip.py
```


双击 **`run_tests.bat`**，或在终端进入目录运行 `run_tests.bat --no-pause`。它依次运行两套测试：Bridge 本身（Python）和注入 Velvet 页面的队列同步脚本（Node.js 18 或更新版本，无需安装任何包；没装 Node 会提示并算作失败）。两套都通过才显示 `ALL TESTS PASSED` 并返回退出码 0，否则显示 `TESTS FAILED` 并返回 1，方便脚本判断。也可分开运行：

```powershell
python -m unittest discover -s tests -v
node tests/queue_sync.test.mjs
```

浏览器手动验收（真实 Velvet 页面 + 按真实时间播放的模拟 EXN100，步骤写在脚本开头）：`python -I tests/manual_browser_stack.py <仓库根目录> <venv 的 python> <ffmpeg>`；加上 `ADMIN_PASSWORD` 后再运行 `node tests/manual_admin_two_devices.mjs`，可自动完成启用管理员密码的双设备验收。

此测试无需真实 EXN100，覆盖控制接口、主服务器认证、管理员验证、反向代理、局域网设备解析、媒体字节一致性、HEAD、Range、短时播放票据权限、停止播放后撤销授权。真实 EXN100 的 DLNA 兼容性与 Windows 防火墙仍需现场验收。

## 设计和版本边界

此 Bridge 是一个**独立可下载目录**，**没有修改父母家 Velvet 原有的 Python 服务或数据库**，通过与当前 `Velvet-HiFi-MP` `main` 的 HTTP API 兼容来实现。新版 Velvet 如果重构 `/api/zones` 或 `/api/cast` 等接口，Bridge 可能需要同步升级。
