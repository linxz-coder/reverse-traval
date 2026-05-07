import datetime as dt

from holiday_helper import HolidayRange
from reverse_travel import (
    CityCandidate,
    FeatureFilters,
    HotelKeywordCandidate,
    ReverseTravelFinder,
    ReverseTravelFinderError,
)


class StubCalendar:
    def __init__(self):
        self.days = {
            dt.date(2026, 5, 1),
            dt.date(2026, 5, 2),
            dt.date(2026, 5, 3),
        }

    def get_upcoming_holidays(self):
        return [
            HolidayRange(
                code="2026-05-01::劳动节",
                name="劳动节",
                start=dt.date(2026, 5, 1),
                end=dt.date(2026, 5, 3),
                days=3,
            )
        ]

    def is_statutory_holiday(self, day):
        return day in self.days


def test_build_compare_windows_skips_statutory_holidays():
    finder = ReverseTravelFinder(StubCalendar())
    holiday = finder._get_holiday("2026-05-01::劳动节")
    windows = finder._build_compare_windows(holiday)

    assert windows
    assert windows[0]["check_in"] == dt.date(2026, 5, 4)
    assert windows[0]["check_out"] == dt.date(2026, 5, 7)
    assert len(windows) <= 8
    assert any(window["check_in"].weekday() < 5 for window in windows)
    assert any(window["check_in"].weekday() >= 5 for window in windows)
    for window in windows:
        assert not finder.calendar.is_statutory_holiday(window["check_in"])


def test_build_all_compare_windows_keeps_full_month():
    finder = ReverseTravelFinder(StubCalendar())
    holiday = finder._get_holiday("2026-05-01::劳动节")
    windows = finder._build_all_compare_windows(holiday)

    assert len(windows) == 30
    assert windows[0]["check_in"] == dt.date(2026, 5, 4)
    assert windows[-1]["check_in"] == dt.date(2026, 6, 2)


def test_chunked_keeps_all_items():
    finder = ReverseTravelFinder(StubCalendar())

    assert finder._chunked(list(range(9)), 4) == [[0, 1, 2, 3], [4, 5, 6, 7], [8]]


def test_extract_price_value():
    finder = ReverseTravelFinder(StubCalendar())
    assert finder._extract_price_value("CNY 1,399") == 1399
    assert finder._extract_price_value("Total (incl. taxes & fees): CNY 212") == 212
    assert finder._nightly_value(1668, 5) == 334
    assert finder._format_cny(651) == "CNY 651"
    assert finder._format_cny_diff(64) == "+CNY 64"
    assert finder._format_cny_diff(-17) == "CNY -17"


def test_build_list_filters_defaults_to_all_and_supports_options():
    finder = ReverseTravelFinder(StubCalendar())

    default_filters = finder._build_list_filters(FeatureFilters())
    assert "16~4*16*4*4" not in default_filters
    assert "3~605*3*605*Pool" not in default_filters

    selected_filters = finder._build_list_filters(
        FeatureFilters(advanced="yes", pool="yes", child_facility="yes")
    )
    assert "16~4*16*4*4" in selected_filters
    assert "16~5*16*5*5" in selected_filters
    assert "3~605*3*605*Pool" in selected_filters
    assert "3~68*3*68*Playground" in selected_filters

    non_advanced_filters = finder._build_list_filters(FeatureFilters(advanced="no"))
    assert "16~2*16*2*≤2" in non_advanced_filters
    assert "16~3*16*3*3" in non_advanced_filters
    assert "16~4*16*4*4" not in non_advanced_filters


def test_normalize_feature_filters_accepts_chinese_tri_state():
    finder = ReverseTravelFinder(StubCalendar())
    filters = finder._normalize_feature_filters(
        advanced_filter="是",
        pool_filter="否",
        child_facility_filter="全部",
    )

    assert filters == FeatureFilters(advanced="yes", pool="no", child_facility="all")


def test_selected_yes_filters_confirm_features_and_reject_explicit_negative():
    finder = ReverseTravelFinder(StubCalendar())
    item = {"is_advanced": None, "has_pool": None, "has_child_facility": None}

    assert finder._apply_feature_filter_context(
        item,
        FeatureFilters(advanced="yes", pool="yes", child_facility="yes"),
    ) is True
    assert item == {"is_advanced": None, "has_pool": None, "has_child_facility": None}
    assert finder._apply_feature_filter_context(
        {"is_advanced": False, "has_pool": True, "has_child_facility": True},
        FeatureFilters(advanced="yes", pool="yes", child_facility="yes"),
    ) is False
    assert finder._text_has_pool_feature("no pool available") is False
    assert finder._text_has_pool_feature("indoor swimming pool") is True
    assert finder._text_has_pool_feature("business center") is None


