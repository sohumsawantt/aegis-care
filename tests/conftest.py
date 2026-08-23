"""Shared fixtures. Environments are session-scoped where possible because
building the FHIR sandbox and running trajectories dominates test time."""
from __future__ import annotations

import warnings

import pytest

from aegis_care.care.coordinator import CAREOptions, RecoveryCoordinator
from aegis_care.environment import AegisEnvironment
from aegis_care.incident.scenarios import ScenarioBuilder

warnings.filterwarnings("ignore")


@pytest.fixture
def env() -> AegisEnvironment:
    return AegisEnvironment()


@pytest.fixture
def builder(env) -> ScenarioBuilder:
    return ScenarioBuilder(env)


@pytest.fixture
def f1_incident(env, builder):
    """A depth-4 wrong-patient-alias incident with a matched clean control."""
    return builder.build("F1", env.tasks[0], depth=4, n_controls=1)


@pytest.fixture
def recovered(env, f1_incident):
    """The F1 incident after a full CARE recovery."""
    coordinator = RecoveryCoordinator(env)
    result = coordinator.recover(f1_incident.incident_id, [f1_incident.seed_key],
                                 options=CAREOptions(),
                                 followup_tasks=[f1_incident.task])
    return f1_incident, result
