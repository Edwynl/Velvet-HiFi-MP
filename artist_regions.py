"""Read-only listening-region facets; never rewrite imported artist metadata.

These are browsing markets, not citizenship. Curated names and aliases take
precedence over release evidence. Ambiguous names remain in Other.
"""
from collections import Counter, defaultdict
import re
import unicodedata

REGION_LABELS = {
    'mainland': 'Mainland', 'hongkong': 'Hong Kong', 'taiwan': 'Taiwan',
    'japan': 'Japan', 'korea': 'Korea', 'west': 'International',
    'unknown': 'Other',
}


def _key(value):
    return ''.join(unicodedata.normalize('NFKC', value or '').casefold().split())


_NAMES = {
    # Names are matched with _key(), i.e. the exact stored string normalised.
    # Aliases matter as much as display names here: the library often keeps a
    # romanised or parenthesised form that no curated roster would carry. An
    # entry that could name two artists (a bare "Susan Wong" covers both 蘇慧倫
    # and 黃浩詩) is deliberately omitted so the tie can stay Other. Romanised
    # soloists are likewise left out: in this library they are often student or
    # diaspora self-releases with no market to infer at all.
    'mainland': '许巍|許巍|朴树|樸樹|李健|毛不易|周深|薛之谦|薛之謙|汪峰|崔健|窦唯|竇唯|许嵩|許嵩|暗杠|陈粒|陳粒|赵雷|趙雷|李荣浩|李榮浩|张靓颖|張靚穎|张碧晨|張碧晨'
                '|陈洁丽|陳潔麗|童丽|童麗|王晰|Elvis Wang|庄心妍|莊心妍|王晨宇IceCraz'
                '|马思唯|馬思唯|MaSiWei|梁博|張杰|张杰|希林娜依·高|希林娜依高|谢帝|鬼卞'
                '|陈婧霏|陳婧霏|郭思达|王以太|牛牛|Niu Niu|Yamy郭颖|Yamy郭穎|DIOR 大穎|DIOR大颖'
                '|派克特|袁娅维|GAI周延|曾比特|Mike曾比特|Mike 曾比特|侃侃|迪凡尼|华晨宇|胡琳'
                '|Bianca Wu|邹丽珍|刘恋|梁龙|邓典|Dian Deng|劉大壯|DaZhuang Liu|妞妞|韩红|Han Hong'
                # Second pass: mostly feature-only entries (no album of their own)
                # that the roster can still place because the identity is known.
                '|王忻辰|隔壁老樊|刘柏辛|Lexie Liu|刘柏辛Lexie|王嘉爾|Jackson Wang|贾廷一|张远|張遠'
                '|李雪琴|范丞丞|黄绮珊|井胧|Jing Long|戚薇|胡歌|七元|Qi Yuan|萧全|韦唯|韋唯'
                '|黄子弘凡|Lars Huang|杨坤|杨肸子|陈楚生|金志文|金玟岐|金莎|谭维维|赵磊|颜人中|高进'
                '|海来阿木|黄舒骏|陈冰|陈卓璇|张含韵|吉克隽逸|王蓉|王源|王铮亮|张新成|周也|陈都灵|龚俊'
                '|徐子未|房东的猫|摩登兄弟刘宇宁|刘宇宁|王赫野|曹轩宾|李兰迪|李嘉格|敖瑞鹏|旭日阳刚|何炅'
                '|佟梦实|侯明昊|小柯|张星特|张钰琪|魔动闪霸|霓虹花园NeonGarden|马赛克|马赛克乐队|艾热AIR'
                '|门尼|朴春|段奥娟|陈墨一（吖毛）|苏唯|刘凤瑶|刘端端|刘雨昕|叶里|孟佳|安崎|尚宇翔'
                '|张予曦|张哲瀚|张小厚|张磊|李泊凡|杨波|武艺|段银莹|毕雯珺|江静|沈依莎|满舒克|焦迈奇'
                '|王唯旖|王啸坤|王嘯坤|王嘉诚|王宇宙Leto|王弦|王栎鑫|王珺|王睿卓|王霏霏|田嘉瑞|白小白'
                '|礼韦 Thehopend|秦昊|程潇|簡弘亦|肖俊|胡夏|苏楚力|葛珊珊|袁帅|霍尊|马闻远|鹭卓|黄誉博'
                '|姜大声|陈安立|陳文強|陳樂一|陈辉|WILLIM缪维霖|丁于|小麻M.Refre$H'
                '|Jony J|Vava|Higher Brothers|KnowKnow|Melo|7Teen|Bruce Liu|Xiao Xia|GoKKy'
                '|Xiayin Wang|Jo-Yu Chen|Liu Yunzhi|China National Symphony Orchestra|余佳运|王加一'
                '|盛夏光年|Shen Wanwan|张可盈|孔令奇|张一鸣|李曉東|艾小伦|喻言|王箏|蔡国庆'
                '|陳安立|陳穎欣|陳紅|陳輝|雅麗|龚玥|承桓|宋沛 MattyPei|慧凯|DENZEL丹澤|张昊'
                # Names confirmed by a second source in 2026-10: the singer was
                # stored under a romanisation or an English name, and the
                # shelf could not place them until the real name was found.
                '|LBI利比|弦子|肖佳|艾志恒Asen|艾辰|苏星婕|賈廷一|陈果|麦小兜|黃齡|郁可唯'
                '|功夫胖KungFu-Pen|唐艺|大壯|海哲明|金池'
                '|劉惜君|唐藝|大壮|宋亚轩|宋波涛|揽佬SKAI ISYOURGOD|楊坤|袁婭維|謝帝|陳果|黄龄'
                '|Mr.岑|何和声|何潔|余火火|刘家奇|庄辉辉|张不翻|张良|张鑫|抠抠|拜荣基|探长'
                '|晴小瑶|杨树人|杨默依Moi|潘成|烟|程响|罗一舟|葉斯淳|蒋坤炜|趙馨玥|钟琳涵|闭文思'
                '|陈一豪|陈小满|陈琪东|雲汐|黄俊涵'
                '|云汐|傅菁|叶斯淳|宋沛Matty|岑先生|杨泽霖|皮卡潘|秦岚|许佳豪'
                '|DENZEL丹泽|何洁|張含韻|王錚亮|譚維維|赵馨玥|鄒麗珍|陳楚生',
    'hongkong': '邓紫棋|鄧紫棋|G.E.M.|陈奕迅|陳奕迅|Eason Chan|张学友|張學友|Jacky Cheung|刘德华|劉德華|Andy Lau|张国荣|張國榮|Leslie Cheung|王菲|Faye Wong|Beyond|陈慧娴|陳慧嫻|Priscilla Chan|李克勤|Hacken Lee|容祖儿|容祖兒|Joey Yung|梅艳芳|梅艷芳|Anita Mui|谭咏麟|譚詠麟|Alan Tam|林忆莲|林憶蓮|Sandy Lam|陈柏宇|陳柏宇|Jason Chan|石詠莉|石咏莉|Sukie S'
                '|林子祥|陳百強|Danny Chan|黎瑞恩|陳潔儀|歌莉雅|Goalpha|薛凱琪|Fiona Sit|薛凯琪'
                '|林奕匡|Phil Lam|林奕匡 (Phil Lam)|鍾鎮濤|Kenny Bee|鄭融|Stephanie Cheng|關心妍'
                '|Jade Kwan|謝霆鋒|谢霆锋|Jer 柳應廷|菲道尔|Firdhaus|葉巧琳|Mischa Ip'
                '|Ian 陳卓賢|Anson Lo 盧瀚霆|Edan 呂爵安|施鑫文月|Sukie S 石詠莉|姜濤|Keung To 姜濤'
                '|方炯鑌|Abin Fang|猴子lámpo|馮允謙|Jay Fung|冯允谦|張天穎|Jaime Cheung|杜麗莎|黃浩詩'
                '|黃妍|Cath Wong|陳慧敏|黃凱逸 (Zelos Wong)|黃貫中|陳凱詠|Jace Chan|JACE 陳凱詠'
                '|陳健安|On Chan|陳健安 (On Chan)|方皓玟|Charmaine Fong|關淑怡|馮曦妤|Sara Fung|雲浩影'
                '|Cloud Wan|Apple 凌雪怡|周國賢|Endy Chow|鄺美雲|攬佬SKAI ISYOURGOD|Tyson Yoshi'
                '|林家謙|Terence Lam|林家謙 (Terence Lam)|林家谦|黃劍文|Kimman Wong|Phoebus Ng 吳啟洋'
                '|Sabrina cheung 張蔓莎|張繼聰|林一峰|Lam Yat Fung|树妮妮|Serrini|湯寶如|呂方|周柏豪'
                '|梁詠琪|Gigi Leung|梁咏琪|林二汶|Eman Lam|劉小慧|刘惜君|史佳蔚|周富堅|炎明熹|葉良俊'
                '|董嘉鴻|葉振棠|蔣卓嘉|蕭景鴻 (阿弟)|邱士縉|邱彥筒|Marf Yau|Marf 邱彦筒|赖美云'
                '|Anson Kong 江𤒹生|Rock 何晉樂|吳若希|宋妍霏|林志美|Samantha Lam|林晓峰|梁祖尧|汤骏业'
                '|王祖蓝|罗景文|姚焯菲|路滨琪|路濱淇|陳光榮|房祖名|張智霖'
                '|KOLOR|Connie Shih|Chintung Tse|Lee Ping-Huei|Jessica Chan|Manson Cheung|呂方|吕方|周富坚|董嘉鸿|陳永健|張智霖|张智霖'
                # Names confirmed by a second source in 2026-10: the singer was
                # stored under a romanisation or an English name, and the
                # shelf could not place them until the real name was found.
                '|JC 陳詠桐|宋亞軒|宋波濤|專家Dickson|張敬軒|應昌佑|李彩樺|杜文澤|林苑'
                '|梁家輝|梁曉豐|梁祖儀|溫拿樂隊|王灝兒|王菀之|甄楚倩|衛蘭|超越|郭芯其|雷有輝|馬天佑|黎明'
                '|Edan 吕爵安|Ian 陈卓贤|JC 陈咏桐|Jer 柳应廷|Sabrina cheung 张蔓莎|Sukie S 石咏莉'
                '|专家Dickson|卫兰|应昌佑|张敬轩|李彩桦|杜文泽|温拿乐队|王灏儿|雷有辉|黄凯逸 (Zelos Wong)'
                '|孫涵|廖頴琛|李文曦|梁業|黃浩琳|何卓彥'
                '|何卓彦|孙涵|廖颕琛|梁业|黄浩琳',
    'taiwan': '周杰伦|周杰倫|Jay Chou|蔡依林|Jolin Tsai|张惠妹|張惠妹|A-Mei|五月天|Mayday|陶喆|David Tao|邓丽君|鄧麗君|Teresa Teng|张雨生|張雨生|张宇|張宇|伍佰|罗大佑|羅大佑|李宗盛|刘若英|劉若英|Rene Liu|苏打绿|蘇打綠|Sodagreen|鱼丁糸|魚丁糸|Oaeen|戴爱玲|戴愛玲|Ailing Dai|Princess Ai|棉花糖|katncandix2|蔡琴|田馥甄|Hebe Tien'
              '|王若琳|Joanna Wang|許茹芸|Valen Hsu|丁噹|丁噹 (Della)|陳綺貞 (Cheer Chen)|A-Lin|A-lin 黃麗玲|黃麗玲'
              '|張信哲|张信哲|Jeff Chang|范逸臣|王羽佳|Yuja Wang|柯有倫|Alan Kuo|徐若瑄|Vivian Hsu'
              '|蔡旻佑|Evan Yo|盧廣仲|Crowd Lu|盧廣仲 (Crowd Lu)|卢广仲|家家|廖昌永|蘇慧倫|苏慧伦|梁心頤|白安'
              '|洪佩瑜|莉莉周她說 Lily Chou-Chou Lied|人力飛行劇團|許哲珮|傻子與白痴|許光漢|陳嘉樺|Ella陈嘉桦'
              '|陳嘉樺 (Ella Chen)|雷深如 (J.Arie)|鶴|The Crane|范晓萱 100%乐团|周华健'
              # Release evidence alone sends these to the mainland, because a
              # stray Chinese imprint on one album outvotes the empty labels on
              # all the others. They are Taiwanese acts, so the roster wins.
              '|魏如萱|林志炫|Terry Lin|任賢齊|任贤齐|Richie Jen|江美琪'
              '|張志宇|zhangzhiyu|蘇有朋|Alec Su|等一下就回家|黃韻玲|Kay Huang|好妹妹|REGENT 林暐竣'
              '|Veegee徐若僑|徐暐翔|Vash Hsu|游鴻明|言承旭|Jerry Yan|邱鋒澤|Feng Ze|陳零九|飛兒樂團|高爾宣 OSN'
              '|王俊傑|Wang Jun-Jie|袁成傑|郭冠廷|麋先生|鼓鼓 呂思緯|地獄旅行團|地狱旅行团|五月天 怪獸'
              '|阿信|孫伯綸|孙浠伦|八三夭劉逼|徐怀钰|张栋梁|鄧寓君（等什麼君）|品冠|蕭秉治|健康合作社'
              # 'Various Artists' is a compilation alias, not a performer, so no
              # release evidence can speak for it: its 139 releases split across
              # Verve (34), WM Taiwan (18), AsiaMuse (8) and five other markets,
              # which leaves no majority and used to park it wherever the label
              # rules happened to tilt. The owner keeps it on the Taiwan shelf.
              '|Various Artists'
              # Names confirmed by a second source in 2026-10: the singer was
              # stored under a romanisation or an English name, and the
              # shelf could not place them until the real name was found.
              '|Eileen Yo游宇潼|小球(莊鵑瑛)|庾澄慶|文章|曾昱嘉|李千娜|李宣榕|林亭翰|林芯儀'
              '|王宏恩|盧栗莉|知更|答案商店|米利亞|魚丁糸|鳳小岳|黃玠瑋|謝沛恩|戴佩妮'
              '|張惠春|李亞明|激膚樂團|羅嘉豪|羅時豐|范曉萱|蔡秋鳳|迪克牛仔|邰正宵|郭采潔'
              '|鄭智化|陈华|陳妍希|陳宇祥|韋禮安'
              '|JUD 陳泳希|人力飞行剧团|凤小岳|卢栗莉|吳汶芳|呂士軒|周華健|小球(庄鹃瑛)|庾澄庆'
              '|张惠春|曾靜玟|朱俐靜|李亚明|李唯楓|李玖哲|林芯仪|林隆璇|激肤乐团|熊貓堂ProducePandas'
              '|王力宏|罗时丰|艾薇|范晓萱|莉莉周她说 Lily Chou-Chou Lied|蔡秋鳳|蘇芮|许光汉|许哲珮'
              '|谢沛恩|貝克小姐Miss Bac.|郑智化|郭采洁|郭靜|陈妍希|陈宇祥|陈绮贞 (Cheer Chen)'
              '|陳彥允|陳華|韦礼安|黄玠玮|蔡秋凤|J HALL 這好|吳霏|李玉璽'
              '|J HALL 这好|JUD 陈泳希|吕士轩|吴汶芳|吴霏|曾静玟|朱俐静|李唯枫|李玉玺|梁心颐'
              '|熊猫堂ProducePandas|苏芮|贝克小姐Miss Bac.|郭静|陈彦允',
    'japan': '玉置浩二|Koji Tamaki|安全地帯|安全地带|Anzen Chitai|宇多田ヒカル|Hikaru Utada|中島美嘉|中岛美嘉|Mika Nakashima|浜崎あゆみ|Ayumi Hamasaki|米津玄師|Kenshi Yonezu|藤井風|Fujii Kaze|YOASOBI|Ado|Official髭男dism|高瀬統也|Toya Takase|TakaseToya|坂本龍一|坂本龙一|Ryuichi Sakamoto|波多野裕介|MARiA (美依礼芽)',
    'korea': 'BIGBANG|BIGBANG (빅뱅)|BTS|防弹少年团|防彈少年團|BLACKPINK|TWICE|NAYEON|IU|아이유|李知恩|G-DRAGON|태양|TAEYANG|NewJeans|aespa|LE SSERAFIM|IVE|Red Velvet|少女时代|少女時代|Girls’ Generation|Girls\' Generation|SHINee|EXO|2NE1|SEVENTEEN',
}
_WEST_NAMES = '萊比錫布商大廈管弦樂團|Gewandhausorchester Leipzig|Gewandhausorchester'
# These carry no usable imprint at all (their releases are self-released, so
# every album row is label-less) and would otherwise sit in Other forever.
_WEST_NAMES += ('|Taylor Swift|Michael Jackson|Sia|Lana Del Rey|Rihanna|Ana Caram'
                '|Janine Jansen|Dominique Fils-Aimé|Dominique Fils-Aim|Sander Van Doorn'
                '|Laura Fygi')
