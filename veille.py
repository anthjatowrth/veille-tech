"""Veille tech hebdo : RSS -> tri/résumés FR via Gemini -> Notion -> mail récap.

Env requis : GEMINI_API_KEY, NOTION_TOKEN, GMAIL_USER, GMAIL_APP_PASSWORD, MAIL_TO
"""

import html
import json
import os
import smtplib
import sys
import time
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText

import feedparser
import requests
import yaml

CONFIG = yaml.safe_load(open(os.path.join(os.path.dirname(__file__), "config.yaml"), encoding="utf-8"))
NOTION = "https://api.notion.com/v1"
NOTION_HEADERS = {
    "Authorization": f"Bearer {os.environ['NOTION_TOKEN']}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}
NOW = datetime.now(timezone.utc)

# Valeurs autorisées par les selects Notion (doivent matcher exactement)
ENUMS = {
    "outil": ["Python", "PostgreSQL", "MySQL", "MongoDB", "Power BI", "Tableau", "Looker Studio",
              "Scikit-learn", "React", "Prefect", "GitHub Actions", "Supabase", "Autre",
              "Data Engineering", "Data Analyst", "DevOps", "Machine Learning", "SQL avancé",
              "Python avancé", "Cloud"],
    "type_maj": ["Breaking change", "Nouvelle fonctionnalité", "Amélioration performance", "Sécurité", "Dépréciation"],
    "pertinence": ["Essentiel", "Important", "Intéressant"],
    "categorie_outil": ["Data Engineering", "Data Analyst", "Machine Learning", "DevOps", "Backend",
                        "Frontend", "Cloud", "Autre", "Bonnes pratiques", "Nouveaux outils"],
    "pertinence_moi": ["Prioritaire", "À explorer", "À surveiller"],
    "signal": ["Très fort", "Fort", "Émergent"],
    "categorie_bp": ["Data Engineering", "Data Analyst", "Machine Learning", "DevOps", "SQL avancé",
                     "Python avancé", "Autre", "Frontend", "Cloud"],
    "niveau": ["Intermédiaire", "Avancé", "Expert", "À explorer", "Prioritaire", "À surveiller"],
}


def valid(value, key, default):
    return value if value in ENUMS[key] else default


# ---------- 1. Collecte RSS ----------

def fetch_candidates():
    cutoff = NOW - timedelta(days=CONFIG["lookback_days"])
    items = []
    for feed in CONFIG["feeds"]:
        try:
            parsed = feedparser.parse(feed["url"], request_headers={"User-Agent": "veille-tech/1.0"})
            for e in parsed.entries[:20]:
                ts = e.get("published_parsed") or e.get("updated_parsed")
                if ts and datetime(*ts[:6], tzinfo=timezone.utc) < cutoff:
                    continue
                link = e.get("link", "")
                if not link:
                    continue
                summary = html.unescape(e.get("summary", ""))[:600]
                items.append({"source": feed["name"], "title": e.get("title", "(sans titre)"),
                              "url": link, "snippet": summary})
        except Exception as exc:  # un feed mort ne bloque pas le run
            print(f"[warn] feed {feed['name']} en échec : {exc}", file=sys.stderr)
    # dédup par URL
    seen, unique = set(), []
    for it in items:
        if it["url"] not in seen:
            seen.add(it["url"])
            unique.append(it)
    return unique[: CONFIG["max_candidates"]]


# ---------- 2. Dédup contre Notion (30 derniers jours) ----------

def notion_recent_urls(db_id, url_prop):
    urls = set()
    payload = {
        "filter": {"timestamp": "created_time",
                   "created_time": {"on_or_after": (NOW - timedelta(days=30)).isoformat()}},
        "page_size": 100,
    }
    try:
        r = requests.post(f"{NOTION}/databases/{db_id}/query", headers=NOTION_HEADERS,
                          json=payload, timeout=30)
        r.raise_for_status()
        for page in r.json().get("results", []):
            u = (page["properties"].get(url_prop) or {}).get("url")
            if u:
                urls.add(u)
    except Exception as exc:
        print(f"[warn] dédup Notion {db_id} : {exc}", file=sys.stderr)
    return urls


# ---------- 3. Tri + résumés FR via Gemini ----------

PROMPT = """Tu es l'assistant de veille d'un data analyst / analytics engineer freelance français.
Son périmètre : data analyst, analytics engineering, data engineering, et surtout l'IA
(prompting, outils, agents, LLM). Stack : SQL, Python, Power BI, Metabase, dbt, Airflow,
FastAPI, n8n, Azure, GCP, PostgreSQL, MySQL.

Voici des articles récents (JSON). Pour chacun, décide s'il mérite d'être retenu
(pertinent ET substantiel pour ce profil ; sinon keep=false). Sois sélectif : maximum
15 items retenus, seulement ce qui vaut vraiment le coup.

Chaque item retenu est classé dans UNE cible :
- "maj" : mise à jour d'un outil existant. Champs : outil (un de {outils}),
  type_maj (un de {types}), pertinence (un de {pertinences}).
- "nouveau" : nouvel outil/framework/service. Champs : categorie (un de {cat_outil}),
  pertinence_moi (un de {pert_moi}), signal (un de {signaux}).
- "pratique" : bonne pratique, méthode, retour d'expérience, technique de prompting.
  Champs : categorie (un de {cat_bp}), niveau (un de {niveaux}), outil_concerne (texte libre court).

Pour chaque item retenu, écris aussi :
- titre_fr : titre reformulé en français, clair et concret
- resume_fr : 2 à 4 phrases en français. Pas de jargon marketing, dis ce que c'est,
  ce que ça change, et pourquoi c'est utile (ou pas) pour ce profil.

Écris enfin resume_global_fr : 3-5 phrases en français résumant la semaine
(les tendances, ce qui mérite du temps en priorité).

Réponds UNIQUEMENT en JSON :
{{"items": [{{"index": 0, "keep": true, "target": "maj", "titre_fr": "...", "resume_fr": "...",
  "outil": "...", "type_maj": "...", "pertinence": "...", "categorie": "...",
  "pertinence_moi": "...", "signal": "...", "niveau": "...", "outil_concerne": "..."}}],
 "resume_global_fr": "..."}}

Articles :
{articles}
"""


def gemini_analyze(candidates):
    prompt = PROMPT.format(
        outils=ENUMS["outil"], types=ENUMS["type_maj"], pertinences=ENUMS["pertinence"],
        cat_outil=ENUMS["categorie_outil"], pert_moi=ENUMS["pertinence_moi"],
        signaux=ENUMS["signal"], cat_bp=ENUMS["categorie_bp"], niveaux=ENUMS["niveau"],
        articles=json.dumps([{"index": i, **c} for i, c in enumerate(candidates)], ensure_ascii=False),
    )
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{CONFIG['gemini_model']}:generateContent?key={os.environ['GEMINI_API_KEY']}")
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"response_mime_type": "application/json", "temperature": 0.3}}
    for attempt in range(3):
        r = requests.post(url, json=body, timeout=180)
        if r.status_code == 429:
            time.sleep(30 * (attempt + 1))
            continue
        r.raise_for_status()
        text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(text)
    raise RuntimeError("Gemini : rate limit persistant (429)")


