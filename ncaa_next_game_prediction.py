#!/usr/bin/env python3
"""
NCAA FBS Football Next Game Predictor
======================================
Predicts the winners, win probabilities, and scores of all upcoming NCAA FBS
college football games for the next round of play.

Uses real-time data from ESPN's College Football APIs including:
- Scheduled matchups, dates, venues, and broadcasts
- AP Top 25 rankings and season records
- Vegas spreads, over/under lines, and moneylines
- ESPN FPI Matchup Predictor probabilities
- Composite statistical power rating model with home-field advantage
"""

import os
import sys
import math
import json
import csv
import argparse
import warnings
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, asdict
from concurrent.futures import ThreadPoolExecutor

# Suppress urllib3 LibreSSL compatibility warning on macOS
warnings.filterwarnings('ignore', category=UserWarning)
warnings.filterwarnings('ignore', message='.*urllib3.*')

try:
    import requests
except ImportError:
    print("Error: 'requests' package is required. Install via 'pip install requests'")
    sys.exit(1)


# Constants
ESPN_SCOREBOARD_URL = "http://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard"
ESPN_SUMMARY_URL = "http://site.api.espn.com/apis/site/v2/sports/football/college-football/summary"
FBS_GROUP_ID = 80  # ESPN group ID for NCAA Division I FBS
DEFAULT_OVER_UNDER = 52.5
HOME_FIELD_ADVANTAGE_PTS = 2.5
SPREAD_STD_DEV = 14.0  # College football scoring margin standard deviation


@dataclass
class TeamInfo:
    id: str
    name: str
    display_name: str
    abbreviation: str
    rank: int  # 1-25 or 99 if unranked
    record: str  # e.g., "3-0"
    wins: int
    losses: int
    home_away: str  # 'home' or 'away'
    is_fbs: bool
    logo_url: Optional[str] = None


@dataclass
class GameOdds:
    provider: str
    details: str
    spread: Optional[float]
    over_under: Optional[float]
    favorite_abbr: Optional[str]
    favorite_is_home: Optional[bool]
    home_moneyline: Optional[str]
    away_moneyline: Optional[str]


@dataclass
class GamePrediction:
    game_id: str
    game_name: str
    season_year: int
    week_number: int
    kickoff_utc: str
    kickoff_local: str
    venue_name: str
    broadcast: str
    is_neutral_site: bool
    
    # Teams
    away_team: TeamInfo
    home_team: TeamInfo
    
    # Odds
    odds: Optional[GameOdds]
    
    # Prediction Outputs
    predicted_winner: str
    predicted_winner_abbr: str
    predicted_loser: str
    win_probability_winner_pct: float
    win_probability_home_pct: float
    win_probability_away_pct: float
    predicted_home_score: int
    predicted_away_score: int
    predicted_margin: float
    confidence_level: str  # 'HIGH', 'MEDIUM', 'TOSS-UP'
    prediction_source: str
    key_factors: str


def parse_record(summary_record: str) -> Tuple[int, int]:
    """Parse 'W-L' string into (wins, losses)."""
    try:
        parts = summary_record.split('-')
        return int(parts[0]), int(parts[1])
    except Exception:
        return 0, 0


def calculate_power_rating(team: TeamInfo) -> float:
    """
    Calculate an estimated power rating (points relative to average FBS team)
    based on AP ranking, win percentage, and division.
    """
    if not team.is_fbs:
        return -20.0  # FCS baseline penalty

    # Rank value: #1 is +30 points, #10 is +20, #25 is +10
    rank_rating = 0.0
    if 1 <= team.rank <= 25:
        rank_rating = 32.0 - (team.rank * 0.88)
    
    # Record factor
    total_games = team.wins + team.losses
    win_pct = team.wins / total_games if total_games > 0 else 0.5
    record_rating = (win_pct - 0.5) * 10.0

    return rank_rating + record_rating


def spread_to_win_prob(spread_favorite: float) -> float:
    """
    Convert a point spread (positive number representing margin) to win probability.
    Using normal distribution CDF with CFB standard deviation.
    """
    z = abs(spread_favorite) / SPREAD_STD_DEV
    # Normal CDF approximation
    prob = 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))
    return min(max(prob * 100.0, 50.1), 99.5)