def test_verified_feature_filter_removes_unconfirmed_pool_hotels(tmp_path):
    class FeatureFinder(ReverseTravelFinder):
        def _fetch_hotel_detail_feature_flags(self, detail_url):
            if "vienna" in detail_url:
                return {"is_advanced": True, "has_pool": False, "has_child_facility": True}
            return {"is_advanced": True, "has_pool": True, "has_child_facility": True}

    finder = FeatureFinder(StubCalendar(), cache_dir=tmp_path)
    choices = [
        {
            "hotel_id": "1",
            "hotel_name": "維也納酒店",
            "detail_url": "https://www.trip.com/hotels/detail/?hotelId=1&cityId=553&checkIn=2026-06-19&checkOut=2026-06-22&tag=vienna",
            "is_advanced": None,
            "has_pool": None,
            "has_child_facility": None,
        },
        {
            "hotel_id": "2",
            "hotel_name": "确认有泳池酒店",
            "detail_url": "https://www.trip.com/hotels/detail/?hotelId=2&cityId=553&checkIn=2026-06-19&checkOut=2026-06-22",
            "is_advanced": None,
            "has_pool": None,
            "has_child_facility": None,
        },
    ]

    filtered = finder._filter_choices_by_verified_features(
        choices,
        FeatureFilters(advanced="yes", pool="yes", child_facility="yes"),
    )

    assert [item["hotel_id"] for item in filtered] == ["2"]
    assert filtered[0]["has_pool"] is True


def test_detail_feature_url_strips_list_filter_params():
    finder = ReverseTravelFinder(StubCalendar())
    url = finder._to_feature_verify_detail_url(
        "https://www.trip.com/hotels/detail/?cityId=553&hotelId=133560029&checkIn=2026-06-19&checkOut=2026-06-22&detailFilters=3%7C605~Pool&hoteluniquekey=abc"
    )

    assert "hotelId=133560029" in url
    assert "cityId=553" in url
    assert "detailFilters" not in url
    assert "hoteluniquekey" not in url


