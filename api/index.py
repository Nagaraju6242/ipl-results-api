import json, re, urllib.request
from http.server import BaseHTTPRequestHandler

IPL_FEED = "https://ipl-stats-sports-mechanic.s3.ap-south-1.amazonaws.com/ipl/feeds"


def fetch_jsonp(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    raw = urllib.request.urlopen(req).read().decode("utf-8")
    return json.loads(raw[raw.index("(") + 1 : raw.rindex(")")])


def clean_name(name):
    return re.sub(r'\s*\(.*?\)\s*', '', name).strip()


def map_dismissal(wicket_type, out_desc, keeper_names):
    """Map IPL wicket type to standardized format."""
    wt = wicket_type.lower() if wicket_type else ""
    if "run out" in wt:
        return "Run Out"
    if "bowled" in wt and "caught" not in wt:
        return "Bowled"
    if "lbw" in wt or "leg before" in wt:
        return "LBW"
    if "stumped" in wt or "stump" in wt:
        return "Stumped"
    if "caught" in wt:
        # Extract fielder name from "c <Fielder> b <Bowler>" or "c & b <Bowler>"
        m = re.match(r'c\s+(.+?)\s+b\s+', out_desc or "")
        if m:
            fielder = m.group(1).strip()
            if any(k in fielder.lower() for k in keeper_names):
                return "KeeperCaught"
        if "& b" in (out_desc or ""):
            return "FielderCaught"  # Caught & Bowled = fielder (bowler) caught
        return "FielderCaught"
    if not wt or "not out" in wt:
        return "Not Out"
    return "Others"


def get_keeper_names(match_id):
    """Get lowercase keeper names from squad data."""
    squad = fetch_jsonp(f"{IPL_FEED}/{match_id}-squad.js")
    names = []
    for team_key in ["squadA", "squadB"]:
        for p in squad.get(team_key, []):
            if str(p.get("IsWK", "0")) == "1":
                names.append(clean_name(p["PlayerShortName"]).lower())
                names.append(clean_name(p["PlayerName"]).lower())
    return names


def split_manhattan(manhattan):
    seen = set()
    for i, m in enumerate(manhattan):
        if m["OverNo"] in seen:
            return manhattan[:i], manhattan[i:]
        seen.add(m["OverNo"])
    return manhattan, []


def get_predictions(match_id):
    summary = fetch_jsonp(f"{IPL_FEED}/{match_id}-matchsummary.js")["MatchSummary"][0]
    inn1 = fetch_jsonp(f"{IPL_FEED}/{match_id}-Innings1.js")["Innings1"]
    inn2 = fetch_jsonp(f"{IPL_FEED}/{match_id}-Innings2.js")["Innings2"]

    # Toss winner
    toss_text = summary["TossDetails"]
    toss_winner = summary["HomeTeamCode"] if summary["HomeTeamName"] in toss_text else summary["AwayTeamCode"]

    # Match winner
    winning_id = str(summary["WinningTeamID"])
    match_winner = summary["HomeTeamCode"] if str(summary["HomeTeamID"]) == winning_id else summary["AwayTeamCode"]

    # Batters
    batters = []
    for inn in [inn1, inn2]:
        for b in inn["BattingCard"]:
            if b["Balls"] > 0:
                batters.append(b)
    batters.sort(key=lambda x: (-x["Balls"], -x["Runs"], -float(x["StrikeRate"])))

    # POTM
    potm = clean_name(summary["MOM"].split("(")[0]) if summary["MOM"] else "N/A"

    # 5th wicket dismissal
    keeper_names = get_keeper_names(match_id)
    fow1 = inn1["FallOfWickets"]
    dismissal_5 = "Not Out"
    if len(fow1) >= 5:
        out_id = fow1[4].get("PlayerID", "")
        for ball in inn1["OverHistory"]:
            if ball["IsWicket"] == "1" and ball.get("OutBatsManID", "") == out_id:
                # Find OutDesc from BattingCard
                out_desc = ""
                for b in inn1["BattingCard"]:
                    if b["PlayerID"] == out_id:
                        out_desc = b.get("OutDesc", "")
                        break
                dismissal_5 = map_dismissal(ball["WicketType"], out_desc, keeper_names)
                break

    # Manhattan
    mg1, mg2 = split_manhattan(inn1["ManhattanGraph"])
    rpo1 = {m["OverNo"]: m["OverRuns"] for m in mg1}
    rpo2 = {m["OverNo"]: m["OverRuns"] for m in mg2}

    s1_at10 = sum(rpo1.get(o, 0) for o in range(10))
    s2_at10 = sum(rpo2.get(o, 0) for o in range(10))
    t1_code = summary["HomeTeamCode"] if str(summary["FirstBattingTeamID"]) == str(summary["HomeTeamID"]) else summary["AwayTeamCode"]
    t2_code = summary["AwayTeamCode"] if t1_code == summary["HomeTeamCode"] else summary["HomeTeamCode"]
    leader = t1_code if s1_at10 > s2_at10 else (t2_code if s2_at10 > s1_at10 else "TIE")

    # Dot balls
    bowler_dots = {}
    for inn in [inn1, inn2]:
        for b in inn["BowlingCard"]:
            name = b["PlayerShortName"]
            bowler_dots[name] = bowler_dots.get(name, 0) + b["DotBalls"]
    top_dot = max(bowler_dots, key=bowler_dots.get) if bowler_dots else "N/A"

    # Top bowler
    all_bowlers = inn1["BowlingCard"] + inn2["BowlingCard"]
    all_bowlers.sort(key=lambda x: (-x["Wickets"], x["Economy"]))

    return {
        "match_id": match_id,
        "individual": {
            "toss_winner": toss_winner,
            "match_winner": match_winner,
            "total_wickets": int(summary["1FallWickets"]) + int(summary["2FallWickets"]),
            "scores": f"{summary['1FallScore']},{summary['2FallScore']}",
            "potm": potm,
            "most_balls_faced": clean_name(batters[0]["PlayerName"]) if batters else "N/A",
            "total_sixes": sum(b["Sixes"] for b in batters),
        },
        "group": {
            "5th_wkt_dismissal": dismissal_5,
            "half_stage_lead": leader,
            "overs_10plus_runs": sum(1 for v in rpo1.values() if v >= 10) + sum(1 for v in rpo2.values() if v >= 10),
            "powerplay_scores": f"{sum(rpo1.get(o, 0) for o in range(6))},{sum(rpo2.get(o, 0) for o in range(6))}",
            "most_dot_balls_bowler": top_dot,
            "top_bowler": all_bowlers[0]["PlayerShortName"] if all_bowlers else "N/A",
            "total_extras": int(inn1["Extras"][0]["TotalExtras"]) + int(inn2["Extras"][0]["TotalExtras"]),
        },
    }


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        from urllib.parse import urlparse, parse_qs
        query = parse_qs(urlparse(self.path).query)
        match_id = query.get("match_id", [None])[0]

        if not match_id:
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "match_id query param required"}).encode())
            return

        try:
            result = get_predictions(match_id)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(result).encode())
        except Exception as e:
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())
