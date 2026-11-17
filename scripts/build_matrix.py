"""一次性预计算 POI 间驾车耗时矩阵（TASKS 0.5，B7）。

用法：
    .venv/Scripts/python.exe scripts/build_matrix.py                 # 默认读 data/pois.json → data/matrix.json
    .venv/Scripts/python.exe scripts/build_matrix.py --pois d.json --out m.json

行为：
- 节点 = 酒店锚点（node 0，VRPTW 的 depot）+ pois.json 全部 POI；
- 主通道：高德 v3/distance（type=1 驾车），每次一个目的地、全部起点（≤100 点限制内），
  n 个目的地共 n 次请求，QPS 限速；某列失败重试 3 次后整列降级 OSRM table 兜底；
- 产出 minutes（ceil 秒→分）、km、fare_cny（出租车启发式估算，公式记录在 meta，
  仅为预算维度提供确定性的交通费量级，非真实计价）；
- 可复跑、参数与列级数据来源记录在 meta；AMAP_KEY 不落盘。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import sys
import time
from pathlib import Path

import httpx

AMAP_DISTANCE_URL = "https://restapi.amap.com/v3/distance"
OSRM_TABLE_URL = "http://router.project-osrm.org/table/v1/driving/"
# 出租车启发式：起步价 13 元(3km) + 2.3 元/km
TAXI_START_FARE, TAXI_START_KM, TAXI_PER_KM = 13.0, 3.0, 2.3
# 酒店锚点：王府井大街中段（演示锚点，非真实酒店坐标；§10 示例 hotel=wangfujing）
HOTEL = {"id": "hotel_wangfujing", "name": "王府井酒店锚点", "lng": 116.410466, "lat": 39.915305}


def load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    return env


def amap_column(client: httpx.Client, key: str, coords: list[dict], dest: dict) -> list[tuple[float, float]]:
    """一个目的地列：返回 [(seconds, meters)]，顺序与 coords 一致。"""
    origins = "|".join(f"{c['lng']},{c['lat']}" for c in coords)
    r = client.get(AMAP_DISTANCE_URL, params={
        "key": key, "origins": origins, "destination": f"{dest['lng']},{dest['lat']}", "type": 1,
    })
    r.raise_for_status()
    data = r.json()
    if data.get("status") != "1":
        raise RuntimeError(f"amap status={data.get('status')} infocode={data.get('infocode')}")
    results = data.get("results") or []
    if len(results) != len(coords):
        raise RuntimeError(f"amap returned {len(results)} results, want {len(coords)}")
    out = []
    for row in results:
        sec, m = float(row["duration"]), float(row["distance"])
        if sec < 0 or m < 0:
            raise RuntimeError(f"negative cell: {row}")
        out.append((sec, m))
    return out


def osrm_table(client: httpx.Client, coords: list[dict]) -> tuple[list[list[float]], list[list[float]]]:
    """OSRM 兜底：一次性全对全表，返回 (seconds, meters)。"""
    path = ";".join(f"{c['lng']},{c['lat']}" for c in coords)
    r = client.get(OSRM_TABLE_URL + path, params={"annotations": "duration,distance"})
    r.raise_for_status()
    data = r.json()
    if data.get("code") != "Ok":
        raise RuntimeError(f"osrm code={data.get('code')}")
    return data["durations"], data["distances"]


def taxi_fare(km: float) -> float:
    return round(TAXI_START_FARE if km <= TAXI_START_KM
                 else TAXI_START_FARE + TAXI_PER_KM * (km - TAXI_START_KM), 1)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pois", default="data/pois.json")
    ap.add_argument("--out", default="data/matrix.json")
    ap.add_argument("--env", default=".env")
    args = ap.parse_args()

    pois_doc = json.loads(Path(args.pois).read_text(encoding="utf-8"))
    coords = [HOTEL] + [
        {"id": p["id"], "name": p["name"], "lng": p["lng"], "lat": p["lat"]}
        for p in pois_doc["pois"]
    ]
    n = len(coords)
    env = load_env(Path(args.env))
    key = os.environ.get("AMAP_KEY") or env.get("AMAP_KEY") or ""

    # 初始化为 None，便于检测缺口
    secs: list[list[float | None]] = [[None] * n for _ in range(n)]
    meters: list[list[float | None]] = [[None] * n for _ in range(n)]
    col_source: list[str] = ["pending"] * n
    fallback_cols: list[int] = []

    with httpx.Client(timeout=30, trust_env=False) as client:
        if key:
            for j, dest in enumerate(coords):
                done = False
                for attempt in range(3):
                    try:
                        col = amap_column(client, key, coords, dest)
                        for i, (s, m) in enumerate(col):
                            secs[i][j], meters[i][j] = s, m
                        col_source[j] = "amap"
                        done = True
                        break
                    except Exception as e:  # noqa: BLE001
                        print(f"WARN amap col {j} ({dest['id']}) attempt {attempt + 1}: {e}",
                              file=sys.stderr)
                        time.sleep(1.5 * (attempt + 1))
                if not done:
                    fallback_cols.append(j)
                print(f"col {j:>2} {dest['id']:<20} -> {col_source[j]}")
                time.sleep(0.35)  # QPS 限速
        else:
            print("AMAP_KEY 未设置，整表走 OSRM 兜底", file=sys.stderr)
            fallback_cols = list(range(n))

        if fallback_cols:
            print(f"OSRM 兜底列: {fallback_cols}", file=sys.stderr)
            o_secs, o_meters = osrm_table(client, coords)
            for j in fallback_cols:
                for i in range(n):
                    secs[i][j], meters[i][j] = o_secs[i][j], o_meters[i][j]
                col_source[j] = "osrm"

    # 对角线固定 0；缺口检测
    for i in range(n):
        secs[i][i] = meters[i][i] = 0.0
    missing = [(i, j) for i in range(n) for j in range(n) if secs[i][j] is None]
    if missing:
        print(f"ERROR: {len(missing)} cells missing, e.g. {missing[:5]}", file=sys.stderr)
        return 1

    minutes = [[math.ceil(float(secs[i][j]) / 60) for j in range(n)] for i in range(n)]
    km = [[round(float(meters[i][j]) / 1000, 1) for j in range(n)] for i in range(n)]
    fare = [[0.0 if i == j else taxi_fare(km[i][j]) for j in range(n)] for i in range(n)]

    # 基本合理性断言：形状、对角线、非对角为正
    assert len(minutes) == n and all(len(r) == n for r in minutes)
    assert all(minutes[i][i] == 0 for i in range(n))
    offdiag = [minutes[i][j] for i in range(n) for j in range(n) if i != j]
    assert all(v > 0 for v in offdiag), "non-positive off-diagonal cell"

    doc = {
        "schema_version": 1,
        "city": "beijing",
        "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "nodes": coords,
        "minutes": minutes,
        "km": km,
        "fare_cny": fare,
        "meta": {
            "duration_provider": "amap:distance_v3(type=1,driving) per-destination; "
                                 "osrm:table fallback per-column",
            "column_sources": col_source,
            "fallback_columns": fallback_cols,
            "fare_formula": f"taxi heuristic: max({TAXI_START_FARE}, {TAXI_START_FARE} "
                            f"+ {TAXI_PER_KM}*(km-{TAXI_START_KM}))",
            "hotel_anchor": "wangfujing (node 0, VRPTW depot)",
            "pois_source": os.path.basename(args.pois),
            "pois_export_date": pois_doc.get("export_date"),
            "n_nodes": n,
        },
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    od_min = [minutes[i][j] for i in range(n) for j in range(n) if i != j]
    print(f"\nwrote {out}: {n}x{n}, off-diag minutes min={min(od_min)} max={max(od_min)}, "
          f"fallback_cols={len(fallback_cols)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