def test_infer_area_name_is_city_specific():
    finder = ReverseTravelFinder(StubCalendar())

    assert finder._infer_area_name(
        city_name="廣州",
        hotel_name="Hyatt Guangzhou Zengcheng",
        area_text="Zengcheng",
    ) == "广州增城片区"
    assert finder._infer_area_name(
        city_name="Guangzhou",
        hotel_name="Pazhou Canton Fair Hotel",
        area_text="Pazhou Convention and Exhibition Center",
    ) == "广州琶洲会展片区"
    assert finder._infer_area_name(
        city_name="广州",
        hotel_name="Shenzhen World Exhibition Hotel",
        area_text="World Exhibition & Convention Center",
    ) != "深圳国际会展中心片区"
    assert finder._infer_area_name(
        city_name="東莞",
        hotel_name="Dongguan Modern International Exhibition Center Hotel",
        area_text="Houjie Convention and Exhibition Center",
    ) == "东莞厚街会展片区"
    assert finder._infer_area_name(
        city_name="Dongguan",
        hotel_name="Songshan Lake Hotel",
        area_text="Songshan Lake",
    ) == "东莞松山湖片区"
    assert finder._infer_area_name(
        city_name="東莞",
        hotel_name="Dongguan Convention Center Hotel",
        area_text="會展中心",
    ) != "深圳国际会展中心片区"
    assert finder._infer_area_name(
        city_name="深圳",
        hotel_name="Shenzhen World Exhibition Hotel",
        area_text="World Exhibition & Convention Center",
    ) == "深圳国际会展中心片区"
    assert finder._infer_area_name(
        city_name="Shanghai",
        hotel_name="Shenzhen World Exhibition Hotel",
        area_text="World Exhibition & Convention Center",
    ) == "Shanghai区域待确认"
    assert finder._infer_area_name(
        city_name="Bangkok",
        hotel_name="Sheraton Grande Sukhumvit Bangkok",
        area_text="Sukhumvit | Near Asok Metro Station",
    ) == "曼谷素坤逸片区"
    assert finder._infer_area_name(
        city_name="吉隆坡",
        hotel_name="Aloft Kuala Lumpur Sentral",
        area_text="KL Sentral | Near Parkson KL Sentral",
    ) == "吉隆坡中央车站片区"
    assert finder._infer_area_name(
        city_name="Chicago",
        hotel_name="Residence Inn Chicago Downtown/Loop",
        area_text="",
    ) == "芝加哥Loop片区"
    assert finder._infer_area_name(
        city_name="Paris",
        hotel_name="Mercure Paris Montmartre Sacre Coeur",
        area_text="",
    ) == "巴黎蒙马特片区"
    assert finder._infer_area_name(
        city_name="London",
        hotel_name="Example Hotel",
        area_text="London City Centre | Near British Museum",
    ) == "伦敦市中心片区"
    assert finder._infer_area_name(
        city_name="Singapore",
        hotel_name="Example Hotel",
        area_text="Orchard Road | Near ION Orchard",
    ) == "新加坡乌节路片区"
    assert finder._infer_area_name(
        city_name="Rome",
        hotel_name="Example Hotel",
        area_text="Trastevere | Near Piazza di Santa Maria",
    ) == "罗马Trastevere片区"
    assert finder._infer_area_name(
        city_name="Huizhou",
        hotel_name="Hampton by Hilton Huizhou Zhongkai Hi-Tech Zone",
        area_text="Zhongkai TCL Technology Building",
    ) == "惠州仲恺片区"
    assert finder._infer_area_name(
        city_name="Boluo",
        hotel_name="Vienna International Hotel Huizhou Boluo Shiwan",
        area_text="Shiwan",
    ) == "惠州博罗片区"
    assert finder._infer_area_name(
        city_name="惠州",
        hotel_name="Double Moon Bay Resort",
        area_text="Shuangyue Bay",
    ) == "惠州双月湾片区"
    assert finder._infer_area_name(
        city_name="Huizhou",
        hotel_name="Shenzhen World Exhibition Hotel",
        area_text="World Exhibition & Convention Center",
    ) == "惠州区域待确认"
    assert finder._infer_area_name(
        city_name="中山",
        hotel_name="Zhongshan Lihe Hilton Hotel",
        area_text="Lihe Plaza East District",
    ) == "中山东区片区"
    assert finder._infer_area_name(
        city_name="Zhongshan",
        hotel_name="Xiaolan Hotel",
        area_text="小榄镇",
    ) == "中山小榄片区"
    assert finder._infer_area_name(
        city_name="Jiangmen",
        hotel_name="Heshan Wanda Realm Hotel",
        area_text="Heshan",
    ) == "江门鹤山片区"
    assert finder._infer_area_name(
        city_name="江門",
        hotel_name="赤坎古镇巢栖亲子酒店",
        area_text="开平赤坎古镇",
    ) == "江门开平赤坎片区"
    assert finder._infer_area_name(
        city_name="Heyuan",
        hotel_name="河源巴伐利亚庄园福朋喜来登度假酒店",
        area_text="巴伐利亚庄园",
    ) == "河源巴伐利亚庄园片区"
    assert finder._infer_area_name(
        city_name="河源",
        hotel_name="万绿湖东方国际酒店",
        area_text="万绿湖风景区",
    ) == "河源万绿湖片区"
    assert finder._infer_area_name(
        city_name="Zhaoqing",
        hotel_name="肇庆星湖丽芮酒店",
        area_text="七星岩牌坊",
    ) == "肇庆七星岩星湖片区"
    assert finder._infer_area_name(
        city_name="肇庆",
        hotel_name="肇庆喜来登酒店",
        area_text="鼎湖山风景区",
    ) == "肇庆鼎湖山片区"
    assert finder._infer_area_name(
        city_name="Zhuhai",
        hotel_name="珠海横琴长隆迎海酒店公寓",
        area_text="长隆海洋王国",
    ) == "珠海横琴长隆片区"
    assert finder._infer_area_name(
        city_name="珠海",
        hotel_name="珠海情侣路海滨泳场酒店",
        area_text="日月贝",
    ) == "珠海情侣路香洲片区"
    assert finder._infer_area_name(
        city_name="Shaoguan",
        hotel_name="韶关经律论文化旅游小镇酒店",
        area_text="南华寺曹溪温泉",
    ) == "韶关南华寺曹溪片区"
    assert finder._infer_area_name(
        city_name="韶关",
        hotel_name="丹霞山景区酒店",
        area_text="仁化丹霞山",
    ) == "韶关丹霞山片区"
    assert finder._infer_area_name(
        city_name="韶关",
        hotel_name="韶关摩尔城假日酒店",
        area_text="",
    ) == "韶关市区片区"
    assert finder._infer_area_name(
        city_name="韶关",
        hotel_name="韶关经律论国际酒店",
        area_text="",
    ) == "韶关曲江片区"
    assert finder._infer_area_name(
        city_name="Shanwei",
        hotel_name="汕尾保利金町湾酒店",
        area_text="金町湾旅游度假区",
    ) == "汕尾金町湾片区"
    assert finder._infer_area_name(
        city_name="汕尾",
        hotel_name="汕尾红海湾酒店",
        area_text="遮浪半岛",
    ) == "汕尾红海湾片区"


