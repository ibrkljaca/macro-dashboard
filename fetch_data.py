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

Autor obrade podataka: I. Brkljaca
"""

import json
import csv
import io
import sys
import datetime
import urllib.request
import urllib.error

# ---------------------------------------------------------------------------
# 1. POSTAVKE
# ---------------------------------------------------------------------------

POCETNO_RAZDOBLJE = "2015-01"   # od kada skidamo povijest
MAPA_PODATAKA = "data"
TIMEOUT = 60

EUROSTAT_BAZA = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data"
ECB_BAZA = "https://data-api.ecb.europa.eu/service/data"


# ---------------------------------------------------------------------------
# 2. POPIS POKAZATELJA
# ---------------------------------------------------------------------------
# Svaki pokazatelj je jedan redak ovdje. Da bi se dodao novi pokazatelj,
# dovoljno je dopisati jedan ovakav blok - nista drugo se ne mijenja.
#
#   id            - ime datoteke koja nastaje (data/<id>.json)
#   naziv         - naslov kartice na dashboardu
#   podnaslov     - mali opisni tekst ispod naslova
#   izvor         - tekst koji pise u donjem desnom kutu kartice
#   jedinica      - "indeks", "posto", "mio_eur"
#   prikaz        - sto se prikazuje kao velika brojka na kartici:
#                   "razina"    = zadnja vrijednost serije
#                   "god_stopa" = godisnja stopa promjene (%)
#   api           - "eurostat" ili "ecb"
#   ostalo        - parametri upita, ovisno o izvoru

POKAZATELJI = [
    {
        "id": "ind_proizvodnja",
        "naziv": "Industrijska proizvodnja",
        "podnaslov": "Indeks obujma, sezonski prilagodeno (2021 = 100)",
        "izvor": "Eurostat",
        "jedinica": "indeks",
        "prikaz": "god_stopa",
        "api": "eurostat",
        "tablica": "sts_inpr_m",
        "filtri": {"geo": "HR", "nace_r2": "B-D", "indic_bt": "PRD",
                   "s_adj": "SCA", "unit": "I21"},
    },
    {
        "id": "maloprodaja",
        "naziv": "Trgovina na malo",
        "podnaslov": "Indeks obujma prodaje, sezonski prilagodeno (2021 = 100)",
        "izvor": "Eurostat",
        "jedinica": "indeks",
        "prikaz": "god_stopa",
        "api": "eurostat",
        "tablica": "sts_trtu_m",
        "filtri": {"geo": "HR", "nace_r2": "G47", "indic_bt": "VOL_SLS",
                   "s_adj": "SCA", "unit": "I21"},
    },
    {
        "id": "gradjevinarstvo",
        "naziv": "Gradevinarstvo",
        "podnaslov": "Indeks obujma gradevinskih radova (2021 = 100)",
        "izvor": "Eurostat",
        "jedinica": "indeks",
        "prikaz": "god_stopa",
        "api": "eurostat",
        "tablica": "sts_copr_m",
        "filtri": {"geo": "HR", "nace_r2": "F", "indic_bt": "PRD",
                   "s_adj": "SCA", "unit": "I21"},
    },
    {
        "id": "izvoz",
        "naziv": "Izvoz robe",
        "podnaslov": "Ukupan izvoz, milijuni eura, sezonski prilagodeno",
        "izvor": "Eurostat",
        "jedinica": "mio_eur",
        "prikaz": "god_stopa",
        "api": "eurostat",
        "tablica": "ei_eteu27_2020_m",
        "filtri": {"geo": "HR", "stk_flow": "EXP", "partner": "WORLD",
                   "unit": "MIO-EUR-SA", "indic": "ET-T"},
    },
    {
        "id": "uvoz",
        "naziv": "Uvoz robe",
        "podnaslov": "Ukupan uvoz, milijuni eura, sezonski prilagodeno",
        "izvor": "Eurostat",
        "jedinica": "mio_eur",
        "prikaz": "god_stopa",
        "api": "eurostat",
        "tablica": "ei_eteu27_2020_m",
        "filtri": {"geo": "HR", "stk_flow": "IMP", "partner": "WORLD",
                   "unit": "MIO-EUR-SA", "indic": "ET-T"},
    },
    {
        "id": "hicp_hr",
        "naziv": "Inflacija (HICP)",
        "podnaslov": "Godisnja stopa promjene cijena",
        "izvor": "Eurostat (HICP)",
        "jedinica": "posto",
        "prikaz": "razina",
        "api": "eurostat",
        "tablica": "prc_hicp_manr",
        "filtri": {"geo": "HR", "coicop": "CP00", "unit": "RCH_A"},
    },
    {
        "id": "hicp_temeljna",
        "naziv": "Temeljna inflacija",
        "podnaslov": "HICP bez energije, hrane, alkohola i duhana",
        "izvor": "Eurostat (HICP)",
        "jedinica": "posto",
        "prikaz": "razina",
        "api": "eurostat",
        "tablica": "prc_hicp_manr",
        # Eurostat je kroz godine mijenjao sifru za temeljnu inflaciju,
        # pa probamo redom dok jedna ne uspije.
        "filtri": {"geo": "HR", "coicop": "TOT_X_NRG_FOOD_NP", "unit": "RCH_A"},
        "zamjenski_filtri": [
            {"geo": "HR", "coicop": "TOT_X_NRG_FOOD", "unit": "RCH_A"},
            {"geo": "HR", "coicop": "TOT_X_NRG_FOOD_NP_UNPR_FOOD", "unit": "RCH_A"},
        ],
    },
    {
        "id": "hicp_ea",
        "naziv": "Inflacija u europodrucju",
        "podnaslov": "Godisnja stopa promjene cijena, EA20",
        "izvor": "Eurostat (HICP)",
        "jedinica": "posto",
        "prikaz": "razina",
        "api": "eurostat",
        "tablica": "prc_hicp_manr",
        "filtri": {"geo": "EA20", "coicop": "CP00", "unit": "RCH_A"},
    },
    {
        "id": "esi",
        "naziv": "Ekonomski sentiment",
        "podnaslov": "Kompozitni indeks sentimenta (dugorocni prosjek = 100)",
        "izvor": "Eurostat (DG ECFIN)",
        "jedinica": "indeks",
        "prikaz": "razina",
        "api": "eurostat",
        "tablica": "ei_bssi_m_r2",
        "filtri": {"geo": "HR", "indic": "BS-ESI-I", "s_adj": "SA"},
    },
    {
        "id": "stambeni_krediti",
        "naziv": "Kamate na stambene kredite",
        "podnaslov": "Prosjecna kamata na nove stambene kredite kucanstvima",
        "izvor": "ECB (MIR)",
        "jedinica": "posto",
        "prikaz": "razina",
        "api": "ecb",
        "skup": "MIR",
        "kljuc": "M.HR.B.A2C.A.R.A.2250.EUR.N",
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

    # Gdje je vremenska dimenzija u nizu dimenzija
    i_vrijeme = dimenzije.index("time")

    # Sigurnosna provjera: filtri moraju suziti upit na tocno jednu seriju.
    # Ako neka dimenzija osim vremena ima vise clanova, upit je premalo odreden
    # i podaci bi se pomijesali - bolje da odmah javi gresku.
    for i, ime in enumerate(dimenzije):
        if i != i_vrijeme and velicine[i] > 1:
            raise ValueError(
                "Upit nije dovoljno odreden: dimenzija '{}' ima {} clanova. "
                "Dodaj je u filtre.".format(ime, velicine[i])
            )

    # Obrnuta mapa: redni broj -> oznaka razdoblja (npr. 12 -> "2016-01")
    indeks_vremena = podaci["dimension"]["time"]["category"]["index"]
    razdoblje_po_poziciji = {v: k for k, v in indeks_vremena.items()}

    # Koliko "koraka" u ravnom nizu vrijedi jedan pomak po vremenskoj dimenziji
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


# ---------------------------------------------------------------------------
# 4. GLAVNI DIO
# ---------------------------------------------------------------------------

def obradi(pokazatelj):
    """Skine jedan pokazatelj i vrati gotov rjecnik spreman za spremanje."""
    if pokazatelj["api"] == "eurostat":
        pokusaji = [pokazatelj["filtri"]] + pokazatelj.get("zamjenski_filtri", [])
        zadnja_greska = None
        tocke = None
        for filtri in pokusaji:
            try:
                tocke = eurostat_serija(pokazatelj["tablica"], filtri)
                break
            except Exception as greska:      # noqa: BLE001
                zadnja_greska = greska
        if tocke is None:
            raise zadnja_greska
    else:
        tocke = ecb_serija(pokazatelj["skup"], pokazatelj["kljuc"])

    stope = godisnje_stope(tocke) if pokazatelj["jedinica"] != "posto" else []

    # Velika brojka na kartici i promjena u odnosu na prethodno razdoblje
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
        "izvor": pokazatelj["izvor"],
        "jedinica": pokazatelj["jedinica"],
        "prikaz": pokazatelj["prikaz"],
        "zadnje_razdoblje": glavna_serija[-1][0],
        "zadnja_vrijednost": zadnja,
        "promjena": promjena,
        "razine": [{"t": t, "v": v} for t, v in tocke],
        "god_stope": [{"t": t, "v": v} for t, v in stope],
    }


def main():
    import os
    os.makedirs(MAPA_PODATAKA, exist_ok=True)

    izvjestaj = []
    uspjelo, palo = 0, 0

    for pokazatelj in POKAZATELJI:
        oznaka = pokazatelj["id"]
        try:
            rezultat = obradi(pokazatelj)
            putanja = os.path.join(MAPA_PODATAKA, oznaka + ".json")
            with open(putanja, "w", encoding="utf-8") as f:
                json.dump(rezultat, f, ensure_ascii=False, indent=1)
            poruka = "OK   {:<20} zadnje: {} = {}".format(
                oznaka, rezultat["zadnje_razdoblje"], rezultat["zadnja_vrijednost"]
            )
            izvjestaj.append({"id": oznaka, "status": "ok",
                              "zadnje_razdoblje": rezultat["zadnje_razdoblje"]})
            uspjelo += 1
        except Exception as greska:          # noqa: BLE001
            # Vazno: ako jedan pokazatelj padne, ostali se svejedno azuriraju,
            # a stara datoteka tog pokazatelja ostaje netaknuta.
            poruka = "PALO {:<20} {}".format(oznaka, greska)
            izvjestaj.append({"id": oznaka, "status": "greska", "poruka": str(greska)})
            palo += 1
        print(poruka, flush=True)

    meta = {
        "azurirano": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "uspjelo": uspjelo,
        "palo": palo,
        "pokazatelji": izvjestaj,
    }
    with open(os.path.join(MAPA_PODATAKA, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)

    print("\nGotovo: {} uspjesno, {} neuspjesno.".format(uspjelo, palo))

    # Ako bas nista nije uspjelo, javi gresku da to vidimo u GitHub Actions.
    if uspjelo == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
