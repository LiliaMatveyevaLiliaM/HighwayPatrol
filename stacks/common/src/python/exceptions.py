# User-defined exceptions

class HPatrolError(Exception):
    def __init__(self, message):
        super().__init__(message)
