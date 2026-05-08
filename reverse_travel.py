from __future__ import annotations

import datetime as dt
import copy
import hashlib
import html
import json
import os
import random
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode, urljoin, urlparse, parse_qs
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from holiday_helper import HolidayCalendar, HolidayCalendarError, HolidayRange

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
)
HOTEL_LIST_LIMIT = 120
QUERY_PROFILE = "tri_state_feature_filters_verified_features_area_cache_v27"
CACHE_DIR = Path(__file__).resolve().parent / ".cache"
SEARCH_CACHE_TTL_SECONDS = 24 * 60 * 60
CITY_CACHE_TTL_SECONDS = 30 * 24 * 60 * 60
DEFAULT_LIST_FILTERS = "29~1*29*1~2*2,17~1*17*1*2,80~0~1*80*0*2"
ADVANCED_YES_FILTERS = ("16~4*16*4*4", "16~5*16*5*5")
ADVANCED_NO_FILTERS = ("16~2*16*2*≤2", "16~3*16*3*3")
POOL_YES_FILTERS = ("3~605*3*605*Pool",)
CHILD_FACILITY_YES_FILTERS = ("3~68*3*68*Playground",)
TRI_STATE_VALUES = {"all", "yes", "no"}
MAX_COMPARE_WINDOWS = 8
MAX_SCROLL_ROUNDS = 16
SCROLL_WAIT_MS = 2200
STABLE_SCROLL_ROUNDS = 6
COMPARE_PAGE_BATCH_SIZE = 4
CHINESE_NAME_WORKERS = 8
FEATURE_VERIFY_WORKERS = 8
BROWSER_SESSION_LIMIT = 2
SUPPLEMENT_MIN_CHOICES = 8
SUPPLEMENT_HOTEL_LIST_LIMIT = 40
MAX_SUPPLEMENT_KEYWORD_CANDIDATES = 2
CITY_SUPPLEMENT_KEYWORDS = {
    "广州": (
        "增城凯悦酒店",
        "增城酒店",
        "琶洲酒店",
    ),
    "深圳": (
        "国际会展中心皇冠假日酒店",
        "国际会展中心洲际酒店",
        "光明美爵酒店",
    ),
    "东莞": (
        "松山湖酒店",
        "厚街会展酒店",
    ),
    "惠州": (
        "惠阳酒店",
        "博罗酒店",
        "仲恺酒店",
    ),
    "中山": (
        "石岐酒店",
        "东区酒店",
        "小榄酒店",
    ),
    "江门": (
        "新会酒店",
        "鹤山酒店",
        "台山酒店",
    ),
    "河源": (
        "万绿湖酒店",
        "巴伐利亚庄园酒店",
        "客天下酒店",
    ),
    "肇庆": (
        "七星岩酒店",
        "鼎湖山酒店",
        "高要酒店",
    ),
    "珠海": (
        "横琴长隆酒店",
        "情侣路酒店",
        "金湾酒店",
    ),
    "韶关": (
        "丹霞山酒店",
        "南华寺酒店",
        "乳源酒店",
    ),
    "汕尾": (
        "金町湾酒店",
        "红海湾酒店",
        "海丰酒店",
    ),
}
CITY_DEFAULT_AREA_NAMES = {
    "深圳": ("深圳国际会展中心片区", "光明虹桥公园片区", "深圳观澜片区", "深圳南山片区"),
    "广州": ("广州增城片区", "广州琶洲会展片区", "广州天河片区", "广州黄埔片区"),
    "东莞": ("东莞松山湖片区", "东莞厚街会展片区", "东莞虎门片区", "东莞东城片区"),
    "惠州": ("惠州惠阳片区", "惠州惠东片区", "惠州博罗片区", "惠州仲恺片区"),
    "中山": ("中山石岐片区", "中山东区片区", "中山小榄片区", "中山古镇片区", "中山三乡片区"),
    "江门": ("江门新会片区", "江门鹤山片区", "江门台山片区", "江门开平赤坎片区", "江门恩平温泉片区"),
    "河源": ("河源万绿湖片区", "河源巴伐利亚庄园片区", "河源源城片区", "河源客天下片区", "河源和平温泉片区"),
    "肇庆": ("肇庆七星岩星湖片区", "肇庆鼎湖山片区", "肇庆端州片区", "肇庆高要片区", "肇庆四会片区"),
    "珠海": ("珠海横琴长隆片区", "珠海情侣路香洲片区", "珠海拱北口岸片区", "珠海金湾航空新城片区", "珠海唐家湾片区"),
    "韶关": ("韶关丹霞山片区", "韶关南华寺曹溪片区", "韶关乳源大峡谷片区", "韶关市区片区", "韶关南雄片区"),
    "汕尾": ("汕尾金町湾片区", "汕尾红海湾片区", "汕尾海丰片区", "汕尾陆丰片区", "汕尾市区片区"),
    "曼谷": ("曼谷素坤逸片区", "曼谷暹罗片区", "曼谷湄南河畔片区", "曼谷是隆沙吞片区", "曼谷水门片区"),
    "吉隆坡": ("吉隆坡武吉免登片区", "吉隆坡城中城片区", "吉隆坡中央车站片区", "吉隆坡谷中城片区", "吉隆坡白沙罗片区"),
    "芝加哥": ("芝加哥卢普片区", "芝加哥河北片区", "芝加哥壮丽大道片区", "芝加哥奥黑尔机场片区", "芝加哥橡树溪片区"),
    "巴黎": ("巴黎蒙马特片区", "巴黎拉德芳斯片区", "巴黎歌剧院片区", "巴黎香榭丽舍片区", "巴黎埃菲尔铁塔片区"),
    "伦敦": ("伦敦西区片区", "伦敦市中心片区", "伦敦国王十字片区", "伦敦金丝雀码头片区", "伦敦希思罗机场片区"),
    "东京": ("东京新宿片区", "东京银座片区", "东京上野浅草片区", "东京涩谷片区", "东京东京站片区"),
    "大阪": ("大阪心斋桥难波片区", "大阪梅田片区", "大阪环球影城片区", "大阪天王寺片区", "大阪关西机场片区"),
    "首尔": ("首尔明洞片区", "首尔弘大片区", "首尔江南片区", "首尔东大门片区", "首尔仁寺洞片区"),
    "新加坡": ("新加坡乌节路片区", "新加坡滨海湾片区", "新加坡圣淘沙片区", "新加坡牛车水片区", "新加坡樟宜机场片区"),
    "纽约": ("纽约时代广场片区", "纽约中城片区", "纽约下城金融区片区", "纽约布鲁克林片区", "纽约中央公园片区"),
    "洛杉矶": ("洛杉矶好莱坞片区", "洛杉矶市中心片区", "洛杉矶圣莫尼卡片区", "洛杉矶国际机场片区", "洛杉矶比佛利山片区"),
    "旧金山": ("旧金山联合广场片区", "旧金山渔人码头片区", "旧金山金融区片区", "旧金山机场片区", "旧金山南湾片区"),
    "悉尼": ("悉尼达令港片区", "悉尼环形码头片区", "悉尼中央商务区片区", "悉尼邦迪海滩片区", "悉尼机场片区"),
    "墨尔本": ("墨尔本中央商务区片区", "墨尔本南岸片区", "墨尔本圣基尔达片区", "墨尔本卡尔顿片区", "墨尔本机场片区"),
    "迪拜": ("迪拜市中心片区", "迪拜码头片区", "迪拜棕榈岛片区", "迪拜德拉片区", "迪拜机场片区"),
}
GLOBAL_CITY_ALIASES = {
    "shanghai": "上海",
    "上海": "上海",
    "suzhou": "苏州",
    "苏州": "苏州",
    "蘇州": "苏州",
    "sao paulo": "圣保罗",
    "são paulo": "圣保罗",
    "saopaulo": "圣保罗",
    "圣保罗": "圣保罗",
    "聖保羅": "圣保罗",
    "moscow": "莫斯科",
    "moskva": "莫斯科",
    "莫斯科": "莫斯科",
    "jakarta": "雅加达",
    "雅加达": "雅加达",
    "雅加達": "雅加达",
    "bangkok": "曼谷",
    "曼谷": "曼谷",
    "kuala lumpur": "吉隆坡",
    "吉隆坡": "吉隆坡",
    "chicago": "芝加哥",
    "芝加哥": "芝加哥",
    "paris": "巴黎",
    "巴黎": "巴黎",
    "london": "伦敦",
    "伦敦": "伦敦",
    "tokyo": "东京",
    "东京": "东京",
    "東京": "东京",
    "osaka": "大阪",
    "大阪": "大阪",
    "seoul": "首尔",
    "首尔": "首尔",
    "首爾": "首尔",
    "singapore": "新加坡",
    "新加坡": "新加坡",
    "hong kong": "香港",
    "hongkong": "香港",
    "香港": "香港",
    "las vegas": "拉斯维加斯",
    "拉斯维加斯": "拉斯维加斯",
    "拉斯維加斯": "拉斯维加斯",
    "new york": "纽约",
    "纽约": "纽约",
    "紐約": "纽约",
    "los angeles": "洛杉矶",
    "洛杉矶": "洛杉矶",
    "洛杉磯": "洛杉矶",
    "san francisco": "旧金山",
    "旧金山": "旧金山",
    "舊金山": "旧金山",
    "sydney": "悉尼",
    "悉尼": "悉尼",
    "melbourne": "墨尔本",
    "墨尔本": "墨尔本",
    "dubai": "迪拜",
    "迪拜": "迪拜",
    "rome": "罗马",
    "罗马": "罗马",
    "羅馬": "罗马",
    "barcelona": "巴塞罗那",
    "巴塞罗那": "巴塞罗那",
    "巴塞隆拿": "巴塞罗那",
    "madrid": "马德里",
    "马德里": "马德里",
    "馬德里": "马德里",
    "amsterdam": "阿姆斯特丹",
    "阿姆斯特丹": "阿姆斯特丹",
    "berlin": "柏林",
    "柏林": "柏林",
    "munich": "慕尼黑",
    "慕尼黑": "慕尼黑",
    "frankfurt": "法兰克福",
    "法兰克福": "法兰克福",
    "法蘭克福": "法兰克福",
    "zurich": "苏黎世",
    "苏黎世": "苏黎世",
    "蘇黎世": "苏黎世",
    "milan": "米兰",
    "米兰": "米兰",
    "米蘭": "米兰",
    "venice": "威尼斯",
    "威尼斯": "威尼斯",
    "istanbul": "伊斯坦布尔",
    "伊斯坦布尔": "伊斯坦布尔",
    "伊斯坦堡": "伊斯坦布尔",
    "phuket": "普吉岛",
    "普吉岛": "普吉岛",
    "普吉島": "普吉岛",
    "bali": "巴厘岛",
    "巴厘岛": "巴厘岛",
    "峇里岛": "巴厘岛",
    "峇里島": "巴厘岛",
    "hanoi": "河内",
    "河内": "河内",
    "河內": "河内",
    "ho chi minh": "胡志明市",
    "ho chi minh city": "胡志明市",
    "胡志明市": "胡志明市",
    "chiang mai": "清迈",
    "清迈": "清迈",
    "清邁": "清迈",
}
GLOBAL_CITY_STRIP_TOKENS: dict[str, set[str]] = {}
for _alias, _label in GLOBAL_CITY_ALIASES.items():
    GLOBAL_CITY_STRIP_TOKENS.setdefault(_label, set()).update({_alias, _label})
