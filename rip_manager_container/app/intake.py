"""Compatibility import for optical intake; new code uses disc_intake."""
import sys
import disc_intake
sys.modules[__name__] = disc_intake
