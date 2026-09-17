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
import sys
import datetime
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
    },
    {
        "id": "izvoz",
        "naziv": "Izvoz robe",
        "podnaslov": "Ukupan izvoz, milijuni eura, sezonski prilagodeno",
        "jedinica": "mio_eur",
        "prikaz": "god_stopa",
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
        "prikaz": "god_stopa",
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
        # Eurostat je 2026. ugasio tablicu prc_hicp_manr, pa je ECB sada prvi
        # izbor. ECB objavljuje isti HICP koji mu Eurostat salje.
        "izvori": [
            {"api": "ecb", "oznaka": "ECB (HICP)", "skup": "ICP",
             "kljuc": "M.HR.N.000000.4.ANR"},
            {"api": "eurostat", "oznaka": "Eurostat (HICP)", "tablica": "prc_hicp_minr",
             "filtri": {"geo": "HR", "coicop": "TOTAL", "unit": "RCH_A"}},
            {"api": "eurostat", "oznaka": "Eurostat (HICP)", "tablica": "prc_hicp_minr",
             "filtri": {"geo": "HR", "coicop": "CP00", "unit": "RCH_A"}},
        ],
    },
    {
        "id": "hicp_temeljna",
        "naziv": "Temeljna inflacija",
        "podnaslov": "HICP bez energije, hrane, alkohola i duhana",
        "jedinica": "posto",
        "prikaz": "razina",
        "izvori": [
            {"api": "ecb", "oznaka": "ECB (HICP)", "skup": "ICP",
             "kljuc": "M.HR.N.XEF000.4.ANR"},
            {"api": "eurostat", "oznaka": "Eurostat (HICP)", "tablica": "prc_hicp_minr",
             "filtri": {"geo": "HR", "coicop": "TOT_X_NRG_FOOD", "unit": "RCH_A"}},
            {"api": "eurostat", "oznaka": "Eurostat (HICP)", "tablica": "prc_hicp_minr",
             "filtri": {"geo": "HR", "coicop": "TOT_X_NRG_FOOD_NP", "unit": "RCH_A"}},
        ],
    },
    {
        "id": "hicp_ea",
        "naziv": "Inflacija u europodrucju",
        "podnaslov": "Godisnja stopa promjene cijena, EA20",
        "jedinica": "posto",
        "prikaz": "razina",
        "izvori": [
            {"api": "ecb", "oznaka": "ECB (HICP)", "skup": "ICP",
             "kljuc": "M.U2.N.000000.4.ANR"},
            {"api": "eurostat", "oznaka": "Eurostat (HICP)", "tablica": "prc_hicp_minr",
             "filtri": {"geo": "EA20", "coicop": "TOTAL", "unit": "RCH_A"}},
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
# 4. GLAVNI DIO
# ---------------------------------------------------------------------------

def skini_iz_izvora(izvor):
    """Skine seriju iz jednog konkretnog izvora."""
    if izvor["api"] == "eurostat":
        return eurostat_serija(izvor["tablica"], izvor["filtri"])
    return ecb_serija(izvor["skup"], izvor["kljuc"])


def obradi(pokazatelj):
    """Skine jedan pokazatelj i vrati gotov rjecnik spreman za spremanje."""
    tocke = None
    upotrijebljeni = None
    greske = []

    for izvor in pokazatelj["izvori"]:
        try:
            tocke = skini_iz_izvora(izvor)
            upotrijebljeni = izvor
            break
        except Exception as greska:          # noqa: BLE001
            opis = izvor.get("tablica") or izvor.get("kljuc")
            greske.append("{}: {}".format(opis, greska))

    if tocke is None:
        raise ValueError(" | ".join(greske))

    stope = godisnje_stope(tocke) if pokazatelj["jedinica"] != "posto" else []

    if pokazatelj["prikaz"] == "god_stopa" and stope:
        glavna_serija = stope
    else:
        glavna_serija = tocke

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
        "_dijagnostika": {
            "upotrijebljeni_izvor": upotrijebljeni.get("tablica") or upotrijebljeni.get("kljuc"),
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
