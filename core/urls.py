"""URL routes for the MemTrace JSON API."""
from django.urls import path

from core.views import (
    api_graph,
    api_investigate,
    api_prove,
    api_quarantine,
    api_replay,
    api_reset,
    api_simulate,
    api_state,
)

urlpatterns = [
    path("state",       api_state,       name="api-state"),
    path("graph",       api_graph,       name="api-graph"),
    path("simulate",    api_simulate,    name="api-simulate"),
    path("reset",       api_reset,       name="api-reset"),
    path("investigate", api_investigate, name="api-investigate"),
    path("prove",       api_prove,       name="api-prove"),
    path("quarantine",  api_quarantine,  name="api-quarantine"),
    path("replay",      api_replay,      name="api-replay"),
]
