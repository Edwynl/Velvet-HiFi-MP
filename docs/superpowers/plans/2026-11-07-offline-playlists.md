# 歌单离线下载 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 iPhone 主屏幕 PWA 中把整张歌单的原文件下载到本地，未开 EQ 时播放直接读本地，断网可用。

**Architecture:** 新增经典脚本模块 `static/js/offline-store.js`，导出 `window.VelvetOffline`：
纯逻辑的下载/引用管理器（依赖注入的 store，可在 node 中用内存 store 测试）+ IndexedDB 适配器。
播放侧在 `startBrowserPlayback()` 中**同步**查询已预热的 blob URL（不在 `play()` 之前 await，保护 iOS 手势链），
歌单页/歌单列表页只追加按钮、标记与离线回退渲染。

**Tech Stack:** 原生 JS（classic script，全局函数）、IndexedDB、Blob URL、Service Worker、node + jsdom 测试。

**Spec:** `docs/superpowers/specs/2026-11-07-offline-playlists-design.md`

## Global Constraints

- 排版只加不改：不移动/重排已有元素，不改变曲目行高；430px 宽度下歌单工具栏不换行。
- 下载 URL 固定为 `/api/stream/{id}?upsample=none`（+ `&ticket=…` 若有），不带 `dsp`、`seekable`、`container`。服务器端零改动。
- API key 绝不进 URL；只用现有短期 stream ticket（`currentStreamTicket()` / `refreshStreamTicket()`）。
- 本地优先规则：`state.dspProfile` 为空 **且** `(state.upsample||'none') === 'none'` → 播本地；否则走在线，仅在服务器不可达时回退本地并 toast `Offline copy, EQ off`。
- UI 文案英文，沿用 `btn btn-ghost`、`showConfirm`、`showToast`。
- `window.VelvetOffline` 不存在时（现有测试夹具），所有页面与播放行为与现在完全一致。
- 一次只下载一首；单曲为续传粒度；每首失败重试 2 次后跳过计数。

## Review Focus

1. 冷启动首次点播（未预热）已下载曲目：不能因 await IndexedDB 导致 iOS 拒绝 `play()` 而无提示 → 走现有 `Tap play again` 提示路径（Task 2 测试）。
2. 两张离线歌单共享同一首歌，删除其中一张 → 共享曲目保留（Task 1 测试）。
3. 下载中途响应被截断（`Content-Length` 与 blob 大小不符）→ 不得存为已完成（Task 1 测试）。
4. 开 EQ、Tailscale 断开 → 不能无限重试在线流，应回退本地（Task 2 测试）。
5. 存储满 `QuotaExceededError` → 停止整张下载、保留已下载、toast `Not enough storage on this device`（Task 1 测试）。

---

### Task 1: 离线存储与下载管理模块

**Files:**
- Create: `static/js/offline-store.js`
- Create: `tests/frontend/offline-store.test.mjs`
- Modify: `static/index.html`（在 `vu-meter-engine.js` 的 script 标签后加 `<script src="/static/js/offline-store.js"></script>`）
- Modify: `tests/frontend/inline-modules.mjs`（ALLOWED 加 `offline-store`）
- Modify: `sw.js`（`STATIC_ASSETS` 加 `/static/js/offline-store.js`，`CACHE_NAME` 版本号 +1）
- Modify: `package.json`（`test:frontend` 末尾串联新测试）

**Interfaces:**
- Produces（全局）：
  - `createOfflineManager({ store, fetchTrack, createObjectURL, revokeObjectURL })` → manager
  - `createIdbOfflineStore(indexedDB)` → store（DB `velvet-offline` v1；对象仓库 `tracks` 键 `id` 值 `{id, blob, size, type, savedAt}`；`playlists` 键 `id` 值 `{id, name, description, tracks, trackIds, savedAt}`）
  - store 接口（所有方法返回 Promise）：`listTrackIds(): number[]`、`getTrack(id)`、`putTrack(rec)`、`deleteTracks(ids)`、`listPlaylists()`、`getPlaylist(id)`、`putPlaylist(rec)`、`deletePlaylist(id)`
  - manager 方法：
    - `init(): Promise<void>` — 载入已下载曲目 id 集合
    - `has(trackId): boolean`（同步）
    - `urlFor(trackId): string`（同步，未预热返回 `''`）
    - `load(trackId): Promise<string>` — 预热单首并返回 blob URL（不存在返回 `''`）
    - `warm(trackIds): Promise<void>` — 预热，内存中最多保留 4 个 URL，超出按最久未用 revoke
    - `status(playlist): {total, downloaded, missing}`（同步，基于 `has`）
    - `download(playlist): Promise<{downloaded, failed, stopped}>` — 先 `putPlaylist`，再顺序下载缺失曲目
    - `pause(): void`、`job(): {playlistId, done, total} | null`
    - `remove(playlistId): Promise<void>` — 删歌单清单，仅删不再被其他离线歌单引用的曲目
    - `getPlaylist(id)`、`listPlaylists()`、`usage(): Promise<number>`（字节）
    - `onChange(fn): () => void` — 进度/状态变化订阅，返回退订函数
  - `window.VelvetOffline`：浏览器中由模块末尾创建（`indexedDB` 存在时），用 `createIdbOfflineStore(indexedDB)`；`fetchTrack` 由 `configure({ getTicket })` 注入的票据拼 URL 后 `fetch`。

