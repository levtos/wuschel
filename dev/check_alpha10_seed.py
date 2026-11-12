import asyncio

from workbench_alpha10 import seed

from core_contracts import wiring  # noqa: F401
from core_contracts.clock import SystemClock
from core_contracts.persistence import MemoryStore
from core_contracts.runtime import Runtime
from core_contracts.testing.contract_types import type_registry
from core_contracts.validation import RegistryValidationError


async def main():
    runtime = Runtime(MemoryStore(), SystemClock(), type_registry())
    await runtime.start()
    try:
        await seed(runtime)
        print("synthetic seed valid")
    except RegistryValidationError as error:
        print([item.model_dump() for item in error.issues])
        raise
    finally:
        await runtime.stop()


if __name__ == "__main__":
    asyncio.run(main())
