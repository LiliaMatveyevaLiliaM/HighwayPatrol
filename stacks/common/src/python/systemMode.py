# Python libraries import statements
from enum import Enum


class SystemMode(str, Enum):
    """
    Helper class to enable easier identification of the mode being deployed
    Please note that the order of classes in the inheritance chain is important
    Reversing them as class SystemMode(Enum, str) will throw TypeError
    """

    DEV  = "dev"
    TEST = "test"
    PROD = "prod"
