# 歌单离线下载 — 设计

日期：2026-11-07　分支：`feat/offline-playlists`（基于 `origin/main`）

## 为什么做

用户在外面通过 Tailscale 连回家里的 VELVET 听歌，曲库以 FLAC 无损为主，每首 25–40MB，
在线播放持续消耗手机流量。希望在家（Wi-Fi）把歌单下载到手机，在外面播放时直接读本地，不走网络。

使用环境：iPhone 14 Pro Max，**从主屏幕打开的 PWA**（Safari 内核）。PWA 已经过针对性优化。

## 目标

1. 歌单页可以一键下载整张歌单（全部曲目），下载的是**原文件**（FLAC 原样，不转码、不压缩）。
2. 播放时，曲目已下载且**未启用 EQ/DSP**时，优先播本地副本，不发起音频网络请求。
   **启用 EQ/DSP 时走在线**（用户决定：要 EQ 就在线）。但如果服务器连不上（`navigator.onLine === false`，
   或在线流加载/播放出错），有本地副本就回退到本地原文件播放，并 toast 提示"Offline copy, EQ off"。
   升频（upsample ≠ none）同样属于服务器端处理，按 EQ 同样规则处理（我的判断；默认值为 none）。
3. 断网（如地铁无信号、Tailscale 断开）时，已下载的歌单仍能打开并播放。
4. 下载可中断、可续传：切到后台/锁屏/断网后回到 App，从未完成的曲目继续。
5. 能看到离线占用空间，能删除某张歌单的离线内容。

## 非目标

- **本地副本不带 EQ/DSP/升频。** 用户已确认：下载原文件，不烧入 EQ。本地播放时服务器端 DSP、升频不生效。
- 不做后台下载（iOS 不允许网页 App 在后台持续下载）。
- 不做自动同步。iOS 网页拿不到"当前是 Wi-Fi 还是蜂窝"，自动下载可能反而耗流量。一切下载由用户手动触发。
- 不做专辑/单曲的下载入口，本次只做歌单。
- 不影响投放（DLNA/zone）播放路径，那条路径仍由服务器推流。

## 反目标（技术上能用但算失败）

- **改动现有排版。** 只允许在现有位置"加"小元素，不能挪动、重排、改变行高或已有按钮样式。iPhone 宽度（430px）下工具栏不能换行。
- 下载过程中把整首 FLAC 读进内存导致 PWA 被 iOS 杀掉。
- 本地播放破坏锁屏连续播放、Media Session、断点恢复等已经调好的移动端行为。
- 删除一张歌单的离线内容时，误删另一张已下载歌单也用到的同一首歌。
- 下载用到的凭据出现在可被缓存/记录的位置（沿用现有的短期 stream ticket，不把 API key 放进 URL）。

## 约束与已知事实（来自代码）

- 浏览器播放入口：`static/index.html` 的 `startBrowserPlayback()`，通过 `streamUrl()` 拼出
  `/api/stream/{id}?upsample=…&dsp=…&ticket=…`，赋给 `<audio>`。
- `GET /api/stream/{id}?upsample=none`（不带 dsp）对浏览器可直接解码的文件返回原文件；
  对浏览器放不了的编码（E-AC3、多声道 FLAC 等）返回立体声 PCM WAV 兼容流。**下载就用这个 URL**，
  格式选择自动正确，服务器端无需改动。
- `<audio>` 发不了 `X-API-Key`，开启保护时用 `POST /api/stream-ticket` 拿到的短期票据（`currentStreamTicket()` / `refreshStreamTicket()`）。
  下载用 `fetch` 同样带票据。
- 歌单页：`static/js/library-views.js` 的 `renderPlaylist()`，工具栏 `.playlist-toolbar-actions` 里已有 Rename / Delete 两个 `btn btn-ghost`。
- Service Worker：`sw.js`，静态资源缓存 `velvet-vN`，`/api/*`（除封面）一律走网络，失败返回 503 JSON。
- 前端测试：`tests/frontend/*.test.mjs`（node + jsdom），在 `package.json` 的 `test:frontend` 里串联。

## 已定方案（我的判断，用户未逐条确认）

