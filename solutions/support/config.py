import os

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://shop:shop@localhost:5432/shop")
PAYMENT_PROVIDER_URL = os.getenv("PAYMENT_PROVIDER_URL", "http://localhost:8002")
TEMPORAL_ADDRESS = os.getenv("TEMPORAL_ADDRESS", "localhost:7233")
TASK_QUEUE = "support"

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
AGENT_MODEL = os.getenv("AGENT_MODEL", "claude-haiku-5-5")

# How long one call to the payment provider may take.
CALL_TIMEOUT_S = 4
# A pause after each tool call, so there's time to break something mid-answer.
STEP_DELAY_S = float(os.getenv("STEP_DELAY_S", "2"))

# The refund policy.
REFUND_WINDOW_DAYS = 30
MAX_RECENT_REFUNDS = 3  # in the 90 days before today
REVIEW_ABOVE_CENTS = 100000  # MXN 1,000.00: a person decides
