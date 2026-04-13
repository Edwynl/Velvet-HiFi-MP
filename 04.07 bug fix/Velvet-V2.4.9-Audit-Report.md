# Velvet V2.4.9 — 完整审查报告
> 审查日期：2026-04-08  
> 审查范围：`static/index.html`（11,807 行）  
> 对比基线：V2.4.6 Bug Report（2026-04-06）

---

## 目录
1. [上版已修复问题核查](#上版已修复问题核查)
2. [新增功能审查](#新增功能审查)
3. [新发现 Bug](#新发现-bug)
4. [仍未修复的问题](#仍未修复的问题)
5. [修复代码片段](#修复代码片段)
6. [综合优先级总表](#综合优先级总表)

---

## 上版已修复问题核查

### ✅ BUG-01 · Shuffle Fisher-Yates — 已修复

**Commit：** `18364aa`

```js
// ✅ 新版正确实现
function shuffleArray(arr) {
  const a = [...arr];
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}
```
两处调用（`btn-shuffle` 和 `shuffle-all-btn`）均已替换。✅

---

### ✅ BUG-02 · `state.repeat` 废弃字段 — 已清理

**Commit：** `18364aa`  
`repeat: false` 行已从 `state` 对象中删除。✅

---

### ✅ BUG-03 · `isSeekingProgress` 全局共享 — 已修复

**Commit：** `18364aa`  
`isSeekingProgress` 变量已移入 `bindProgressScrub` 闭包，重命名为 `isSeeking`，桌面和移动端互相隔离。✅

---

### ✅ BUG-04 · resize 缺少防抖 — 已修复

**Commit：** `18364aa`  
`debounce(fn, 150)` 已内联实现并应用于 resize 监听器。✅

---

### ✅ BUG-05 · visibilitychange 触发 iOS 重播 — 深度修复（超越原建议）

**Commits：** `a1cecdb` → `5b3081c` → `d74ff17` → `3773ca6`（共 4 次迭代）

原建议是在 `visibilitychange` 回调中增加 300ms 延迟并检查 `audio.paused`。实际修复更为彻底，解决了根本原因：

| Commit | 修复内容 |
|--------|----------|
| `a1cecdb` | 用 `readyState < 2` 替代 `audio.paused`，区分"真正断流"和"iOS 锁屏正常暂停" |
| `5b3081c` | 在 `visibilitychange` hidden 时**同步**关闭 AudioContext，在 Safari suspend 它之前主动断开图谱 |
| `d74ff17` | 将 cleanup 改为无条件关闭（不只在 `suspended` 状态） |
| `3773ca6` | 关闭前先调用 `audioSource.disconnect()` + `audioAnalyser.disconnect()`，防止 Safari 媒体管线仍认为图谱活跃 |

```js
// ✅ 最终实现
async function cleanupSuspendedAudioContextIfNeeded() {
  if (audioContext) {
    if (audioSource)  { try { audioSource.disconnect(); }  catch {} }
    if (audioAnalyser){ try { audioAnalyser.disconnect(); } catch {} }
    try { await audioContext.close(); } catch {}
    audioContext = null;
    audioAnalyser = null;
    audioSource = null;
    audioDataArray = null;
  }
}

document.addEventListener('visibilitychange', () => {
  if (document.hidden) {
    cleanupSuspendedAudioContextIfNeeded(); // 同步调用，Safari suspend 之前先断开
    if (!canRecoverBrowserPlayback()) return;
    if (audio.readyState < 2) {
      scheduleBrowserPlaybackRecovery('visibilitychange', { ... });
    } else if (!audio.paused) {
      noteBrowserPlaybackProgress();
    }
  }
});
```
✅ 这是正确且完整的做法。

---

### ✅ OPT-M04 · VU Meter 平板误判 — 已修复

**Commit：** `18364aa`

```js
// ✅ 新版：屏幕宽度 >= 768px 的设备（平板）均启用 VU Meter
function shouldUseWebAudioAnalyser() {
  return window.innerWidth >= 768 || !window.matchMedia('(pointer: coarse)').matches;
}
```
✅

---

## 新增功能审查

### 🆕 UI 元素 Proxy 缓存（`const UI`）

**引入版本：** V2.4.8（commit `24d03cb`）

```js
const UI = new Proxy({}, {
  get(target, prop) {
    const cached = target[prop];
    if (cached && cached.isConnected) return cached;
    const el = document.getElementById(prop);
    if (el) target[prop] = el;
    return el;
  }
});
```

**评价：** 设计合理。用 `isConnected` 防止 SPA 中因 `innerHTML` 替换产生的陈旧 DOM 引用，避免了大量重复的 `document.getElementById` 调用。✅

---

### 🆕 页面 HTML 缓存（`_pageCache`）+ API 数据缓存（`_dataCache`）

**引入版本：** V2.4.8 / V2.4.9（commits `24d03cb`、`204903b`）

`_pageCache`：缓存已渲染的视图 HTML，上限 5 页，LRU 淘汰。  
`_dataCache`：缓存 album/artist API 响应，TTL 5 分钟，back 导航时跳过 fetch。

**⚠️ 发现新 Bug，见 NEW-BUG-01。**

---

### 🆕 移动端播放器"不再自动弹出"逻辑（`mobilePlayerDismissed`）

**引入版本：** V2.4.9（commit `9d690fa`）

用户点击返回按钮关闭全屏播放器后，设置 `state.mobilePlayerDismissed = true`，后续切歌不再自动弹出。

**⚠️ 发现新 Bug，见 NEW-BUG-02。**

---

## 新发现 Bug

---

### NEW-BUG-01 · `_pageCache` 对含事件绑定的视图缓存，恢复后卡片点击无效 🔴 高优先级

**位置：** `index.html` 第 7317–7318 行

**问题描述：**  
`render()` 中通过白名单 `detailViews` 排除了不能缓存的视图，但白名单不完整。以下视图**全部含有 `addEventListener` 绑定**，却未被排除在缓存之外：

| 视图 | addEventListener 调用数 | 问题 |
|------|-------------------------|------|
| `artists` | 9 | 滚动、过滤、卡片点击全部失效 |
| `tracks` | 5 | 歌曲行点击无效，无法播放 |
| `genres` | 2 | 类别卡点击失效 |
| `playlists` | 2 | 播放列表行点击失效 |
| `composers` | 1 | 过滤输入失效 |
| `works` | 1 | 失效 |
| `credits` | 1 | 失效 |
| `history` | 1 | 失效 |
| `most-played` | 1 | 失效 |

当用户从 `artists` → `albums` → 返回 `artists`，恢复的是 `innerHTML` 字符串，所有 `addEventListener` 绑定**全部丢失**，页面卡片点击完全没有响应。

**现有代码（第 7317–7318 行）：**
```js
// 白名单不完整，以下视图仍会被缓存 ❌
const detailViews = ['album', 'artist', 'playlist', 'work', 'credit', 'home', 'albums', 'discover'];
const cached = detailViews.includes(state.view) ? null : _getCachedPage(state.view);
```

**注意：** 代码注释写道 *"interactive cards re-bind via render()"*，但从缓存恢复时第 7323 行直接 `return`，根本不会执行 render 函数体，更不会重新绑定。

**修复方案：** 将所有含 `addEventListener` 的视图加入白名单（即禁止缓存）：

```js
// ✅ 扩展白名单，排除所有依赖事件绑定的视图
const NO_CACHE_VIEWS = new Set([
  'album', 'artist', 'playlist', 'work', 'credit',
  'home', 'albums', 'discover',
  // 以下是新增的，均含 addEventListener
  'artists', 'tracks', 'genres', 'playlists',
  'composers', 'works', 'credits', 'history', 'most-played'
]);
const cached = NO_CACHE_VIEWS.has(state.view) ? null : _getCachedPage(state.view);
```

**或者**（更好的长远方案）：改用虚拟 DOM diff 或将卡片点击改为**事件委托（event delegation）**，这样从缓存恢复后事件仍然有效：

```js
// 事件委托示例：绑定在 vc 上，不依赖动态元素
vc.addEventListener('click', e => {
  const card = e.target.closest('[data-artist-id]');
  if (card) navigate('artist', { id: card.dataset.artistId });
});
```

---

### NEW-BUG-02 · `mobilePlayerDismissed` 永远不重置，换专辑后播放器永久不弹出 🟡 中优先级

**位置：** `index.html` 第 5619、5982、7037–7038 行

**问题描述：**  
用户关闭全屏播放器后 `state.mobilePlayerDismissed = true`，此后**整个会话期间**，任何新曲目都不会自动弹出全屏播放器——包括用户主动点击另一张专辑播放的情况，体验非常反直觉。

正确行为应该是：用户**在当前播放上下文中**关闭了播放器 → 不要再弹。但用户**主动选择新曲目**时，这是明确的"我要播放这首歌"的意图，应该再次弹出。

**现有代码：**
```js
// state 初始化
mobilePlayerDismissed: false,  // 只在初始化时为 false，之后永不重置

// playTrack 中（第 5982 行）
if (isNewTrack && window.innerWidth <= 768 && !state.mobilePlayerDismissed) {
  toggleMobilePlayer(true, { source: 'auto' });
  // ❌ 如果 dismissed=true，这里直接跳过，永不再弹
}

// 关闭时（第 7037 行）
if (source === 'dismiss') {
  state.mobilePlayerDismissed = true;
  // ❌ 没有配套的重置逻辑
}
```

**修复方案：在用户主动选择新曲目时重置 dismissed 状态**

```js
async function playTrack(track, queue, idx) {
  const playbackToken = ++browserPlaybackToken;
  state.queue    = queue || state.queue;
  state.queueIdx = idx !== undefined ? idx : state.queueIdx;

  const isNewTrack = !state.currentTrack || state.currentTrack.id !== track.id;

  // ✅ 用户主动选新曲 = 明确意图，重置 dismissed 状态
  if (isNewTrack) {
    state.mobilePlayerDismissed = false;
  }

  state.currentTrack = track;

  if (isNewTrack && window.innerWidth <= 768 && !state.mobilePlayerDismissed) {
    toggleMobilePlayer(true, { source: 'auto' });
  }
  // ... 其余逻辑不变
}
```

---

### NEW-BUG-03 · VU Meter rAF 提前退出时未置 null — 仍未完全修复 🟠 低优先级

**位置：** `index.html` 第 6916 行

**问题描述：** V2.4.6 Bug Report 中的 OPT-A05 **未在此版本修复**。`animate()` 函数中，当 `vuMeterOpen = false` 时直接 `return`，但 `vuAnimationFrame` 变量未被置为 `null`，导致 `stopVUAnimation()` 偶尔会对一个已完成的帧 ID 调用 `cancelAnimationFrame`，存在轻微状态泄漏。

```js
function animate(time) {
  if (!vuMeterOpen) return; // ❌ 未置 vuAnimationFrame = null
  // ...
  vuAnimationFrame = requestAnimationFrame(animate);
}
```

**修复（一行）：**
```js
function animate(time) {
  if (!vuMeterOpen) {
    vuAnimationFrame = null; // ✅
    return;
  }
  // ...
  vuAnimationFrame = requestAnimationFrame(animate);
}
```

---

### NEW-BUG-04 · 生产代码包含大量 `console.log`，存在性能与信息泄露风险 🟡 中优先级

**位置：** 全文共 **40 处** `console.log/warn/error`，其中 `console.log` 约 25 处

**问题：**
- 每次 API 调用、每次导航、每次渲染开始/结束均有日志输出，在用户设备上持续执行字符串拼接与 console I/O
- 日志中包含 API 路径、view 名称、track 数量等内部信息，在开发者工具或截图中可见
- 典型高频日志：

```js
console.log(`[VELVET] API Call: ${path}`);         // 每次请求都触发
console.log('[VELVET] render() started');           // 每次页面切换
console.log('[VELVET] Navigating to: ${view}', params); // 每次导航
console.log('[VELVET] renderHome started');
console.log('[VELVET] Stats loaded:', stats);        // 含数据量信息
```

**修复方案：使用 DEBUG flag 控制**

```js
// ✅ 在文件顶部定义调试开关
const DEBUG = false; // 发布时设为 false

// 封装
const log = DEBUG ? console.log.bind(console) : () => {};

// 替换所有 console.log（保留 console.warn 和 console.error）
log('[VELVET] Navigating to:', view, params);
log('[VELVET] render() started');
// ...
```

---

## 仍未修复的问题

以下问题来自上版 Bug Report，v2.4.9 中**仍未处理**：

### OPT-M01 · 全屏播放器缺少 swipe-down 手势 🟡

用户只能通过返回按钮关闭，不符合 iOS/Android 原生交互规范。

**建议实现：**
```js
(function bindMobilePlayerSwipe() {
  const overlay = UI['mobile-fullscreen-player'];
  let startY = 0;

  overlay.addEventListener('touchstart', e => {
    startY = e.touches[0].clientY;
  }, { passive: true });

  overlay.addEventListener('touchmove', e => {
    const dy = e.touches[0].clientY - startY;
    if (dy > 0) {
      overlay.style.transform = `translateY(${Math.min(dy, 200)}px)`;
      overlay.style.transition = 'none';
    }
  }, { passive: true });

  overlay.addEventListener('touchend', e => {
    const dy = e.changedTouches[0].clientY - startY;
    overlay.style.transition = '';
    overlay.style.transform = '';
    if (dy > 80) {
      // ✅ 下滑超过 80px 关闭，且标记 dismissed
      toggleMobilePlayer(false, { source: 'dismiss' });
    }
  });
})();
```

---

### OPT-M02 · 全屏播放器缺少左右 swipe 切歌 🟡

```js
(function bindMobilePlayerTrackSwipe() {
  const artSection = document.querySelector('.mobile-player-art-section');
  if (!artSection) return;
  let startX = 0;

  artSection.addEventListener('touchstart', e => {
    startX = e.touches[0].clientX;
  }, { passive: true });

  artSection.addEventListener('touchend', e => {
    const dx = e.changedTouches[0].clientX - startX;
    if (Math.abs(dx) < 60) return;
    dx < 0 ? playNext() : playPrev();
  });
})();
```

---

### OPT-M03 · `window.innerWidth <= 768` 分散在 8+ 处，缺少统一封装 🟠

```js
// ✅ 统一封装，改动断点只需修改一处
function isMobileViewport() {
  return window.innerWidth <= 768;
}
```

---

### OPT-M05 · 音量缺少统一 setter 🟠

```js
function setVolume(value) {
  const clamped = Math.min(1, Math.max(0, value));
  audio.volume = clamped;
  const pct = Math.round(clamped * 100);
  UI['volume-slider'].value = pct;
  UI['mobile-volume-slider'].value = pct;
  localStorage.setItem('velvet_volume', pct);
}

UI['volume-slider'].addEventListener('input', e => setVolume(e.target.value / 100));
UI['mobile-volume-slider'].addEventListener('input', e => setVolume(e.target.value / 100));

// 初始化时读取保存音量
const savedVol = parseInt(localStorage.getItem('velvet_volume') ?? '85', 10);
setVolume(savedVol / 100);
```

---

### OPT-A02 · 用户偏好不持久化（刷新后 shuffle/repeat/upsample 重置） 🟠

```js
const PERSIST_KEYS = ['shuffle', 'repeatMode', 'upsample', 'dspProfile'];

function savePrefs() {
  const prefs = {};
  PERSIST_KEYS.forEach(k => { prefs[k] = state[k]; });
  localStorage.setItem('velvet_prefs', JSON.stringify(prefs));
}

function loadPrefs() {
  try {
    const prefs = JSON.parse(localStorage.getItem('velvet_prefs') || '{}');
    PERSIST_KEYS.forEach(k => { if (prefs[k] !== undefined) state[k] = prefs[k]; });
  } catch {}
}
```

---

### OPT-A03 · API Key 明文存储于 localStorage 🔴

**位置：** 第 5420 行、第 5553 行

`localStorage` 可被同域 JavaScript 读取，存在 XSS 风险。  
**建议：** 后端改用 `httpOnly; Secure; SameSite=Strict` Session Cookie，前端无需存储 key。

---

### OPT-A04 · Cast polling 固定 1500ms，不随播放状态降频 🟡

```js
// 当前：固定 1500ms，暂停时也不降频
state.castPollId = setInterval(async () => { ... }, 1500);

// 建议：根据播放状态动态调整
function startCastPolling() {
  stopCastPolling();
  const schedule = () => {
    state.castPollId = setTimeout(async () => {
      await doCastPoll();
      if (state.castPollId !== null) {
        state.castPollId = null;
        schedule(); // 重新调度
      }
    }, state.playing ? 1500 : 5000); // 播放时 1.5s，暂停时 5s
  };
  schedule();
}
```

---

### OPT-A01 · 单文件 11,807 行（未拆分） 🟠

CSS、HTML、JS 仍全部内联，每次改动都导致浏览器无法复用缓存。建议拆分为独立文件（详见上版 Bug Report OPT-A01）。

---

## 综合优先级总表

| ID | 描述 | 状态 | 优先级 | 修复难度 |
|----|------|------|--------|----------|
| **NEW-BUG-01** | `_pageCache` 恢复后事件绑定丢失，卡片点击无效 | 🔴 新发现 | 高 | 低 |
| **OPT-A03** | API Key 明文 localStorage | 未修复 | 🔴 高 | 中 |
| **NEW-BUG-02** | `mobilePlayerDismissed` 永不重置 | 🟡 新发现 | 中 | 低 |
| **NEW-BUG-04** | 生产代码残留 40 处 console.log | 🟡 新发现 | 中 | 低 |
| **OPT-M01** | 全屏播放器缺 swipe-down 关闭手势 | 未修复 | 🟡 中 | 中 |
| **OPT-M02** | 缺左右 swipe 切歌手势 | 未修复 | 🟡 中 | 中 |
| **OPT-A04** | Cast polling 固定频率不降频 | 未修复 | 🟡 中 | 中 |
| **NEW-BUG-03** | VU rAF 提前退出未置 null | 🟠 新发现 | 低 | 极低 |
| **OPT-M03** | `isMobileViewport` 断点硬编码 8+ 处 | 未修复 | 🟠 低 | 低 |
| **OPT-M05** | 音量缺统一 setter | 未修复 | 🟠 低 | 低 |
| **OPT-A02** | 用户偏好不持久化 | 未修复 | 🟠 低 | 低 |
| **OPT-A01** | 单文件 11,807 行未拆分 | 未修复 | 🟠 低 | 高 |

---

## 总结

V2.4.9 是质量提升明显的版本：

- 上版报告的 **6 个 Bug 全部修复**，其中 BUG-05 iOS 音频图谱问题的修复深度和严谨性远超原建议，经历了 4 次迭代，从根本上解决了 Safari AudioContext 生命周期管理问题。
- 新引入的 UI Proxy 缓存和 API 数据缓存整体设计合理。

**当前最需要修复的问题是 NEW-BUG-01**：`_pageCache` 白名单不完整，`artists`/`tracks`/`genres` 等视图从缓存恢复后事件全部失效，用户将看到能点但没反应的死页面。修复只需在 `NO_CACHE_VIEWS` Set 中加 8 行视图名，成本极低。

---

*生成工具：Claude Sonnet 4.6 · 代码审查基于 git checkout v2.4.9 (commit 3773ca6)*