# ---------- 4. Écriture Notion ----------

def notion_create(db_id, properties):
    r = requests.post(f"{NOTION}/pages", headers=NOTION_HEADERS,
                      json={"parent": {"database_id": db_id}, "properties": properties}, timeout=30)
    if not r.ok:
        print(f"[warn] création page Notion : {r.status_code} {r.text[:300]}", file=sys.stderr)
    return r.ok


def rt(text):
    return {"rich_text": [{"text": {"content": text[:1900]}}]}


def push_item(item, url):
    today = {"date": {"start": NOW.date().isoformat()}}
    title = {"title": [{"text": {"content": item.get("titre_fr", "")[:200]}}]}
    cfg = CONFIG["notion"]
    if item["target"] == "maj":
        return notion_create(cfg["db_maj_outils"], {
            "Titre": title, "Date": today, "Lien source": {"url": url},
            "Résumé FR": rt(item.get("resume_fr", "")),
            "Outil": {"select": {"name": valid(item.get("outil"), "outil", "Autre")}},
            "Type de mise à jour": {"select": {"name": valid(item.get("type_maj"), "type_maj", "Nouvelle fonctionnalité")}},
            "Pertinence": {"select": {"name": valid(item.get("pertinence"), "pertinence", "Intéressant")}},
        })
    if item["target"] == "nouveau":
        return notion_create(cfg["db_nouveaux_outils"], {
            "Titre": title, "Date découverte": today, "Lien officiel": {"url": url},
            "Résumé FR": rt(item.get("resume_fr", "")),
            "Catégorie": {"select": {"name": valid(item.get("categorie"), "categorie_outil", "Autre")}},
            "Pertinence pour moi": {"select": {"name": valid(item.get("pertinence_moi"), "pertinence_moi", "À surveiller")}},
            "Signal marché": {"select": {"name": valid(item.get("signal"), "signal", "Émergent")}},
        })
    return notion_create(cfg["db_bonnes_pratiques"], {
        "Titre": title, "Date": today, "Lien source": {"url": url},
        "Résumé FR": rt(item.get("resume_fr", "")),
        "Catégorie": {"select": {"name": valid(item.get("categorie"), "categorie_bp", "Autre")}},
        "Niveau": {"select": {"name": valid(item.get("niveau"), "niveau", "À explorer")}},
        "Outil concerné": rt(item.get("outil_concerne", "")),
    })


