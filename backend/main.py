"""FastAPI app entry point. Run from the repo root:
uv run uvicorn backend.main:app --reload
"""

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

from backend.api import alerts, classification, entities, errors, evidence, market_history, market_search, markets, projects, stocks, twitter  # noqa: E402 (after load_dotenv, deps read env vars)
from company_graph import api as company_graph_api  # noqa: E402

# The Vite dev server (frontend/, port 5173) calls this API on port 8000, a different origin.
FRONTEND_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]

app = FastAPI(title="What'sPoppin API")

errors.install(app)

app.add_middleware(CORSMiddleware, allow_origins=FRONTEND_ORIGINS, allow_methods=["GET", "POST", "PUT", "DELETE"], allow_headers=["*"])

app.include_router(alerts.router)
app.include_router(classification.router)
app.include_router(entities.router)
app.include_router(evidence.router)
app.include_router(market_search.router)  # before markets: /markets/search is a fixed path
app.include_router(markets.router)
app.include_router(market_history.router)
app.include_router(projects.router)
app.include_router(stocks.router)
app.include_router(twitter.router)
app.include_router(company_graph_api.router)


@app.get("/")
def root() -> dict:
    return {"status": "ok"}