def prob_to_spread_margin(prob_pct: float) -> float:
    """
    Convert win probability (0 to 100) to estimated point spread margin.
    Uses logistic/normal quantile mapping with CFB standard deviation.
    """
    p = max(min(prob_pct / 100.0, 0.999), 0.001)
    logit_val = math.log(p / (1.0 - p))
    # Logistic approximation for normal distribution quantile
    return logit_val * (SPREAD_STD_DEV * 0.5513)


def moneyline_to_win_prob(moneyline_str: Optional[str]) -> Optional[float]:
    """Convert moneyline (+150 or -180) to implied win probability."""
    if not moneyline_str:
        return None
    try:
        val = float(moneyline_str.replace('+', ''))
        if val > 0:
            return (100.0 / (val + 100.0)) * 100.0
        elif val < 0:
            return (abs(val) / (abs(val) + 100.0)) * 100.0
    except Exception:
        return None
    return None


def fetch_espn_scoreboard(week: Optional[int] = None) -> Dict[str, Any]:
    """Fetch current/upcoming FBS scoreboard data from ESPN API."""
    params = {
        'groups': FBS_GROUP_ID,
        'limit': 200
    }
    if week:
        params['week'] = week
        
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
    }
    
    response = requests.get(ESPN_SCOREBOARD_URL, params=params, headers=headers, timeout=10)
    response.raise_for_status()
    return response.json()


def fetch_game_fpi_predictor(event_id: str) -> Optional[Dict[str, Any]]:
    """Fetch ESPN FPI Matchup Predictor for a specific game."""
    try:
        url = f"{ESPN_SUMMARY_URL}?event={event_id}"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
        }
        res = requests.get(url, headers=headers, timeout=5)
        if res.status_code == 200:
            data = res.json()
            return data.get('predictor')
    except Exception:
        return None
    return None


def extract_game_data(event: Dict[str, Any]) -> Optional[Tuple[Dict[str, Any], TeamInfo, TeamInfo, Optional[GameOdds]]]:
    """Extract and sanitize raw event data into structured objects."""
    try:
        competitions = event.get('competitions', [])
        if not competitions:
            return None
        comp = competitions[0]
        
        home_raw = next((c for c in comp.get('competitors', []) if c.get('homeAway') == 'home'), None)
        away_raw = next((c for c in comp.get('competitors', []) if c.get('homeAway') == 'away'), None)
        
        if not home_raw or not away_raw:
            return None
            
        def build_team(raw: Dict[str, Any]) -> TeamInfo:
            t = raw.get('team', {})
            rank = raw.get('curatedRank', {}).get('current', 99)
            rec_summary = "0-0"
            if raw.get('records'):
                rec_summary = raw['records'][0].get('summary', '0-0')
            w, l = parse_record(rec_summary)
            is_fbs = t.get('conferenceId') is not None or rank <= 25 or 'FBS' in t.get('uid', '')
            
            return TeamInfo(
                id=str(t.get('id', '')),
                name=t.get('name', ''),
                display_name=t.get('displayName', 'Unknown Team'),
                abbreviation=t.get('abbreviation', 'UNK'),
                rank=rank,
                record=rec_summary,
                wins=w,
                losses=l,
                home_away=raw.get('homeAway', ''),
                is_fbs=is_fbs,
                logo_url=t.get('logo')
            )
            
        home_team = build_team(home_raw)
        away_team = build_team(away_raw)
        
        # Odds extraction
        odds_obj = None
        raw_odds_list = comp.get('odds', [])
        if raw_odds_list:
            ro = raw_odds_list[0]
            fav_is_home = None
            if 'homeTeamOdds' in ro and ro['homeTeamOdds'].get('favorite'):
                fav_is_home = True
            elif 'awayTeamOdds' in ro and ro['awayTeamOdds'].get('favorite'):
                fav_is_home = False
                
            home_ml = None
            away_ml = None
            if 'moneyline' in ro:
                home_ml = ro['moneyline'].get('home', {}).get('close', {}).get('odds')
                away_ml = ro['moneyline'].get('away', {}).get('close', {}).get('odds')
                
            spread_val = ro.get('spread')
            if spread_val is not None:
                spread_val = float(spread_val)
                
            ou_val = ro.get('overUnder')
            if ou_val is not None:
                ou_val = float(ou_val)
                
            odds_obj = GameOdds(
                provider=ro.get('provider', {}).get('name', 'Consensus'),
                details=ro.get('details', 'EVEN'),
                spread=spread_val,
                over_under=ou_val,
                favorite_abbr=ro.get('homeTeamOdds', {}).get('team', {}).get('abbreviation') if fav_is_home else (
                    ro.get('awayTeamOdds', {}).get('team', {}).get('abbreviation') if fav_is_home is False else None
                ),
                favorite_is_home=fav_is_home,
                home_moneyline=home_ml,
                away_moneyline=away_ml
            )
            
        return comp, home_team, away_team, odds_obj
    except Exception as err:
        return None


