# Script: sse.py
# Purpose: Push every decision, answer and status change to the open interfaces as server-sent events
# Author: Jonas Lüthi
# Date: September 2026

import asyncio
import json









#### Step 1: Format one event ####

# Write one server-sent event with its name and its JSON payload
def format_sse(event_name, payload):
    return "event: " + event_name + "\ndata: " + json.dumps(payload, ensure_ascii = False) + "\n\n"









#### Step 2: Define the broadcaster ####

# Hand every published event to every subscriber, each through its own queue, and drop events for a subscriber that stopped reading
class EventBroadcaster:

    def __init__(self, queue_size = 256):
        self.queue_size = queue_size
        self.queues = set()
        self.published_count = 0



    # Open a queue for one interface
    def subscribe(self):
        queue = asyncio.Queue(maxsize = self.queue_size)
        self.queues.add(queue)
        return queue



    # Close the queue of one interface
    def unsubscribe(self, queue):
        self.queues.discard(queue)



    # Put one event into every queue, where a full queue loses this event rather than blocking the worker
    def publish(self, event_name, payload):
        self.published_count = self.published_count + 1
        for queue in list(self.queues):
            try:
                queue.put_nowait((event_name, payload))
            except asyncio.QueueFull:
                pass



    # Yield the events of one queue as text, with a heartbeat while nothing happens, until the reader goes away
    async def stream(self, queue, heartbeat_seconds = 15.0):
        try:
            while True:
                try:
                    event_name, payload = await asyncio.wait_for(queue.get(), timeout = heartbeat_seconds)
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
                    continue
                yield format_sse(event_name, payload)
        finally:
            self.unsubscribe(queue)