def test_outside_search_city_filter_uses_detail_url_and_city_keywords():
    finder = ReverseTravelFinder(StubCalendar())
    city = CityCandidate(
        city_id=316,
        city_name="江門",
        province_id=23,
        country_id=1,
        lat=22.58,
        lon=113.08,
        filter_id="",
        search_coordinate="",
    )

    assert finder._is_outside_search_city(
        {"hotel_name": "佛山九江希尔顿惠庭酒店", "area_hint": ""},
        city,
        "https://www.trip.com/hotels/detail/?cityEnName=Foshan&cityId=251&hotelId=102227300",
    )
    assert finder._is_outside_search_city(
        {"hotel_name": "中山大信酒店（小榄店）", "area_hint": ""},
        city,
        "",
    )
    assert not finder._is_outside_search_city(
        {"hotel_name": "鹤山万达嘉华酒店", "area_hint": ""},
        city,
        "",
    )


def test_outside_search_city_filter_uses_city_id_for_yunfu():
    finder = ReverseTravelFinder(StubCalendar())
    city = CityCandidate(
        city_id=552,
        city_name="肇慶",
        province_id=23,
        country_id=1,
        lat=23.05,
        lon=112.46,
        filter_id="",
        search_coordinate="",
    )

    assert finder._is_outside_search_city(
        {"hotel_name": "雲浮雲安鳳悅假日酒店（雲安區店）", "area_hint": ""},
        city,
        "https://www.trip.com/hotels/detail/?cityId=3933&hotelId=122458846",
    )


def test_outside_search_city_filter_detects_macau_for_zhuhai():
    finder = ReverseTravelFinder(StubCalendar())
    city = CityCandidate(
        city_id=31,
        city_name="珠海",
        province_id=23,
        country_id=1,
        lat=22.27,
        lon=113.57,
        filter_id="",
        search_coordinate="",
    )

    assert finder._is_outside_search_city(
        {"hotel_name": "澳門威尼斯人", "area_hint": ""},
        city,
        "https://www.trip.com/hotels/detail/?cityEnName=Macau&cityId=59&hotelId=123",
    )
    assert not finder._is_outside_search_city(
        {"hotel_name": "珠海横琴口岸酒店", "area_hint": "近澳门"},
        city,
        "",
    )


def test_outside_search_city_filter_detects_qingyuan_for_shaoguan():
    finder = ReverseTravelFinder(StubCalendar())
    city = CityCandidate(
        city_id=422,
        city_name="韶关",
        province_id=23,
        country_id=1,
        lat=24.81,
        lon=113.59,
        filter_id="",
        search_coordinate="",
    )

    assert finder._is_outside_search_city(
        {"hotel_name": "清远英德奥园希尔顿逸林度假酒店", "area_hint": ""},
        city,
        "https://www.trip.com/hotels/detail/?cityEnName=Qingyuan&hotelId=123",
    )
    assert not finder._is_outside_search_city(
        {"hotel_name": "韶关丹霞山酒店", "area_hint": "仁化丹霞山"},
        city,
        "",
    )


def test_outside_search_city_filter_detects_huizhou_for_shanwei():
    finder = ReverseTravelFinder(StubCalendar())
    city = CityCandidate(
        city_id=1391,
        city_name="汕尾",
        province_id=23,
        country_id=1,
        lat=22.79,
        lon=115.37,
        filter_id="",
        search_coordinate="",
    )

    assert finder._is_outside_search_city(
        {"hotel_name": "惠東雙月灣君廷度假酒店", "area_hint": ""},
        city,
        "",
    )
    assert not finder._is_outside_search_city(
        {"hotel_name": "汕尾金町湾酒店", "area_hint": "保利金町湾"},
        city,
        "",
    )


