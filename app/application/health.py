from app.domain.health import HealthStatus


def get_health_status() -> HealthStatus:
    return HealthStatus()
