# Velvet VU Meter 故障分析

基于当前上传的 `index.html` 代码，对“VU meter 能打开但指针不动”的现象做了归纳分析。

## 现象

- 桌面端和手机端都失效
- VU 面板可以打开
- 指针不动
- `vuAudio.volume = 0` 是有意设计，用来避免影响主播放，并兼顾锁屏继续播放

---

## 结论

这更像是 **VU 的取样链路没有拿到有效音频数据**，而不是 UI 或动画本身坏了。

最可能有两个原因：

### 1. VU 只对 Browser Output 生效

当前实现里，VU 分析依赖页面内本地 `audio` 元素同步出来的 `vuAudio`。

如果实际播放走的是：

- `cast`
- `native`

那么音乐虽然确实在播，但 **并不是在浏览器本地 `audio` 元素里播**。  
此时 VU 逻辑仍然去读本地 `audio`/`vuAudio`，分析器就只能拿到空数据，结果就是针始终停在初始位置。

这也能解释：

- 为什么桌面端和手机端都坏
- 为什么回退旧版本也不行

因为问题不一定只在版本代码本身，也可能是当前输出路径已经变了。

---

### 2. `initAudioAnalyser()` 初始化条件过严

当前逻辑本质上是：

- `vuAudio.readyState >= 3` 时才立即初始化
- 否则等待 `canplaythrough`

这对普通完整音频文件可能没问题，但对流式地址（例如 `/api/stream/...`）来说，`canplaythrough` 很可能：

- 根本不触发
- 或触发得很晚

这样就会导致：

- `doInit()` 没有执行
- `audioSource` 没创建
- `audioDataArray` 没创建
- `getAudioLevel()` 永远返回 0
- 指针一直不动

这个原因我认为是 **最像主因** 的。

---

## 不是主因的点

### `vuAudio.volume = 0`

这个不是主要问题。

你的设计目的是：

- 让 `vuAudio` 只作为分析链路
- 不实际出声
- 不干扰主播放
- 不影响锁屏行为

这个设计本身是合理的。  
因此问题不应简单归结为“音量设成 0 所以 analyser 没数据”。

---

## 建议修改

## A. 放宽 analyser 初始化条件

把原来类似这样的逻辑：

```js
if (vuAudio.readyState >= 3) {
  doInit();
} else {
  vuAudio.addEventListener('canplaythrough', doInit, { once: true });
}
```

改为：

```js
if (vuAudio.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA) {
  doInit();
} else {
  vuAudio.addEventListener('loadeddata', doInit, { once: true });
  vuAudio.addEventListener('canplay', doInit, { once: true });
  vuAudio.load();
}
```

---

## B. 在 `doInit()` 里确保 AudioContext 已恢复

建议写成：

```js
async function doInit() {
  if (!audioContext || !vuAudio || audioSource) return;

  try {
    if (audioContext.state === 'suspended') {
      await audioContext.resume();
    }
  } catch {}

  vuAudio.play().catch(() => {});

  audioSource = audioContext.createMediaElementSource(vuAudio);
  audioSource.connect(audioAnalyser);

  audioDataArray = new Uint8Array(audioAnalyser.frequencyBinCount);
}
```

---

## C. 非 Browser Output 时直接禁用或提示

如果当前输出不是浏览器本地播放，建议不要继续显示可用的 VU 开关，否则用户会感觉是“坏了”。

可加保护：

```js
function toggleVUMeters(show) {
  if (show !== false && !isBrowserZone()) {
    showToast('VU meter only works in Browser Output');
    return;
  }

  // 原逻辑...
}
```

---

## 建议优先排查顺序

### 第一优先级

先看控制台里 VU 动画相关日志，例如：

- `levels.left`
- `levels.right`
- `vuAudio.paused`
- `vuAudio.readyState`

如果表现为：

- `levels.left/right` 一直是 0
- `readyState` 长时间卡在较低状态

那就很像是 **初始化被 `canplaythrough` 卡住**。

---

### 第二优先级

确认当前是不是在 **Browser Output**。

如果实际播放已经切到：

- Native
- Cast

那么当前这套基于本地 `audio` 的 analyser 本来就拿不到有效 PCM 数据。

---

## 最终判断

最有可能的是下面两者之一：

1. **当前输出路径不是 Browser Output**
2. **analyser 初始化依赖 `canplaythrough`，导致 `doInit()` 没真正执行**

其中第 2 条我认为最值得先改，因为命中率最高，而且改动最小。

---

## 推荐你先做的事

先改掉初始化逻辑：

- 不再只等 `canplaythrough`
- 改为 `loadeddata` / `canplay`
- 并在 `doInit()` 中强制 `audioContext.resume()`

如果改完后浏览器本地输出下 VU 恢复了，那基本就能确认根因。

---

## 一句话总结

**不是 UI 坏了，也不太像是 `volume = 0` 的设计导致的；更像是 VU 的独立分析音频链路没有真正初始化成功，或者当前播放根本不在浏览器本地链路上。**
