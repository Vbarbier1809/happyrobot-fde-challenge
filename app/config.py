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

    # FMCSA QCMobile API (carrier verification) — used by preflight.py for now
    fmcsa_web_key: str = ""
    fmcsa_base_url: str = "https://mobile.fmcsa.dot.gov/qc/services"
    fmcsa_test_dot: str = ""
    fmcsa_test_mc: str = ""

    # Deployed service URL, once it exists — used by preflight.py
    public_base_url: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )


settings = Settings()
