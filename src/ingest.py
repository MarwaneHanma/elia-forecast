"""Télécharge la consommation électrique belge (Elia, jeu ods001) et la stocke dans SQLite.

Premier lancement : tout l'historique depuis 2015.
Lancements suivants : seulement les jours récents (mise à jour incrémentale).

Usage :
    .venv\\Scripts\\python src\\ingest.py
"""

import io
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

# --- Paramètres -------------------------------------------------------------
DB_PATH = Path(__file__).resolve().parent.parent / "data" / "elia.db"
EXPORT_URL = (
    "https://opendata.elia.be/api/explore/v2.1/catalog/datasets/ods001/exports/csv"
)
FIELDS = "datetime,resolutioncode,totalload,dayaheadforecast,mostrecentforecast"
START = "2015-01-01T00:00:00Z"  # début de l'historique disponible
OVERLAP_DAYS = 3  # on retélécharge 3 jours pour récupérer les valeurs corrigées

# --- Base de données ----------------------------------------------------------
CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS load (
    datetime_utc        TEXT PRIMARY KEY,  -- début du quart d'heure, UTC
    total_load_mw       REAL,              -- consommation mesurée (MW)
    dayahead_fc_mw      REAL,              -- prévision J+1 d'Elia (MW)
    mostrecent_fc_mw    REAL,              -- dernière prévision d'Elia (MW)
    ingested_at         TEXT NOT NULL      -- date du téléchargement
)
"""

UPSERT = """
INSERT INTO load (datetime_utc, total_load_mw, dayahead_fc_mw, mostrecent_fc_mw, ingested_at)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT(datetime_utc) DO UPDATE SET
    total_load_mw    = excluded.total_load_mw,
    dayahead_fc_mw   = excluded.dayahead_fc_mw,
    mostrecent_fc_mw = excluded.mostrecent_fc_mw,
    ingested_at      = excluded.ingested_at
"""


def start_date(conn: sqlite3.Connection) -> str:
    """Date à partir de laquelle télécharger : début de l'historique ou dernière mesure - 3 jours."""
    last = conn.execute(
        "SELECT MAX(datetime_utc) FROM load WHERE total_load_mw IS NOT NULL"
    ).fetchone()[0]
    if last is None:
        return START
    since = datetime.fromisoformat(last).replace(tzinfo=timezone.utc) - timedelta(days=OVERLAP_DAYS)
    return since.strftime("%Y-%m-%dT%H:%M:%SZ")


def download(since: str) -> pd.DataFrame:
    """Télécharge en CSV toutes les lignes depuis `since` (aucune limite de lignes sur /exports)."""
    params = {
        "select": FIELDS,
        "where": f"datetime >= '{since}'",
        "order_by": "datetime",
        "timezone": "UTC",
    }
    resp = requests.get(EXPORT_URL, params=params, timeout=300)
    resp.raise_for_status()
    return pd.read_csv(io.StringIO(resp.text), sep=";")


def clean(df: pd.DataFrame) -> pd.DataFrame:
    """Contrôle et met en forme les données avant l'écriture en base."""
    unexpected = set(df["resolutioncode"]) - {"PT15M"}
    if unexpected:
        raise ValueError(f"Résolution inattendue : {unexpected}")

    df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    df = df.drop_duplicates(subset="datetime", keep="last").sort_values("datetime")

    return pd.DataFrame({
        "datetime_utc": df["datetime"].dt.strftime("%Y-%m-%d %H:%M:%S"),
        "total_load_mw": df["totalload"],
        "dayahead_fc_mw": df["dayaheadforecast"],
        "mostrecent_fc_mw": df["mostrecentforecast"],
    })


def save(conn: sqlite3.Connection, df: pd.DataFrame) -> None:
    """Insère les lignes, ou les met à jour si le quart d'heure existe déjà."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    rows = [
        (r.datetime_utc,
         None if pd.isna(r.total_load_mw) else r.total_load_mw,
         None if pd.isna(r.dayahead_fc_mw) else r.dayahead_fc_mw,
         None if pd.isna(r.mostrecent_fc_mw) else r.mostrecent_fc_mw,
         now)
        for r in df.itertuples(index=False)
    ]
    with conn:  # transaction : tout est écrit, ou rien
        conn.executemany(UPSERT, rows)


def main() -> None:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(CREATE_TABLE)

    since = start_date(conn)
    print(f"Téléchargement depuis {since} ...")
    df = clean(download(since))
    save(conn, df)

    n, first, last = conn.execute(
        "SELECT COUNT(*), MIN(datetime_utc), MAX(datetime_utc) FROM load"
    ).fetchone()
    print(f"{len(df)} lignes traitées. Base : {n} lignes, de {first} à {last} (UTC).")
    conn.close()


if __name__ == "__main__":
    main()
