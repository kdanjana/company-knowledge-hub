"""Users, roles and departments.

Roles are Django groups:
  auditor  -> run audits, review findings, evaluation, chat over ALL documents
  employee -> chat only, over documents open to everyone or to their department
A superuser counts as an auditor.
"""
