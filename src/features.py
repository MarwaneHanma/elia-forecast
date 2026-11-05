"""Construit le tableau horaire « variables explicatives + cible » à partir de data/elia.db.

Une ligne = une heure (UTC). Toutes les variables sont connues la veille à midi,
au moment où l'on fait la prévision J+1 :
- calendrier (heure, jour, jour férié) : connu à l'avance ;
- météo : prévision de la veille ('forecast_d1') ou observée ('observed') pour l'entraînement ;
- consommation passée : seulement 48 h et 7 jours avant, déjà mesurée à ce moment-là.
"""

import sqlite3
from pathlib import Path

import holidays
import pandas as pd

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "elia.db"

TARGET = "load_mw"
FEATURES = [
    "hour", "dayofweek", "dayofyear", "is_holiday",
    "temperature_c", "temperature_24h", "heating_degrees", "radiation_wm2",
    "load_lag_48h", "load_lag_168h",
]


def build_dataset(weather_source: str, db_path: Path = DB_PATH) -> pd.DataFrame:
    with sqlite3.connect(db_path) as conn:
        load = pd.read_sql(
            "SELECT datetime_utc, total_load_mw, dayahead_fc_mw FROM load", conn,
            index_col="datetime_utc", parse_dates=["datetime_utc"],
        )
        weather = pd.read_sql(
            "SELECT datetime_utc, temperature_c, radiation_wm2 FROM weather WHERE source = ?",
            conn, params=(weather_source,),
            index_col="datetime_utc", parse_dates=["datetime_utc"],
        )
    load.index = load.index.tz_localize("UTC")
    weather.index = weather.index.tz_localize("UTC")

    # Quarts d'heure -> heures : moyenne des 4 valeurs, sur une grille horaire sans trou.
    df = load.resample("h").mean().rename(
        columns={"total_load_mw": TARGET, "dayahead_fc_mw": "elia_dayahead_mw"}
    )
    df = df.join(weather, how="left")

    # Calendrier, en heure belge : c'est elle qui rythme la vie des gens.
    local = df.index.tz_convert("Europe/Brussels")
    be_holidays = holidays.Belgium(years=range(local.year.min(), local.year.max() + 1))
    df["hour"] = local.hour
    df["dayofweek"] = local.dayofweek          # 0 = lundi
    df["dayofyear"] = local.dayofyear
    df["is_holiday"] = [d in be_holidays for d in local.date]

    # Météo : inertie thermique des bâtiments et besoin de chauffage.
    df["temperature_24h"] = df["temperature_c"].rolling(24, min_periods=12).mean()
    df["heating_degrees"] = (15 - df["temperature_c"]).clip(lower=0)

    # Consommation passée, déjà connue au moment de la prévision.
    df["load_lag_48h"] = df[TARGET].shift(48)
    df["load_lag_168h"] = df[TARGET].shift(168)
    return df
