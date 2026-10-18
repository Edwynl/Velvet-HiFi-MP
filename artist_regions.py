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
    'mainland': '许巍|許巍|朴树|樸樹|李健|毛不易|周深|薛之谦|薛之謙|汪峰|崔健|窦唯|竇唯|许嵩|許嵩|暗杠|陈粒|陳粒|赵雷|趙雷|李荣浩|李榮浩|张靓颖|張靚穎|张碧晨|張碧晨',
    'hongkong': '邓紫棋|鄧紫棋|G.E.M.|陈奕迅|陳奕迅|Eason Chan|张学友|張學友|Jacky Cheung|刘德华|劉德華|Andy Lau|张国荣|張國榮|Leslie Cheung|王菲|Faye Wong|Beyond|陈慧娴|陳慧嫻|Priscilla Chan|李克勤|Hacken Lee|容祖儿|容祖兒|Joey Yung|梅艳芳|梅艷芳|Anita Mui|谭咏麟|譚詠麟|Alan Tam|林忆莲|林憶蓮|Sandy Lam|陈柏宇|陳柏宇|Jason Chan|石詠莉|石咏莉|Sukie S',
    'taiwan': '周杰伦|周杰倫|Jay Chou|蔡依林|Jolin Tsai|张惠妹|張惠妹|A-Mei|五月天|Mayday|陶喆|David Tao|邓丽君|鄧麗君|Teresa Teng|张雨生|張雨生|张宇|張宇|伍佰|罗大佑|羅大佑|李宗盛|刘若英|劉若英|Rene Liu|苏打绿|蘇打綠|Sodagreen|鱼丁糸|魚丁糸|Oaeen|戴爱玲|戴愛玲|Ailing Dai|Princess Ai|棉花糖|katncandix2|蔡琴|田馥甄|Hebe Tien',
    'japan': '玉置浩二|Koji Tamaki|安全地帯|安全地带|Anzen Chitai|宇多田ヒカル|Hikaru Utada|中島美嘉|中岛美嘉|Mika Nakashima|浜崎あゆみ|Ayumi Hamasaki|米津玄師|Kenshi Yonezu|藤井風|Fujii Kaze|YOASOBI|Ado|Official髭男dism|高瀬統也|Toya Takase|TakaseToya|坂本龍一|坂本龙一|Ryuichi Sakamoto',
    'korea': 'BIGBANG|BIGBANG (빅뱅)|BTS|防弹少年团|防彈少年團|BLACKPINK|TWICE|NAYEON|IU|아이유|李知恩|G-DRAGON|태양|TAEYANG|NewJeans|aespa|LE SSERAFIM|IVE|Red Velvet|少女时代|少女時代|Girls’ Generation|Girls\' Generation|SHINee|EXO|2NE1|SEVENTEEN',
}
_CURATED = {_key(name): region for region, names in _NAMES.items() for name in names.split('|')}

_LABELS = (
    ('korea', r'\bkorea\b|\byg entertainment\b|\bsm entertainment\b|\bjyp\b|\bhybe\b|\bkakao\b|\bdreamus\b'),
    ('japan', r'\bjapan\b|\bnippon\b|\bsony music labels\b|\bavex\b|日本'),
    ('hongkong', r'hong\s*kong|\bcinepoly\b|\bgo east\b|英皇|新藝寶|新艺宝|正東|正东'),
    ('taiwan', r'\btaiwan\b|\btaipei\b|\bhim international\b|\blinfair\b|滾石|滚石|相信音樂|相信音乐|飛碟|飞碟|豐華|丰华'),
    ('mainland', r'\bchina\b|\bbeijing\b|\bshanghai\b|太合|摩登天空|北京|上海|中國唱片|中国唱片'),
    ('west', r'\b(?:columbia|atlantic|capitol|republic|interscope|warner records|rca records|decca|deutsche grammophon)\b'),
)


def classify(names, releases):
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
