#!/usr/bin/env python3
"""
Radar d'actu : repère une info DU JOUR qui vient d'apparaître, qui est reprise partout et qui touche directement
le spectateur (argent, droits, transports, sécurité…), puis déclenche le workflow « Flash actu » du dépôt de
production (vérification par Claude, puis notification « Flash à valider »).

Sources (gratuites, publiques) : Google Actualités (à la une, France, Économie) + flux « à la une » de franceinfo,
Le Monde, 20 Minutes, BFMTV, Le Figaro, Le Parisien, Ouest-France ; recherches en hausse (Google Trends) ;
tendances X en France.

Les articles de toutes les sources sont regroupés par sujet. Pour chaque sujet NOUVEAU (né il y a moins de
`fraicheur_h` heures, jamais vu par le radar avant) :
  score = médias distincts qui en parlent (max 10) + 5 si un compte d'alerte prioritaire l'a posté (Cerfia,
          Impact, AlertesInfos, franceinfo, BFMTV) + 3 si impact direct sur le spectateur (mots d'impact)
          + 1 si annonce officielle (gouvernement, Macron, ministre…) + 2 si recherches en hausse + 2 si tendance X
          + 1 si né il y a moins d'1 h 30.
Les alertes des comptes prioritaires passent en premier : flash dès score_min, avec leur propre quota par 24 h.
Les autres sujets (simple reprise par les médias) demandent score_min_autres et ont un petit quota à part : ils ne
bloquent jamais une alerte. Garde-fous : jamais au premier passage (on mémorise l'existant), pause entre deux flashs,
sujets exclus (sport, people, télé…). Le tri fin (déjà traité ? vraiment d'aujourd'hui ? hook possible ?) est fait
ensuite par Claude dans le workflow Flash.

    python radar.py              # déclenche vraiment si AGENT_TOKEN est défini
    python radar.py --essai      # affiche les sujets et leur score, sans rien déclencher ni mémoriser
    python radar.py --essai --tout   # idem en notant TOUS les sujets frais (réglage du score)
    python radar.py --verifier   # teste le jeton AGENT_TOKEN auprès de GitHub, sans rien déclencher
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import unicodedata
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

ICI = Path(__file__).resolve().parent
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
VERSION_ETAT = 2
MOTS_VIDES = set(
    "alors apres avant avec aussi autre aux bien cela cette ceux chez comme dans depuis des deux elle elles encore entre "
    "est etait etre fait faire font ils leur leurs mais meme moins nous pour plus quand que quel quelle qui sans selon "
    "ses son sont sous sur tous tout tres une vers voici vous dont sera seront avoir ete apres-midi ainsi contre lors "
    "pourquoi comment nouveau nouvelle nouveaux selon france francais francaise direct live video photos info infos "
    "annonce annonces explique ecoutez regardez".split()
)

# Flux « à la une » : (nom affiché, URL, type). Les flux Google regroupent déjà plusieurs médias par sujet.
FLUX = [
    ("Google Actualités", "https://news.google.com/rss?hl=fr&gl=FR&ceid=FR:fr", "google"),
    ("Google France", "https://news.google.com/rss/headlines/section/topic/NATION?hl=fr&gl=FR&ceid=FR:fr", "google"),
    ("Google Économie", "https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=fr&gl=FR&ceid=FR:fr", "google"),
    ("franceinfo", "https://www.francetvinfo.fr/titres.rss", "media"),
    ("Le Monde", "https://www.lemonde.fr/rss/une.xml", "media"),
    ("20 Minutes", "https://www.20minutes.fr/feeds/rss-une.xml", "media"),
    ("BFMTV", "https://www.bfmtv.com/rss/news-24-7/", "media"),
    ("Le Figaro", "https://www.lefigaro.fr/rss/figaro_actualites.xml", "media"),
    ("Le Parisien", "https://feeds.leparisien.fr/leparisien/rss", "media"),
    ("Ouest-France", "https://www.ouest-france.fr/rss/une", "media"),
    ("Libération", "https://www.liberation.fr/arc/outboundfeeds/rss-all/collection/accueil-une/?outputType=xml", "media"),
    ("TF1 Info / LCI", "https://www.tf1info.fr/feeds/rss-une.xml", "media"),
    ("CNEWS", "https://www.cnews.fr/rss.xml", "media"),
    ("Europe 1", "https://www.europe1.fr/rss.xml", "media"),
    ("France Inter", "https://www.radiofrance.fr/franceinter/rss", "media"),
    ("L'Express", "https://www.lexpress.fr/arc/outboundfeeds/rss/alaune.xml", "media"),
    ("Mediapart", "https://www.mediapart.fr/articles/feed", "media"),
    ("RFI", "https://www.rfi.fr/fr/rss", "media"),
    ("France 24", "https://www.france24.com/fr/rss", "media"),
    ("Courrier international", "https://www.courrierinternational.com/feed/all/rss.xml", "media"),
    # flux directs bloqués : on passe par Google Actualités
    ("Les Echos", "https://news.google.com/rss/search?q=site:lesechos.fr+when:1d&hl=fr&gl=FR&ceid=FR:fr", "media"),
    ("Le Point", "https://news.google.com/rss/search?q=site:lepoint.fr+when:1d&hl=fr&gl=FR&ceid=FR:fr", "media"),
    ("AFP", "https://news.google.com/rss/search?q=AFP+when:1d&hl=fr&gl=FR&ceid=FR:fr", "google"),
]

# Comptes d'alerte PRIORITAIRES (réglables dans radar.config.json → "prioritaires") :
#   - "telegram" : chaîne publique lue gratuitement (t.me/s/<nom>) ;
#   - "x" : compte X lu par l'API officielle X, seulement si X_BEARER_TOKEN est défini (payant à l'usage).
#   - "alertes_seulement" : seuls les posts « ALERTE / FLASH / URGENT… » sont prioritaires (comptes très prolifiques).
PRIORITAIRES_DEFAUT = [
    {"nom": "Cerfia", "x": "CerfiaFR"},
    {"nom": "Impact", "x": "ImpactMediaFR"},
    {"nom": "AlertesInfos", "x": "AlertesInfos", "telegram": "AlertesInfos"},
    {"nom": "franceinfo", "x": "franceinfo", "alertes_seulement": True},
    {"nom": "BFMTV", "x": "BFMTV", "alertes_seulement": True},
]
ALERTE = re.compile(r"\b(?:FLASH|ALERTE|URGENT|BREAKING|DERNI[ÈE]RE\s+MINUTE)\b")

# Ce qui fait une vidéo à la « Bonne Question » : l'info touche directement le spectateur.
IMPACT = (
    "argent prix hausse baisse taxe taxes impot impots amende amendes interdit interdite interdiction interdits gratuit gratuite "
    "rembourse remboursement remboursements aide aides allocation allocations chomage unedic retraite retraites smic salaire "
    "salaires loyer loyers essence gazole carburant carburants electricite facture factures grève greve greves sncf train trains "
    "tgv ter metro rer bus avion vols aeroport ecole ecoles college lycee bac eleves permis police gendarmerie prison condamne "
    "condamnes condamnation tribunal couvre-feu manifestation manifestations loi decret milliards millions euros record penurie "
    "fermeture fermetures suppression supprime supprimes coupure panne pannes alerte vigilance tempete inondation evacuation "
    "cambriolage pillage pillages arnaque arnaques fraude hopital urgences vaccin virus epidemie securite sociale mutuelle "
    "banque compte comptes livret epargne inflation budget impose obligatoire sanction sanctions"
).split()
OFFICIEL = "macron gouvernement ministre ministres lecornu prefet prefecture elysee matignon conseil assemblee senat decret discours".split()
EXCLURE_DEFAUT = [
    # sport
    "ligue", "football", "foot ", "match", "mercato", "champions", "tennis", "rugby", "formule 1", "basket", "cyclisme",
    "tour de france", "psg", "olympique", "transfert", "entraineur", "buteur", "jo 20",
    # people, télé, culture
    "star academy", "diaporama", "telerealite", "tele-realite", "koh-lanta", "mariage", "divorce", "chanteur", "chanteuse",
    "actrice", "acteur", "serie ", "netflix", "film ", "festival", "concert", "album", "people", "princesse", "prince ",
    "horoscope", "loto", "euromillions", "recette", "jeu video", "jeux video", "cyberpunk", "playstation", "bioware",
    "les sims", "nintendo", "xbox", "streameur", "youtubeur", "influenceuse", "influenceur",
]


def maintenant() -> datetime:
    return datetime.now(timezone.utc)


def lire(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "fr-FR,fr;q=0.9"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read()


def normaliser(texte: str) -> str:
    t = unicodedata.normalize("NFKD", texte.lower())
    return "".join(c for c in t if not unicodedata.combining(c))


def mots(texte: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", normaliser(texte)) if len(w) >= 4 and w not in MOTS_VIDES}


def proche(a: set[str], b: set[str]) -> bool:
    """Même sujet ? (les titres changent d'un média et d'une heure à l'autre)"""
    if not a or not b:
        return False
    commun = len(a & b)
    return commun >= 3 or commun / min(len(a), len(b)) >= 0.5


def date_de(it) -> datetime | None:
    for balise in ("pubDate", "{http://purl.org/dc/elements/1.1/}date"):
        v = it.findtext(balise)
        if v:
            try:
                d = parsedate_to_datetime(v)
            except (TypeError, ValueError):
                try:
                    d = datetime.fromisoformat(v.replace("Z", "+00:00"))
                except ValueError:
                    continue
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    return None


def articles(fraicheur: timedelta, t: datetime) -> tuple[list[dict], list[str]]:
    """Tous les articles récents de toutes les sources (titre, médias, lien, date)."""
    out, erreurs = [], []
    for nom, url, genre in FLUX:
        try:
            racine = ET.fromstring(lire(url))
        except Exception as e:
            erreurs.append(f"{nom} ({type(e).__name__})")
            continue
        for rang, it in enumerate(racine.iter("item"), 1):
            titre = html.unescape(it.findtext("title") or "").strip()
            if not titre:
                continue
            date = date_de(it)
            if date and t - date > fraicheur * 4:  # beaucoup trop vieux pour compter, même comme reprise
                continue
            if genre == "google":
                desc = it.findtext("description") or ""
                medias = [html.unescape(m) for m in re.findall(r'<font color="#6f6f6f">(.*?)</font>', desc)]
                liens = re.findall(r'<a href="([^"]+)"', desc)[:3] or [it.findtext("link")]
                titre = re.sub(r"\s+-\s+[^-]+$", "", titre)
            else:
                medias, liens = [nom], [it.findtext("link")]
            out.append({"titre": titre, "medias": medias or [nom], "liens": [x for x in liens if x], "date": date,
                        "une": genre == "google" and nom == "Google Actualités" and rang <= 10, "mots": mots(titre)})
    return out, erreurs


def nettoyer_alerte(texte: str) -> str:
    """« 🇫🇷 FLASH - L'État voudrait… » → « L'État voudrait… » (sans drapeaux, émojis ni préfixe d'alerte)."""
    t = re.sub(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F]", " ", texte)
    t = re.sub(r"^\s*(?:[A-Za-zÀ-ÿ ]+\(Twitter\)\s*)?(?:FLASH|ALERTE(?:\s+INFO)?|URGENT|INFO|BREAKING|DERNI[ÈE]RE\s+MINUTE)\s*[-:–—]\s*", "", t.strip(), flags=re.I)
    t = re.sub(r"https?://\S+|pic\.twitter\.com/\S+|#\w+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def telegram(chaine: str) -> list[tuple[str, datetime, str]]:
    """Derniers messages d'une chaîne Telegram publique : (texte, date, lien)."""
    page = lire(f"https://t.me/s/{chaine}").decode("utf-8", errors="replace")
    out = []
    for bloc in re.findall(r'<div class="tgme_widget_message_wrap.*?</time>', page, re.S):
        txt = re.search(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', bloc, re.S)
        quand = re.search(r'<time datetime="([^"]+)"', bloc)
        lien = re.search(r'<a class="tgme_widget_message_date" href="([^"]+)"', bloc)
        if txt and quand:
            texte = html.unescape(re.sub(r"<br\s*/?>", " ", txt.group(1)))
            out.append((re.sub("<[^>]+>", "", texte), datetime.fromisoformat(quand.group(1)), lien.group(1) if lien else f"https://t.me/{chaine}"))
    return out


def x_api(compte: str, etat: dict) -> list[tuple[str, datetime, str]]:
    """Derniers posts d'un compte X via l'API officielle (X_BEARER_TOKEN). Ne relit que les posts nouveaux (since_id)."""
    jeton = os.environ.get("X_BEARER_TOKEN")
    if not jeton:
        return []
    ids = etat.setdefault("x_ids", {})
    depuis = etat.setdefault("x_depuis", {})

    def get(chemin: str) -> dict:
        req = urllib.request.Request(f"https://api.x.com/2/{chemin}", headers={"Authorization": f"Bearer {jeton}", "User-Agent": "radar-ai"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())

    if compte not in ids:
        ids[compte] = get(f"users/by/username/{compte}")["data"]["id"]
    q = "max_results=10&exclude=retweets,replies&tweet.fields=created_at"
    if depuis.get(compte):
        q += f"&since_id={depuis[compte]}"
    d = get(f"users/{ids[compte]}/tweets?{q}")
    posts = d.get("data", [])
    if posts:
        depuis[compte] = posts[0]["id"]
    return [(p["text"], datetime.fromisoformat(p["created_at"].replace("Z", "+00:00")), f"https://x.com/{compte}/status/{p['id']}") for p in posts]


def relais_notifications(cfg: dict, etat: dict) -> list[tuple[str, str, datetime, str]]:
    """Notifications X relayées par un téléphone ou un émulateur Android (MacroDroid → ntfy).
    Chaque message ntfy = une notification X : titre = nom du compte, texte = le post. Renvoie (compte, texte, date, lien)."""
    # nom du canal ntfy : dans .env (RELAIS_TOPIC), jamais dans ce dépôt public (sinon n'importe qui pourrait y écrire)
    topic = os.environ.get("RELAIS_TOPIC") or (cfg.get("relais_notifications") or {}).get("topic")
    if not topic:
        return []
    depuis = etat.get("relais_depuis") or "30m"
    page = lire(f"https://ntfy.sh/{topic}/json?poll=1&since={depuis}").decode("utf-8", errors="replace")
    out = []
    for ligne in page.splitlines():
        try:
            m = json.loads(ligne)
        except ValueError:
            continue
        if m.get("event") != "message":
            continue
        etat["relais_depuis"] = m["id"]
        texte = m.get("message") or ""
        pj = m.get("attachment") or {}
        if pj.get("url") and (pj.get("size") or 0) < 20000:  # texte envoyé en pièce jointe (encodage) : on le lit
            try:
                texte = lire(pj["url"]).decode("utf-8", errors="replace")
            except Exception:
                pass
        out.append((m.get("title") or "", texte, datetime.fromtimestamp(m["time"], tz=timezone.utc), m.get("click") or "https://x.com"))
    return out


def alertes(cfg: dict, etat: dict, fraicheur: timedelta, t: datetime) -> tuple[list[dict], list[str]]:
    """Posts récents des comptes d'alerte prioritaires (X si clé API, sinon Telegram quand il existe)."""
    out, erreurs = [], []
    comptes = cfg.get("prioritaires", PRIORITAIRES_DEFAUT)

    def ajouter(c: dict, texte: str, date: datetime, lien: str) -> None:
        if t - date > fraicheur:
            return
        titre = nettoyer_alerte(texte)[:220]
        if len(titre) < 20:
            return
        # Comptes très prolifiques (franceinfo, BFMTV) : seules leurs ALERTES sont prioritaires ; leurs autres posts
        # comptent comme une simple reprise par un média.
        prioritaire = not c.get("alertes_seulement") or bool(ALERTE.search(texte[:60]))
        out.append({"titre": titre, "medias": [c["nom"]], "liens": [lien], "date": date, "une": False, "mots": mots(titre), "prioritaire": prioritaire})

    for c in comptes:
        posts = []
        try:
            if c.get("x") and os.environ.get("X_BEARER_TOKEN"):
                posts = x_api(c["x"], etat)
            elif c.get("telegram"):
                posts = telegram(c["telegram"])
        except Exception as e:
            erreurs.append(f"{c['nom']} ({type(e).__name__})")
            continue
        for texte, date, lien in posts:
            ajouter(c, texte, date, lien)
    # Notifications X relayées (gratuit) : le compte est reconnu au nom affiché en titre de la notification
    # (jamais dans le texte : « l'impact de… » ne doit pas être pris pour le compte Impact).
    try:
        for titre_notif, texte, date, lien in relais_notifications(cfg, etat):
            tn = normaliser(titre_notif)
            c = next((c for c in comptes if re.search(rf"\b{re.escape(normaliser(c['nom']))}\b", tn)
                      or (c.get("x") and re.search(rf"\b{re.escape(normaliser(c['x']))}\b", tn))), None)
            if c is not None:
                ajouter(c, texte, date, lien)  # sinon : notification d'un autre compte, ignorée
    except Exception as e:
        erreurs.append(f"relais notifications ({type(e).__name__})")
    return out, erreurs


def regrouper(arts: list[dict]) -> list[dict]:
    """Regroupe les articles qui parlent du même sujet."""
    sujets: list[dict] = []
    for a in sorted(arts, key=lambda x: x["date"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True):
        s = next((s for s in sujets if any(proche(a["mots"], m["mots"]) for m in s["membres"])), None)
        if s is None:
            sujets.append({"membres": [a]})
        else:
            s["membres"].append(a)
    for s in sujets:
        m = s["membres"]
        s["titre"] = max(m, key=lambda x: len(x["medias"]))["titre"]
        s["mots"] = set().union(*(x["mots"] for x in m))
        s["medias"] = sorted({md for x in m for md in x["medias"] if md})
        s["liens"] = [l for x in m for l in x["liens"]][:4]
        dates = [x["date"] for x in m if x["date"]]
        s["ne_le"] = min(dates) if dates else None
        s["une"] = any(x["une"] for x in m)
        s["prioritaire"] = sorted({x["medias"][0] for x in m if x.get("prioritaire")})
        if s["prioritaire"]:  # le titre de l'alerte d'abord : c'est l'info telle qu'elle tombe
            s["titre"] = next(x["titre"] for x in m if x.get("prioritaire"))
    return sujets


def google_trends() -> list[str]:
    root = ET.fromstring(lire("https://trends.google.com/trending/rss?geo=FR"))
    return [it.findtext("title") or "" for it in root.iter("item")]


def tendances_x() -> list[str]:
    page = lire("https://trends24.in/france/").decode("utf-8", errors="replace")
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


JETON_REFUSE = (
    "  ✗ GitHub refuse le jeton du radar (HTTP {code}) : il est supprimé, expiré ou sans la permission « Actions : Read and write »\n"
    "    sur {cible}. Crée un nouveau jeton et remplace la ligne AGENT_TOKEN= du fichier .env du radar, puis teste : python3 radar.py --verifier"
)


def appel_github(cfg: dict, chemin: str, corps: dict | None = None) -> tuple[int, dict]:
    jeton = os.environ.get("AGENT_TOKEN")
    cible = os.environ.get("CIBLE") or cfg["cible"]
    req = urllib.request.Request(
        f"https://api.github.com/repos/{cible}" + (f"/{chemin}" if chemin else ""),
        data=json.dumps(corps).encode() if corps is not None else None,
        method="POST" if corps is not None else "GET",
        headers={"Authorization": f"Bearer {jeton}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.status, {k.lower(): v for k, v in r.headers.items()}


def rapport_sante(cfg: dict, etat: dict, t: datetime) -> None:
    """Une fois par jour : date d'expiration du jeton du radar, envoyée au workflow « Santé » du dépôt de production, qui
    prévient sur le téléphone avant qu'il n'expire (et si le radar ne donne plus de nouvelles pendant 2 jours)."""
    if not os.environ.get("AGENT_TOKEN") or (etat.get("sante_le") and t - datetime.fromisoformat(etat["sante_le"]) < timedelta(hours=20)):
        return
    try:
        _, entetes = appel_github(cfg, "")
        expire = entetes.get("github-authentication-token-expiration") or "jamais"
        appel_github(cfg, "actions/workflows/sante.yml/dispatches",
                     {"ref": "main", "inputs": {"radar_expire": expire, "radar_lieu": os.environ.get("RADAR_LIEU", "PC")}})
        etat["sante_le"] = t.isoformat()
        print(f"Rapport santé envoyé (jeton du radar : expire {expire}).")
    except (urllib.error.HTTPError, OSError) as e:
        print(f"Rapport santé impossible ({e}) : nouvel essai au prochain passage.")


def declencher(cfg: dict, sujet: dict, signaux: str) -> bool:
    """Lance le workflow Flash. Renvoie False si GitHub refuse (le sujet sera retenté au passage suivant)."""
    cible = os.environ.get("CIBLE") or cfg["cible"]
    if not os.environ.get("AGENT_TOKEN"):
        print("  (pas de AGENT_TOKEN : déclenchement simulé)")
        return True
    corps = {"ref": "main", "inputs": {"sujet": sujet["titre"][:250], "liens": "\n".join(sujet["liens"]), "signaux": signaux[:250]}}
    try:
        statut, _ = appel_github(cfg, "actions/workflows/flash.yml/dispatches", corps)
    except urllib.error.HTTPError as e:
        print(JETON_REFUSE.format(code=e.code, cible=cible) if e.code in (401, 403, 404) else f"  ✗ GitHub : erreur HTTP {e.code}, nouvel essai au prochain passage.")
        return False
    except OSError as e:
        print(f"  ✗ GitHub injoignable ({e}) : nouvel essai au prochain passage.")
        return False
    print(f"  → workflow Flash déclenché sur {cible} (HTTP {statut})")
    return True


def verifier(cfg: dict) -> None:
    """Teste le jeton sans rien déclencher : lecture du workflow Flash du dépôt de production."""
    cible = os.environ.get("CIBLE") or cfg["cible"]
    if not os.environ.get("AGENT_TOKEN"):
        sys.exit("✗ Aucun AGENT_TOKEN (fichier .env du radar ou secret GitHub).")
    try:
        appel_github(cfg, "actions/workflows/flash.yml")
    except urllib.error.HTTPError as e:
        sys.exit(JETON_REFUSE.format(code=e.code, cible=cible))
    print(f"✓ Jeton du radar accepté par GitHub ({cible}). Vérifie qu'il a bien « Actions : Read and write » pour pouvoir déclencher.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--essai", action="store_true", help="affiche les scores sans rien déclencher ni mémoriser")
    ap.add_argument("--verifier", action="store_true", help="teste le jeton AGENT_TOKEN auprès de GitHub")
    ap.add_argument("--tout", action="store_true", help="avec --essai : note tous les sujets frais, même déjà vus")
    a = ap.parse_args()
    charger_env()
    cfg = charger("radar.config.json", {})
    if a.verifier:
        return verifier(cfg)
    t = maintenant()
    fraicheur = timedelta(hours=cfg.get("fraicheur_h", 6))

    etat = charger("state.json", {"vus": [], "flashs": []})
    arts, erreurs = articles(fraicheur, t)
    prio, erreurs_prio = alertes(cfg, etat, fraicheur, t)
    arts += prio
    erreurs += erreurs_prio
    if erreurs:
        print("Sources indisponibles : " + ", ".join(erreurs))
    sujets = regrouper(arts)
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

    # Mémoire (48 h). Un nouveau format d'état (nouvelles sources) = on réapprend l'existant sans rien déclencher.
    premier_passage = not etat["vus"] or etat.get("version") != VERSION_ETAT
    # Reprise après une pause (PC éteint, ou radar GitHub qui prend le relais) : les sujets apparus pendant la pause sont
    # mémorisés sans flash, sauf ceux nés il y a moins de 30 min. Sinon : une rafale de flashs sur de l'actu déjà vieille.
    derniere = etat.get("derniere_passe")
    pause = t - datetime.fromisoformat(derniere) if derniere else None
    reprise = not premier_passage and (pause is None or pause > timedelta(minutes=cfg.get("reprise_min", 40)))
    if reprise:
        print(f"Reprise après {round(pause.total_seconds() / 60) if pause else '?'} min sans passage : l'actu plus ancienne est mémorisée sans flash.")
    vus = [] if etat.get("version") != VERSION_ETAT else [v for v in etat["vus"] if t - datetime.fromisoformat(v["vu_le"]) < timedelta(hours=48)]
    flashs = [f for f in etat.get("flashs", []) if t - datetime.fromisoformat(f["le"]) < timedelta(hours=24)]
    exclus = [normaliser(x) for x in cfg.get("exclure", [])] + EXCLURE_DEFAUT
    suivi = timedelta(hours=cfg.get("suivi_h", 3))

    candidats = []
    for s in sujets:
        connu = next((v for v in vus if proche(s["mots"], set(v["mots"])) or any(proche(m["mots"], set(v["mots"])) for m in s["membres"])), None)
        if connu is None:
            initial = premier_passage or (reprise and not (s["ne_le"] and t - s["ne_le"] < timedelta(minutes=30)))
            connu = {"mots": sorted(s["mots"])[:40], "titre": s["titre"], "vu_le": t.isoformat(), "initial": initial, "flash": False}
            vus.append(connu)
        if not a.tout and (connu.get("initial") or connu.get("flash") or t - datetime.fromisoformat(connu["vu_le"]) > suivi):
            continue  # déjà là avant le radar, déjà envoyé, ou suivi terminé
        if s["ne_le"] and t - s["ne_le"] > fraicheur:
            continue  # sujet qui date : pas une info du jour
        titre_n = re.sub(r"\ben train de\b", " ", normaliser(" ".join(m["titre"] for m in s["membres"])))  # « en train de » ≠ un train
        impact = sorted({w for w in IMPACT if re.search(rf"\b{re.escape(w)}\b", titre_n)})
        officiel = any(re.search(rf"\b{w}\b", titre_n) for w in OFFICIEL)
        en_trend = any(len(s["mots"] & q) >= 2 or (len(q) == 1 and q <= s["mots"]) for q in trends if q)
        en_x = any(len(s["mots"] & q) >= 2 or (len(q) == 1 and q <= s["mots"]) for q in xs if q)
        tout_frais = bool(s["ne_le"]) and t - s["ne_le"] < timedelta(minutes=90)
        couverture = min(len([m for m in s["medias"] if m not in s["prioritaire"]]), 10)
        score = couverture + 5 * bool(s["prioritaire"]) + 3 * bool(impact) + officiel + 2 * en_trend + 2 * en_x + tout_frais
        exclue = any(re.search(rf"\b{re.escape(e.strip())}\b", normaliser(s["titre"])) for e in exclus if e.strip())
        age = round((t - s["ne_le"]).total_seconds() / 60) if s["ne_le"] else None
        candidats.append({**s, "score": score, "impact": impact, "officiel": officiel, "trend": en_trend, "x": en_x,
                          "exclue": exclue, "age_min": age, "connu": connu, "couverture": couverture})

    candidats.sort(key=lambda c: -c["score"])
    for c in candidats[: 25 if a.tout else 8]:
        age = f"né il y a {c['age_min']:>3} min" if c["age_min"] is not None else "date inconnue"
        print(f"  {c['score']:>2} pts · {age} · {c['couverture']} médias{' · ALERTE ' + '+'.join(c['prioritaire']) if c['prioritaire'] else ''}{' · impact' if c['impact'] else ''}{' · officiel' if c['officiel'] else ''}"
              f"{' · recherches' if c['trend'] else ''}{' · X' if c['x'] else ''}{' · EXCLU' if c['exclue'] else ''} · {c['titre'][:90]}")

    if premier_passage:
        print(f"Premier passage : {len(vus)} sujets mémorisés, aucun flash.")
    elif a.essai:
        print("Mode essai : rien n'est déclenché.")
    else:
        # Les alertes des comptes prioritaires passent d'abord, avec leur propre quota : les autres sujets (simple
        # reprise par les médias) ont un seuil plus haut et un petit quota à part, ils ne peuvent jamais les bloquer.
        dernier = max((datetime.fromisoformat(f["le"]) for f in flashs), default=None)
        depuis = t - dernier if dernier else timedelta(days=1)
        n_alertes = sum(1 for f in flashs if f.get("alerte"))
        alertes_ok = n_alertes < cfg.get("max_alertes_par_jour", 6) and depuis >= timedelta(minutes=cfg.get("pause_alertes_min", 10))
        autres_ok = len(flashs) - n_alertes < cfg.get("max_autres_par_jour", 3) and depuis >= timedelta(minutes=cfg.get("pause_min", 20))
        elus = [c for c in candidats if not c["exclue"] and (
            (c["prioritaire"] and alertes_ok and c["score"] >= cfg.get("score_min", 8))
            or (not c["prioritaire"] and autres_ok and c["score"] >= cfg.get("score_min_autres", 11)))]
        elus.sort(key=lambda c: (not c["prioritaire"], -c["score"]))
        if elus:
            c = elus[0]
            signaux = ((f"alerte {', '.join(c['prioritaire'])} ; " if c["prioritaire"] else "") + f"{c['couverture']} médias ({', '.join(c['medias'][:6])})"
                       + (f", impact : {', '.join(c['impact'][:4])}" if c["impact"] else "")
                       + (", annonce officielle" if c["officiel"] else "") + (", recherches en hausse" if c["trend"] else "")
                       + (", tendance X" if c["x"] else "") + (f", né il y a {c['age_min']} min" if c["age_min"] is not None else ""))
            print(f"FLASH : {c['titre']} ({signaux})")
            if declencher(cfg, c, signaux):
                c["connu"]["flash"] = True
                flashs.append({"titre": c["titre"], "le": t.isoformat(), "alerte": bool(c["prioritaire"])})
        else:
            print(f"Rien d'assez chaud. Alertes prioritaires : {n_alertes}/{cfg.get('max_alertes_par_jour', 6)} en 24 h"
                  f"{'' if alertes_ok else ' (en pause ou plafond)'} · autres sujets : {len(flashs) - n_alertes}/{cfg.get('max_autres_par_jour', 3)}"
                  f"{'' if autres_ok else ' (en pause ou plafond)'}.")

    if not a.essai:
        rapport_sante(cfg, etat, t)
        (ICI / "state.json").write_text(json.dumps({"version": VERSION_ETAT, "vus": vus[-800:], "flashs": flashs,
                                                     "x_ids": etat.get("x_ids", {}), "x_depuis": etat.get("x_depuis", {}),
                                                     "relais_depuis": etat.get("relais_depuis"), "derniere_passe": t.isoformat(),
                                                     "sante_le": etat.get("sante_le")}, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    main()