GLOBAL_AREA_PATTERNS = {
    "曼谷": [
        (("sukhumvit", "asok", "phrom phong", "emquartier", "emsphere", "素坤逸"), "曼谷素坤逸片区"),
        (("pratunam", "ratchaprarop", "水门", "水門"), "曼谷水门片区"),
        (("siam", "central world", "mbk", "暹罗", "暹羅"), "曼谷暹罗片区"),
        (("riverside", "chao phraya", "asiatique", "charoen krung", "湄南河", "河畔"), "曼谷湄南河畔片区"),
        (("silom", "sathorn", "surawong", "surawongse", "patpong", "是隆", "沙吞"), "曼谷是隆沙吞片区"),
        (("chidlom", "ploenchit", "wireless road", "奇隆"), "曼谷齐隆片区"),
        (("ratchada", "ratchadaphisek", "huai khwang"), "曼谷拉差达片区"),
        (("suvarnabhumi", "bkk airport", "素万那普", "素萬那普"), "曼谷素万那普机场片区"),
        (("old town", "chinatown", "yaowarat", "唐人街", "老城"), "曼谷老城唐人街片区"),
    ],
    "吉隆坡": [
        (("bukit bintang", "pavilion kuala lumpur", "武吉免登"), "吉隆坡武吉免登片区"),
        (("klcc", "petronas", "suria klcc", "双子塔", "雙子塔"), "吉隆坡城中城片区"),
        (("kl sentral", "kuala lumpur sentral", "central station", "中央车站", "中央車站"), "吉隆坡中央车站片区"),
        (("mid valley", "the gardens", "谷中城"), "吉隆坡谷中城片区"),
        (("petaling jaya", "八打灵", "八打靈"), "八打灵再也片区"),
        (("damansara", "白沙罗", "白沙羅"), "吉隆坡白沙罗片区"),
        (("bangsar", "孟沙"), "吉隆坡孟沙片区"),
        (("chow kit", "秋杰"), "吉隆坡秋杰片区"),
        (("chinatown", "china town", "petaling street", "茨厂街", "唐人街"), "吉隆坡唐人街片区"),
        (("cheras", "maluri", "陈秀莲", "陳秀蓮"), "吉隆坡蕉赖片区"),
    ],
    "芝加哥": [
        (("loop", "downtown/loop", "downtown chicago", "millennium park", "theater district"), "芝加哥卢普片区"),
        (("river north", "north river", "河北", "河畔北"), "芝加哥河北片区"),
        (("magnificent mile", "mag mile", "壮丽大道", "壯麗大道"), "芝加哥壮丽大道片区"),
        (("o'hare", "ohare", "ord", "奥黑尔", "奧黑爾"), "芝加哥奥黑尔机场片区"),
        (("oak brook", "oakbrook", "橡树溪", "橡樹溪"), "芝加哥橡树溪片区"),
        (("west loop", "fulton market"), "芝加哥西卢普片区"),
        (("lincoln park", "林肯公园", "林肯公園"), "芝加哥林肯公园片区"),
        (("navy pier", "海军码头", "海軍碼頭"), "芝加哥海军码头片区"),
    ],
    "巴黎": [
        (("montmartre", "sacre coeur", "sacré-coeur", "蒙马特", "蒙馬特", "圣心", "聖心"), "巴黎蒙马特片区"),
        (("la defense", "la défense", "defense", "défense", "拉德芳斯"), "巴黎拉德芳斯片区"),
        (("opera", "opéra", "galeries lafayette", "歌剧院", "歌劇院"), "巴黎歌剧院片区"),
        (("champs-elysees", "champs-élysées", "elysees", "香榭丽舍", "香榭麗舍"), "巴黎香榭丽舍片区"),
        (("eiffel", "tour eiffel", "埃菲尔", "艾菲尔", "鐵塔"), "巴黎埃菲尔铁塔片区"),
        (("latin quarter", "quartier latin", "拉丁区", "拉丁區"), "巴黎拉丁区片区"),
        (("saint germain", "saint-germain", "圣日耳曼", "聖日耳曼"), "巴黎圣日耳曼片区"),
        (("montparnasse", "蒙帕纳斯", "蒙帕納斯"), "巴黎蒙帕纳斯片区"),
        (("creteil", "créteil", "克雷泰尔", "克雷泰爾"), "巴黎克雷泰尔片区"),
        (("suresnes", "叙雷纳", "敍雷納"), "巴黎叙雷纳片区"),
        (("louvre", "卢浮宫", "羅浮宮"), "巴黎卢浮宫片区"),
    ],
    "伦敦": [
        (("west end", "soho", "covent garden", "leicester square", "西区", "蘇豪", "科文特花园"), "伦敦西区片区"),
        (("city centre", "city center", "central london", "downtown"), "伦敦市中心片区"),
        (("king's cross", "kings cross", "st pancras", "国王十字", "國王十字"), "伦敦国王十字片区"),
        (("canary wharf", "docklands", "金丝雀码头", "金絲雀碼頭"), "伦敦金丝雀码头片区"),
        (("heathrow", "lhr", "希思罗", "希斯路"), "伦敦希思罗机场片区"),
        (("paddington", "帕丁顿", "柏灵顿"), "伦敦帕丁顿片区"),
        (("kensington", "切尔西", "切爾西"), "伦敦肯辛顿切尔西片区"),
    ],
    "东京": [
        (("shinjuku", "新宿"), "东京新宿片区"),
        (("ginza", "银座", "銀座"), "东京银座片区"),
        (("ueno", "asakusa", "上野", "浅草", "淺草"), "东京上野浅草片区"),
        (("shibuya", "涩谷", "澀谷"), "东京涩谷片区"),
        (("tokyo station", "marunouchi", "东京站", "東京站", "丸之内", "丸之內"), "东京东京站片区"),
        (("odaiba", "台场", "台場"), "东京台场片区"),
        (("haneda", "羽田"), "东京羽田机场片区"),
    ],
    "大阪": [
        (("shinsaibashi", "namba", "dotonbori", "心斋桥", "心齋橋", "难波", "難波", "道顿堀", "道頓堀"), "大阪心斋桥难波片区"),
        (("umeda", "梅田"), "大阪梅田片区"),
        (("universal studios", "usj", "环球影城", "環球影城"), "大阪环球影城片区"),
        (("tennoji", "天王寺"), "大阪天王寺片区"),
        (("kansai airport", "kix", "关西机场", "關西機場"), "大阪关西机场片区"),
    ],
    "首尔": [
        (("myeongdong", "明洞"), "首尔明洞片区"),
        (("hongdae", "弘大"), "首尔弘大片区"),
        (("gangnam", "江南"), "首尔江南片区"),
        (("dongdaemun", "东大门", "東大門"), "首尔东大门片区"),
        (("insadong", "仁寺洞"), "首尔仁寺洞片区"),
        (("itaewon", "梨泰院"), "首尔梨泰院片区"),
    ],
    "新加坡": [
        (("orchard", "乌节", "烏節"), "新加坡乌节路片区"),
        (("marina bay", "滨海湾", "濱海灣"), "新加坡滨海湾片区"),
        (("sentosa", "圣淘沙", "聖淘沙"), "新加坡圣淘沙片区"),
        (("chinatown", "牛车水", "牛車水", "唐人街"), "新加坡牛车水片区"),
        (("changi", "樟宜"), "新加坡樟宜机场片区"),
        (("bugis", "武吉士"), "新加坡武吉士片区"),
        (("little india", "小印度"), "新加坡小印度片区"),
    ],
    "纽约": [
        (("times square", "时代广场", "時代廣場"), "纽约时代广场片区"),
        (("midtown", "曼哈顿中城", "曼哈頓中城"), "纽约中城片区"),
        (("financial district", "wall street", "downtown manhattan", "金融区", "金融區", "华尔街", "華爾街"), "纽约下城金融区片区"),
        (("brooklyn", "布鲁克林", "布魯克林"), "纽约布鲁克林片区"),
        (("central park", "中央公园", "中央公園"), "纽约中央公园片区"),
        (("jfk", "kennedy airport", "肯尼迪"), "纽约肯尼迪机场片区"),
    ],
    "洛杉矶": [
        (("hollywood", "好莱坞", "荷里活"), "洛杉矶好莱坞片区"),
        (("downtown", "dtla", "市中心"), "洛杉矶市中心片区"),
        (("santa monica", "圣莫尼卡", "聖莫尼卡"), "洛杉矶圣莫尼卡片区"),
        (("lax", "los angeles international airport", "洛杉矶国际机场", "洛杉磯國際機場"), "洛杉矶国际机场片区"),
        (("beverly hills", "比佛利", "比弗利"), "洛杉矶比佛利山片区"),
    ],
    "旧金山": [
        (("union square", "联合广场", "聯合廣場"), "旧金山联合广场片区"),
        (("fisherman's wharf", "fishermans wharf", "渔人码头", "漁人碼頭"), "旧金山渔人码头片区"),
        (("financial district", "金融区", "金融區"), "旧金山金融区片区"),
        (("sfo", "san francisco airport", "旧金山机场", "舊金山機場"), "旧金山机场片区"),
        (("south san francisco", "南旧金山", "南舊金山"), "旧金山南湾片区"),
    ],
    "悉尼": [
        (("darling harbour", "darling harbor", "达令港", "達令港"), "悉尼达令港片区"),
        (("circular quay", "the rocks", "环形码头", "環形碼頭", "岩石区"), "悉尼环形码头片区"),
        (("cbd", "city centre", "city center", "市中心"), "悉尼中央商务区片区"),
        (("bondi", "邦迪"), "悉尼邦迪海滩片区"),
        (("sydney airport", "syd", "悉尼机场", "悉尼機場"), "悉尼机场片区"),
    ],
    "墨尔本": [
        (("cbd", "city centre", "city center", "市中心"), "墨尔本中央商务区片区"),
        (("southbank", "south bank", "南岸"), "墨尔本南岸片区"),
        (("st kilda", "saint kilda", "圣基尔达", "聖基爾達"), "墨尔本圣基尔达片区"),
        (("carlton", "卡尔顿", "卡爾頓"), "墨尔本卡尔顿片区"),
        (("melbourne airport", "tullamarine", "墨尔本机场", "墨爾本機場"), "墨尔本机场片区"),
    ],
    "迪拜": [
        (("downtown dubai", "burj khalifa", "dubai mall", "哈利法塔", "迪拜购物中心", "迪拜購物中心"), "迪拜市中心片区"),
        (("dubai marina", "jbr", "迪拜码头", "迪拜碼頭"), "迪拜码头片区"),
        (("palm jumeirah", "朱美拉棕榈", "朱美拉棕櫚", "棕榈岛", "棕櫚島"), "迪拜棕榈岛片区"),
        (("deira", "德拉"), "迪拜德拉片区"),
        (("dubai airport", "dxb", "迪拜机场", "迪拜機場"), "迪拜机场片区"),
    ],
}
AREA_NAME_REPLACEMENTS = {
    "深圳福田中心区": "深圳福田中心片区",
    "深圳福田中心区片区": "深圳福田中心片区",
    "吉隆坡KLCC片区": "吉隆坡城中城片区",
    "芝加哥Loop片区": "芝加哥卢普片区",
    "芝加哥西Loop片区": "芝加哥西卢普片区",
    "悉尼CBD片区": "悉尼中央商务区片区",
    "墨尔本CBD片区": "墨尔本中央商务区片区",
    "罗马Trastevere片区": "罗马特拉斯提弗列片区",
}
AREA_CANDIDATE_TRANSLATIONS = {
    "klcc": "城中城",
    "kuala lumpur city centre": "城中城",
    "kuala lumpur city center": "城中城",
    "petronas twin towers": "城中城",
    "bukit bintang": "武吉免登",
    "pavilion kuala lumpur": "武吉免登",
    "kl sentral": "中央车站",
    "kuala lumpur sentral": "中央车站",
    "mid valley city": "谷中城",
    "mid valley": "谷中城",
    "china town": "唐人街",
    "chinatown": "唐人街",
    "petaling street": "唐人街",
    "damansara": "白沙罗",
    "mutiara damansara": "白沙罗",
    "petaling jaya": "八打灵再也",
    "pj state": "八打灵再也",
    "bangsar south": "孟沙南",
    "bangsar": "孟沙",
    "chow kit": "秋杰",
    "golden triangle": "金三角",
    "pudu": "富都",
    "cheras": "蕉赖",
    "chan sow lin": "蕉赖",
    "sudirman": "苏迪曼",
    "senayan": "史纳延",
    "kuningan": "库宁安",
    "mega kuningan": "库宁安",
    "thamrin": "坦林",
    "gajah mada": "加查马达",
    "kemayoran": "马腰兰",
    "pantai indah kapuk": "潘泰因达卡普克",
    "pik avenue": "潘泰因达卡普克",
    "pondok indah": "蓬多克英达",
    "kemang": "克芒",
    "blok m": "布洛克艾姆",
    "central park": "中央公园",
    "monas": "独立广场",
    "south jakarta": "雅加达南区",
    "central jakarta": "雅加达中区",
    "west jakarta": "雅加达西区",
    "north jakarta": "雅加达北区",
    "loop": "卢普",
    "west loop": "西卢普",
    "cbd": "中央商务区",
    "downtown": "市中心",
    "city centre": "市中心",
    "city center": "市中心",
    "people's square": "人民广场",
    "people square": "人民广场",
    "jing'an": "静安寺",
    "jing an": "静安寺",
    "pudong": "浦东",
    "lujiazui": "陆家嘴",
    "xujiahui": "徐家汇",
    "the bund": "外滩",
    "bund": "外滩",
    "nanjing road": "南京路",
    "suzhou bay": "苏州湾",
    "jinji lake": "金鸡湖",
    "gusu district": "姑苏",
    "high-tech zone": "高新区",
    "sip": "工业园区",
    "suzhou industrial park": "工业园区",
    "dushu lake": "独墅湖",
    "guanqian street": "观前街",
    "shantang street": "山塘街",
    "pingjiang road": "平江路",
    "trastevere": "特拉斯提弗列",
    "fulton market": "富尔顿市场",
    "river north": "河北",
    "magnificent mile": "壮丽大道",
    "midtown": "中城",
    "times square": "时代广场",
    "financial district": "金融区",
    "central business district": "中央商务区",
}
AREA_CITY_CANDIDATE_TRANSLATIONS = {
    "圣保罗": {
        "avenida paulista": "保利斯塔",
        "paulista avenue": "保利斯塔",
        "paulista": "保利斯塔",
        "jardins": "雅尔丁斯",
        "itaim bibi": "伊泰姆比比",
        "vila mariana": "维拉马里亚纳",
        "pinheiros": "皮涅罗斯",
        "morumbi": "莫伦比",
    },
    "莫斯科": {
        "moscow city": "莫斯科城",
        "tverskoy": "特维尔",
        "tverskaya": "特维尔",
        "arbat": "阿尔巴特",
        "presnensky": "普列斯年斯基",
        "zamoskvorechye": "扎莫斯克沃列奇耶",
        "red square": "红场",
        "kremlin": "克里姆林宫",
    },
    "迪拜": {
        "downtown dubai": "市中心",
        "dubai marina": "码头",
        "palm jumeirah": "朱美拉棕榈岛",
        "deira": "德拉",
        "jumeirah beach": "朱美拉海滩",
        "bur dubai": "布尔迪拜",
    },
    "柏林": {
        "mitte": "米特",
        "alexanderplatz": "亚历山大广场",
        "charlottenburg": "夏洛滕堡",
        "kurfurstendamm": "选帝侯大街",
        "kurfürstendamm": "选帝侯大街",
        "potsdamer platz": "波茨坦广场",
        "kreuzberg": "克罗伊茨贝格",
        "friedrichshain": "腓特烈斯海恩",
    },
    "新加坡": {
        "marina bay": "滨海湾",
        "orchard road": "乌节路",
        "orchard": "乌节路",
        "sentosa": "圣淘沙",
        "chinatown": "牛车水",
        "bugis": "武吉士",
        "little india": "小印度",
    },
    "香港": {
        "tsim sha tsui": "尖沙咀",
        "central": "中环",
        "causeway bay": "铜锣湾",
        "mong kok": "旺角",
        "admiralty": "金钟",
        "wan chai": "湾仔",
        "shatin": "沙田",
        "sha tin": "沙田",
        "hong kong disneyland": "迪士尼",
        "disneyland": "迪士尼",
    },
    "澳门": {
        "cotai": "路氹",
        "macau peninsula": "澳门半岛",
        "macao peninsula": "澳门半岛",
        "taipa": "氹仔",
        "coloane": "路环",
        "senado square": "议事亭前地",
        "ruins of st. paul": "大三巴",
        "ruins of saint paul": "大三巴",
    },
    "拉斯维加斯": {
        "las vegas strip": "拉斯维加斯大道",
        "the strip": "拉斯维加斯大道",
        "strip": "拉斯维加斯大道",
        "downtown las vegas": "市中心",
        "fremont street": "弗里蒙特街",
        "summerlin": "萨默林",
        "henderson": "亨德森",
        "convention center": "会展中心",
    },
}
CITY_ID_LABELS = {
    "31": "珠海",
    "59": "澳门",
    "251": "佛山",
    "553": "中山",
    "3933": "云浮",
}
CITY_LABEL_KEYWORDS = {
    "深圳": ("shenzhen", "深圳"),
    "广州": ("guangzhou", "广州", "廣州"),
    "东莞": ("dongguan", "东莞", "東莞"),
    "中山": ("zhongshan", "中山"),
    "惠州": ("huizhou", "惠州", "boluo", "博罗", "博羅", "huidong", "惠东", "惠東", "huiyang", "惠阳", "惠陽", "longmen", "龙门", "龍門", "双月湾", "雙月灣", "巽寮", "巽寮湾", "巽寮灣"),
    "江门": ("jiangmen", "江门", "江門", "xinhui", "新会", "新會", "heshan", "鹤山", "鶴山", "taishan", "台山", "kaiping", "开平", "開平", "enping", "恩平"),
    "河源": ("heyuan", "河源", "源城", "东源", "東源", "龙川", "龍川", "紫金", "连平", "連平", "和平", "万绿湖", "萬綠湖"),
    "肇庆": ("zhaoqing", "肇庆", "肇慶", "端州", "鼎湖", "高要", "四会", "四會", "七星岩", "七星巖", "星湖"),
    "珠海": ("zhuhai", "珠海", "香洲", "横琴", "橫琴", "长隆", "長隆", "拱北", "金湾", "金灣", "唐家湾", "唐家灣", "斗门", "斗門"),
    "韶关": ("shaoguan", "韶关", "韶關", "丹霞山", "南华寺", "南華寺", "乳源", "南雄", "乐昌", "樂昌", "曲江", "浈江", "湞江", "武江", "翁源"),
    "汕尾": ("shanwei", "汕尾", "海丰", "海豐", "陆丰", "陸豐", "陆河", "陸河", "红海湾", "紅海灣", "金町湾", "金町灣", "深汕"),
    "佛山": ("foshan", "佛山", "shunde", "顺德", "順德", "jiujiang", "九江"),
    "云浮": ("yunfu", "云浮", "雲浮", "云安", "雲安", "罗定", "羅定", "新兴", "新興", "郁南"),
    "澳门": ("macau", "macao", "澳门", "澳門"),
    "清远": ("qingyuan", "清远", "清遠", "英德", "连州", "連州", "佛冈", "佛岡"),
    "郴州": ("chenzhou", "郴州", "宜章", "汝城"),
    "赣州": ("ganzhou", "赣州", "贛州", "大余", "崇义", "崇義"),
}


class ReverseTravelFinderError(RuntimeError):
    pass


@dataclass
class CityCandidate:
    city_id: int
    city_name: str
    province_id: int
    country_id: int
    lat: float
    lon: float
    filter_id: str
    search_coordinate: str


@dataclass(frozen=True)
class HotelKeywordCandidate:
    hotel_id: str
    title: str
    filter_id: str
    lat: float
    lon: float
    search_coordinate: str


@dataclass(frozen=True)
class FeatureFilters:
    advanced: str = "all"
    pool: str = "all"
    child_facility: str = "all"

    def cache_parts(self) -> tuple[str, str, str]:
        return (self.advanced, self.pool, self.child_facility)

    def to_response(self) -> dict[str, dict[str, str]]:
        return {
            "advanced": {
                "value": self.advanced,
                "label": self._label(self.advanced),
                "name": "高级酒店（四钻/四星级以上）",
            },
            "pool": {
                "value": self.pool,
                "label": self._label(self.pool),
                "name": "游泳池",
            },
            "child_facility": {
                "value": self.child_facility,
                "label": self._label(self.child_facility),
                "name": "儿童设施",
            },
        }

    @staticmethod
    def _label(value: str) -> str:
        return {"all": "全部", "yes": "是", "no": "否"}.get(value, "全部")


