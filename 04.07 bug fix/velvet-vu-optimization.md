# Velvet V2.4.10 — VU Meter 音频中断 Bug 分析与优化建议

> 针对问题：打开 VU Meter 后，手机端锁屏/最小化无法播放，桌面端切歌不播放

---

## 根本原因分析

### 一句话总结

`createMediaElementSource(audio)` 被调用后，**主播放用的 `<audio>` 元素的输出路由被永久劫持进 Web Audio 图**。关闭 AudioContext 并 null 掉引用之后，该 audio 元素在部分浏览器（Safari/iOS WebKit、某些 Android Chrome）上**无法完全恢复正常输出**，导致后续 `audio.play()` 静音或失败。

---

## 详细 Bug 链条

```
1. 用户打开 VU Meter
   └─ startVUAnimation() → initAudioAnalyser()
      └─ audioContext.createMediaElementSource(audio)
         ✅ audio 元素输出路由进入 Web Audio 图
         ✅ 必须经过 audioSource → audioAnalyser → destination 才能出声

2. 用户锁屏 / 最小化 / 切 tab
   └─ visibilitychange → document.hidden = true
      └─ cleanupSuspendedAudioContextIfNeeded()
         ✅ audioSource.disconnect()
         ✅ audioAnalyser.disconnect()
         ✅ audioContext.close()
         ✅ 所有引用置 null
         ⚠️  问题：audio 元素曾被 MediaElementSource 捕获
             Web Audio 规范要求 close() 后应释放捕获，
             但 Safari/WebKit 并不可靠地执行这一步

3. 用户解锁 / 切歌
   └─ startBrowserPlayback()
      └─ cleanupSuspendedAudioContextIfNeeded()  ← audioContext 已是 null，no-op
         └─ audio.removeAttribute('src') → audio.load()
            └─ audio.src = newUrl → audio.play()
               ❌ 静音或播放失败
               ← audio 元素内部输出路由仍处于"被捕获"残留状态
```

---

## 具体 Bug 点（代码定位）

### Bug A — `createMediaElementSource` 单次捕获副作用（最核心）

**位置**：`index.html` ~L5853

```js
audioSource = audioContext.createMediaElementSource(audio);  // ← 问题根源
```

Web Audio 规范允许每个 HTMLMediaElement 只被一个 AudioContext 捕获一次。
在 Safari/iOS 上，即使 `audioContext.close()` 已被调用，
`audio` 元素的内部输出 routing 在部分情况下仍不恢复，
导致后续 `audio.play()` 输出路径断裂。

**此外**，当 `cleanupSuspendedAudioContextIfNeeded()` 运行后 `audioContext = null`，
下次用户再打开 VU Meter，`initAudioAnalyser()` 会再次在**同一个 audio 元素**上
调用 `createMediaElementSource(audio)`——这在不同 AudioContext 之间重复捕获
同一个 media element 在 Chrome 会报错（已被捕获的 InvalidStateError），
在 Safari 会静默失败。

---

### Bug B — `visibilitychange` 过于激进地销毁 AudioContext

**位置**：`index.html` ~L6655–6668

```js
document.addEventListener('visibilitychange', () => {
  if (document.hidden) {
    cleanupSuspendedAudioContextIfNeeded();   // ← 每次隐藏都销毁
    ...
  }
});
```

问题：即使 AudioContext 处于 `running` 状态（用户只是 Alt-Tab），
也会销毁 AudioContext 并让 audio 元素陷入"已被捕获但无图"状态。
这使得 `audio.play()` 在恢复可见后在 Safari 上静默失败。

---

### Bug C — `cleanupSuspendedAudioContextIfNeeded` 只处理 `audioContext != null` 的情况

**位置**：`index.html` ~L5681

```js
async function cleanupSuspendedAudioContextIfNeeded() {
  if (audioContext) {  // ← null 时整个函数是 no-op
    ...
    audioContext = null;
  }
}
```

在 Bug A 场景中：AudioContext 已被关闭且置 null，
但 `audio` 元素仍处于"残留捕获"状态。
此时 `cleanupSuspendedAudioContextIfNeeded()` 是 no-op，
完全感知不到 audio 元素的问题。

---

### Bug D — VU 关闭后 `vuAnimationFrame` 状态泄漏（v2.4.10 已部分修复）

