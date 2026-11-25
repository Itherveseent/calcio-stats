"""Scarica risultati e statistiche dei giocatori di calcio (API pubblica non ufficiale di ESPN)
e li accumula in data/games.json. Campionati: Serie A, Premier League, Liga, Bundesliga,
Ligue 1, Champions League."""
import json, os, sys, time, traceback, datetime as dt
import requests

BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"
OUT = "data/games.json"
START = os.environ.get("START_DATE", "2026-08-01")  # inizio della stagione in corso

LEAGUES = {
    "ita.1": "Serie A",
    "eng.1": "Premier League",
    "esp.1": "La Liga",
    "ger.1": "Bundesliga",
    "fra.1": "Ligue 1",
    "uefa.champions": "Champions League",
}


def get(url, **params):
    for i in range(3):
        try:
            r = requests.get(url, params=params, timeout=25)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            print("  errore di rete, riprovo:", e, file=sys.stderr)
            time.sleep(1.5 * (i + 1))
    return None


def gi(d, k):
    try:
        return int(d.get(k))
    except (TypeError, ValueError):
        return 0


def team_abbr(t):
    return t.get("abbreviation") or t.get("shortDisplayName") or t.get("displayName", "?")


# Sinonimi noti dei nomi di statistica ESPN usa per il calcio (il nome esatto non è
# documentato e può variare da competizione a competizione, quindi ne proviamo diversi).
SYNONYMS = {
    "g":  ["goals", "totalgoals", "g"],
    "a":  ["assists", "goalassists", "totalassists", "a"],
    "sh": ["totalshots", "shotstotal", "shots", "sh"],
    "st": ["shotsontarget", "ontargetscoringatt", "st"],
    "fc": ["foulscommitted", "fouls", "fc"],
    "fa": ["foulssuffered", "wasfouled", "foulswon", "fa"],
    "yc": ["yellowcards", "totalyellowcards", "yc"],
    "rc": ["redcards", "totalredcards", "rc"],
    "sv": ["saves", "totalsaves", "goalkeepersaves", "sv"],
}


