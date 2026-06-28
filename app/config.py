from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # TMS TCP connection
    tms_host: str = "localhost"
    tms_port: int = 17159
    tms_auth_token: str = "TMS_AUTH_TOKEN"

    # Inbound REST API auth (bearer token HappyRobot sends)
    api_auth_token: str = "changeme"

    # Socket / retry tuning
    socket_timeout: float = 10.0
    max_retries: int = 3
    retry_backoff_base: float = 0.5  # seconds; actual delay = base * 2^attempt

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )


settings = Settings()
