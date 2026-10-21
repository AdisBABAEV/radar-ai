#!/usr/bin/env python3
"""
Radar d'actu : toutes les 10 minutes, repère une info qui VIENT d'apparaître et qui monte vite, puis déclenche
le workflow « Flash actu » du dépôt de production (vidéo prête à publier + notification).

Signaux (gratuits, publics) :
  - Google Actualités « À la une » (France) : chaque info est regroupée avec les médias qui la reprennent (jusqu'à 5) ;
  - Google Trends (recherches en forte hausse en France) ;
  - tendances X en France (classement public).

Score d'une info nouvelle = médias qui la reprennent (1-5) + 2 si elle est dans les recherches en hausse
+ 2 si elle est en tendance X + 1 si elle est dans le top 10 de Google Actualités. Flash si score ≥ score_min.
Garde-fous : jamais au premier passage (on mémorise l'existant), pause entre deux flashs, plafond par jour,
mots exclus (sport, météo…). Réglages : radar.config.json. État : state.json (cache GitHub Actions).

    python radar.py              # déclenche vraiment si AGENT_TOKEN est défini
    python radar.py --essai      # affiche les candidats et leur score, sans rien déclencher
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import unicodedata
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

ICI = Path(__file__).resolve().parent
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
MOTS_VIDES = set(
    "alors apres avant avec aussi autre aux bien cela cette ceux chez comme dans depuis des deux elle elles encore entre "
    "est etait etre fait faire font ils leur leurs mais meme moins nous pour plus quand que quel quelle qui sans selon "
    "ses son sont sous sur tous tout tres une vers voici vous dont sera seront avoir ete apres-midi ainsi contre lors "
    "pourquoi comment nouveau nouvelle nouveaux selon france francais francaise direct live video photos info infos".split()
)


def maintenant() -> datetime:
    return datetime.now(timezone.utc)


def lire(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "fr-FR,fr;q=0.9"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", errors="replace")


def mots(texte: str) -> set[str]:
    t = unicodedata.normalize("NFKD", texte.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return {w for w in re.findall(r"[a-z0-9]+", t) if len(w) >= 4 and w not in MOTS_VIDES}


def proche(a: set[str], b: set[str]) -> bool:
    """Même info ? (titres qui changent d'une heure à l'autre)"""
    if not a or not b:
        return False
    commun = len(a & b)
    return commun >= 3 or commun / min(len(a), len(b)) >= 0.5


def google_actualites() -> list[dict]:
    root = ET.fromstring(lire("https://news.google.com/rss?hl=fr&gl=FR&ceid=FR:fr"))
    infos = []
    for rang, it in enumerate(root.iter("item"), 1):
        desc = it.findtext("description") or ""
        medias = [html.unescape(m) for m in re.findall(r'<font color="#6f6f6f">(.*?)</font>', desc)]
        liens = re.findall(r'<a href="([^"]+)"', desc)
        titre = re.sub(r"\s+-\s+[^-]+$", "", html.unescape(it.findtext("title") or ""))
        try:
            date = parsedate_to_datetime(it.findtext("pubDate") or "")
        except (TypeError, ValueError):
            date = None
        infos.append({"rang": rang, "titre": titre, "medias": medias or [None], "liens": liens[:3] or [it.findtext("link")], "date": date})
    return infos


def google_trends() -> list[str]:
    root = ET.fromstring(lire("https://trends.google.com/trending/rss?geo=FR"))
    return [it.findtext("title") or "" for it in root.iter("item")]


def tendances_x() -> list[str]:
    page = lire("https://trends24.in/france/")
    carte = re.search(r'<ol class="?trend-card__list"?>(.*?)</ol>', page, re.S)
    if not carte:
        return []
    return [html.unescape(re.sub("<[^>]+>", "", m)).strip().lstrip("#") for m in re.findall(r"trend-link\"?>(.*?)</a>", carte.group(1), re.S)]


def charger_env() -> None:
    """Sur le PC (service toutes les 2 min), le jeton est dans .env (jamais versionné)."""
    p = ICI / ".env"
    if p.exists():
        for ligne in p.read_text(encoding="utf-8").splitlines():
            if "=" in ligne and not ligne.lstrip().startswith("#"):
                k, v = ligne.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def charger(nom: str, defaut):
    p = ICI / nom
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else defaut


def declencher(cfg: dict, info: dict, signaux: str) -> None:
    jeton = os.environ.get("AGENT_TOKEN")
    cible = os.environ.get("CIBLE") or cfg["cible"]
    if not jeton:
        print("  (pas de AGENT_TOKEN : déclenchement simulé)")
        return
    corps = json.dumps({"ref": "main", "inputs": {"sujet": info["titre"][:250], "liens": "\n".join(info["liens"]), "signaux": signaux[:250]}}).encode()
    req = urllib.request.Request(
        f"https://api.github.com/repos/{cible}/actions/workflows/flash.yml/dispatches",
        data=corps,
        method="POST",
        headers={"Authorization": f"Bearer {jeton}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        print(f"  → workflow Flash déclenché sur {cible} (HTTP {r.status})")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--essai", action="store_true", help="affiche les scores sans rien déclencher ni mémoriser")
    a = ap.parse_args()
    charger_env()
    cfg = charger("radar.config.json", {})
    etat = charger("state.json", {"vus": [], "flashs": []})
    t = maintenant()

    infos = google_actualites()
    try:
        trends = [mots(q) for q in google_trends()]
    except Exception as e:
        print(f"Google Trends indisponible : {e}")
        trends = []
    try:
        xs = [mots(q) for q in tendances_x()]
    except Exception as e:
        print(f"Tendances X indisponibles : {e}")
        xs = []

    # Mémoire (48 h) : chaque info avec l'heure où le radar l'a vue pour la première fois. Une info est suivie
    # pendant `suivi_h` heures : elle peut devenir chaude plus tard (de 1 à 5 médias, puis dans les recherches…).
    vus = [v for v in etat["vus"] if t - datetime.fromisoformat(v["vu_le"]) < timedelta(hours=48)]
    flashs = [f for f in etat["flashs"] if t - datetime.fromisoformat(f["le"]) < timedelta(hours=24)]
    premier_passage = not etat["vus"]
    exclus = [mots(x) for x in cfg.get("exclure", [])]
    suivi = timedelta(hours=cfg.get("suivi_h", 3))

    candidats = []
    for info in infos:
        m = mots(info["titre"])
        connu = next((v for v in vus if proche(m, set(v["mots"]))), None)
        if connu is None:
            connu = {"mots": sorted(m), "titre": info["titre"], "vu_le": t.isoformat(), "initial": premier_passage, "flash": False}
            vus.append(connu)
        if connu.get("initial") or connu.get("flash") or t - datetime.fromisoformat(connu["vu_le"]) > suivi:
            continue  # déjà là avant le radar, déjà envoyée, ou trop ancienne
        medias = len({x for x in info["medias"] if x})
        en_trend = any(len(m & q) >= 1 for q in trends if q)
        en_x = any(len(m & q) >= 1 for q in xs if q)
        score = medias + 2 * en_trend + 2 * en_x + (info["rang"] <= 10)
        exclue = any(e and e <= m for e in exclus)
        age = round((t - datetime.fromisoformat(connu["vu_le"])).total_seconds() / 60)
        candidats.append({**info, "mots": m, "age_min": age, "score": score, "trend": en_trend, "x": en_x, "exclue": exclue, "connu": connu})

    candidats.sort(key=lambda c: -c["score"])
    for c in candidats[:8]:
        print(f"  {c['score']:>2} pts · suivie depuis {c['age_min']:>3} min · {len(set(c['medias']))} médias{' · recherches' if c['trend'] else ''}"
              f"{' · X' if c['x'] else ''}{' · EXCLUE' if c['exclue'] else ''} · {c['titre'][:90]}")

    if premier_passage:
        print(f"Premier passage : {len(vus)} infos mémorisées, aucun flash.")
    elif a.essai:
        print("Mode essai : rien n'est déclenché.")
    else:
        dernier = max((datetime.fromisoformat(f["le"]) for f in flashs), default=None)
        if len(flashs) >= cfg.get("max_par_jour", 4):
            print("Plafond de flashs atteint pour 24 h.")
        elif dernier and t - dernier < timedelta(minutes=cfg.get("pause_min", 45)):
            print("Pause entre deux flashs.")
        else:
            for c in candidats:
                if c["exclue"] or c["score"] < cfg.get("score_min", 7):
                    continue
                signaux = f"{len(set(c['medias']))} médias ({', '.join(x for x in c['medias'] if x)})" + (", recherches en hausse" if c["trend"] else "") + (", tendance X" if c["x"] else "")
                print(f"FLASH : {c['titre']} ({signaux})")
                declencher(cfg, c, signaux)
                c["connu"]["flash"] = True
                flashs.append({"titre": c["titre"], "le": t.isoformat()})
                break
            else:
                print("Rien d'assez chaud.")

    if not a.essai:
        (ICI / "state.json").write_text(json.dumps({"vus": vus[-400:], "flashs": flashs}, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    main()
