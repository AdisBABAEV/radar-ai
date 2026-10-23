# radar-ai

Radar d'actualité pour la chaîne : toutes les 10 minutes, il repère une info qui vient d'apparaître et qui monte vite
(Google Actualités, Google Trends, tendances X en France), puis déclenche le workflow **Flash actu** du dépôt de
production, qui vérifie l'info, fabrique la vidéo (format standard, plus d'une minute) et envoie une notification.

Ce dépôt est public pour profiter des minutes GitHub Actions gratuites : il ne contient **aucune donnée privée**
(seulement des titres d'actualité publics, gardés dans le cache des Actions).

## Mise en route
1. **Jeton** : sur GitHub, *Settings → Developer settings → Fine-grained tokens → Generate new token*.
   Repository access : **Only select repositories → AGENT_TIKTOK**. Permissions : **Actions : Read and write**. Rien d'autre.
2. Dans CE dépôt : *Settings → Secrets and variables → Actions → New repository secret* : `AGENT_TOKEN` = ce jeton.
3. *Actions → Radar actu → Run workflow* pour un premier passage. Le premier passage mémorise l'actu du moment
   sans rien déclencher ; les suivants (toutes les 10 min) déclenchent un flash quand une nouvelle info devient chaude.

## Sources
- **Prioritaires — comptes d'alerte** (`prioritaires`) : Cerfia, Impact (@ImpactMediaFR), AlertesInfos, franceinfo, BFMTV.
  Un post de leur part vaut **+5 points** : une alerte du jour qui touche le spectateur déclenche un flash dans les 2 minutes.
  Leurs alertes passent **en premier**, avec leur propre quota : les autres sujets ne peuvent jamais les bloquer.
  Pour franceinfo et BFMTV (`alertes_seulement`), seuls les posts « ALERTE / FLASH / URGENT » sont prioritaires ; leurs
  autres posts comptent comme une simple reprise.
  - lus par l'**API officielle X** si `X_BEARER_TOKEN` est défini (`.env` du radar sur le PC, ou secret GitHub) ;
    payant à l'usage (environ 0,005 $ par post lu, seuls les posts nouveaux sont relus) ;
  - sinon, quand elle existe, par leur **chaîne Telegram publique** (gratuit) : aujourd'hui seul AlertesInfos (relais non officiel) ;
  - et/ou par les **notifications X relayées** (gratuit) : un téléphone ou émulateur Android avec l'appli X (cloche « tous
    les posts » sur ces comptes) et MacroDroid qui envoie chaque notification X à un canal ntfy privé
    (titre = nom du compte, texte = le post). Nom du canal dans `.env` : `RELAIS_TOPIC=…` (jamais dans ce dépôt public).
- **Médias** (confirmation) : Google Actualités (une, France, Économie), franceinfo, Le Monde, 20 Minutes, BFMTV,
  Le Figaro, Le Parisien, Ouest-France, Libération, TF1 Info/LCI, CNEWS, Europe 1, France Inter, L'Express, Mediapart,
  RFI, France 24, Courrier international, et via Google Actualités : Les Echos, Le Point, AFP.

## Réglages (`radar.config.json`)
| Clé | Rôle |
|---|---|
| `cible` | dépôt de production (`propriétaire/nom`) |
| `prioritaires` | comptes d'alerte : `{ "nom", "x": "<compte X>", "telegram": "<chaîne>" }` |
| `score_min` | seuil d'une **alerte d'un compte prioritaire** (défaut 8). Score = médias distincts (max 10) + 5 si alerte d'un compte prioritaire + 3 si impact direct sur le spectateur + 1 si annonce officielle + 2 si recherches en hausse + 2 si tendance X + 1 si né il y a moins d'1 h 30. Baisser = plus de flashs |
| `score_min_autres` | seuil des **autres sujets**, sans alerte d'un compte prioritaire (défaut 11 : seulement l'actu reprise partout) |
| `max_alertes_par_jour`, `pause_alertes_min` | quota d'alertes prioritaires par 24 h (défaut 6) et pause minimale avant l'une d'elles (défaut 10 min) |
| `max_autres_par_jour`, `pause_min` | quota des autres sujets par 24 h (défaut 3) et pause avant l'un d'eux (défaut 20 min). Garde-fous pour ton quota Claude (le dépôt de production a aussi son propre plafond) |
| `fraicheur_h` | âge maximum d'un sujet (heures) : seules les infos du jour comptent |
| `suivi_h` | pendant combien d'heures un nouveau sujet est surveillé (il peut devenir chaud plus tard) |
| `exclure` | mots qui écartent un sujet (en plus du sport, du people et de la télé exclus d'office) |

Tester sans rien déclencher : `python radar.py --essai` (ou `--essai --tout` pour noter tous les sujets frais).