class ReverseTravelFinder:
    def __init__(
        self,
        calendar: HolidayCalendar,
        cache_dir: str | Path | None = None,
        search_cache_ttl_seconds: int = SEARCH_CACHE_TTL_SECONDS,
    ) -> None:
        self.calendar = calendar
        self.cache_dir = Path(cache_dir) if cache_dir is not None else CACHE_DIR
        self.search_cache_ttl_seconds = search_cache_ttl_seconds
        self._browser_semaphore = threading.BoundedSemaphore(BROWSER_SESSION_LIMIT)
        self._cache_lock = threading.Lock()
        self._search_cache: dict[tuple[str, ...], dict[str, Any]] = {}
        self._search_cache_meta: dict[tuple[str, ...], dict[str, Any]] = {}
        self._city_cache: dict[str, dict[str, Any]] = self._load_cache_items(self._city_cache_path())
        self._hotel_name_cache: dict[str, dict[str, str]] = self._load_cache_items(self._hotel_name_cache_path())
        self._hotel_feature_cache: dict[str, dict[str, Any]] = self._load_cache_items(self._hotel_feature_cache_path())
        self.geonames_username = os.environ.get("GEONAMES_USERNAME", "").strip()
        self._geonames_area_cache: dict[tuple[float, float, str], str] = {}

    def list_holidays(self) -> list[dict[str, Any]]:
        holidays = self.calendar.get_upcoming_holidays()
        return [
            {
                "code": item.code,
                "name": item.name,
                "start": item.start.isoformat(),
                "end": item.end.isoformat(),
                "days": item.days,
                "label": f"{item.name} {item.start.isoformat()} 至 {item.end.isoformat()} ({item.days}天)",
            }
            for item in holidays
        ]

    def _city_cache_path(self) -> Path:
        return self.cache_dir / "city_cache.json"

    def _hotel_name_cache_path(self) -> Path:
        return self.cache_dir / "hotel_name_cache.json"

    def _hotel_feature_cache_path(self) -> Path:
        return self.cache_dir / "hotel_feature_cache.json"

    def _search_cache_path(self, cache_key: tuple[str, ...]) -> Path:
        raw_key = json.dumps(cache_key, ensure_ascii=False, separators=(",", ":"))
        digest = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
        return self.cache_dir / "search" / f"{digest}.json"

    def _read_json_file(self, path: Path) -> Any:
        try:
            with path.open("r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, json.JSONDecodeError):
            return None

    def _write_json_file(self, path: Path, data: Any) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
            with tmp_path.open("w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, separators=(",", ":"))
            tmp_path.replace(path)
        except OSError:
            return

    def _load_cache_items(self, path: Path) -> dict[str, Any]:
        data = self._read_json_file(path)
        if not isinstance(data, dict):
            return {}
        items = data.get("items")
        return items if isinstance(items, dict) else {}

    def _save_city_cache(self) -> None:
        with self._cache_lock:
            items = copy.deepcopy(self._city_cache)
        self._write_json_file(self._city_cache_path(), {"version": 1, "items": items})

    def _save_hotel_name_cache(self) -> None:
        with self._cache_lock:
            items = copy.deepcopy(self._hotel_name_cache)
        self._write_json_file(self._hotel_name_cache_path(), {"version": 1, "items": items})

    def _save_hotel_feature_cache(self) -> None:
        with self._cache_lock:
            items = copy.deepcopy(self._hotel_feature_cache)
        self._write_json_file(self._hotel_feature_cache_path(), {"version": 1, "items": items})

    def _is_cache_meta_fresh(self, meta: dict[str, Any] | None, ttl_seconds: int) -> bool:
        if ttl_seconds <= 0 or not meta:
            return False
        try:
            created_at = float(meta.get("created_at") or 0)
        except (TypeError, ValueError):
            return False
        return created_at > 0 and time.time() - created_at <= ttl_seconds

    def _load_search_cache(self, cache_key: tuple[str, ...]) -> dict[str, Any] | None:
        if self.search_cache_ttl_seconds <= 0:
            return None
        record = self._read_json_file(self._search_cache_path(cache_key))
        if not isinstance(record, dict) or record.get("cache_key") != list(cache_key):
            return None
        try:
            created_at = float(record.get("created_at") or 0)
        except (TypeError, ValueError):
            return None
        if not self._is_cache_meta_fresh({"created_at": created_at}, self.search_cache_ttl_seconds):
            return None
        result = record.get("result")
        if not isinstance(result, dict):
            return None
        return {"created_at": created_at, "result": result}

    def _store_search_cache(self, cache_key: tuple[str, ...], base_result: dict[str, Any], created_at: float) -> None:
        if self.search_cache_ttl_seconds <= 0:
            return
        record = {
            "version": 1,
            "cache_key": list(cache_key),
            "created_at": created_at,
            "created_at_text": self._format_timestamp(created_at),
            "ttl_seconds": self.search_cache_ttl_seconds,
            "result": copy.deepcopy(base_result),
        }
        self._write_json_file(self._search_cache_path(cache_key), record)

    def _build_cache_info(self, source: str, created_at: float, hit: bool) -> dict[str, Any]:
        age_seconds = max(0, round(time.time() - created_at))
        expires_at = created_at + self.search_cache_ttl_seconds
        return {
            "hit": hit,
            "source": source,
            "source_label": {"live": "实时查询", "memory": "内存缓存", "disk": "本地缓存"}.get(source, source),
            "created_at": self._format_timestamp(created_at),
            "age_seconds": age_seconds,
            "ttl_seconds": self.search_cache_ttl_seconds,
            "expires_at": self._format_timestamp(expires_at),
        }

    def _format_timestamp(self, timestamp: float) -> str:
        try:
            return dt.datetime.fromtimestamp(timestamp).astimezone().isoformat(timespec="seconds")
        except (OSError, OverflowError, ValueError):
            return ""

    def _load_cached_city_candidate(self, cache_key: str) -> CityCandidate | None:
        if not cache_key:
            return None
        with self._cache_lock:
            record = copy.deepcopy(self._city_cache.get(cache_key))
        if not isinstance(record, dict):
            return None
        if not self._is_cache_meta_fresh(record, CITY_CACHE_TTL_SECONDS):
            return None
        candidate = record.get("candidate")
        if not isinstance(candidate, dict):
            return None
        try:
            return CityCandidate(**candidate)
        except (TypeError, ValueError):
            return None

    def _store_city_candidate(self, cache_key: str, candidate: CityCandidate) -> None:
        if not cache_key:
            return
        with self._cache_lock:
            self._city_cache[cache_key] = {
                "created_at": time.time(),
                "candidate": candidate.__dict__.copy(),
            }
        self._save_city_cache()

    def find_choices(
        self,
        city: str,
        holiday_code: str,
        min_price: int | None,
        max_price: int | None,
        advanced_filter: str | None = "all",
        pool_filter: str | None = "all",
        child_facility_filter: str | None = "all",
        use_cache: bool = True,
        cache_only: bool = False,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        feature_filters = self._normalize_feature_filters(
            advanced_filter=advanced_filter,
            pool_filter=pool_filter,
            child_facility_filter=child_facility_filter,
        )
        cache_key = (QUERY_PROFILE, city.strip().lower(), holiday_code, *feature_filters.cache_parts())
        cache_info: dict[str, Any] | None = None
        with self._cache_lock:
            cached = self._search_cache.get(cache_key)
            cached_meta = self._search_cache_meta.get(cache_key)
            if cached is not None and not self._is_cache_meta_fresh(cached_meta, self.search_cache_ttl_seconds):
                self._search_cache.pop(cache_key, None)
                self._search_cache_meta.pop(cache_key, None)
                cached = None
                cached_meta = None
        if not use_cache:
            base_result = self._call_find_choices_base(
                city=city,
                holiday_code=holiday_code,
                feature_filters=feature_filters,
                progress_callback=progress_callback,
            )
            created_at = time.time()
            with self._cache_lock:
                self._search_cache[cache_key] = copy.deepcopy(base_result)
                self._search_cache_meta[cache_key] = {"created_at": created_at}
            self._store_search_cache(cache_key, base_result, created_at)
            cache_info = self._build_cache_info(source="live", created_at=created_at, hit=False)
        elif cached is None:
            disk_record = self._load_search_cache(cache_key)
            if disk_record is not None:
                base_result = copy.deepcopy(disk_record["result"])
                cache_info = self._build_cache_info(
                    source="disk",
                    created_at=float(disk_record["created_at"]),
                    hit=True,
                )
                with self._cache_lock:
                    self._search_cache[cache_key] = copy.deepcopy(base_result)
                    self._search_cache_meta[cache_key] = {
                        "created_at": float(disk_record["created_at"]),
                    }
            else:
                base_result = self._call_find_choices_base(
                    city=city,
                    holiday_code=holiday_code,
                    feature_filters=feature_filters,
                    progress_callback=progress_callback,
                )
                created_at = time.time()
                with self._cache_lock:
                    self._search_cache[cache_key] = copy.deepcopy(base_result)
                    self._search_cache_meta[cache_key] = {"created_at": created_at}
                self._store_search_cache(cache_key, base_result, created_at)
                cache_info = self._build_cache_info(source="live", created_at=created_at, hit=False)
        else:
            base_result = copy.deepcopy(cached)
            cache_info = self._build_cache_info(
                source="memory",
                created_at=float((cached_meta or {}).get("created_at") or time.time()),
                hit=True,
            )

        filtered_choices: list[dict[str, Any]] = []
        for hotel in base_result["choices"]:
            if min_price is not None and hotel["holiday_avg_nightly_tax_total_value"] < min_price:
                continue
            if max_price is not None and hotel["holiday_avg_nightly_tax_total_value"] > max_price:
                continue
            filtered_choices.append(hotel)

        base_result["price_filter"] = {"min_price": min_price, "max_price": max_price}
        base_result["feature_filters"] = feature_filters.to_response()
        self._refresh_choice_area_names(filtered_choices, base_result["city"])
        base_result["choices"] = filtered_choices
        base_result["area_recommendations"] = self._build_area_recommendations(filtered_choices, base_result["city"])
        base_result["cache"] = cache_info
        return base_result

    def _call_find_choices_base(
        self,
        city: str,
        holiday_code: str,
        feature_filters: FeatureFilters,
        progress_callback: Callable[[dict[str, Any]], None] | None,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "city": city,
            "holiday_code": holiday_code,
            "feature_filters": feature_filters,
        }
        if progress_callback is not None:
            kwargs["progress_callback"] = progress_callback
        return self._find_choices_base(**kwargs)

    def _emit_progress(
        self,
        progress_callback: Callable[[dict[str, Any]], None] | None,
        message: str,
        stage: str,
        **extra: Any,
    ) -> None:
        if progress_callback is None:
            return
        payload = {"stage": stage, "message": message}
        payload.update(extra)
        progress_callback(payload)

    def _find_choices_base(
        self,
        city: str,
        holiday_code: str,
        feature_filters: FeatureFilters,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        self._emit_progress(progress_callback, "正在准备节假日和对比日期...", "prepare", percent=5)
        holiday = self._get_holiday(holiday_code)
        compare_windows = self._build_compare_windows(holiday)
        if not compare_windows:
            raise ReverseTravelFinderError("未来一个月内没有可比较的非法定假期时间段。")

        self._emit_progress(progress_callback, "正在识别城市和 Trip.com 搜索范围...", "resolve_city", percent=10)
        city_candidate = self._resolve_city(city)

        with self._browser_semaphore:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(
                    headless=True,
                    args=["--disable-blink-features=AutomationControlled"],
                )
                context = browser.new_context(
                    user_agent=UA,
                    locale="zh-CN",
                    timezone_id="Asia/Shanghai",
                    viewport={"width": 1440, "height": 1400},
                    service_workers="block",
                )
                context.route("**/*", self._route_lightweight_resources)
                context.add_init_script(
                    """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3] });
                    """
                )
                page = context.new_page()
                try:
                    page.goto("https://www.trip.com/hotels", wait_until="commit", timeout=15000)
                    page.wait_for_timeout(1200)
                except PlaywrightTimeoutError:
                    pass

                self._emit_progress(
                    progress_callback,
                    f"正在抓取{city_candidate.city_name}假期酒店列表...",
                    "holiday_hotels",
                    percent=18,
                )
                holiday_hotels = self._fetch_hotel_list(
                    city_candidate=city_candidate,
                    check_in=holiday.start,
                    check_out=holiday.check_out,
                    limit=HOTEL_LIST_LIMIT,
                    page=page,
                    feature_filters=feature_filters,
                )

                if not holiday_hotels:
                    browser.close()
                    raise ReverseTravelFinderError("没有抓到该城市在假期时段的酒店列表。")

                self._emit_progress(
                    progress_callback,
                    f"已抓到假期酒店 {len(holiday_hotels)} 家，正在抓取 {len(compare_windows)} 个平日代表时段...",
                    "comparison_hotels",
                    percent=36,
                    hotel_count=len(holiday_hotels),
                    comparison_total=len(compare_windows),
                )
                page.close()
                comparison_hotels = self._fetch_hotel_lists_parallel(
                    city_candidate=city_candidate,
                    windows=compare_windows,
                    limit=HOTEL_LIST_LIMIT,
                    context=context,
                    feature_filters=feature_filters,
                )

                comparison_map = self._build_comparison_map(comparison_hotels, compare_windows, holiday.days)
                choices = self._build_choices_from_hotels(city_candidate, holiday, holiday_hotels, comparison_map)
                if len(choices) < SUPPLEMENT_MIN_CHOICES:
                    self._emit_progress(
                        progress_callback,
                        "正在补充重点片区酒店，避免漏掉符合条件的酒店...",
                        "supplemental_hotels",
                        percent=62,
                        choice_count=len(choices),
                    )
                    supplemental_holiday_hotels, supplemental_comparison_hotels = self._fetch_supplemental_hotel_lists(
                        city_query=city,
                        city_candidate=city_candidate,
                        holiday=holiday,
                        compare_windows=compare_windows,
                        context=context,
                        feature_filters=feature_filters,
                    )
                    if supplemental_holiday_hotels:
                        holiday_hotels = self._merge_hotel_lists(holiday_hotels, supplemental_holiday_hotels)
                        for key, hotels in supplemental_comparison_hotels.items():
                            comparison_hotels[key] = self._merge_hotel_lists(
                                comparison_hotels.get(key, []),
                                hotels,
                            )
                        comparison_map = self._build_comparison_map(comparison_hotels, compare_windows, holiday.days)
                        choices = self._build_choices_from_hotels(
                            city_candidate,
                            holiday,
                            holiday_hotels,
                            comparison_map,
                        )
                browser.close()

        choices.sort(key=lambda item: (item["price_diff_nightly"], item["holiday_avg_nightly_tax_total_value"]))
        self._emit_progress(
            progress_callback,
            f"已完成价格对比，正在核验 {len(choices)} 家候选酒店设施...",
            "verify_features",
            percent=78,
            choice_count=len(choices),
        )
        choices = self._filter_choices_by_verified_features(choices, feature_filters)
        self._emit_progress(
            progress_callback,
            f"设施核验后保留 {len(choices)} 家，正在补全中文酒店名...",
            "chinese_names",
            percent=88,
            choice_count=len(choices),
        )
        self._enrich_choices_with_chinese_hotel_names(choices)
        self._emit_progress(progress_callback, "正在整理推荐区域和最终结果...", "finalize", percent=96)
        self._refresh_choice_area_names(choices, city_candidate.city_name)
        area_recommendations = self._build_area_recommendations(choices, city_candidate.city_name)

        return {
            "city": city_candidate.city_name,
            "holiday": {
                "code": holiday.code,
                "name": holiday.name,
                "check_in": holiday.start.isoformat(),
                "check_out": holiday.check_out.isoformat(),
                "days": holiday.days,
            },
            "price_filter": {"min_price": None, "max_price": None},
            "feature_filters": feature_filters.to_response(),
            "comparison_windows": [
                {
                    "check_in": item["check_in"].isoformat(),
                    "check_out": item["check_out"].isoformat(),
                }
                for item in compare_windows
            ],
            "area_recommendations": area_recommendations,
            "choices": choices,
        }

    def _build_comparison_map(
        self,
        comparison_hotels: dict[str, list[dict[str, Any]]],
        compare_windows: list[dict[str, dt.date]],
        nights: int,
    ) -> dict[str, dict[str, Any]]:
        comparison_map: dict[str, dict[str, Any]] = {}
        for window in compare_windows:
            hotels = comparison_hotels.get(window["check_in"].isoformat(), [])
            for hotel in hotels:
                room_type = self._classify_room_type(hotel["room_name"])
                if room_type == "unknown":
                    continue
                key = f"{hotel['hotel_id']}::{room_type}"
                nightly_value = self._nightly_value(hotel["tax_total_value"], nights)
                current = comparison_map.get(key)
                if not current:
                    comparison_map[key] = {
                        "hotel_id": hotel["hotel_id"],
                        "hotel_name": hotel["hotel_name"],
                        "room_type": room_type,
                        "nightly_values": [nightly_value],
                        "sample_count": 1,
                        "lowest_sample": {
                            **hotel,
                            "nightly_tax_total_value": nightly_value,
                            "window_check_in": window["check_in"].isoformat(),
                            "window_check_out": window["check_out"].isoformat(),
                        },
                    }
                    continue
                current["nightly_values"].append(nightly_value)
                current["sample_count"] += 1
                if nightly_value < current["lowest_sample"]["nightly_tax_total_value"]:
                    current["lowest_sample"] = {
                        **hotel,
                        "nightly_tax_total_value": nightly_value,
                        "window_check_in": window["check_in"].isoformat(),
                        "window_check_out": window["check_out"].isoformat(),
                    }
        return comparison_map

    def _build_choices_from_hotels(
        self,
        city_candidate: CityCandidate,
        holiday: HolidayRange,
        holiday_hotels: list[dict[str, Any]],
        comparison_map: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        choices: list[dict[str, Any]] = []
        for hotel in holiday_hotels:
            room_type = self._classify_room_type(hotel["room_name"])
            if room_type == "unknown":
                continue
            holiday_nightly_value = self._nightly_value(hotel["tax_total_value"], holiday.days)
            comparison = comparison_map.get(f"{hotel['hotel_id']}::{room_type}")
            if not comparison:
                continue
            average_nightly_value = round(sum(comparison["nightly_values"]) / comparison["sample_count"])
            diff = holiday_nightly_value - average_nightly_value
            if diff > 100:
                continue
            lowest_sample = comparison["lowest_sample"]
            detail_url = self._to_zh_detail_url(hotel["detail_url"])
            if not detail_url:
                detail_url = self._to_zh_detail_url(
                    self._build_detail_url_from_ids(
                        city_id=city_candidate.city_id,
                        hotel_id=hotel["hotel_id"],
                        check_in=holiday.start,
                        check_out=holiday.check_out,
                    )
                )

            choices.append(
                {
                    "hotel_id": hotel["hotel_id"],
                    "hotel_name": hotel["hotel_name"],
                    "hotel_original_name": hotel["hotel_name"],
                    "hotel_name_source": "",
                    "area_name": hotel.get("area_name") or self._infer_area_name(
                        city_name=city_candidate.city_name,
                        hotel_name=hotel["hotel_name"],
                        area_text=hotel.get("area_hint") or "",
                    ),
                    "area_hint": hotel.get("area_hint") or "",
                    "area_source": hotel.get("area_source") or "酒店位置",
                    "is_advanced": hotel.get("is_advanced"),
                    "has_pool": hotel.get("has_pool"),
                    "has_child_facility": hotel.get("has_child_facility"),
                    "room_type": room_type,
                    "room_type_label": self._room_type_label(room_type),
                    "holiday_room_name": self._localize_room_name(hotel["room_name"]),
                    "holiday_room_price": hotel["room_price_text"],
                    "holiday_tax_total_price": hotel["tax_total_text"],
                    "holiday_tax_total_value": hotel["tax_total_value"],
                    "holiday_avg_nightly_tax_total_price": self._format_cny(holiday_nightly_value),
                    "holiday_avg_nightly_tax_total_value": holiday_nightly_value,
                    "comparison_average_nightly_tax_total_price": self._format_cny(average_nightly_value),
                    "comparison_average_nightly_tax_total_value": average_nightly_value,
                    "comparison_sample_count": comparison["sample_count"],
                    "comparison_lowest_room_name": self._localize_room_name(lowest_sample["room_name"]),
                    "comparison_lowest_room_price": lowest_sample["room_price_text"],
                    "comparison_lowest_tax_total_price": lowest_sample["tax_total_text"],
                    "comparison_lowest_tax_total_value": lowest_sample["tax_total_value"],
                    "comparison_lowest_nightly_tax_total_price": self._format_cny(
                        lowest_sample["nightly_tax_total_value"]
                    ),
                    "comparison_lowest_nightly_tax_total_value": lowest_sample["nightly_tax_total_value"],
                    "comparison_lowest_check_in": lowest_sample["window_check_in"],
                    "comparison_lowest_check_out": lowest_sample["window_check_out"],
                    "price_diff_nightly": diff,
                    "price_diff_nightly_text": self._format_cny_diff(diff),
                    "detail_url": detail_url,
                }
            )
        return choices

    def _normalize_feature_filters(
        self,
        advanced_filter: str | None,
        pool_filter: str | None,
        child_facility_filter: str | None,
    ) -> FeatureFilters:
        return FeatureFilters(
            advanced=self._normalize_tri_state(advanced_filter, "高级酒店"),
            pool=self._normalize_tri_state(pool_filter, "游泳池"),
            child_facility=self._normalize_tri_state(child_facility_filter, "儿童设施"),
        )

    def _normalize_tri_state(self, value: str | None, field_name: str) -> str:
        normalized = (value or "all").strip().lower()
        if normalized in {"全部", "不限"}:
            normalized = "all"
        elif normalized == "是":
            normalized = "yes"
        elif normalized == "否":
            normalized = "no"
        if normalized not in TRI_STATE_VALUES:
            raise ReverseTravelFinderError(f"{field_name}筛选项只能是“是”“否”或“全部”。")
        return normalized

    def _get_holiday(self, holiday_code: str) -> HolidayRange:
        for item in self.calendar.get_upcoming_holidays():
            if item.code == holiday_code:
                return item
        raise ReverseTravelFinderError("没有找到对应的法定假期。")

    def _build_compare_windows(self, holiday: HolidayRange) -> list[dict[str, dt.date]]:
        return self._sample_compare_windows(self._build_all_compare_windows(holiday))

    def _build_all_compare_windows(self, holiday: HolidayRange) -> list[dict[str, dt.date]]:
        nights = holiday.days
        start = holiday.end + dt.timedelta(days=1)
        last_start = holiday.end + dt.timedelta(days=30)
        windows: list[dict[str, dt.date]] = []
        current = start
        while current <= last_start:
            check_out = current + dt.timedelta(days=nights)
            valid = True
            for offset in range(nights):
                if self.calendar.is_statutory_holiday(current + dt.timedelta(days=offset)):
                    valid = False
                    break
            if valid:
                windows.append({"check_in": current, "check_out": check_out})
            current += dt.timedelta(days=1)
        return windows

    def _sample_compare_windows(self, windows: list[dict[str, dt.date]]) -> list[dict[str, dt.date]]:
        if len(windows) <= MAX_COMPARE_WINDOWS:
            return windows
        weekdays = [item for item in windows if item["check_in"].weekday() < 5]
        weekends = [item for item in windows if item["check_in"].weekday() >= 5]
        sampled = self._pick_evenly(weekdays, MAX_COMPARE_WINDOWS // 2)
        sampled.extend(self._pick_evenly(weekends, MAX_COMPARE_WINDOWS - len(sampled)))
        if len(sampled) < MAX_COMPARE_WINDOWS:
            selected_dates = {item["check_in"] for item in sampled}
            remainder = [item for item in windows if item["check_in"] not in selected_dates]
            sampled.extend(self._pick_evenly(remainder, MAX_COMPARE_WINDOWS - len(sampled)))
        return sorted(sampled, key=lambda item: item["check_in"])

    def _pick_evenly(self, items: list[dict[str, dt.date]], limit: int) -> list[dict[str, dt.date]]:
        if limit <= 0 or not items:
            return []
        if len(items) <= limit:
            return items[:]
        if limit == 1:
            return [items[len(items) // 2]]
        indexes = {
            round(idx * (len(items) - 1) / (limit - 1))
            for idx in range(limit)
        }
        return [items[idx] for idx in sorted(indexes)]

    def _route_lightweight_resources(self, route) -> None:
        if route.request.resource_type in {"image", "media", "font"}:
            route.abort()
            return
        route.continue_()

    def _resolve_city(self, city: str) -> CityCandidate:
        city_cache_key = city.strip().lower()
        cached_city = self._load_cached_city_candidate(city_cache_key)
        if cached_city is not None:
            return cached_city

        trace_id = self._trace_id()
        client_id = trace_id.split("-")[0]
        pid = str(uuid.uuid4())
        payload = {
            "code": 0,
            "codeType": "",
            "keyWord": city,
            "searchType": "D",
            "scenicCode": 0,
            "cityCodeOfUser": 0,
            "searchConditions": [
                {"type": "D_PROVINCE", "value": "T"},
                {"type": "SupportNormalSearch", "value": "T"},
                {"type": "DisplayTagIcon", "value": "F"},
            ],
            "head": {
                "platform": "PC",
                "clientId": client_id,
                "bu": "ibu",
                "group": "TRIP",
                "aid": "",
                "sid": "",
                "ouid": "",
                "caid": "",
                "csid": "",
                "couid": "",
                "region": "XX",
                "locale": "en-XX",
                "timeZone": "8",
                "currency": "CNY",
                "p": str(random.randint(10_000_000_000, 19_999_999_999)),
                "pageID": "10320668150",
                "deviceID": "PC",
                "clientVersion": "0",
                "frontend": {"vid": client_id, "sessionID": "1", "pvid": "1"},
                "extension": [
                    {"name": "cityId", "value": ""},
                    {"name": "checkIn", "value": ""},
                    {"name": "checkOut", "value": ""},
                    {"name": "region", "value": "XX"},
                ],
                "tripSub1": "",
                "qid": "",
                "pid": pid,
                "hotelExtension": {},
                "cid": client_id,
                "traceLogID": uuid.uuid4().hex[:13],
                "ticket": "",
                "href": "https://www.trip.com/hotels",
            },
        }
        url = "https://www.trip.com/htls/getKeyWordSearch?" + urlencode(
            {"htl_customtraceid": uuid.uuid4().hex, "x-traceID": trace_id}
        )
        req = Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "accept": "application/json",
                "content-type": "application/json",
                "currency": "CNY",
                "locale": "en-XX",
                "p": payload["head"]["p"],
                "pid": pid,
                "referer": "https://www.trip.com/hotels",
                "trip-trace-id": trace_id,
                "user-agent": UA,
                "x-traceid": trace_id,
            },
            method="POST",
        )
        with urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        for item in data.get("keyWordSearchResults") or []:
            if item.get("resultType") != "CT":
                continue
            coords = item.get("coordinateInfos") or []
            preferred = next((x for x in coords if x.get("coordinateType") == "NORMAL"), None) or coords[0]
            candidate = CityCandidate(
                city_id=int(item["city"]["geoCode"]),
                city_name=item["city"]["currentLocaleName"],
                province_id=int(item["province"]["geoCode"]),
                country_id=int(item["country"]["geoCode"]),
                lat=float(preferred["latitude"]),
                lon=float(preferred["longitude"]),
                filter_id=((item.get("item") or {}).get("data") or {}).get("filterID") or f"19|{item['code']}",
                search_coordinate="|".join(
                    f"{x['coordinateType']}_{x['latitude']}_{x['longitude']}_{x.get('accuracy', 0)}"
                    for x in coords
                ),
            )
            self._store_city_candidate(city_cache_key, candidate)
            return candidate
        raise ReverseTravelFinderError("没有识别到这个城市。")

    def _fetch_supplemental_hotel_lists(
        self,
        city_query: str,
        city_candidate: CityCandidate,
        holiday: HolidayRange,
        compare_windows: list[dict[str, dt.date]],
        context,
        feature_filters: FeatureFilters,
    ) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
        candidates = self._resolve_hotel_keyword_candidates(city_query, city_candidate)
        holiday_hotels: list[dict[str, Any]] = []
        comparison_hotels: dict[str, list[dict[str, Any]]] = {}
        for candidate in candidates:
            page = context.new_page()
            try:
                candidate_holiday_hotels = self._fetch_hotel_list(
                    city_candidate=city_candidate,
                    check_in=holiday.start,
                    check_out=holiday.check_out,
                    limit=SUPPLEMENT_HOTEL_LIST_LIMIT,
                    page=page,
                    feature_filters=feature_filters,
                    keyword_candidate=candidate,
                )
            finally:
                try:
                    page.close()
                except Exception:
                    pass
            if not candidate_holiday_hotels:
                continue
            holiday_hotels = self._merge_hotel_lists(holiday_hotels, candidate_holiday_hotels)
            candidate_comparison_hotels = self._fetch_hotel_lists_parallel(
                city_candidate=city_candidate,
                windows=compare_windows,
                limit=SUPPLEMENT_HOTEL_LIST_LIMIT,
                context=context,
                feature_filters=feature_filters,
                keyword_candidate=candidate,
            )
            for key, hotels in candidate_comparison_hotels.items():
                comparison_hotels[key] = self._merge_hotel_lists(comparison_hotels.get(key, []), hotels)
        return holiday_hotels, comparison_hotels

    def _resolve_hotel_keyword_candidates(
        self,
        city_query: str,
        city_candidate: CityCandidate,
    ) -> list[HotelKeywordCandidate]:
        candidates: list[HotelKeywordCandidate] = []
        seen_ids: set[str] = set()
        for keyword in self._supplement_keywords(city_query, city_candidate):
            try:
                results = self._keyword_search_results(keyword)
            except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError):
                continue
            for item in results:
                candidate = self._hotel_keyword_candidate_from_result(item, city_candidate)
                if candidate is None or candidate.hotel_id in seen_ids:
                    continue
                seen_ids.add(candidate.hotel_id)
                candidates.append(candidate)
                break
            if len(candidates) >= MAX_SUPPLEMENT_KEYWORD_CANDIDATES:
                break
        return candidates

    def _supplement_keywords(self, city_query: str, city_candidate: CityCandidate) -> list[str]:
        city_label = self._normalize_city_label(city_candidate.city_name or city_query)
        seeds = CITY_SUPPLEMENT_KEYWORDS.get(city_label, ())
        if not seeds:
            return []
        prefixes = [item for item in (city_query.strip(), city_label) if item]
        keywords: list[str] = []
        seen: set[str] = set()
        for seed in seeds:
            seed = seed.strip()
            if not seed:
                continue
            keyword = seed if any(seed.startswith(prefix) for prefix in prefixes) else f"{prefixes[0]}{seed}"
            if keyword in seen:
                continue
            seen.add(keyword)
            keywords.append(keyword)
        return keywords

    def _keyword_search_results(self, keyword: str) -> list[dict[str, Any]]:
        trace_id = self._trace_id()
        client_id = trace_id.split("-")[0]
        pid = str(uuid.uuid4())
        payload = {
            "code": 0,
            "codeType": "",
            "keyWord": keyword,
            "searchType": "D",
            "scenicCode": 0,
            "cityCodeOfUser": 0,
            "searchConditions": [
                {"type": "D_PROVINCE", "value": "T"},
                {"type": "SupportNormalSearch", "value": "T"},
                {"type": "DisplayTagIcon", "value": "F"},
            ],
            "head": {
                "platform": "PC",
                "clientId": client_id,
                "bu": "ibu",
                "group": "TRIP",
                "aid": "",
                "sid": "",
                "ouid": "",
                "caid": "",
                "csid": "",
                "couid": "",
                "region": "XX",
                "locale": "en-XX",
                "timeZone": "8",
                "currency": "CNY",
                "p": str(random.randint(10_000_000_000, 19_999_999_999)),
                "pageID": "10320668150",
                "deviceID": "PC",
                "clientVersion": "0",
                "frontend": {"vid": client_id, "sessionID": "1", "pvid": "1"},
                "extension": [
                    {"name": "cityId", "value": ""},
                    {"name": "checkIn", "value": ""},
                    {"name": "checkOut", "value": ""},
                    {"name": "region", "value": "XX"},
                ],
                "tripSub1": "",
                "qid": "",
                "pid": pid,
                "hotelExtension": {},
                "cid": client_id,
                "traceLogID": uuid.uuid4().hex[:13],
                "ticket": "",
                "href": "https://www.trip.com/hotels",
            },
        }
        url = "https://www.trip.com/htls/getKeyWordSearch?" + urlencode(
            {"htl_customtraceid": uuid.uuid4().hex, "x-traceID": trace_id}
        )
        req = Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "accept": "application/json",
                "content-type": "application/json",
                "currency": "CNY",
                "locale": "en-XX",
                "p": payload["head"]["p"],
                "pid": pid,
                "referer": "https://www.trip.com/hotels",
                "trip-trace-id": trace_id,
                "user-agent": UA,
                "x-traceid": trace_id,
            },
            method="POST",
        )
        with urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        results = data.get("keyWordSearchResults") if isinstance(data, dict) else []
        return results if isinstance(results, list) else []

    def _hotel_keyword_candidate_from_result(
        self,
        item: dict[str, Any],
        city_candidate: CityCandidate,
    ) -> HotelKeywordCandidate | None:
        if item.get("resultType") != "H":
            return None
        result_city = item.get("city") or {}
        try:
            result_city_id = int(result_city.get("geoCode") or 0)
        except (TypeError, ValueError):
            result_city_id = 0
        if result_city_id and result_city_id != city_candidate.city_id:
            return None
        result_city_name = " ".join(
            str(result_city.get(key) or "")
            for key in ("currentLocaleName", "enusName")
        )
        if result_city_name and self._normalize_city_label(result_city_name) != self._normalize_city_label(city_candidate.city_name):
            return None

        item_data = ((item.get("item") or {}).get("data") or {})
        title = str(item_data.get("title") or item.get("word") or item.get("name") or "").strip()
        hotel_id = str(item_data.get("value") or item.get("code") or "").strip()
        if not title or not hotel_id:
            return None
        lat, lon, search_coordinate = self._hotel_keyword_coordinate(item, city_candidate)
        return HotelKeywordCandidate(
            hotel_id=hotel_id,
            title=title,
            filter_id=str(item_data.get("filterID") or f"31|{hotel_id}"),
            lat=lat,
            lon=lon,
            search_coordinate=search_coordinate,
        )

    def _hotel_keyword_coordinate(
        self,
        item: dict[str, Any],
        city_candidate: CityCandidate,
    ) -> tuple[float, float, str]:
        extra = ((item.get("item") or {}).get("extra") or {})
        formatted = str(extra.get("formattedCoordinateInfo") or "").strip()
        parts = formatted.split("|")
        if len(parts) >= 2:
            try:
                lat = float(parts[0])
                lon = float(parts[1])
                accuracy = parts[2] if len(parts) >= 3 else "0"
                return lat, lon, f"NORMAL_{lat}_{lon}_{accuracy}"
            except (TypeError, ValueError):
                pass
        return city_candidate.lat, city_candidate.lon, city_candidate.search_coordinate

    def _merge_hotel_lists(
        self,
        primary: list[dict[str, Any]],
        supplemental: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        for item in [*primary, *supplemental]:
            key = self._hotel_merge_key(item)
            if not key:
                continue
            current = merged.get(key)
            if current is None or self._prefer_hotel_item(item, current):
                merged[key] = item
        return list(merged.values())

    def _hotel_merge_key(self, item: dict[str, Any]) -> str:
        hotel_id = str(item.get("hotel_id") or "").strip()
        room_type = self._classify_room_type(str(item.get("room_name") or ""))
        if hotel_id and room_type != "unknown":
            return f"{hotel_id}::{room_type}"
        return hotel_id or str(item.get("detail_href") or item.get("hotel_name") or "").strip()

    def _prefer_hotel_item(self, item: dict[str, Any], current: dict[str, Any]) -> bool:
        item_value = int(item.get("tax_total_value") or 0)
        current_value = int(current.get("tax_total_value") or 0)
        if item_value and current_value and item_value != current_value:
            return item_value < current_value
        return self._hotel_item_score(item) > self._hotel_item_score(current)

    def _fetch_hotel_list(
        self,
        city_candidate: CityCandidate,
        check_in: dt.date,
        check_out: dt.date,
        limit: int,
        page,
        feature_filters: FeatureFilters,
        keyword_candidate: HotelKeywordCandidate | None = None,
    ) -> list[dict[str, Any]]:
        url = self._build_hotel_list_url(city_candidate, check_in, check_out, feature_filters, keyword_candidate)
        response_items: list[dict[str, Any]] = []
        response_lock = threading.Lock()
        nights = max(1, (check_out - check_in).days)

        def collect_response_items(response) -> None:
            items = self._extract_hotel_list_response_items(response, nights=nights)
            if not items:
                return
            with response_lock:
                response_items.extend(items)

        page.on("response", collect_response_items)
        try:
            for wait_ms in (2500, 4500):
                try:
                    try:
                        page.goto(url, wait_until="domcontentloaded", timeout=60000)
                        page.wait_for_timeout(wait_ms)
                    except PlaywrightTimeoutError:
                        continue

                    items = self._collect_scrolled_hotel_list(
                        page=page,
                        limit=limit,
                        city_candidate=city_candidate,
                        check_in=check_in,
                        check_out=check_out,
                        response_items=response_items,
                        response_lock=response_lock,
                        feature_filters=feature_filters,
                    )
                    if items:
                        return items
                finally:
                    with response_lock:
                        response_items.clear()
        finally:
            try:
                page.remove_listener("response", collect_response_items)
            except (AttributeError, ValueError):
                pass
        return []

    def _fetch_hotel_lists_parallel(
        self,
        city_candidate: CityCandidate,
        windows: list[dict[str, dt.date]],
        limit: int,
        context,
        feature_filters: FeatureFilters,
        keyword_candidate: HotelKeywordCandidate | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        results: dict[str, list[dict[str, Any]]] = {}
        for batch in self._chunked(windows, COMPARE_PAGE_BATCH_SIZE):
            states: list[dict[str, Any]] = []
            try:
                for window in batch:
                    page = context.new_page()
                    state = self._create_hotel_list_state(
                        page=page,
                        city_candidate=city_candidate,
                        check_in=window["check_in"],
                        check_out=window["check_out"],
                        limit=limit,
                        feature_filters=feature_filters,
                        keyword_candidate=keyword_candidate,
                    )
                    page.on("response", state["handler"])
                    states.append(state)

                for state in states:
                    try:
                        state["page"].goto(state["url"], wait_until="domcontentloaded", timeout=60000)
                    except PlaywrightTimeoutError:
                        state["load_failed"] = True

                active = [state for state in states if not state.get("load_failed")]
                if active:
                    active[0]["page"].wait_for_timeout(4500)

                for round_index in range(MAX_SCROLL_ROUNDS + 1):
                    active = [state for state in states if not state["done"] and not state.get("load_failed")]
                    if not active:
                        break

                    for state in active:
                        self._collect_hotel_list_state_snapshot(state)
                    for state in active:
                        self._update_hotel_list_state_progress(state, round_index)

                    active = [state for state in states if not state["done"] and not state.get("load_failed")]
                    if not active:
                        break

                    for state in active:
                        self._advance_hotel_list_scroll(state["page"], wait_ms=0)
                    active[0]["page"].wait_for_timeout(SCROLL_WAIT_MS)

                for state in states:
                    self._drain_hotel_list_state_response_items(state)
                    results[state["key"]] = self._finalize_hotel_items(
                        list(state["collected"].values())[:limit],
                        city_candidate=city_candidate,
                        check_in=state["check_in"],
                        check_out=state["check_out"],
                        feature_filters=state["feature_filters"],
                    )
            finally:
                for state in states:
                    try:
                        state["page"].remove_listener("response", state["handler"])
                    except (AttributeError, ValueError):
                        pass
                    try:
                        state["page"].close()
                    except Exception:
                        pass

        return results

    def _create_hotel_list_state(
        self,
        page,
        city_candidate: CityCandidate,
        check_in: dt.date,
        check_out: dt.date,
        limit: int,
        feature_filters: FeatureFilters,
        keyword_candidate: HotelKeywordCandidate | None = None,
    ) -> dict[str, Any]:
        response_items: list[dict[str, Any]] = []
        response_lock = threading.Lock()
        nights = max(1, (check_out - check_in).days)

        def collect_response_items(response) -> None:
            items = self._extract_hotel_list_response_items(response, nights=nights)
            if not items:
                return
            with response_lock:
                response_items.extend(items)

        return {
            "key": check_in.isoformat(),
            "page": page,
            "handler": collect_response_items,
            "url": self._build_hotel_list_url(
                city_candidate,
                check_in,
                check_out,
                feature_filters,
                keyword_candidate,
            ),
            "check_in": check_in,
            "check_out": check_out,
            "feature_filters": feature_filters,
            "limit": limit,
            "response_items": response_items,
            "response_lock": response_lock,
            "collected": {},
            "last_count": 0,
            "stable_rounds": 0,
            "done": False,
            "load_failed": False,
        }

    def _collect_hotel_list_state_snapshot(self, state: dict[str, Any]) -> None:
        self._drain_hotel_list_state_response_items(state)
        self._add_hotel_list_state_items(
            state,
            self._extract_hotel_list_snapshot(state["page"], state["limit"]),
        )
        self._drain_hotel_list_state_response_items(state)

    def _drain_hotel_list_state_response_items(self, state: dict[str, Any]) -> None:
        with state["response_lock"]:
            items = state["response_items"][:]
            state["response_items"].clear()
        self._add_hotel_list_state_items(state, items)

    def _add_hotel_list_state_items(self, state: dict[str, Any], items: list[dict[str, Any]]) -> None:
        collected = state["collected"]
        for item in items:
            key = item["hotel_id"] or item["detail_href"] or item["hotel_name"]
            if not key:
                continue
            if key not in collected:
                collected[key] = item
            elif self._hotel_item_score(item) > self._hotel_item_score(collected[key]):
                collected[key] = item
            if len(collected) >= state["limit"]:
                state["done"] = True
                break

    def _update_hotel_list_state_progress(self, state: dict[str, Any], round_index: int) -> None:
        current_count = len(state["collected"])
        if current_count >= state["limit"]:
            state["done"] = True
            return
        if round_index >= 1 and current_count == state["last_count"]:
            state["stable_rounds"] += 1
        else:
            state["stable_rounds"] = 0
        if state["stable_rounds"] >= STABLE_SCROLL_ROUNDS:
            state["done"] = True
            return
        state["last_count"] = current_count

    def _chunked(self, items: list[Any], size: int) -> list[list[Any]]:
        return [items[index : index + size] for index in range(0, len(items), max(1, size))]

    def _collect_scrolled_hotel_list(
        self,
        page,
        limit: int,
        city_candidate: CityCandidate,
        check_in: dt.date,
        check_out: dt.date,
        response_items: list[dict[str, Any]],
        response_lock: threading.Lock,
        feature_filters: FeatureFilters,
    ) -> list[dict[str, Any]]:
        collected: dict[str, dict[str, Any]] = {}
        last_count = 0
        stable_rounds = 0

        def add_items(items: list[dict[str, Any]]) -> None:
            for item in items:
                key = item["hotel_id"] or item["detail_href"] or item["hotel_name"]
                if not key:
                    continue
                if key not in collected:
                    collected[key] = item
                elif self._hotel_item_score(item) > self._hotel_item_score(collected[key]):
                    collected[key] = item
                if len(collected) >= limit:
                    break

        def drain_response_items() -> None:
            with response_lock:
                items = response_items[:]
                response_items.clear()
            add_items(items)

        for round_index in range(MAX_SCROLL_ROUNDS + 1):
            drain_response_items()
            add_items(self._extract_hotel_list_snapshot(page, limit))
            drain_response_items()

            current_count = len(collected)
            if current_count >= limit:
                break
            if round_index >= 1 and current_count == last_count:
                stable_rounds += 1
            else:
                stable_rounds = 0
            if stable_rounds >= STABLE_SCROLL_ROUNDS:
                break

            last_count = current_count
            self._advance_hotel_list_scroll(page)

        drain_response_items()
        return self._finalize_hotel_items(
            list(collected.values())[:limit],
            city_candidate=city_candidate,
            check_in=check_in,
            check_out=check_out,
            feature_filters=feature_filters,
        )

    def _hotel_item_score(self, item: dict[str, Any]) -> int:
        score = sum(
            1
            for key in ("detail_href", "room_name", "room_price_text", "tax_total_text")
            if item.get(key)
        )
        if item.get("_source") == "api":
            score += 2
        if not self._is_placeholder_hotel_name(item.get("hotel_name") or ""):
            score += 1
        return score

    def _extract_hotel_list_response_items(self, response, nights: int) -> list[dict[str, Any]]:
        if "/htls/getHotelList" not in response.url:
            return []
        try:
            data = response.json()
        except Exception:
            return []
        hotel_list = data.get("hotelList") if isinstance(data, dict) else None
        if not isinstance(hotel_list, list):
            return []
        return self._normalize_hotel_api_items(hotel_list, nights=nights)

    def _normalize_hotel_api_items(self, hotels: list[dict[str, Any]], nights: int) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for row in hotels:
            basic = row.get("hotelBasicInfo") or {}
            room = row.get("roomInfo") or {}
            min_room = row.get("minRoomInfo") or {}
            position = row.get("positionInfo") or {}

            hotel_id = str(basic.get("hotelId") or "").strip()
            hotel_name = str(basic.get("hotelName") or basic.get("hotelEnName") or "").strip()
            room_name = str(
                room.get("physicalRoomName")
                or room.get("name")
                or min_room.get("roomName")
                or ""
            ).strip()
            room_price_text = self._format_api_price_text(basic.get("price"))
            tax_total_text = self._extract_api_total_price_text(
                basic.get("priceExplanation"),
                basic.get("onlineAndShopTaxPrice") or basic.get("onlineTaxPrice"),
                nights=nights,
            )
            if not hotel_id or not hotel_name or not tax_total_text:
                continue
            city_name = str(position.get("cityName") or position.get("cityNameEn") or "").strip()
            area_hint = self._hotel_area_hint(row)
            area_name = self._infer_area_name(
                city_name=city_name,
                hotel_name=hotel_name,
                area_text=area_hint,
            )
            feature_flags = self._extract_api_feature_flags(row)
            item = {
                "hotel_id": hotel_id,
                "hotel_name": hotel_name,
                "detail_href": "",
                "room_name": room_name,
                "room_price_text": room_price_text,
                "tax_total_text": tax_total_text,
                "area_name": area_name,
                "area_hint": area_hint,
                "area_source": "Trip.com 位置",
                **feature_flags,
                "_source": "api",
            }
            coordinates = self._extract_coordinates(row)
            if coordinates:
                item["latitude"], item["longitude"] = coordinates
            items.append(item)
        return items

    def _extract_coordinates(self, value: Any) -> tuple[float, float] | None:
        found: list[tuple[float, float]] = []

        def coerce_number(raw: Any) -> float | None:
            if raw in ("", None):
                return None
            if isinstance(raw, (int, float)):
                return float(raw)
            match = re.search(r"-?\d+(?:\.\d+)?", str(raw))
            return float(match.group(0)) if match else None

        def visit(node: Any) -> None:
            if found:
                return
            if isinstance(node, dict):
                lat_value = None
                lon_value = None
                for key, child in node.items():
                    lowered = str(key).lower()
                    if "lat" in lowered and "relation" not in lowered:
                        lat_value = coerce_number(child)
                    if any(token in lowered for token in ("lng", "lon", "longitude")):
                        lon_value = coerce_number(child)
                if lat_value is not None and lon_value is not None and -90 <= lat_value <= 90 and -180 <= lon_value <= 180:
                    found.append((lat_value, lon_value))
                    return
                for child in node.values():
                    visit(child)
            elif isinstance(node, list):
                for child in node:
                    visit(child)

        visit(value)
        return found[0] if found else None

    def _extract_api_feature_flags(self, row: dict[str, Any]) -> dict[str, bool | None]:
        text = json.dumps(row, ensure_ascii=False).lower()
        return {
            "has_pool": self._text_has_pool_feature(text),
            "has_child_facility": self._text_has_child_feature(text),
            "is_advanced": self._extract_advanced_flag(row),
        }

    def _text_has_pool_feature(self, text: str) -> bool | None:
        if re.search(r"no\s+pool|without\s+pool|无泳池|無泳池|没有泳池|沒有泳池", text, flags=re.IGNORECASE):
            return False
        if re.search(r"\bpool\b|swimming|泳池|游泳", text, flags=re.IGNORECASE):
            return True
        return None

    def _text_has_child_feature(self, text: str) -> bool | None:
        if re.search(
            r"no\s+(children|kids)|without\s+(children|kids)|无儿童|無兒童|没有儿童|沒有兒童|无亲子|無親子",
            text,
            flags=re.IGNORECASE,
        ):
            return False
        if re.search(
            r"playground|family-friendly|parent-child|children|kid|儿童|兒童|亲子|親子",
            text,
            flags=re.IGNORECASE,
        ):
            return True
        return None

    def _feature_values_by_key(self, value: Any, key_tokens: tuple[str, ...]) -> list[Any]:
        found: list[Any] = []
        if isinstance(value, dict):
            for key, child in value.items():
                if any(token in str(key).lower() for token in key_tokens):
                    found.append(child)
                found.extend(self._feature_values_by_key(child, key_tokens))
        elif isinstance(value, list):
            for child in value:
                found.extend(self._feature_values_by_key(child, key_tokens))
        return found

    def _classify_advanced_value(self, value: Any) -> bool | None:
        if value in ("", None):
            return None
        text = str(value).lower()
        match = re.search(r"\d+(?:\.\d+)?", text)
        if match:
            return float(match.group(0)) >= 4
        if re.search(r"五星|五钻|五鑽|四星|四钻|四鑽|luxury|premium|upscale|deluxe", text):
            return True
        if re.search(r"三星|三钻|三鑽|二星|二钻|二鑽|经济|經濟|舒适|舒適|budget|comfort", text):
            return False
        return None

    def _extract_advanced_flag(self, row: dict[str, Any]) -> bool | None:
        basic = row.get("hotelBasicInfo") or {}
        candidates = [
            basic.get("star"),
            basic.get("starRating"),
            basic.get("hotelStar"),
            basic.get("hotelStarLevel"),
            basic.get("diamond"),
            basic.get("diamondLevel"),
        ]
        candidates.extend(self._feature_values_by_key(row, ("star", "diamond")))
        for value in candidates:
            classified = self._classify_advanced_value(value)
            if classified is not None:
                return classified
        return None

    def _hotel_area_hint(self, row: dict[str, Any]) -> str:
        basic = row.get("hotelBasicInfo") or {}
        position = row.get("positionInfo") or {}
        values: list[str] = [
            str(basic.get("hotelName") or ""),
            str(basic.get("hotelEnName") or ""),
            str(basic.get("hotelAddress") or ""),
            str(position.get("positionDesc") or ""),
            str(position.get("positionName") or ""),
            str(position.get("cityName") or ""),
            str(position.get("cityNameEn") or ""),
        ]
        for key in ("zoneNames", "transport"):
            value = position.get(key)
            if isinstance(value, list):
                values.extend(str(item) for item in value)
            elif value:
                values.append(str(value))
        return " ".join(item.strip() for item in values if item and str(item).strip())

    def _infer_area_name(self, city_name: str, hotel_name: str, area_text: str) -> str:
        text = " ".join([city_name or "", hotel_name or "", area_text or ""]).lower()
        city = city_name or ""
        normalized_city = self._area_city_label(city)
        city_patterns: dict[str, list[tuple[tuple[str, ...], str]]] = {
            "深圳": [
                (("wecc", "world exhibition", "international convention and exhibition", "international exhibition", "国际会展", "國際會展", "會展中心", "会展中心"), "深圳国际会展中心片区"),
                (("guangming", "光明", "hongqiao", "虹桥", "虹橋"), "光明虹桥公园片区"),
                (("guanlan", "mission hills", "觀瀾", "观澜"), "深圳观澜片区"),
                (("nanshan", "南山"), "深圳南山片区"),
                (("shenzhen bay", "深圳湾", "深圳灣"), "深圳湾片区"),
                (("qianhai", "前海"), "深圳前海片区"),
                (("shekou", "蛇口"), "深圳蛇口片区"),
                (("futian", "福田"), "深圳福田中心片区"),
                (("bao'an", "baoan", "宝安", "寶安"), "深圳宝安片区"),
                (("longhua", "龙华", "龍華"), "深圳龙华片区"),
                (("longgang", "龙岗", "龍崗"), "深圳龙岗片区"),
                (("yantian", "盐田", "鹽田"), "深圳盐田片区"),
                (("luohu", "罗湖", "羅湖"), "深圳罗湖片区"),
                (("pingshan", "坪山"), "深圳坪山片区"),
            ],
            "广州": [
                (("zengcheng", "增城"), "广州增城片区"),
                (("pazhou", "canton fair", "琶洲", "广交会", "廣交會", "會展中心", "会展中心"), "广州琶洲会展片区"),
                (("tianhe", "天河"), "广州天河片区"),
                (("yuexiu", "越秀"), "广州越秀片区"),
                (("haizhu", "海珠"), "广州海珠片区"),
                (("panyu", "番禺"), "广州番禺片区"),
                (("baiyun", "白云", "白雲"), "广州白云片区"),
                (("huangpu", "黄埔", "黃埔"), "广州黄埔片区"),
                (("huadu", "花都"), "广州花都片区"),
                (("conghua", "从化", "從化"), "广州从化片区"),
                (("nansha", "南沙"), "广州南沙片区"),
            ],
            "东莞": [
                (("songshan lake", "songshanhu", "松山湖"), "东莞松山湖片区"),
                (("houjie", "厚街", "guangdong modern international exhibition", "modern international exhibition", "现代国际展览", "現代國際展覽", "国际会展", "國際會展", "會展中心", "会展中心", "exhibition center"), "东莞厚街会展片区"),
                (("humen", "虎门", "虎門"), "东莞虎门片区"),
                (("chang'an", "changan", "长安", "長安"), "东莞长安片区"),
                (("nancheng", "南城"), "东莞南城片区"),
                (("dongcheng", "东城", "東城"), "东莞东城片区"),
                (("guancheng", "莞城"), "东莞莞城片区"),
                (("wanjiang", "万江", "萬江"), "东莞万江片区"),
                (("changping", "常平"), "东莞常平片区"),
                (("tangxia", "塘厦", "塘廈"), "东莞塘厦片区"),
                (("fenggang", "凤岗", "鳳崗"), "东莞凤岗片区"),
                (("dalang", "大朗"), "东莞大朗片区"),
                (("liaobu", "寮步"), "东莞寮步片区"),
                (("dalingshan", "大岭山", "大嶺山"), "东莞大岭山片区"),
                (("huangjiang", "黄江", "黃江"), "东莞黄江片区"),
                (("machong", "麻涌"), "东莞麻涌片区"),
                (("shilong", "石龙", "石龍"), "东莞石龙片区"),
                (("qingxi", "清溪"), "东莞清溪片区"),
                (("shatian", "沙田"), "东莞沙田片区"),
                (("daojiao", "dao jiao", "道滘"), "东莞道滘片区"),
                (("binhaiwan", "滨海湾", "濱海灣"), "东莞滨海湾片区"),
                (("qiaotou", "桥头", "橋頭"), "东莞桥头片区"),
                (("shipai", "石排"), "东莞石排片区"),
                (("gaobu", "高埗"), "东莞高埗片区"),
            ],
            "惠州": [
                (("xunliao", "巽寮", "巽寮湾", "巽寮灣"), "惠州巽寮湾片区"),
                (("double moon bay", "shuangyue", "双月湾", "雙月灣"), "惠州双月湾片区"),
                (("daya bay", "dayawan", "大亚湾", "大亞灣"), "惠州大亚湾片区"),
                (("huidong", "惠东", "惠東"), "惠州惠东片区"),
                (("huiyang", "惠阳", "惠陽", "danshui", "淡水"), "惠州惠阳片区"),
                (("boluo", "博罗", "博羅", "shiwan", "石湾", "石灣"), "惠州博罗片区"),
                (("zhongkai", "仲恺", "仲愷", "chenjiang", "陈江", "陳江", "tcl"), "惠州仲恺片区"),
                (("huicheng", "惠城", "jiangbei", "江北", "west lake", "西湖", "51新天地"), "惠州惠城片区"),
                (("longmen", "龙门", "龍門", "nankunshan", "南昆山"), "惠州龙门片区"),
                (("luofu", "罗浮山", "羅浮山"), "惠州罗浮山片区"),
            ],
            "中山": [
                (("shiqi", "石岐", "兴中", "興中"), "中山石岐片区"),
                (("east district", "dongqu", "东区", "東區", "利和", "lihe"), "中山东区片区"),
                (("west district", "xiqu", "西区", "西區"), "中山西区片区"),
                (("south district", "nanqu", "南区", "南區"), "中山南区片区"),
                (("torch", "huoju", "火炬", "开发区", "開發區", "zhongshan port", "中山港"), "中山火炬开发区片区"),
                (("xiaolan", "小榄", "小欖"), "中山小榄片区"),
                (("guzhen", "古镇", "古鎮", "灯都", "燈都"), "中山古镇片区"),
                (("sanxiang", "三乡", "三鄉"), "中山三乡片区"),
                (("tanzhou", "坦洲"), "中山坦洲片区"),
                (("nanlang", "南朗", "翠亨", "cuiheng"), "中山南朗翠亨片区"),
                (("dongfeng", "东凤", "東鳳"), "中山东凤片区"),
                (("shaxi", "沙溪"), "中山沙溪片区"),
                (("dachong", "大涌", "大湧"), "中山大涌片区"),
                (("gangkou", "港口"), "中山港口片区"),
                (("minzhong", "民众", "民眾"), "中山民众片区"),
                (("huangpu", "黄圃", "黃圃"), "中山黄圃片区"),
                (("nantou", "南头", "南頭"), "中山南头片区"),
                (("banfu", "板芙"), "中山板芙片区"),
                (("henglan", "横栏", "橫欄"), "中山横栏片区"),
                (("wuguishan", "五桂山"), "中山五桂山片区"),
            ],
            "江门": [
                (("xinhui", "新会", "新會", "gudou", "古兜"), "江门新会片区"),
                (("heshan", "鹤山", "鶴山", "gulao", "古劳", "古勞"), "江门鹤山片区"),
                (("taishan", "台山", "下川岛", "下川島", "naqin", "那琴", "川岛", "川島"), "江门台山片区"),
                (("kaiping", "开平", "開平", "chikan", "赤坎"), "江门开平赤坎片区"),
                (("enping", "恩平", "泉林", "温泉", "溫泉"), "江门恩平温泉片区"),
                (("pengjiang", "蓬江", "五邑", "万达", "萬達", "利和", "白石"), "江门蓬江片区"),
                (("jianghai", "江海", "高新", "礼乐", "禮樂"), "江门江海片区"),
                (("yinhuwan", "银湖湾", "銀湖灣", "滨海新区", "濱海新區"), "江门银湖湾片区"),
            ],
            "河源": [
                (("wanlv", "wanlu", "万绿湖", "萬綠湖", "新丰江", "新豐江"), "河源万绿湖片区"),
                (("bavaria", "巴伐利亚", "巴伐利亞", "福源寺"), "河源巴伐利亚庄园片区"),
                (("ketianxia", "客天下", "春沐源", "chunmuyuan"), "河源客天下片区"),
                (("yuancheng", "源城", "兴源", "興源", "亚洲第一高喷泉", "亞洲第一高噴泉"), "河源源城片区"),
                (("dongyuan", "东源", "東源", "康禾"), "河源东源片区"),
                (("longchuan", "龙川", "龍川"), "河源龙川片区"),
                (("zijin", "紫金"), "河源紫金片区"),
                (("lianping", "连平", "連平"), "河源连平片区"),
                (("heping", "和平", "温泉", "溫泉"), "河源和平温泉片区"),
            ],
            "肇庆": [
                (("qixingyan", "seven star", "七星岩", "七星巖", "星湖", "牌坊", "星湖湾", "星湖灣"), "肇庆七星岩星湖片区"),
                (("dinghu", "鼎湖", "鼎湖山", "砚洲", "硯洲"), "肇庆鼎湖山片区"),
                (("duanzhou", "端州", "敏捷广场", "敏捷廣場", "宋城墙", "宋城牆"), "肇庆端州片区"),
                (("gaoyao", "高要", "金利"), "肇庆高要片区"),
                (("sihui", "四会", "四會", "大旺", "高新区", "高新區"), "肇庆四会片区"),
                (("guangning", "广宁", "廣寧"), "肇庆广宁片区"),
                (("huaiji", "怀集", "懷集"), "肇庆怀集片区"),
                (("fengkai", "封开", "封開"), "肇庆封开片区"),
                (("deqing", "德庆", "德慶", "盘龙峡", "盤龍峽"), "肇庆德庆片区"),
            ],
            "珠海": [
                (("hengqin", "横琴", "橫琴", "chimelong", "长隆", "長隆", "ocean kingdom", "海洋王国", "海洋王國"), "珠海横琴长隆片区"),
                (("xiangzhou", "香洲", "情侣路", "情侶路", "海滨", "海濱", "日月贝", "日月貝", "野狸岛", "野狸島"), "珠海情侣路香洲片区"),
                (("gongbei", "拱北", "口岸", "港珠澳", "港珠澳大桥", "港珠澳大橋"), "珠海拱北口岸片区"),
                (("jida", "吉大", "免税", "免稅"), "珠海吉大片区"),
                (("wanzai", "湾仔", "灣仔", "会展", "會展", "十字门", "十字門"), "珠海湾仔会展片区"),
                (("jinwan", "金湾", "金灣", "航空新城", "机场", "機場", "sanzao", "三灶"), "珠海金湾航空新城片区"),
                (("tangjia", "唐家", "唐家湾", "唐家灣", "高新区", "高新區", "淇澳"), "珠海唐家湾片区"),
                (("doumen", "斗门", "斗門", "井岸", "御温泉", "御溫泉"), "珠海斗门片区"),
            ],
            "韶关": [
                (("danxia", "丹霞", "丹霞山", "仁化"), "韶关丹霞山片区"),
                (("nanhua", "南华寺", "南華寺", "caoxi", "曹溪", "马坝", "馬壩"), "韶关南华寺曹溪片区"),
                (("ruyuan", "乳源", "大峡谷", "大峽谷", "云门山", "雲門山", "南岭", "南嶺"), "韶关乳源大峡谷片区"),
                (("zhenjiang", "浈江", "湞江", "wujiang", "武江", "韶关站", "韶關站", "高铁站", "高鐵站", "摩尔城", "摩爾城", "百年东街", "百年東街"), "韶关市区片区"),
                (("nanxiong", "南雄", "珠玑", "珠璣", "帽子峰"), "韶关南雄片区"),
                (("lechang", "乐昌", "樂昌", "坪石"), "韶关乐昌片区"),
                (("wengyuan", "翁源", "翁山源", "始兴", "始興"), "韶关翁源始兴片区"),
                (("qujiang", "曲江", "经律论", "經律論", "小坑", "汤泉谷", "湯泉谷"), "韶关曲江片区"),
                (("xinfeng", "新丰", "新豐"), "韶关新丰片区"),
            ],
            "汕尾": [
                (("jinding", "jinding bay", "金町", "金町湾", "金町灣", "保利金町湾", "保利金町灣"), "汕尾金町湾片区"),
                (("red bay", "honghai", "红海湾", "紅海灣", "遮浪", "zhelang"), "汕尾红海湾片区"),
                (("haifeng", "海丰", "海豐", "莲花山", "蓮花山"), "汕尾海丰片区"),
                (("lufeng", "陆丰", "陸豐", "碣石", "甲子", "金厢", "金廂"), "汕尾陆丰片区"),
                (("luhe", "陆河", "陸河"), "汕尾陆河片区"),
                (("chengqu", "城区", "城區", "香洲", "品清湖", "凤山", "鳳山", "汕尾站", "善美"), "汕尾市区片区"),
                (("shenshan", "深汕", "鹅埠", "鵝埠", "鲘门", "鮜門"), "汕尾深汕合作区片区"),
            ],
        }
        patterns = city_patterns.get(normalized_city, [])
        if not patterns and not normalized_city:
            if "shenzhen" in text or "深圳" in text:
                patterns = city_patterns["深圳"]
            elif "guangzhou" in text or "广州" in text or "廣州" in text:
                patterns = city_patterns["广州"]
            elif "dongguan" in text or "东莞" in text or "東莞" in text:
                patterns = city_patterns["东莞"]
            elif "huizhou" in text or "惠州" in text:
                patterns = city_patterns["惠州"]
            elif "jiangmen" in text or "江门" in text or "江門" in text:
                patterns = city_patterns["江门"]
            elif "heyuan" in text or "河源" in text:
                patterns = city_patterns["河源"]
            elif "zhaoqing" in text or "肇庆" in text or "肇慶" in text:
                patterns = city_patterns["肇庆"]
            elif "zhuhai" in text or "珠海" in text:
                patterns = city_patterns["珠海"]
            elif "shaoguan" in text or "韶关" in text or "韶關" in text:
                patterns = city_patterns["韶关"]
            elif "shanwei" in text or "汕尾" in text:
                patterns = city_patterns["汕尾"]
        for keywords, area_name in patterns:
            if any(keyword.lower() in text for keyword in keywords):
                return area_name

        global_area = self._infer_global_area_name(
            city_name=normalized_city,
            hotel_name=hotel_name,
            area_text=area_text,
        )
        if global_area:
            return global_area

        return f"{normalized_city}区域待确认" if normalized_city else "区域待确认"

    def _area_city_label(self, city_name: str) -> str:
        normalized = self._normalize_city_label(city_name)
        lowered = (normalized or city_name or "").strip().lower()
        raw_lowered = (city_name or "").strip().lower()
        for key, label in GLOBAL_CITY_ALIASES.items():
            if key in lowered or key in raw_lowered or key in (city_name or ""):
                return label
        return normalized

    def _infer_global_area_name(self, city_name: str, hotel_name: str, area_text: str) -> str:
        city_label = self._area_city_label(city_name)
        text = " ".join([city_name or "", hotel_name or "", area_text or ""]).lower()
        for keywords, area_name in GLOBAL_AREA_PATTERNS.get(city_label, []):
            if any(keyword.lower() in text for keyword in keywords):
                return area_name

        candidates = self._generic_area_candidates(area_text)
        candidates.extend(self._hotel_name_area_candidates(hotel_name))
        seen: set[str] = set()
        for candidate in candidates:
            if candidate in seen:
                continue
            seen.add(candidate)
            cleaned = self._clean_generic_area_candidate(candidate, city_label, hotel_name)
            if cleaned:
                return self._format_generic_area_name(city_label, cleaned)
        return ""

    def _generic_area_candidates(self, area_text: str) -> list[str]:
        if not area_text:
            return []
        normalized = re.sub(r"\s+", " ", html.unescape(str(area_text))).strip()
        normalized = re.sub(r"\bnear\b", "| Near", normalized, flags=re.IGNORECASE)
        pieces = re.split(r"\||•|·|,|;|\n", normalized)
        candidates: list[str] = []
        seen: set[str] = set()

        def add_candidate(raw_value: str) -> None:
            value = raw_value.strip(" -")
            if not value or value in seen:
                return
            seen.add(value)
            candidates.append(value)
            if not re.search(r"[A-Za-z]", value):
                return
            words = re.findall(r"[A-Za-z][A-Za-z'&.-]*", value)
            words = [word for word in words if len(word) > 1 and word.lower() not in {"near", "the", "and"}]
            for size in (3, 2, 1):
                if len(words) < size:
                    continue
                tail = " ".join(words[-size:])
                if tail and tail not in seen:
                    seen.add(tail)
                    candidates.append(tail)

        for piece in pieces:
            value = piece.strip(" -")
            if not value:
                continue
            value = re.sub(r"^near\s+", "", value, flags=re.IGNORECASE).strip(" -")
            add_candidate(value)
        return candidates

    def _hotel_name_area_candidates(self, hotel_name: str) -> list[str]:
        if not hotel_name:
            return []
        normalized = re.sub(r"\s+", " ", html.unescape(str(hotel_name))).strip()
        if not normalized:
            return []
        candidates: list[str] = []
        seen: set[str] = set()

        def add_candidate(raw_value: str) -> None:
            value = raw_value.strip(" -")
            if len(value) < 2 or value in seen:
                return
            seen.add(value)
            candidates.append(value)

        for phrase in self._chinese_area_phrases(normalized):
            add_candidate(phrase)

        for match in re.finditer(r"[（(【\[]([^）)】\]]{2,80})[）)】\]]", normalized):
            add_candidate(match.group(1))

        for piece in re.split(r"\||•|·|,|;|/|\n", normalized):
            piece = piece.strip()
            if not piece:
                continue
            for phrase in self._chinese_area_phrases(piece):
                add_candidate(phrase)
        return candidates

    def _chinese_area_phrases(self, text: str) -> list[str]:
        normalized = self._normalize_area_chinese_chars(text)
        suffixes = (
            "新国际博览中心",
            "国际博览中心",
            "博览中心",
            "会展中心",
            "高铁北站",
            "高铁站",
            "火车站",
            "机场",
            "人民广场",
            "万达广场",
            "广场",
            "步行街",
            "商业区",
            "开发区",
            "高新区",
            "新区",
            "工业园区",
            "园区",
            "风景区",
            "度假区",
            "古镇",
            "古城",
            "老街",
            "大学城",
            "科技园",
            "产业园",
            "口岸",
            "码头",
            "外滩",
            "陆家嘴",
            "南京路",
            "徐家汇",
            "静安寺",
            "新天地",
            "田子坊",
            "金鸡湖",
            "阳澄湖",
            "独墅湖",
            "太湖",
            "苏州湾",
            "山塘街",
            "平江路",
            "观前街",
            "拙政园",
            "留园",
            "同里",
            "周庄",
            "姑苏",
            "普陀",
            "浦东",
            "浦西",
            "宝山",
            "松江",
            "闵行",
            "青浦",
            "嘉定",
            "吴中",
            "相城",
            "常熟",
            "昆山",
            "吴江",
        )
        suffix_pattern = "|".join(re.escape(item) for item in sorted(suffixes, key=len, reverse=True))
        pattern = rf"[\u3400-\u9fff]{{0,14}}(?:{suffix_pattern})"
        phrases: list[str] = []
        seen: set[str] = set()
        sorted_suffixes = sorted(suffixes, key=len, reverse=True)
        for match in re.finditer(pattern, normalized):
            phrase = match.group(0)
            if phrase not in seen:
                seen.add(phrase)
                phrases.append(phrase)
            for suffix in sorted_suffixes:
                if phrase.endswith(suffix) and suffix != phrase and suffix not in seen:
                    seen.add(suffix)
                    phrases.append(suffix)
                    break
        return phrases

    def _clean_generic_area_candidate(self, value: str, city_label: str, hotel_name: str = "") -> str:
        text = re.sub(r"\s+", " ", value or "").strip(" -")
        if not text:
            return ""
        hotel_lower = (hotel_name or "").lower()
        if hotel_lower and hotel_lower in text.lower():
            return ""
        translated = self._translate_area_candidate(text, city_label)
        if translated:
            return translated
        if re.search(r"\b(city centre|city center|downtown)\b", text, flags=re.IGNORECASE):
            return "市中心"
        text = re.sub(r"\b(near|metro station|station|airport|hotel|resort|apartment|mall)\b.*$", "", text, flags=re.IGNORECASE).strip(" -")
        for token in self._city_area_strip_tokens(city_label):
            if re.search(r"[\u3400-\u9fff]", token):
                text = text.replace(token, "")
            else:
                text = re.sub(rf"\b{re.escape(token)}\b", "", text, flags=re.IGNORECASE)
        text = text.strip(" -")
        if city_label:
            text = text.replace(city_label, "").strip(" -")
        text = re.sub(r"\d+.*$", "", text).strip(" -")
        translated = self._translate_area_candidate(text, city_label)
        if translated:
            return translated
        if re.search(r"[\u3400-\u9fff]", text):
            return self._clean_chinese_area_candidate(text, city_label)
        if re.search(
            r"\b(hilton|marriott|sheraton|ibis|mercure|sofitel|aloft|wyndham|days|westin|kasa|pullman|adagio|peninsula|fairfield|residence inn|four points|best western)\b",
            text,
            flags=re.IGNORECASE,
        ):
            return ""
        if not text or len(text) > 34:
            return ""
        if re.search(r"\b(road|street|avenue|alley|soi|jalan|lorong|place|center|centre)\b", text, flags=re.IGNORECASE):
            return ""
        if re.search(r"^[A-Za-z][A-Za-z\s'&.-]{2,}$", text):
            return ""
        return ""

    def _clean_chinese_area_candidate(self, value: str, city_label: str) -> str:
        original = self._normalize_area_chinese_chars(value)
        text = re.sub(r"\s+", "", original).strip(" -")
        if not text:
            return ""
        for token in self._city_area_strip_tokens(city_label):
            normalized_token = self._normalize_area_chinese_chars(token)
            if re.search(r"[\u3400-\u9fff]", normalized_token):
                text = re.sub(rf"^{re.escape(normalized_token)}市?", "", text)
                text = re.sub(rf"{re.escape(normalized_token)}市?$", "", text)
        if len(text) <= 1 and city_label and original.startswith(city_label):
            text = original
        text = re.sub(r"^(近|邻近|靠近|位于|坐落于)", "", text)
        text = re.sub(r"地铁站.*$", "", text)
        text = re.sub(r"地铁.*$", "", text)
        text = re.sub(r"(地铁站|地鐵站|公交站|巴士站|站店|店)$", "", text)
        text = re.sub(r"(附近|周边|周邊)$", "", text)
        brand_pattern = (
            r"JW|AC|NOA|"
            r"希尔顿|希爾頓|万豪|萬豪|万楓|萬楓|喜来登|喜來登|丽思|麗思|"
            r"香格里拉|智选假日|智選假日|皇冠假日|洲际|洲際|假日|凯悦|凱悦|凱悅|"
            r"美居|温德姆|溫德姆|雅高|铂尔曼|鉑爾曼|宜必思|亚朵|亞朵|桔子|"
            r"维也纳|維也納|汉庭|漢庭|如家|锦江|錦江|全季|美仑|美侖|"
            r"诺富特|諾富特|丽呈|麗呈|开元|開元|君亭|瑞贝庭|瑞貝庭|欢朋|歡朋|"
            r"绿地|綠地|万达|萬達|波特曼"
        )
        parts = re.split(brand_pattern, text, maxsplit=1, flags=re.IGNORECASE)
        if parts and parts[0].strip():
            text = parts[0].strip()
        elif re.search(brand_pattern, text, flags=re.IGNORECASE):
            return ""
        text = re.split(r"酒店|大酒店|饭店|飯店|宾馆|賓館|公寓|民宿|客栈|客棧|旅馆|旅館", text, maxsplit=1)[0]
        text = text.strip(" -")
        if text.endswith("片区"):
            text = text[:-2]
        if not text or len(text) <= 1 or len(text) > 18:
            return ""
        if text in {"城市", "酒店", "饭店", "大酒店", "市区", "市中心"}:
            return "市中心" if text in {"市区", "市中心"} else ""
        if re.search(r"[A-Za-z]", text):
            translated = self._translate_area_candidate(text, city_label)
            return translated
        return text

    def _normalize_area_chinese_chars(self, text: str) -> str:
        replacements = {
            "蘇": "苏",
            "灣": "湾",
            "達": "达",
            "萬": "万",
            "網": "网",
            "綠": "绿",
            "環": "环",
            "寶": "宝",
            "長": "长",
            "壽": "寿",
            "夢": "梦",
            "裡": "里",
            "鄉": "乡",
            "臨": "临",
            "納": "纳",
            "術": "术",
            "學": "学",
            "龍": "龙",
            "門": "门",
            "國": "国",
            "際": "际",
            "會": "会",
            "覽": "览",
            "鐵": "铁",
            "車": "车",
            "廣": "广",
            "場": "场",
            "業": "业",
            "區": "区",
            "開": "开",
            "發": "发",
            "園": "园",
            "風": "风",
            "鎮": "镇",
            "碼": "码",
            "灘": "滩",
            "陸": "陆",
            "匯": "汇",
            "靜": "静",
            "雞": "鸡",
            "陽": "阳",
            "獨": "独",
            "觀": "观",
            "廟": "庙",
            "縣": "县",
            "閔": "闵",
            "吳": "吴",
            "飯": "饭",
            "賓": "宾",
            "棧": "栈",
            "館": "馆",
            "諾": "诺",
            "麗": "丽",
            "鉑": "铂",
            "爾": "尔",
            "凱": "凯",
            "悅": "悦",
            "亞": "亚",
            "維": "维",
            "漢": "汉",
            "歡": "欢",
            "園": "园",
            "師": "师",
        }
        return "".join(replacements.get(char, char) for char in str(text or ""))

    def _city_area_strip_tokens(self, city_label: str) -> set[str]:
        tokens = set(GLOBAL_CITY_STRIP_TOKENS.get(city_label, set()))
        if city_label:
            tokens.add(city_label)
        return tokens

    def _format_generic_area_name(self, city_label: str, area_label: str) -> str:
        area = area_label.strip()
        if not area:
            return ""
        if city_label and not area.startswith(city_label):
            return f"{city_label}{area}片区"
        return f"{area}片区"

    def _translate_area_candidate(self, value: str, city_label: str = "") -> str:
        lowered = re.sub(r"\s+", " ", (value or "").strip().lower())
        if not lowered:
            return ""
        city_translations = AREA_CITY_CANDIDATE_TRANSLATIONS.get(city_label, {})
        if lowered in city_translations:
            return city_translations[lowered]
        for token, translated in sorted(city_translations.items(), key=lambda item: len(item[0]), reverse=True):
            if re.search(rf"\b{re.escape(token)}\b", lowered):
                return translated
        if lowered in AREA_CANDIDATE_TRANSLATIONS:
            return AREA_CANDIDATE_TRANSLATIONS[lowered]
        for token, translated in sorted(AREA_CANDIDATE_TRANSLATIONS.items(), key=lambda item: len(item[0]), reverse=True):
            if re.search(rf"\b{re.escape(token)}\b", lowered):
                return translated
        return ""

    def _normalize_area_display_name(self, area_name: str, city_name: str = "") -> str:
        text = re.sub(r"\s+", " ", str(area_name or "")).strip()
        text = self._normalize_area_chinese_chars(text)
        if not text or self._is_generic_area_name(text):
            return ""
        text = AREA_NAME_REPLACEMENTS.get(text, text)
        city_label = self._area_city_label(city_name or text)
        for token, translated in sorted(
            AREA_CITY_CANDIDATE_TRANSLATIONS.get(city_label, {}).items(),
            key=lambda item: len(item[0]),
            reverse=True,
        ):
            text = re.sub(rf"\b{re.escape(token)}\b", translated, text, flags=re.IGNORECASE)
        for token, translated in sorted(AREA_CANDIDATE_TRANSLATIONS.items(), key=lambda item: len(item[0]), reverse=True):
            text = re.sub(rf"\b{re.escape(token)}\b", translated, text, flags=re.IGNORECASE)
        text = AREA_NAME_REPLACEMENTS.get(text, text)
        text = re.sub(r"片区片区$", "片区", text)
        if re.search(r"[A-Za-z]", text):
            return ""
        if not text.endswith("片区"):
            text = f"{text}片区"
        if city_label and text == f"{city_label}片区":
            return ""
        return text

    def _geonames_area_name(self, lat: Any, lon: Any, city_name: str) -> str:
        if not self.geonames_username:
            return ""
        try:
            lat_value = round(float(lat), 5)
            lon_value = round(float(lon), 5)
        except (TypeError, ValueError):
            return ""
        city_label = self._area_city_label(city_name)
        cache_key = (lat_value, lon_value, city_label)
        if cache_key in self._geonames_area_cache:
            return self._geonames_area_cache[cache_key]

        def read_service(path: str, params: dict[str, Any]) -> dict[str, Any]:
            query = urlencode({**params, "username": self.geonames_username})
            req = Request(f"https://secure.geonames.org/{path}?{query}", headers={"User-Agent": UA})
            with urlopen(req, timeout=2.5) as resp:
                return json.loads(resp.read().decode("utf-8"))

        candidates: list[str] = []
        service_params = {"lat": lat_value, "lng": lon_value, "lang": "zh", "style": "SHORT"}
        try:
            data = read_service("neighbourhoodJSON", service_params)
            neighbourhood = data.get("neighbourhood") if isinstance(data, dict) else None
            if isinstance(neighbourhood, dict):
                candidates.append(str(neighbourhood.get("name") or ""))
        except (OSError, HTTPError, URLError, TimeoutError, json.JSONDecodeError):
            pass
        try:
            data = read_service("findNearbyJSON", {**service_params, "radius": 5, "maxRows": 5})
            rows = data.get("geonames") if isinstance(data, dict) else None
            if isinstance(rows, list):
                for row in rows:
                    if isinstance(row, dict):
                        candidates.append(str(row.get("name") or row.get("toponymName") or ""))
        except (OSError, HTTPError, URLError, TimeoutError, json.JSONDecodeError):
            pass

        for candidate in candidates:
            cleaned = self._normalize_area_display_name(self._format_generic_area_name(city_label, candidate), city_label)
            if cleaned and city_label not in cleaned.replace(f"{city_label}", "", 1):
                self._geonames_area_cache[cache_key] = cleaned
                return cleaned
        self._geonames_area_cache[cache_key] = ""
        return ""

    def _normalize_city_label(self, city_name: str) -> str:
        text = (city_name or "").strip().lower()
        if "shanghai" in text or "上海" in city_name:
            return "上海"
        if "suzhou" in text or "苏州" in city_name or "蘇州" in city_name:
            return "苏州"
        if (
            "sao paulo" in text
            or "são paulo" in text
            or "saopaulo" in text
            or "圣保罗" in city_name
            or "聖保羅" in city_name
        ):
            return "圣保罗"
        if "moscow" in text or "moskva" in text or "莫斯科" in city_name:
            return "莫斯科"
        if "jakarta" in text or "雅加达" in city_name or "雅加達" in city_name:
            return "雅加达"
        if "hong kong" in text or "hongkong" in text or "香港" in city_name:
            return "香港"
        if "las vegas" in text or "拉斯维加斯" in city_name or "拉斯維加斯" in city_name:
            return "拉斯维加斯"
        if "shenzhen" in text or "深圳" in city_name:
            return "深圳"
        if "guangzhou" in text or "广州" in city_name or "廣州" in city_name:
            return "广州"
        if "dongguan" in text or "东莞" in city_name or "東莞" in city_name:
            return "东莞"
        if "zhongshan" in text or "中山" in city_name:
            return "中山"
        if (
            "jiangmen" in text
            or "江门" in city_name
            or "江門" in city_name
            or "xinhui" in text
            or "新会" in city_name
            or "新會" in city_name
            or "heshan" in text
            or "鹤山" in city_name
            or "鶴山" in city_name
            or "taishan" in text
            or "台山" in city_name
            or "kaiping" in text
            or "开平" in city_name
            or "開平" in city_name
            or "enping" in text
            or "恩平" in city_name
        ):
            return "江门"
        if (
            "heyuan" in text
            or "河源" in city_name
            or "yuancheng" in text
            or "源城" in city_name
            or "dongyuan" in text
            or "东源" in city_name
            or "東源" in city_name
            or "longchuan" in text
            or "龙川" in city_name
            or "龍川" in city_name
            or "zijin" in text
            or "紫金" in city_name
            or "lianping" in text
            or "连平" in city_name
            or "連平" in city_name
        ):
            return "河源"
        if (
            "zhaoqing" in text
            or "肇庆" in city_name
            or "肇慶" in city_name
            or "duanzhou" in text
            or "端州" in city_name
            or "dinghu" in text
            or "鼎湖" in city_name
            or "gaoyao" in text
            or "高要" in city_name
            or "sihui" in text
            or "四会" in city_name
            or "四會" in city_name
        ):
            return "肇庆"
        if (
            "zhuhai" in text
            or "珠海" in city_name
            or "xiangzhou" in text
            or "香洲" in city_name
            or "hengqin" in text
            or "横琴" in city_name
            or "橫琴" in city_name
            or "gongbei" in text
            or "拱北" in city_name
            or "jinwan" in text
            or "金湾" in city_name
            or "金灣" in city_name
            or "doumen" in text
            or "斗门" in city_name
            or "斗門" in city_name
        ):
            return "珠海"
        if (
            "macau" in text
            or "macao" in text
            or "澳门" in city_name
            or "澳門" in city_name
        ):
            return "澳门"
        if (
            "shaoguan" in text
            or "韶关" in city_name
            or "韶關" in city_name
            or "danxia" in text
            or "丹霞" in city_name
            or "renhua" in text
            or "仁化" in city_name
            or "ruyuan" in text
            or "乳源" in city_name
            or "nanxiong" in text
            or "南雄" in city_name
            or "lechang" in text
            or "乐昌" in city_name
            or "樂昌" in city_name
            or "qujiang" in text
            or "曲江" in city_name
        ):
            return "韶关"
        if (
            "shanwei" in text
            or "汕尾" in city_name
            or "haifeng" in text
            or "海丰" in city_name
            or "海豐" in city_name
            or "lufeng" in text
            or "陆丰" in city_name
            or "陸豐" in city_name
            or "luhe" in text
            or "陆河" in city_name
            or "陸河" in city_name
            or "honghai" in text
            or "红海湾" in city_name
            or "紅海灣" in city_name
            or "jinding" in text
            or "金町" in city_name
            or "shenshan" in text
            or "深汕" in city_name
        ):
            return "汕尾"
        if (
            "qingyuan" in text
            or "清远" in city_name
            or "清遠" in city_name
            or "yingde" in text
            or "英德" in city_name
        ):
            return "清远"
        if (
            "chenzhou" in text
            or "郴州" in city_name
            or "yizhang" in text
            or "宜章" in city_name
        ):
            return "郴州"
        if (
            "ganzhou" in text
            or "赣州" in city_name
            or "贛州" in city_name
        ):
            return "赣州"
        if (
            "yunfu" in text
            or "云浮" in city_name
            or "雲浮" in city_name
            or "yunan" in text
            or "云安" in city_name
            or "雲安" in city_name
            or "luoding" in text
            or "罗定" in city_name
            or "羅定" in city_name
        ):
            return "云浮"
        if (
            "foshan" in text
            or "佛山" in city_name
            or "shunde" in text
            or "顺德" in city_name
            or "順德" in city_name
            or "jiujiang" in text
            or "九江" in city_name
        ):
            return "佛山"
        if (
            "huizhou" in text
            or "惠州" in city_name
            or "boluo" in text
            or "博罗" in city_name
            or "博羅" in city_name
            or "huidong" in text
            or "惠东" in city_name
            or "惠東" in city_name
            or "huiyang" in text
            or "惠阳" in city_name
            or "惠陽" in city_name
            or "longmen" in text
            or "龙门" in city_name
            or "龍門" in city_name
        ):
            return "惠州"
        return (city_name or "").strip()

    def _is_outside_search_city(
        self,
        item: dict[str, Any],
        city_candidate: CityCandidate,
        detail_url: str,
    ) -> bool:
        expected = self._normalize_city_label(city_candidate.city_name)
        if not expected:
            return False
        detail_city = self._detect_city_label_from_detail_url(detail_url)
        if detail_city:
            return detail_city != expected

        text = " ".join(
            str(value or "")
            for value in (
                item.get("hotel_name"),
                item.get("area_name"),
                item.get("area_hint"),
                item.get("detail_href"),
            )
        )
        if self._text_mentions_city_label(text, expected):
            return False
        detected = self._detect_city_label_from_text(text)
        return bool(detected and detected != expected)

    def _detect_city_label_from_detail_url(self, detail_url: str) -> str:
        if not detail_url:
            return ""
        query = parse_qs(urlparse(detail_url).query)
        for key in ("cityName", "cityEnName"):
            value = (query.get(key) or [""])[0]
            city_label = self._normalize_city_label(value)
            if city_label and city_label != value.strip():
                return city_label
        city_id = (query.get("cityId") or [""])[0]
        return CITY_ID_LABELS.get(str(city_id).strip(), "")

    def _detect_city_label_from_text(self, text: str) -> str:
        for city_label, keywords in CITY_LABEL_KEYWORDS.items():
            if self._text_has_any_city_keyword(text, keywords):
                return city_label
        return ""

    def _text_mentions_city_label(self, text: str, city_label: str) -> bool:
        return self._text_has_any_city_keyword(text, CITY_LABEL_KEYWORDS.get(city_label, ()))

    def _text_has_any_city_keyword(self, text: str, keywords: tuple[str, ...]) -> bool:
        lowered = (text or "").lower()
        return any(keyword.lower() in lowered for keyword in keywords)

    def _build_area_recommendations(
        self,
        choices: list[dict[str, Any]],
        city_name: str,
        include_defaults: bool = True,
    ) -> list[dict[str, Any]]:
        groups: dict[str, dict[str, Any]] = {}
        coordinate_centers = self._coordinate_centers_by_city(choices, city_name)
        for item in choices:
            area_name = self._choice_area_name(item, city_name)
            if not area_name or self._is_generic_area_name(area_name):
                area_name = self._coordinate_area_name(item, city_name, coordinate_centers)
            if not area_name or self._is_generic_area_name(area_name):
                continue
            group = groups.setdefault(
                area_name,
                {
                    "area_name": area_name,
                    "hotel_count": 0,
                    "lower_price_hotel_count": 0,
                    "slightly_higher_hotel_count": 0,
                    "holiday_values": [],
                    "diff_values": [],
                    "room_types": set(),
                    "representative_hotels": [],
                },
            )
            diff = int(item.get("price_diff_nightly") or 0)
            group["hotel_count"] += 1
            if diff <= 0:
                group["lower_price_hotel_count"] += 1
            else:
                group["slightly_higher_hotel_count"] += 1
            group["holiday_values"].append(int(item.get("holiday_avg_nightly_tax_total_value") or 0))
            group["diff_values"].append(diff)
            if item.get("room_type_label"):
                group["room_types"].add(item["room_type_label"])
            if len(group["representative_hotels"]) < 4:
                group["representative_hotels"].append(item.get("hotel_name") or item.get("hotel_original_name") or "")

        recommendations: list[dict[str, Any]] = []
        for group in groups.values():
            holiday_values = [value for value in group["holiday_values"] if value > 0]
            diff_values = group["diff_values"] or [0]
            avg_holiday = round(sum(holiday_values) / len(holiday_values)) if holiday_values else 0
            avg_diff = round(sum(diff_values) / len(diff_values))
            lower_ratio = group["lower_price_hotel_count"] / max(1, group["hotel_count"])
            recommendations.append(
                {
                    "area_name": group["area_name"],
                    "hotel_count": group["hotel_count"],
                    "lower_price_hotel_count": group["lower_price_hotel_count"],
                    "slightly_higher_hotel_count": group["slightly_higher_hotel_count"],
                    "lower_price_ratio": lower_ratio,
                    "average_holiday_nightly_tax_total_value": avg_holiday,
                    "average_holiday_nightly_tax_total_price": self._format_cny(avg_holiday),
                    "average_price_diff_nightly": avg_diff,
                    "average_price_diff_nightly_text": self._format_cny_diff(avg_diff),
                    "room_type_labels": sorted(group["room_types"]),
                    "representative_hotels": [name for name in group["representative_hotels"] if name],
                    "reason": self._area_recommendation_reason(group["hotel_count"], group["lower_price_hotel_count"], avg_diff),
                }
            )

        recommendations.sort(
            key=lambda item: (
                -item["lower_price_ratio"],
                -item["lower_price_hotel_count"],
                -item["hotel_count"],
                item["average_price_diff_nightly"],
                item["average_holiday_nightly_tax_total_value"],
            )
        )
        if include_defaults:
            recommendations = self._add_default_area_recommendations(recommendations, city_name, choices)
        return recommendations[:8]

    def _is_generic_area_name(self, area_name: str) -> bool:
        text = area_name or ""
        return "热门酒店片区" in text or "区域待确认" in text

    def _coordinate_centers_by_city(
        self,
        choices: list[dict[str, Any]],
        city_name: str,
    ) -> dict[str, tuple[float, float]]:
        values_by_city: dict[str, list[tuple[float, float]]] = {}
        for item in choices:
            coords = self._item_coordinate_pair(item)
            if coords is None:
                continue
            city_label = self._area_city_label(str(item.get("recommend_city") or city_name or ""))
            if not city_label or re.search(r"[A-Za-z]", city_label):
                continue
            values_by_city.setdefault(city_label, []).append(coords)
        centers: dict[str, tuple[float, float]] = {}
        for city_label, values in values_by_city.items():
            latitudes = sorted(lat for lat, _ in values)
            longitudes = sorted(lon for _, lon in values)
            midpoint = len(values) // 2
            if len(values) % 2:
                centers[city_label] = (latitudes[midpoint], longitudes[midpoint])
            else:
                centers[city_label] = (
                    (latitudes[midpoint - 1] + latitudes[midpoint]) / 2,
                    (longitudes[midpoint - 1] + longitudes[midpoint]) / 2,
                )
        return centers

    def _item_coordinate_pair(self, item: dict[str, Any]) -> tuple[float, float] | None:
        try:
            lat = float(item.get("latitude"))
            lon = float(item.get("longitude"))
        except (TypeError, ValueError):
            return None
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return None
        return lat, lon

    def _coordinate_area_name(
        self,
        item: dict[str, Any],
        city_name: str,
        coordinate_centers: dict[str, tuple[float, float]],
    ) -> str:
        coords = self._item_coordinate_pair(item)
        if coords is None:
            return ""
        city_label = self._area_city_label(str(item.get("recommend_city") or city_name or ""))
        if not city_label or re.search(r"[A-Za-z]", city_label):
            return ""
        center = coordinate_centers.get(city_label)
        if center is None:
            return ""
        lat, lon = coords
        center_lat, center_lon = center
        dlat = lat - center_lat
        dlon = lon - center_lon
        if abs(dlat) < 0.018 and abs(dlon) < 0.018:
            direction = "中心"
        elif abs(dlat) > abs(dlon) * 1.7:
            direction = "北部" if dlat > 0 else "南部"
        elif abs(dlon) > abs(dlat) * 1.7:
            direction = "东部" if dlon > 0 else "西部"
        else:
            direction = ("东" if dlon > 0 else "西") + ("北部" if dlat > 0 else "南部")
        return f"{city_label}{direction}片区"

    def _choice_area_name(self, item: dict[str, Any], city_name: str) -> str:
        choice_city = str(item.get("recommend_city") or city_name or "")
        raw_area = str(item.get("area_name") or "").strip()
        if raw_area and not self._is_generic_area_name(raw_area):
            normalized_raw_area = self._normalize_area_display_name(raw_area, choice_city)
            if normalized_raw_area:
                return normalized_raw_area
        inferred = self._infer_area_name(
            city_name=choice_city,
            hotel_name=" ".join(
                str(value or "")
                for value in (
                    item.get("hotel_original_name"),
                    item.get("hotel_name"),
                )
            ),
            area_text=" ".join(
                str(value or "")
                for value in (
                    item.get("area_hint"),
                    raw_area,
                )
            ),
        )
        return self._normalize_area_display_name(inferred, choice_city)

    def _refresh_choice_area_names(self, choices: list[dict[str, Any]], city_name: str) -> None:
        coordinate_centers = self._coordinate_centers_by_city(choices, city_name)
        for item in choices:
            area_name = self._choice_area_name(item, city_name)
            if not area_name or self._is_generic_area_name(area_name):
                area_name = self._coordinate_area_name(item, city_name, coordinate_centers)
            item["area_name"] = "" if not area_name or self._is_generic_area_name(area_name) else area_name

    def enhance_area_data(self, city_name: str, choices: list[dict[str, Any]]) -> dict[str, Any]:
        enhanced_choices = copy.deepcopy(choices or [])
        geonames_lookups = 0
        geonames_hits = 0
        coordinate_centers = self._coordinate_centers_by_city(enhanced_choices, city_name)
        for item in enhanced_choices:
            choice_city = str(item.get("recommend_city") or city_name or "")
            area_name = ""
            if geonames_lookups < 8 and item.get("latitude") not in ("", None) and item.get("longitude") not in ("", None):
                geonames_lookups += 1
                area_name = self._geonames_area_name(item.get("latitude"), item.get("longitude"), choice_city)
                if area_name:
                    geonames_hits += 1
                    item["area_source"] = "GeoNames"
            if not area_name:
                area_name = self._choice_area_name(item, choice_city)
                if area_name:
                    item["area_source"] = "区域规范化"
            if not area_name or self._is_generic_area_name(area_name):
                area_name = self._coordinate_area_name(item, choice_city, coordinate_centers)
                if area_name:
                    item["area_source"] = "酒店坐标"
            item["area_name"] = "" if not area_name or self._is_generic_area_name(area_name) else area_name

        recommendations = self._build_area_recommendations(enhanced_choices, city_name, include_defaults=False)
        if not recommendations:
            recommendations = self._build_area_recommendations(enhanced_choices, city_name, include_defaults=True)
        return {
            "city": city_name,
            "choices": enhanced_choices,
            "area_recommendations": recommendations,
            "area_refresh": {
                "status": "succeeded",
                "source": "geonames" if geonames_hits else "local",
                "geonames_enabled": bool(self.geonames_username),
                "geonames_lookups": geonames_lookups,
                "geonames_hits": geonames_hits,
            },
        }

    def _add_default_area_recommendations(
        self,
        recommendations: list[dict[str, Any]],
        city_name: str,
        choices: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not choices or len(recommendations) >= 3:
            return recommendations
        city_label = self._area_city_label(city_name)
        defaults = CITY_DEFAULT_AREA_NAMES.get(city_label, ())
        if not defaults:
            return recommendations
        existing = {item["area_name"] for item in recommendations}
        filled = list(recommendations)
        for area_name in defaults:
            if area_name in existing:
                continue
            filled.append(
                {
                    "area_name": area_name,
                    "hotel_count": 0,
                    "lower_price_hotel_count": 0,
                    "slightly_higher_hotel_count": 0,
                    "lower_price_ratio": 0,
                    "average_holiday_nightly_tax_total_value": 0,
                    "average_holiday_nightly_tax_total_price": self._format_cny(0),
                    "average_price_diff_nightly": 0,
                    "average_price_diff_nightly_text": self._format_cny_diff(0),
                    "room_type_labels": [],
                    "representative_hotels": [],
                    "reason": "当前命中酒店不足 3 个具体片区，补充展示该城市常见旅游区域。",
                }
            )
            existing.add(area_name)
            if len(filled) >= 3:
                break
        return filled

    def _area_recommendation_reason(self, hotel_count: int, lower_count: int, avg_diff: int) -> str:
        if lower_count == hotel_count:
            return f"{hotel_count} 家命中酒店假期价格不高于平日代表均价。"
        if lower_count:
            return f"{lower_count} 家酒店假期更低，整体涨幅可控。"
        return f"{hotel_count} 家酒店假期涨幅不超过 100 元/晚。"

    def _format_api_price_text(self, value: Any) -> str:
        price = self._coerce_api_price(value)
        return f"CNY {price:,}" if price is not None else ""

    def _extract_api_total_price_text(self, price_explanation: Any, nightly_tax_price: Any, nights: int) -> str:
        text = str(price_explanation or "")
        match = re.search(r"Total price:\s*CNY\s*[\d,]+", text)
        if match:
            return match.group(0)
        nightly_value = self._coerce_api_price(nightly_tax_price)
        if nightly_value is None:
            return ""
        return f"Total price: CNY {nightly_value * max(1, nights):,}"

    def _coerce_api_price(self, value: Any) -> int | None:
        if value in ("", None):
            return None
        if isinstance(value, (int, float)):
            return round(float(value))
        match = re.search(r"\d+(?:\.\d+)?", str(value).replace(",", ""))
        return round(float(match.group(0))) if match else None

    def _is_placeholder_hotel_name(self, value: str) -> bool:
        return value.strip().lower() in {"hi china", "hi china!"}

    def _extract_hotel_list_snapshot(self, page, limit: int) -> list[dict[str, Any]]:
        extracted = page.evaluate(
            """(limit) => {
                const cards = Array.from(document.querySelectorAll('.hotel-card')).slice(0, limit);
                return cards.map((card) => {
                    const detailNode = card.querySelector(
                        'a.hotelName, .hotel-title a, .list-card-title a, a[href*="/hotels/detail/"]'
                    );
                    const roomPriceNode =
                        card.querySelector('.room-price .sale') ||
                        card.querySelector('.room-price .price-line span:last-child');
                    const totalNode = card.querySelector('.room-price .price-explain');
                    return {
                        hotel_id: card.getAttribute('id') || '',
                        hotel_name: (detailNode?.textContent || '').trim(),
                        detail_href: detailNode?.getAttribute('href') || '',
                        room_name: (card.querySelector('.room-name')?.textContent || '').trim(),
                        room_price_text: (roomPriceNode?.textContent || '').trim(),
                        tax_total_text: (totalNode?.innerText || totalNode?.textContent || '').trim(),
                        raw_text: (card.innerText || '').trim(),
                    };
                });
            }""",
            limit,
        )

        items = self._normalize_hotel_cards(extracted)
        if items:
            return items

        fallback = page.evaluate(
            """(limit) => {
                const anchors = Array.from(document.querySelectorAll('a[href*="/hotels/detail/"]'));
                const seen = new Set();
                const rows = [];
                for (const anchor of anchors) {
                    const href = anchor.getAttribute('href') || '';
                    if (!href || seen.has(href)) {
                        continue;
                    }
                    seen.add(href);
                    let node = anchor.parentElement;
                    let container = null;
                    while (node && node !== document.body) {
                        const text = (node.innerText || '').trim();
                        const cnyCount = (text.match(/CNY/g) || []).length;
                        if (text.includes('Total price') || cnyCount >= 2) {
                            container = node;
                            break;
                        }
                        node = node.parentElement;
                    }
                    rows.push({
                        hotel_id: '',
                        hotel_name: (anchor.textContent || '').trim(),
                        detail_href: href,
                        room_name: '',
                        room_price_text: '',
                        tax_total_text: '',
                        raw_text: (container?.innerText || anchor.innerText || '').trim(),
                    });
                    if (rows.length >= limit) {
                        break;
                    }
                }
                return rows;
            }""",
            limit,
        )
        return self._normalize_hotel_cards(fallback)

    def _advance_hotel_list_scroll(self, page, wait_ms: int = SCROLL_WAIT_MS) -> None:
        page.evaluate(
            """() => {
                const scrollers = Array.from(document.querySelectorAll('div, main, section'))
                    .filter((node) => node.scrollHeight > node.clientHeight + 300)
                    .sort((a, b) => b.scrollHeight - a.scrollHeight);
                const target = scrollers[0];
                if (target) {
                    target.scrollTop = target.scrollHeight;
                }
                window.scrollTo(0, document.body.scrollHeight);
            }"""
        )
        page.mouse.wheel(0, 7000)
        if wait_ms > 0:
            page.wait_for_timeout(wait_ms)

    def _finalize_hotel_items(
        self,
        items: list[dict[str, Any]],
        city_candidate: CityCandidate,
        check_in: dt.date,
        check_out: dt.date,
        feature_filters: FeatureFilters,
    ) -> list[dict[str, Any]]:
        finalized: list[dict[str, Any]] = []
        for item in items:
            detail_url = urljoin("https://www.trip.com", item["detail_href"])
            if not self._to_zh_detail_url(detail_url):
                detail_url = self._build_detail_url_from_ids(
                    city_id=city_candidate.city_id,
                    hotel_id=item["hotel_id"],
                    check_in=check_in,
                    check_out=check_out,
                )
            item["detail_url"] = detail_url
            if self._is_outside_search_city(item, city_candidate, detail_url):
                continue
            item["room_price_value"] = self._extract_price_value(item["room_price_text"])
            item["tax_total_value"] = self._extract_price_value(item["tax_total_text"])
            if not self._apply_feature_filter_context(item, feature_filters):
                continue
            finalized.append(item)
        return finalized

    def _apply_feature_filter_context(self, item: dict[str, Any], feature_filters: FeatureFilters) -> bool:
        if feature_filters.advanced == "yes":
            if item.get("is_advanced") is False:
                return False
        elif feature_filters.advanced == "no" and item.get("is_advanced") is True:
            return False

        if feature_filters.pool == "yes":
            if item.get("has_pool") is False:
                return False
        elif feature_filters.pool == "no" and item.get("has_pool") is True:
            return False

        if feature_filters.child_facility == "yes":
            if item.get("has_child_facility") is False:
                return False
        elif feature_filters.child_facility == "no" and item.get("has_child_facility") is True:
            return False

        return True

    def _excluded_by_negative_feature_filter(self, item: dict[str, Any], feature_filters: FeatureFilters) -> bool:
        if feature_filters.pool == "no" and item.get("has_pool") is True:
            return True
        if feature_filters.child_facility == "no" and item.get("has_child_facility") is True:
            return True
        if feature_filters.advanced == "no" and item.get("is_advanced") is True:
            return True
        return False

    def _normalize_hotel_cards(self, cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for card in cards:
            raw_text = str(card.get("raw_text") or "").strip()
            hotel_name = str(card.get("hotel_name") or "").strip()
            room_name = str(card.get("room_name") or "").strip()
            room_price_text = str(card.get("room_price_text") or "").strip()
            tax_total_text = str(card.get("tax_total_text") or "").strip()
            detail_href = str(card.get("detail_href") or "").strip()

            if not hotel_name and raw_text:
                hotel_name = raw_text.splitlines()[0].strip()

            cny_matches = re.findall(r"CNY\s*[\d,]+", raw_text)
            if not room_price_text and cny_matches:
                room_price_text = cny_matches[0]

            if tax_total_text:
                tax_total_text = tax_total_text.splitlines()[0].strip()
            else:
                total_match = re.search(r"Total price:\s*CNY\s*[\d,]+", raw_text)
                if total_match:
                    tax_total_text = total_match.group(0)
                elif len(cny_matches) >= 2:
                    tax_total_text = f"Total price: {cny_matches[-1]}"

            if not room_name and raw_text:
                lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
                price_lines = set(cny_matches)
                for line in lines:
                    if line == hotel_name or line in price_lines or line.startswith("Total price:"):
                        continue
                    if any(keyword in line for keyword in ("bed", "Bed", "Twin", "King", "Queen", "Room", "房")):
                        room_name = line
                        break

            hotel_id = str(card.get("hotel_id") or "").strip() or self._extract_hotel_id(detail_href)
            if not hotel_id or not hotel_name or not tax_total_text:
                continue

            items.append(
                {
                    "hotel_id": hotel_id,
                    "hotel_name": hotel_name,
                    "detail_href": detail_href,
                    "room_name": room_name,
                    "room_price_text": room_price_text,
                    "tax_total_text": tax_total_text,
                    "has_pool": self._text_has_pool_feature(raw_text.lower()),
                    "has_child_facility": self._text_has_child_feature(raw_text.lower()),
                    "is_advanced": None,
                }
            )
        return items

    def _extract_hotel_id(self, detail_href: str) -> str:
        if not detail_href:
            return ""
        query = parse_qs(urlparse(detail_href).query)
        hotel_ids = query.get("hotelId") or []
        return hotel_ids[0] if hotel_ids else ""

    def _enrich_choices_with_chinese_hotel_names(self, choices: list[dict[str, Any]]) -> None:
        missing: dict[str, str] = {}
        for item in choices:
            hotel_id = str(item.get("hotel_id") or "")
            if not hotel_id:
                continue
            with self._cache_lock:
                cached = self._hotel_name_cache.get(hotel_id)
            if cached is None and hotel_id not in missing:
                missing[hotel_id] = item.get("detail_url") or ""

        if missing:
            cache_changed = False
            with ThreadPoolExecutor(max_workers=CHINESE_NAME_WORKERS) as executor:
                future_map = {
                    executor.submit(self._fetch_trip_hk_chinese_hotel_name, detail_url): hotel_id
                    for hotel_id, detail_url in missing.items()
                }
                for future in as_completed(future_map):
                    hotel_id = future_map[future]
                    try:
                        value = future.result()
                    except Exception:
                        value = {}
                    with self._cache_lock:
                        self._hotel_name_cache[hotel_id] = value
                    cache_changed = True
            if cache_changed:
                self._save_hotel_name_cache()

        for item in choices:
            hotel_id = str(item.get("hotel_id") or "")
            if not hotel_id:
                continue
            with self._cache_lock:
                cached = self._hotel_name_cache.get(hotel_id) or {}
            name = cached.get("hotel_name") or ""
            if name:
                item["hotel_name"] = name
                item["hotel_name_source"] = cached.get("source") or "Trip.com HK"

    def _filter_choices_by_verified_features(
        self,
        choices: list[dict[str, Any]],
        feature_filters: FeatureFilters,
    ) -> list[dict[str, Any]]:
        required = self._required_feature_keys(feature_filters)
        if not required:
            return choices

        missing: dict[str, str] = {}
        for item in choices:
            hotel_id = str(item.get("hotel_id") or "")
            if not hotel_id:
                continue
            cached = self._cached_hotel_feature_flags(hotel_id)
            if cached:
                self._apply_feature_flags(item, cached)
            if any(item.get(key) is not True for key in required):
                missing[hotel_id] = item.get("detail_url") or ""

        if missing:
            cache_changed = False
            with ThreadPoolExecutor(max_workers=FEATURE_VERIFY_WORKERS) as executor:
                future_map = {
                    executor.submit(self._fetch_hotel_detail_feature_flags, detail_url): hotel_id
                    for hotel_id, detail_url in missing.items()
                    if detail_url
                }
                for future in as_completed(future_map):
                    hotel_id = future_map[future]
                    try:
                        flags = future.result()
                    except Exception:
                        flags = {}
                    if not flags:
                        continue
                    with self._cache_lock:
                        self._hotel_feature_cache[hotel_id] = flags
                    cache_changed = True
            if cache_changed:
                self._save_hotel_feature_cache()

        filtered: list[dict[str, Any]] = []
        for item in choices:
            hotel_id = str(item.get("hotel_id") or "")
            if hotel_id:
                cached = self._cached_hotel_feature_flags(hotel_id)
                if cached:
                    self._apply_feature_flags(item, cached)
            if all(item.get(key) is True for key in required):
                filtered.append(item)
        return filtered

    def _required_feature_keys(self, feature_filters: FeatureFilters) -> tuple[str, ...]:
        keys: list[str] = []
        if feature_filters.advanced == "yes":
            keys.append("is_advanced")
        if feature_filters.pool == "yes":
            keys.append("has_pool")
        if feature_filters.child_facility == "yes":
            keys.append("has_child_facility")
        return tuple(keys)

    def _cached_hotel_feature_flags(self, hotel_id: str) -> dict[str, bool]:
        with self._cache_lock:
            cached = copy.deepcopy(self._hotel_feature_cache.get(hotel_id) or {})
        return {
            key: value
            for key, value in cached.items()
            if key in {"is_advanced", "has_pool", "has_child_facility"} and isinstance(value, bool)
        }

    def _apply_feature_flags(self, item: dict[str, Any], flags: dict[str, bool]) -> None:
        for key in ("is_advanced", "has_pool", "has_child_facility"):
            if key in flags:
                item[key] = flags[key]

    def _fetch_hotel_detail_feature_flags(self, detail_url: str) -> dict[str, bool]:
        verify_url = self._to_feature_verify_detail_url(detail_url)
        if not verify_url:
            return {}
        req = Request(
            verify_url,
            headers={
                "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "accept-language": "zh-CN,zh;q=0.9,en;q=0.5",
                "user-agent": UA,
            },
        )
        try:
            with urlopen(req, timeout=18) as resp:
                body = resp.read().decode("utf-8", errors="ignore")
        except (HTTPError, URLError, TimeoutError, OSError):
            return {}
        return self._extract_detail_feature_flags(body)

    def _to_feature_verify_detail_url(self, detail_url: str) -> str:
        if not detail_url:
            return ""
        parsed = urlparse(detail_url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        hotel_ids = query.get("hotelId") or []
        if not hotel_ids:
            return ""
        city_ids = query.get("cityId") or query.get("city") or []
        params = {
            "hotelId": hotel_ids[0],
            "adult": 2,
            "children": 0,
            "crn": 1,
            "curr": "CNY",
            "locale": "zh-CN",
        }
        if city_ids:
            params["cityId"] = city_ids[0]
        for key in ("checkIn", "checkOut"):
            values = query.get(key)
            if values:
                params[key] = values[0]
        return "https://www.trip.com/hotels/detail/?" + urlencode(params)

    def _extract_detail_feature_flags(self, body: str) -> dict[str, bool]:
        text = self._detail_feature_text(body)
        flags: dict[str, bool] = {}

        pool = self._detail_has_pool_feature(text)
        if pool is not None:
            flags["has_pool"] = pool
        child = self._detail_has_child_feature(text)
        if child is not None:
            flags["has_child_facility"] = child
        advanced = self._detail_is_advanced_feature(text)
        if advanced is not None:
            flags["is_advanced"] = advanced
        return flags

    def _detail_feature_text(self, body: str) -> str:
        decoded = html.unescape(body or "")
        decoded = re.sub(r"https?://\S+", " ", decoded)
        decoded = re.sub(r"detailFilters=[^\"'&\s]+", " ", decoded, flags=re.IGNORECASE)
        decoded = re.sub(r"hoteluniquekey=[^\"'&\s]+", " ", decoded, flags=re.IGNORECASE)
        return decoded.lower()

    def _detail_has_pool_feature(self, text: str) -> bool | None:
        if re.search(r"no\s+pool|without\s+pool|无泳池|無泳池|没有泳池|沒有泳池", text, flags=re.IGNORECASE):
            return False
        if re.search(
            r"游泳池|泳池|室内泳池|室外泳池|swimming\s+pool|indoor\s+pool|outdoor\s+pool|heated\s+pool",
            text,
            flags=re.IGNORECASE,
        ):
            return True
        return None

    def _detail_has_child_feature(self, text: str) -> bool | None:
        if re.search(
            r"儿童乐园|兒童樂園|儿童泳池|兒童泳池|亲子|親子|children'?s\s+playground|kids?\s+club|children'?s\s+pool|family-friendly",
            text,
            flags=re.IGNORECASE,
        ):
            return True
        return None

    def _detail_is_advanced_feature(self, text: str) -> bool | None:
        if re.search(r"5-star|4-star|5\s+star|4\s+star|五星|四星|五钻|四钻|五鑽|四鑽|豪华型|豪華型|高档型|高檔型", text):
            return True
        if re.search(r"3-star|2-star|3\s+star|2\s+star|三星|二星|三钻|二钻|三鑽|二鑽|经济型|經濟型", text):
            return False
        return None

    def _fetch_trip_hk_chinese_hotel_name(self, detail_url: str) -> dict[str, str]:
        hk_url = self._to_trip_hk_detail_url(detail_url)
        if not hk_url:
            return {}
        req = Request(
            hk_url,
            headers={
                "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "accept-language": "zh-HK,zh;q=0.9,en;q=0.5",
                "user-agent": UA,
            },
        )
        try:
            with urlopen(req, timeout=18) as resp:
                body = resp.read().decode("utf-8", errors="ignore")
        except (HTTPError, URLError, TimeoutError, OSError):
            return {}
        name = self._extract_trip_hk_chinese_hotel_name(body)
        if not name:
            return {}
        return {"hotel_name": name, "source": "Trip.com HK"}

    def _to_trip_hk_detail_url(self, detail_url: str) -> str:
        if not detail_url:
            return ""
        parsed = urlparse(detail_url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        hotel_ids = query.get("hotelId") or []
        if not hotel_ids:
            return ""
        query["locale"] = ["zh_hk"]
        query["curr"] = ["CNY"]
        if "crn" not in query:
            query["crn"] = ["1"]
        path = parsed.path if "/hotels/detail/" in parsed.path else "/hotels/detail/"
        return parsed._replace(scheme="https", netloc="hk.trip.com", path=path, query=urlencode(query, doseq=True)).geturl()

    def _build_detail_url_from_ids(
        self,
        city_id: int,
        hotel_id: str,
        check_in: dt.date,
        check_out: dt.date,
    ) -> str:
        params = {
            "cityId": city_id,
            "hotelId": hotel_id,
            "checkIn": check_in.isoformat(),
            "checkOut": check_out.isoformat(),
            "adult": 2,
            "children": 0,
            "crn": 1,
            "curr": "CNY",
            "locale": "zh-CN",
        }
        return "https://www.trip.com/hotels/detail/?" + urlencode(params)

    def _extract_trip_hk_chinese_hotel_name(self, body: str) -> str:
        decoded = html.unescape(body)
        patterns = [
            (r'\\"nameLocale\\":\\"([^"\\]+)\\"', "json nameLocale"),
            (r'"nameLocale":"([^"]+)"', "json nameLocale"),
            (r'\\"keywords\\":\\"([^"\\]+)\\"', "seo keywords"),
            (r'"keywords":"([^"]+)"', "seo keywords"),
            (r'\\"title\\":\\"([^"\\]+?)\\s+-\\s+\\d{4}\\s+', "seo title"),
            (r'"title":"([^"]+?)\\s+-\\s+\\d{4}\\s+', "seo title"),
            (r'class="crumbSEO_crumb_content__[^"]*">([^<]+)</span>', "breadcrumb"),
            (r'\\"name\\":\\"([^"\\(]+)(?:\\([^"\\)]*\\))?\\"', "structured data"),
        ]
        for pattern, _source in patterns:
            match = re.search(pattern, decoded)
            if not match:
                continue
            name = self._clean_chinese_hotel_name(match.group(1))
            if self._is_reliable_chinese_hotel_name(name):
                return name
        return ""

    def _clean_chinese_hotel_name(self, value: str) -> str:
        value = html.unescape(value or "")
        if "\\u" in value:
            value = value.encode("utf-8", errors="ignore").decode("unicode_escape", errors="ignore")
        value = re.sub(r"\\/", "/", value)
        value = re.sub(r"\s+", " ", value).strip()
        value = re.sub(r"\s*[-｜|]\s*20\d{2}.*$", "", value).strip()
        value = re.sub(r"\([^)]*[A-Za-z][^)]*\)$", "", value).strip()
        return value

    def _is_reliable_chinese_hotel_name(self, value: str) -> bool:
        if not value or len(value) > 80:
            return False
        if any(token in value for token in ("Trip.com", "訂房", "優惠", "住客評論", "酒店推薦")):
            return False
        return bool(re.search(r"[\u3400-\u9fff]", value))

    def _to_zh_detail_url(self, detail_url: str) -> str:
        if not detail_url:
            return ""
        parsed = urlparse(detail_url)
        if not parsed.path or "/hotels/detail/" not in parsed.path:
            return ""
        query = parse_qs(parsed.query, keep_blank_values=True)
        query["locale"] = ["zh-CN"]
        return parsed._replace(query=urlencode(query, doseq=True)).geturl()

    def _nightly_value(self, total_value: int, nights: int) -> int:
        if nights <= 0:
            return total_value
        return round(total_value / nights)

    def _classify_room_type(self, room_name: str) -> str:
        text = (room_name or "").strip().lower()
        if not text:
            return "unknown"
        twin_patterns = [
            r"\btwin\b",
            r"\b2\s*beds?\b",
            r"\btwo\s*beds?\b",
            r"双床",
            r"两张床",
            r"2张床",
        ]
        king_patterns = [
            r"\bking\b",
            r"\bqueen\b",
            r"\bdouble\s*bed\b",
            r"\bdouble\s*room\b",
            r"\bdouble\b",
            r"大床",
            r"一张床",
            r"1张床",
        ]
        if any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in twin_patterns):
            return "twin"
        if any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in king_patterns):
            return "king"
        return "unknown"

    def _room_type_label(self, room_type: str) -> str:
        if room_type == "king":
            return "大床房"
        if room_type == "twin":
            return "双床房"
        return "未识别房型"

    def _localize_room_name(self, room_name: str) -> str:
        text = (room_name or "").strip()
        if not text:
            return text
        replacements = [
            (r"\bDeluxe\b", "豪华"),
            (r"\bPremier\b", "尊贵"),
            (r"\bSuperior\b", "高级"),
            (r"\bExecutive\b", "行政"),
            (r"\bClassic\b", "经典"),
            (r"\bStandard\b", "标准"),
            (r"\bGuestroom\b", "客房"),
            (r"\bRoom\b", "房"),
            (r"\bSuite\b", "套房"),
            (r"\bKing\b", "大床"),
            (r"\bQueen\b", "大床"),
            (r"\bTwin\b", "双床"),
            (r"\bDouble bed\b", "大床"),
            (r"\bDouble\b", "双人"),
            (r"\bBed\b", "床"),
            (r"\bBund\b", "外滩"),
            (r"\bRiver Wing\b", "江景楼"),
            (r"\bCity View\b", "城景"),
            (r"\bRiver View\b", "江景"),
            (r"\bNo window\b", "无窗"),
            (r"\bSpecial promotion\b", "特惠"),
        ]
        localized = text
        for pattern, replacement in replacements:
            localized = re.sub(pattern, replacement, localized, flags=re.IGNORECASE)
        localized = re.sub(r"\s+", " ", localized).strip(" -(),")
        return localized

    def _build_list_url(
        self,
        city_candidate: CityCandidate,
        check_in: dt.date,
        check_out: dt.date,
        feature_filters: FeatureFilters | None = None,
    ) -> str:
        feature_filters = feature_filters or FeatureFilters()
        params = {
            "city": city_candidate.city_id,
            "cityName": city_candidate.city_name,
            "provinceId": city_candidate.province_id,
            "countryId": city_candidate.country_id,
            "districtId": 0,
            "checkin": check_in.strftime("%Y/%m/%d"),
            "checkout": check_out.strftime("%Y/%m/%d"),
            "lat": city_candidate.lat,
            "lon": city_candidate.lon,
            "searchType": "CT",
            "searchWord": city_candidate.city_name,
            "searchValue": f"{city_candidate.filter_id}*19*{city_candidate.city_id}*1".replace("|", "~"),
            "searchCoordinate": city_candidate.search_coordinate.replace("|", "~"),
            "crn": 1,
            "adult": 2,
            "children": 0,
            "searchBoxArg": "t",
            "travelPurpose": 0,
            "ctm_ref": "ix_sb_dl",
            "domestic": "true",
            "listFilters": self._build_list_filters(feature_filters),
            "locale": "zh-CN",
            "curr": "CNY",
        }
        return "https://www.trip.com/hotels/list?" + urlencode(params)

    def _build_hotel_list_url(
        self,
        city_candidate: CityCandidate,
        check_in: dt.date,
        check_out: dt.date,
        feature_filters: FeatureFilters | None = None,
        keyword_candidate: HotelKeywordCandidate | None = None,
    ) -> str:
        if keyword_candidate is None:
            return self._build_list_url(city_candidate, check_in, check_out, feature_filters)
        return self._build_keyword_list_url(city_candidate, keyword_candidate, check_in, check_out, feature_filters)

    def _build_keyword_list_url(
        self,
        city_candidate: CityCandidate,
        keyword_candidate: HotelKeywordCandidate,
        check_in: dt.date,
        check_out: dt.date,
        feature_filters: FeatureFilters | None = None,
    ) -> str:
        feature_filters = feature_filters or FeatureFilters()
        filter_type = keyword_candidate.filter_id.split("|", 1)[0] or "31"
        params = {
            "city": city_candidate.city_id,
            "cityName": city_candidate.city_name,
            "provinceId": city_candidate.province_id,
            "countryId": city_candidate.country_id,
            "districtId": 0,
            "checkin": check_in.strftime("%Y/%m/%d"),
            "checkout": check_out.strftime("%Y/%m/%d"),
            "lat": keyword_candidate.lat,
            "lon": keyword_candidate.lon,
            "searchType": "H",
            "searchWord": keyword_candidate.title,
            "searchValue": (
                f"{keyword_candidate.filter_id}*{filter_type}*{keyword_candidate.hotel_id}*1".replace("|", "~")
            ),
            "searchCoordinate": keyword_candidate.search_coordinate.replace("|", "~"),
            "crn": 1,
            "adult": 2,
            "children": 0,
            "searchBoxArg": "t",
            "travelPurpose": 0,
            "ctm_ref": "ix_sb_dl",
            "domestic": "true",
            "listFilters": self._build_list_filters(feature_filters),
            "locale": "zh-CN",
            "curr": "CNY",
        }
        return "https://www.trip.com/hotels/list?" + urlencode(params)

    def _build_list_filters(self, feature_filters: FeatureFilters) -> str:
        filters = [item for item in DEFAULT_LIST_FILTERS.split(",") if item]
        if feature_filters.advanced == "yes":
            filters.extend(ADVANCED_YES_FILTERS)
        elif feature_filters.advanced == "no":
            filters.extend(ADVANCED_NO_FILTERS)

        if feature_filters.pool == "yes":
            filters.extend(POOL_YES_FILTERS)
        if feature_filters.child_facility == "yes":
            filters.extend(CHILD_FACILITY_YES_FILTERS)

        deduped: list[str] = []
        seen: set[str] = set()
        for item in filters:
            if item in seen:
                continue
            seen.add(item)
            deduped.append(item)
        return ",".join(deduped)

    def _extract_price_value(self, text: str) -> int:
        match = re.search(r"CNY\s*([\d,]+)", text)
        if not match:
            match = re.search(r"(\d[\d,]*)", text.replace(".00", ""))
        if not match:
            return 0
        return int(match.group(1).replace(",", ""))

    def _format_cny(self, value: int) -> str:
        return f"CNY {value}"

    def _format_cny_diff(self, value: int) -> str:
        sign = "+" if value > 0 else ""
        return f"{sign}CNY {value}"

    def _trace_id(self) -> str:
        prefix = str(random.randint(1_000_000_000, 1_999_999_999))
        millis = int(dt.datetime.now().timestamp() * 1000)
        suffix = random.randint(1_000_000_000, 1_999_999_999)
        return f"{prefix}-{millis}-{suffix}"