- [ ] **Step 1: 写失败测试** `tests/frontend/offline-store.test.mjs`：用 `vm` 运行模块源码，内存 store + 假 `fetchTrack`。断言：
  - `download({id:1, tracks:[{id:11},{id:22}]})` 后 `has(11)&&has(22)`，`status(...)` 为 `{total:2, downloaded:2, missing:0}`。
  - 再下载同一歌单不重复调用 `fetchTrack`（续传跳过已完成）。
  - 歌单 1 `[11,22]`、歌单 2 `[22,33]` 都下载后 `remove(1)` → `has(11)===false`，`has(22)===true`，`has(33)===true`。
  - `fetchTrack` 返回 `{blob:{size:5}, contentLength:10}` → 该曲目不存，结果 `failed===1`。
  - 某曲目连续抛错 3 次 → 跳过，`failed===1`，其余照常；`fetchTrack` 对该曲目恰被调用 3 次。
  - `putTrack` 抛 `{name:'QuotaExceededError'}` → `download` 结果 `stopped==='quota'`，之前已存的曲目仍 `has`。
  - `pause()` 后当前曲目结束即停止，`stopped==='paused'`；`job()` 在下载期间返回 `{playlistId:1, done, total:2}`，结束后为 `null`。
  - `warm([1..6])` 后 `revokeObjectURL` 被调用 2 次；`urlFor` 对最近 4 个非空。
- [ ] **Step 2:** `node tests/frontend/offline-store.test.mjs` → FAIL（`createOfflineManager is not defined`）。
- [ ] **Step 3:** 实现 `static/js/offline-store.js`（含 IndexedDB 适配器、`configure`、成功下载后 `navigator.storage?.persist?.()`）。
  `fetchTrack(id, signal)` 返回 `{blob, contentLength}`；非 2xx 抛错。
- [ ] **Step 4:** 加 script 标签、inline-modules ALLOWED、sw.js、package.json。运行 `npm run test:frontend` → 全部 PASS。
- [ ] **Step 5:** Commit `feat(offline): 歌单离线存储与下载管理模块`

### Task 2: 播放接入（本地优先 + 断网回退）

**Files:**
- Modify: `static/index.html` — `startBrowserPlayback()`、`audio` 的 `error` 监听、`playTrack()`（预热）、启动 `DOMContentLoaded`（`VelvetOffline?.init()` 与 `configure`）
- Create: `tests/frontend/offline-playback.test.mjs`
- Modify: `package.json`

**Interfaces:**
- Consumes: Task 1 的 `has / urlFor / load / warm`。
- Produces（index.html 内全局）：
  - `browserPrefersLocal(): boolean` — `!state.dspProfile && (state.upsample||'none')==='none'`，或 `browserForceLocal === true`
  - `let browserForceLocal = false` — 服务器不可达时置 true；`window` 的 `online` 事件置回 false
  - `localPlaybackUrl(trackId): string` — `browserPrefersLocal() && VelvetOffline?.has(id)` 时返回 `urlFor(id)`，否则 `''`

- [ ] **Step 1: 写失败测试**（仿 `stream-ticket.test.mjs` 用 `between()` 抽取源码到 vm）：
  - 无 DSP、已下载且已预热 → `startBrowserPlayback` 赋给 `audio.src` 的是 `blob:` URL，且未调用 `api('/stream-ticket')`、URL 不含 `/api/stream`。
  - 有 DSP → `audio.src` 含 `/api/stream/`。
  - 已下载但未预热、无 DSP → 先 `await load(id)` 再赋值 blob URL（失败或 `NotAllowedError` 时走现有 `Tap play again` 路径）。
  - 有 DSP、`browserForceLocal=true` → blob URL，且 toast `Offline copy, EQ off` 只出现一次（同曲目不重复）。
  - `audio` error：当前非本地、有本地副本、探测 `/api/security/status` 失败 → `browserForceLocal===true` 并以当前位置重启播放；探测成功 → 沿用现有错误处理。
  - 本地播放时不调用 `scheduleDspStartupHealthCheck`，`browserStreamOffset===0`。
