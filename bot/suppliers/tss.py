#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TSS Group a.s. (tssgroup.sk, Žilina) — autopríslušenstvo: KEETEC/Pandora alarmy, parkovacie
senzory a kamery, autokamery, autofólie (KEETECFOL), PPF, detailing (Nasiol)…

Dealer login (TSS_USER / TSS_PASSWORD, formulár /prihlasenie, Buxus CMS). Ceny vidí len
prihlásený partner. Náklad = cena s DPH (nie sme platcovia), predaj = +30 %, nahor na ,90
(pricing.calculate_retail_ideo — rovnaké pravidlo). Bez ceny → produkt sa neimportuje.
Sklad: „Skladom“ → SR 2–3 prac. dni; „Na ceste“/„Na objednávku“ → EÚ 5–10 (onbackorder);
„Vypredané“/„Nedostupné“ → nedá sa objednať. Kategórie: len explicitná mapa podľa
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

from bs4 import BeautifulSoup

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
    t = (text or "").strip().lower()
    if t.startswith("skladom") or "instock" in t:
        return "instock", "2–3 pracovné dni"
    if any(k in t for k in ("na ceste", "objedn", "do ", "preorder", "backorder", "dní", "týž")):
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


class TssSupplier(BaseSupplier):
    name = "tss"

    def __init__(self, http=None, credentials=None):
        super().__init__(http=http, credentials=credentials or {})
        self.http.delay = max(float(getattr(self.http, "delay", 0) or 0), float(os.getenv("TSS_DELAY", "1.5")))
        self._logged_in = False
        self._login_tried = False
        self.blocked_reason = ""
        self._listing: Dict[str, Dict[str, str]] = {}

    # ------------------------------------------------------------ login (1× na beh)
    def ensure_login(self) -> bool:
        if self._logged_in or self._login_tried:
            return self._logged_in
        self._login_tried = True
        u, p = self.credentials.get("user"), self.credentials.get("password")
        if not u or not p:
            return False
        page = self.http.get(BASE + "/prihlasenie").text or ""
        if re.search(r"captcha|g-recaptcha|hcaptcha|cf-challenge", page, re.I):
            self.blocked_reason = "captcha"
            return False
        m = re.search(r'name="_token" value="([^"]+)"', page)
        data = {"user_login": u, "user_password": p, "rememberme": "0", "tss_login_form": "Prihlásiť",
                "redirect": "", "_token": m.group(1) if m else ""}
        r = self.http.session.post(BASE + "/prihlasenie", data=data, timeout=30)
        t = r.text or ""
        if re.search(r"captcha|overovac[ií] k[óo]d|dvojfaktor|2fa", t, re.I):
            self.blocked_reason = "captcha/2FA"
            return False
        self._logged_in = "Neplatné meno alebo heslo" not in t and bool(re.search(r"odhl[áa]s", t, re.I))
        return self._logged_in

    # ------------------------------------------------------------ crawl
    def _listing_page(self, path: str) -> Tuple[List[str], Optional[str]]:
        h = self.http.get(urljoin(BASE, path)).text or ""
        soup = BeautifulSoup(h, "html.parser")
        box = soup.select_one("#fs_results_box") or soup
        urls = []
        for card in box.select(".flex-list-item"):
            a = card.select_one(".product-name a")
            if not a or not a.get("href"):
                continue
            url = urljoin(BASE, a["href"].split("?")[0])
            av = card.select_one("[class*=product-availability]")
            code = card.select_one('[title="Kód produktu"]')
            img = card.select_one("[class*=tag-main-product-images-]")
            pid = re.search(r"tag-main-product-images-(\d+)", " ".join(img.get("class", []))) if img else None
            self._listing[url] = {"stock": av.get_text(" ", strip=True) if av else "",
                                  "code": code.get_text(" ", strip=True) if code else "",
                                  "pid": pid.group(1) if pid else ""}
            urls.append(url)
        nxt = soup.select_one(".fs_pager .fs-next a[href]")
        return urls, (nxt["href"] if nxt else None)

    def discover_product_urls(self, limit: Optional[int] = None) -> List[str]:
        self.ensure_login()
        out: List[str] = []
        seen = set()
        for seed in SEEDS:
            path: Optional[str] = f"{ROOT}{seed}?view=default"
            pages = 0
            while path and pages < 60:
                pages += 1
                urls, path = self._listing_page(path)
                for u in urls:
                    if u not in seen:
                        seen.add(u)
                        out.append(u)
                if limit and len(out) >= limit:
                    return out[:limit]
        return out

    def iter_catalog(self, limit: Optional[int] = None) -> Iterator[ProductRaw]:
        """Full import: už existujúce SKU/URL (HQ endpoint) preskoč ešte pred stiahnutím detailu."""
        if not self.ensure_login():
            raise RuntimeError(f"TSS login zlyhal{' (' + self.blocked_reason + ')' if self.blocked_reason else ''} – koniec behu")
        skip_skus, skip_urls = set(), set()
        if os.getenv("SG_SKIP_EXISTING", "0") == "1":
            try:
                from hq_skus import existing
                skip_skus, skip_urls, _src = existing()
            except Exception:
                pass
        n = 0
        for url in self.discover_product_urls(None):
            lst = self._listing.get(url, {})
            if url in skip_urls or sku_of(lst.get("code", ""), lst.get("pid", "")) in skip_skus:
                continue
            p = self.parse_product(url)
            if p:
                yield p
                n += 1
                if limit and n >= limit:
                    return

    # ------------------------------------------------------------ detail
    def _price(self, soup: BeautifulSoup, h: str) -> Tuple[Optional[float], Optional[float], str]:
        """(bez DPH, s DPH, text). Len z cenového bloku prihláseného partnera."""
        net = gross = None
        txt = ""
        for el in soup.select("[class*=price], [id*=price]"):
            if "button-show-price" in " ".join(el.get("class", [])) or el.get("id") == "modal-show-price":
                continue
            t = el.get_text(" ", strip=True)
            if "€" not in t and "EUR" not in t:
                continue
            txt = (txt + " | " + t)[:300]
            for m in re.finditer(r"([\d\s\u00a0.,]+)\s*(?:€|EUR)[^\d]{0,25}?(bez\s*DPH|s\s*DPH)?", t, re.I):
                v = _money(m.group(1))
                lab = (m.group(2) or "").lower()
                if v is None:
                    continue
                if "bez" in lab and net is None:
                    net = v
                elif lab.startswith("s") and gross is None:
                    gross = v
            if net is None and gross is None:
                m = re.search(r"bez\s*DPH[^\d]{0,20}([\d\s\u00a0.,]+)", t, re.I)
                net = _money(m.group(1)) if m else None
                m = re.search(r"s\s*DPH[^\d]{0,20}([\d\s\u00a0.,]+)", t, re.I)
                gross = _money(m.group(1)) if m else None
        if gross is None and net is not None:
            gross = round(net * 1.23, 2)
        return net, gross, txt

    def parse_product(self, url: str) -> Optional[ProductRaw]:
        url = url.split("#", 1)[0]
        h = self.http.get(url).text or ""
        soup = BeautifulSoup(h, "html.parser")
        h1 = soup.select_one("h1.product-title") or soup.find("h1")
        name = re.sub(r"\s+", " ", h1.get_text(" ", strip=True)) if h1 else ""
        if not name:
            return None
        crumbs = [a.get("href") or "" for a in soup.select("ol.breadcrumb li a")]
        crumb = crumbs[-1] if crumbs else ""
        if ROOT.rstrip("/") not in crumb:
            return None  # iná divízia (zabezpečenie objektov…)
        ld: dict = {}
        for s in soup.find_all("script", type="application/ld+json"):
            try:
                d = json.loads(s.string or "{}")
                if d.get("@type") == "Product":
                    ld = d
                    break
            except Exception:
                continue
        meta = soup.select_one("p.product-meta")
        code = ""
        if meta:
            code = meta.get_text(" ", strip=True).split("|")[-1].strip()
        lst = self._listing.get(url, {})
        code = code or lst.get("code", "")
        pid = lst.get("pid") or (re.search(r'"id":(\d+)', h).group(1) if re.search(r'"id":(\d+)', h) else "")
        params = {}
        for tr in soup.select("#parameters table tr"):
            th, td = tr.find("th"), tr.find("td")
            if th and td:
                params[th.get_text(" ", strip=True)] = td.get_text(" ", strip=True)
        brand = params.pop("Výrobca", "") or (meta.find("img").get("alt", "").title() if meta and meta.find("img") else "")
        params.pop("Hmotnosť", None)
        desc_el = soup.select_one("#description")
        desc = desc_el.get_text(" ", strip=True)[:3000] if desc_el else _html.unescape(re.sub(r"<[^>]+>", " ", _html.unescape(ld.get("description", ""))))[:3000]
        short = ""
        if h1:
            d = h1.find_next("div")
            short = d.get_text(" ", strip=True)[:240] if d else ""
        imgs = [urljoin(BASE, a.get("href")) for a in soup.select(".product-gallery-wrapper a[href]")
                if re.search(r"\.(jpe?g|png|webp)$", a.get("href", ""), re.I)][:6]
        stock_txt = lst.get("stock") or str((ld.get("offers") or {}).get("availability", ""))
        st, dt = stock_of(stock_txt)
        net, gross, ptxt = self._price(soup, h) if self._logged_in else (None, None, "")
        if gross is None:
            return None  # bez dealer ceny nič neimportujeme
        cat = tss_category(crumb)
        weight = re.search(r"Hmotnosť</th>\s*<td>([\d.,]+)\s*kg", h)
        return ProductRaw(
            name=name, source="tss", supplier_url=url, sku=sku_of(code, pid), brand=brand,
            description=desc, short_description=short or desc[:240], cost_price=f"{gross:.2f}",
            stock_status=st, delivery_text=dt, attributes=params, existing_images=imgs,
            raw_extra={"_sg_supplier": "tss", "_sg_supplier_art_no": code, "tss_id": pid, "catalog_url": crumb,
                       "price_basis": f"TSS dealer {net} € bez DPH / {gross} € s DPH ({ptxt[:120]})",
                       "unit_cost": gross, "stock_text": stock_txt, "sg_category_path": cat or [],
                       "weight_kg": weight.group(1).replace(",", ".") if weight else "",
                       "media_source": "tssgroup.sk", "media_allowed": "pending"},
        )
