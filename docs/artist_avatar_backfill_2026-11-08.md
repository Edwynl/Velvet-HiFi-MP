# 歌手头像补齐（专辑 ≥3 张）· 2026-11-08

## 范围

- 曲库共 2405 位歌手：2027 位已有头像，378 位没有。
- 本次只处理 **专辑 ≥3 张且没有头像** 的 **23 位**（合计 97 张专辑）。
- **已有头像的歌手一律不动**；本次没有改动任何一张已有头像，也没有覆盖任何已存在的图片文件。
- 判定条件：`artists.image_path` 为空 **且** `albums` 表中该歌手的去重专辑数 ≥3。
- 完成后复核：`无头像且 ≥3 专辑` 的歌手为 **0 位**；库内有头像歌手 2027 → **2050**。

机器可读清单见 `docs/artist_avatar_backfill_2026-11-08.json`（逐条的来源、证据链、核实结论）。

## 为什么这些图是对的

历史上这套头像来自 `enrichment.search_artist_photo()`：取 Deezer 的第一条结果加 DuckDuckGo 的第一张图，从不核对「这张图是不是这个人」。2026 年 6 月的审计里，19 张里约一半是错的（张碧晨得到一个穿皮衣的白人、张靓颖得到土星插画、孟佳得到陈奕迅海报、刘雨昕得到音乐节横幅）。

本次不再按「名字像就收」，而是**按发行目录交叉验证**：

1. **专辑标题比对**是主证据。把库里该歌手的专辑名与音乐服务上该艺人页的专辑名做归一化比对（去标点、去 `- Single` 等发行标记、`feat.` 后缀取前半段），命中数 ≥2 即判定为同一人；命中 1 条时要求名字也吻合。
   - 这条规则直接解出了名字完全不同的案例：库里 `謝凌君` 在 Deezer 上叫 **Lisa Djaati**，4 张专辑对得上；库里 `Li Nasi` 在 Deezer 上叫 **Nasi Li**，5 张对得上。
2. **专辑封面互证**。库内自带封面，封面常印着艺名：`Samuelyu` 的《犬吠》封面印着 **余毓兴**，`Lizhen` 的《遇見你》印着 **俐蓁**，`FIFI HU` 的《Candy》印着 **胡呈嫚**，`Andy Schaub` 的《Lutu》同时印着 **ANDY SCHAUB** 与 **安哲逸**。
3. **库内别名表**参与检索。`artist_aliases` 是库内「同一人的其他名字」的权威表，补图时会用别名一起检索，因此 `Samuelyu` 也能以 `余毓兴` 命中 Apple。
4. **占位图一律拒收**。服务的通用图（Apple 的 `apple-music.png`、Deezer 的空 hash 灰块、纯色块图）不写入库——比首字母占位还糟。
5. **逐张目视核实**。脚本判定的「对」不等于「图对」，最后一步是把 23 张头像与其本人专辑封面并排看一遍。

## 目视核实抓出的两张错图

这一步不能省。**Apple 艺人页自己的头图也会给错人**：

| 歌手 | Apple 给的图 | 问题 | 换成 |
|------|--------------|------|------|
| 清水翔太 | 草原骑马牛仔 | 图库照片，与《恋唄》封面上的本人（白发脏辫歌手）完全不是一个人 | Wikimedia Commons《Shimizu Shota Japan Day 2008》现场照，与《恋唄》封面同一人 |
| 安哲逸 / Andy Schaub | 女团霓虹舞台照 | 与库里《Lutu》封面上的本人（深色卷发）不是同一人 | Deezer「安哲逸」肖像，与《Lutu》封面人脸逐项一致（深色卷发、长脸、高鼻梁） |

两张都写进了 `docs/artist_portraits_picked.json`，以后重跑不会把错图放回去。

其余 21 张逐张核对通过：或是本人正脸照，或是该歌手本人作品的画面（Deezer/Apple 上的「艺人图」经常直接用单曲封面），与其专辑封面一致。

## 图源与逐条证据