# Third pass: self-released international acts, overwhelmingly classical
# soloists, chamber ensembles and indie labels with no imprint on any release.
_WEST_NAMES += ('|Ariana Grande|Travis Scott|AYLIVA|Daniela Andrade|Geza Anda|Livingston'
                '|Belcea Quartet|Jackson 5|Lewis Capaldi|Miriam Prandi|Renaud Capuçon|Julius Asal'
                '|Vesa Lassila|The Persuasions|Daniel Blumenthal|Dire Straits|María Dueñas|Mø'
                '|Olivia Rodrigo|Hélène Grimaud|Pablo de Pedro Cano|Kari Lövaas|Leo Brouwer'
                '|Alexandra Dariescu|Amy Harman|Andrè Schuen|Justin Lee|Katharina Konradi'
                '|Magdalena Hoffmann|Max Richter|Tom Harrell|Wiener Philharmoniker|Emily D\'Angelo'
                '|John Powell|Leonkoro Quartet|Lorne Balfe|Nils Lofgren Band|The Silk Road Ensemble'
                '|Ukrainian Freedom Orchestra|CupcakKe|Dean Lewis|Bryan Adams|Macy Gray'
                '|Meridian Studio Ensemble|Oscars Motettkör|ROSALÍA|Tarmo Peltokoski|Hermann Prey'
                '|Eldbjørg Hemsing|Katie Bray|Matchbook Romance|Naomi Sharon|Olivia Belli'
                '|Tamsin Waley-Cohen|Carole Dawn Reinhart|James Newton Howard|Steven Isserlis'
                '|Tame Impala|Anne Bisson|Calidore String Quartet|Echo Collective|Fleetwood Mac'
                '|Gothenburg Symphony Orchestra|Madison Beer|Mary Colucci|Thibault Cauvin'
                '|Alena Baeva|Alexa Tarantino|Anette Askvik|Billy Cobham|LIGHTS|Lisa Batiashvili'
                '|Pleyel Quartett Köln|Les Siècles|Gabríel Ólafs|HAUSER|Steely Dan|Zero 7'
                '|Ksenija Sidorova|Víkingur Ólafsson|Toronto Symphony Orchestra|Plínio Fernandes'
                '|Avi Avital|Gianandrea Noseda|Los Angeles Philharmonic|Hannah Jane Lewis|Keith Jarrett'
                '|San Francisco Symphony|MCKENNA Grace|Alice Sara Ott|Berliner Philharmoniker'
                '|Big Red Machine|Bodysync|Christopher Tin|Greg Kurstin|Amati-Ensemble|Homero Francesch'
                '|Joep Beving|Rob Grant|Ali Gatie|Abbie Callahan|Alfons Kontarsky|Andrew Choi'
                '|Antonio Pappano|Ashley Anne|Ashley Jackson|Ashley Sienna|Ashley Walls|Austin Snell'
                '|BØRNS|Caitlyn Smith|Chaka Khan|City of Birmingham Symphony Orchestra|Colin Lee'
                '|DJ HASEBE|DJ Snake|David Sanger|Davin Kingston|Doubleheader|Dustin Lynch|EJAE'
                '|Eros Ramazzotti|Gabi Sklar|Graham Barham|Gustavo Dudamel|Gustavo Gimeno|H.E.R.'
                '|Helga Löhrer|His All Star Jazz|Honey Dijon|Huw Watkins|Iggy Pop|Itamar Golan'
                '|James Hurr|Janet Jackson|John Wilson|Kanye West|Karsten Sollors|Keri-Lynn Wilson'
                '|Keval Shah|Kirill Petrenko|Klaus Mäkelä|Kylie Minogue|Labrinth'
                '|Lars Wouters van den Oudenweijer|London Grammar|London Philharmonic Orchestra'
                '|London Symphony Orchestra|Lorenzo Meseguer|Mahler Chamber Orchestra|Manfred Honeck'
                '|Marcos N. Lima|Margaret Hermant|Marianne Schofield|Marie-Nicole Lemieux|Marta Gómez'
                '|Murray Grainger|Neeme Järvi|Neil Leiter|Neneh Cherry|Norwegian String Quintet'
                '|Novel Fergus|Orchestre national de Metz Grand Est|Oslo Philharmonic Orchestra'
                '|Ozgür Aydin|Paavo Järvi|Paquito D\'Rivera|Patricia Kopatchinskaja|Patrick Brasca'
                '|Paul McCartney|Paulo Londra|Peter Dixon|Peter Phillips|Phil Woods|Playboi Carti'
                '|PnB Rock|Rachael Yamagata|Rebecca Pidgeon|Riccardo Chailly'
                '|Royal Liverpool Philharmonic Orchestra|Royal Philharmonic Orchestra|Samuel Maíllo'
                '|Sara K.|Saunder Jurriaans|Siedah Garrett|Sim C|Skrillex|Slash'
                '|Soy La Ley Afro-Cuban Jazz Band|The Avener|The Bangas|The Broadcasters|The Civil Wars'
                '|The Jacksons|The Masters|The Robots|The Serpent|The Tantrums|The Weeknd|Tom Morello'
                '|Tom Poster|Torleif Thedéen|Toronto Mendelssohn Choir|Vadym Kholodenko|Valerie Joyce'
                '|Vladimir Jurowski|William Vann|Wreckx-N-Effect|YEBBA|Yannick Nézet-Séguin'
                '|50 Cent|A Boogie Wit da Hoodie|A$AP Rocky|Anderson .Paak|Andraé Crouch'
                '|Andrew Staples|Andris Nelsons|Arjan Dhillon|Artem Konstantinov|Arthur Marco'
                '|B.o.B|BBC Philharmonic|Badi Assad|Bamberger Symphoniker|Barry Wordsworth|Belinda'
                '|Bianca Costa|BloodPop®|Bon Iver|Boris Brovtsyn|Brendan Walter|Brendon Urie'
                '|Casey Abrams|Cedric Gervais|CeeLo Green|Chance the Rapper|Channel Tres'
                '|Christy Baron|Chromeo|Clams Casino|Cord Allman'
                '|Cypress Hill|Dabin|Dan Tepfer|Daniel Harding|Daniel Heide|David Chesky'
                '|David Reiland|Debbie Harry|Dixie Chicks|Domingo Hindoyan|Dot Major|Edmundo Cassis'
                '|Ella Mai|Endel|Ensemble Pygmalion|Erik Werba|Esa-Pekka Salonen'
                '|Estonian Festival Orchestra|Father John Misty|Flo Milli|Flowsik'
                '|François-Xavier Roth|Fred Hersch|Gentle Bones|Gernot Kahl|Gordon Flanders'
                '|Gravagerz|Heavy D|Itamar Golan|J Hus|Karsten Sollors|Livingston Taylor|Meek Mill'
                '|Phat|PulseArc|Raphaël Pichon|Rhys Rutherford|SAAY|Sixtus|Slapshat'
                '|Soul Secret Agency|Springhead|Stef Aranas|Synapson|The English Concert'
                '|The Tallis Scholars|Theodore Platt|Tiffany Stringer|Timmy McKeever'
                '|Tudor Iustin Andrei')
