# Macro Dashboard — Hrvatska

Interaktivni prikaz deset makroekonomskih pokazatelja za Hrvatsku, koji se
sam ažurira svaki dan iz službenih izvora.

## Što se ovdje nalazi

```
macro-dashboard/
├── fetch_data.py                ← skripta koja skida podatke
├── data/                        ← ovdje robot sprema svježe podatke (JSON)
├── index.html                   ← sama stranica (dolazi u sljedećem koraku)
└── .github/workflows/update.yml ← nalog GitHubu da skriptu pokreće svaki dan
```

## Pokazatelji i izvori

| Pokazatelj | Izvor | Tablica / ključ |
|---|---|---|
| Industrijska proizvodnja | Eurostat | `sts_inpr_m` |
| Trgovina na malo | Eurostat | `sts_trtu_m` |
| Građevinarstvo | Eurostat | `sts_copr_m` |
| Izvoz robe | Eurostat | `ei_eteu27_2020_m` |
| Uvoz robe | Eurostat | `ei_eteu27_2020_m` |
| Inflacija (HICP) | Eurostat | `prc_hicp_manr` |
| Temeljna inflacija | Eurostat | `prc_hicp_manr` |
| Inflacija u europodručju | Eurostat | `prc_hicp_manr` |
| Ekonomski sentiment (ESI) | Eurostat (DG ECFIN) | `ei_bssi_m_r2` |
| Kamate na stambene kredite | ECB | `MIR` |
| Neto plaća | DZS | PxWeb baza „Zaposlenost i plaće” (rezerva: `place.xlsx`) |

Svi podaci počinju od siječnja 2015.

### Godišnje stope promjene

Za industrijsku proizvodnju, trgovinu na malo i građevinarstvo preuzima se
**službeno objavljena** godišnja stopa (kalendarski prilagođen niz), a ne stopa
izračunata iz sezonski prilagođenog indeksa. Razlika zna biti i 0,8 postotnih
bodova, pa u tekst ide službena brojka — ona koju navode Eurostat i DZS.

Gdje službene stope nema, skripta je računa sama i to bilježi u
`data/meta.json` pod `stopa`.

### Neto plaća (DZS)

DZS nema stalne šifre tablica kao Eurostat, pa skripta svaki put sama pronađe
tablicu s prosječnom mjesečnom isplaćenom neto plaćom u PC-Axis bazi, provjeri
je i spoji nizove (npr. tablicu po NKD-u 2007. i onu po NKD-u 2025.). Iznosi
prije 2023. preračunavaju se iz kuna po tečaju 7,53450. Realna stopa računa se
DZS-ovim indeksom potrošačkih cijena; ako on nije dostupan, HICP-om, i to piše
na kartici. Sve što je skripta pronašla zapisuje se u `data/dzs_dijagnostika.json`.

## Kako se ažurira

GitHub Actions pokreće `fetch_data.py` svaki dan u 05:00 UTC
(07:00 ljeti, 06:00 zimi po hrvatskom vremenu). Skripta skine serije,
izračuna godišnje stope promjene i zapiše rezultat u `data/`.

Ako neki pokazatelj ne uspije, ostali se svejedno ažuriraju, a zadnja
ispravna verzija tog pokazatelja ostaje netaknuta. Status svakog pokretanja
zapisan je u `data/meta.json`.

Ručno pokretanje: kartica **Actions** → *Azuriraj podatke* → *Run workflow*.

## Dodavanje novog pokazatelja

U `fetch_data.py`, u popis `POKAZATELJI`, dopiše se jedan blok po
uzoru na postojeće. Ništa drugo se ne mijenja.

---

Obrada: I. Brkljača
