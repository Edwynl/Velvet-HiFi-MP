# 歌手浏览 / Back 误弹管理员密码修复

2026-10-17，Windows 本地，分支 `codex/manage-admin-password`。

根因：`renderArtist()` 每次进入歌手页面都会自动 POST `/artists/{id}/enrich-bio`。这会更新数据库，因此被管理员校验拦截。专辑 Back 返回歌手页重新渲染，同样会发起请求；并非 Back 自身需要管理权限，也与某一位歌手无关。

修复：浏览时直接显示 GET `/artists/{id}` 已返回的 `artist.bio`，转义其内容。缺少简介时不自动申请修改。批量补充简介仍在 Settings 中由用户明确操作；“Re-read Tags” 的管理员保护保留，取消验证静默结束。

- 修改前回归：预期 FAIL，自动 biography POST 计数为 1。[日志](before-fail.txt)
- 修复后新增回归：3 种简介状态、9 次 Back 均 PASS；主动修改仍触发管理员验证。[日志](artist-regression.txt)
- 完整前端：14 / 14 脚本套件 PASS，退出码 0。[日志](frontend-full.txt)
- `localhost:8765` 隔离预览：真实页面从专辑 10391 连续返回歌手 239 三次；没有歌手 POST、没有管理员框、没有浏览器错误。[结果](live-preview.json)

源代码已由当前 8765 预览直接提供，已打开的标签页需刷新后加载。未修改管理员鉴权规则、密码、真实音乐文件或主数据库。未 Push、Merge 或触发 GitHub Actions。所有本地提交包含 `[skip ci]`。
