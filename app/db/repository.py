"""数据访问层（TASKS 1.1）：POI 与距离矩阵的只读加载、schema 校验与内存缓存。

设计要点：
- 只读：本模块不提供任何写入口，事实类数据只从 data/ 落盘文件读取（§5 原则）；
- schema 校验：pydantic 严格校验字段类型/取值域，坏数据在加载期即失败，不进求解器；
- 一致性校验：matrix.json 节点序必须为 [hotel] + pois.json 顺序（build_matrix.py 约定）；
- 缓存：文件只读不变（B7 一次性预计算），模块级 lru_cache 单例，进程内零重复 IO；
- 求解器/校验器共享同一份只读数据，但校验器不经过求解器代码路径（ADR-001）。
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

_REPO_ROOT = Path(__file__).resolve().parents[2]
POIS_PATH = _REPO_ROOT / "data" / "pois.json"
MATRIX_PATH = _REPO_ROOT / "data" / "matrix.json"

_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def parse_hhmm(value: str) -> int:
    """"HH:MM" → 距午夜分钟数；非法格式直接抛 ValueError。"""
    m = _HHMM.match(value)
    if not m:
        raise ValueError(f"invalid HH:MM time: {value!r}")
    return int(m.group(1)) * 60 + int(m.group(2))


def format_hhmm(minutes: int) -> str:
    """分钟数 → "HH:MM"（对齐 §10 响应示例的时间表示）。"""
    minutes = max(0, min(24 * 60 - 1, int(minutes)))
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


class POI(BaseModel):
    """单个 POI（含人工校对后的属性，B6）。open/close 同时保留原文与分钟值。"""

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    category: str = Field(min_length=1)
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    address: str = ""
    open: str
    close: str
    ticket: float = Field(ge=0)           # 门票（元）
    duration_min: int = Field(gt=0)       # 建议停留（分钟）
    score: float = Field(ge=0, le=5)      # 评分（drop penalty 权重基础，§8）
    closed_weekdays: list[int] = Field(default_factory=list)  # 0=周一（ISO weekday-1）
    note: str = ""
    export_date: str = ""
    source: str = ""

    # 派生（不参与序列化）：窗口的分钟表示
    open_min: int = 0
    close_min: int = 0

    @field_validator("open", "close")
    @classmethod
    def _check_time(cls, v: str) -> str:
        parse_hhmm(v)
        return v

    @field_validator("closed_weekdays")
    @classmethod
    def _check_weekdays(cls, v: list[int]) -> list[int]:
        if any(d < 0 or d > 6 for d in v):
            raise ValueError("closed_weekdays must be in 0..6 (0=Monday)")
        return v

    def model_post_init(self, _ctx: object) -> None:
        self.open_min = parse_hhmm(self.open)
        self.close_min = parse_hhmm(self.close)
        if self.open_min >= self.close_min:
            raise ValueError(f"POI {self.id}: open {self.open} >= close {self.close}")
        if self.close_min - self.open_min < self.duration_min:
            raise ValueError(
                f"POI {self.id}: duration {self.duration_min}min exceeds window "
                f"[{self.open},{self.close}]"
            )


class Hotel(BaseModel):
    """酒店锚点（VRPTW depot，matrix.json 节点 0）。"""

    id: str
    name: str
    lat: float
    lng: float


class MatrixData(BaseModel):
    """n×n 预计算矩阵：minutes（驾车分钟）/ km / fare_cny（出租车启发式）。"""

    node_ids: list[str]                       # 长度 n，node_ids[0] = 酒店
    minutes: list[list[int]]                  # n×n，非负
    km: list[list[float]]
    fare_cny: list[list[float]]
    hotel: Hotel
    meta: dict = Field(default_factory=dict)

    @field_validator("minutes", "km", "fare_cny")
    @classmethod
    def _square(cls, v: list[list[object]]) -> list[list[object]]:
        sizes = {len(row) for row in v}
        if len(v) == 0 or sizes != {len(v)}:
            raise ValueError(f"matrix not square: {len(v)}x{sizes}")
        return v

    def model_post_init(self, _ctx: object) -> None:
        n = len(self.node_ids)
        if len(set(self.node_ids)) != n:
            raise ValueError("duplicate node ids in matrix")
        if not (len(self.minutes) == len(self.km) == len(self.fare_cny) == n):
            raise ValueError(f"matrix size mismatch: nodes={n}")
        for i in range(n):
            if self.minutes[i][i] != 0 or self.km[i][i] != 0 or self.fare_cny[i][i] != 0:
                raise ValueError(f"matrix diagonal[{i}][{i}] must be 0")
            for j in range(n):
                if self.minutes[i][j] < 0 or self.km[i][j] < 0 or self.fare_cny[i][j] < 0:
                    raise ValueError(f"negative cell [{i}][{j}]")
                if i != j and self.minutes[i][j] <= 0:
                    raise ValueError(f"non-positive off-diagonal minutes [{i}][{j}]")

    @property
    def n(self) -> int:
        return len(self.node_ids)

    def index_of(self, node_id: str) -> int:
        try:
            return self.node_ids.index(node_id)
        except ValueError:
            raise KeyError(f"unknown matrix node: {node_id}") from None


class DataRepository:
    """POI + 矩阵的只读视图：加载一次、强校验、按 id 取数。"""

    def __init__(self, pois: list[POI], matrix: MatrixData, city: str) -> None:
        self._pois = pois
        self._by_id = {p.id: p for p in pois}
        if len(self._by_id) != len(pois):
            raise ValueError("duplicate POI ids in pois.json")
        self.matrix = matrix
        self.city = city
        self.hotel_id = matrix.hotel.id
        self._validate_consistency()

    def _validate_consistency(self) -> None:
        # 约定（build_matrix.py）：node 0 = 酒店锚点，node i≥1 依次对应 pois[i-1]
        if self.matrix.n != len(self._pois) + 1:
            raise ValueError(
                f"matrix has {self.matrix.n} nodes, expected {len(self._pois) + 1} "
                "(hotel + all POIs)"
            )
        if self.matrix.node_ids[0] != self.hotel_id:
            raise ValueError("matrix node 0 must be the hotel anchor")
        for i, poi in enumerate(self._pois, start=1):
            if self.matrix.node_ids[i] != poi.id:
                raise ValueError(
                    f"matrix node {i} is {self.matrix.node_ids[i]!r}, "
                    f"expected POI {poi.id!r} (order must follow pois.json)"
                )

    # ---- 读接口（无写入口） ----
    @property
    def pois(self) -> list[POI]:
        return list(self._pois)

    def poi(self, poi_id: str) -> POI:
        try:
            return self._by_id[poi_id]
        except KeyError:
            raise KeyError(f"unknown POI: {poi_id}") from None

    def pois_in_matrix_order(self) -> list[POI | None]:
        """按矩阵下标返回 POI（下标 0 为酒店，置 None），供求解器/校验器对齐。"""
        return [None, *self._pois]

    def hotel_minutes(self, node_id: str) -> int:
        return self.matrix.minutes[self.matrix.index_of(node_id)][0]


def _load_repository(pois_path: Path, matrix_path: Path) -> DataRepository:
    import json

    pois_doc = json.loads(pois_path.read_text(encoding="utf-8"))
    pois = [POI.model_validate(p) for p in pois_doc["pois"]]

    matrix_doc = json.loads(matrix_path.read_text(encoding="utf-8"))
    nodes = matrix_doc["nodes"]
    matrix = MatrixData(
        node_ids=[nd["id"] for nd in nodes],
        minutes=matrix_doc["minutes"],
        km=matrix_doc["km"],
        fare_cny=matrix_doc["fare_cny"],
        hotel=Hotel.model_validate(nodes[0]),
        meta=matrix_doc.get("meta", {}),
    )

    if pois_doc.get("city") != matrix_doc.get("city"):
        raise ValueError(
            f"city mismatch: pois.json={pois_doc.get('city')!r} "
            f"matrix.json={matrix_doc.get('city')!r}"
        )
    return DataRepository(pois, matrix, city=pois_doc["city"])


@lru_cache(maxsize=1)
def get_repository() -> DataRepository:
    """进程内单例（文件为一次性导出的静态数据集，B7）。"""
    return _load_repository(POIS_PATH, MATRIX_PATH)
