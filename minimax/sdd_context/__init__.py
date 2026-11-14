"""SDD context management module for the MiniMax x OceanBase competition entry.

The package implements a small but complete "spec driven development" context
module:

* :mod:`sdd_context.db`        - seekdb / OceanBase storage of engineering
  material (specifications, code symbols, tests) plus a native full text index.
* :mod:`sdd_context.documents`  - parsing of specs, Python sources and test
  files into addressable documents.
* :mod:`sdd_context.ingest`    - importing a project into the database.
* :mod:`sdd_context.retrieve`  - retrieving the material that applies to a task.
* :mod:`sdd_context.context`   - assembling the development context.
* :mod:`sdd_context.state`     - persisting task state in PowerContext.
* :mod:`sdd_context.minimax_client` - talking to the MiniMax API.
* :mod:`sdd_context.agent`     - the "context -> MiniMax -> patch -> tests"
  loop.
"""

__all__ = ["__version__"]

__version__ = "1.0.0"
