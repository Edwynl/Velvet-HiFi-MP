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

---

## 第二轮审核修复（2026-10-17，基于 `c2f8087`）

### P1-4 首次 VU play() 被节流（已修复）

- **根因**：`vuLastPlayAttemptAt = 0` 加上 `now - vuLastPlayAttemptAt >= 1200` 的判断，意味着 `performance.now()` 小于 1200 时（页面加载后 1.2 秒内），VU 打开手势里的首次 `play()` 会被跳过。等到下一次重试时已经不在手势内，iOS 会拒绝。上一轮加的"点按重试"也重置为 0，同样中招。
- **修复**：重试策略抽成 `VelvetVU.createPlayGate()`，初始值为 `-Infinity`。首次请求和点按、换曲后的 `rearm()` 都立即放行；同一时间只允许一个 `play()` 在途；普通重试间隔 1.2 秒；`NotAllowedError` 退避 30 秒，并把 `needsGesture` 置位，界面显示 "Tap the meters to enable"；401/404/无法解码之类的错误退避 5 秒；`AbortError` 按普通间隔重试。
- **测试**：`test:vu` 覆盖页面年龄 0/16/500/1199 ms 时首次请求都放行，以及在途、重试、拒绝、点按、失败、中断各状态。变异测试：把初值改回等效旧代码的 `nextAt = 1200`，测试报 "first play() throttled at 0 ms"。浏览器实测：把 `performance.now()` 固定在页面年龄约 300 ms 时打开 VU，250 ms 内镜像已经在播。

### P1-5 镜像与主播放争抢转码槽位（已修复镜像部分）

- **根因**：镜像直接复制 `audio.src`。升频或 DSP 时，每路流各占一个 FFmpeg 槽位（`max_concurrent_streams` 默认 3），所以每个开着 VU 的听众占 2 槽。槽位满时服务器先等 3 秒，再返回 503；如果新镜像比新主播放先抢到槽位，503 就会落在主播放上。
- **修复**：`VelvetVU.mirrorSourceUrl()` 把镜像改成同一首歌的直通流：`upsample=none`，去掉 `dsp` 和 `quality`，保留 stream ticket。直通流不占槽位，FLAC 原文件的流量也小于升频 WAV。升频不改变电平；DSP 打开时 VU 读的是源信号（pre-DSP），界面显示 "VU signal active · source, pre-DSP"。
- **资源释放**：暂停（以及主播放缓冲不足）时 `releaseVUMirrorSource()` 移除 `src` 并调用 `load()`，断开镜像的 HTTP 连接；切到 Cast/DLNA/原生输出时释放整个 AudioContext 和镜像，切回浏览器输出后自动重建；关闭 VU 的释放逻辑不变。
- **测试**：
  - `pytest`：`test_vu_mirror_direct_stream_needs_no_transcode_slot`。槽位全占满时直通请求仍返回 200/206，且不占槽；同状态下的 DSP 请求返回 503。
  - `test:vu`：URL 推导单元测试，以及 `syncVUAudioToPlayback` 不再出现 `vuAudio.src = audio.src` 的断言。变异测试：改回直接复制 URL，测试报错。
- **实测**（Windows，测试实例，`upsample=2x`）：开着 VU 时只有 1 个 ffmpeg 进程，镜像 URL 为 `upsample=none`，漂移 -12 ms。400 ms 间隔连续切 6 首，主播放没有 error 事件且时间在走；ffmpeg 数在 5 秒内回到 1；服务器日志里没有 503。

### P1-6（原有问题，非本分支引入，未修复）服务器回收旧转码的速度接近 3 秒等待窗口

用 `curl --limit-rate 100k` 模拟 2 个慢速远程客户端占住 2 个槽，浏览器再切到另一首升频曲目：主播放报 `MEDIA_ERR_SRC_NOT_SUPPORTED`（即 503）。**修复前后的镜像行为都会出现这个结果**：同一首歌在修复版上重跑，同样失败。这说明问题与 VU 无关，而是上一首主播放的 ffmpeg 在客户端断开后约 2–5 秒才被回收，超过了 `TRANSCODE_SLOT_WAIT_SECONDS = 3`。（我最初做的"旧版失败、新版成功"对比用了不同曲目，结论不成立，已作废。）本地回环下浏览器几秒就能下完转码流、槽位很快释放，所以单客户端测不出来；Tailscale 等慢链路更容易触发。建议另开任务，评估断开时立即终止 FFmpeg 或延长等待窗口。

### P2 修复

- **镜像漂移**：阈值从 0.6 s 收紧到 0.25 s（`VU_MAX_DRIFT_SECONDS`），每 400 ms 检查一次。镜像现在一定是直通流，支持 Range，seek 开销低。实测漂移 -12 ~ +43 ms。
- **suspended 状态下重复 resume()**：`resumeVUContext()` 保证同一时间只有一个 `resume()` 在途，最多每秒 1 次；点按表盘时强制执行一次。实测：模拟 Chrome（`resume()` 一直挂起）时，3 秒 722 帧只调用 1 次；模拟 Safari（`resume()` 被拒绝）时，3 秒 3 次。修复前是每帧一次。
- **Responsive 刻度真实性**：Responsive 模式给浮层加 `vu-relative` 类，VU 数字刻度降到 30% 不透明度，校准下拉框禁用，状态显示 "Responsive · relative scale, not VU"。
- **恢复播放提示**：镜像被拒绝或 AudioContext 处于 suspended 时，显示 "Tap the meters to enable"。点按后实测 AudioContext 恢复为 running。

### 第二轮测试证据（Windows 11，本地）

- `npm ci`：0 vulnerabilities
- `npm run test:vu`：PASS
- `npm run test:frontend`：11 项全部 OK/PASS
- `python -m pytest tests -q`（项目 `venv`）：191 passed，67 subtests passed
- 测试结束后无残留 ffmpeg 进程

### 兼容性

主播放链路（`audio` 元素、stream ticket、FLAC 直通、WASAPI/DLNA/原生输出、封面、PWA 缓存）的代码未改动。镜像只在 VU 打开且为浏览器输出时存在。仍未覆盖 iOS/Android 真机和真实 Tailscale 链路。
