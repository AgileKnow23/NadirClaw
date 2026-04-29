"""HTTP route modules for the NadirClaw FastAPI app.

Each submodule defines an `APIRouter` named `router` that server.py mounts.
This keeps server.py focused on app lifecycle and the chat-completions
hot path while feature-coherent endpoint groups (blast, pipeline,
observability, classify) live next to each other.
"""
