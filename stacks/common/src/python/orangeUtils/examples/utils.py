import time

from orangeDevUtils import timeUtils as tu

year, month, day = tu.returnYMD(time.time())

print(f'{year}-{month}-{day}')
