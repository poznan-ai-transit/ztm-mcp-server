from __future__ import annotations

import csv
import io
from dataclasses import dataclass


def _parse_bool_flag(value: str | None) -> bool | None:
    if value is None:
        return None
    cleaned = value.strip().lower()
    if cleaned in {"1", "true", "tak", "yes"}:
        return True
    if cleaned in {"0", "false", "nie", "no"}:
        return False
    return None


_FLOOR_TYPE_LABELS: dict[str, str] = {
    "1": "low_floor",
    "2": "low_entry",
    "3": "high_floor",
    "lf": "low_floor",
    "le": "low_entry",
    "hf": "high_floor",
}


@dataclass(frozen=True, slots=True)
class VehicleMetadata:
    """Static accessibility and equipment metadata for a single ZTM vehicle."""

    vehicle_id: str
    floor_type: str | None
    has_ramp: bool | None
    has_air_conditioner: bool | None
    raw: dict[str, str]

    @property
    def is_low_floor(self) -> bool | None:
        if self.floor_type is None:
            return None
        normalized = _FLOOR_TYPE_LABELS.get(self.floor_type.strip().lower())
        if normalized in {"low_floor", "low_entry"}:
            return True
        if normalized == "high_floor":
            return False
        return None

    def to_dict(self) -> dict[str, object]:
        normalized = (
            _FLOOR_TYPE_LABELS.get(self.floor_type.strip().lower(), self.floor_type)
            if self.floor_type is not None
            else None
        )
        return {
            "vehicle_id": self.vehicle_id,
            "floor_type": normalized,
            "is_low_floor": self.is_low_floor,
            "has_ramp": self.has_ramp,
            "has_air_conditioner": self.has_air_conditioner,
        }


class ZTMVehicleCatalog:
    """Immutable-by-convention lookup table parsed from vehicle_dictionary.csv."""

    def __init__(self, vehicles: dict[str, VehicleMetadata] | None = None) -> None:
        self._vehicles: dict[str, VehicleMetadata] = vehicles or {}

    @classmethod
    def from_csv_text(cls, csv_text: str) -> ZTMVehicleCatalog:
        reader = csv.DictReader(io.StringIO(csv_text))
        vehicles: dict[str, VehicleMetadata] = {}
        for row in reader:
            vehicle_id = (row.get("vehicle") or "").strip()
            if not vehicle_id:
                continue
            floor_raw = (row.get("hf_lf_le") or "").strip() or None
            vehicles[vehicle_id] = VehicleMetadata(
                vehicle_id=vehicle_id,
                floor_type=floor_raw,
                has_ramp=_parse_bool_flag(row.get("ramp")),
                has_air_conditioner=_parse_bool_flag(row.get("air_conditioner")),
                raw={k: (v or "").strip() for k, v in row.items() if k is not None},
            )
        return cls(vehicles)

    def replace(self, other: ZTMVehicleCatalog) -> None:
        self._vehicles = dict(other._vehicles)

    def get(self, vehicle_id: str | None) -> VehicleMetadata | None:
        if not vehicle_id:
            return None
        return self._vehicles.get(vehicle_id.strip())

    def __len__(self) -> int:
        return len(self._vehicles)


SAMPLE_CATALOG_CSV = """vehicle,hf_lf_le,ramp,air_conditioner,extra_col
3011,1,1,1,Solaris
3012,2,1,0,Moderus Beta
3013,3,0,0,Konstal 105Na
3014,LF,tak,yes,TextFlags
3015,HF,nie,no,HighFloorText
3016,,,unknown,MissingFlags
"""


def test_from_csv_text_parses_numeric_and_text_floor_codes():
    catalog = ZTMVehicleCatalog.from_csv_text(SAMPLE_CATALOG_CSV)
    assert len(catalog) == 6

    lf_num = catalog.get("3011")
    assert lf_num == VehicleMetadata(
        vehicle_id="3011",
        floor_type="1",
        has_ramp=True,
        has_air_conditioner=True,
        raw={
            "vehicle": "3011",
            "hf_lf_le": "1",
            "ramp": "1",
            "air_conditioner": "1",
            "extra_col": "Solaris",
        },
    )
    assert lf_num.is_low_floor is True
    assert lf_num.to_dict() == {
        "vehicle_id": "3011",
        "floor_type": "low_floor",
        "is_low_floor": True,
        "has_ramp": True,
        "has_air_conditioner": True,
    }

    le_num = catalog.get("3012")
    assert le_num is not None
    assert le_num.is_low_floor is True
    assert le_num.has_ramp is True
    assert le_num.has_air_conditioner is False
    assert le_num.to_dict()["floor_type"] == "low_entry"

    hf_num = catalog.get("3013")
    assert hf_num is not None
    assert hf_num.is_low_floor is False
    assert hf_num.has_ramp is False
    assert hf_num.has_air_conditioner is False
    assert hf_num.to_dict()["floor_type"] == "high_floor"

    text_lf = catalog.get("3014")
    assert text_lf is not None
    assert text_lf.is_low_floor is True
    assert text_lf.has_ramp is True
    assert text_lf.has_air_conditioner is True

    text_hf = catalog.get("3015")
    assert text_hf is not None
    assert text_hf.is_low_floor is False
    assert text_hf.has_ramp is False
    assert text_hf.has_air_conditioner is False

    unknown = catalog.get("3016")
    assert unknown is not None
    assert unknown.floor_type is None
    assert unknown.is_low_floor is None
    assert unknown.has_ramp is None
    assert unknown.has_air_conditioner is None


def test_get_handles_whitespace_none_and_missing_vehicles():
    catalog = ZTMVehicleCatalog.from_csv_text(SAMPLE_CATALOG_CSV)

    assert catalog.get("  3011 ") is not None
    assert catalog.get(None) is None
    assert catalog.get("") is None
    assert catalog.get("   ") is None
    assert catalog.get("9999") is None


def test_from_csv_text_skips_rows_without_vehicle_id():
    csv_text = "vehicle,hf_lf_le,ramp,air_conditioner\n,1,1,1\n   ,2,0,1\n"
    catalog = ZTMVehicleCatalog.from_csv_text(csv_text)
    assert len(catalog) == 0


def test_replace_updates_catalog_in_place():
    catalog = ZTMVehicleCatalog.from_csv_text(SAMPLE_CATALOG_CSV)
    original_ref = catalog

    updated = ZTMVehicleCatalog.from_csv_text(
        "vehicle,hf_lf_le,ramp,air_conditioner\n4001,1,1,1\n"
    )
    catalog.replace(updated)

    assert catalog is original_ref
    assert len(catalog) == 1
    assert catalog.get("3011") is None
    assert catalog.get("4001") is not None