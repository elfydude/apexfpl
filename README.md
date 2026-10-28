# ⚽ Apex FPL — Advanced Fantasy Premier League AI Strategy & Recommender

[![Deploy to Google Cloud Run](https://github.com/elfydude/apexfpl/actions/workflows/deploy.yml/badge.svg)](https://github.com/elfydude/apexfpl/actions/workflows/deploy.yml)
[![Live Web App](https://img.shields.io/badge/Live%20App-apex--fpl.web.app-brightgreen?logo=firebase)](https://apex-fpl.web.app)
[![Cloud Run Service](https://img.shields.io/badge/GCP-Cloud%20Run-4285F4?logo=googlecloud)](https://apexfpl-345454539836.asia-south1.run.app)
[![Python](https://img.shields.io/badge/Python-3.11+-blue?logo=python)](https://python.org)

**Apex FPL** is a full-stack, AI-powered decision support platform for Fantasy Premier League managers. It combines official Premier League data, in-depth **FotMob** underlying performance metrics (xG, xA, big chances created, shot locations), mathematical optimization via **Mixed Integer Linear Programming (MILP)**, and **Gemini-powered conversational AI** to generate optimal multi-gameweek squad transfers, chip strategies, and starting XI selections.

---

## 🌟 Key Features

### 1. 🧠 MILP Multi-Gameweek Strategy Solver
- **Multi-Period Optimization**: Formulates transfer, benching, and captaincy planning across rolling 1–8 gameweek horizons.
- **Dynamic Free Transfer (FT) Banking**: Accurately simulates the current FPL rules (banking up to 5 FTs) and evaluates the opportunity cost of rolling vs. burning transfers.
- **Chip Timing Optimization**: Suggests mathematically grounded windows for Wildcard, Free Hit, Bench Boost, and Triple Captain chips.
- **Budget & Price Changes**: Enforces strict budget caps, selling value calculations, and squad position constraints (2 GK, 5 DEF, 5 MID, 3 FWD, max 3 players per club).

### 2. 📊 FPL & FotMob Statistical Fusion
- **Live FPL Sync**: Automatic live sync with the official Fantasy Premier League Bootstrap API for fixtures, deadlines, injury news, ownership, and price trends.
- **FotMob Deep Metrics**: Integrates underlying match metrics:
  - Non-penalty Expected Goals (npxG) & Expected Assists (xA)
  - Key passes, big chances created, and defensive actions
  - Historical form weighted by recent fixture difficulty ratings (FDR)

### 3. 🤖 AI Chat Assistant (Powered by Gemini)
- Natural language analysis of transfer dilemmas, bench dilemmas, and captain choices.
- Context-aware chatbot with access to your active team composition, bank balance, chips remaining, and live fixture difficulty.

### 4. 🖥️ Interactive Web Interface
- **Visual Pitch View**: Real-time squad overview with jersey icons, fixture chips, and captaincy badges.
- **Transfer Hub & Comparison Engine**: Side-by-side metric radars, head-to-head comparisons, and projected point delta.
- **Gameweek Roadmap**: Clear visual pathway of planned transfers, captain picks, and banked FT balances for upcoming gameweeks.
- **One-Click FPL ID Import**: Enter any FPL Manager ID to instantly load active squads and transfer history.

### 5. 🚀 Automated Cloud CI/CD
- **Containerized**: Standardized Python runtime with Docker.
- **Cloud Run Backend**: Serverless container hosting on Google Cloud Platform with autoscaling and zero cold-start bottlenecks.
- **Firebase Hosting CDN**: Ultra-fast global static asset caching via Firebase CDN with automated rewrites to Cloud Run.
- **GitHub Actions**: Fully automated push-to-deploy pipeline targeting Google Cloud Run and Firebase Hosting simultaneously.

---

## 🏗️ Architecture

```mermaid
graph TD
    User([FPL Manager]) -->|HTTPS / Browsing| Firebase[Firebase Hosting CDN: apex-fpl.web.app]
    Firebase -->|Static Assets JS/CSS/HTML| User
    Firebase -->|API Rewrites: /api/*| CloudRun[Google Cloud Run: apexfpl]
    
    CloudRun --> Recommender[Recommender Engine]
    CloudRun --> MILP[MILP Strategy Solver (SciPy / NumPy)]
    CloudRun --> ChatEngine[Gemini AI Chat Engine]
    
    Recommender --> FPL[Official FPL API]
    Recommender --> FotMob[FotMob Statistics API]
    
    GitHub[(GitHub Repo)] -->|Push to main| GHActions[GitHub Actions CI/CD]
    GHActions -->|Build & Deploy| CloudRun
    GHActions -->|Deploy & Invalidate| Firebase
```

---

## 🛠️ Tech Stack

- **Backend**: Python 3.11+, standard library `ThreadingHTTPServer`, RESTful JSON API
- **Optimization & ML**: SciPy, NumPy, Pandas, Scikit-learn
- **AI / LLM**: Google Gemini API (`google-genai`)
- **External Data**: Official FPL API, FotMob Wrapper (`aiohttp`, `requests`)
- **Frontend**: Vanilla JavaScript (ES6+), Modern Responsive CSS, HTML5
- **Infrastructure**: Google Cloud Run, Artifact Registry, Google Cloud Build, Firebase Hosting
- **CI/CD**: GitHub Actions

---

## 🚀 Getting Started Locally

### Prerequisites
- Python 3.10 or higher
- Node.js (for optional Firebase deployment / tools)
- Google Cloud SDK (`gcloud`) (for deployment)

### 1. Clone Repository
```bash
git clone https://github.com/elfydude/apexfpl.git
cd apexfpl
```

### 2. Set Up Virtual Environment
```bash
# Windows
python -m venv venv
.\venv\Scripts\activate

# macOS / Linux
python3 -m venv venv
source venv/bin/activate
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Configure Environment Variables
Create a `.env` file in the root directory:
```env
PORT=5050
GEMINI_API_KEY=your_gemini_api_key_here
```

### 5. Run the Server
```bash
python server.py
```
Open your browser and navigate to:
```
http://localhost:5050
```

---

## 📦 Deployment & CI/CD Pipeline

The project features a unified continuous delivery pipeline configured in [`.github/workflows/deploy.yml`](.github/workflows/deploy.yml).

### Triggering Deployments
Every `git push` to `main` automatically runs:
1. **Cloud Run Build & Deploy**: Packs the application into a container, uploads to Google Artifact Registry, and deploys a new revision to Google Cloud Run in `asia-south1`.
2. **Firebase Hosting Deploy**: Deploys the static frontend to Firebase Hosting (`apex-fpl.web.app`) and binds all reverse-proxy routing to Cloud Run.

### GitHub Secrets Required
- `GCP_SA_KEY`: Service account key JSON for GCP with permissions for Cloud Run Admin, Cloud Build Editor, Artifact Registry Admin, Storage Admin, and Firebase Admin.

---

## 📄 License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.