_NORTH_NAMES = 'ITZY|李承铉'
_NORTH_NAMES += ('|Minnie|ATEEZ|EVERGLOW|JEON SOMI|PSY.P|Hera Hyesang Park|Seong-Jin Cho'
                 '|YUJU|Gunho|Lee Seung Chul|Lim Jae Beum|MOON haewon|Eric Nam|Daesung'
                 '|T.O.P|GD X TAEYANG|Doyi Lee|KiiiKiii|Billlie')
_JP_NAMES = ('|Kajiura Yuki|梶浦由記|Joe Hisaishi|久石譲|Kinokoteikoku|Ken Ara'
             '|Nobuyuki Tsujii|Midori|Tokyo String Quartet|Yugo')

_CURATED = {
    _key(name): region
    for region, names in list(_NAMES.items()) + [('west', _WEST_NAMES), ('korea', _NORTH_NAMES),
                                            ('japan', _JP_NAMES)]
    for name in names.split('|')
}

# Ordered narrow-to-broad: the first matching region wins for a release, and a
# release still only carries a region after the majority test in classify().
# Every imprint here is registered in the market it names (checked against the
# Wikipedia label directories), or was confirmed by the library's own artists
# that this facet already resolves correctly. No pattern may swallow a rival
# market, and the existing Han guard in classify() keeps Western imprints from
# claiming an artist whose name is written in Chinese.
_LABELS = (
    ('korea', r'\bkorea\b|\byg entertainment\b|\bsm entertainment\b|\bjyp\b|\bhybe\b|\bkakao\b'
              r'|\bdreamus\b|\bp nation\b|\bgenie music\b'),
    ('japan', r'\bjapan\b|\bnippon\b|\bsony music labels\b|\bavex\b|日本'
              r'|\bthe first take\b|\bin real life music\b|\baniplex\b'),
    ('hongkong', r'hong\s*kong|\bcinepoly\b|\bgo east\b|英皇|新藝寶|新艺宝|正東|正东'
                 r'|\beast asia (?:record|music)\b|\bwow music\b|\bmok-a-bye\b'
                 r'|\bmedia asia\b|\bcapital artists\b|\bthe voice entertainment\b'
                 r'|\buniversal music ltd\b'),
    ('taiwan', r'\btaiwan\b|\btaipei\b|\bhim\b|\blinfair\b|滾石|滚石|相信音樂|相信音乐|飛碟|飞碟|豐華|丰华'
               r'|\basiamuse\b|亞神|亚神|\bevosound\b|任意門|任意门'
               r'|旭瑞|新禧未來|新禧未来|光影歲月|光影岁月'),
    ('mainland', r'\bchina\b|\bbeijing\b|\bshanghai\b|太合|摩登天空|北京|上海|中國唱片|中国唱片'
                 r'|莫非音乐|莫非音樂|雨林音乐|雨林音樂|百納娛樂|百纳娱乐|秀动发行'
                 r'|塗鴉文化|涂鸦文化|獨立發行|独立发行|第四音乐|第四音樂'
                 r'|坚诚文化|堅誠文化|新坚诚文化|新堅誠文化'),
    # Classical imprints are mostly Western, but Sony Classical also carries
    # Chinese pianists (Lang Lang), so cross-region cases go through the roster
    # instead of a rule here.
    ('west', r'\b(?:columbia|atlantic|capitol|republic|interscope|warner records|rca records'
            r'|decca|deutsche grammophon|epic|verve|sony classical|warner classics|signum records'
            r'|island records|bmg rights|parlophone|mercury records|universal records'
            r'|lakeshore|geffen|walt disney records)\b'),
)