def predict_game(
    comp: Dict[str, Any],
    home_team: TeamInfo,
    away_team: TeamInfo,
    odds: Optional[GameOdds],
    fpi_predictor: Optional[Dict[str, Any]],
    season_year: int,
    week_number: int
) -> GamePrediction:
    """
    Generate game prediction by combining Vegas spreads, ESPN FPI predictor,
    and power ratings model.
    """
    is_neutral = comp.get('neutralSite', False)
    hfa = 0.0 if is_neutral else HOME_FIELD_ADVANTAGE_PTS
    
    # 1. Base Power Ratings Model
    home_power = calculate_power_rating(home_team) + hfa
    away_power = calculate_power_rating(away_team)
    power_diff = home_power - away_power  # Positive means home favored
    power_win_prob_home = spread_to_win_prob(abs(power_diff)) if power_diff >= 0 else (100.0 - spread_to_win_prob(abs(power_diff)))
    
    # 2. Vegas Line Model (Spread & Moneyline)
    vegas_win_prob_home = None
    vegas_margin_home = None
    if odds and odds.spread is not None:
        spread_mag = abs(odds.spread)
        if odds.favorite_is_home is True:
            vegas_win_prob_home = spread_to_win_prob(spread_mag)
            vegas_margin_home = spread_mag
        elif odds.favorite_is_home is False:
            vegas_win_prob_home = 100.0 - spread_to_win_prob(spread_mag)
            vegas_margin_home = -spread_mag
        else:
            vegas_win_prob_home = 50.0
            vegas_margin_home = 0.0
            
    # Check moneyline
    if odds and (odds.home_moneyline or odds.away_moneyline):
        home_ml_prob = moneyline_to_win_prob(odds.home_moneyline)
        away_ml_prob = moneyline_to_win_prob(odds.away_moneyline)
        if home_ml_prob and away_ml_prob:
            # Normalize sum to 100
            tot = home_ml_prob + away_ml_prob
            norm_home = (home_ml_prob / tot) * 100.0
            if vegas_win_prob_home is not None:
                vegas_win_prob_home = (vegas_win_prob_home * 0.7) + (norm_home * 0.3)
            else:
                vegas_win_prob_home = norm_home

    # 3. ESPN FPI Matchup Predictor Model
    fpi_win_prob_home = None
    if fpi_predictor and 'homeTeam' in fpi_predictor:
        try:
            fpi_proj = float(fpi_predictor['homeTeam'].get('gameProjection', 50.0))
            fpi_win_prob_home = fpi_proj
        except Exception:
            pass

    # Blend probabilities
    weights = []
    probs = []
    
    if vegas_win_prob_home is not None:
        probs.append(vegas_win_prob_home)
        weights.append(0.55)  # Market odds have highest predictive accuracy
        
    if fpi_win_prob_home is not None:
        probs.append(fpi_win_prob_home)
        weights.append(0.30)  # ESPN FPI predictor
        
    # Power rating model fallback / complement
    probs.append(power_win_prob_home)
    weights.append(0.15 if vegas_win_prob_home is not None else 0.70)
    
    # Weighted average probability for home team
    total_weight = sum(weights)
    final_home_prob = sum(p * w for p, w in zip(probs, weights)) / total_weight
    final_home_prob = round(min(max(final_home_prob, 1.0), 99.0), 1)
    final_away_prob = round(100.0 - final_home_prob, 1)
    
    # Determine winner
    if final_home_prob >= 50.0:
        winner = home_team.display_name
        winner_abbr = home_team.abbreviation
        loser = away_team.display_name
        winner_prob = final_home_prob
    else:
        winner = away_team.display_name
        winner_abbr = away_team.abbreviation
        loser = home_team.display_name
        winner_prob = final_away_prob

    # Predicted Scores Calculation
    total_points = odds.over_under if (odds and odds.over_under) else DEFAULT_OVER_UNDER
    
    # Margin estimate: from Vegas spread if available, else from power rating / prob
    if vegas_margin_home is not None:
        predicted_home_margin = vegas_margin_home
    else:
        # Convert win prob back to point margin approx
        predicted_home_margin = prob_to_spread_margin(final_home_prob)
        
    raw_home_score = (total_points + predicted_home_margin) / 2.0
    raw_away_score = (total_points - predicted_home_margin) / 2.0
    
    pred_home_score = max(int(round(raw_home_score)), 3)
    pred_away_score = max(int(round(raw_away_score)), 3)
    
    # Ensure no ties in prediction
    if pred_home_score == pred_away_score:
        if final_home_prob >= 50.0:
            pred_home_score += 3
        else:
            pred_away_score += 3

    # Confidence rating
    if winner_prob >= 75.0 or abs(predicted_home_margin) >= 12.0:
        confidence = "HIGH"
    elif winner_prob >= 60.0 or abs(predicted_home_margin) >= 4.5:
        confidence = "MEDIUM"
    else:
        confidence = "TOSS-UP"

    # Prediction source and key factors
    sources = []
    if vegas_win_prob_home is not None:
        sources.append("Vegas Line")
    if fpi_win_prob_home is not None:
        sources.append("ESPN FPI")
    sources.append("Composite Power Rating")
    source_str = " + ".join(sources)
    
    factors = []
    if home_team.rank <= 25:
        factors.append(f"#{home_team.rank} {home_team.name}")
    if away_team.rank <= 25:
        factors.append(f"#{away_team.rank} {away_team.name}")
    if not is_neutral:
        factors.append(f"Home Field ({home_team.name})")
    if odds and odds.details:
        factors.append(f"Spread {odds.details}")
    factors.append(f"Records: {away_team.abbreviation} ({away_team.record}) vs {home_team.abbreviation} ({home_team.record})")
    key_factors_str = "; ".join(factors)

    # Format date & venue
    kickoff_utc = comp.get('date', '')
    kickoff_local = kickoff_utc
    try:
        dt = datetime.strptime(kickoff_utc, "%Y-%m-%dT%H:%SZ")
        kickoff_local = dt.strftime("%a, %b %d • %I:%M %p UTC")
    except Exception:
        pass
        
    venue_name = comp.get('venue', {}).get('fullName', 'TBD Venue')
    broadcast_names = [b.get('names', [''])[0] for b in comp.get('broadcasts', []) if b.get('names')]
    broadcast_str = ", ".join(broadcast_names) if broadcast_names else "TBD"

    return GamePrediction(
        game_id=str(comp.get('id', '')),
        game_name=f"{away_team.display_name} at {home_team.display_name}",
        season_year=season_year,
        week_number=week_number,
        kickoff_utc=kickoff_utc,
        kickoff_local=kickoff_local,
        venue_name=venue_name,
        broadcast=broadcast_str,
        is_neutral_site=is_neutral,
        away_team=away_team,
        home_team=home_team,
        odds=odds,
        predicted_winner=winner,
        predicted_winner_abbr=winner_abbr,
        predicted_loser=loser,
        win_probability_winner_pct=winner_prob,
        win_probability_home_pct=final_home_prob,
        win_probability_away_pct=final_away_prob,
        predicted_home_score=pred_home_score,
        predicted_away_score=pred_away_score,
        predicted_margin=abs(pred_home_score - pred_away_score),
        confidence_level=confidence,
        prediction_source=source_str,
        key_factors=key_factors_str
    )


