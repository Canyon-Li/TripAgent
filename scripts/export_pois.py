"""高德 Web 服务一次性导出北京 POI 数据集（TASKS 0.4，B5/B6）。

用法：
    .venv/Scripts/python.exe scripts/export_pois.py            # 默认导出 ./data/pois.json
    .venv/Scripts/python.exe scripts/export_pois.py --out d.json

行为：
- 精选清单 CURATED 中每个 POI 调一次高德 place/text v3（citylimit=北京），取最优命中；
- 坐标/地址/高德 poi id 来自接口，营业时间/门票/建议时长/评分为人工校对初值，
  每条带 needs_review 字段清单，人工核对后清空对应项；
- 可复跑：同一清单与参数产出相同结构（坐标/地址随高德数据更新）；
- 绝不把 AMAP_KEY 写入输出文件。

输出 schema（v1）与 §8 求解输入字段一一对应：open/close/ticket/duration_min/score。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path

import httpx

AMAP_PLACE_URL = "https://restapi.amap.com/v3/place/text"
CITY = "北京"
# 北京市大致边界（经纬度兜底校验）
BBOX = (115.4, 39.4, 117.6, 41.1)
REVIEW_FIELDS = ["hours", "ticket", "duration", "score"]

# 精选 POI 清单：id 稳定（供 must_visit / 矩阵索引引用），keywords 用于检索，
# expect 为命中名校验子串；hours/ticket/duration/score 为待人工校对的初值。
CURATED: list[dict] = [
    dict(id="tiananmen_square", keywords="天安门广场", expect="天安门",
         category="landmark", open="05:00", close="22:00", ticket=0,
         duration_min=60, score=4.8, note="需预约；升旗时间随日期变化", closed_weekdays=[]),
    dict(id="forbidden_city", keywords="故宫博物院", expect="故宫",
         category="history", open="08:30", close="17:00", ticket=60,
         duration_min=180, score=4.9, note="周一闭馆；旺季 16:10 停止入场", closed_weekdays=[0]),
    dict(id="jingshan_park", keywords="景山公园", expect="景山",
         category="nature", open="06:00", close="21:00", ticket=2,
         duration_min=60, score=4.7, note="万春亭俯瞰故宫全景", closed_weekdays=[]),
    dict(id="beihai_park", keywords="北海公园", expect="北海",
         category="nature", open="06:00", close="21:00", ticket=10,
         duration_min=90, score=4.7, note="", closed_weekdays=[]),
    dict(id="temple_of_heaven", keywords="天坛公园", expect="天坛",
         category="history", open="06:00", close="22:00", ticket=15,
         duration_min=120, score=4.8, note="联票（含祈年殿）另计", closed_weekdays=[]),
    dict(id="yonghe_lamasery", keywords="雍和宫", expect="雍和宫",
         category="religion", open="09:00", close="16:30", ticket=25,
         duration_min=75, score=4.7, note="", closed_weekdays=[]),
    dict(id="guozijian", keywords="北京国子监", expect="国子监",
         category="history", open="08:30", close="17:00", ticket=30,
         duration_min=75, score=4.6, note="周一闭馆", closed_weekdays=[0]),
    dict(id="nanluoguxiang", keywords="南锣鼓巷", expect="南锣鼓巷",
         category="food", open="10:00", close="21:00", ticket=0,
         duration_min=90, score=4.4, note="胡同商业街", closed_weekdays=[]),
    dict(id="shichahai", keywords="什刹海风景区", expect="什刹海",
         category="nature", open="00:00", close="23:59", ticket=0,
         duration_min=90, score=4.6, note="全天开放；前海/后海/酒吧街", closed_weekdays=[]),
    dict(id="national_museum", keywords="中国国家博物馆", expect="国家博物馆",
         category="museum", open="09:00", close="17:00", ticket=0,
         duration_min=150, score=4.8, note="周一闭馆；免费需预约", closed_weekdays=[0]),
    dict(id="wangfujing_street", keywords="王府井步行街", expect="王府井",
         category="shopping", open="10:00", close="22:00", ticket=0,
         duration_min=90, score=4.5, note="示例请求中的酒店锚点区域", closed_weekdays=[]),
    dict(id="qianmen_street", keywords="前门大街", expect="前门",
         category="shopping", open="10:00", close="22:00", ticket=0,
         duration_min=90, score=4.5, note="大栅栏", closed_weekdays=[]),
    dict(id="gui_street", keywords="簋街", expect="簋街",
         category="food", open="10:00", close="23:00", ticket=0,
         duration_min=90, score=4.5, note="夜间餐饮一条街；实际营业至凌晨（简化为当日窗口）",
         closed_weekdays=[]),
    dict(id="summer_palace", keywords="颐和园", expect="颐和园",
         category="history", open="06:00", close="20:00", ticket=30,
         duration_min=180, score=4.9, note="旺季 6:00-20:00", closed_weekdays=[]),
    dict(id="yuanmingyuan", keywords="圆明园遗址公园", expect="圆明园",
         category="history", open="07:00", close="19:30", ticket=10,
         duration_min=150, score=4.7, note="", closed_weekdays=[]),
    dict(id="badaling_great_wall", keywords="八达岭长城", expect="八达岭",
         category="history", open="06:30", close="16:30", ticket=40,
         duration_min=210, score=4.8, note="远郊（延庆），往返车程约 3h", closed_weekdays=[]),
    dict(id="mutianyu_great_wall", keywords="慕田峪长城", expect="慕田峪",
         category="history", open="07:30", close="18:00", ticket=45,
         duration_min=240, score=4.8, note="远郊（怀柔），往返车程约 3h", closed_weekdays=[]),
    dict(id="fragrant_hills_park", keywords="北京香山公园", expect="香山",
         category="nature", open="06:00", close="18:00", ticket=10,
         duration_min=180, score=4.6, note="秋季红叶季人流极大", closed_weekdays=[]),
    dict(id="798_art_zone", keywords="798艺术区", expect="798",
         category="modern", open="10:00", close="18:00", ticket=0,
         duration_min=150, score=4.5, note="园区全天开放，画廊约 10:00-18:00", closed_weekdays=[]),
    dict(id="olympic_park", keywords="北京奥林匹克公园", expect="奥林匹克",
         category="modern", open="06:00", close="22:00", ticket=0,
         duration_min=120, score=4.7, note="鸟巢/水立方外观免费", closed_weekdays=[]),
    dict(id="sanlitun_taikoo", keywords="三里屯太古里", expect="太古里",
         category="shopping", open="10:00", close="22:00", ticket=0,
         duration_min=120, score=4.5, note="", closed_weekdays=[]),
    dict(id="capital_museum", keywords="首都博物馆", expect="首都博物馆",
         category="museum", open="09:00", close="17:00", ticket=0,
         duration_min=120, score=4.7, note="周一闭馆；免费需预约", closed_weekdays=[0]),
    dict(id="beijing_zoo", keywords="北京动物园", expect="动物园",
         category="nature", open="07:30", close="18:00", ticket=15,
         duration_min=150, score=4.5, note="熊猫馆另计", closed_weekdays=[]),
]


def load_env(path: Path) -> dict[str, str]:
    """极简 .env 解析（不引入 python-dotenv 依赖）。真实环境变量优先。"""
    env: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    return env


def pick_best(pois: list[dict], expect: str) -> dict | None:
    for p in pois:
        if expect in p.get("name", ""):
            return p
    return pois[0] if pois else None


def fetch_poi(client: httpx.Client, key: str, entry: dict) -> dict:
    r = client.get(AMAP_PLACE_URL, params={
        "key": key, "keywords": entry["keywords"], "city": CITY,
        "citylimit": "true", "offset": 5, "page": 1,
    })
    r.raise_for_status()
    data = r.json()
    if data.get("status") != "1":
        raise RuntimeError(f"amap status={data.get('status')} infocode={data.get('infocode')} "
                           f"info={data.get('info')}")
    poi = pick_best(data.get("pois") or [], entry["expect"])
    if poi is None:
        raise RuntimeError("no pois returned")
    if entry["expect"] not in poi.get("name", ""):
        raise RuntimeError(f"best hit {poi.get('name')!r} does not contain {entry['expect']!r}")
    return poi


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="data/pois.json", help="输出文件路径")
    ap.add_argument("--env", default=".env", help=".env 路径（读取 AMAP_KEY）")
    args = ap.parse_args()

    env = load_env(Path(args.env))
    key = __import__("os").environ.get("AMAP_KEY") or env.get("AMAP_KEY")
    if not key:
        print("ERROR: AMAP_KEY 未设置（.env 或环境变量）", file=sys.stderr)
        return 2

    today = dt.date.today().isoformat()
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    pois: list[dict] = []
    misses: list[str] = []
    # 直连（restapi.amap.com 国内可达，绕开本机代理）
    with httpx.Client(timeout=15, trust_env=False) as client:
        for entry in CURATED:
            ok = None
            for attempt in range(3):
                try:
                    ok = fetch_poi(client, key, entry)
                    break
                except Exception as e:  # noqa: BLE001 —— 重试后仍失败则记入 misses
                    if attempt == 2:
                        print(f"MISS {entry['id']}: {e}", file=sys.stderr)
                    time.sleep(1.0 * (attempt + 1))
            if ok is None:
                misses.append(entry["id"])
                continue
            lng, lat = (float(x) for x in ok["location"].split(","))
            if not (BBOX[0] <= lng <= BBOX[2] and BBOX[1] <= lat <= BBOX[3]):
                print(f"MISS {entry['id']}: coord {lng},{lat} outside Beijing bbox",
                      file=sys.stderr)
                misses.append(entry["id"])
                continue
            pois.append({
                "id": entry["id"],
                "name": ok["name"],
                "category": entry["category"],
                "lat": lat,
                "lng": lng,
                "address": ok.get("address") or "",
                "amap_poi_id": ok.get("id") or "",
                "open": entry["open"],
                "close": entry["close"],
                "ticket": entry["ticket"],
                "duration_min": entry["duration_min"],
                "score": entry["score"],
                "closed_weekdays": entry["closed_weekdays"],
                "note": entry["note"],
                "needs_review": list(REVIEW_FIELDS),  # 人工校对完成前不清空
                "export_date": today,
                "source": "amap:place_text_v3",
            })
            print(f"OK   {entry['id']:<20} {ok['name']}  {lng},{lat}")
            time.sleep(0.35)  # 免费 QPS 限速

    # id 唯一性校验
    ids = [p["id"] for p in pois]
    assert len(ids) == len(set(ids)), "duplicate ids"

    doc = {
        "schema_version": 1,
        "city": "beijing",
        "export_date": today,
        "provider": {"api": "amap:place_text_v3", "citylimit": True, "retrieved": len(pois)},
        "pois": pois,
    }
    out_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\nwrote {out_path}: {len(pois)} pois, {len(misses)} misses")
    return 1 if misses else 0


if __name__ == "__main__":
    raise SystemExit(main())