def test_keyword_supplement_uses_city_specific_hotel_search():
    finder = ReverseTravelFinder(StubCalendar())
    city = CityCandidate(
        city_id=32,
        city_name="廣州",
        province_id=23,
        country_id=1,
        lat=23.137464,
        lon=113.325062,
        filter_id="19|32",
        search_coordinate="NORMAL_23.137464_113.325062_0",
    )
    candidate = HotelKeywordCandidate(
        hotel_id="78218905",
        title="Hyatt Regency Guangzhou Zengcheng",
        filter_id="31|78218905",
        lat=23.14911,
        lon=113.610375,
        search_coordinate="NORMAL_23.14911_113.610375_2",
    )

    assert finder._supplement_keywords("广州", city)[0] == "广州增城凯悦酒店"
    url = finder._build_keyword_list_url(
        city,
        candidate,
        dt.date(2026, 5, 1),
        dt.date(2026, 5, 6),
        FeatureFilters(),
    )

    assert "searchType=H" in url
    assert "searchValue=31~78218905%2A31%2A78218905%2A1" in url
    assert "Hyatt+Regency+Guangzhou+Zengcheng" in url


def test_hotel_keyword_candidate_from_result_keeps_same_city():
    finder = ReverseTravelFinder(StubCalendar())
    city = CityCandidate(
        city_id=32,
        city_name="廣州",
        province_id=23,
        country_id=1,
        lat=23.137464,
        lon=113.325062,
        filter_id="19|32",
        search_coordinate="NORMAL_23.137464_113.325062_0",
    )

    candidate = finder._hotel_keyword_candidate_from_result(
        {
            "resultType": "H",
            "code": "78218905",
            "city": {"currentLocaleName": "廣州", "enusName": "Guangzhou", "geoCode": 32},
            "item": {
                "data": {
                    "filterID": "31|78218905",
                    "title": "廣州增城凱悦酒店",
                    "value": "78218905",
                },
                "extra": {"formattedCoordinateInfo": "23.14911|113.610375|2"},
            },
        },
        city,
    )

    assert candidate == HotelKeywordCandidate(
        hotel_id="78218905",
        title="廣州增城凱悦酒店",
        filter_id="31|78218905",
        lat=23.14911,
        lon=113.610375,
        search_coordinate="NORMAL_23.14911_113.610375_2",
    )


def test_merge_hotel_lists_keeps_room_types_and_lower_price():
    finder = ReverseTravelFinder(StubCalendar())
    merged = finder._merge_hotel_lists(
        [
            {
                "hotel_id": "78218905",
                "hotel_name": "Hyatt Regency Guangzhou Zengcheng",
                "room_name": "Hyatt Queen Room",
                "tax_total_value": 2915,
            }
        ],
        [
            {
                "hotel_id": "78218905",
                "hotel_name": "Hyatt Regency Guangzhou Zengcheng",
                "room_name": "Hyatt Queen Room",
                "tax_total_value": 2800,
            },
            {
                "hotel_id": "78218905",
                "hotel_name": "Hyatt Regency Guangzhou Zengcheng",
                "room_name": "Twin Beds Room",
                "tax_total_value": 3100,
            },
        ],
    )

    assert [(item["room_name"], item["tax_total_value"]) for item in merged] == [
        ("Hyatt Queen Room", 2800),
        ("Twin Beds Room", 3100),
    ]


def test_classify_room_type():
    finder = ReverseTravelFinder(StubCalendar())
    assert finder._classify_room_type("Deluxe King Room") == "king"
    assert finder._classify_room_type("Guestroom (Double bed)") == "king"
    assert finder._classify_room_type("Superior Twin Room") == "twin"
    assert finder._classify_room_type("Selected Deluxe Room (2 beds)") == "twin"
    assert finder._classify_room_type("Deluxe Room") == "unknown"


def test_extract_trip_hk_chinese_hotel_name():
    finder = ReverseTravelFinder(StubCalendar())
    body = (
        r'\"seoTdk\":{\"title\":\"深圳東海朗廷酒店 - 2026 深圳酒店訂房人氣優惠及住客評論｜Trip.com\",'
        r'\"keywords\":\"深圳東海朗廷酒店\"},'
        r'\"nameInfo\":{\"name\":\"深圳東海朗廷酒店(The Langham, Shenzhen)\",'
        r'\"nameEn\":\"The Langham, Shenzhen\",\"nameLocale\":\"深圳東海朗廷酒店\"}'
    )

    assert finder._extract_trip_hk_chinese_hotel_name(body) == "深圳東海朗廷酒店"


