"""Télécharge la météo horaire belge (Open-Meteo) et la stocke dans SQLite.

Deux sources, dans la même table `weather` :
- 'observed'    : météo réellement observée (réanalyse ERA5), depuis 2015.
                  Sert à entraîner le modèle.
- 'forecast_d1' : prévision faite la veille (archive des anciens runs), depuis le 19/01/2024.
                  Sert à évaluer le modèle honnêtement : la veille, on ne connaît pas encore
                  la météo réelle de demain, seulement sa prévision.

La météo belge = moyenne de 5 grandes villes.

Usage :
    .venv\\Scripts\\python src\\ingest_weather.py
"""

import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

# --- Paramètres -------------------------------------------------------------
DB_PATH = Path(__file__).resolve().parent.parent / "data" / "elia.db"

CITIES = {  # nom: (latitude, longitude)
    "Bruxelles": (50.85, 4.35),
    "Anvers": (51.22, 4.40),
    "Gand": (51.05, 3.72),
    "Liège": (50.63, 5.57),
    "Charleroi": (50.41, 4.44),
}

SOURCES = {
    # source: (adresse de l'API, nom des variables demandées, première date disponible)
    "observed": (
        "https://archive-api.open-meteo.com/v1/archive",
        ["temperature_2m", "shortwave_radiation"],
        date(2015, 1, 1),
    ),
    "forecast_d1": (
        "https://previous-runs-api.open-meteo.com/v1/forecast",
        ["temperature_2m_previous_day1", "shortwave_radiation_previous_day1"],
        date(2024, 1, 20),
    ),
}
OVERLAP_DAYS = 7  # l'archive ERA5 a quelques jours de retard : on recharge la dernière semaine

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS weather (
    datetime_utc    TEXT NOT NULL,  -- début de l'heure, UTC
    source          TEXT NOT NULL,  -- 'observed' ou 'forecast_d1'
    temperature_c   REAL,           -- température à 2 m (°C), moyenne des 5 villes
    radiation_wm2   REAL,           -- rayonnement solaire (W/m²), moyenne des 5 villes
    ingested_at     TEXT NOT NULL,
    PRIMARY KEY (datetime_utc, source)
)
"""

UPSERT = """
INSERT INTO weather (datetime_utc, source, temperature_c, radiation_wm2, ingested_at)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT(datetime_utc, source) DO UPDATE SET
    temperature_c = excluded.temperature_c,
    radiation_wm2 = excluded.radiation_wm2,
    ingested_at   = excluded.ingested_at
"""


def start_date(conn: sqlite3.Connection, source: str) -> date:
    """Première date à télécharger pour cette source."""
    first_available = SOURCES[source][2]
    last = conn.execute(
        "SELECT MAX(datetime_utc) FROM weather WHERE source = ?", (source,)
    ).fetchone()[0]
    if last is None:
        return first_available
    return max(first_available, datetime.fromisoformat(last).date() - timedelta(days=OVERLAP_DAYS))


def download_year(source: str, start: date, end: date) -> pd.DataFrame:
    """Télécharge une période (au plus un an) pour les 5 villes et renvoie la moyenne horaire."""
    url, variables, _ = SOURCES[source]
    params = {
        "latitude": ",".join(str(lat) for lat, _ in CITIES.values()),
        "longitude": ",".join(str(lon) for _, lon in CITIES.values()),
        "hourly": ",".join(variables),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "timezone": "UTC",
    }
    resp = requests.get(url, params=params, timeout=120)
    resp.raise_for_status()

    # Une réponse par ville : on empile les 5 tableaux, puis on fait la moyenne par heure.
    per_city = [pd.DataFrame(city["hourly"]) for city in resp.json()]
    df = pd.concat(per_city).groupby("time").mean()
    df.columns = ["temperature_c", "radiation_wm2"]  # même ordre que `variables`
    return df.dropna(how="all")  # heures pas encore disponibles


def download(source: str, start: date, end: date) -> pd.DataFrame:
    """Découpe la période en tranches d'un an pour garder des requêtes raisonnables."""
    chunks = []
    while start <= end:
        chunk_end = min(end, date(start.year, 12, 31))
        chunks.append(download_year(source, start, chunk_end))
        start = chunk_end + timedelta(days=1)
    return pd.concat(chunks)


def save(conn: sqlite3.Connection, source: str, df: pd.DataFrame) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    rows = [
        (pd.Timestamp(t).strftime("%Y-%m-%d %H:%M:%S"), source,
         None if pd.isna(r.temperature_c) else r.temperature_c,
         None if pd.isna(r.radiation_wm2) else r.radiation_wm2,
         now)
        for t, r in df.iterrows()
    ]
    with conn:
        conn.executemany(UPSERT, rows)


def main() -> None:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(CREATE_TABLE)

    yesterday = date.today() - timedelta(days=1)
    for source in SOURCES:
        start = start_date(conn, source)
        print(f"{source} : téléchargement du {start} au {yesterday} ...")
        df = download(source, start, yesterday)
        save(conn, source, df)
        n, first, last = conn.execute(
            "SELECT COUNT(*), MIN(datetime_utc), MAX(datetime_utc) FROM weather WHERE source = ?",
            (source,),
        ).fetchone()
        print(f"  {len(df)} heures traitées. Table : {n} heures, de {first} à {last} (UTC).")
    conn.close()


if __name__ == "__main__":
    main()
