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


@dataclass(frozen=True, slots=True)
class VehicleMetadata:
    """Static accessibility and equipment metadata for a single ZTM vehicle."""

    vehicle_id: str
    floor_type: str | None  # e.g. "LF" (low-floor), "LE" (low-entry), "HF" (high-floor)
    has_ramp: bool | None
    has_air_conditioner: bool | None
    raw: dict[str, str]  # preserves any extra ZTM columns for future tools

    @property
    def is_low_floor(self) -> bool | None:
        if self.floor_type is None:
            return None
        code = self.floor_type.upper()
        if code in {"LF", "LE"}:
            return True
        if code == "HF":
            return False
        return None

    def to_dict(self) -> dict[str, object]:
        return {
            "vehicle_id": self.vehicle_id,
            "floor_type": self.floor_type,
            "is_low_floor": self.is_low_floor,
            "has_ramp": self.has_ramp,
            "has_air_conditioner": self.has_air_conditioner,
        }


class ZTMVehicleCatalog:
    """Immutable in-memory lookup table parsed from vehicle_dictionary.csv."""

    def __init__(self, vehicles: dict[str, VehicleMetadata] | None = None) -> None:
        self._vehicles: dict[str, VehicleMetadata] = vehicles or {}

    def replace(self, other: ZTMVehicleCatalog) -> None:
        self._vehicles = dict(other._vehicles)

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

    def get(self, vehicle_id: str | None) -> VehicleMetadata | None:
        if not vehicle_id:
            return None
        return self._vehicles.get(vehicle_id.strip())

    def __len__(self) -> int:
        return len(self._vehicles)
