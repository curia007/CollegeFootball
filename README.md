# College Football Next Game Predictor 🏈

An autonomous NCAA Division I FBS football game predictor that models and forecasts game outcomes, win probabilities, scorelines, and confidence tiers for the next upcoming round of college football games.

Powered by real-time data from ESPN's college football endpoints, combining market betting lines, ESPN FPI projections, and statistical power ratings.

---

## 🌟 Features

- **Real-Time Data Ingestion**: Live fetching of upcoming NCAA FBS schedules, AP Top 25 rankings, team win-loss records, kickoff times (UTC & local), venue details, and TV broadcasts directly from ESPN APIs.
- **Multi-Factor Prediction Engine**:
  - **Vegas Market Odds**: Point spreads, over/under lines, and moneylines converted to implied win probabilities.
  - **ESPN FPI Matchup Projections**: Real-time integration with ESPN's Football Power Index game simulations.
  - **Composite Power Ratings**: Dynamic team strength ratings based on AP ranking tiers, win percentages, and home-field advantage (+2.5 pts).
- **Projected Scorelines & Margins**: Calculates expected point totals and individual team scores calibrated against game over/under totals and spread margins.
- **Confidence Rating**: Categorizes predictions into `HIGH`, `MEDIUM`, and `TOSS-UP` tiers based on win probability differentials and scoring spreads.
- **Formatted Terminal UI**: Clean table visualization highlighting AP Top 25 matchups, kickoff schedules, predictions, and betting details.
- **Automated Data Export**: Automatically exports full predictions to both JSON (`data/ncaa_fbs_predictions.json`) and CSV (`data/ncaa_fbs_predictions.csv`).

---

## 🚀 Getting Started

### Prerequisites

- Python 3.9+
- Python packages: `requests`, `pandas` (optional, for custom CSV/data analysis)

```bash
pip install requests pandas
```

### Quick Run

Run the predictor with default settings (fetches all upcoming FBS games for the next week and exports predictions):

```bash
python3 main.py
```

or directly:

```bash
python3 ncaa_next_game_prediction.py
```

---

## ⚙️ CLI Options & Usage

The predictor supports various command-line arguments to filter matchups and customize output:

```bash
python3 ncaa_next_game_prediction.py [OPTIONS]
```

### Available Arguments

| Flag | Description |
|------|-------------|
| `--top25-only` | Filter and display only games featuring AP Top 25 ranked teams |
| `--week <NUM>` | Target a specific season week number (e.g. `--week 5`) |
| `--no-export` | Skip exporting predictions to JSON and CSV files |
| `--json <PATH>` | Custom output file path for JSON export (default: `data/ncaa_fbs_predictions.json`) |
| `--csv <PATH>` | Custom output file path for CSV export (default: `data/ncaa_fbs_predictions.csv`) |
| `--no-fpi` | Skip parallel fetching of ESPN FPI matchup predictor for faster execution |

### Examples

**View only AP Top 25 matchups in terminal without writing files:**
```bash
python3 ncaa_next_game_prediction.py --top25-only --no-export
```

**Predict games for a specific week and export to custom files:**
```bash
python3 ncaa_next_game_prediction.py --week 5 --json week5_predictions.json --csv week5_predictions.csv
```

---

## 📊 Prediction Methodology

1. **Market Implied Probability**: Spreads are converted using a normal distribution cumulative distribution function (CDF) calibrated for college football variance ($\sigma = 14.0$). Moneylines are normalized to compute consensus market probabilities.
2. **ESPN FPI Integration**: Concurrently queries ESPN game summaries via multithreading (`ThreadPoolExecutor`) to extract analytical FPI match predictions.
3. **Power Rating Differential**: Assesses team strength according to curated rank curve and season win percentage, adjusting for neutral sites vs. home-field advantage.
4. **Ensemble Blending**: Combines Vegas lines (55%), ESPN FPI (30%), and power ratings (15%) to generate a calibrated win probability.
5. **Score Formulation**: Deconstructs the over/under total line and expected margin to estimate final scores for both home and away teams.

---

## 📁 Output Formats

### CSV Format (`data/ncaa_fbs_predictions.csv`)
Columns include: `Game ID`, `Week`, `Kickoff`, `Away Team`, `Home Team`, `Spread`, `O/U`, `Predicted Winner`, `Predicted Score`, `Win Probability (%)`, `Confidence`, and `Broadcast`.

### JSON Format (`data/ncaa_fbs_predictions.json`)
Structured JSON containing full team metadata, records, rankings, betting odds, probabilities, and predicted scorelines.

---

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
