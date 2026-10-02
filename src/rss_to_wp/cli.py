"""Command line entry point for quality-controlled publishing."""

from pathlib import Path
from typing import Optional

import typer

from rss_to_wp import __version__
from rss_to_wp.config import get_app_settings, load_feeds_config
from rss_to_wp.pipeline import run_pipeline
from rss_to_wp.rewriter import OpenAIRewriter
from rss_to_wp.storage import DedupeStore
from rss_to_wp.utils import setup_logging
from rss_to_wp.wordpress import WordPressClient

app = typer.Typer(add_completion=False)


@app.callback()
def main(version: bool = typer.Option(False, "--version", "-v")):
    if version:
        typer.echo(__version__)
        raise typer.Exit()


@app.command()
def run(
    config: Path = typer.Option(Path("feeds.yaml"), "--config", "-c"),
    dry_run: bool = typer.Option(False, "--dry-run", "-n"),
    single_feed: Optional[str] = typer.Option(None, "--single-feed", "-f"),
    hours: int = typer.Option(72, "--hours", "-h", min=1, max=168),
):
    """Review fresh sources and publish only complete, grounded articles."""
    logger = setup_logging()
    try:
        settings = get_app_settings()
        feeds = load_feeds_config(config)
        if single_feed:
            feeds.feeds = [f for f in feeds.feeds if f.name.casefold() == single_feed.casefold()]
        if not feeds.feeds:
            raise ValueError("No matching feeds configured")
        rewriter = OpenAIRewriter(
            settings.openai_api_key,
            settings.openai_model,
            review_model=settings.openai_review_model,
        )
        wp = (
            None
            if dry_run
            else WordPressClient(
                settings.wordpress_base_url,
                settings.wordpress_username,
                settings.wordpress_app_password,
                settings.wordpress_post_status,
            )
        )
        summary, _ = run_pipeline(feeds, settings, DedupeStore(), rewriter, wp, dry_run, hours)
        logger.info("run_complete", **summary)
    except Exception as exc:
        logger.error("run_failed", error_type=type(exc).__name__)
        raise typer.Exit(1) from exc
    # A partial outage is still an operational failure, even if some stories
    # published. The workflow saves its state in an always() step.
    if summary.get("error", 0):
        raise typer.Exit(1)


@app.command()
def status():
    store = DedupeStore()
    typer.echo(f"Processed entries: {store.get_processed_count()}")
    for entry in store.get_recent_entries(limit=10):
        typer.echo(f"{entry['entry_title']} — {entry['wp_post_url']}")


@app.command()
def clear_db(confirm: bool = typer.Option(False, "--yes", "-y")):
    if confirm or typer.confirm("Clear the processed entries database?"):
        typer.echo(f"Cleared {DedupeStore().clear_all()} entries")


if __name__ == "__main__":
    app()
