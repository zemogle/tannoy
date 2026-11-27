import datetime
from zoneinfo import ZoneInfo

DAY_START = datetime.time(9, 30)
DAY_END = datetime.time(18, 0)
DAY_TIMEZONE = ZoneInfo('Europe/London')
DEFAULT_TALK_MINUTES = 25
# Two 20 minute coffee breaks and at least one hour for lunch each day.
FIXED_BREAK_MINUTES_PER_DAY = 2 * 20 + 60
