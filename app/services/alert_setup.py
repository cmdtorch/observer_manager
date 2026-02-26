import structlog

from app.services.clients.glitchtip_client import GlitchTipService

logger = structlog.get_logger()


async def setup_glitchtip_project_alert(
    glitchtip_service: GlitchTipService,
    organization_slug: str,
    project_slug: str,
    webhook_url: str,
) -> None:
    """
    Ensure the GlitchTip project has exactly one alert recipient:
    our Telegram webhook. Creates alert if none exist, updates first if exists.
    """
    try:
        recipient = {
            "recipientType": "webhook",
            "url": webhook_url,
        }

        alerts = await glitchtip_service.list_project_alerts(organization_slug, project_slug)

        if alerts:
            alert_id = alerts[0]["id"]
            await glitchtip_service.update_project_alert(
                organization_slug, project_slug, alert_id, [recipient]
            )
            logger.info(
                "glitchtip_alert_updated",
                org_slug=organization_slug,
                project_slug=project_slug,
                alert_id=alert_id,
                webhook_url=webhook_url,
            )
        else:
            await glitchtip_service.create_project_alert(
                organization_slug, project_slug, [recipient]
            )
            logger.info(
                "glitchtip_alert_created",
                org_slug=organization_slug,
                project_slug=project_slug,
                webhook_url=webhook_url,
            )

    except Exception as exc:
        logger.error(
            "glitchtip_alert_setup_failed",
            org_slug=organization_slug,
            project_slug=project_slug,
            error=str(exc),
        )
        # Do not re-raise — alert setup failure is non-critical
