"""A transparent editorial familiarity tier, not a live popularity chart."""
import unicodedata


def _key(name):
    return ''.join(unicodedata.normalize('NFKC', name or '').casefold().split())


_FAMILIAR = frozenset(_key(name) for name in (
    '周杰倫', '周杰伦', 'Jay Chou', '陳奕迅', '陈奕迅', 'Eason Chan',
    '張學友', '张学友', 'Jacky Cheung', '劉德華', '刘德华', 'Andy Lau',
    '張國榮', '张国荣', 'Leslie Cheung', '王菲', 'Faye Wong',
    '林俊傑', '林俊杰', 'JJ Lin', '孫燕姿', '孙燕姿', 'Stefanie Sun',
    '蔡依林', 'Jolin Tsai', '梁靜茹', '梁静茹', 'Fish Leong',
    '五月天', 'Mayday', 'Beyond', '鄧麗君', '邓丽君', 'Teresa Teng',
    '張惠妹', '张惠妹', 'A-Mei', '陶喆', 'David Tao', '王力宏', 'Leehom Wang',
    '莫文蔚', 'Karen Mok', '李宗盛', 'Jonathan Lee', '羅大佑', '罗大佑', 'Lo Ta-yu',
    '李克勤', 'Hacken Lee', '譚詠麟', '谭咏麟', 'Alan Tam',
    '許冠傑', '许冠杰', 'Sam Hui', '梅艷芳', '梅艳芳', 'Anita Mui',
    '林憶蓮', '林忆莲', 'Sandy Lam', '容祖兒', '容祖儿', 'Joey Yung',
    '楊千嬅', '杨千嬅', 'Miriam Yeung', '鄭秀文', '郑秀文', 'Sammi Cheng',
    '謝霆鋒', '谢霆锋', 'Nicholas Tse', '古巨基', 'Leo Ku',
    '陳慧琳', '陈慧琳', 'Kelly Chen', '黎明', 'Leon Lai', '郭富城', 'Aaron Kwok',
    '張敬軒', '张敬轩', 'Hins Cheung', '鄧紫棋', '邓紫棋', 'G.E.M.', 'GEM',
    '薛之謙', '薛之谦', 'Joker Xue', '李榮浩', '李荣浩', 'Ronghao Li',
    '張韶涵', '张韶涵', 'Angela Chang', '田馥甄', 'Hebe Tien', 'S.H.E',
    '蘇打綠', '苏打绿', 'Sodagreen', '伍佰', 'Wu Bai', '張震嶽', '张震岳',
    '任賢齊', '任贤齐', 'Richie Jen', '周華健', '周华健', 'Wakin Chau',
    '宇多田ヒカル', 'Hikaru Utada', 'Utada Hikaru', '浜崎あゆみ', 'Ayumi Hamasaki',
    '安室奈美恵', 'Namie Amuro', '中島美嘉', 'Mika Nakashima', 'MISIA',
    '椎名林檎', 'Sheena Ringo', '松任谷由実', '山下達郎', 'Tatsuro Yamashita',
    '竹内まりや', 'Mariya Takeuchi', '玉置浩二', '安全地帯', '中森明菜',
    '松田聖子', 'Seiko Matsuda', '米津玄師', 'Kenshi Yonezu', '藤井風', 'Fujii Kaze',
    'YOASOBI', 'Ado', 'Aimer', 'Official HIGE DANdism', 'Mr.Children', 'B’z',
    'BTS', '防弾少年団', '방탄소년단', 'BLACKPINK', 'TWICE', 'IU', '아이유',
    'BIGBANG', 'G-Dragon', '少女時代', 'Girls’ Generation', "Girls' Generation",
    'EXO', 'SHINee', 'Red Velvet', 'NewJeans', 'SEVENTEEN', 'Stray Kids',
    'aespa', 'IVE', 'LE SSERAFIM', 'BoA', '東方神起', 'TVXQ', '2NE1',
))
_COLLECTIVE = frozenset(_key(name) for name in (
    'Various Artists', 'Various Artist', 'Various', 'VA', 'V.A.',
    '群星', '合辑', '合輯', 'Unknown', 'Unknown Artist', '未知歌手',
))


def artist_familiarity(name):
    key = _key(name)
    return -1 if key in _COLLECTIVE else int(key in _FAMILIAR)