def test_build_detail_url_from_ids_and_reject_invalid_detail_url():
    finder = ReverseTravelFinder(StubCalendar())
    assert finder._to_zh_detail_url("https://www.trip.com") == ""

    detail_url = finder._build_detail_url_from_ids(
        city_id=30,
        hotel_id="374623",
        check_in=dt.date(2026, 5, 1),
        check_out=dt.date(2026, 5, 6),
    )

    assert "cityId=30" in detail_url
    assert "hotelId=374623" in detail_url
    assert finder._to_trip_hk_detail_url(detail_url).startswith("https://hk.trip.com/hotels/detail/")


def test_normalize_hotel_cards_fallback_text():
    finder = ReverseTravelFinder(StubCalendar())
    items = finder._normalize_hotel_cards(
        [
            {
                "hotel_id": "",
                "hotel_name": "Yunrui Hotel, Zhongshan Park, Shanghai",
                "detail_href": "/hotels/detail/?cityId=2&hotelId=686139&checkIn=2026-05-01&checkOut=2026-05-06",
                "raw_text": "\n".join(
                    [
                        "Yunrui Hotel, Zhongshan Park, Shanghai",
                        "Guestroom (Double bed) (Special promotion, no window)",
                        "CNY 315",
                        "Total price: CNY 1,668",
                        "1 room × 5 nights incl. taxes & fees",
                    ]
                ),
            }
        ]
    )

    assert items == [
        {
            "hotel_id": "686139",
            "hotel_name": "Yunrui Hotel, Zhongshan Park, Shanghai",
            "detail_href": "/hotels/detail/?cityId=2&hotelId=686139&checkIn=2026-05-01&checkOut=2026-05-06",
            "room_name": "Guestroom (Double bed) (Special promotion, no window)",
            "room_price_text": "CNY 315",
            "tax_total_text": "Total price: CNY 1,668",
            "has_pool": None,
            "has_child_facility": None,
            "is_advanced": None,
        }
    ]


def test_normalize_hotel_api_items_uses_total_tax_price():
    finder = ReverseTravelFinder(StubCalendar())
    items = finder._normalize_hotel_api_items(
        [
            {
                "hotelBasicInfo": {
                    "hotelId": 109336017,
                    "hotelName": "InterContinental Hotels SHENZHEN WECC by IHG",
                    "price": 531,
                    "onlineTaxPrice": "619",
                    "priceExplanation": "Total price: CNY 3,094\n1 room × 5 nights incl. taxes & fees",
                },
                "roomInfo": {"physicalRoomName": "Deluxe Queen Room"},
                "minRoomInfo": {"roomName": "Fallback Room"},
                "positionInfo": {"cityName": "Shenzhen", "positionName": "Shenzhen World Exhibition & Convention Center"},
            },
            {
                "hotelBasicInfo": {
                    "hotelId": 110656684,
                    "hotelName": "EVEN Hotel SHENZHEN GUANGMING CLOUD PARK by IHG",
                    "price": "298",
                    "onlineTaxPrice": "348",
                },
                "roomInfo": {},
                "minRoomInfo": {"roomName": "Superior 2-bed Room"},
                "positionInfo": {"cityName": "Shenzhen", "positionName": "Guangming"},
            },
        ],
        nights=5,
    )

    assert items == [
        {
            "hotel_id": "109336017",
            "hotel_name": "InterContinental Hotels SHENZHEN WECC by IHG",
            "detail_href": "",
            "room_name": "Deluxe Queen Room",
            "room_price_text": "CNY 531",
            "tax_total_text": "Total price: CNY 3,094",
            "area_name": "深圳国际会展中心片区",
            "area_hint": "InterContinental Hotels SHENZHEN WECC by IHG Shenzhen World Exhibition & Convention Center Shenzhen",
            "area_source": "Trip.com 位置",
            "has_pool": None,
            "has_child_facility": None,
            "is_advanced": None,
            "_source": "api",
        },
        {
            "hotel_id": "110656684",
            "hotel_name": "EVEN Hotel SHENZHEN GUANGMING CLOUD PARK by IHG",
            "detail_href": "",
            "room_name": "Superior 2-bed Room",
            "room_price_text": "CNY 298",
            "tax_total_text": "Total price: CNY 1,740",
            "area_name": "光明虹桥公园片区",
            "area_hint": "EVEN Hotel SHENZHEN GUANGMING CLOUD PARK by IHG Guangming Shenzhen",
            "area_source": "Trip.com 位置",
            "has_pool": None,
            "has_child_facility": None,
            "is_advanced": None,
            "_source": "api",
        },
    ]