- **存储用 IndexedDB 存 Blob**，不放在 Cache API 里经 SW 返回给 `<audio>`。原因：iOS 上 `<audio>` 走 SW 返回的
  缓存响应需要正确处理 Range/206，历史上问题多；而 `URL.createObjectURL(blob)` 直接交给 `<audio>`，
  拖动进度、锁屏播放都由 WebKit 自己处理。WebKit 中 IndexedDB 里的 Blob 是落盘的，不占内存。
- 下载成功后调用 `navigator.storage.persist()` 申请持久存储；用 `navigator.storage.estimate()` 显示占用。
- **按"曲目"为单位存储，按"歌单"为单位引用。** 曲目 Blob 以 `track_id` 为键只存一份；
  另存每张离线歌单的清单（歌单 id、名称、曲目列表元数据、下载时间）。删除歌单离线内容时，只删除不再被任何离线歌单引用的曲目。
- **续传粒度为单曲**：一首没下完就重下这一首，不做字节级续传。一次只下一首（顺序下载），失败的曲目重试几次后跳过并计数。
- 下载时带上歌单详情中的曲目元数据（标题、艺人、专辑、时长、格式等），离线时歌单页用它渲染。
- 播放：在 `startBrowserPlayback()` 开头查本地；命中且无 EQ/DSP/升频（或离线回退）则 `audio.src = blobURL`，跳过票据获取、`start=` 偏移（本地文件可直接 seek）以及 DSP 启动健康检查。
  未命中保持现有逻辑不变。为了不打断 iOS 的手势链/自动连播，应在当前曲目开始时**预先准备好队列下一首的 blob URL**（或保证查询足够快），并及时 `revokeObjectURL` 旧的。
- 离线可用：SW 静态资源已含 `library-views.js` 等；新增的前端文件也要加入 `STATIC_ASSETS` 并提升缓存版本号。
  当 `/api/playlists/{id}` 请求失败而本地有该歌单清单时，歌单页用本地清单渲染。歌单列表页在断网时至少能看到已下载的歌单。
  断网冷启动时 SW 对 `/api/*` 返回 503，启动流程（`/api/security/status`、首页书架）需验证不会卡死或弹登录框；首页书架为空可以接受。

## 界面（只加不改）

- 歌单工具栏：在 Rename / Delete 旁加一个同样式的 `btn btn-ghost` 按钮。按钮文字随状态变化：
  `Download` → `12/120`（下载中，可点暂停）→ `Downloaded`（全部完成）→ `+3`/`Update`（歌单有新曲目未下载）。
  需在 430px 宽度下验证不换行；放不下时文字缩短为图标加数字。
- 曲目行：已下载的曲目加一个很小的标记（如一个点或小图标），放在现有元素里，不改变行高和对齐。
- 设置页：新增一个 "Offline" 小节，显示总占用、各离线歌单（名称、曲目数、大小、删除按钮）。追加在现有小节之后，不改动现有小节。
- 歌单页内删除离线内容：长按/再次点击 `Downloaded` 时弹出现有的 `showConfirm` 对话框确认。
- UI 文案沿用现有的英文风格。

## 留给实现者决定

- 新代码放在独立模块（例如 `static/js/offline-store.js`）还是并入现有文件；是否需要懒加载。
- IndexedDB 的 schema 细节、Blob URL 的生命周期管理方式。
- 下载进度在离开歌单页后是否继续（建议：页面内状态与下载任务解耦，离开页面继续下载，回来时显示进度）。
- 存储空间不足（`QuotaExceededError`）时的提示文案与处理方式（停止下载，保留已下载部分）。
- 具体测试拆分。至少覆盖：已下载曲目播放不发出 `/api/stream` 请求；删除歌单离线内容时共享曲目不被误删；
  续传会跳过已完成曲目；断网时歌单页从本地清单渲染；SW 静态资源列表包含新文件。

## 验收

- 前端测试全部通过（`npm run test:frontend`），Python 测试不受影响。
- 浏览器内实测：下载一张小歌单 → 断开服务器 → 歌单可打开、可播放、可拖动进度、可自动下一首；网络面板中无 `/api/stream` 请求。
- 430px 宽度截图对比：工具栏不换行，曲目行高不变。
- iPhone 实机（用户自测）：主屏幕 PWA 下载、锁屏连播、飞行模式播放。
