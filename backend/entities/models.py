from pydantic import BaseModel


class EntityAlias(BaseModel):
    symbol: str  # e.g. "NVDA" — matches entities.symbol in the db design
    aliases: list[str] = []  # e.g. ["Nvidia", "Nvidia Corporation"]
