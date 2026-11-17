#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Macro Dashboard - skripta za dohvat podataka
=============================================

Sto ova skripta radi, u jednoj recenici:
ode na Eurostat i ECB, skine zadane serije podataka za Hrvatsku,
izracuna godisnje stope promjene i spremi sve u mapu data/ kao JSON datoteke
koje web-stranica onda cita i crta.

Pokrece se sama svaki dan preko GitHub Actions (vidi .github/workflows/update.yml).
Rucno se pokrece naredbom:  python fetch_data.py

DVIJE ZASTITE koje ova skripta ima:

1. Vise izvora po pokazatelju. Svaki pokazatelj ima popis mjesta odakle se
   moze skinuti, poredanih po prednosti. Ako prvo mjesto ne radi (statisticari
   povremeno gase i preimenuju tablice), skripta sama proba sljedece.

2. Provjera svjezine. Ako serija postoji ali je prestala pristizati - sto je
   podmukliji kvar od obicne greske, jer izgleda kao da sve radi - skripta to
   oznaci u izvjestaju kao ZASTARJELO.

Autor obrade podataka: I. Brkljaca
"""

import json
import csv
import io
import os
import re
import sys
import time
import datetime
import zipfile
import xml.etree.ElementTree as ET
import urllib.parse
import urllib.request

# ---------------------------------------------------------------------------
# 1. POSTAVKE
# ---------------------------------------------------------------------------

POCETNO_RAZDOBLJE = "2015-01"   # od kada skidamo povijest
MAPA_PODATAKA = "data"
TIMEOUT = 60
DOPUSTENO_KASNJENJE_MJESECI = 4  # koliko serija smije kasniti prije upozorenja

EUROSTAT_BAZA = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data"
ECB_BAZA = "https://data-api.ecb.europa.eu/service/data"


# ---------------------------------------------------------------------------
# 2. POPIS POKAZATELJA
# ---------------------------------------------------------------------------
# Svaki pokazatelj je jedan blok. Da bi se dodao novi, dopise se jedan ovakav
# blok - nista drugo u skripti se ne mijenja.
#
#   id         - ime datoteke koja nastaje (data/<id>.json)
#   naziv      - naslov kartice na dashboardu
#   podnaslov  - mali opisni tekst ispod naslova
#   jedinica   - "indeks", "posto" ili "mio_eur"
#   prikaz     - velika brojka na kartici: "razina" ili "god_stopa"
#   izvori     - popis mjesta odakle se podatak moze skinuti, po prednosti

POKAZATELJI = [
    {
        "id": "ind_proizvodnja",
        "naziv": "Industrijska proizvodnja",
        "podnaslov": "Indeks obujma, sezonski prilagodeno (2021 = 100)",
        "jedinica": "indeks",
        "prikaz": "god_stopa",
        "izvori": [
            {"api": "eurostat", "oznaka": "Eurostat", "tablica": "sts_inpr_m",
             "filtri": {"geo": "HR", "nace_r2": "B-D", "indic_bt": "PRD",
                        "s_adj": "SCA", "unit": "I21"}},
        ],
        # Sluzbeno objavljena godisnja stopa. Racuna se iz kalendarski
        # prilagodenog niza, pa se ne poklapa u decimalu sa stopom koju bismo
        # sami izveli iz sezonski prilagodenog indeksa. Za objavu vrijedi ova.
        "izvori_stope": [
            {"api": "eurostat", "oznaka": "Eurostat", "tablica": "sts_inpr_m",
             "filtri": {"geo": "HR", "nace_r2": "B-D", "indic_bt": "PRD",
                        "s_adj": "CA", "unit": "PCH_SM"}},
        ],
    },
    {
        "id": "maloprodaja",
        "naziv": "Trgovina na malo",
        "podnaslov": "Indeks obujma prodaje, sezonski prilagodeno (2021 = 100)",
        "jedinica": "indeks",
        "prikaz": "god_stopa",
        "izvori": [
            {"api": "eurostat", "oznaka": "Eurostat", "tablica": "sts_trtu_m",
             "filtri": {"geo": "HR", "nace_r2": "G47", "indic_bt": "VOL_SLS",
                        "s_adj": "SCA", "unit": "I21"}},
        ],
        "izvori_stope": [
            {"api": "eurostat", "oznaka": "Eurostat", "tablica": "sts_trtu_m",
             "filtri": {"geo": "HR", "nace_r2": "G47", "indic_bt": "VOL_SLS",
                        "s_adj": "CA", "unit": "PCH_SM"}},
        ],
    },
    {
        "id": "gradjevinarstvo",
        "naziv": "Gradevinarstvo",
        "podnaslov": "Indeks obujma gradevinskih radova (2021 = 100)",
        "jedinica": "indeks",
        "prikaz": "god_stopa",
        "izvori": [
            {"api": "eurostat", "oznaka": "Eurostat", "tablica": "sts_copr_m",
             "filtri": {"geo": "HR", "nace_r2": "F", "indic_bt": "PRD",
                        "s_adj": "SCA", "unit": "I21"}},
        ],
        "izvori_stope": [
            {"api": "eurostat", "oznaka": "Eurostat", "tablica": "sts_copr_m",
             "filtri": {"geo": "HR", "nace_r2": "F", "indic_bt": "PRD",
                        "s_adj": "CA", "unit": "PCH_SM"}},
        ],
    },
    {
        "id": "izvoz",
        "naziv": "Izvoz robe",
        "podnaslov": "Ukupan izvoz, milijuni eura, sezonski prilagodeno",
        "jedinica": "mio_eur",
        "prikaz": "razina",
        "izvori": [
            {"api": "eurostat", "oznaka": "Eurostat", "tablica": "ei_eteu27_2020_m",
             "filtri": {"geo": "HR", "stk_flow": "EXP", "partner": "WORLD",
                        "unit": "MIO-EUR-SA", "indic": "ET-T"}},
        ],
    },
    {
        "id": "uvoz",
        "naziv": "Uvoz robe",
        "podnaslov": "Ukupan uvoz, milijuni eura, sezonski prilagodeno",
        "jedinica": "mio_eur",
        "prikaz": "razina",
        "izvori": [
            {"api": "eurostat", "oznaka": "Eurostat", "tablica": "ei_eteu27_2020_m",
             "filtri": {"geo": "HR", "stk_flow": "IMP", "partner": "WORLD",
                        "unit": "MIO-EUR-SA", "indic": "ET-T"}},
        ],
    },
    {
        "id": "hicp_hr",
        "naziv": "Inflacija (HICP)",
        "podnaslov": "Godisnja stopa promjene cijena",
        "jedinica": "posto",
        "prikaz": "razina",
        # POVIJEST OVOG POKAZATELJA - vrijedi procitati prije diranja:
        # Pocetkom 2026. HICP je presao na novu klasifikaciju (ECOICOP ver. 2).
        # Stara tablica prc_hicp_manr i stare ECB sifre serija (npr. 000000)
        # nisu nestale - samo su se zamrznule na prosincu 2025. i dalje uredno
        # odgovaraju na upite. Zato ih ovdje NE drzimo kao rezervu: izvor koji
        # vraca lanjske podatke bez ijedne greske gori je od izvora koji padne.
        # Nova tablica je prc_hicp_minr, dimenzija se zove coicop18,
        # a ukupni indeks je TOTAL (ne vise CP00).
        "izvori": [
            {"api": "eurostat", "oznaka": "Eurostat (HICP)", "tablica": "prc_hicp_minr",
             "filtri": {"geo": "HR", "coicop18": "TOTAL", "unit": "RCH_A"}},
        ],
    },
    {
        "id": "hicp_temeljna",
        "naziv": "Temeljna inflacija",
        "podnaslov": "HICP bez energije, hrane, alkohola i duhana",
        "jedinica": "posto",
        "prikaz": "razina",
        # TOT_X_NRG_FOOD = "Overall index excluding energy, food, alcohol and tobacco"
        "izvori": [
            {"api": "eurostat", "oznaka": "Eurostat (HICP)", "tablica": "prc_hicp_minr",
             "filtri": {"geo": "HR", "coicop18": "TOT_X_NRG_FOOD", "unit": "RCH_A"}},
        ],
    },
    {
        "id": "hicp_ea",
        "naziv": "Inflacija u europodrucju",
        "podnaslov": "Godisnja stopa promjene cijena, EA20",
        "jedinica": "posto",
        "prikaz": "razina",
        "izvori": [
            {"api": "eurostat", "oznaka": "Eurostat (HICP)", "tablica": "prc_hicp_minr",
             "filtri": {"geo": "EA20", "coicop18": "TOTAL", "unit": "RCH_A"}},
        ],
    },
    {
        "id": "esi",
        "naziv": "Ekonomski sentiment",
        "podnaslov": "Kompozitni indeks sentimenta (dugorocni prosjek = 100)",
        "jedinica": "indeks",
        "prikaz": "razina",
        "izvori": [
            {"api": "eurostat", "oznaka": "Eurostat (DG ECFIN)", "tablica": "ei_bssi_m_r2",
             "filtri": {"geo": "HR", "indic": "BS-ESI-I", "s_adj": "SA"}},
        ],
    },
    {
        "id": "stambeni_krediti",
        "naziv": "Kamate na stambene kredite",
        "podnaslov": "Prosjecna kamata na nove stambene kredite kucanstvima",
        "jedinica": "posto",
        "prikaz": "razina",
        "izvori": [
            {"api": "ecb", "oznaka": "ECB (MIR)", "skup": "MIR",
             "kljuc": "M.HR.B.A2C.A.R.A.2250.EUR.N"},
        ],
    },
    {
        "id": "neto_placa",
        "naziv": "Neto placa",
        "podnaslov": "Prosjecna mjesecna isplacena neto placa po zaposlenome "
                     "u pravnim osobama, euro",
        "jedinica": "eur",
        "prikaz": "razina",
        # DZS nema API kao Eurostat s fiksnim siframa tablica. Skripta zato
        # sama pronade tablicu u DZS-ovoj PC-Axis (PxWeb) bazi, u mapi
        # "Zaposlenost i place". Rezerva je datoteka "Statistika u nizu"
        # (place.xlsx) sa stalnom adresom. Sve sto je skripta vidjela zapisuje
        # se u data/dzs_dijagnostika.json - tamo se gleda ako nesto pukne.
        # Iznosi prije 2023. preracunavaju se iz kuna po tecaju 7,53450.
        "izvori": [
            {"api": "dzs_pxweb_place", "oznaka": "DZS",
             "tablica": "PxWeb: Zaposlenost i place"},
            {"api": "dzs_xlsx_place", "oznaka": "DZS",
             "tablica": "Statistika u nizu: place.xlsx"},
        ],
        # Za realnu stopu treba godisnja stopa inflacije. Prednost ima DZS-ov
        # indeks potrosackih cijena (njime DZS racuna realnu placu). Ako ga
        # skripta ne nade ili brojke ne prodju provjeru, uzima HICP s
        # Eurostata - i to zapise, a na kartici pise koji je deflator.
        "deflatori": [
            {"api": "dzs_pxweb_ipc", "oznaka": "indeks potrošačkih cijena (DZS)",
             "tablica": "PxWeb: Cijene"},
            {"api": "eurostat", "oznaka": "HICP (Eurostat)", "tablica": "prc_hicp_minr",
             "filtri": {"geo": "HR", "coicop18": "TOTAL", "unit": "RCH_A"}},
        ],
    },
]


# ---------------------------------------------------------------------------
# 3. POMOCNE FUNKCIJE
# ---------------------------------------------------------------------------

def dohvati(url):
    """Otvori adresu i vrati sadrzaj kao tekst."""
    zahtjev = urllib.request.Request(
        url,
        headers={"User-Agent": "macro-dashboard/1.0 (podaci za nekomercijalni prikaz)"},
    )
    with urllib.request.urlopen(zahtjev, timeout=TIMEOUT) as odgovor:
        return odgovor.read().decode("utf-8")


def eurostat_serija(tablica, filtri):
    """
    Skine jednu seriju s Eurostata i vrati je kao listu parova (razdoblje, vrijednost).

    Eurostat vraca format koji se zove JSON-stat: vrijednosti su u jednom
    dugackom nizu, a posebno je zapisano koji redni broj odgovara kojem mjesecu.
    Ova funkcija to rasplete natrag u obican popis.
    """
    parametri = ["format=JSON", "lang=EN", "sinceTimePeriod=" + POCETNO_RAZDOBLJE]
    for kljuc, vrijednost in filtri.items():
        parametri.append("{}={}".format(kljuc, vrijednost))
    url = "{}/{}?{}".format(EUROSTAT_BAZA, tablica, "&".join(parametri))

    podaci = json.loads(dohvati(url))

    if "value" not in podaci or not podaci["value"]:
        raise ValueError("Eurostat je vratio prazan skup (provjeri filtre).")

    dimenzije = podaci["id"]
    velicine = podaci["size"]
    i_vrijeme = dimenzije.index("time")

    # Sigurnosna provjera: filtri moraju suziti upit na tocno jednu seriju.
    for i, ime in enumerate(dimenzije):
        if i != i_vrijeme and velicine[i] > 1:
            raise ValueError(
                "Upit nije dovoljno odreden: dimenzija '{}' ima {} clanova. "
                "Dodaj je u filtre.".format(ime, velicine[i])
            )

    indeks_vremena = podaci["dimension"]["time"]["category"]["index"]
    razdoblje_po_poziciji = {v: k for k, v in indeks_vremena.items()}

    korak = 1
    for v in velicine[i_vrijeme + 1:]:
        korak *= v

    tocke = []
    for ravni_indeks, vrijednost in podaci["value"].items():
        if vrijednost is None:
            continue
        pozicija = (int(ravni_indeks) // korak) % velicine[i_vrijeme]
        razdoblje = razdoblje_po_poziciji.get(pozicija)
        if razdoblje:
            tocke.append((razdoblje, float(vrijednost)))

    tocke.sort(key=lambda par: par[0])
    return tocke


def ecb_serija(skup, kljuc):
    """
    Skine jednu seriju s ECB-a i vrati je kao listu parova (razdoblje, vrijednost).
    ECB moze vratiti obican CSV, sto je najjednostavnije za citanje.
    """
    url = "{}/{}/{}?format=csvdata&startPeriod={}".format(
        ECB_BAZA, skup, kljuc, POCETNO_RAZDOBLJE
    )
    tekst = dohvati(url)

    citac = csv.DictReader(io.StringIO(tekst))
    tocke = []
    for redak in citac:
        razdoblje = redak.get("TIME_PERIOD")
        vrijednost = redak.get("OBS_VALUE")
        if razdoblje and vrijednost not in (None, "", "NaN"):
            try:
                tocke.append((razdoblje, float(vrijednost)))
            except ValueError:
                continue

    if not tocke:
        raise ValueError("ECB je vratio prazan skup (provjeri kljuc serije).")

    tocke.sort(key=lambda par: par[0])
    return tocke


def godisnje_stope(tocke):
    """
    Iz serije razina izracuna godisnju stopu promjene u postocima
    (usporedba s istim mjesecom prosle godine).
    """
    po_razdoblju = dict(tocke)
    rezultat = []
    for razdoblje, vrijednost in tocke:
        godina, mjesec = razdoblje.split("-")[0], razdoblje.split("-")[1]
        prosla = "{}-{}".format(int(godina) - 1, mjesec)
        if prosla in po_razdoblju and po_razdoblju[prosla] not in (0, None):
            stopa = (vrijednost / po_razdoblju[prosla] - 1) * 100
            rezultat.append((razdoblje, round(stopa, 2)))
    return rezultat


def mjeseci_kasnjenja(razdoblje):
    """Koliko mjeseci zadnji podatak zaostaje za danasnjim danom."""
    try:
        godina, mjesec = razdoblje.split("-")[0], razdoblje.split("-")[1]
        danas = datetime.date.today()
        return (danas.year - int(godina)) * 12 + (danas.month - int(mjesec))
    except (ValueError, IndexError):
        return None


# ---------------------------------------------------------------------------
# 3b. DZS (Drzavni zavod za statistiku)
# ---------------------------------------------------------------------------
# DZS objavljuje podatke u PC-Axis (PxWeb) bazi koja ima strojno sucelje,
# ali tablice nemaju trajne sifre kao kod Eurostata: mape i tablice se
# preimenuju (npr. prijelazom na NKD 2025. od sijecnja 2026.). Zato skripta
# ne pamti tocnu tablicu, nego je svaki put potrazi po nazivu, provjeri
# sadrzaj i spoji sve mjesecne nizove koje nade. Sto je vidjela i odabrala,
# zapisuje u data/dzs_dijagnostika.json.

DZS_PXWEB = "https://web.dzs.hr/PXWeb/api/v1/hr"
DZS_PLACE_XLSX = "https://podaci.dzs.hr/media/yegdgb5v/place.xlsx"
TECAJ_HRK = 7.53450
DZS_DIJAGNOSTIKA = {}

MJESECI_IMENA = [("sije", 1), ("velj", 2), ("ožuj", 3), ("ozuj", 3), ("trav", 4),
                 ("svib", 5), ("lip", 6), ("srp", 7), ("kolov", 8), ("ruj", 9),
                 ("listop", 10), ("stud", 11), ("prosin", 12)]
RIMSKI = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7,
          "VIII": 8, "IX": 9, "X": 10, "XI": 11, "XII": 12}


def dohvati_bajtove(url, podaci=None):
    """Kao dohvati(), ali vraca sirove bajtove i po potrebi salje POST (JSON)."""
    zaglavlja = {"User-Agent": "macro-dashboard/1.0 (podaci za nekomercijalni prikaz)"}
    tijelo = None
    if podaci is not None:
        tijelo = json.dumps(podaci).encode("utf-8")
        zaglavlja["Content-Type"] = "application/json"
    zahtjev = urllib.request.Request(url, data=tijelo, headers=zaglavlja)
    with urllib.request.urlopen(zahtjev, timeout=TIMEOUT) as odgovor:
        return odgovor.read()


def pxweb(putanja, upit=None):
    """Jedan poziv PxWeb sucelja. Mala pauza jer DZS ogranicava broj upita."""
    time.sleep(0.4)
    dijelovi = [urllib.parse.quote(d) for d in putanja.split("/") if d]
    url = DZS_PXWEB + ("/" + "/".join(dijelovi) if dijelovi else "")
    sirovo = dohvati_bajtove(url, upit)
    return json.loads(sirovo.decode("utf-8-sig"))


def mali(tekst):
    return (tekst or "").lower()


def u_mjesec(*tekstovi):
    """Pretvori oznaku razdoblja (2026M01, 2026-01, I. 2026., sijecanj 2026...)
    u oblik 'GGGG-MM'. Godisnje i tromjesecne oznake vracaju None."""
    for tekst in tekstovi:
        if tekst is None:
            continue
        t = str(tekst).strip()
        m = re.search(r"(\d{4})\s*M\s*(\d{1,2})\b", t, re.I)
        if not m:
            m = re.search(r"\b(\d{4})\s*[-./]\s*(\d{1,2})\b(?!\s*=)", t)
        if m and 1 <= int(m.group(2)) <= 12:
            return "{}-{:02d}".format(int(m.group(1)), int(m.group(2)))
        m = re.search(r"\b(XII|XI|IX|X|VIII|VII|VI|IV|V|III|II|I)\.?\s*(\d{4})", t)
        if m:
            return "{}-{:02d}".format(int(m.group(2)), RIMSKI[m.group(1)])
        godina = re.search(r"\b(19|20)\d{2}\b", t)
        if godina:
            nizak = mali(t)
            for korijen, broj_mj in MJESECI_IMENA:
                if korijen in nizak:
                    return "{}-{:02d}".format(int(godina.group(0)), broj_mj)
    return None


def u_eure(tocke):
    """Iznose u kunama (prije 2023.) preracuna u eure po fiksnom tecaju.
    Neto placa u eurima prije 2023. nije bila ni blizu 2.500, a u kunama je
    uvijek iznad toga, pa je granica sigurna."""
    rezultat = []
    for t, v in tocke:
        if t < "2023-01" and v > 2500:
            v = v / TECAJ_HRK
        rezultat.append((t, round(v, 2)))
    return rezultat


def spoji_nizove(nizovi):
    """Spoji vise nizova [(t, v)]; za preklapajuca razdoblja vrijedi niz koji
    seze dalje u sadasnjost (to je novija tablica)."""
    nizovi = sorted([n for n in nizovi if n], key=lambda n: n[-1][0])
    spojeno = {}
    for niz in nizovi:
        spojeno.update(dict(niz))
    return sorted(spojeno.items())


# --- PxWeb: pretrazivanje stabla -------------------------------------------

def pxweb_katalog(rijeci_mape, najvise_razina=4, najvise_upita=60):
    """Prode stablo baze od korijena. U mape ulazi samo ako im naziv sadrzi
    neku od zadanih rijeci (na prvoj razini) i nije arhiva prije 2014.
    Vrati popis svih tablica koje je vidio: {putanja, naziv, azurirano}."""
    tablice, posjeceno = [], 0
    red = [("", 0)]
    while red and posjeceno < najvise_upita:
        putanja, razina = red.pop(0)
        try:
            stavke = pxweb(putanja)
        except Exception as greska:          # noqa: BLE001
            DZS_DIJAGNOSTIKA.setdefault("greske_pretrage", []).append(
                "{}: {}".format(putanja or "/", greska))
            continue
        posjeceno += 1
        for s in stavke:
            naziv = s.get("text", "")
            nova = (putanja + "/" + s.get("id", "")).strip("/")
            if s.get("type") == "t":
                tablice.append({"putanja": nova, "naziv": naziv,
                                "azurirano": s.get("updated")})
            elif s.get("type") == "l" and razina < najvise_razina:
                if razina == 0 and not any(r in mali(naziv) for r in rijeci_mape):
                    continue
                if "arhiv" in mali(naziv) and ("2013" in naziv or "2000" in naziv):
                    continue
                red.append((nova, razina + 1))
    return tablice


def pxweb_vrijeme(varijable):
    """Pronadi vremensku varijablu tablice."""
    for v in varijable:
        if v.get("time"):
            return v
    for v in varijable:
        if mali(v.get("code")) in ("razdoblje", "mjesec", "vrijeme", "godina", "tid", "time"):
            return v
    for v in varijable:
        tekstovi = v.get("valueTexts") or []
        if tekstovi and sum(1 for x in tekstovi[:24] if u_mjesec(x)) >= min(12, len(tekstovi)):
            return v
    return None


UKUPNO = ("ukupno", "sveukupno", "republika hrvatska", "hrvatska", "sve djelatnosti",
          "total", "ukupni indeks", "ukupan indeks")


def odaberi_ukupno(tekstovi, kodovi):
    for i, t in enumerate(tekstovi):
        if mali(t).strip(" .") in UKUPNO or mali(t).startswith("ukupno"):
            return i
    for i, k in enumerate(kodovi):
        if str(k).upper() in ("TOTAL", "UKUPNO", "00", "0", "CP00", "T"):
            return i
    for i, t in enumerate(tekstovi):
        if any(u in mali(t) for u in UKUPNO):
            return i
    return None


def pxweb_niz(tablica, odabir_pokazatelja):
    """Za jednu tablicu: procitaj opis, za svaku varijablu osim vremena izaberi
    jednu vrijednost (ukupno / trazeni pokazatelj), pa skini cijeli niz.
    Vrati (tocke, opis_odabira) ili digne gresku s razlogom."""
    meta = pxweb(tablica["putanja"])
    varijable = meta.get("variables", [])
    vrijeme = pxweb_vrijeme(varijable)
    if vrijeme is None:
        raise ValueError("nema mjesecne vremenske varijable")
    mjesecnih = sum(1 for k, t in zip(vrijeme["values"], vrijeme.get("valueTexts", vrijeme["values"]))
                    if u_mjesec(k, t))
    if mjesecnih < 6:
        raise ValueError("vrijeme nije mjesecno (npr. {})".format(vrijeme["valueTexts"][:3]))

    upit, odabrano = [], {}
    for v in varijable:
        if v is vrijeme:
            continue
        kodovi, tekstovi = v["values"], v.get("valueTexts", v["values"])
        if len(kodovi) == 1:
            i = 0
        else:
            i = odabir_pokazatelja(tekstovi, kodovi)
            if i is None:
                i = odaberi_ukupno(tekstovi, kodovi)
        if i is None:
            raise ValueError("ne znam koju vrijednost uzeti za '{}': {}".format(
                v.get("text"), tekstovi[:8]))
        odabrano[v.get("text") or v["code"]] = tekstovi[i]
        upit.append({"code": v["code"], "selection": {"filter": "item", "values": [kodovi[i]]}})
    upit.append({"code": vrijeme["code"], "selection": {"filter": "all", "values": ["*"]}})

    js = pxweb(tablica["putanja"], {"query": upit, "response": {"format": "json-stat2"}})
    return jsonstat2_niz(js, vrijeme["code"]), {"naslov": meta.get("title"), "odabrano": odabrano}


def jsonstat2_niz(js, kod_vremena):
    """Raspetljaj JSON-stat2 odgovor (sve dimenzije osim vremena imaju 1 clan)."""
    ids, velicine = js["id"], js["size"]
    i_vr = ids.index(kod_vremena)
    kategorija = js["dimension"][kod_vremena]["category"]
    indeks = kategorija.get("index")
    if isinstance(indeks, list):
        indeks = {k: i for i, k in enumerate(indeks)}
    oznake = kategorija.get("label", {})
    po_poziciji = {p: k for k, p in indeks.items()}
    korak = 1
    for v in velicine[i_vr + 1:]:
        korak *= v
    vrijednosti = js.get("value", [])
    if isinstance(vrijednosti, dict):
        stavke = [(int(k), v) for k, v in vrijednosti.items()]
    else:
        stavke = list(enumerate(vrijednosti))
    tocke = []
    for ravni, v in stavke:
        if v is None:
            continue
        kod = po_poziciji.get((ravni // korak) % velicine[i_vr])
        t = u_mjesec(kod, oznake.get(kod))
        if t:
            tocke.append((t, float(v)))
    tocke.sort()
    return tocke


# --- Neto placa iz PxWeb-a ---------------------------------------------------

LOSE_ZA_PLACU = ("satu", " sat", "spol", "županij", "zupanij", "obrazov", "decil",
                 "kvartil", "medijal", "obračunsk", "obracunsk", "zanimanj", "dob",
                 "godišnj", "godisnj", "tromjeseč", "tromjesec")


def ocjena_tablice_place(naziv):
    n = mali(naziv)
    if "neto" not in n or any(l in n for l in LOSE_ZA_PLACU):
        return None
    ocjena = 1
    for rijec, bod in (("isplać", 2), ("isplac", 2), ("prosječn", 2), ("prosjecn", 2),
                       ("mjeseč", 1), ("mjesec", 1), ("djelatnost", 1)):
        if rijec in n:
            ocjena += bod
    return ocjena


def pokazatelj_neto_placa(tekstovi, kodovi):
    """Ako varijabla nabraja pokazatelje, uzmi iznos neto place (ne realni
    indeks, ne po satu, ne bruto). Ako nabraja valute, uzmi euro."""
    kandidati = [i for i, t in enumerate(tekstovi)
                 if "neto" in mali(t) and not any(x in mali(t) for x in
                    ("sat", "real", "indeks", "bruto", "medijal", "stopa", "%"))]
    if kandidati:
        s_eurom = [i for i in kandidati if "eur" in mali(tekstovi[i])]
        return (s_eurom or kandidati)[0]
    eur = [i for i, t in enumerate(tekstovi) if mali(t).strip(" .") in ("euro", "eur", "eura")]
    return eur[0] if eur else None


def dzs_pxweb_place():
    katalog = pxweb_katalog(("zaposlenost", "plać", "plac"))
    DZS_DIJAGNOSTIKA["place_katalog"] = katalog
    kandidati = [(ocjena_tablice_place(t["naziv"]), t) for t in katalog]
    kandidati = sorted([k for k in kandidati if k[0]], key=lambda k: -k[0])[:8]
    if not kandidati:
        raise ValueError("u bazi nije pronadena tablica s neto placom "
                         "({} tablica pregledano)".format(len(katalog)))
    nizovi, upotrijebljeno, odbaceno = [], [], []
    for _, tablica in kandidati:
        try:
            tocke, opis = pxweb_niz(tablica, pokazatelj_neto_placa)
            tocke = u_eure(tocke)
            vrijednosti = [v for _, v in tocke]
            if len(tocke) < 6 or not (300 < sorted(vrijednosti)[len(vrijednosti) // 2] < 5000):
                raise ValueError("niz ne izgleda kao neto placa u eurima "
                                 "(medijan {})".format(sorted(vrijednosti)[len(vrijednosti) // 2]
                                                       if vrijednosti else "-"))
            nizovi.append(tocke)
            upotrijebljeno.append(dict(tablica, **opis, od=tocke[0][0], do=tocke[-1][0]))
        except Exception as greska:          # noqa: BLE001
            odbaceno.append({"putanja": tablica["putanja"], "naziv": tablica["naziv"],
                             "razlog": str(greska)})
    DZS_DIJAGNOSTIKA["place_upotrijebljene"] = upotrijebljeno
    DZS_DIJAGNOSTIKA["place_odbacene"] = odbaceno
    if not nizovi:
        raise ValueError("nijedna tablica nije prosla provjeru: " +
                         "; ".join("{} ({})".format(o["naziv"], o["razlog"]) for o in odbaceno))
    return spoji_nizove(nizovi)


# --- Indeks potrosackih cijena iz PxWeb-a (za realnu placu) -----------------

def ocjena_tablice_ipc(naziv):
    n = mali(naziv)
    if "potroša" not in n and "potrosa" not in n:
        return None
    if "harmoniz" in n or "hicp" in n:
        return None
    ocjena = 1
    for rijec in ("indeks", "mjeseč", "mjesec", "prethodne godine"):
        if rijec in n:
            ocjena += 1
    return ocjena


def dzs_pxweb_ipc():
    """Vrati godisnju stopu inflacije prema DZS-ovom IPC-u [(t, posto)]."""
    katalog = pxweb_katalog(("cijen",))
    DZS_DIJAGNOSTIKA["ipc_katalog"] = katalog
    kandidati = sorted([(ocjena_tablice_ipc(t["naziv"]), t) for t in katalog
                        if ocjena_tablice_ipc(t["naziv"])], key=lambda k: -k[0])[:6]
    odbaceno = []
    for _, tablica in kandidati:
        tip = {}

        def odabir(tekstovi, kodovi):
            for i, t in enumerate(tekstovi):
                if "isti mjesec prethodne godine" in mali(t):
                    tip["vrsta"] = "godisnji_indeks"
                    return i
            for i, t in enumerate(tekstovi):
                if "godišnj" in mali(t) and "stop" in mali(t):
                    tip["vrsta"] = "godisnja_stopa"
                    return i
            for i, t in enumerate(tekstovi):
                if re.search(r"(19|20)\d{2}\s*=\s*100", t):
                    tip["vrsta"] = "bazni_indeks"
                    return i
            return None

        try:
            tocke, opis = pxweb_niz(tablica, odabir)
            naslov = mali(tablica["naziv"] + " " + (opis.get("naslov") or ""))
            vrsta = tip.get("vrsta")
            if vrsta is None:
                if "isti mjesec prethodne godine" in naslov:
                    vrsta = "godisnji_indeks"
                elif re.search(r"(19|20)\d{2}\s*=\s*100", naslov):
                    vrsta = "bazni_indeks"
            if vrsta == "godisnji_indeks":
                stope = [(t, round(v - 100, 2)) for t, v in tocke]
            elif vrsta == "godisnja_stopa":
                stope = [(t, v) for t, v in tocke]
            elif vrsta == "bazni_indeks":
                stope = godisnje_stope(tocke)
            else:
                raise ValueError("ne znam je li indeks bazni ili godisnji")
            if len(stope) < 12:
                raise ValueError("prekratak niz ({} mj.)".format(len(stope)))
            DZS_DIJAGNOSTIKA.setdefault("ipc_upotrijebljene", []).append(
                dict(tablica, **opis, vrsta=vrsta, od=stope[0][0], do=stope[-1][0]))
            return stope
        except Exception as greska:          # noqa: BLE001
            odbaceno.append({"putanja": tablica["putanja"], "naziv": tablica["naziv"],
                             "razlog": str(greska)})
    DZS_DIJAGNOSTIKA["ipc_odbacene"] = odbaceno
    raise ValueError("IPC nije pronaden ({} kandidata odbaceno)".format(len(odbaceno)))


# --- Rezerva: datoteka "Statistika u nizu" (place.xlsx) ----------------------

def xlsx_listovi(bajtovi):
    """Procitaj .xlsx bez dodatnih paketa: vrati {list: {(redak, stupac): vrijednost}}."""
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
          "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
    z = zipfile.ZipFile(io.BytesIO(bajtovi))
    dijeljeni = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", ns):
            dijeljeni.append("".join(t.text or "" for t in si.iter(
                "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t")))
    veze = {}
    for rel in ET.fromstring(z.read("xl/_rels/workbook.xml.rels")):
        veze[rel.get("Id")] = rel.get("Target")
    listovi = {}
    for sheet in ET.fromstring(z.read("xl/workbook.xml")).find("m:sheets", ns):
        cilj = veze.get(sheet.get("{%s}id" % ns["r"]), "")
        cilj = cilj.lstrip("/")
        cilj = cilj if cilj.startswith("xl/") else "xl/" + cilj
        celije = {}
        for c in ET.fromstring(z.read(cilj)).iter("{%s}c" % ns["m"]):
            ref = re.match(r"([A-Z]+)(\d+)", c.get("r", ""))
            if not ref:
                continue
            stupac = 0
            for slovo in ref.group(1):
                stupac = stupac * 26 + ord(slovo) - 64
            v = c.find("m:v", ns)
            if c.get("t") == "s" and v is not None:
                vrijednost = dijeljeni[int(v.text)]
            elif c.get("t") == "inlineStr":
                vrijednost = "".join(t.text or "" for t in c.iter("{%s}t" % ns["m"]))
            elif v is not None:
                try:
                    vrijednost = float(v.text)
                except (TypeError, ValueError):
                    vrijednost = v.text
            else:
                continue
            celije[(int(ref.group(2)), stupac)] = vrijednost
        listovi[sheet.get("name")] = celije
    return listovi


def xlsx_neto_niz(celije):
    """U jednom listu nadi stupac s neto placom i procitaj mjesecni niz.
    Godina se cesto pise samo u prvom retku godine, pa se 'nosi' prema dolje."""
    najbolji = []
    zaglavlja = [(r, s, v) for (r, s), v in celije.items()
                 if isinstance(v, str) and "neto" in mali(v)
                 and not any(x in mali(v) for x in ("sat", "real", "indeks", "medijal"))]
    zadnji_redak = max((r for r, _ in celije), default=0)
    for r0, s0, naslov in zaglavlja:
        tocke, godina = [], None
        for r in range(r0 + 1, zadnji_redak + 1):
            razdoblje = None
            for s in range(1, min(s0, 3)):        # razdoblje je u stupcima A i B
                v = celije.get((r, s))
                if v is None:
                    continue
                if isinstance(v, float) and 1990 <= v <= 2100 and v == int(v):
                    godina = int(v)
                elif isinstance(v, float) and 30000 < v < 60000:
                    d = datetime.date(1899, 12, 30) + datetime.timedelta(days=int(v))
                    razdoblje = "{}-{:02d}".format(d.year, d.month)
                elif isinstance(v, str):
                    razdoblje = u_mjesec(v) or razdoblje
                    g = re.fullmatch(r"\s*((19|20)\d{2})\.?\s*", v)
                    if g:
                        godina = int(g.group(1))
                    elif not razdoblje and godina:
                        nizak = mali(v)
                        for korijen, broj_mj in MJESECI_IMENA:
                            if nizak.startswith(korijen):
                                razdoblje = "{}-{:02d}".format(godina, broj_mj)
                                break
                        rim = re.fullmatch(r"\s*(XII|XI|IX|X|VIII|VII|VI|IV|V|III|II|I)\.?\s*", v)
                        if rim:
                            razdoblje = "{}-{:02d}".format(godina, RIMSKI[rim.group(1)])
            vrijednost = celije.get((r, s0))
            if razdoblje and isinstance(vrijednost, float):
                tocke.append((razdoblje, vrijednost))
        tocke = sorted(dict(tocke).items())
        if len(tocke) > len(najbolji) or (tocke and najbolji and tocke[-1][0] > najbolji[-1][0]):
            najbolji = tocke
    return najbolji


def dzs_xlsx_place():
    listovi = xlsx_listovi(dohvati_bajtove(DZS_PLACE_XLSX))
    nizovi = {ime: xlsx_neto_niz(c) for ime, c in listovi.items()}
    DZS_DIJAGNOSTIKA["xlsx_listovi"] = {ime: (len(n), n[0][0] if n else None,
                                              n[-1][0] if n else None)
                                        for ime, n in nizovi.items()}
    nizovi = [u_eure(n) for n in nizovi.values() if len(n) >= 24]
    if not nizovi:
        raise ValueError("u place.xlsx nije pronaden mjesecni niz neto place")
    return spoji_nizove(nizovi)


def realne_stope(nominalne, inflacija):
    """Realna godisnja stopa = (1 + nominalna) / (1 + inflacija) - 1."""
    infl = dict(inflacija)
    return [(t, round(((1 + n / 100) / (1 + infl[t] / 100) - 1) * 100, 2))
            for t, n in nominalne if t in infl]


def provjeri_deflator(stope, usporedba):
    """IPC i HICP se razlikuju, ali ne iz godine u godinu za vise postotnih bodova.
    Veliko odstupanje znaci da je skripta procitala krivi stupac."""
    usp = dict(usporedba)
    razlike = [abs(v - usp[t]) for t, v in stope if t in usp]
    if len(razlike) < 12:
        return True
    return sum(razlike) / len(razlike) < 2.0


# ---------------------------------------------------------------------------
# 4. GLAVNI DIO
# ---------------------------------------------------------------------------

def skini_iz_izvora(izvor):
    """Skine seriju iz jednog konkretnog izvora."""
    if izvor["api"] == "dzs_pxweb_place":
        return dzs_pxweb_place()
    if izvor["api"] == "dzs_xlsx_place":
        return dzs_xlsx_place()
    if izvor["api"] == "dzs_pxweb_ipc":
        return dzs_pxweb_ipc()
    if izvor["api"] == "eurostat":
        return eurostat_serija(izvor["tablica"], izvor["filtri"])
    return ecb_serija(izvor["skup"], izvor["kljuc"])


def prvi_koji_radi(izvori):
    """Proba izvore redom i vrati prvi koji uspije, uz popis onih koji nisu."""
    greske = []
    for izvor in izvori:
        try:
            return skini_iz_izvora(izvor), izvor, greske
        except Exception as greska:          # noqa: BLE001
            opis = izvor.get("tablica") or izvor.get("kljuc")
            greske.append("{}: {}".format(opis, greska))
    return None, None, greske


def obradi(pokazatelj):
    """Skine jedan pokazatelj i vrati gotov rjecnik spreman za spremanje."""
    tocke, upotrijebljeni, greske = prvi_koji_radi(pokazatelj["izvori"])
    if tocke is None:
        raise ValueError(" | ".join(greske))

    # Godisnja stopa promjene.
    # Ako pokazatelj ima "izvori_stope", uzimamo sluzbeno objavljenu stopu -
    # ona je ono sto Eurostat i DZS navode u priopcenjima, i ona ide u tekst.
    # Sami je racunamo samo ako sluzbene nema.
    podrijetlo_stope = "izracunato"
    stope = []
    if pokazatelj.get("izvori_stope"):
        sluzbene, izvor_stope, greske_stope = prvi_koji_radi(pokazatelj["izvori_stope"])
        if sluzbene:
            stope = sluzbene
            podrijetlo_stope = "sluzbeno: " + (izvor_stope.get("tablica") or izvor_stope.get("kljuc"))
        else:
            greske.extend(greske_stope)

    if not stope and pokazatelj["jedinica"] != "posto":
        stope = godisnje_stope(tocke)

    if pokazatelj["prikaz"] == "god_stopa" and stope:
        glavna_serija = stope
    else:
        glavna_serija = tocke

    # Realna godisnja stopa (samo za pokazatelje koji imaju "deflatori").
    realne, deflator = [], None
    if pokazatelj.get("deflatori") and stope:
        hicp = None
        for izvor in pokazatelj["deflatori"]:
            try:
                inflacija = skini_iz_izvora(izvor)
                if izvor["api"] == "eurostat":
                    hicp = inflacija
                elif hicp is None:
                    # DZS-ov IPC usporedujemo s HICP-om da uhvatimo krivo procitan stupac
                    rezerva = [d for d in pokazatelj["deflatori"] if d["api"] == "eurostat"]
                    hicp = skini_iz_izvora(rezerva[0]) if rezerva else []
                    if not provjeri_deflator(inflacija, hicp):
                        raise ValueError("odstupa od HICP-a vise od 2 p.b. u prosjeku")
                realne = realne_stope(stope, inflacija)
                if realne:
                    deflator = izvor["oznaka"]
                    break
            except Exception as greska:          # noqa: BLE001
                greske.append("deflator {}: {}".format(izvor["oznaka"], greska))

    zadnja = glavna_serija[-1][1]
    pretposljednja = glavna_serija[-2][1] if len(glavna_serija) > 1 else None
    promjena = round(zadnja - pretposljednja, 2) if pretposljednja is not None else None

    return {
        "id": pokazatelj["id"],
        "naziv": pokazatelj["naziv"],
        "podnaslov": pokazatelj["podnaslov"],
        "izvor": upotrijebljeni["oznaka"],
        "jedinica": pokazatelj["jedinica"],
        "prikaz": pokazatelj["prikaz"],
        "zadnje_razdoblje": glavna_serija[-1][0],
        "zadnja_vrijednost": zadnja,
        "promjena": promjena,
        "razine": [{"t": t, "v": v} for t, v in tocke],
        "god_stope": [{"t": t, "v": v} for t, v in stope],
        **({"realne_god_stope": [{"t": t, "v": v} for t, v in realne],
            "deflator": deflator} if pokazatelj.get("deflatori") else {}),
        "_dijagnostika": {
            "upotrijebljeni_izvor": upotrijebljeni.get("tablica") or upotrijebljeni.get("kljuc"),
            "podrijetlo_stope": podrijetlo_stope,
            "neuspjeli_izvori": greske,
        },
    }


def main():
    os.makedirs(MAPA_PODATAKA, exist_ok=True)

    izvjestaj = []
    uspjelo, palo, zastarjelo = 0, 0, 0

    for pokazatelj in POKAZATELJI:
        oznaka = pokazatelj["id"]
        try:
            rezultat = obradi(pokazatelj)

            kasni = mjeseci_kasnjenja(rezultat["zadnje_razdoblje"])
            je_zastarjelo = kasni is not None and kasni > DOPUSTENO_KASNJENJE_MJESECI

            with open(os.path.join(MAPA_PODATAKA, oznaka + ".json"), "w",
                      encoding="utf-8") as f:
                json.dump(rezultat, f, ensure_ascii=False, indent=1)

            stavka = {
                "id": oznaka,
                "status": "zastarjelo" if je_zastarjelo else "ok",
                "zadnje_razdoblje": rezultat["zadnje_razdoblje"],
                "kasni_mjeseci": kasni,
                "izvor": rezultat["_dijagnostika"]["upotrijebljeni_izvor"],
                "stopa": rezultat["_dijagnostika"]["podrijetlo_stope"],
            }
            if rezultat["_dijagnostika"]["neuspjeli_izvori"]:
                stavka["preskoceni_izvori"] = rezultat["_dijagnostika"]["neuspjeli_izvori"]
            izvjestaj.append(stavka)

            if je_zastarjelo:
                zastarjelo += 1
                print("STARO {:<18} zadnje: {} (kasni {} mj.) - provjeri je li "
                      "tablica ugasena".format(oznaka, rezultat["zadnje_razdoblje"], kasni),
                      flush=True)
            else:
                uspjelo += 1
                print("OK    {:<18} {} = {}".format(
                    oznaka, rezultat["zadnje_razdoblje"], rezultat["zadnja_vrijednost"]),
                    flush=True)

        except Exception as greska:          # noqa: BLE001
            # Ako jedan pokazatelj padne, ostali se svejedno azuriraju,
            # a stara datoteka tog pokazatelja ostaje netaknuta.
            palo += 1
            izvjestaj.append({"id": oznaka, "status": "greska", "poruka": str(greska)})
            print("PALO  {:<18} {}".format(oznaka, greska), flush=True)

    # Sto je skripta vidjela u DZS-ovoj bazi - za popravke kad DZS nesto preimenuje.
    if DZS_DIJAGNOSTIKA:
        DZS_DIJAGNOSTIKA["vrijeme"] = datetime.datetime.now(
            datetime.timezone.utc).isoformat(timespec="seconds")
        with open(os.path.join(MAPA_PODATAKA, "dzs_dijagnostika.json"), "w",
                  encoding="utf-8") as f:
            json.dump(DZS_DIJAGNOSTIKA, f, ensure_ascii=False, indent=1)

    meta = {
        "azurirano": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "uspjelo": uspjelo,
        "zastarjelo": zastarjelo,
        "palo": palo,
        "pokazatelji": izvjestaj,
    }
    with open(os.path.join(MAPA_PODATAKA, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)

    print("\nGotovo: {} svjeze, {} zastarjelo, {} neuspjelo.".format(
        uspjelo, zastarjelo, palo))

    if uspjelo == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
