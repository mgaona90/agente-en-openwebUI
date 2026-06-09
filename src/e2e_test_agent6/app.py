"""FastAPI entrypoint."""
from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import tracing
from .config import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    tracing.configure()
    yield
    tracing.flush()


def create_app() -> FastAPI:
    app = FastAPI(title="my-agent", version="0.1.0", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    from .routes.chat import router as chat_router
    from .routes.health import router as health_router
    from .routes.models import router as models_router

    app.include_router(health_router)
    app.include_router(models_router)
    app.include_router(chat_router)

    return app


app = create_app()
