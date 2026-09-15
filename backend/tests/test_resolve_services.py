"""resolve_services against the organizers' classifier.

The expected notification lists are derived from the raw xlsx cells with a hand-written
column map (letters read from the sheet header), independent of the parser's own header
logic. If the organizers' file is absent (stand without data/organizers), the table test
is skipped and only the synthetic cases run.
"""

from pathlib import Path

import pytest

from app.domain.services import available_flags, resolve_services
from app.importers.classifier import NO_RESPONSE, parse_classifier

XLSX = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "organizers"
    / ("Классификатор_происшествий_v046.xlsx")
)

# Column letter → (service, flag needed) as read from rows 1-3 of the sheet.
COLUMN_MAP: dict[str, tuple[str, str | None]] = {
    "N": ("101", None),
    "O": ("101", "no_access"),
    "P": ("ods_psc", None),
    "Q": ("ods_psc", "threat"),
    "R": ("ods_psc", "injured"),
    "S": ("ods_psc", "no_access"),
    "T": ("mgpss", None),
    "U": ("102", None),
    "V": ("102", "offense"),
    "W": ("102", "injured"),
    "X": ("103", None),
    "Y": ("103", "injured"),
    "Z": ("103", "not_on_site"),
    "AA": ("mosgaz", None),
    "AB": ("mosgaz", "gasification"),
    "AC": ("cemp", None),
    "AD": ("cemp", "threat"),
    "AE": ("cemp", "injured"),
    "AF": ("cemp", "medical_help"),
    "AG": ("cemp", "evacuation"),
    "AH": ("fsb", None),
    "AI": ("fsb", "mass_incident"),
    "AJ": ("mosoblgaz", None),
    "AK": ("autoroads", None),
    "AL": ("mosgortrans", None),
    "AM": ("mosgortrans", "injured"),
    "AN": ("mosgortrans", "road_closed"),
    "AO": ("gkh", None),
    "AP": ("gormost", None),
    "AQ": ("gormost", "tunnel"),
    "AR": ("gormost", "pedestrian_bridge"),
    "AS": ("gormost", "road_bridge"),
    "AT": ("kim", None),
    "AU": ("mgts", None),
    "AV": ("mgts", "telecom_object"),
    "AW": ("metro", None),
    "AX": ("mosvodokanal", None),
    "AY": ("moek", None),
    "AZ": ("moesk", None),
    "BA": ("oek", None),
    "BB": ("moslift", None),
    "BC": ("codd", None),
    "BD": ("dep_gkh", None),
    "BE": ("duty_arm112", None),
    "BF": ("mkp_analytics", None),
    "BG": ("mayor_office", None),
    "BH": ("moscollector", None),
    "BI": ("mzd", None),
    "BJ": ("dep_education", None),
    "BK": ("crvh", None),
    "BL": ("military_commandant", None),
    "BM": ("oati", None),
    "BN": ("mosvodostok", None),
    "BO": ("dep_ppoos", None),
    "BP": ("dep_tszn", None),
    "BQ": ("rsvo", None),
    "BR": ("evazhd", None),
    "BS": ("msppn", None),
    "BT": ("dtu_ritual", None),
    "BU": ("dtu", None),
    "BV": ("rosgvardia", None),
    "BW": ("territorial_oiv", None),
    "BX": ("territorial_oiv_tinao", None),
    "BY": ("autoroads_ao", None),
    "BZ": ("dep_construction", None),
    "CA": ("dep_construction", "construction_site"),
    "CB": ("veterinary", None),
    "CC": ("moszhilinspection", None),
    "CD": ("dep_culture", "listed_object"),
    "CE": ("csa_glinki", None),
    "CF": ("ntu", None),
    "CG": ("fso", None),
    "CH": ("msr_kub", None),
    "CI": ("msr_fires", None),
    "CJ": ("tourism", None),
    "CK": ("dgp_integration", None),
    "CL": ("dgp_arm112", None),
    "CM": ("cukb_mo", None),
    "CN": ("cukb_bpla_mo", None),
    "CO": ("transport_organizer", None),
    "CP": ("transport_organizer", "road_closed"),
    "CQ": ("mosecomonitoring", None),
    "CR": ("mo_rhbz_polygons", None),
    "CS": ("mo_rhbz_moscow", None),
    "CT": ("cityenergo", None),
    "CU": ("dep_civil_construction", None),
}