v2.4.10 的 NEW-BUG-03 只处理了 `animate()` 内的早退 null 设置。
但 `stopVUAnimation()` 在 `toggleVUMeters(false)` 后被调用时，
不会调用 `cleanupSuspendedAudioContextIfNeeded()`——
即 VU 关闭后 AudioContext **仍然存活**，继续持有 audio 元素的路由捕获，
直到下一次 `startBrowserPlayback()` 才会清理。
这意味着关闭 VU 面板后锁屏，依然会触发 Bug A 链条。

---

## 优化方案

### 方案一（推荐）：使用 `captureStream()` 代替 `createMediaElementSource()`

**核心思路**：`captureStream()` / `mozCaptureStream()` 获取 MediaStream，
再用 `createMediaStreamSource()` 接入 Web Audio，
完全不影响 audio 元素的正常输出路由。

```js
function initAudioAnalyser() {
  if (audioContext) return;
  try {
    // 优先用 captureStream：不劫持 audio 元素的输出
    const stream = audio.captureStream?.() || audio.mozCaptureStream?.();
    
    audioContext = new (window.AudioContext || window.webkitAudioContext)();
    audioAnalyser = audioContext.createAnalyser();
    audioAnalyser.fftSize = 256;
    audioAnalyser.smoothingTimeConstant = 0.6;

    if (stream) {
      // MediaStreamSource 不影响 audio 元素的原始输出路由
      audioSource = audioContext.createMediaStreamSource(stream);
      audioSource.connect(audioAnalyser);
      // 不需要 connect(destination)，audio 元素正常出声
    } else {
      // Fallback：Safari 不支持 captureStream，回退旧方式
      audioSource = audioContext.createMediaElementSource(audio);
      audioSource.connect(audioAnalyser);
      audioAnalyser.connect(audioContext.destination); // 必须，否则静音
    }
    audioDataArray = new Uint8Array(audioAnalyser.frequencyBinCount);
  } catch (e) {
    console.warn('Audio analyser not available:', e);
    audioContext = null;
    audioAnalyser = null;
    audioSource = null;
    audioDataArray = null;
  }
}
```

**好处**：
- Chrome/Firefox/Android 完全修复
- audio 元素的输出路由不受 Web Audio 影响
- `cleanupSuspendedAudioContextIfNeeded()` 后无副作用

**局限**：Safari/iOS 不支持 `captureStream()`，需要保留 fallback。

---

### 方案二（Safari 专项修复）：关闭 AudioContext 后重建 audio 元素

当 `createMediaElementSource(audio)` fallback 路径触发后，
一旦 AudioContext 被关闭，需要用全新的 `<audio>` 元素替换旧元素，
以彻底消除"残留捕获"状态。

```js
let audioElementCaptured = false; // 标记 audio 元素是否被 MediaElementSource 捕获

async function cleanupSuspendedAudioContextIfNeeded() {
  if (!audioContext) return;

  try { audioSource?.disconnect(); } catch {}
  try { audioAnalyser?.disconnect(); } catch {}
  try { await audioContext.close(); } catch {}

  audioContext = null;
  audioAnalyser = null;
  audioSource = null;
  audioDataArray = null;

  // 如果使用了 createMediaElementSource，audio 元素可能已被污染
  // 在 Safari 上必须重建 audio 元素
  if (audioElementCaptured) {
    rebuildAudioElement();
    audioElementCaptured = false;
  }
}

function rebuildAudioElement() {
  const oldAudio = audio;
  const newAudio = new Audio();
  newAudio.preload = 'auto';
  newAudio.playsInline = true;
  newAudio.volume = oldAudio.volume;
  newAudio.playbackRate = oldAudio.playbackRate;

  // 迁移事件监听器（需要提取为具名函数，见"配套重构"）
  reattachAudioEventListeners(newAudio);

  audio = newAudio;
}
```

**配套重构**：将 audio 上的所有 `addEventListener` 改为具名函数，
以便 `rebuildAudioElement()` 可以重新绑定：

```js
// 改前（匿名函数，无法解绑/重绑）
audio.addEventListener('pause', () => { ... });

// 改后（具名函数）
const onAudioPause = () => { ... };
audio.addEventListener('pause', onAudioPause);
```

---

