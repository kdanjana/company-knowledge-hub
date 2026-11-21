"""Simulated company knowledge base (what QuickBite's chatbot team already built).

In a real company these tables would live in the chatbot team's own vector store.
The auditor (`audit` app) never imports these models: it reads the tables through
core.kb_adapter, exactly as it would read another team's database.
"""
