from importlib.metadata import entry_points

ALIASES = {"chess.com": "chesscom", "chess-com": "chesscom", "local-pgn": "local", "pgn": "local"}


def canonical(name: str) -> str:
    name = name.strip().lower()
    return ALIASES.get(name, name)


class ProviderRegistry:
    def __init__(self):
        from chesscoach.providers.chesscom import ChessCom

        self.adapters = {"chesscom": ChessCom}
        for plugin in entry_points(group="chesscoach.providers"):
            self.register(plugin.name, plugin.load())

    def register(self, name, factory):
        self.adapters[canonical(name)] = factory

    def get(self, name, **kwargs):
        key = canonical(name)
        if key not in self.adapters:
            raise ValueError(f"Unsupported remote provider: {name}. Available: {', '.join(self.adapters)}")
        return self.adapters[key](**kwargs)
