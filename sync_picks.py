import os
import requests

NOTION_TOKEN = os.getenv("NOTION_TOKEN")
WEEKS_DB_ID = os.getenv("NOTION_WEEKS_DB_ID")
MATCHUPS_DB_ID = os.getenv("NOTION_MATCHUPS_DB_ID")
SLEEPER_LEAGUE_ID = os.getenv("SLEEPER_LEAGUE_ID")

HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json"
}

def get_current_week():
    state = requests.get("https://api.sleeper.app/v1/state/nfl").json()
    return state["week"]

def get_week_page_id(week_num):
    url = f"https://api.notion.com/v1/databases/{WEEKS_DB_ID}/query"
    response = requests.post(url, headers=HEADERS)
    res = response.json()
    
    # Check if Notion returned an error
    if response.status_code != 200:
        print(f"Notion API Error ({response.status_code}): {res}")
        return None

    results = res.get("results", [])
    if not results:
        print(f"Database query succeeded (Status 200), but the database contains 0 rows.")
        return None

    target = f"Week {week_num}"
    for row in results:
        for prop_name, prop_data in row["properties"].items():
            if prop_data.get("type") == "title":
                title_list = prop_data.get("title", [])
                if title_list and title_list[0]["plain_text"].strip().lower() == target.lower():
                    return row["id"]

    print(f"Connected to database, but could not find row titled '{target}'.")
    return None

def get_week_data(week):
    # Fetch NFL Games & Winners from ESPN Scoreboard
    espn_url = f"https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard?week={week}"
    espn_events = requests.get(espn_url).json().get("events", [])
    
    nfl_matchups = []
    completed_nfl_winners = {}

    for ev in espn_events:
        comp = ev["competitions"][0]
        teams = [c["team"]["abbreviation"] for c in comp["competitors"]]
        label = f"{teams[0]} / {teams[1]}"
        nfl_matchups.append(label)

        if comp["status"]["type"]["completed"]:
            for team in comp["competitors"]:
                if team.get("winner"):
                    abbr = team["team"]["abbreviation"]
                    opp = [t["team"]["abbreviation"] for t in comp["competitors"] if t != team][0]
                    completed_nfl_winners[f"{abbr} / {opp}"] = abbr
                    completed_nfl_winners[f"{opp} / {abbr}"] = abbr

    # Fetch Sleeper Fantasy Matchups & Winners
    users = {u["user_id"]: u["display_name"][:3].upper() for u in requests.get(f"https://api.sleeper.app/v1/league/{SLEEPER_LEAGUE_ID}/users").json()}
    rosters = requests.get(f"https://api.sleeper.app/v1/league/{SLEEPER_LEAGUE_ID}/rosters").json()
    roster_names = {r["roster_id"]: users.get(r["owner_id"], f"R{r['roster_id']}") for r in rosters}

    matchups = requests.get(f"https://api.sleeper.app/v1/league/{SLEEPER_LEAGUE_ID}/matchups/{week}").json()
    grouped = {}
    for m in matchups:
        grouped.setdefault(m["matchup_id"], []).append(m)

    fantasy_matchups = []
    completed_fantasy_winners = {}

    for _, teams in grouped.items():
        if len(teams) == 2:
            n1 = roster_names.get(teams[0]["roster_id"], "T1")
            n2 = roster_names.get(teams[1]["roster_id"], "T2")
            label = f"{n1}/{n2}"
            fantasy_matchups.append(label)

            if teams[0]["points"] > 0 or teams[1]["points"] > 0:
                win_name = n1 if teams[0]["points"] > teams[1]["points"] else n2
                completed_fantasy_winners[f"{n1}/{n2}"] = win_name
                completed_fantasy_winners[f"{n2}/{n1}"] = win_name

    return nfl_matchups, completed_nfl_winners, fantasy_matchups, completed_fantasy_winners

def sync_notion(week_page_id, nfl_games, nfl_winners, fantasy_games, fantasy_winners):
    # Query matchups related to this week's page
    query_payload = {
        "filter": {
            "property": "Week Link",
            "relation": {"contains": week_page_id}
        }
    }
    rows = requests.post(f"https://api.notion.com/v1/databases/{MATCHUPS_DB_ID}/query", headers=HEADERS, json=query_payload).json().get("results", [])

    existing_titles = {}
    for row in rows:
        title_objs = row["properties"]["Matchup"]["title"]
        if title_objs:
            t = title_objs[0]["plain_text"].strip()
            w_objs = row["properties"]["Winner"]["rich_text"]
            w = w_objs[0]["plain_text"] if w_objs else None
            existing_titles[t] = (row["id"], w)

    all_winners = {**nfl_winners, **fantasy_winners}

    # 1. Create missing matchup rows linked via Relation
    all_upcoming = [("NFL", g) for g in nfl_games] + [("Fantasy", f) for f in fantasy_games]
    for category, title in all_upcoming:
        # Check standard and reverse ordering
        reversed_title = f"{title.split('/')[1].strip()} / {title.split('/')[0].strip()}" if " / " in title else title
        if title not in existing_titles and reversed_title not in existing_titles:
            requests.post(
                "https://api.notion.com/v1/pages",
                headers=HEADERS,
                json={
                    "parent": {"database_id": MATCHUPS_DB_ID},
                    "properties": {
                        "Matchup": {"title": [{"text": {"content": title}}]},
                        "Type": {"select": {"name": category}},
                        "Week Link": {"relation": [{"id": week_page_id}]}
                    }
                }
            )

    # 2. Update winners for finished games
    for title, (page_id, cur_win) in existing_titles.items():
        if not cur_win and title in all_winners:
            requests.patch(
                f"https://api.notion.com/v1/pages/{page_id}",
                headers=HEADERS,
                json={
                    "properties": {
                        "Winner": {"rich_text": [{"text": {"content": all_winners[title]}}]}
                    }
                }
            )

if __name__ == "__main__":
    current_week = get_current_week()
    week_page_id = get_week_page_id(current_week)
    
    if week_page_id:
        nfl_g, nfl_w, fan_g, fan_w = get_week_data(current_week)
        sync_notion(week_page_id, nfl_g, nfl_w, fan_g, fan_w)
    else:
        print(f"Could not find a page for 'Week {current_week}' in NFL Weeks database.")