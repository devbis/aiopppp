import asyncio


class DebounceEvent:
    """
    DebounceEvent class to debounce multiple events.
    Tick triggers the event. Wait for delay and if no other event, wait returns the result.

    Example:
    debounce = DebounceEvent(delay=0.5)

    async def print_hello(s):
        await debounce.wait()
        print(s)

    t = asyncio.create_task(print_hello("Hello"))

    for _ in range(10):
        await debounce.tick()
        await asyncio.sleep(0.2)

    await t

    """

    def __init__(self, delay=0.2, wait_for_first=True):
        self.counter = 0
        self.ev = asyncio.Event()
        self.delay = delay
        self.wait_for_first = wait_for_first

    async def tick(self):
        self.counter += 1
        loop = asyncio.get_running_loop()
        loop.call_at(loop.time() + self.delay, self._timeout_counter)

    def _timeout_counter(self):
        self.counter -= 1
        if self.counter == 0:
            self.ev.set()

    async def wait(self):
        if not self.wait_for_first and not self.counter:
            await asyncio.sleep(0)
            return

        await self.ev.wait()

    def clear(self):
        self.ev.clear()

    def is_set(self):
        return self.ev.is_set()
