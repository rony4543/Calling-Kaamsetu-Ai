import sys
from app.orchestrator.router import get_router, InboundMessage
import logging
logging.basicConfig(level=logging.DEBUG)

router = get_router()
msg = InboundMessage(wa_id="910000000001", message_id="sim-in-1", type="text", text="Hello")
print("Calling handle...")
router.handle(msg)
print("Done!")
