import sqlite3
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver


def open_checkpointer(path: str | Path) -> SqliteSaver:
    checkpoint_path = Path(path)
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(checkpoint_path), check_same_thread=False)
    checkpointer = SqliteSaver(connection)
    checkpointer.setup()
    return checkpointer
