# ApexFPL Cloud Deployment Guide

This directory houses all cloud deployment specifications, container definitions, and infrastructure configuration files for ApexFPL.

---

## Architecture Overview

```
Client Browser
      │
      ▼
Firebase Hosting CDN (Global Edge) ──── https://apex-fpl.web.app
      │
      ├──> Static Assets (index.html, styles.css, app.js)
      │
      └──> API Rewrites (/api/*)
            │
            ▼
Google Cloud Run Service (asia-south1) ── https://apexfpl-...asia-south1.run.app
      │
      └──> Python 3.11 Container (FastAPI, HiGHS MILP Solver, Strategy Engines)
```

---

## Directory Contents

| File | Description |
| :--- | :--- |
| [`Dockerfile`](Dockerfile) | Multi-stage, lightweight Debian Python 3.11 container specification |
| [`.dockerignore`](.dockerignore) | Patterns excluded from Docker image builds |
| [`cloudbuild.yaml`](cloudbuild.yaml) | Declarative Google Cloud Build multi-step pipeline |
| [`.gcloudignore`](.gcloudignore) | Files excluded when uploading sources to Google Cloud |
| [`firebase.json`](firebase.json) | Firebase Hosting edge CDN, CORS caching headers & Cloud Run proxy rewrites |
| [`.firebaserc`](.firebaserc) | Firebase project linkage mapping (`vijay-tmt`) |
| [`deploy.sh`](deploy.sh) | Executable bash deployment script for manual or automated executions |

---

## Automated Deployment (CI/CD)

Any commit pushed to `main` or `master` triggers [`.github/workflows/deploy.yml`](../.github/workflows/deploy.yml):
1. **Authenticates** to GCP via `GCP_SA_KEY` service account secret.
2. **Builds & Deploys** container to Google Cloud Run (`apexfpl` in `asia-south1`).
3. **Deploys** frontend assets and proxy rules to Firebase Hosting (`apex-fpl.web.app`).

---

## Manual Deployment

To run a manual deployment from the terminal:

```bash
# Ensure gcloud and firebase-tools are authenticated
bash deployment/deploy.sh
```