### 方案三（最小改动，快速修复）：VU 关闭时立即清理 AudioContext

在 `stopVUAnimation()` 中立即销毁 AudioContext，而不是留给下次 `startBrowserPlayback`。
同时，在 `visibilitychange` 中将清理推迟到 AudioContext **实际被挂起**后。

```js
// 修改 stopVUAnimation：VU 关闭时主动清理
function stopVUAnimation() {
  if (vuAnimationFrame) {
    cancelAnimationFrame(vuAnimationFrame);
    vuAnimationFrame = null;
  }
  // VU 关闭后立即解除 audio 元素的 Web Audio 捕获
  cleanupSuspendedAudioContextIfNeeded();
}

// 修改 visibilitychange：只在 AudioContext 实际被挂起时销毁
document.addEventListener('visibilitychange', () => {
  if (document.hidden) {
    if (audioContext && audioContext.state === 'suspended') {
      // 已被 OS 挂起，确认清理
      cleanupSuspendedAudioContextIfNeeded();
    } else if (audioContext) {
      // 主动挂起而非销毁，保持图完整
      audioContext.suspend().catch(() => {});
    }
    ...
  } else {
    // 页面恢复可见时，恢复 AudioContext
    if (audioContext && audioContext.state === 'suspended') {
      audioContext.resume().catch(() => {});
    }
  }
});
```

**注意**：此方案不解决根本的 `createMediaElementSource` 副作用，
只减少了销毁频率，降低触发概率，适合作为临时补丁。

---

### 方案四（最稳健，长期方案）：VU Meter 使用独立 audio 元素

使用一个独立的、**仅用于分析**的 `<audio>` 元素来接入 Web Audio，
主播放的 `audio` 元素完全不接触 Web Audio API。

```js
let vuAudio = null; // 专用分析元素，仅 VU Meter 使用

function initAudioAnalyser() {
  if (audioContext) return;
  try {
    // 创建专用分析元素，src 与主 audio 保持同步
    vuAudio = new Audio();
    vuAudio.src = audio.src;
    vuAudio.currentTime = audio.currentTime;
    vuAudio.volume = 0;           // 静音，只用于分析
    vuAudio.muted = true;
    vuAudio.playsInline = true;
    if (state.playing) vuAudio.play().catch(() => {});

    audioContext = new (window.AudioContext || window.webkitAudioContext)();
    audioAnalyser = audioContext.createAnalyser();
    audioAnalyser.fftSize = 256;
    audioAnalyser.smoothingTimeConstant = 0.6;
    audioSource = audioContext.createMediaElementSource(vuAudio);
    audioSource.connect(audioAnalyser);
    // 不连接 destination，vuAudio 已静音
    audioDataArray = new Uint8Array(audioAnalyser.frequencyBinCount);
  } catch (e) {
    console.warn('Audio analyser not available:', e);
  }
}

async function cleanupSuspendedAudioContextIfNeeded() {
  if (!audioContext) return;
  try { audioSource?.disconnect(); } catch {}
  try { audioAnalyser?.disconnect(); } catch {}
  try { await audioContext.close(); } catch {}
  audioContext = null; audioAnalyser = null;
  audioSource = null; audioDataArray = null;

  if (vuAudio) {
    vuAudio.pause();
    vuAudio.src = '';
    vuAudio = null; // vuAudio 被 MediaElementSource 捕获，但我们不在乎它
  }
}
```

**好处**：
- 主 `audio` 元素完全不接触 Web Audio，锁屏/切歌完全不受影响
- VU Meter 的分析 audio 可以随意销毁重建
- 最符合"关注点分离"原则

**缺点**：
- 同一首歌打开两个流（多一倍带宽），若是本地流则无所谓
- vuAudio 需要同步 currentTime、播放状态等，有一定维护成本
- 对低延迟分析可能有轻微误差（两个流的缓冲不完全同步）

---

## 推荐修复路径

| 平台 | 推荐方案 |
|------|---------|
| Chrome / Firefox / Android | 方案一（captureStream） |
| Safari / iOS | 方案四（独立 vuAudio 元素） |
| 快速补丁，最小改动 | 方案三（stopVUAnimation 时清理） |
| 长期稳健架构 | 方案四 |

**实际建议**：

