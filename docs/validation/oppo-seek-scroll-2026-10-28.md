# Android 平板进度条与浏览位置修复

## 定位

- 桌面布局的进度时间默认 `opacity: 0`，只有 `:hover` 显示；触屏平板也会使用这个布局。
- 原拖动逻辑只有 Pointer Events；没有 Touch Events 拖动回退，且 `setPointerCapture()` 抛错会中止处理。无法接触 OPPO Pad 真机，不能断言其具体浏览器版本走的是哪条失败路径。
- `renderArtist()` 在已经显示专辑卡片后继续等待 `/artists/:id/enrich-bio`。外层 `render()` 在整个函数完成后调用 `handoff.finish()`，无条件再次回顶。慢请求让用户先看到内容并开始滚动，随后被晚到的回顶打断；旧页面的完成也可能干扰新页面。
- Albums / Artists 标题栏只检查事件目标本身的 tagName。按钮内部的 span、SVG 或 path 点击会被误认为标题栏背景点击，触发回顶。

## 修改

- 触屏媒体查询下显示桌面进度时间与滑块；鼠标悬停和触摸拖动显示目标时间。
- 支持 Pointer Events 和 Touch Events，识别触点、取消、丢失捕获和窗口失焦，防止重复事件及合成 click 重复提交。捕获 API 不可用时仍能完成拖动。
- 拖动只更新预览，松手提交一次 seek，避免反复重启转码流；播放时间更新不覆盖拖动预览。Touch Events 的非 passive 监听仅绑定在进度条，避免阻塞页面正常滚动。
- 页面回顶在内容 DOM 切换时执行一次。简介独立加载，只更新原来的、仍连接的简介节点。
- 标题栏通过 `closest()` 排除交互控件及其子元素。
- 保留 viewport、Apple 元数据、manifest、standalone、安全区、启动方案、认证与缓存设置。

## 验证

- `npm run test:frontend`：通过全部前端测试。
- `npm run test:pwa`：通过 standalone viewport、导航返回、PWA 认证检查。
- 最终触控监听调整后，重新运行 `mobile-player-behavior.test.mjs`；导航补充案例重新运行 `navigation-back.test.mjs`：通过。
- 旧 main 快照运行新增导航测试：失败，晚到的 render 完成把 scrollTop 从 550 重置成 0；修复后保持 550。
- Chromium 1280 × 800、触屏/coarse pointer、CDP 原生触摸输入：60% 显示 `2:00`，拖动期间 seek 次数为 0，松手后只有一次 `120` 秒跳转。屏蔽全部 Pointer Events 后，Touch Events 仍完成同样的跳转。
- Chromium 慢简介：scrollTop 从 550 到 555，是浏览器滚动锚定补偿；同一卡片屏幕坐标从 362.34 到 362.73，保持视野，没有回顶。延迟的专辑 render 完成后 scrollTop 保持 420。
- 测试使用独立端口、静态文件服务和合成 API 数据；未读写正式媒体库。
- OPPO 原生浏览器、真机音频 seek 和 iPhone standalone 的最终交互仍需设备复核；Chromium 模拟不是 OPPO 实测。

事件处理参考：[W3C Pointer Events](https://www.w3.org/TR/pointerevents4/)。