def predict_all_next_games(week: Optional[int] = None, fetch_fpi: bool = True) -> List[GamePrediction]:
    """Fetch and predict all next NCAA FBS games for the upcoming week."""
    raw_data = fetch_espn_scoreboard(week=week)
    
    season_info = raw_data.get('season', {})
    season_year = season_info.get('year', 2026)
    week_info = raw_data.get('week', {})
    week_number = week_info.get('number', 1)
    
    events = raw_data.get('events', [])
    if not events:
        return []

    # Extract valid games
    extracted = []
    for event in events:
        item = extract_game_data(event)
        if item:
            comp, home_team, away_team, odds = item
            event_id = str(event.get('id', ''))
            extracted.append((event_id, comp, home_team, away_team, odds))

    # Concurrently fetch FPI predictors if enabled
    fpi_map: Dict[str, Optional[Dict[str, Any]]] = {}
    if fetch_fpi:
        event_ids = [item[0] for item in extracted]
        with ThreadPoolExecutor(max_workers=12) as executor:
            future_to_id = {executor.submit(fetch_game_fpi_predictor, eid): eid for eid in event_ids}
            for future in future_to_id:
                eid = future_to_id[future]
                try:
                    fpi_map[eid] = future.result()
                except Exception:
                    fpi_map[eid] = None

    # Generate predictions
    predictions: List[GamePrediction] = []
    for event_id, comp, home_team, away_team, odds in extracted:
        fpi = fpi_map.get(event_id)
        pred = predict_game(
            comp=comp,
            home_team=home_team,
            away_team=away_team,
            odds=odds,
            fpi_predictor=fpi,
            season_year=season_year,
            week_number=week_number
        )
        predictions.append(pred)

    return predictions


