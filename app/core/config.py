import os
from dataclasses import dataclass

@dataclass
class Settings:
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./local_job_assistant.db")

settings = Settings()
