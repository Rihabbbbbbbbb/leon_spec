"""
Q&A assistant package — grounded question answering over accessible spec files.

Modules:
- retrieval.py : index accessible spec files + retrieve relevant passages
- prompt.py    : strict prompt template (answer only from retrieved content)
- mock_data.py : example mock data so the UI runs without real retrieval
- route.py     : FastAPI router exposing POST /api/ask
"""
