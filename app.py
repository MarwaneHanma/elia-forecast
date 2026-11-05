"""Tableau de bord : prévisions J+1 (notre modèle et Elia) face à la consommation réelle.

Usage :
    .venv\\Scripts\\streamlit run app.py
"""

import sqlite3
from datetime import timedelta
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

DB_PATH = Path(__file__).resolve().parent / "data" / "elia.db"
COLORS = {"Réalité": "#8a8986", "Notre modèle": "#2a78d6", "Elia J+1": "#eb6834"}
SERIES = list(COLORS)
color_scale = alt.Scale(domain=SERIES, range=list(COLORS.values()))
# Pour les graphiques d'erreur, seulement les deux prévisions (la réalité n'a pas d'erreur).
FORECASTS = SERIES[1:]
error_scale = alt.Scale(domain=FORECASTS, range=[COLORS[s] for s in FORECASTS])

st.set_page_config(page_title="Prévision consommation Belgique", layout="wide")


@st.cache_data
def load_predictions() -> pd.DataFrame:
    with sqlite3.connect(DB_PATH) as conn:
        df = pd.read_sql("SELECT datetime_utc, actual_mw, model_mw, elia_mw FROM predictions", conn)
    df["datetime"] = pd.to_datetime(df["datetime_utc"]).dt.tz_localize("UTC").dt.tz_convert("Europe/Brussels")
    df = df.rename(columns={"actual_mw": "Réalité", "model_mw": "Notre modèle", "elia_mw": "Elia J+1"})
    return df.drop(columns="datetime_utc").dropna()


def errors(df: pd.DataFrame, col: str) -> tuple[float, float]:
    err = (df["Réalité"] - df[col]).abs()
    return err.mean(), 100 * (err / df["Réalité"]).mean()


df = load_predictions()

st.title("Consommation électrique belge : prévision J+1")
st.caption(
    "Gradient boosting entraîné sur 2016-2023, testé avec la météo prévue la veille. "
    "Données : Elia Open Data (ods001), Open-Meteo. Pas de temps horaire, heure belge."
)

# --- Filtre de période -------------------------------------------------------
last_day = df["datetime"].max().date()
first_day = df["datetime"].min().date()
period = st.date_input(
    "Période",
    value=(last_day - timedelta(days=13), last_day),
    min_value=first_day, max_value=last_day,
)
if len(period) != 2:  # l'utilisateur n'a encore cliqué que la date de début
    st.stop()
start, end = period
view = df[(df["datetime"].dt.date >= start) & (df["datetime"].dt.date <= end)]
if view.empty:
    st.warning("Aucune donnée sur cette période.")
    st.stop()

# --- Indicateurs ---------------------------------------------------------------
mae_m, mape_m = errors(view, "Notre modèle")
mae_e, mape_e = errors(view, "Elia J+1")
c1, c2, c3, c4 = st.columns(4)
c1.metric("MAE notre modèle", f"{mae_m:,.0f} MW".replace(",", " "))
c2.metric("MAE Elia J+1", f"{mae_e:,.0f} MW".replace(",", " "))
c3.metric("MAPE notre modèle", f"{mape_m:.2f} %")
c4.metric("MAPE Elia J+1", f"{mape_e:.2f} %")

# --- Courbes : prévisions et réalité -------------------------------------------
long = view.melt("datetime", SERIES, var_name="Série", value_name="MW")
hover = alt.selection_point(fields=["datetime"], nearest=True, on="pointerover", empty=False)
base = alt.Chart(long).encode(
    x=alt.X("datetime:T", title=None),
    y=alt.Y("MW:Q", title="MW", scale=alt.Scale(zero=False)),
    color=alt.Color("Série:N", scale=color_scale, legend=alt.Legend(orient="top", title=None)),
)
lines = base.mark_line(strokeWidth=2)
points = base.mark_point(size=60, filled=True).encode(
    opacity=alt.condition(hover, alt.value(1), alt.value(0))
)
rule = alt.Chart(view).mark_rule(color="#8a8986").encode(
    x="datetime:T",
    opacity=alt.condition(hover, alt.value(0.6), alt.value(0)),
    tooltip=[alt.Tooltip("datetime:T", title="Heure", format="%a %d/%m %H:%M")]
    + [alt.Tooltip(f"{s}:Q", format=",.0f") for s in SERIES],
).add_params(hover)
st.subheader("Prévisions et réalité")
st.altair_chart((lines + points + rule).properties(height=360), use_container_width=True)

# --- Erreur par heure -----------------------------------------------------------
left, right = st.columns(2)
by_hour = pd.DataFrame({
    "heure": view["datetime"].dt.hour,
    "Notre modèle": (view["Réalité"] - view["Notre modèle"]).abs(),
    "Elia J+1": (view["Réalité"] - view["Elia J+1"]).abs(),
}).groupby("heure").mean().reset_index().melt("heure", var_name="Série", value_name="MAE")
left.subheader("Erreur moyenne par heure")
left.altair_chart(
    alt.Chart(by_hour).mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
        x=alt.X("heure:O", title="heure (locale)"),
        xOffset="Série:N",
        y=alt.Y("MAE:Q", title="MAE (MW)"),
        color=alt.Color("Série:N", scale=error_scale, legend=alt.Legend(orient="top", title=None)),
        tooltip=["heure", "Série", alt.Tooltip("MAE:Q", format=",.0f")],
    ).properties(height=300),
    use_container_width=True,
)

# --- Erreur par mois (toute la période de test) --------------------------------
monthly = df.assign(mois=df["datetime"].dt.tz_localize(None).dt.to_period("M").dt.to_timestamp())
monthly = pd.DataFrame({
    "mois": monthly["mois"],
    "Notre modèle": (monthly["Réalité"] - monthly["Notre modèle"]).abs() / monthly["Réalité"] * 100,
    "Elia J+1": (monthly["Réalité"] - monthly["Elia J+1"]).abs() / monthly["Réalité"] * 100,
}).groupby("mois").mean().reset_index().melt("mois", var_name="Série", value_name="MAPE")
right.subheader("Erreur relative par mois (depuis 2024)")
right.altair_chart(
    alt.Chart(monthly).mark_line(strokeWidth=2, point=alt.OverlayMarkDef(size=60)).encode(
        x=alt.X("mois:T", title=None),
        y=alt.Y("MAPE:Q", title="MAPE (%)"),
        color=alt.Color("Série:N", scale=error_scale, legend=alt.Legend(orient="top", title=None)),
        tooltip=[alt.Tooltip("mois:T", format="%B %Y"), "Série", alt.Tooltip("MAPE:Q", format=".2f")],
    ).properties(height=300),
    use_container_width=True,
)

with st.expander("Voir les données de la période"):
    st.dataframe(view.set_index("datetime").round(0), use_container_width=True)
