#! /usr/bin/python3

# This is used exclusively to get the base stack name for this project
# Why? Just so we don't have to write the name in several places
# The name is used in the application as well as during CDK deployment
# This helps to greatly simplify deployment
from stacks.common.src.python.superGlblVars import baseStackName

print(baseStackName)
