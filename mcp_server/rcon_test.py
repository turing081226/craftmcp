import os

from mcrcon import MCRcon

password = os.environ["MC_RCON_PASSWORD"]

with MCRcon("127.0.0.1", password, port=25575) as rcon:
    print(rcon.command("list"))