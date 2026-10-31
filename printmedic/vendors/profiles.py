"""Vendor profiles.

A profile identifies a manufacturer and records what PrintMedic can *honestly* do for it.
No profile grants automated maintenance: none of these vendors publishes an open, documented
interface for head cleaning/alignment that a third-party desktop app may drive. Those
operations are therefore offered as guided procedures only. Status and consumables come from
vendor-neutral standards (IPP / PWG 5100) when the device exposes them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class VendorProfile:
    vendor_id: str
    name: str
    patterns: tuple[str, ...]
    support_url: str
    maintenance: dict[str, str] = field(default_factory=dict)  # lang -> where to find the tools
    notes: dict[str, str] = field(default_factory=dict)        # lang -> limitations

    def matches(self, text: str) -> bool:
        return any(re.search(p, text, re.I) for p in self.patterns)


PROFILES: tuple[VendorProfile, ...] = (
    VendorProfile(
        "hp", "HP", (r"\bhp\b", r"hewlett[- ]?packard", r"\bdeskjet\b", r"\bofficejet\b", r"\blaserjet\b",
                     r"\benvy\b", r"\bsmart tank\b", r"\bdj \d"),
        "https://support.hp.com/",
        {"en": "Use the printer control panel (Setup/Tools → Printer Maintenance) or the HP Smart app "
               "(Printer Settings → Print Quality Tools) for head cleaning and alignment.",
         "pl": "Użyj panelu drukarki (Konfiguracja/Narzędzia → Konserwacja drukarki) lub aplikacji HP Smart "
               "(Ustawienia drukarki → Narzędzia jakości druku), aby wyczyścić głowicę lub wyrównać wydruk."},
        {"en": "HP's LEDM/EWS interfaces are not publicly documented for third-party control; PrintMedic "
               "uses IPP for status when available.",
         "pl": "Interfejsy HP LEDM/EWS nie są publicznie udokumentowane dla aplikacji firm trzecich; PrintMedic "
               "korzysta z IPP, gdy jest dostępne."},
    ),
    VendorProfile(
        "samsung", "Samsung (legacy)", (r"\bsamsung\b", r"\bxpress\b", r"\bscx-", r"\bml-\d", r"\bclp-"),
        "https://support.hp.com/",
        {"en": "Samsung printing products are supported by HP. Use the printer panel or Samsung Printer "
               "Diagnostics where it is still installed.",
         "pl": "Drukarki Samsung są obecnie wspierane przez HP. Użyj panelu drukarki lub programu "
               "Samsung Printer Diagnostics, jeśli jest zainstalowany."},
    ),
    VendorProfile(
        "canon", "Canon", (r"\bcanon\b", r"\bpixma\b", r"\bimageclass\b", r"\bi-sensys\b", r"\bmaxify\b",
                           r"\bimagerunner\b"),
        "https://global.canon/en/support/",
        {"en": "Run Cleaning / Deep Cleaning / Print Head Alignment from the printer menu (Maintenance) or "
               "from the Canon driver's Maintenance tab.",
         "pl": "Uruchom Czyszczenie / Głębokie czyszczenie / Wyrównanie głowicy z menu drukarki (Konserwacja) "
               "lub z karty Konserwacja w sterowniku Canon."},
    ),
    VendorProfile(
        "epson", "Epson", (r"\bepson\b", r"\becotank\b", r"\bworkforce\b", r"\bstylus\b", r"\bexpression\b"),
        "https://epson.com/support",
        {"en": "Use Nozzle Check, Head Cleaning and Print Head Alignment from the printer's Maintenance menu "
               "or the Epson driver's Maintenance/Utility tab.",
         "pl": "Użyj funkcji Sprawdzanie dysz, Czyszczenie głowicy i Wyrównanie głowicy w menu Konserwacja "
               "drukarki lub na karcie Konserwacja/Narzędzia sterownika Epson."},
    ),
    VendorProfile(
        "brother", "Brother", (r"\bbrother\b", r"\bdcp-", r"\bmfc-", r"\bhl-\d"),
        "https://support.brother.com/",
        {"en": "Use Ink/Maintenance → Cleaning or Improve Print Quality on the control panel; laser models "
               "offer drum cleaning from the panel.",
         "pl": "Użyj Tusz/Konserwacja → Czyszczenie lub Popraw jakość druku na panelu; modele laserowe "
               "oferują czyszczenie bębna z panelu."},
    ),
    VendorProfile(
        "xerox", "Xerox", (r"\bxerox\b", r"\bversalink\b", r"\baltalink\b", r"\bphaser\b", r"\bworkcentre\b"),
        "https://www.support.xerox.com/",
        {"en": "Use Device → Support / Troubleshooting on the control panel or the Embedded Web Server.",
         "pl": "Użyj Urządzenie → Pomoc / Rozwiązywanie problemów na panelu lub wbudowanego serwera WWW."},
    ),
    VendorProfile(
        "kyocera", "Kyocera", (r"\bkyocera\b", r"\becosys\b", r"\btaskalfa\b"),
        "https://www.kyoceradocumentsolutions.com/download/",
        {"en": "Use System Menu → Adjustment/Maintenance on the control panel or Command Center RX.",
         "pl": "Użyj Menu systemowe → Regulacja/Konserwacja na panelu lub Command Center RX."},
    ),
    VendorProfile(
        "lexmark", "Lexmark", (r"\blexmark\b",),
        "https://support.lexmark.com/",
        {"en": "Use Settings → Troubleshooting → Print Quality Test Pages on the control panel or the "
               "Embedded Web Server.",
         "pl": "Użyj Ustawienia → Rozwiązywanie problemów → Strony testowe jakości druku na panelu lub "
               "wbudowanego serwera WWW."},
    ),
    VendorProfile(
        "ricoh", "Ricoh", (r"\bricoh\b", r"\baficio\b", r"\bnashuatec\b", r"\bgestetner\b", r"\blanier\b"),
        "https://www.ricoh.com/support",
        {"en": "Use User Tools → Maintenance on the control panel or Web Image Monitor.",
         "pl": "Użyj Narzędzia użytkownika → Konserwacja na panelu lub Web Image Monitor."},
    ),
    VendorProfile(
        "microsoft", "Microsoft", (r"^microsoft\b",),
        "https://support.microsoft.com/windows",
    ),
)

GENERIC = VendorProfile(
    "generic", "Generic / IPP", (),
    "https://www.pwg.org/ipp/everywhere.html",
    {"en": "Use the maintenance functions on the printer's control panel. Consult the manufacturer's manual.",
     "pl": "Użyj funkcji konserwacji na panelu drukarki. Sprawdź instrukcję producenta."},
)


def detect_vendor(*texts: str | None) -> VendorProfile:
    """Pick the first profile matching any of the given strings (manufacturer, model, driver...)."""
    joined = " | ".join(t for t in texts if t)
    for profile in PROFILES:
        if profile.matches(joined):
            return profile
    return GENERIC


def profile_by_id(vendor_id: str) -> VendorProfile:
    return next((p for p in PROFILES if p.vendor_id == vendor_id), GENERIC)
