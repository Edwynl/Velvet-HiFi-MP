# Velvet V2.4.6 — Bug Report & 优化建议
> 审查日期：2026-04-06  
> 审查范围：`static/index.html`（11,652 行）— 前端播放器 + 移动端代码

---

## 目录
1. [Bug 清单](#bug-清单)
2. [移动端优化建议](#移动端优化建议)
3. [架构与性能优化](#架构与性能优化)
4. [修复代码片段](#修复代码片段)

---

## Bug 清单

---

### BUG-01 · Shuffle 算法分布不均匀 🔴 高优先级

**位置：** `index.html` 第 6701 行、第 9439 行

**问题描述：**  
使用 `Array.sort(() => Math.random() - 0.5)` 进行随机排序。由于 `sort` 的比较函数被要求具有传递性和一致性，而随机函数不满足这一条件，V8 引擎（及其他 JS 引擎）在不同轮次对同一对元素的比较结果会不同，导致最终排列**分布严重不均**，某些歌曲会更频繁地出现在队列前端。

**现有代码：**
```js
// 第 6701 行
const shuffled = [...state.queue].sort(() => Math.random() - 0.5);

// 第 9439 行
const shuffled = [...tracks].sort(() => Math.random() - 0.5);
```

**修复方案：Fisher-Yates（Knuth）洗牌算法**
```js
/**
 * 真正均匀的随机洗牌 — Fisher-Yates 算法
 * 时间复杂度 O(n)，每种排列出现概率相等
 */
function shuffleArray(arr) {
  const a = [...arr];
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

// 替换第 6701 行
const shuffled = shuffleArray(state.queue);

// 替换第 9439 行
const shuffled = shuffleArray(tracks);
```

---

### BUG-02 · `state.repeat` 废弃字段残留（状态不一致）🟡 中优先级

**位置：** `index.html` 第 5649–5651 行

**问题描述：**  
`state` 对象中同时存在 `repeat: false` 和 `repeatMode: 'none'` 两个字段。实际业务逻辑全部使用 `repeatMode`，但 `repeat` 字段从未被清理。在某些未来扩展或调试时，容易误读状态，也会让序列化/恢复 state 产生混淆。

**现有代码：**
```js
const state = {
  // ...
  repeat: false,      // ← 废弃，注释写着 'none' | 'one' | 'all' 但实际从不使用
  repeatMode: 'none', // ← 实际使用的字段
  // ...
};
```

**修复方案：**
```js
const state = {
  // ...
  // repeat: false,   ← 删除此行
  repeatMode: 'none', // 'none' | 'one' | 'all'
  // ...
};
```

---

### BUG-03 · `isSeekingProgress` 全局共享导致桌面/移动端 seek 互相干扰 🔴 高优先级

**位置：** `index.html` 第 6894 行、6902–6934 行

**问题描述：**  
`isSeekingProgress` 是一个模块级全局变量，而 `bindProgressScrub()` 被调用了两次（分别绑定桌面端 `#progress-row` 和移动端 `#mobile-progress-row`）。两个绑定共享同一标志位，导致在一个进度条上触发 `pointerdown` 后，另一个进度条的 `pointermove` 也会触发 seek，可能引起：
- 移动端拖动时桌面端也意外跳转
- `pointercancel` 未能正确清理共享状态时，后续点击被忽略

**现有代码：**
```js
let isSeekingProgress = false; // 全局共享 ❌

function bindProgressScrub(containerEl) {
  const stopSeek = (e) => {
    isSeekingProgress = false; // 两个实例都写同一个变量
    // ...
  };
  containerEl.addEventListener('pointerdown', e => {
    isSeekingProgress = true;  // 任意一个触发都会影响另一个
    // ...
  });
  containerEl.addEventListener('pointermove', e => {
    if (!isSeekingProgress) return; // 无法区分是哪个实例在 seeking
    // ...
  });
}

bindProgressScrub($('progress-row'));       // 桌面
bindProgressScrub($('mobile-progress-row')); // 移动端
```

**修复方案：将标志位封装到闭包内**
```js
function bindProgressScrub(containerEl) {
  let isSeeking = false; // ✅ 每次调用独立的闭包变量

  const track = containerEl.querySelector('.progress-track, .mobile-progress-track');

  const stopSeek = (e) => {
    isSeeking = false;
    if (e && typeof e.pointerId === 'number' && containerEl.hasPointerCapture?.(e.pointerId)) {
      containerEl.releasePointerCapture(e.pointerId);
    }
  };

  containerEl.addEventListener('click', e => {
    if (!track) return;
    seekFromClientX(e.clientX, track);
  });
  containerEl.addEventListener('pointerdown', e => {
    if (e.pointerType === 'mouse' && e.button !== 0) return;
    if (!track) return;
    isSeeking = true;
    containerEl.setPointerCapture?.(e.pointerId);
    seekFromClientX(e.clientX, track);
    e.preventDefault();
  });
  containerEl.addEventListener('pointermove', e => {
    if (!isSeeking) return;
    if (!track) return;
    seekFromClientX(e.clientX, track);
    e.preventDefault();
  });
  containerEl.addEventListener('pointerup', stopSeek);
  containerEl.addEventListener('pointercancel', stopSeek);
  containerEl.addEventListener('lostpointercapture', () => { isSeeking = false; });
}

bindProgressScrub($('progress-row'));
bindProgressScrub($('mobile-progress-row'));
```

---

### BUG-04 · resize 事件监听器缺少防抖 🟡 中优先级

**位置：** `index.html` 第 5322–5326 行

**问题描述：**  
`window.addEventListener('resize', ...)` 没有防抖，resize 事件在拖动窗口或旋转屏幕时每秒触发几十次，每次都会执行 DOM 查询和 `closeMenu()` 操作，造成不必要的性能消耗。在低端 Android 设备旋转屏幕时尤为明显。

**现有代码：**
```js
window.addEventListener('resize', () => {
  if (window.innerWidth > 768) {
    closeMenu();
  }
});
```

**修复方案：加入 debounce**
```js
function debounce(fn, delay) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), delay);
  };
}

window.addEventListener('resize', debounce(() => {
  if (window.innerWidth > 768) {
    closeMenu();
  }
}, 150));
```

---

### BUG-05 · `visibilitychange` 恢复可能导致 iOS Safari 歌曲从头播放 🔴 高优先级

**位置：** `index.html` 第 6584–6589 行

**问题描述：**  
每次从后台切回（`visibilitychange` 触发）都会无条件调用 `scheduleBrowserPlaybackRecovery()`，没有先检查音频是否**实际已中断**。在 iOS Safari 上，从后台切回时音频往往只是短暂暂停，会自动恢复；此时 recovery 机制介入，会重新设置 `audio.src` 并 `audio.load()`，导致歌曲**从当前记录的 `resumeTime` 处重新加载**，体验上表现为歌曲"卡一下从头跳"。

**现有代码：**
```js
document.addEventListener('visibilitychange', () => {
  if (document.hidden || !canRecoverBrowserPlayback()) return;
  // ❌ 没有检查 audio 是否真的停止了
  scheduleBrowserPlaybackRecovery('visibilitychange', {
    delayMs: browserPlaybackRecoveryConfig.visibilityDelayMs,
  });
});
```

**修复方案：先等待一小段时间，确认音频未自行恢复再触发 recovery**
```js
document.addEventListener('visibilitychange', () => {
  if (document.hidden || !canRecoverBrowserPlayback()) return;

  // 给浏览器 300ms 时间自行恢复，再决定是否需要 recovery
  setTimeout(() => {
    if (!canRecoverBrowserPlayback()) return;
    // 如果音频已在播放，无需 recovery
    if (!audio.paused) return;
    scheduleBrowserPlaybackRecovery('visibilitychange', {
      delayMs: browserPlaybackRecoveryConfig.visibilityDelayMs,
    });
  }, 300);
});
```

---

## 移动端优化建议

---

### OPT-M01 · 缺少 swipe-down 手势关闭全屏播放器 🟡

**问题：** 全屏播放器只有返回按钮可以关闭，不符合 iOS/Android 原生音乐 App 的交互规范。

**建议实现：**
```js
(function bindMobilePlayerSwipe() {
  const overlay = $('mobile-fullscreen-player');
  let startY = 0;
  let isDragging = false;

  overlay.addEventListener('touchstart', e => {
    startY = e.touches[0].clientY;
    isDragging = true;
  }, { passive: true });

  overlay.addEventListener('touchmove', e => {
    if (!isDragging) return;
    const dy = e.touches[0].clientY - startY;
    if (dy > 0) {
      // 跟随手指拖动，给予视觉反馈
      overlay.style.transform = `translateY(${Math.min(dy, 200)}px)`;
      overlay.style.transition = 'none';
    }
  }, { passive: true });

  overlay.addEventListener('touchend', e => {
    isDragging = false;
    const dy = e.changedTouches[0].clientY - startY;
    overlay.style.transition = '';
    overlay.style.transform = '';
    if (dy > 80) {
      // 下滑超过 80px 则关闭
      toggleMobilePlayer(false);
    }
  });
})();
```

---

### OPT-M02 · 缺少左右 swipe 切歌手势 🟡

**问题：** 全屏播放器没有左右滑动切换曲目的手势，是移动端音乐播放器的标准功能。

**建议实现：**
```js
(function bindMobilePlayerTrackSwipe() {
  const artSection = $('mobile-player-art-section'); // 专辑封面区域
  if (!artSection) return;

  let startX = 0;

  artSection.addEventListener('touchstart', e => {
    startX = e.touches[0].clientX;
  }, { passive: true });

  artSection.addEventListener('touchend', e => {
    const dx = e.changedTouches[0].clientX - startX;
    if (Math.abs(dx) < 60) return; // 忽略小幅滑动
    if (dx < 0) {
      playNext();   // 向左滑：下一曲
    } else {
      playPrev();   // 向右滑：上一曲
    }
  });
})();
```

---

### OPT-M03 · `window.innerWidth <= 768` 硬编码到处散落 🟠

**问题：** `window.innerWidth <= 768` 这个判断出现了 8+ 次，分散在不同函数中。一旦需要调整断点（如适配平板 1024px），需要全局搜索替换，容易遗漏。

**建议：** 统一封装为一个函数：
```js
/**
 * 统一的移动端判断，修改断点只需改这一处
 */
function isMobileViewport() {
  return window.innerWidth <= 768;
}

// 用法（替换所有 window.innerWidth <= 768）
if (isMobileViewport()) {
  toggleMobilePlayer(true);
}
```

---

### OPT-M04 · VU Meter 在触控平板上被错误禁用 🟡

**问题：** `shouldUseWebAudioAnalyser()` 通过检测 `pointer: coarse` 来判断是否为移动端，从而禁用 WebAudio Analyser。但 iPad 也是 `pointer: coarse`，导致在平板上 VU 表无法正常工作。

**现有代码：**
```js
function shouldUseWebAudioAnalyser() {
  return !window.matchMedia('(pointer: coarse)').matches; // 平板也被误杀
}
```

**修复方案：改为结合屏幕尺寸判断**
```js
function shouldUseWebAudioAnalyser() {
  // 仅在小屏手机上禁用（< 768px），平板和桌面均启用
  return window.innerWidth >= 768 || !window.matchMedia('(pointer: coarse)').matches;
}
```

---

### OPT-M05 · 音量同步逻辑分散，缺少统一 setter 🟠

**问题：** 桌面和移动端音量滑块各自监听、各自写 `audio.volume`，没有统一的 setter 函数，代码重复，且容易在未来引入新的音量控件时出现不同步。

**现有代码：**
```js
$('volume-slider').addEventListener('input', e => {
  audio.volume = e.target.value / 100;
  $('mobile-volume-slider').value = e.target.value;
});
$('mobile-volume-slider').addEventListener('input', e => {
  audio.volume = e.target.value / 100;
  $('volume-slider').value = e.target.value;
});
audio.volume = 0.85;
```

**建议重构：**
```js
function setVolume(value) {
  const clamped = Math.min(1, Math.max(0, value));
  audio.volume = clamped;
  const pct = Math.round(clamped * 100);
  $('volume-slider').value = pct;
  $('mobile-volume-slider').value = pct;
  // 持久化
  localStorage.setItem('velvet_volume', pct);
}

$('volume-slider').addEventListener('input', e => setVolume(e.target.value / 100));
$('mobile-volume-slider').addEventListener('input', e => setVolume(e.target.value / 100));

// 初始化时读取保存的音量
const savedVolume = parseInt(localStorage.getItem('velvet_volume') ?? '85', 10);
setVolume(savedVolume / 100);
```

---

## 架构与性能优化

---

### OPT-A01 · 11,652 行单文件，无法利用浏览器缓存 🟠

**问题：** 所有 CSS、HTML 模板、JavaScript 全部内联在 `index.html`，每次访问都需要完整下载和解析整个文件（约 392KB）。即使只改了一行 JS，浏览器也无法复用任何缓存。

**建议：** 拆分文件结构：
```
static/
├── index.html          ← 保留骨架 HTML，约 5KB
├── css/
│   ├── base.css        ← Reset + Variables
│   ├── layout.css      ← Sidebar, Main, Player
│   └── mobile.css      ← 所有 @media 移动端样式
└── js/
    ├── state.js        ← state 对象 + 工具函数
    ├── audio.js        ← 播放逻辑、watchdog、recovery
    ├── ui.js           ← DOM 更新、页面渲染
    └── mobile.js       ← 移动端手势、mobile player
```

---

### OPT-A02 · 用户偏好设置不持久化，每次刷新丢失 🟠

**问题：** `shuffle`、`repeatMode`、`upsample`、`dspProfile`、volume 等偏好在刷新后全部重置。

**建议：** 用 `localStorage` 持久化关键偏好：
```js
const PERSIST_KEYS = ['shuffle', 'repeatMode', 'upsample', 'dspProfile'];

function savePrefs() {
  const prefs = {};
  PERSIST_KEYS.forEach(k => { prefs[k] = state[k]; });
  localStorage.setItem('velvet_prefs', JSON.stringify(prefs));
}

function loadPrefs() {
  try {
    const raw = localStorage.getItem('velvet_prefs');
    if (!raw) return;
    const prefs = JSON.parse(raw);
    PERSIST_KEYS.forEach(k => {
      if (prefs[k] !== undefined) state[k] = prefs[k];
    });
  } catch {}
}

// 在 state 修改的关键位置调用 savePrefs()
// 在 DOMContentLoaded 或初始化时调用 loadPrefs()
```

---

### OPT-A03 · API Key 以明文存于 localStorage 🔴

**位置：** 第 5471 行

**问题：** 
```js
apiKey: localStorage.getItem('velvet_api_key') || ''
```
`localStorage` 可被同域内任何 JavaScript 读取，存在 XSS 风险（若任何第三方脚本或浏览器扩展注入 JS，即可读取 key）。

**建议：**
- 后端改用 `httpOnly; Secure; SameSite=Strict` 的 Session Cookie 鉴权，前端不需要存储 key
- 或至少加入 Content Security Policy（CSP）头限制脚本执行

---

### OPT-A04 · Cast Polling 固定 1500ms，建议改为自适应/WebSocket 🟡

**位置：** 第 6037 行

**问题：** `setInterval(..., 1500)` 在整个 Cast 过程中持续发请求，即使用户切换到其他 Tab、设备空闲或歌曲暂停时也不会降频。

**建议：** 短期用指数退避降频，长期改为服务端推送（SSE 或 WebSocket）：
```js
// 暂停时降低 polling 频率
const POLL_ACTIVE_MS = 1500;
const POLL_IDLE_MS   = 5000;

function getCastPollInterval() {
  return state.playing ? POLL_ACTIVE_MS : POLL_IDLE_MS;
}
```

---

### OPT-A05 · VU Meter rAF 在 overlay 关闭时仍运行 🟡

**位置：** 第 6810–6858 行

**问题：** `startVUAnimation()` 启动后，即使 `mobile-vu-meters` overlay 不可见（CSS `display:none`），`requestAnimationFrame` 仍在持续执行，空耗 CPU/GPU。

**现有代码：**
```js
function animate(time) {
  if (!vuMeterOpen) return; // ← 已有检查，但 rAF 仍被取消得不够彻底
  // ...
  vuAnimationFrame = requestAnimationFrame(animate);
}
```

**问题根源：** `if (!vuMeterOpen) return;` 会停止递归，但 `vuAnimationFrame` 变量未被置 null，`stopVUAnimation()` 有时候会在 frame 已经自然退出后再次调用 `cancelAnimationFrame(stale_id)`。

**修复：**
```js
function animate(time) {
  if (!vuMeterOpen) {
    vuAnimationFrame = null; // ← 确保清空
    return;
  }
  // ... 动画逻辑 ...
  vuAnimationFrame = requestAnimationFrame(animate);
}
```

---

## 优先级汇总

| ID | 描述 | 优先级 | 难度 |
|----|------|--------|------|
| BUG-01 | Shuffle 算法偏差（Fisher-Yates） | 🔴 高 | 低 |
| BUG-03 | isSeekingProgress 全局共享 | 🔴 高 | 低 |
| BUG-05 | visibilitychange 触发 iOS 重播 | 🔴 高 | 低 |
| OPT-A03 | API Key 明文 localStorage | 🔴 高 | 中 |
| BUG-02 | state.repeat 废弃字段 | 🟡 中 | 低 |
| BUG-04 | resize 缺少 debounce | 🟡 中 | 低 |
| OPT-M01 | 全屏播放器 swipe-down 手势 | 🟡 中 | 中 |
| OPT-M02 | 左右 swipe 切歌手势 | 🟡 中 | 中 |
| OPT-M04 | VU Meter 平板误判 | 🟡 中 | 低 |
| OPT-A04 | Cast polling 固定频率 | 🟡 中 | 中 |
| OPT-A05 | VU rAF 未彻底清理 | 🟡 中 | 低 |
| OPT-M03 | isMobile 硬编码断点 | 🟠 低 | 低 |
| OPT-M05 | 音量缺少统一 setter | 🟠 低 | 低 |
| OPT-A01 | 单文件拆分 | 🟠 低 | 高 |
| OPT-A02 | 用户偏好不持久化 | 🟠 低 | 低 |

---

*生成工具：Claude Sonnet 4.6 · 代码审查基于 git clone Edwynl/Velvet-V2.4.6*
