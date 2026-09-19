# Script: config.py
# Purpose: Read every setting of the service from the .env file and the environment in one place
# Author: Andrés Gallardo
# Date: September 2026

from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict









#### Step 1: Locate the .env file ####

# Build the path from the location of this file, so the working directory never matters
BACKEND_FOLDER = Path(__file__).resolve().parent.parent
ENV_FILE_PATH = BACKEND_FOLDER / ".env"









#### Step 2: Define the settings ####

# Hold every setting of the service, where a real environment variable wins over the .env file
class Settings(BaseSettings):

    # Read the .env file next to the backend code and ignore unrelated entries in it
    model_config = SettingsConfigDict(
        env_file = ENV_FILE_PATH,
        env_file_encoding = "utf-8",
        extra = "ignore",
    )



    # Connect to the authorization service, where the key stays empty for offline work
    leash_base_url: str = "https://leash-api-production.up.railway.app"
    team_api_key: str = ""



    # Connect to the language model provider, where empty values mean no model is configured
    llm_base_url: str = ""
    llm_model: str = ""
    llm_api_key: str = ""



    # Limit the time for one decision and for the model call inside it, in milliseconds
    engine_budget_ms: int = Field(default = 6000, gt = 0)
    llm_budget_ms: int = Field(default = 2500, gt = 0)



    # Keep a reserve of the decision deadline for sending the answer, and say how long one long poll may wait
    post_reserve_ms: int = Field(default = 1500, gt = 0)
    long_poll_wait_seconds: int = Field(default = 25, ge = 1, le = 25)



    # Choose the platform the worker talks to, the live service or the in-process copy fed by the public purchases,
    # and whether the worker polls from the start or only while a run started through the web API is open
    leash_mode: Literal["live", "offline"] = "live"
    worker_autostart: bool = False



    # Locate the decision store and the audit folder, where an empty value means the default under outputs/
    state_db_path: str = ""
    audit_folder: str = ""



    # Allow the interface at these origins to call the web API, as a comma-separated list
    web_cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"



    # Choose what happens when a one-time goal was already bought, and when merchant text tries to instruct the agent.
    # A second order of the one requested thing asks the customer unless another value is set, because one thing means one.
    goal_fulfilled_action: Literal["off", "note", "step_up"] = "step_up"
    injection_action: Literal["step_up", "decline"] = "step_up"



    # Set how far above a limit a purchase may be, as a share of the limit, and still be put to the customer.
    # A larger overshoot declines, and a share of zero declines every overshoot.
    overshoot_tolerance_share: Decimal = Field(default = Decimal("0.10"), ge = 0, le = 1)



    # Set how many minutes apart two orders at the same shop may be and still be put to the customer as one order split in two.
    # The minutes run on the simulated time of the purchases, and an order exactly that long ago still counts.
    split_order_window_minutes: int = Field(default = 120, gt = 0)



    # Set how many hours after an approved order the same cart at the same shop is put to the customer as a repeated order,
    # and how far the two amounts may be apart, as a share of the earlier amount.
    # The hours run on the simulated time of the purchases. An order exactly that long ago still counts, and so does a difference of exactly the share.
    duplicate_window_hours: int = Field(default = 48, gt = 0)
    duplicate_amount_share: Decimal = Field(default = Decimal("0.10"), ge = 0, le = 1)



    # Set how many approved purchases at a shop make it a shop the customer uses regularly,
    # and how similar two shop names must be, from 0 to 1, before a new shop counts as an imitation of a shop the customer uses.
    # A purchase count exactly at the minimum is regular use, and a similarity exactly at the threshold is an imitation.
    familiarity_regular_min_purchases: int = Field(default = 3, ge = 1)
    lookalike_name_similarity: Decimal = Field(default = Decimal("0.85"), ge = Decimal("0.75"), le = Decimal("0.95"))



    # Send the trust score of a decision to the platform as three evidence items next to the evidence of the guards.
    # Switched off, the score is still computed, stored and shown, and only the answer to the platform leaves it out.
    trust_score_in_evidence: bool = True



    # Set how many signs that someone other than the customer is driving the session ask the customer, and how many decline.
    # A count exactly at a number reaches it. Below the asking count a single sign passes, unless the instruction asks to watch the session and the sign is a strong one.
    session_ask_signal_count: int = Field(default = 2, ge = 1)
    session_decline_signal_count: int = Field(default = 3, ge = 1)



    # Set how many purchase attempts in the ten minutes before a purchase make a quick series, and how many purchases a card history needs
    # before an hour without any purchase counts as an hour the customer never shops at. A thin history says too little about the hours of a customer.
    velocity_min_recent_attempts: int = Field(default = 2, ge = 1)
    hour_min_history_purchases: int = Field(default = 20, ge = 1)









#### Step 3: Share the settings ####

# Build the settings on the first call and hand back the same object for the rest of the process
@lru_cache(maxsize = None)
def get_settings():
    return Settings()



# Locate the repository root, which holds outputs/ and data/
REPOSITORY_FOLDER = BACKEND_FOLDER.parent



# Resolve the path of the decision store, which defaults to outputs/state/relay.sqlite3
def resolve_state_db_path(settings = None):
    if settings is None:
        settings = get_settings()
    if settings.state_db_path == "":
        return REPOSITORY_FOLDER / "outputs" / "state" / "relay.sqlite3"
    return Path(settings.state_db_path)



# Resolve the audit folder, which defaults to outputs/audit
def resolve_audit_folder(settings = None):
    if settings is None:
        settings = get_settings()
    if settings.audit_folder == "":
        return REPOSITORY_FOLDER / "outputs" / "audit"
    return Path(settings.audit_folder)
