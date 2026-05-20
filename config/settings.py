"""
Application configuration via environment variables and .env files.
Uses pydantic-settings so every value can be overridden at runtime.
Credentials are NEVER hard-coded here – provide them via .env or the shell.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class CherwellConfig(BaseSettings):
    """Connection settings for the Cherwell REST API."""

    model_config = SettingsConfigDict(
        env_prefix="CHERWELL_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    base_url: str = "https://your-cherwell-server.example.com/CherwellAPI"
    client_id: str = ""
    username: str = ""
    password: str = ""
    grant_type: str = "password"
    timeout: int = 30
    verify_ssl: bool = True


class ServiceNowConfig(BaseSettings):
    """Connection settings for the ServiceNow REST Table API."""

    model_config = SettingsConfigDict(
        env_prefix="SERVICENOW_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    instance_url: str = "https://your-instance.service-now.com"
    username: str = ""
    password: str = ""
    api_version: str = "v1"
    timeout: int = 30
    verify_ssl: bool = True


class MigrationConfig(BaseSettings):
    """Behavioural settings for the migration pipeline."""

    model_config = SettingsConfigDict(
        env_prefix="MIGRATION_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    batch_size: int = 50
    mock_mode: bool = True          # Set to False for real API calls
    output_dir: str = "./output"
    state_file: str = "./output/migration_state.json"
    log_level: str = "INFO"
    auto_approve: bool = False      # Set to True for unattended / CI runs
    max_retries: int = 3
    retry_delay: float = 1.0        # Seconds between retries (exponential back-off base)
    source_object_type: str = "Incident"
    target_table: str = "incident"
