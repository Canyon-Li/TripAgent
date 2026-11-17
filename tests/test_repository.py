"""数据访问层测试（TASKS 1.1）：schema 校验、一致性校验、缓存与只读性。"""

from __future__ import annotations

import pytest

from app.db.repository import (
    MATRIX_PATH,
    POI,
    POIS_PATH,
    DataRepository,
    format_hhmm,
    get_repository,
    parse_hhmm,
)


def test_load_real_dataset() -> None:
    repo = get_repository()
    pois = repo.pois
    assert len(pois) >= 18, "验收口径：POI ≥ 18（B5）"
    assert repo.city == "beijing"
    assert repo.matrix.n == len(pois) + 1
    assert repo.hotel_id == "hotel_wangfujing"
    # 节点序 = [酒店] + pois.json 顺序
    ordered = repo.pois_in_matrix_order()
    assert ordered[0] is None
    assert ordered[1].id == pois[0].id
    assert ordered[-1].id == pois[-1].id


def test_repository_is_cached_singleton() -> None:
    assert get_repository() is get_repository()


def test_time_helpers_roundtrip() -> None:
    assert parse_hhmm("05:00") == 300
    assert parse_hhmm("23:59") == 1439
    assert format_hhmm(300) == "05:00"
    assert format_hhmm(1439) == "23:59"
    with pytest.raises(ValueError):
        parse_hhmm("24:00")
    with pytest.raises(ValueError):
        parse_hhmm("7:30")


def test_poi_schema_rejects_bad_fields() -> None:
    base = dict(
        id="x", name="X", category="landmark", lat=39.9, lng=116.4,
        open="08:00", close="17:00", ticket=10, duration_min=60, score=4.0,
    )
    POI.model_validate(base)  # 合法
    with pytest.raises(ValueError):
        POI.model_validate({**base, "duration_min": 0})
    with pytest.raises(ValueError):
        POI.model_validate({**base, "score": 5.5})
    with pytest.raises(ValueError):
        POI.model_validate({**base, "closed_weekdays": [7]})
    with pytest.raises(ValueError):
        POI.model_validate({**base, "open": "18:00", "close": "17:00"})  # open>=close
    with pytest.raises(ValueError):
        POI.model_validate({**base, "duration_min": 9999})  # 停留超出营业窗口


def test_cross_consistency_node_order() -> None:
    repo = get_repository()
    good = repo.pois
    swapped = [good[1], good[0], *good[2:]]  # 破坏 pois.json 顺序
    with pytest.raises(ValueError, match="order must follow pois.json"):
        DataRepository(swapped, repo.matrix, city=repo.city)


def test_cross_consistency_hotel_not_node0() -> None:
    from app.db.repository import Hotel

    repo = get_repository()
    fake_hotel = Hotel(id="hotel_other", name="另一家", lat=39.9, lng=116.4)
    bad_matrix = repo.matrix.model_copy(update={"hotel": fake_hotel})
    with pytest.raises(ValueError, match="hotel"):
        DataRepository(repo.pois, bad_matrix, city=repo.city)


def test_unknown_poi_raises_keyerror() -> None:
    repo = get_repository()
    with pytest.raises(KeyError):
        repo.poi("not_a_poi")


def test_matrix_paths_exist() -> None:
    assert POIS_PATH.exists() and MATRIX_PATH.exists()
