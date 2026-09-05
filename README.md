# Setu: Fintech Settlement AI Engine

Welcome to Setu, a production-ready, autonomous AI engine designed to revolutionize fintech reconciliation and settlement operations. This platform combines a high-concurrency dynamic REST API, real-time data streaming, and advanced machine learning to predict, manage, and resolve settlement exceptions on the fly.

## Project Overview

Setu provides a comprehensive backend and an intuitive frontend interface tailored to streamline financial operations. It acts as an autonomous FinOps assistant, ingesting live transactional data, predicting settlement health, and providing natural language reconciliation through a state-of-the-art AI engine.

## Key Features

- **Autonomous AI Reconciliation:** A powerful AI engine capable of answering complex natural language queries regarding settlements, payouts, and missing transactions.
- **Live Data Ingestion & Streaming:** Real-time endpoints to ingest transactions, settlement batches, and merchant ledger events seamlessly.
- **Machine Learning Predictor:** Integrated HistGradientBoosting models for multi-class settlement health classification and accurate settlement duration regression.
- **Dynamic REST API:** High-concurrency backend service built in Python, supporting live webhooks and automated hot-reloading for data.
- **Interactive Support UI:** A modern, clean frontend designed for settlement support teams to easily interface with the reconciliation engine.

## Architecture & Components

The repository is structured into distinct, decoupled modules:

### 1. Backend 
The core API server built to handle production-scale transaction ingestion and AI reconciliation.
- Provides endpoints for data syncing, health checking, and live data streaming.
- Manages an integrated SQLite database for efficient local processing and query handling.
- Powered by an advanced language model agent tailored for settlement operations.

### 2. Machine Learning Pipeline 
A robust predictive suite that enhances settlement expectations.
- Predicts settlement delays and evaluates the health of the settlement process.
- Features empirical calibration across different banking rails (NEFT, RTGS, IMPS, UPI).
- Employs trained models with high accuracy to minimize human intervention in exception handling.

### 3. Frontend 
The user-facing portal that interacts with the backend.
- Provides a responsive workspace for support agents to chat with the AI and track transaction lifecycles.
- Seamlessly integrates with the backend APIs to display real-time statuses and predictions.

## Getting Started

To get the system running locally:

1. Navigate to the `backend` directory.
2. Install the required dependencies: `pip install -r requirements.txt`.
3. Start the main API server: `python api_server.py`. 
4. Open the `index.html` file in the `frontend` directory in your web browser to access the Setu dashboard.

## Technical Stack

- **Backend:** Python, SQLite, BaseHTTPRequestHandler (Zero-dependency portability design)
- **Machine Learning:** Scikit-Learn, Pandas, NumPy, Joblib
- **Frontend:** HTML5, CSS3, Vanilla JavaScript

## Data Handling

The system supports both SQLite database interactions and automated ingestion from local CSV datasets (located in the Database folders). You can force a resync of all datasets from disk via the dedicated synchronization endpoint, ensuring that your AI engine always operates on the freshest data.

## Documentation

Interactive API documentation is built directly into the server. Once the backend is running, navigate to the `/docs` endpoint in your browser to explore the available REST interfaces and test live payloads.

---

Built for scale, precision, and the future of automated FinOps.
