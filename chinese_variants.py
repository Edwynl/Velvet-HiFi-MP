"""Traditional/Simplified Chinese conversion shared by search and indexing."""

# ─── Traditional/Simplified Chinese Conversion ─────────────────────────────────
# Mapping table for common Traditional to Simplified Chinese characters
# Used to improve search matching across Chinese text variants

_T2S_MAP = {
    '傑': '杰', '倫': '伦', '華': '华', '國': '国', '愛': '爱',
    '藝': '艺', '術': '术', '團': '团', '幣': '币', '時': '时',
    '習': '习', '書': '书', '開': '开', '見': '见', '們': '们',
    '會': '会', '學': '学', '東': '东', '車': '车', '電': '电',
    '網': '网', '離': '离', '雲': '云', '區': '区', '號': '号',
    '種': '种', '樣': '样', '義': '义', '經': '经', '織': '织',
    '萬': '万', '與': '与', '節': '节', '備': '备', '質': '质',
    '體': '体', '機': '机', '處': '处', '關': '关', '陳': '陈',
    '盡': '尽', '導': '导', '歲': '岁', '廠': '厂', '廣': '广',
    '庫': '库', '廳': '厅', '據': '据', '線': '线', '極': '极',
    '協': '协', '產': '产', '電': '电', '電': '电', '觀': '观',
    '論': '论', '資': '资', '際': '际', '證': '证', '試': '试',
    '認': '认', '讓': '让', '遠': '远', '適': '适', '選': '选',
    '錢': '钱', '錯': '错', '長': '长', '門': '门', '間': '间',
    '陸': '陆', '陽': '阳', '難': '难', '雜': '杂', '雖': '虽',
    '離': '离', '電': '电', '靈': '灵', '韓': '韩', '風': '风',
    '飛': '飞', '養': '养', '餘': '余', '餓': '饿', '館': '馆',
    '馬': '马', '驕': '骄', '驗': '验', '髮': '发', '魯': '鲁',
    '麵': '面', '黃': '黄', '黑': '黑', '齊': '齐', '齡': '龄',
    '龍': '龙', '龜': '龟', '點': '点', '麗': '丽', '麥': '麦',
    '鳥': '鸟', '鳴': '鸣', '麵': '面', '鹽': '盐', '麥': '麦',
    '屬': '属', '鄉': '乡', '錶': '表', '鏟': '铲', '鏡': '镜',
    '鐵': '铁', '鏟': '铲', '鋼': '钢', '錄': '录', '錐': '锥',
    '鍋': '锅', '鍵': '键', '鋪': '铺', '鏈': '链', '銷': '销',
    '錯': '错', '錘': '锤', '鍾': '钟', '鏟': '铲', '鏟': '铲',
    '鏟': '铲', '鏟': '铲',
}

_S2T_MAP = {v: k for k, v in _T2S_MAP.items()}

def to_simplified(text: str) -> str:
    """Convert Traditional Chinese text to Simplified Chinese."""
    if not text:
        return text
    return ''.join(_T2S_MAP.get(c, c) for c in text)

def to_traditional(text: str) -> str:
    """Convert Simplified Chinese text to Traditional Chinese."""
    if not text:
        return text
    return ''.join(_S2T_MAP.get(c, c) for c in text)

def get_all_variants(text: str) -> list[str]:
    """Get all variants (simplified and traditional) of a search term."""
    if not text:
        return [text]
    variants = [text]
    simplified = to_simplified(text)
    traditional = to_traditional(text)
    if simplified != text and simplified not in variants:
        variants.append(simplified)
    if traditional != text and traditional not in variants:
        variants.append(traditional)
    return variants
