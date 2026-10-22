
# The Stripe day schedule (aiv/slots.py) is a process-wide cache filled from the Stripe list API. Tests mock
# requests.get per test, so the shared cache is kept offline here; tests of the schedule use their own Book().
from aiv import slots as _slots  # noqa: E402

_slots.BOOK.refresh = lambda force=False: False
