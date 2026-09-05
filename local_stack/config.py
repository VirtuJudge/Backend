QUEUE_KEY = "virtujudge:local:jobs"
RESULT_KEY_PREFIX = "virtujudge:local:result:"
HEARTBEAT_KEY = "virtujudge:local:worker:heartbeat"

DEFAULT_REDIS_URL = "redis://localhost:6379/0"

HEARTBEAT_TTL_SECONDS = 15
RESULT_TTL_SECONDS = 3600
BLPOP_TIMEOUT_SECONDS = 1