def _num(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def _entry_stats(entry):
    """Qualunque sia la forma in cui ESPN mette le statistiche su un singolo giocatore
    (una lista "statistics" di categorie, o una lista "stats" piatta di {name,value}),
    le appiattisce in un dizionario nome_minuscolo -> valore."""
    out = {}
    for block in (entry.get("statistics") or []):
        if not isinstance(block, dict):
            continue
        for s in (block.get("stats") or []):
            if isinstance(s, dict):
                name = (s.get("name") or s.get("abbreviation") or s.get("displayName") or "").lower()
                if name:
                    out[name] = s.get("value", s.get("displayValue"))
    for s in (entry.get("stats") or []):
        if isinstance(s, dict):
            name = (s.get("name") or s.get("abbreviation") or s.get("displayName") or "").lower()
            if name:
                out[name] = s.get("value", s.get("displayValue"))
    return out


def _find_player_entries(node, team=None, out=None):
    """Cerca in tutto il JSON della partita qualunque oggetto che sembri un giocatore
    (ha una chiave "athlete"), portandosi dietro la squadra più vicina trovata nel
    percorso. Non assume un punto fisso come fa l'NBA, perché per il calcio ESPN lo
    mette altrove."""
    if out is None:
        out = []
    if isinstance(node, dict):
        local_team = team
        t = node.get("team")
        if isinstance(t, dict):
            abbr = team_abbr(t)
            if abbr and abbr != "?":
                local_team = abbr
        if isinstance(node.get("athlete"), dict):
            out.append((local_team, node))
        for k, v in node.items():
            _find_player_entries(v, local_team, out)
    elif isinstance(node, list):
        for item in node:
            _find_player_entries(item, team, out)
    return out


def _events(summary):
    """Estrae gol/assist/ammonizioni/espulsioni dagli eventi della partita
    (header.competitions[0].details), che per il calcio è dove ESPN li mette
    davvero, invece che in una tabella di statistiche per giocatore."""
    comps = (summary.get("header") or {}).get("competitions", [])
    details = comps[0].get("details", []) if comps else []
    out = {}

    def bump(ath, field):
        if not ath:
            return
        pid = ath.get("id") or ath.get("displayName")
        if not pid:
            return
        rec = out.setdefault(pid, {
            "n": ath.get("displayName", ""),
            "g": 0, "a": 0, "yc": 0, "rc": 0,
        })
        rec[field] += 1

    for ev in details:
        typ = (ev.get("type") or {}).get("text", "") or ""
        typ = typ.lower()
        involved = ev.get("athletesInvolved") or []
        if "yellow card" in typ:
            bump(involved[0] if involved else None, "yc")
        elif "red card" in typ:
            bump(involved[0] if involved else None, "rc")
        elif "goal" in typ and "own goal" not in typ:
            if involved:
                bump(involved[0], "g")
            if len(involved) > 1:
                bump(involved[1], "a")
        elif "own goal" in typ:
            if involved:
                bump(involved[0], "g")
    return out, details


def players(summary, debug=False):
    out = {}
    entries = _find_player_entries(summary.get("boxscore", summary))
    if not entries:
        entries = _find_player_entries(summary)
    events, raw_details = _events(summary)
    if debug:
        print("  DEBUG chiavi summary:", list(summary.keys()), file=sys.stderr)
        print("  DEBUG chiavi boxscore:", list(summary.get("boxscore", {}).keys()), file=sys.stderr)
        print("  DEBUG giocatori trovati nel JSON:", len(entries), file=sys.stderr)
        if entries:
            team0, e0 = entries[0]
            print("  DEBUG esempio squadra:", team0, "chiavi giocatore:", list(e0.keys()), file=sys.stderr)
            print("  DEBUG esempio athlete:", e0.get("athlete"), file=sys.stderr)
            print("  DEBUG esempio statistics grezzo:", e0.get("statistics"), file=sys.stderr)
            print("  DEBUG esempio mainStat:", e0.get("mainStat"), file=sys.stderr)
            print("  DEBUG esempio summary campo:", e0.get("summary"), file=sys.stderr)
            print("  DEBUG esempio displayValue:", e0.get("displayValue"), file=sys.stderr)
        print("  DEBUG eventi (details) trovati:", len(raw_details), file=sys.stderr)
        if raw_details:
            print("  DEBUG esempio evento grezzo:", raw_details[0], file=sys.stderr)
        print("  DEBUG giocatori con eventi gol/cartellini:", len(events), file=sys.stderr)
        rosters = summary.get("rosters")
        if rosters:
            print("  DEBUG chiavi primo roster (livello squadra):", list(rosters[0].keys()), file=sys.stderr)
    for team, entry in entries:
        info = entry.get("athlete", {})
        pid = info.get("id") or info.get("displayName")
        if not pid:
            continue
        d = _entry_stats(entry)
        rec = out.setdefault(pid, {
            "n": info.get("displayName", ""), "t": team or "?",
            "pos": (info.get("position") or {}).get("abbreviation", ""),
            "g": 0, "a": 0, "sh": 0, "st": 0, "fc": 0, "fa": 0, "yc": 0, "rc": 0, "sv": 0,
        })
        for field, names in SYNONYMS.items():
            for name in names:
                if name in d:
                    rec[field] = max(rec[field], _num(d[name]))
                    break
    # Integra/sovrascrive gol, assist e cartellini con quelli ricavati dagli eventi,
    # che per il calcio sono più affidabili della tabella statistiche.
    for pid, ev in events.items():
        rec = out.get(pid)
        if rec is None:
            rec = out.setdefault(pid, {
                "n": ev["n"], "t": "?", "pos": "",
                "g": 0, "a": 0, "sh": 0, "st": 0, "fc": 0, "fa": 0, "yc": 0, "rc": 0, "sv": 0,
            })
        rec["g"] = max(rec["g"], ev["g"])
        rec["a"] = max(rec["a"], ev["a"])
        rec["yc"] = max(rec["yc"], ev["yc"])
        rec["rc"] = max(rec["rc"], ev["rc"])
    return list(out.values())


def save(data):
    data["updated"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes")
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, separators=(",", ":"), ensure_ascii=False)
    os.replace(tmp, OUT)


def main():
    os.makedirs("data", exist_ok=True)
    data = {"games": {}}
    if os.path.exists(OUT):
        try:
            data = json.load(open(OUT, encoding="utf-8"))
        except Exception:
            print("File dati illeggibile, riparto da zero", file=sys.stderr)
            data = {"games": {}}
    games = data.setdefault("games", {})

    if games and data.get("from") == START:
        last = max(g["date"][:10] for g in games.values())
        start = dt.date.fromisoformat(last) - dt.timedelta(days=2)
    else:
        start = dt.date.fromisoformat(START)
    data["from"] = START

    debug_done = False
    day, end, errors, added_total = start, dt.date.today() + dt.timedelta(days=1), 0, 0
    print("Scarico dal", start, "al", end, "per", len(LEAGUES), "campionati - partite già salvate:", len(games))
    while day <= end:
        for lg in LEAGUES:
            sb = get(f"{BASE}/{lg}/scoreboard", dates=day.strftime("%Y%m%d"), limit=100)
            added = 0
            for ev in (sb or {}).get("events", []):
                key = lg + ":" + ev["id"]
                try:
                    if not ev["status"]["type"]["completed"] or key in games:
                        continue
                    comp = ev["competitions"][0]["competitors"]
                    home = next(c for c in comp if c["homeAway"] == "home")
                    away = next(c for c in comp if c["homeAway"] == "away")
                    summ = get(f"{BASE}/{lg}/summary", event=ev["id"])
                    if not summ:
                        errors += 1
                        continue
                    pls = players(summ, debug=not debug_done)
                    debug_done = True
                    games[key] = {
                        "date": ev["date"], "lg": lg,
                        "away": {"t": team_abbr(away["team"]), "s": gi(away, "score")},
                        "home": {"t": team_abbr(home["team"]), "s": gi(home, "score")},
                        "players": pls,
                    }
                    added += 1
                except Exception:
                    errors += 1
                    print("Partita saltata:", lg, ev.get("id"), file=sys.stderr)
                    traceback.print_exc()
            added_total += added
            if added:
                save(data)
        day += dt.timedelta(days=1)
    print("Aggiunte", added_total, "partite nuove - problemi:", errors)

    upcoming = []
    for off in (0, 1, 2):
        d = dt.date.today() + dt.timedelta(days=off)
        for lg, lgname in LEAGUES.items():
            sb = get(f"{BASE}/{lg}/scoreboard", dates=d.strftime("%Y%m%d"), limit=100)
            for ev in (sb or {}).get("events", []):
                try:
                    if ev["status"]["type"]["completed"]:
                        continue
                    comp = ev["competitions"][0]["competitors"]
                    home = next(c for c in comp if c["homeAway"] == "home")
                    away = next(c for c in comp if c["homeAway"] == "away")
                    upcoming.append({"date": ev["date"], "lg": lg, "lgname": lgname,
                                      "home": team_abbr(home["team"]), "away": team_abbr(away["team"])})
                except Exception:
                    errors += 1
    data["upcoming"] = sorted(upcoming, key=lambda g: g["date"])
    data["leagues"] = LEAGUES
    save(data)
    print("Fatto. Partite totali:", len(games), "- problemi:", errors)


if __name__ == "__main__":
    main()