- [ ] **Step 2:** 运行新测试 → FAIL。
- [ ] **Step 3:** 实现。`playTrack()` 开始时调用 `VelvetOffline?.warm([当前, 队列下一首])`（不 await）。
  探测函数 `probeServerReachable(): Promise<boolean>` 用 `fetch('/api/security/status', {cache:'no-store'})`，3 秒超时，`navigator.onLine===false` 直接返回 false。
- [ ] **Step 4:** `npm run test:frontend` → 全部 PASS。
- [ ] **Step 5:** Commit `feat(offline): 已下载曲目优先播本地，断网自动回退`

### Task 3: 歌单页与歌单列表页 UI

**Files:**
- Modify: `static/js/library-views.js` — `renderPlaylist()`、歌单列表 `renderPlaylists`（2327 行附近）
- Modify: `static/index.html` — CSS：`.track-item.is-offline` 标记
- Create: `tests/frontend/offline-playlist-ui.test.mjs`（仿 `playlist-swipe.test.mjs`，注入假的 `window.VelvetOffline`）
- Modify: `package.json`

**Interfaces:**
- Consumes: Task 1 的 `status / download / pause / job / remove / getPlaylist / listPlaylists / usage / onChange / has`。
- Produces: 按钮 `#offline-playlist-btn`（`btn btn-ghost`，位于 `.playlist-toolbar-actions` 中 Rename 之前）。

按钮文案（规格确定）：未下载任何曲目 `Download`；下载中 `{done}/{total}`（点击 = `pause()`）；部分缺失 `+{missing}`；全部完成 `Downloaded`（点击 → `showConfirm({title:'Remove offline copy?', confirmText:'Remove'})` → `remove(id)`）。

- [ ] **Step 1: 写失败测试**：
  - 无 `VelvetOffline` 时 `renderPlaylist` 输出与现在一致（无 `#offline-playlist-btn`），现有 `playlist-swipe` 断言不变。
  - 有 `VelvetOffline`，`status` 为 0/2 → 按钮文本 `Download`，在 `.playlist-toolbar-actions` 内；点击调用 `download` 且参数含 `id、name、tracks`。
  - `onChange` 推送 `{playlistId:1, done:1, total:2}` → 文本 `1/2`；离开页面后（容器替换）回调不抛错并已退订。
  - `has(11)===true` → 对应 `.track-item` 有 `is-offline` 类，另一行没有。
  - 全部下载 → `Downloaded`；点击 → confirm → `remove(1)` 被调用。
  - `api('/playlists/1')` 返回 `null` 且 `getPlaylist(1)` 有清单 → 用清单渲染曲目。
  - 歌单列表：`api('/playlists')` 返回 `null` → 用 `listPlaylists()` 渲染卡片；`usage()` 为 1.2e9 → `.section-sub` 文本含 `1.2 GB offline`。
- [ ] **Step 2:** 运行 → FAIL。
- [ ] **Step 3:** 实现。CSS：`.track-item.is-offline .track-num{position:relative}` 加绝对定位的 4px 金色圆点（`var(--gold)`），不占布局。
- [ ] **Step 4:** `npm run test:frontend` → 全部 PASS。
- [ ] **Step 5:** Commit `feat(offline): 歌单页下载按钮、离线标记与断网渲染`

### Task 4: 浏览器实测与 PR

- [ ] 启动预览服务器，创建 2 首歌的测试歌单；下载 → 网络面板确认播放时无 `/api/stream` 请求；拖动进度、自动下一首正常。
- [ ] 在 DevTools 中让服务器离线（停服务或 SW 503）→ 刷新 → 歌单列表与歌单页可打开、可播放。
- [ ] 430px 宽截图：工具栏不换行，曲目行高与 main 一致。
- [ ] `npm run test:frontend` 与 `python -m pytest -q` 全部通过。
- [ ] 推送分支并开 PR（base `main`），描述中列出 iPhone 实机自测清单。

## 与规格的偏差（实施前已定）

- 规格写的是"设置页新增 Offline 小节"。设置页依赖 `/api/config`（需要管理员、需要在线），离线时无法打开，
  所以改为：占用空间显示在歌单列表页副标题（`… · 1.2 GB offline`），删除入口在歌单页 `Downloaded` 按钮。设置页不改。