def push_digest(counts, resume_global):
    week = NOW.isocalendar()
    notion_create(CONFIG["notion"]["db_digest"], {
        "Semaine": {"title": [{"text": {"content": f"Semaine {week.week} — {NOW.year}"}}]},
        "Période": {"date": {"start": (NOW - timedelta(days=7)).date().isoformat(),
                             "end": NOW.date().isoformat()}},
        "Nb mises à jour": {"number": counts["maj"]},
        "Nb nouveaux outils": {"number": counts["nouveau"]},
        "Nb bonnes pratiques": {"number": counts["pratique"]},
        "Résumé global FR": rt(resume_global),
        "Statut": {"select": {"name": "Envoyé"}},
    })


# ---------- 5. Mail récap ----------

SECTION_LABELS = {"maj": "🔄 Mises à jour outils", "nouveau": "🆕 Nouveaux outils & frameworks",
                  "pratique": "💡 Bonnes pratiques"}


def send_mail(kept, resume_global):
    parts = [f"<p>{html.escape(resume_global)}</p>"]
    for target, label in SECTION_LABELS.items():
        rows = [k for k in kept if k["item"]["target"] == target]
        if not rows:
            continue
        parts.append(f"<h3>{label}</h3><ul>")
        for k in rows:
            it = k["item"]
            parts.append(
                f'<li><a href="{html.escape(k["url"])}">{html.escape(it["titre_fr"])}</a>'
                f'<br><small>{html.escape(it.get("resume_fr", ""))}</small></li>')
        parts.append("</ul>")
    body = ("<html><body style='font-family:sans-serif;max-width:680px'>"
            f"<h2>Veille tech — semaine {NOW.isocalendar().week}</h2>" + "".join(parts) +
            "<p><small>Détails et historique dans Notion → Veille Tech.</small></p></body></html>")
    msg = MIMEText(body, "html", "utf-8")
    msg["Subject"] = f"Veille tech — semaine {NOW.isocalendar().week} ({len(kept)} items)"
    msg["From"] = os.environ["GMAIL_USER"]
    msg["To"] = os.environ["MAIL_TO"]
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(os.environ["GMAIL_USER"], os.environ["GMAIL_APP_PASSWORD"])
        s.send_message(msg)


# ---------- Main ----------

def main():
    candidates = fetch_candidates()
    print(f"{len(candidates)} candidats collectés")
    if not candidates:
        print("Rien de neuf, arrêt.")
        return

    cfg = CONFIG["notion"]
    known = (notion_recent_urls(cfg["db_maj_outils"], "Lien source")
             | notion_recent_urls(cfg["db_nouveaux_outils"], "Lien officiel")
             | notion_recent_urls(cfg["db_bonnes_pratiques"], "Lien source"))
    candidates = [c for c in candidates if c["url"] not in known]
    print(f"{len(candidates)} après dédup Notion")
    if not candidates:
        return

    result = gemini_analyze(candidates)
    kept, counts = [], {"maj": 0, "nouveau": 0, "pratique": 0}
    for item in result.get("items", []):
        if not item.get("keep") or item.get("target") not in counts:
            continue
        idx = item.get("index")
        if not isinstance(idx, int) or idx >= len(candidates):
            continue
        url = candidates[idx]["url"]
        if push_item(item, url):
            counts[item["target"]] += 1
            kept.append({"item": item, "url": url})

    resume_global = result.get("resume_global_fr", "")
    print(f"Retenus : {counts}")
    if kept:
        push_digest(counts, resume_global)
        send_mail(kept, resume_global)
        print("Digest Notion + mail envoyés.")
    else:
        print("Aucun item retenu cette semaine, pas de mail.")


if __name__ == "__main__":
    main()