def test_build_area_recommendations_prioritizes_discount_areas():
    finder = ReverseTravelFinder(StubCalendar())
    recommendations = finder._build_area_recommendations(
        [
            {
                "area_name": "深圳国际会展中心片区",
                "hotel_name": "深圳国际会展中心洲际酒店",
                "holiday_avg_nightly_tax_total_value": 619,
                "price_diff_nightly": -100,
                "room_type_label": "大床房",
            },
            {
                "area_name": "深圳国际会展中心片区",
                "hotel_name": "深圳国际会展中心皇冠假日酒店",
                "holiday_avg_nightly_tax_total_value": 552,
                "price_diff_nightly": 50,
                "room_type_label": "双床房",
            },
            {
                "area_name": "光明虹桥公园片区",
                "hotel_name": "深圳光明美爵酒店",
                "holiday_avg_nightly_tax_total_value": 554,
                "price_diff_nightly": 80,
                "room_type_label": "大床房",
            },
        ],
        "深圳",
    )

    assert recommendations[0]["area_name"] == "深圳国际会展中心片区"
    assert recommendations[0]["hotel_count"] == 2
    assert recommendations[0]["lower_price_hotel_count"] == 1
    assert recommendations[0]["average_price_diff_nightly_text"] == "CNY -25"


def test_build_area_recommendations_removes_generic_area_names():
    finder = ReverseTravelFinder(StubCalendar())
    recommendations = finder._build_area_recommendations(
        [
            {
                "area_name": "中山区域待确认",
                "hotel_name": "中山利和希尔顿酒店",
                "hotel_original_name": "Hilton Zhongshan Downtown",
                "area_hint": "Lihe Plaza East District Zhongshan",
                "holiday_avg_nightly_tax_total_value": 500,
                "price_diff_nightly": 20,
                "room_type_label": "大床房",
            },
            {
                "area_name": "中山区域待确认",
                "hotel_name": "中山小榄假日酒店",
                "area_hint": "小榄镇",
                "holiday_avg_nightly_tax_total_value": 300,
                "price_diff_nightly": -10,
                "room_type_label": "双床房",
            },
        ],
        "中山",
    )

    assert [item["area_name"] for item in recommendations] == ["中山小榄片区", "中山东区片区", "中山石岐片区"]
    assert len(recommendations) >= 3
    assert all("热门酒店片区" not in item["area_name"] for item in recommendations)
    assert all("区域待确认" not in item["area_name"] for item in recommendations)


def test_build_area_recommendations_supports_global_city_areas():
    finder = ReverseTravelFinder(StubCalendar())
    recommendations = finder._build_area_recommendations(
        [
            {
                "area_name": "",
                "area_hint": "Sukhumvit | Near Asok Metro Station",
                "hotel_name": "Sheraton Grande Sukhumvit Bangkok",
                "hotel_original_name": "Sheraton Grande Sukhumvit Bangkok",
                "holiday_avg_nightly_tax_total_value": 1356,
                "price_diff_nightly": -2,
                "room_type_label": "大床房",
            },
            {
                "area_name": "",
                "area_hint": "Riverside | Near Asiatique Sky",
                "hotel_name": "Ibis Bangkok Riverside",
                "hotel_original_name": "Ibis Bangkok Riverside",
                "holiday_avg_nightly_tax_total_value": 454,
                "price_diff_nightly": 35,
                "room_type_label": "大床房",
            },
            {
                "area_name": "",
                "area_hint": "Pratunam Market | Near Central World",
                "hotel_name": "Centara Watergate Pavilion Hotel Bangkok",
                "hotel_original_name": "Centara Watergate Pavilion Hotel Bangkok",
                "holiday_avg_nightly_tax_total_value": 390,
                "price_diff_nightly": 4,
                "room_type_label": "双床房",
            },
        ],
        "Bangkok",
    )

    assert {item["area_name"] for item in recommendations[:3]} == {
        "曼谷素坤逸片区",
        "曼谷湄南河畔片区",
        "曼谷水门片区",
    }


def test_build_area_recommendations_fills_common_global_city_defaults():
    finder = ReverseTravelFinder(StubCalendar())
    recommendations = finder._build_area_recommendations(
        [
            {
                "area_name": "",
                "area_hint": "",
                "hotel_name": "Example London Hotel",
                "hotel_original_name": "Example London Hotel",
                "holiday_avg_nightly_tax_total_value": 1200,
                "price_diff_nightly": 0,
                "room_type_label": "大床房",
            }
        ],
        "London",
    )

    assert [item["area_name"] for item in recommendations[:3]] == [
        "伦敦西区片区",
        "伦敦市中心片区",
        "伦敦国王十字片区",
    ]


