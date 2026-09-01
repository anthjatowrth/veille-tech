# Veille tech hebdo

RSS → tri et résumés FR (Gemini) → Notion (Veille Tech) → mail récap. Tourne sur GitHub Actions : gratuit, PC éteint ou pas, déclenchable à la main.

## Installation (une fois, ~15 min)

**1. Clé Gemini** : https://aistudio.google.com/apikey → créer une clé (free tier, pas de CB).

**2. Intégration Notion** : https://www.notion.so/my-integrations → « New integration » (interne, capacités lecture + insertion) → copier le token `ntn_...`. Puis dans Notion, ouvrir la page **Veille Tech** → menu `...` → Connexions → ajouter ton intégration (donne accès aux 4 bases enfants).

**3. Mot de passe d'application Gmail** : https://myaccount.google.com/apppasswords (nécessite la validation en 2 étapes) → créer, copier les 16 caractères.

**4. Repo GitHub** : créer un repo **privé** `veille-tech`, y pousser ces fichiers :

```powershell
cd veille-tech
git init && git add . && git commit -m "veille tech automatisée"
git branch -M main
git remote add origin https://github.com/<ton-user>/veille-tech.git
git push -u origin main
```

**5. Secrets** : repo → Settings → Secrets and variables → Actions → « New repository secret » ×5 :

| Nom | Valeur |
|---|---|
| `GEMINI_API_KEY` | clé étape 1 |
| `NOTION_TOKEN` | token étape 2 |
| `GMAIL_USER` | ton adresse Gmail |
| `GMAIL_APP_PASSWORD` | les 16 caractères étape 3 |
| `MAIL_TO` | adresse de réception |

**6. Premier run** : onglet Actions → « Veille tech hebdo » → « Run workflow ». Vérifier les logs, le mail et Notion.

Ensuite : run automatique chaque lundi matin. Le bouton « Run workflow » reste dispo pour un déclenchement manuel.

## Exécution locale (optionnel)

```powershell
pip install -r requirements.txt
$env:GEMINI_API_KEY="..."; $env:NOTION_TOKEN="..."; $env:GMAIL_USER="..."; $env:GMAIL_APP_PASSWORD="..."; $env:MAIL_TO="..."
python veille.py
```

## Ajuster

- **Sources** : éditer `feeds` dans `config.yaml` (n'importe quel flux RSS/Atom).
- **Fréquence** : ligne `cron` dans `.github/workflows/veille.yml`.
- **Sélectivité / ton des résumés** : variable `PROMPT` dans `veille.py`.

## Points d'attention

- Dédup automatique : les URLs déjà présentes dans Notion (30 derniers jours) sont ignorées.
- Free tier Gemini : 1 à 2 appels par run, très loin des limites. En cas de 429, le script réessaie 3 fois.
- GitHub désactive le cron après 60 jours sans activité sur le repo ; un commit ou un run manuel le réactive (un mail te prévient).
- Un feed mort ne bloque pas le run (warning dans les logs).
