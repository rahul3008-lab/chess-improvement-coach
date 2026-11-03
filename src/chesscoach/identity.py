from sqlalchemy import select

from chesscoach.providers import canonical
from chesscoach.storage.models import Account, Player


def resolve_account(session, provider: str, username: str, player_id: int | None = None) -> Account:
    provider, username = canonical(provider), username.strip().lower()
    if not username or len(username) > 200:
        raise ValueError("Username must contain 1–200 characters.")
    account = session.scalar(
        select(Account).where(Account.provider == provider, Account.username == username)
    )
    if account:
        if player_id is not None and account.player_id != player_id:
            raise ValueError("Account already belongs to another player; automatic reassignment is disabled.")
        return account
    if player_id is None:
        player = Player(display_name=username)
        session.add(player)
        session.flush()
        player_id = player.id
    elif not session.get(Player, player_id):
        raise ValueError("Player does not exist.")
    account = Account(player_id=player_id, provider=provider, username=username)
    session.add(account)
    session.flush()
    return account
