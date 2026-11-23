"""Daily role alerts (weekdays 9 AM PT) and instant alerts for new briefs."""

from __future__ import annotations

import logging
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from .service import Copilot

log = logging.getLogger(__name__)

NO_MATCHES = "Good morning. No new roles match your profile right now."


def dm_channel(client, user_id: str) -> str:
    return client.conversations_open(users=user_id)["channel"]["id"]


def send_alerts(client, copilot: Copilot, only_new: bool, say_when_empty: bool) -> None:
    """DM each recruiter the roles that match their profile."""
    for user_id, profile in copilot.profiles().items():
        try:
            alerts = copilot.alerts_for(profile, only_new=only_new)
            if not alerts and not say_when_empty:
                continue
            channel = dm_channel(client, user_id)
            if not alerts:
                client.chat_postMessage(channel=channel, text=NO_MATCHES)
                continue
            for brief, text, blocks in alerts:
                client.chat_postMessage(channel=channel, text=text, blocks=blocks)
            copilot.alert_state.mark(user_id, [brief.role_id for brief, _, _ in alerts])
        except Exception:
            log.exception("Could not send role alerts to %s", user_id)


def start_scheduler(client, copilot: Copilot) -> BackgroundScheduler:
    settings = copilot.settings
    tz = ZoneInfo(settings.alert_timezone)
    scheduler = BackgroundScheduler(timezone=tz)
    scheduler.add_job(
        send_alerts,
        CronTrigger(day_of_week="mon-fri", hour=settings.alert_hour, minute=settings.alert_minute, timezone=tz),
        kwargs={"client": client, "copilot": copilot, "only_new": True, "say_when_empty": True},
        id="daily_role_alerts",
        replace_existing=True,
    )
    if settings.brief_scan_minutes > 0:
        scheduler.add_job(
            send_alerts,
            IntervalTrigger(minutes=settings.brief_scan_minutes, timezone=tz),
            kwargs={"client": client, "copilot": copilot, "only_new": True, "say_when_empty": False},
            id="instant_role_alerts",
            replace_existing=True,
        )
    scheduler.start()
    log.info("Scheduler started: daily alerts weekdays %02d:%02d %s",
             settings.alert_hour, settings.alert_minute, settings.alert_timezone)
    return scheduler
