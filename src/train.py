"""Entraîne le modèle J+1 et écrit ses prévisions sur la période de test dans SQLite.

Même découpage que le notebook 02 :
- entraînement 2016-2023 avec la météo observée (sans le 1er confinement) ;
- prévisions à partir du 21/01/2024 avec la météo prévue la veille.

Les prévisions vont dans la table `predictions`, que lit le tableau de bord.

Usage :
    .venv\\Scripts\\python src\\train.py
"""

import sqlite3
from datetime import datetime, timezone

from sklearn.ensemble import HistGradientBoostingRegressor

from features import DB_PATH, FEATURES, TARGET, build_dataset

TEST_START = "2024-01-21"

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS predictions (
    datetime_utc    TEXT PRIMARY KEY,   -- début de l'heure, UTC
    actual_mw       REAL,               -- consommation mesurée (moyenne horaire)
    model_mw        REAL NOT NULL,      -- prévision J+1 de notre modèle
    elia_mw         REAL,               -- prévision J+1 d'Elia
    created_at      TEXT NOT NULL
)
"""


def main() -> None:
    obs = build_dataset("observed")
    fc = build_dataset("forecast_d1")

    train = obs.loc["2016":"2023"].drop(obs.loc["2020-03-15":"2020-06-30"].index)
    train = train.dropna(subset=FEATURES + [TARGET])
    model = HistGradientBoostingRegressor(
        max_iter=500, learning_rate=0.05, categorical_features=["dayofweek"], random_state=0
    )
    model.fit(train[FEATURES], train[TARGET])

    # On prévoit toutes les heures où les variables existent, même si la mesure n'est pas encore là.
    test = fc.loc[TEST_START:].dropna(subset=FEATURES)
    test = test.assign(model_mw=model.predict(test[FEATURES]))

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    rows = [
        (t.strftime("%Y-%m-%d %H:%M:%S"),
         None if r[TARGET] != r[TARGET] else r[TARGET],   # NaN -> NULL
         r["model_mw"],
         None if r["elia_dayahead_mw"] != r["elia_dayahead_mw"] else r["elia_dayahead_mw"],
         now)
        for t, r in test.iterrows()
    ]
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DROP TABLE IF EXISTS predictions")  # on recalcule tout à chaque entraînement
        conn.execute(CREATE_TABLE)
        conn.executemany("INSERT INTO predictions VALUES (?, ?, ?, ?, ?)", rows)
    print(f"{len(rows)} prévisions écrites, du {test.index.min():%Y-%m-%d} au {test.index.max():%Y-%m-%d}.")


if __name__ == "__main__":
    main()
