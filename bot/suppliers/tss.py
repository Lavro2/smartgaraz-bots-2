#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TSS Group a.s. (tssgroup.sk, Žilina) — autopríslušenstvo: KEETEC/Pandora alarmy, parkovacie
senzory a kamery, autokamery, autofólie (KEETECFOL), PPF, detailing (Nasiol)…

Zdroj dát: HQ (hq.smartgaraz.sk/api/sync/tss-catalog). TSS (Cloudflare) blokuje GitHub runnery,
preto cenníky (XLSX z Moje TSS) + sklad a dealer ceny z kategórií sťahuje hosting (HQ lib/tss.php,
1 login na beh, ≥1,6 s medzi požiadavkami). Tento bot na tssgroup.sk nepristupuje vôbec. Náklad = cena s DPH (nie sme platcovia), predaj = +30 %, nahor na ,90
(pricing.calculate_retail_ideo — rovnaké pravidlo). Bez ceny → produkt sa neimportuje.
Sklad: „Skladom“ → SR 2–3 prac. dni; „Na ceste“ (alebo text s lehotou) → EÚ 5–10 (onbackorder);
„Na objednávku“ bez lehoty / „Nie je skladom“ / „Vypredané“ → Nedostupné, nedá sa objednať. Kategórie: len explicitná mapa podľa
drobčekovej navigácie TSS, inak bez kategórie (koncept). Tempo ≥1,5 s, jeden login na beh,
žiadne obchádzanie ochrán (captcha/2FA → koniec).
"""
from __future__ import annotations

import html as _html
import json
import os
import re
from typing import Dict, Iterator, List, Optional, Tuple
from urllib.parse import urljoin


from .base import BaseSupplier, ProductRaw

BASE = "https://www.tssgroup.sk"
ROOT = "/produkty/autoprislusenstvo/"
# L2 sekcie relevantné pre auto (nie fólie na budovy, nanokeramika pre domácnosť/lode,
# promo materiály, vzorky, vyradené; „akcia-vypredaj“ sú duplicity).
SEEDS = (
    "zabezpecenie-auta", "centralne-zamykanie", "lokatory", "parkovacie-senzory", "parkovacie-kamery",
    "bezpecnostne-systemy", "cierne-skrinky", "audio-do-auta", "carplay-androidauto", "monitory-do-auta",
    "osvetlenie", "vystrazne-systemy", "baterie-a-nabijanie", "menice-napatia", "kompresory-a-fukare",
    "vyhrievanie-sedadiel", "odpudzovace", "alkohol-testery", "autofolie", "folie-na-ochranu-laku", "detailing",
)
AE = "Autoelektronika & príslušenstvo"
AF = "Autofólie & PPF"
AK = "Autokozmetika & Detail"
# (regex na slug-cestu drobčekov za /autoprislusenstvo/, cieľová cesta). Poradie = priorita.
# None = vedome bez kategórie (promo materiály). Bez zhody → bez kategórie (koncept).
RULES: Tuple[Tuple[str, Optional[List[str]]], ...] = (
    (r"promo", None),
    (r"zabezpecenie-auta/(izolacne-pasky|instalacny-material)", [AE, "Inštalačný materiál"]),
    (r"^(zabezpecenie-auta|centralne-zamykanie|lokatory)", [AE, "Autoalarmy & zabezpečenie"]),
    (r"^parkovacie-senzory", [AE, "Parkovacie senzory"]),
    (r"^parkovacie-kamery", [AE, "Parkovacie kamery"]),
    (r"^bezpecnostne-systemy", [AE, "Asistenčné systémy"]),
    (r"^cierne-skrinky", [AE, "Autokamery"]),
    (r"^(audio-do-auta|carplay-androidauto|monitory-do-auta)", [AE, "Audio & multimédiá"]),
    (r"^(osvetlenie|vystrazne-systemy)", [AE, "Osvetlenie & výstražné svetlá"]),
    (r"^(baterie-a-nabijanie|menice-napatia)", [AE, "Nabíjanie & napájanie"]),
    (r"^(kompresory-a-fukare|vyhrievanie-sedadiel|odpudzovace|alkohol-testery)", [AE, "Komfort & doplnky"]),
    (r"prislusenstvo-k-(ppf-)?foliam/(tekutiny|aplikacna|cistenie)", [AK, "Chémia pre fólie & PPF"]),
    (r"prislusenstvo-k-(ppf-)?foliam/(mikrovlak|susiace)", [AK, "Mikrovlákno & utierky"]),
    (r"prislusenstvo-k-(ppf-)?foliam", [AK, "Detailingové náradie"]),
    (r"^autofolie", [AF, "Autofólie na okná"]),
    (r"^folie-na-ochranu-laku", [AF, "PPF fólie"]),
    (r"^detailing/(keramicka|lestenie)", [AK, "Leštenie & vosky"]),
    (r"^detailing/(starostlivost-o-exterier|autosampony|skla)", [AK, "Exteriér & umývanie"]),
    (r"^detailing/starostlivost-o-interier", [AK, "Interiér"]),
    (r"^detailing/kolesa", [AK, "Disky & pneumatiky"]),
    (r"^detailing/(mikrovlak|susiace)", [AK, "Mikrovlákno & utierky"]),
    (r"^detailing/(aplikatory|kefy|spongie)", [AK, "Detailingové náradie"]),
)


def tss_category(crumb_path: str) -> Optional[List[str]]:
    p = (crumb_path or "").split(ROOT, 1)[-1].strip("/")
    if not p:
        return None
    for pat, cat in RULES:
        if re.search(pat, p):
            return list(cat) if cat else None
    return None


def stock_of(text: str) -> Tuple[str, str]:
    """Legenda webu má 3 stavy: Skladom v SR / Sklad v EÚ (5–10 dní) / Nedostupné.
    „Na ceste“ alebo text s dodacou lehotou → objednateľné (EÚ 5–10 dní);
    „Na objednávku“ bez lehoty, „Nie je skladom“ → nedostupné (termín nevieme sľúbiť)."""
    t = (text or "").strip().lower()
    if t.startswith("skladom") or "instock" in t:
        return "instock", "2–3 pracovné dni"
    if "na ceste" in t or re.search(r"\d+\s*(?:-|–)?\s*\d*\s*(?:h\b|hod|dn|dní|dni|týž|tyž|tyz)", t):
        return "onbackorder", "5–10 pracovných dní"
    return "outofstock", "nedostupné"


def _money(s: str) -> Optional[float]:
    m = re.search(r"(\d{1,3}(?:[ \u00a0.]\d{3})*|\d+)[,.](\d{2})(?!\d)", s or "")
    if not m:
        return None
    return float(re.sub(r"[ \u00a0.]", "", m.group(1)) + "." + m.group(2))


def sku_of(code: str, pid: str) -> str:
    c = re.sub(r"[^A-Za-z0-9]+", "-", (code or "").strip()).strip("-").upper()
    return f"TSS-{c}" if c else f"TSS-ID{pid}"


BRANDS = {"keetec": "Keetec", "keetecfol": "KeetecFOL", "pandora": "Pandora", "nasiol": "Nasiol", "bene": "BENE",
          "protector": "Protector", "steelmate": "Steelmate", "neoline": "Neoline", "orwell": "Orwell", "gswf": "GSWF",
          "zabat": "Zabat", "evoxys": "Evoxys", "camplus": "Camplus", "sd3": "SD3", "awex": "Awex", "kemo": "Kemo",
          "onlite": "Onlite", "bury": "Bury", "forteza": "Forteza", "audac": "Audac", "marstek": "Marstek", "alcosense": "AlcoSense"}
HQ_URL = os.getenv("HQ_URL", "https://hq.smartgaraz.sk").rstrip("/")


def full_image(url: str) -> str:
    """Náhľad z výpisu → pôvodná fotka (bez cache/pagespeed)."""
    u = re.sub(r"/buxus/images/cache/[^/]+/", "/buxus/images/", url or "")
    return re.sub(r"\.pagespeed\.[^/]*$", "", u)


class TssSupplier(BaseSupplier):
    name = "tss"

    def __init__(self, http=None, credentials=None):
        super().__init__(http=http, credentials=credentials or {})
        self._cat: Optional[dict] = None
        self.blocked_reason = ""

    def _catalog(self) -> dict:
        if self._cat is None:
            import requests
            tok = os.environ.get("HQ_SYNC_TOKEN", "")
            r = requests.get(f"{HQ_URL}/api/sync/tss-catalog", timeout=120,
                             headers={"Authorization": f"Bearer {tok}", "Accept-Encoding": "gzip", "User-Agent": "smartgaraz-bot"})
            r.raise_for_status()
            self._cat = r.json()
            self._by_url = {u.split("#")[0]: it for u, it in (self._cat.get("items") or {}).items()}
        return self._cat

    def ensure_login(self) -> bool:
        """Žiadny login z GitHubu — len overí, že HQ má čerstvý TSS katalóg."""
        try:
            return bool(self._catalog().get("items"))
        except Exception as exc:
            self.blocked_reason = f"HQ katalóg: {exc}"
            return False

    def discover_product_urls(self, limit: Optional[int] = None) -> List[str]:
        urls = list((self._catalog().get("items") or {}).keys())
        return urls[:limit] if limit else urls

    def iter_catalog(self, limit: Optional[int] = None) -> Iterator[ProductRaw]:
        if not self.ensure_login():
            raise RuntimeError(f"TSS katalóg z HQ nie je k dispozícii ({self.blocked_reason})")
        skip_skus, skip_urls = set(), set()
        if os.getenv("SG_SKIP_EXISTING", "0") == "1":
            try:
                from hq_skus import existing
                skip_skus, skip_urls, _src = existing()
            except Exception:
                pass
        # Najprv položky s jasnou kategóriou a skladom (rýchlo prvé publikovateľné produkty).
        items = list((self._catalog().get("items") or {}).values())
        items.sort(key=lambda it: (tss_category(it.get("cat", "")) is None, stock_of(it.get("stock", ""))[0] != "instock"))
        shard = os.getenv("TSS_SHARD", "").strip()   # "k/N" → paralelné joby bez prekryvu
        k, nsh = (int(shard.split("/")[0]), int(shard.split("/")[1])) if "/" in shard else (0, 1)
        import zlib
        n = 0
        for it in items:
            if nsh > 1 and zlib.crc32(sku_of(it.get("code", ""), it.get("pid", "")).encode()) % nsh != k:
                continue
            if it["url"] in skip_urls or sku_of(it.get("code", ""), it.get("pid", "")) in skip_skus:
                continue
            p = self._to_raw(it)
            if p:
                yield p
                n += 1
                if limit and n >= limit:
                    return

    def parse_product(self, url: str) -> Optional[ProductRaw]:
        self._catalog()
        it = self._by_url.get((url or "").split("#")[0])
        return self._to_raw(it) if it else None

    def _to_raw(self, it: dict) -> Optional[ProductRaw]:
        name = re.sub(r"\s+", " ", it.get("name") or "").strip()
        code = (it.get("code") or "").strip()
        pl = (self._catalog().get("prices") or {}).get(code.upper()) or {}
        net = it.get("price")
        if net is None and pl.get("dealer") is not None:
            net = pl["dealer"]
        if not name or net is None or float(net) <= 0:
            return None
        net = float(net)
        gross = round(net * 1.23, 2)
        moc = pl.get("moc")
        desc = (pl.get("desc") or "").strip()
        st, dt = stock_of(it.get("stock", ""))
        cat = tss_category(it.get("cat", ""))
        first = re.split(r"[\s-]", name, maxsplit=1)[0].lower()
        brand = BRANDS.get(first, "")
        img = full_image(it.get("img") or "")
        return ProductRaw(
            name=name, source="tss", supplier_url=it["url"], sku=sku_of(code, it.get("pid", "")), brand=brand,
            description=desc or name, short_description=(desc[:240] if desc else name), cost_price=f"{gross:.2f}",
            stock_status=st, delivery_text=dt, attributes={"Kód výrobcu": code} if code else {},
            existing_images=[img] if img else [],
            raw_extra={"_sg_supplier": "tss", "_sg_supplier_art_no": code, "tss_id": it.get("pid", ""), "catalog_url": it.get("cat", ""),
                       "price_basis": f"TSS dealer {net:.2f} € bez DPH / {gross:.2f} € s DPH" + (f" (MOC {moc} € bez DPH)" if moc else ""),
                       "unit_cost": gross, "stock_text": it.get("stock", ""), "sg_category_path": cat or [],
                       "tss_moc_bez_dph": moc, "media_source": "tssgroup.sk", "media_allowed": "pending",
                       "tss_catalog_at": self._catalog().get("generated_at", "")},
        )
