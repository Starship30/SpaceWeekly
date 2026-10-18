"""Load IANA timezones from one archive instead of hundreds of extracted files."""
import os
import sys

sys.path.insert(0, os.path.join(sys._MEIPASS, "tzdata.zip"))
