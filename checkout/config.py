import os

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://shop:shop@localhost:5432/shop")
INVENTORY_URL = os.getenv("INVENTORY_URL", "http://localhost:8001")
PAYMENT_PROVIDER_URL = os.getenv("PAYMENT_PROVIDER_URL", "http://localhost:8002")
TEMPORAL_ADDRESS = os.getenv("TEMPORAL_ADDRESS", "localhost:7233")
TASK_QUEUE = "checkout"

# How long one call to inventory or the payment provider may take.
CALL_TIMEOUT_S = 4
# A pause after each step, so there's time to break something mid-order.
STEP_DELAY_S = float(os.getenv("STEP_DELAY_S", "2"))
