import pathlib
import re

from printmedic.i18n import set_language, tr
from printmedic.i18n.en import STRINGS as EN
from printmedic.i18n.pl import STRINGS as PL

ROOT = pathlib.Path(__file__).parents[1] / "printmedic"


def test_languages_have_identical_keys():
    assert set(EN) == set(PL)


def test_placeholders_match_between_languages():
    for key, en in EN.items():
        assert set(re.findall(r"{(\w+)}", en)) == set(re.findall(r"{(\w+)}", PL[key])), key


def test_every_literal_key_in_code_exists():
    used = set()
    for p in ROOT.rglob("*.py"):
        src = p.read_text(encoding="utf-8")
        used |= set(re.findall(r'tr\(\s*"([a-zA-Z0-9_.\-]+)"', src))
        used |= set(re.findall(r'\("((?:preview|warn|rollback|pre|verify|guide|assist\.step)\.[a-z0-9_.\-]+)"', src))
        used |= set(re.findall(r'"(guide\.[a-z_]+)"', src))
        used |= set(re.findall(r'"(capnote\.[a-z_]+)"', src))
    assert sorted(k for k in used if k not in EN) == []


def test_polish_is_actually_translated():
    same = [k for k in EN if EN[k] == PL[k] and len(EN[k]) > 12]
    assert len(same) < 10, same


def test_tr_formats_and_falls_back():
    set_language("pl")
    assert tr("status.refreshed", n=3) == "Zaktualizowano listę — urządzeń: 3."
    assert tr("cond.some-new-reason") == "some new reason"
    set_language("en")
    assert tr("status.refreshed", n=3).startswith("Inventory updated")