def print_predictions_table(predictions: List[GamePrediction], week: int, season: int):
    """Print beautifully formatted ASCII table of game predictions."""
    print("=" * 110)
    print(f"🏈 NCAA FBS FOOTBALL PREDICTIONS — SEASON {season} • WEEK {week} 🏈".center(110))
    print("=" * 110)
    print(f"{'#':<3} | {'MATCHUP':<46} | {'LINE':<12} | {'PREDICTED WINNER':<24} | {'SCORE':<9} | {'PROB':<6} | {'CONF':<7}")
    print("-" * 110)

    for idx, p in enumerate(predictions, 1):
        away_rank_str = f"#{p.away_team.rank} " if p.away_team.rank <= 25 else ""
        home_rank_str = f"#{p.home_team.rank} " if p.home_team.rank <= 25 else ""
        
        matchup = f"{away_rank_str}{p.away_team.display_name} @ {home_rank_str}{p.home_team.display_name}"
        if len(matchup) > 46:
            matchup = matchup[:43] + "..."
            
        line_str = p.odds.details if (p.odds and p.odds.details) else "EVEN"
        if len(line_str) > 12:
            line_str = line_str[:12]
            
        pred_winner = f"★ {p.predicted_winner_abbr}"
        score_str = f"{p.predicted_away_score}-{p.predicted_home_score}"
        prob_str = f"{p.win_probability_winner_pct:.1f}%"
        conf_str = p.confidence_level
        
        print(f"{idx:<3} | {matchup:<46} | {line_str:<12} | {pred_winner:<24} | {score_str:<9} | {prob_str:<6} | {conf_str:<7}")

    print("=" * 110)
    print(f"Total Games Predicted: {len(predictions)}")
    
    # Summary Insights
    top25_games = [p for p in predictions if p.home_team.rank <= 25 or p.away_team.rank <= 25]
    high_conf_games = [p for p in predictions if p.confidence_level == "HIGH"]
    tossup_games = [p for p in predictions if p.confidence_level == "TOSS-UP"]
    
    print(f"Top 25 Matchups: {len(top25_games)} | High Confidence Picks: {len(high_conf_games)} | Close/Toss-up Games: {len(tossup_games)}")
    print("=" * 110)