def classify(names, releases):
    # The first name is the row's display name: the spelling the library
    # settled on after the aliases were gathered. It settles the shelf on
    # its own, because an alias can be a name another artist also uses
    # (陳光榮 records as "Justin Lee", and so does a Western musician), and
    # a tie between the display name and such an alias would push a singer
    # the roster can place back into Other.
    if names:
        display = _CURATED.get(_key(names[0]))
        if display:
            return display
    curated = {_CURATED[_key(name)] for name in names if _key(name) in _CURATED}
    if len(curated) == 1:
        return curated.pop()
    if len(curated) > 1:
        return 'unknown'
    # Script is only decisive for kana and hangul, never Han or Latin alone.
    text = ' '.join(names)
    if re.search('[\u3040-\u30ff]', text):
        return 'japan'
    if re.search('[\uac00-\ud7af]', text):
        return 'korea'
    votes = Counter()
    for label, genre in releases:
        genre = (genre or '').casefold()
        label = unicodedata.normalize('NFKC', label or '').casefold()
        if re.search(r'k[ -]?pop|corée|韓国|韩国', genre):
            votes['korea'] += 1
        elif re.search(r'j[ -]?pop|japon|日本', genre):
            votes['japan'] += 1
        else:
            for region, pattern in _LABELS:
                if re.search(pattern, label):
                    # Western imprints also license Asian artists.
                    if region != 'west' or not re.search('[\u3400-\u9fff]', text):
                        votes[region] += 1
                    break
    if votes:
        winner, count = votes.most_common(1)[0]
        if count > votes.total() / 2:
            return winner
    return 'unknown'


def artist_region_map(db):
    names = {row['id']: [row['name']] for row in db.execute('SELECT id,name FROM artists')}
    for row in db.execute('SELECT artist_id,name FROM artist_aliases'):
        if row['artist_id'] in names:
            names[row['artist_id']].append(row['name'])
    releases = defaultdict(list)
    for row in db.execute('SELECT artist_id,label,genre FROM albums WHERE artist_id IS NOT NULL'):
        releases[row['artist_id']].append((row['label'], row['genre']))
    return {id: classify(aliases, releases[id]) for id, aliases in names.items()}
