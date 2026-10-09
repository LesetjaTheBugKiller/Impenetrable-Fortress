class DeskError(Exception):
    """A setup or file problem the operator should see."""


class BeaconError(DeskError):
    """The public time beacon could not be read."""


class TooEarly(DeskError):
    """The time-lock key for this file is not published yet."""
