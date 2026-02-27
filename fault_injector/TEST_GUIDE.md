# Fault Injector Tests Guide

This document describes the current test layout and run commands.

## Layout

```text
fault_injector/tests/
├── unit/
│   └── features/
│       ├── channels/
│       │   └── test_ssh_channel.py
│       └── scenarios/
│           └── test_scenarios.py
├── integration/
│   ├── test_netconf_connection.py
│   └── test_switch_operations.py
├── e2e/
│   ├── journeys/
│   └── helpers/
│       └── page-objects/
├── fixtures/
│   └── switch_config.yaml
├── mocks/
│   └── __init__.py
├── helpers/
│   └── test_base.py
├── conftest.py
└── __init__.py
```

## Unit Tests

```bash
pytest fault_injector/tests/unit/features/channels/test_ssh_channel.py -v
pytest fault_injector/tests/unit/features/scenarios/test_scenarios.py -v
```

## Integration Tests

Configure `fault_injector/tests/fixtures/switch_config.yaml` first.

```bash
python -m fault_injector.tests.integration.test_netconf_connection
python -m fault_injector.tests.integration.test_switch_operations --dry-run
python -m fault_injector.tests.integration.test_switch_operations --real
```

## Run Recommended Suite

```bash
pytest fault_injector/tests/unit/features/channels/test_ssh_channel.py \
  fault_injector/tests/unit/features/scenarios/test_scenarios.py -v --cov=fault_injector
```

## Notes

- Keep shared fixtures in `fault_injector/tests/conftest.py`.
- Keep reusable test utilities in `fault_injector/tests/helpers/`.
- Keep test data/config in `fault_injector/tests/fixtures/`.