1. **立即**：在 `stopVUAnimation()` 中调用 `cleanupSuspendedAudioContextIfNeeded()`（方案三的一部分），减少 AudioContext 存活时间
2. **短期**：实现 `captureStream` 优先路径（方案一），Chrome/Firefox/Android 完全修复
3. **长期**：将 VU Meter 重构为独立 `vuAudio` 元素（方案四），彻底解耦

---

## 其他优化建议（与 VU Bug 无关）

### 1. `initAudioAnalyser` 缺乏错误恢复

当 `initAudioAnalyser` 抛错时，`audioContext` 可能已被创建但 `audioSource` 未创建，
导致后续 `audioContext` 非 null 但图不完整：

```js
// 当前代码：catch 只 warn，不清理
} catch (e) {
  console.warn('Audio analyser not available:', e);
  // ← audioContext 仍非 null！
}

// 建议：catch 时完整清理
} catch (e) {
  console.warn('Audio analyser not available:', e);
  try { audioContext?.close(); } catch {}
  audioContext = null; audioAnalyser = null;
  audioSource = null; audioDataArray = null;
}
```

### 2. `getAudioLevel()` 的左右声道是伪立体声

当前实现用**频率分段**模拟左右声道，而非真实双声道分离：

```js
// 当前：用频率低/高半区假装左/右声道
const leftLevel = ...low frequency half...
const rightLevel = ...high frequency half...
```

这是 AnalyserNode 的限制（mono 频谱）。如需真实立体声，需要：
- 使用 `ChannelSplitterNode` 分离左右声道
- 对每个声道单独创建 `AnalyserNode`

```js
// 真实立体声方案
const splitter = audioContext.createChannelSplitter(2);
const analyserL = audioContext.createAnalyser();
const analyserR = audioContext.createAnalyser();
audioSource.connect(splitter);
splitter.connect(analyserL, 0);
splitter.connect(analyserR, 1);
analyserL.connect(audioContext.destination);
```

### 3. `visibilitychange` 在 desktop 上的不必要触发

`visibilitychange` 在桌面 Alt-Tab 也会触发，当前会执行 AudioContext 清理。
建议仅在移动设备上启用激进的 AudioContext 清理：

```js
document.addEventListener('visibilitychange', () => {
  if (document.hidden) {
    const isMobile = /Mobi|Android/i.test(navigator.userAgent)
                  || window.matchMedia('(pointer: coarse)').matches;
    if (isMobile) {
      cleanupSuspendedAudioContextIfNeeded();
    } else if (audioContext?.state === 'suspended') {
      cleanupSuspendedAudioContextIfNeeded();
    }
    ...
  }
});
```

### 4. VU Meter 关闭后不清理 AudioContext

```js
function stopVUAnimation() {
  if (vuAnimationFrame) {
    cancelAnimationFrame(vuAnimationFrame);
    vuAnimationFrame = null;
  }
  // ← 建议加上：
  // cleanupSuspendedAudioContextIfNeeded();
}
```

VU 关闭后 AudioContext 应立即释放 audio 元素的捕获，
而不是等到下次 `startBrowserPlayback` 才清理。
这是导致锁屏问题的关键遗漏点之一。

### 5. `shouldUseWebAudioAnalyser()` 不完整

当前基于屏幕宽度判断是否启用 VU，但应在 `initAudioAnalyser()` 中也加入检查：

```js
function initAudioAnalyser() {
  if (audioContext) return;
  if (!shouldUseWebAudioAnalyser()) return; // ← 缺失的防护
  ...
}
```

---

## 总结

| Bug | 严重度 | 根因 | 最小修复 |
|-----|--------|------|---------|
| 锁屏后无声 | 高 | `createMediaElementSource` 残留捕获 | `stopVUAnimation` 中清理 AC |
| 切歌无声 | 高 | 同上，audio 元素路由被破坏 | 同上 + 方案一 captureStream |
| 再次打开 VU 报错 | 中 | 同一 audio element 被重复 `createMediaElementSource` | `initAudioAnalyser` guard 检查 |
| AC 销毁后 null 未完整清理 | 低 | catch 块缺清理 | 补全 catch 清理逻辑 |
| 左右声道为伪立体声 | 低 | 使用单 AnalyserNode 频谱分段 | ChannelSplitter 重构 |