# 30 rows of the sheet (type code, flags): plain rows plus every flag family.
CASES: list[tuple[str, list[str]]] = [
    ("1.1.1.1", []),
    ("1.1.1.1", ["injured"]),
    ("1.1.1.1", ["no_access"]),
    ("1.1.1.1", ["threat"]),
    ("1.1.1.1", ["offense"]),
    ("1.1.1.1", ["injured", "not_on_site"]),
    ("1.1.1.2", ["gasification"]),
    ("1.1.4.1", ["road_closed"]),
    ("1.1.10.1", ["tunnel"]),
    ("1.1.10.1", ["pedestrian_bridge", "road_bridge"]),
    ("1.5.5.1", ["telecom_object"]),
    ("1.6.5.1", ["construction_site"]),
    ("2.1.0.0", []),
    ("2.1.0.0", ["road_closed"]),
    ("2.2.16.0", ["mass_incident"]),
    ("5.5.1.0", ["mass_incident", "injured"]),
    ("15.1.1.0", []),
    ("15.1.2.0", ["injured"]),
    ("15.1.2.0", ["not_on_site"]),
    ("15.1.3.0", []),
    ("17.1.1.0", ["evacuation", "medical_help"]),
    ("22.1.0.0", []),
    ("22.1.0.0", ["injured"]),
    ("22.2.0.0", ["listed_object"]),
    ("24.1.0.0", []),
    ("24.2.0.0", []),
    ("13.1.1.0", ["gasification", "injured"]),
    ("14.3.1.0", []),
    ("16.1.1.0", ["road_closed", "injured"]),
    ("19.1.2.0", ["offense"]),
]


@pytest.fixture(scope="module")
def sheet_types():
    if not XLSX.exists():
        pytest.skip("нет файла организаторов data/organizers/Классификатор_происшествий_v046.xlsx")
    import openpyxl
    from openpyxl.utils import column_index_from_string

    classifier = parse_classifier(XLSX)
    ws = openpyxl.load_workbook(XLSX)["Лист1"]
    raw_rows = {}
    for t in classifier.types:
        cells = {}
        for letter in COLUMN_MAP:
            value = ws.cell(t.source_row, column_index_from_string(letter)).value
            if value is not None and str(value).strip():
                cells[letter] = str(value).strip()
        raw_rows[t.code] = cells
    return classifier.type_by_code(), raw_rows


def expected_from_cells(cells: dict[str, str], flags: list[str]) -> set[str]:
    chosen = set(flags)
    notified: set[str] = set()
    removed: set[str] = set()
    for letter, value in cells.items():
        service, flag = COLUMN_MAP[letter]
        if flag is not None and flag not in chosen:
            continue
        if value.lower() == NO_RESPONSE:
            removed.add(service)
        else:
            notified.add(service)
    return notified - removed


@pytest.mark.parametrize(("code", "flags"), CASES)
def test_resolve_matches_sheet_columns(sheet_types, code: str, flags: list[str]) -> None:
    types, raw_rows = sheet_types
    assert code in types, f"тип {code} исчез из классификатора"
    rules = [r.as_dict() for r in types[code].service_rules]
    assert set(resolve_services(rules, flags)) == expected_from_cells(raw_rows[code], flags)


def test_every_type_default_list_matches_sheet(sheet_types) -> None:
    types, raw_rows = sheet_types
    mismatches = [
        code
        for code, t in types.items()
        if set(resolve_services([r.as_dict() for r in t.service_rules]))
        != expected_from_cells(raw_rows[code], [])
    ]
    assert mismatches == []


def test_classifier_size(sheet_types) -> None:
    types, _ = sheet_types
    assert len(types) == 1283


SYNTHETIC_RULES = [
    {"service": "102", "when": [], "notify": True},
    {"service": "103", "when": ["injured"], "notify": True},
    {"service": "103", "when": ["not_on_site"], "notify": False},
    {"service": "101", "when": ["no_access"], "notify": True},
]


def test_default_rules_apply_without_flags() -> None:
    assert resolve_services(SYNTHETIC_RULES) == ["102"]


def test_flag_adds_service_and_order_is_stable() -> None:
    assert resolve_services(SYNTHETIC_RULES, ["no_access", "injured"]) == ["102", "103", "101"]


def test_negative_rule_removes_service() -> None:
    assert resolve_services(SYNTHETIC_RULES, ["injured", "not_on_site"]) == ["102"]


def test_unknown_flags_are_ignored() -> None:
    assert resolve_services(SYNTHETIC_RULES, ["something_else"]) == ["102"]


def test_available_flags_lists_each_once() -> None:
    assert available_flags(SYNTHETIC_RULES) == ["injured", "not_on_site", "no_access"]
