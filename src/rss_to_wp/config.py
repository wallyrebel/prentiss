"""Configuration models and loading."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal, Optional

import pendulum
import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class FeedConfig(BaseModel):
    """Configuration for a single RSS feed."""

    name: str
    url: str
    default_category: Optional[str] = None
    default_tags: list[str] = Field(default_factory=list)
    max_per_run: int = Field(default=2, ge=1, le=20)
    source_name: Optional[str] = None
    use_original_title: bool = False
    source_type: Literal["rss", "ms_sos_news"] = "rss"
    max_age_hours: Optional[int] = Field(default=None, ge=1, le=168)

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        """Ensure URL starts with http(s)."""
        if not v.startswith(("http://", "https://")):
            raise ValueError(f"Invalid URL: {v}")
        return v


class CategoryRule(BaseModel):
    """Put a story in an extra category when its headline matches a pattern.

    Feeds only carry a single default_category, so every story lands in the same
    catch-all bucket. These rules add the town/topic category on top of it, which
    is what keeps archives like /category/booneville-ms-news/ current.
    """

    pattern: str
    category: str

    @field_validator("pattern")
    @classmethod
    def validate_pattern(cls, v: str) -> str:
        """Ensure the pattern is a usable regex."""
        try:
            re.compile(v, re.IGNORECASE)
        except re.error as e:
            raise ValueError(f"Invalid regex pattern: {v} ({e})")
        return v

    def matches(self, headline: str) -> bool:
        """True if this rule applies to the given headline."""
        return re.search(self.pattern, headline, re.IGNORECASE) is not None


class QualityPolicy(BaseModel):
    """Editorial thresholds, not promises of a Google ranking benefit."""

    min_source_words: int = Field(default=180, ge=100)
    min_article_words: int = Field(default=250, ge=150)
    max_article_words: int = Field(default=900, ge=250)
    min_facts: int = Field(default=6, ge=5)
    max_expansion_ratio: float = Field(default=1.6, ge=1, le=2)
    max_posts_per_run: int = Field(default=3, ge=1, le=10)
    max_reviews_per_run: int = Field(default=12, ge=1, le=40)
    max_sources_per_article: int = Field(default=3, ge=1, le=4)
    audience: str = "Prentiss County, Mississippi; Booneville, Baldwyn, Jumpertown, New Site, Marietta, Thrasher and Wheeler"


class FeedsConfig(BaseModel):
    """Container for all feed configurations."""

    feeds: list[FeedConfig] = Field(default_factory=list)
    category_rules: list[CategoryRule] = Field(default_factory=list)
    quality: QualityPolicy = Field(default_factory=QualityPolicy)


class AppSettings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # OpenAI
    openai_api_key: str = Field(..., description="OpenAI API key")
    openai_model: str = Field(default="gpt-5.4-mini", description="OpenAI model to use")
    openai_review_model: str = Field(
        default="gpt-5.4-mini", description="Independent editorial review model"
    )

    # WordPress
    wordpress_base_url: str = Field(..., description="WordPress site URL")
    wordpress_username: str = Field(..., description="WordPress username")
    wordpress_app_password: str = Field(..., description="WordPress application password")
    wordpress_post_status: str = Field(default="publish", description="Post status")
    use_stock_images: bool = False

    # Image fallback providers (optional)
    pexels_api_key: Optional[str] = Field(default=None, description="Pexels API key")
    unsplash_access_key: Optional[str] = Field(default=None, description="Unsplash access key")

    # Logging & Timezone
    log_level: str = Field(default="INFO", description="Log level")
    log_file: Optional[str] = Field(default=None, description="Optional log file path")
    timezone: str = Field(default="UTC", description="Timezone for date calculations")

    # Email notifications (optional)
    smtp_email: Optional[str] = Field(default=None, description="SMTP sender email")
    smtp_password: Optional[str] = Field(default=None, description="SMTP password/app password")
    notification_email: Optional[str] = Field(
        default=None, description="Email to send notifications to"
    )

    @field_validator("wordpress_base_url")
    @classmethod
    def strip_trailing_slash(cls, v: str) -> str:
        """Remove trailing slash from URL."""
        return v.rstrip("/")

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, v: str) -> str:
        """Validate timezone is valid."""
        try:
            pendulum.timezone(v)
        except Exception:
            raise ValueError(f"Invalid timezone: {v}")
        return v


def load_feeds_config(config_path: str | Path) -> FeedsConfig:
    """Load feeds configuration from YAML file.

    Args:
        config_path: Path to the feeds.yaml configuration file.

    Returns:
        FeedsConfig object with validated feed configurations.

    Raises:
        FileNotFoundError: If config file doesn't exist.
        ValidationError: If config is invalid.
    """
    config_path = Path(config_path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    return FeedsConfig.model_validate(data or {"feeds": []})


def get_app_settings() -> AppSettings:
    """Load application settings from environment.

    Returns:
        AppSettings object with validated settings.
    """
    return AppSettings()


def get_data_dir() -> Path:
    """Get the data directory for runtime files.

    Creates the directory if it doesn't exist.

    Returns:
        Path to data directory.
    """
    # Use directory relative to the project root
    data_dir = Path(__file__).parent.parent.parent / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir
