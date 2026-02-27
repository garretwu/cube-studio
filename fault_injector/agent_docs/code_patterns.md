<!--
=============================================================================
FILE: code_patterns.md
PURPOSE: Document coding conventions, patterns, and best practices
GUIDANCE FOR AI AGENTS:
- Follow these patterns when writing code
- Maintain consistency with existing codebase
- When in doubt, check existing code for examples
- Update this file when introducing new patterns
- These patterns are MANDATORY, not optional
=============================================================================
-->

# Code Patterns: Fault Injector

## Overview
This document defines the coding conventions and patterns for the fault_injector project. All code should follow these guidelines to maintain consistency and quality.

---

## Naming Conventions

### Files & Directories

| Type | Convention | Example |
|------|------------|---------|
| Modules | snake_case | `fault_injector.py` |
| Packages | snake_case | `fault_injector/` |
| Test files | test_*.py | `test_scenarios.py` |

### Python Code

| Type | Convention | Example |
|------|------------|---------|
| Classes | PascalCase | `FaultOrchestrator` |
| Functions | snake_case | `inject_fault()` |
| Variables | snake_case | `fault_id` |
| Constants | UPPER_SNAKE | `MAX_RETRIES` |
| Private methods | _leading_underscore | `_execute_impl()` |
| Async functions | snake_case with async | `async def run_scenario()` |

---

## Code Structure

### Module Organization

```python
"""
Module docstring - Describe the module's purpose.
"""
from __future__ import annotations  # Always first

# Standard library imports
import asyncio
import logging
from typing import Any, Optional

# Third-party imports
from pydantic import BaseModel

# Local imports
from lib.channels.base import BaseChannel

# Module-level constants
LOGGER = logging.getLogger(__name__)
MAX_RETRIES = 3

# Classes and functions
```

### Class Structure

```python
class ExampleClass:
    """
    Brief description of the class.
    
    Longer description if needed.
    
    Attributes:
        attr1: Description of attr1
        attr2: Description of attr2
    """
    
    def __init__(self, param1: str, param2: int = 10):
        """
        Initialize the class.
        
        Args:
            param1: Description of param1
            param2: Description of param2
        """
        self.attr1 = param1
        self.attr2 = param2
    
    async def public_method(self) -> Result:
        """Public method doing something."""
        return await self._internal_method()
    
    async def _internal_method(self) -> Result:
        """Private internal method."""
        pass
```

---

## Async Patterns

### Async Context Managers

```python
from contextlib import asynccontextmanager

@asynccontextmanager
async def managed_resource():
    """Context manager for resource cleanup."""
    resource = await acquire_resource()
    try:
        yield resource
    finally:
        await release_resource(resource)
```

### Error Handling in Async Code

```python
async def safe_execute():
    """Execute with proper error handling."""
    try:
        result = await risky_operation()
        return Result(success=True, output=result)
    except SpecificError as e:
        LOGGER.error(f"Specific error occurred: {e}")
        return Result(success=False, error=str(e))
    except Exception as e:
        LOGGER.exception(f"Unexpected error: {e}")
        raise
```

### Concurrent Operations

```python
async def run_parallel(tasks: list[Coroutine]):
    """Run multiple operations concurrently."""
    results = await asyncio.gather(*tasks, return_exceptions=True)
    return results
```

---

## Type Hints

### Function Signatures

```python
from typing import Optional, Union, Any
from collections.abc import Coroutine

# Good
def process_data(data: dict[str, Any]) -> Optional[str]:
    pass

# Good with default
def configure(name: str, timeout: int = 30) -> None:
    pass

# Good with Union
def handle_result(result: Union[Success, Failure]) -> bool:
    pass

# Async function
async def fetch_data(url: str) -> dict[str, Any]:
    pass
```

### Pydantic Models

```python
from pydantic import BaseModel, Field
from datetime import datetime

class SessionConfig(BaseModel):
    """Configuration for a fault injection session."""
    
    session_id: str = Field(..., description="Unique session identifier")
    timeout: int = Field(default=60, ge=1, le=3600)
    enabled: bool = Field(default=True)
    created_at: datetime = Field(default_factory=datetime.now)
```

---

## Error Handling

### Custom Exceptions

```python
class FaultInjectorError(Exception):
    """Base exception for fault_injector."""
    pass

class SafetyViolationError(FaultInjectorError):
    """Raised when a safety violation is detected."""
    pass

class ChannelError(FaultInjectorError):
    """Raised when a channel operation fails."""
    pass
```

### Result Pattern

```python
from dataclasses import dataclass

@dataclass
class Result:
    """Standard result type for operations."""
    success: bool
    output: str = ""
    error: str = ""
    
    @classmethod
    def ok(cls, output: str = "") -> "Result":
        return cls(success=True, output=output)
    
    @classmethod
    def fail(cls, error: str) -> "Result":
        return cls(success=False, error=error)
```

---

## Logging

### Logger Setup

```python
import logging

LOGGER = logging.getLogger(__name__)

# Log levels:
# DEBUG - Detailed diagnostic information
# INFO - Confirmation of expected operation
# WARNING - Something unexpected happened
# ERROR - A problem occurred, but the program can continue
# CRITICAL - A serious error occurred
```

### Logging Patterns

```python
# Good: Structured logging with context
LOGGER.info(f"Injecting fault: fault_id={fault_id}, node={node}")
LOGGER.error(f"Failed to inject fault: {error}", exc_info=True)

# Bad: Unstructured logging
LOGGER.info("Injecting fault")
LOGGER.error("Something went wrong")
```

---

## Testing Patterns

### Test Structure

```python
import pytest
from unittest.mock import AsyncMock, MagicMock

class TestExampleClass:
    """Tests for ExampleClass."""
    
    @pytest.fixture
    def example_instance(self):
        """Create test instance."""
        return ExampleClass(param1="test")
    
    @pytest.mark.asyncio
    async def test_public_method_returns_expected_result(self, example_instance):
        """Test that public_method returns expected result."""
        result = await example_instance.public_method()
        
        assert result.success is True
        assert "expected" in result.output
```

### Test Naming

```python
# Good: test_[method]_[scenario]_[expected_result]
def test_inject_fault_when_node_unavailable_returns_error():
    pass

def test_recover_after_crash_restores_state():
    pass
```

---

## Documentation

### Docstring Format

```python
def complex_function(param1: str, param2: int) -> dict:
    """
    Brief one-line description.
    
    Longer description if needed. Explain the algorithm
    or important details here.
    
    Args:
        param1: Description of param1
        param2: Description of param2
    
    Returns:
        Description of return value
    
    Raises:
        ValueError: When param1 is empty
        ChannelError: When channel operation fails
    
    Example:
        >>> result = complex_function("test", 10)
        >>> print(result["status"])
        "success"
    """
    pass
```

---

## Import Organization

```python
# 1. Future imports (always first)
from __future__ import annotations

# 2. Standard library (alphabetical)
import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

# 3. Third-party (alphabetical)
import httpx
from pydantic import BaseModel

# 4. Local imports (alphabetical)
from lib.channels.base import BaseChannel
from fault_injector.config.schema import FaultInjectorConfig
```

---

## Forbidden Patterns

### Do NOT Do These

```python
# BAD: Bare except
try:
    do_something()
except:
    pass

# BAD: Mutable default arguments
def add_item(item, items=[]):
    items.append(item)

# BAD: Global state
global_counter = 0

# BAD: Hardcoded credentials
password = "secret123"

# BAD: Blocking I/O in async code
async def bad_async():
    time.sleep(10)  # Blocks the event loop!

# GOOD: Use async sleep
async def good_async():
    await asyncio.sleep(10)
```

---

## Code Review Checklist

Before submitting code, verify:

- [ ] All functions have type hints
- [ ] All public functions have docstrings
- [ ] No bare `except:` clauses
- [ ] No mutable default arguments
- [ ] Logging uses structured format
- [ ] Tests cover new functionality
- [ ] No hardcoded secrets or credentials
