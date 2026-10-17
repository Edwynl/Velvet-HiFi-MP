# VELVET VU Meter refactor — review handoff

**Branch:** `feat/standards-based-vu-meter-ballistics` (based on `main` at `8274dbf`). Keep separate from PR #2 and PR #10. **Do not merge without owner approval.** All commits use `[skip ci]`; do not start or rerun GitHub Actions.

## What changed

- `static/js/vu-meter-engine.js`: independent, testable PCM time-domain full-wave rectified average detector, calibrated to a sine-wave RMS reference. No FFT byte averages or peak-hold lock.
- 2nd-order spring/damping approximation: design step reaches 99% in ~300 ms, ~1.11% overshoot. Integration accounts for real elapsed time (30/60 fps equivalence).
- `static/index.html`: stereo analysers use `getFloatTimeDomainData`, and connect to `AudioContext.destination` through a zero-gain node to keep the silent graph pulled. Default Classic VU at -18 dBFS sine RMS; -20/-18/-15/-12/-9/-6 selectable; Responsive is explicitly visual-only, not a calibrated meter. VU numerals are positioned on a voltage/amplitude-derived arc.
- Audio overlay mirrors the current browser stream for display without changing the main playback routing. Playback retries are throttled and stream-seek sync is reduced; the duplicate audio is silenced through an AudioContext gain node. **This architecture still uses a second stream and can require a second transcoder** — benchmark with network and CPU tools before shipping.
- Service worker cache increment to load new meter script on PWA update. JSDOM harness allowlist extended.
- `tests/frontend/vu-meter.test.mjs` plus `npm run test:vu` and existing `npm run test:frontend` command integration.

## Required local commands (Windows)

```powershell
git switch feat/standards-based-vu-meter-ballistics
npm ci
npm run test:vu
npm run test:frontend
python -m pytest tests -q
```

Only run the last command if local Python dependencies are already installed. Do not claim these suites have passed until actually executed on a checked-out branch. Verify no workflows are queued. Test the full PWA under browser hardware.

## Important reviewer acceptance checks

1. Confirm the new inline classic-script module is served before the main script in `index.html` and the JSDOM fixtures; inspect browser console with DevTools.
2. Confirm stereo L/R independence, relative loudness accuracy at multiple sine test levels, zero-input release, 300-ms response, slight overshoot, and equal time response at 30/60/120 fps.
3. Confirm standard/near-standard ballistics are *a sampled-browser approximation*, not certified IEC 60268-17 compliance. Digital -18 dBFS alignment is an optional engineering convention.
4. Check mobile Chrome Android and Safari iOS VU overlay, lock/unlock, rapid song changes, seek/pause, 30-minute pause, network interruptions, and output mode switching. Confirm the VU never breaks the independent main HTML5 audio playback chain.
5. Check waveforms and CPU usage for 44.1/48/96/192 kHz stereo FLAC; inspect whether the second VU mirror stream requests an additional FFmpeg decode and whether mobile/remote traffic is tolerable.
6. Validate CSS mark/needle geometry on small phones, tablets, desktop and McIntosh-inspired blue mode; in particular the pivot and labels align at 0 VU, -20 VU and +3 VU. Avoid changing cover-art quality.
7. Security: check short-lived media tickets on mirrored request, auth renewal/revocation, failure throttling; do not expose long-lived credentials.
8. Review skip-CI effect carefully: GitHub Actions budgets are exhausted. Do not manually trigger GitHub CI. Keep PR draft, if eventually created.

## Known limitations

- VU works only when using Velvet's browser output; when playing to native DAC / DLNA, the browser stream is not automatically the actual remote signal. The UI explicitly notes browser-only metering.
- The PCM analyzer samples the browser-decoded mirror, not actual external DAC analogue output. Digital meter values are approximate and neither LUFS nor True Peak.
- Browser PCM windows are read on the display frame. Future AudioWorklet integration could improve capture granularity if mobile/browser compatibility is validated.
- Physical devices and full local regression suites have **not** been run in this ChatGPT session; review and validate before merging.

---

## Claude 审核结果（2026-10-17，Windows 11 本地）

审核范围：`8274dbf..c670356` 的 7 个提交，以 Audio DSP + 前端双重标准。修复已直接提交到本分支，未改 PR #2 / PR #10，未合并 main，未改 FLAC 精度或封面画质，未触发 GitHub Actions。

### P0（阻断）

| # | 问题 | 证据 | 处理 |
|---|------|------|------|
| P0-1 | `server.py` 的 `PUBLIC_FRONTEND_JS_MODULES` 白名单不含 `vu-meter-engine.js`，服务器返回 404，`window.VelvetVU` 不存在，VU 显示 "Meter engine unavailable"，等于整个功能失效；SW 预缓存同样拿不到该文件。 | 修复前 `curl /static/js/vu-meter-engine.js` → 404；修复后浏览器内 `fetch` → 200，`typeof VelvetVU === 'object'`。 | 加入白名单；新增 `test_every_shell_and_precached_module_is_served`（index.html 与 sw.js 引用的每个模块都必须在白名单里），已确认修复前红、修复后绿。 |
| P0-2 | 分支交付时 `npm run test:vu` 与 `npm run test:frontend` 都是失败状态。 | `test:vu`：-18.0124 vs -18 ±0.001；300 ms 判定 0.9886 vs 0.99（输入本身低 0.012 dB）。`test:frontend`：`pwa-auth` 预缓存数 10≠9，且硬编码旧缓存名 `velvet-v6`。 | 1 kHz/48k 采样整流均值比连续时间 2/π 低 0.012 dB，属采样效应，容差放宽到 0.02 dB 并注明原因；弹道判定改为相对阶跃目标；`pwa-auth` 改为 10 个资源，缓存名从 sw.js 读取。 |

