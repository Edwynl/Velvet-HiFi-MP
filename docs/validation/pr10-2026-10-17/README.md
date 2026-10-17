# PR #10 — Windows 本地验证（2026-10-17，Australia/Sydney）

**代码修复及本地软件回归：GO。生产合并/发布验收：NO-GO，待真实 Tailnet HTTPS、设备和备份验收。PR 保持 Draft，不合并 main。**

起点：`20e595fe467a765aedebaae21459f3ec78f85cbf`。
实测代码提交：`45b784fca84bd3b764459c3edf2d893928a69619`（`[skip ci]`）。随后仅增加验证记录及更新交接文档。

## 修复与数据关联策略

- 归档文件和原文件同时缺失时，恢复抛出 `ArchiveError`，API 返回 HTTP 409，清单保持 `recovery_required` 并继续出现在回收列表，不返回成功数量。
- 若 `moved=true` 且归档丢失，即使原路径出现文件，也不推断其内容就是原音频。部分成功恢复会持久记录每个条目的状态，后续可以重试；已成功恢复且原文件存在时支持重复调用。损坏清单在移动任何文件前被拒绝。
- **明确处理关联，而非承诺完整数据库恢复：** 有任一播放列表或播放历史关联的专辑，在移动文件及删除数据库行之前返回 409；索引删除和文件归档两种操作均受保护。保留原专辑即可保留 Track ID、播放列表位置/时间及历史记录。
- 使用 `BEGIN IMMEDIATE` 从关联检查一直保护到 SQL 删除提交，避免其他请求在检查后新增关联。回归测试验证竞争写入被阻止、拒绝请求释放锁、SQL 失败恢复文件及原索引、其他专辑关联不受影响。
- 无关联专辑允许删除/归档，但 UI 和 API 明确说明：文件恢复需要重新扫描，Track ID 可能变化。清单不含数据库快照，**无法恢复旧版本已级联删除的播放列表或历史关联**。恢复响应包含 `index_restored=false`、`track_ids_preserved=false`。
- 未改变 schema 9、FLAC 编解码/音质、EXN100 高清封面或 DSP。音频测试只使用临时样本。Windows 真实打开文件句柄导致的重命名失败及释放后恢复已实测。

## 实际结果与原始日志

| 运行 | 结果 | 实际数量 | 日志 |
| --- | --- | --- | --- |
| 原始 `20e595f` 问题复现 | FAIL（预期，证明回归有效） | 1 个 unittest，`moved=true/false` 两个子场景均失败：未抛出异常 | [原始问题复现](baseline-20e595f-reproduction.txt) |
| 首轮定向 Python | FAIL | 54 PASS、1 FAIL；20 个子测试通过 | [失败全文](targeted-python-first-fail.txt) |
| 首轮完整 PowerShell | FAIL，退出码 1 | 224 PASS、1 FAIL；83 个子测试通过；前端未执行 | [失败全文](full-powershell-first-fail.txt) |
| 修正后的定向 Python/API/真实 HTTP 代理 | PASS | 59 PASS、0 FAIL；32 个子测试通过 | [定向结果](targeted-python.txt) |
| 定向前端导航及归档说明 | PASS | 1 个脚本套件 | [前端结果](targeted-frontend.txt) |
| 最终完整 PowerShell（非 Quick，未 SkipInstall） | PASS，退出码 0 | **229 Python PASS、0 FAIL、0 SKIP；95 个子测试通过；12/12 前端/PWA 脚本套件通过**；语法检查、pip 安装、npm ci 成功 | [完整日志](full-powershell.txt) |
| 独立安全/HTTPS/媒体票据/真实 HTTP 代理复核 | PASS，退出码 0 | **33 PASS、0 FAIL、0 SKIP；34 个子测试通过**，含 3 项真实 Uvicorn/HTTP 测试 | [安全日志](proxy-security.txt)、[JUnit XML](proxy-security.xml) |

子测试为 pytest 单独报告的数量，不计作额外顶层测试；33 项安全测试也包含在完整 Python 套件中，不与 229 相加。前端脚本使用 assert 和自定义执行器，没有统一的断言数量统计；因此按 12 个实际执行成功的脚本套件报告。

首轮失败为新增锁模拟测试的路径匹配问题：Windows `filesystem_path()` 添加扩展长度前缀，模拟条件仍比较未转换路径，导致故障未注入。修正测试比较条件后通过，并新增真实 Windows 文件句柄锁测试确认恢复行为。应用恢复逻辑不因此放宽。两次失败日志完整保留，不掩盖失败历史。

最终 Python 运行有 1 条 FastAPI/Starlette TestClient 弃用警告；npm 有 `whatwg-encoding` 弃用提示，审计为 0 vulnerabilities。前端日志中的播放失败/DSP 回退是故障注入测试的预期输出；所有 12 个成功标记和最终退出码为 0。

## Tailscale 代理验证范围

真实 Uvicorn 子进程绑定随机本地端口，使用临时数据库及一次性测试凭据，通过真实 HTTP 请求模拟 Serve 转发头，不使用 TestClient 代替传输。Uvicorn 沿用生产默认代理头处理方式。

- 未设置密码：直接本机请求 200，Serve 请求/仅 Tailnet Host/伪造 `127.0.0.1` 转发地址/`Forwarded`/`X-Real-IP`/Tailscale 用户头均无法读取统计、删除专辑或抢设初始密码（403）。
- 已设置密码：在 `VELVET_AUTH_ENFORCE_ALL_NETWORKS=true` 和 `false` 两种模式下，Serve 请求及伪造 `127.0.0.1`、`::1` 转发地址，无凭据/无效凭据均为 401；有效 API key 可正常读取统计（200）；未授权请求不能关闭鉴权；登录页面仍可访问。
- 独立安全套件还覆盖 HTTPS 链接仅在 Serve 确认后展示、媒体票据作用域/过期/篡改/撤销、封面 cookie 权限及 DLNA 授权边界。
- 本机只读 `tailscale serve status` 退出码为 0，**未发现 HTTPS Serve 配置**。因此本次不宣称真实 Tailnet TLS、手机 PWA、远端网络和设备链路通过；没有开启 Serve/Funnel 或修改 VPN。详见 [环境记录](environment.txt)。

## 复现命令（本地 PowerShell）

```powershell
pwsh -NoProfile -File scripts/windows_pr10_local_test.ps1
$env:VELVET_AUTH_ENFORCE_ALL_NETWORKS = 'true'
.\.venv\Scripts\python.exe -m pytest tests/test_security_boundaries.py tests/test_remote_access_https.py tests/test_media_tickets.py tests/test_tailscale_proxy_security.py -v
```

实测 Windows NT 10.0.26300 X64、PowerShell 7.6.6、Python 3.12.10、pytest 9.1.1、Node 24.13.1、npm 11.8.0、FFmpeg 9.0.2、Tailscale 1.104.1。没有冒称 Node 22 也在本机验证过。

## 交付边界

所有新增提交含 `[skip ci]`；不发起、重跑或启用 GitHub Actions。推送后在 PR 评论中记录新提交对应的 Actions 数量及与推送前快照的比较；[推送前 Actions 快照](actions-before-push.json)仅作历史基线，不代表本次 CI。未修改 PR #2，未合并 main。

本次软件验证没有未解决的测试失败；整体发布仍需真实 Tailnet HTTPS/移动设备、DAC/WASAPI/EXN100 及真实音乐库备份验收。文件系统重命名与 SQLite 仍不是跨系统原子事务，断电后的清单可能需要人工核验。上述限制沿用交接中的验收边界。
