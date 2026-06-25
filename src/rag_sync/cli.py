import typer

app = typer.Typer(help="RAG Sync control CLI.")


@app.callback()
def main() -> None:
    """RAG Sync control CLI."""
