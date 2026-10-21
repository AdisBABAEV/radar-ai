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

## Réglages (`radar.config.json`)
| Clé | Rôle |
|---|---|
| `cible` | dépôt de production (`propriétaire/nom`) |
| `score_min` | seuil de déclenchement (défaut 7). Score = médias qui reprennent l'info (1-5) + 2 si recherches en hausse + 2 si tendance X + 1 si top 10. Baisser = plus de flashs |
| `suivi_h` | pendant combien d'heures une nouvelle info est surveillée (elle peut devenir chaude plus tard) |
| `max_par_jour`, `pause_min` | garde-fous pour ton quota Claude (le dépôt de production a aussi son propre plafond) |
| `exclure` | mots qui écartent une info (sport, météo…) |

Tester sans rien déclencher : `python radar.py --essai`.