### P1（应在合并前处理）

| # | 问题 | 证据 | 处理 |
|---|------|------|------|
| P1-1 | 默认 0 VU = -18 dBFS 会让现代母带长时间顶在 +3 端，"卡 75%"变成"卡 100%"。 | 曲库随机 16 首真实 FLAC（44.1/96 kHz）离线跑生产引擎：-18 dBFS 时 9/16 首有 47–93% 的时间顶针（中位 +3…+7 VU）；-12 dBFS 时 15/16 首顶针 ≤2%，指针在 -3…0 VU 附近摆动。 | 默认改为 -12 dBFS（`VU_DEFAULT_CALIBRATION`），-18 仍可选；已加测试。属于产品默认值调整，如需保留 -18 请告知。 |
| P1-2 | 刻度标签与指针弧不对齐：新 CSS 用 `top:0;width:100%` 覆盖，但桌面 ≥1024 px、横屏手机等断点的 `.mobile-vu-markings` 规则带 `!important`，新规则被压掉。 | 844×390 横屏实测标签角度偏差 -6.2°…+6.2°（-20 和 +3 偏离最大）；修复后 1024/691/375/844 宽、Classic 与 Blue 两种风格下 ≤0.4°。 | 覆盖规则加 `!important`；已加测试。 |
| P1-3（原有架构，非本分支引入） | VU 镜像流是第二个 `<audio>`，与主播放同一 URL、独立请求：Tailscale 远程播放时流量约翻倍；升频/DSP 时服务器可能多跑一个 FFmpeg 转码。暂停时镜像只 `pause()` 不释放连接。 | 代码确认（`vuAudio.src = audio.src`）；测试服务器未开访问日志，**带宽与 CPU 未实测**。 | 未改架构，建议后续评估（例如用 AudioWorklet 或复用主元素需另行验证 iOS）。 |

### P2（建议）

- **192 kHz AudioContext 窗口过短（已修复）**：本机 AudioContext 实测为 192 kHz，`fftSize=2048` 只有 10.7 ms（注释假设 48k 下 43 ms），每帧只看到约 64% 的样本，短瞬态会被随机漏检。改为按采样率取窗（`analyserFftSize`：44.1/48k→2048，96k→4096，192k→8192），浏览器实测 192k 下 fftSize=8192。
- **缺少机械止挡（已修复）**：过载时运动状态可超出 +3 VU 很远，+14 VU 过载后要 160 ms 才离开顶针。加入上下止挡后过载释放立即离针；已加测试。
- **iOS 锁屏/切歌后镜像 play() 被拒（已缓解）**：NotAllowedError 会退避 30 秒且无恢复入口。新增"点一下表盘重新启用"（在用户手势内重试），浏览器内模拟退避后点击已恢复；**iOS / Android 真机未测**。
- 采样整流检波在与采样率锁相的纯音上有误差（48k 下 12 kHz 最差 -2.1 dB、8 kHz -0.85 dB），对音乐无实际影响，未改。
- 镜像与主播放允许 0.6 s 漂移才校正，指针可能滞后耳听最多 0.6 s；切歌和切换模式时指针直接复位到静止位，而不是自然回落。未改。
- 刻度位置是"-20 VU 在静止针位"的仿射映射（真实 VU 中 -20 VU 约在 7% 偏转处），0 VU 位于 68.5%（真表约 71%）。刻度与指针内部自洽，属可接受的设计取舍。
- 小尺寸横屏下 -7/-5/-3 标签拥挤，是线性电压刻度的固有特性。

### 关于"某些歌曲指针停在 75%"

根因已确认：旧代码对 `getByteFrequencyData`（-100…-30 dB 映射到 0–255 的**对数**字节）求平均，再乘 2.45 并做 0.72 次幂。任何平均频点高于约 -80 dB 的音乐都会被推到软件上限 +12°（旧量程 -48…+12°，在旧表盘上约 3/4 处），峰值保持又让它很难回落。复现旧算法跑同样 16 首歌：p10–p90 基本全在 +11…+12°，标准差约 2°。新代码用线性 PCM 时域整流均值，同样 16 首歌标准差 9–30°。Responsive 模式对平稳信号 20 秒内摆动 <0.01°（已加测试），**不是**人为制造跳动。

### 实测证据

- 浏览器端到端（Chromium 预览，AudioContext 192 kHz，RAF 约 241 fps）：JS 生成 1 kHz 测试音 L=-12 dBFS、R=-18 dBFS sine RMS。检波读数 -12.03 / -18.03 dBFS；指针 L 22.19°（0 VU 预期 22.28°）、R -23.37°（-6 VU 预期 -23.32°），左右声道独立。暂停 400 ms 内回到 -60°，镜像随之暂停；恢复后回到 22.19°；关闭 VU 后 AudioContext 与镜像均释放，主播放继续推进；控制台无 JS 错误（只有测试库缺封面导致的 404，以及伪造 track id 0 的 played 上报 500）。
- `npm ci`：0 vulnerabilities。
- `npm run test:vu`：PASS。
- `npm run test:frontend`：11 项全部 OK/PASS。
- `python -m pytest tests -q`（项目 `venv`，Python 3.12.10）：190 passed，67 subtests passed。注意：本机 PATH 中的 `python` 指向 hermes-agent 虚拟环境，没有安装 pytest，因此用 `venv\Scripts\python.exe` 执行。

### 未覆盖

iOS Safari / Android Chrome 真机、锁屏 30 分钟、网络中断、DLNA/原生 DAC 输出切换、Tailscale 带宽和服务器 CPU 都未实测。