def save_predictions_json(predictions: List[GamePrediction], filepath: str):
    """Export predictions to a formatted JSON file."""
    output_data = []
    for p in predictions:
        d = {
            "game_id": p.game_id,
            "game_name": p.game_name,
            "season_year": p.season_year,
            "week_number": p.week_number,
            "kickoff_utc": p.kickoff_utc,
            "kickoff_local": p.kickoff_local,
            "venue": p.venue_name,
            "broadcast": p.broadcast,
            "is_neutral_site": p.is_neutral_site,
            "away_team": asdict(p.away_team),
            "home_team": asdict(p.home_team),
            "odds": asdict(p.odds) if p.odds else None,
            "prediction": {
                "predicted_winner": p.predicted_winner,
                "predicted_winner_abbreviation": p.predicted_winner_abbr,
                "predicted_loser": p.predicted_loser,
                "win_probability_winner_pct": p.win_probability_winner_pct,
                "win_probability_home_pct": p.win_probability_home_pct,
                "win_probability_away_pct": p.win_probability_away_pct,
                "predicted_home_score": p.predicted_home_score,
                "predicted_away_score": p.predicted_away_score,
                "predicted_margin": p.predicted_margin,
                "confidence_level": p.confidence_level,
                "model_sources": p.prediction_source,
                "key_factors": p.key_factors
            }
        }
        output_data.append(d)
        
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(output_data, f, indent=2)
    print(f"✓ Saved JSON predictions to: {filepath}")


def save_predictions_csv(predictions: List[GamePrediction], filepath: str):
    """Export predictions to a CSV file for spreadsheet analysis."""
    headers = [
        "Game ID", "Week", "Kickoff Time", "Away Team", "Away Rank", "Away Record",
        "Home Team", "Home Rank", "Home Record", "Spread / Line", "Over / Under",
        "Predicted Winner", "Win Probability (%)", "Predicted Score (Away-Home)",
        "Predicted Margin", "Confidence Level", "Venue", "Broadcast"
    ]
    
    with open(filepath, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        for p in predictions:
            writer.writerow([
                p.game_id,
                p.week_number,
                p.kickoff_local,
                p.away_team.display_name,
                p.away_team.rank if p.away_team.rank <= 25 else "UR",
                p.away_team.record,
                p.home_team.display_name,
                p.home_team.rank if p.home_team.rank <= 25 else "UR",
                p.home_team.record,
                p.odds.details if p.odds else "N/A",
                p.odds.over_under if (p.odds and p.odds.over_under) else "N/A",
                p.predicted_winner,
                f"{p.win_probability_winner_pct:.1f}%",
                f"{p.predicted_away_score} - {p.predicted_home_score}",
                p.predicted_margin,
                p.confidence_level,
                p.venue_name,
                p.broadcast
            ])
    print(f"✓ Saved CSV predictions to: {filepath}")


def main():
    parser = argparse.ArgumentParser(
        description="Predict the winners and scores of all next NCAA FBS Football games."
    )
    parser.add_argument(
        "--week", type=int, default=None,
        help="Specify the NCAA week number (default: next upcoming week)"
    )
    parser.add_argument(
        "--top25-only", action="store_true",
        help="Only show games involving AP Top 25 ranked teams"
    )
    parser.add_argument(
        "--json", type=str, default="ncaa_fbs_predictions.json",
        help="Path to export predictions as JSON (default: ncaa_fbs_predictions.json)"
    )
    parser.add_argument(
        "--csv", type=str, default="ncaa_fbs_predictions.csv",
        help="Path to export predictions as CSV (default: ncaa_fbs_predictions.csv)"
    )
    parser.add_argument(
        "--no-export", action="store_true",
        help="Disable exporting predictions to files"
    )
    
    args = parser.parse_args()

    print("\n🏈 Fetching latest NCAA FBS schedule, odds, and predictor data...")
    try:
        predictions = predict_all_next_games(week=args.week)
    except Exception as e:
        print(f"Error fetching and predicting NCAA games: {e}", file=sys.stderr)
        sys.exit(1)

    if not predictions:
        print("No upcoming games found for the specified round.")
        sys.exit(0)

    season = predictions[0].season_year
    week = predictions[0].week_number

    display_preds = predictions
    if args.top25_only:
        display_preds = [p for p in predictions if p.home_team.rank <= 25 or p.away_team.rank <= 25]

    print_predictions_table(display_preds, week=week, season=season)

    if not args.no_export:
        save_predictions_json(predictions, args.json)
        save_predictions_csv(predictions, args.csv)


if __name__ == "__main__":
    main()