def test_refresh_choice_area_names_hides_unresolved_generic_area():
    finder = ReverseTravelFinder(StubCalendar())
    choices = [
        {
            "area_name": "中山区域待确认",
            "hotel_name": "无法识别区域酒店",
            "hotel_original_name": "Unknown Area Hotel",
            "area_hint": "",
        },
        {
            "area_name": "中山区域待确认",
            "hotel_name": "中山利和希尔顿酒店",
            "hotel_original_name": "Hilton Zhongshan Downtown",
            "area_hint": "Lihe Plaza",
        },
    ]

    finder._refresh_choice_area_names(choices, "中山")

    assert choices[0]["area_name"] == ""
    assert choices[1]["area_name"] == "中山东区片区"


def test_find_choices_uses_memory_and_disk_cache(tmp_path):
    class CachedFinder(ReverseTravelFinder):
        def __init__(self):
            super().__init__(StubCalendar(), cache_dir=tmp_path, search_cache_ttl_seconds=3600)
            self.calls = 0

        def _find_choices_base(self, city, holiday_code, feature_filters):
            self.calls += 1
            return {
                "city": "深圳",
                "holiday": {
                    "code": holiday_code,
                    "name": "劳动节",
                    "check_in": "2026-05-01",
                    "check_out": "2026-05-04",
                    "days": 3,
                },
                "price_filter": {"min_price": None, "max_price": None},
                "feature_filters": feature_filters.to_response(),
                "comparison_windows": [{"check_in": "2026-05-04", "check_out": "2026-05-07"}],
                "area_recommendations": [],
                "choices": [
                    {
                        "hotel_id": "1",
                        "hotel_name": "深圳缓存酒店A",
                        "area_name": "深圳国际会展中心片区",
                        "holiday_avg_nightly_tax_total_value": 500,
                        "price_diff_nightly": -20,
                        "room_type_label": "大床房",
                    },
                    {
                        "hotel_id": "2",
                        "hotel_name": "深圳缓存酒店B",
                        "area_name": "深圳宝安片区",
                        "holiday_avg_nightly_tax_total_value": 900,
                        "price_diff_nightly": 30,
                        "room_type_label": "双床房",
                    },
                ],
            }

    finder = CachedFinder()
    first = finder.find_choices("深圳", "2026-05-01::劳动节", None, None, "yes", "yes", "all")
    assert finder.calls == 1
    assert first["cache"]["source"] == "live"
    assert first["cache"]["hit"] is False

    second = finder.find_choices("深圳", "2026-05-01::劳动节", None, 600, "yes", "yes", "all")
    assert finder.calls == 1
    assert second["cache"]["source"] == "memory"
    assert second["cache"]["hit"] is True
    assert [item["hotel_id"] for item in second["choices"]] == ["1"]

    forced = finder.find_choices("深圳", "2026-05-01::劳动节", None, None, "yes", "yes", "all", use_cache=False)
    assert finder.calls == 2
    assert forced["cache"]["source"] == "live"
    assert forced["cache"]["hit"] is False

    restarted = CachedFinder()
    third = restarted.find_choices("深圳", "2026-05-01::劳动节", None, None, "yes", "yes", "all")
    assert restarted.calls == 0
    assert third["cache"]["source"] == "disk"
    assert third["cache"]["hit"] is True
    assert len(third["choices"]) == 2

    fallback = restarted.find_choices("广州", "2026-05-01::劳动节", None, None, "yes", "yes", "all", cache_only=True)
    assert restarted.calls == 1
    assert fallback["cache"]["source"] == "live"
    assert fallback["cache"]["hit"] is False


def test_city_and_hotel_name_cache_persist(tmp_path):
    finder = ReverseTravelFinder(StubCalendar(), cache_dir=tmp_path)
    candidate = CityCandidate(
        city_id=30,
        city_name="深圳",
        province_id=23,
        country_id=1,
        lat=22.543099,
        lon=114.057868,
        filter_id="19|30",
        search_coordinate="NORMAL_22.543099_114.057868_0",
    )

    finder._store_city_candidate("深圳", candidate)
    finder._hotel_name_cache["109336017"] = {"hotel_name": "深圳国际会展中心洲际酒店", "source": "Trip.com HK"}
    finder._save_hotel_name_cache()

    restarted = ReverseTravelFinder(StubCalendar(), cache_dir=tmp_path)
    assert restarted._load_cached_city_candidate("深圳") == candidate
    assert restarted._hotel_name_cache["109336017"]["hotel_name"] == "深圳国际会展中心洲际酒店"