| id | 库内名字 | 服务端身份 | 证据 | 图源 |
|----|---------|-----------|------|------|
| 166 | Grass Walkers | Grass Walkers | 专辑 3/3 | Deezer 艺人图 |
| 189 | Lizhen | 俐蓁 | 封面印名 + Apple 专辑 Slow Motion／遇見你／後來的後來 | Apple 艺人图 |
| 231 | Timeless Fusion Party | Timeless Fusion Party | 专辑 10/10 | Deezer 艺人图 |
| 294 | Croixx | Croixx | 专辑 3/3（Bad Habits／Bonnie & Clyde／Covered in Fire） | Deezer 艺人图 |
| 422 | johnny yukon | johnny yukon | 专辑 5/5 | Deezer 艺人图 |
| 605 | Jeson | Jeson | 专辑 6/6（Hallelujah／La cura sbagliata／Perdonare te…） | Deezer 艺人图 |
| 696 | Finn L | Finn L | 专辑 4/6（其余为中文名条目） | Deezer 艺人图 |
| 770 | 88rising | 88rising | 专辑 6/6（2HII／Let It Go…） | Deezer 艺人图 |
| 1169 | 清水翔太 | Shota Shimizu | 别名命中 + 专辑 PUZZLE - From THE FIRST TAKE | **人工选定**（Wikimedia 现场照） |
| 1240 | 謝凌君 | Lisa Djaati | 专辑 4/8（漣漪效應／Re-Vox／Apple and Orange…） | Deezer 艺人图 |
| 1268 | Shiny | Shiny | 专辑 3/3（Jian Qiang／Shiny - 1st EP／Yu Bei Lang Man） | Deezer 艺人图 |
| 1560 | FIFI HU | 胡呈嫚 | 封面印名 + Apple 专辑 Candy／Dance Tonight／BAD GURL | Apple 艺人图 |
| 1576 | Ji.Banai | Ji.Banai | 专辑 3/3 | Deezer 艺人图 |
| 1588 | Kuei-Gang | Kuei-Gang | 专辑 3/3 | Deezer 艺人图 |
| 1596 | Li Nasi | Nasi Li | 专辑 7/7（Accustomed to Sorrow／So Be It…） | Deezer 艺人图 |
| 1624 | RedLee | Redlee | 专辑 3/3（Tong Be Diao／VorTeX／InfraRED） | Deezer 艺人图 |
| 1630 | Samuelyu | 余毓兴 | 封面印名 + Apple 专辑 以犬之名／红／六月战歌 | Apple 艺人图 |
| 1631 | Sansheng Chiang | Sansheng Chiang | 专辑 8/8 | Deezer 艺人图 |
| 1643 | TOLAKU | TOLAKU | 专辑 3/3 | Deezer 艺人图 |
| 1651 | Uku Lee | 優客李驥 | Apple 专辑 認錯2025／The Time We Dream／啟航夢想 | Apple 艺人图 |
| 1678 | Andy Schaub | 安哲逸 | 封面印名 + Apple slug `andy-schaub` | **人工选定**（Deezer 肖像） |
| 1683 | HKCO | HKCO | 专辑 3/3（Encore／Telling Stories…） | Deezer 艺人图 |
| 1746 | SmashRegz | SmashRegz | 专辑 3/3（Film 戲劇話／嘎抓／Loading） | Deezer 艺人图 |

来源分布：Deezer 艺人图 17、Apple 艺人图 4、人工选定 2。

## 顺带写入的别名（5 条）

补图过程中确认了 5 位歌手在音乐服务上的真实名，这 5 条已写入 `artist_aliases`，来源标记为 `release_corroborated`，并在 `artist_alias_provenance.evidence` 留下证据：

| 歌手 | 新别名 | 依据 |
|------|--------|------|
| Lizhen | 俐蓁 | 专辑封面印名 + Apple 发行目录 |
| FIFI HU | 胡呈嫚 | 专辑封面印名 + Apple 发行目录 |
| Samuelyu | 余毓兴 | 专辑封面印名 + Apple 发行目录 |
| Andy Schaub | 安哲逸 | 专辑封面印名 + Apple slug |
| Uku Lee | 優客李驥 | Apple slug `uku-lee` + 发行目录逐条对应 |

**地区货架零影响**：加别名前后这 5 位的地区判定完全不变（taiwan / hongkong 不变），别名按 `artist_id` 绑定也不影响其他歌手。

## 顺带修掉的脚本问题

`scripts/backfill_artist_images.py` 这次改了五处：

1. **Apple 艺人图 404**。定位艺人身份图的正则停在尺寸段之前，交出去的 URL 没有尺寸段，Apple 直接回 404（`..._file_cropped.png` → 404；`..._file_cropped.png/600x600bb.jpg` → 200）。`_upscale()` 现在会把尺寸段补回去。
2. **Apple 通用图被当成头像存下**。页面的 `og:image` 常常是 Apple 的占位图（`apple-music.png`），脚本虽然识别出了它，却仍按 `ok` 返回，会把 Apple Music 的 logo 存成艺人头像。现在识别为通用图就跳过该源、继续问下一个源。
3. **扁平占位图没有换源**。服务返回纯色块时（实测 Deezer 的空 hash 灰块、Apple 的 `Music221` 底图），旧逻辑只在最后才试 Wikimedia。现在命中扁平图就把该源拉黑，换下一个源重试。
4. **网络抖动被当成「查无此人」**。长跑时 Deezer 限流（429）与偶发 DNS 失败会让本来能查到的歌手直接落空——本轮 `Grass Walkers` 与 `Lizhen` 就这样丢过一次。现在 API 调用、艺人页抓取与图片下载都带重试与退避。
5. **新增 Deezer 图源**（免密钥公开 API），并用「专辑标题交叉验证」替代纯名字匹配；Deezer 按名字搜不到某个歌手时（`Shiny` 搜出来是别的 Shiny），还会用库内专辑名反查艺人页。脚本同时改为读取库内 `artist_aliases` 作为检索词。

另外新增 8 条单元测试（尺寸段补全、发行标记归一、专辑名互认、占位图拒收、通用图不再落库），`pytest tests/` 全绿（580 passed + 819 subtests），`npm run test:pwa` 通过。

## 复核方式

```powershell
# 还有没有 >=3 张专辑却没头像的歌手
python -c "import sqlite3; c=sqlite3.connect(r'velvet_data/library.db'); \
print(c.execute('''SELECT a.id,a.name FROM artists a JOIN albums al ON al.artist_id=a.id \
WHERE (a.image_path IS NULL OR a.image_path='') GROUP BY a.id \
HAVING COUNT(DISTINCT al.id)>=3''').fetchall())"

# 单张核对：头像 + 一张专辑封面并排
python $env:TEMP\velvet_contact_sheet.py out.png 1169,1678
```

服务端的 `/api/artists/{id}/image` 每次请求都实时读库里的 `image_path`，因此库一改，8765 立刻生效，无需重启。

## 边界

- 静态检查与脚本测试不能代替真机复核；Apple 的 PWA 行为仍需真机验证，本次未触碰任何 PWA 代码。
- 真人写实头像的效果最终仍建议在 iPhone 上过一眼。
