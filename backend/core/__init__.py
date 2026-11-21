"""Core auditor pipeline: plain Python, no Django.

Django (the `audit` app) calls these modules and stores the results.
Keeping this package free of Django makes it easy to unit-test.
"""
